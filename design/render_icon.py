"""Render the app icon: the flacmn logo's dot-matrix "mn", off-white on a charcoal rounded square.

    python design/render_icon.py      # writes packaging/icons/iaflac.icns (macOS), iaflac.ico (Windows), iaflac.png

The dots are read from iaflac/gui/static/logo.svg, so the icon always matches the logo. The square follows Apple's
icon grid (824 of 1024 px, corner radius about 22 %, a soft shadow below); Windows uses the same picture.
Needs Pillow; the .icns needs macOS's iconutil.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "packaging" / "icons"
CHARCOAL, PAPER = (0x2E, 0x2E, 0x33), (0xF6, 0xF4, 0xEE)
SS = 4                                            # supersampling, for smooth dot edges at every size


def mn_dots() -> list:
    """The centres of the logo's "mn" dots, in the SVG's units (each dot is a circle of radius 0.3448)."""
    svg = (ROOT / "iaflac" / "gui" / "static" / "logo.svg").read_text(encoding="utf-8")
    group = svg[svg.index('<g transform="translate(3.83495,46.176167)">'):]
    d = re.search(r'<path d="([^"]+)"', group).group(1)
    x = y = 0.0
    dots = []
    for dx, dy in re.findall(r"m\s*(-?[\d.]+),(-?[\d.]+)", d):   # each dot starts with a relative move to its left edge
        x, y = x + float(dx), y + float(dy)
        dots.append((x + 0.3448, y))
    return dots


def icon(size: int = 1024) -> Image.Image:
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    inset = round(s * 100 / 1024)                 # Apple's grid: the square is 824 of 1024
    box = (inset, inset, s - inset, s - inset)
    radius = round((box[2] - box[0]) * 0.2237)
    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((box[0], box[1] + s * 0.012, box[2], box[3] + s * 0.012), radius, fill=(0, 0, 0, 110))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(s * 0.014)))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(box, radius, fill=CHARCOAL + (255,))
    dots = mn_dots()
    xs, ys = [p[0] for p in dots], [p[1] for p in dots]
    r = 0.3448
    w, h = max(xs) - min(xs) + 2 * r, max(ys) - min(ys) + 2 * r
    k = (box[2] - box[0]) * 0.70 / w                # the mn spans 70 % of the square
    ox = (s - w * k) / 2 - (min(xs) - r) * k
    oy = (s - h * k) / 2 - (min(ys) - r) * k
    for x, y in dots:
        cx, cy = ox + x * k, oy + y * k
        draw.ellipse((cx - r * k, cy - r * k, cx + r * k, cy + r * k), fill=PAPER + (255,))
    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    big = icon(1024)
    big.save(OUT / "iaflac.png")
    big.save(OUT / "iaflac.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "iaflac.iconset"
            iconset.mkdir()
            for pt in (16, 32, 128, 256, 512):
                icon(pt).save(iconset / f"icon_{pt}x{pt}.png")
                icon(pt * 2).save(iconset / f"icon_{pt}x{pt}@2x.png")
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT / "iaflac.icns")], check=True)
    else:
        print("iconutil not found (not macOS): iaflac.icns not written")
    print(f"icons in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
