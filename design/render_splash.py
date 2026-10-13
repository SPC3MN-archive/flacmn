"""Render the launch screen's video: spcmn's dot wordmark as ferrofluid, off-white on black, 60 fps, 10 s.

The page shows it over the charcoal launch screen with `mix-blend-mode: lighten`, so the black around the letters
becomes the page's own charcoal exactly. The motion is the one splash.js used to draw live: each droplet's rim is the
sum of slow ripples (2 to 6 lobes), each swelling and fading on its own beat, its centre wandering around its spot;
the droplets are blurred and the alpha thresholded (the SVG "goo" filter), so close ones meet in a neck; at 6.5 s
the fluid dips towards the still grid and straight back out. A fixed seed keeps the render repeatable.

    python design/render_splash.py            # writes iaflac/gui/static/splash.mp4 and splash-poster.png

Needs numpy, Pillow and ffmpeg (libx264).
"""

from __future__ import annotations

import math
import os
import subprocess
from concurrent.futures import ProcessPoolExecutor
from functools import partial
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "iaflac" / "gui" / "static"
W, H = 1280, 388                     # twice the largest size the page shows it at (640 css px), for Retina screens
SS = 3                               # supersampling, for clean edges
FPS, SECONDS = 60, 10.0              # the launch screen stays up 10 s at most
VIEW = (-1.2, -1.2, 34.4, 10.4)      # the wordmark's box, in grid units
PAPER = (0xF6, 0xF4, 0xEE)

LETTERS = [(0, ".####|#####|###..|..###|#####|####."), (6, "####.|#####|##.##|##.##|#####|####.|##...|##..."),
           (12, ".####|#####|##...|##...|#####|.####"), (18, "#.#####.|########|##.##.##|##.##.##|##.##.##|##.##.##"),
           (27, "#.##.|#####|##.##|##.##|##.##|##.##")]
DOTS = [(x0 + x + 0.5, y + 0.5) for x0, rows in LETTERS for y, row in enumerate(rows.split("|"))
        for x, ch in enumerate(row) if ch == "#"]

CYCLE, NEAR, TURN, HALF = 9.5, 0.6, 8.0, 1.5
K = 12                               # rim points a droplet, joined by a closed Catmull-Rom spline


def fluidity(t: float) -> float:
    x = abs((t % CYCLE) - TURN) / HALF
    return 1 - (1 - NEAR) * ((1 - x) ** 2 if x < 1 else 0)


def make_drops(rng: np.random.Generator) -> list:
    rand = lambda a, b: a + rng.random() * (b - a)
    sign = lambda: -1 if rng.random() < 0.5 else 1
    drops = []
    for x, y in DOTS:
        waves = [(lobes, amp * rand(0.6, 1.3), rand(2.6, 6.2) * sign(), rand(0, 2 * math.pi), rand(0.4, 1.6), rand(0, 2 * math.pi))
                 for lobes, amp in ((2, 0.17), (3, 0.11), (4, 0.07), (5, 0.045), (6, 0.028))]
        drift = (rand(1, 2.4), rand(1, 2.4), rand(0, 2 * math.pi), rand(0, 2 * math.pi))
        drops.append((x, y, 0.45, waves, drift))
    return drops


def outline(drop, t: float, e: float) -> list:
    """The droplet's rim as a dense polygon (the spline sampled 8 times a segment), in grid units."""
    x, y, r, waves, drift = drop
    R = r * (1 - 0.067 * e)
    cx = x + e * 0.14 * math.sin(drift[0] * t + drift[2])
    cy = y + e * 0.14 * math.sin(drift[1] * t + drift[3])
    pts = []
    for k in range(K):
        ang = k / K * 2 * math.pi
        m = 1.0
        for lobes, amp, speed, phase, surge, surge_phase in waves:
            m += e * amp * (0.5 + 0.5 * abs(math.sin(surge * t + surge_phase))) * math.sin(lobes * ang + speed * t + phase)
        px, py = cx + R * m * math.cos(ang) - x, cy + R * m * math.sin(ang) - y
        d = math.hypot(px, py)
        if d > 0.8:                                   # never further than this from its spot: holes stay open
            px, py = px * 0.8 / d, py * 0.8 / d
        pts.append((x + px, y + py))
    out = []
    for i in range(K):
        p0, p1, p2, p3 = pts[(i - 1) % K], pts[i], pts[(i + 1) % K], pts[(i + 2) % K]
        for s in range(8):
            u = s / 8
            u2, u3 = u * u, u * u * u
            out.append(tuple(0.5 * ((2 * p1[j]) + (-p0[j] + p2[j]) * u + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * u2
                                    + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * u3) for j in (0, 1)))
    return out


def frame(drops, t: float) -> np.ndarray:
    e = fluidity(t)
    w, h = W * SS, H * SS
    k = min(w / VIEW[2], h / VIEW[3])
    ox, oy = (w - VIEW[2] * k) / 2 - VIEW[0] * k, (h - VIEW[3] * k) / 2 - VIEW[1] * k
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    for d in drops:
        draw.polygon([(ox + px * k, oy + py * k) for px, py in outline(d, t, e)], fill=255)
    sigma = 0.17 * e * k                              # feGaussianBlur stdDeviation 0.17 (grid units), easing with the fluid
    if sigma > 0.3:
        mask = mask.filter(ImageFilter.GaussianBlur(sigma))
    a = np.asarray(mask, dtype=np.float32) / 255.0
    # the goo's alpha threshold (the SVG's a * 18 - 8, centred on 0.47), made steep: at a neck the blurred alpha lingers
    # near the threshold, and a soft one leaves grey seams there; the supersampling gives the edges their smoothness
    a = np.clip((a - 0.47) * 40 + 0.5, 0, 1)
    small = Image.fromarray((a * 255 + 0.5).astype(np.uint8)).resize((W, H), Image.LANCZOS)
    a = np.asarray(small, dtype=np.float32)[..., None] / 255.0
    return (a * np.array(PAPER, dtype=np.float32) + 0.5).astype(np.uint8)


def main() -> int:
    drops = make_drops(np.random.default_rng(20261013))
    start = 1.5                                       # part-way into the cycle: already moving freely when it shows
    n = int(FPS * SECONDS)
    Image.fromarray(frame(drops, start)).save(OUT / "splash-poster.png", optimize=True)
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-c:v", "libx264", "-preset", "veryslow", "-crf", "14", "-tune", "animation", "-pix_fmt", "yuv420p",
         "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
         "-movflags", "+faststart", "-an", str(OUT / "splash.mp4")], stdin=subprocess.PIPE)
    with ProcessPoolExecutor(max(1, (os.cpu_count() or 2) - 1)) as pool:      # frames in parallel, written in order
        for i, img in enumerate(pool.map(partial(frame, drops), [start + i / FPS for i in range(n)], chunksize=4)):
            ff.stdin.write(img.tobytes())
            if i % 60 == 0:
                print(f"{i / FPS:.0f} s", file=sys.stderr)
    ff.stdin.close()
    return ff.wait()


if __name__ == "__main__":
    raise SystemExit(main())
