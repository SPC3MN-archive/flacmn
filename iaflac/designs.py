"""Designs for the parts flacmn generates: any part not printed from a scan or an upload, whether it is left plain or
filled with a colour. A design is a layout, a size of the spcmn mark on it, and the mark's colour:

  layouts   title     the album, artist and year (front covers and disc labels)
            list      the title, then the tracks with their lengths (the original tracklist)
            centred   the same, centred down the middle
            columns   the tracks in two columns, however few there are
            compact   numbers and titles only, small
            blank     the colour alone

  logo      the mark on a generated part (on a CD only the back and its spines carry it; on a cassette every
            generated part does): small (in a corner, or at the end of a spine; the default),
            medium (centred low, or centred on a blank), large (nearly the full width: on blank and title layouts,
            where nothing else needs the room). "none" leaves it off (kept for callers; the app always shows it)

  ink       charcoal (#2E2E33, the spcmn text colour) on a light panel, off-white (#F6F4EE, the spcmn page colour)
            on a dark one: chosen automatically ("auto"), or forced with "charcoal" / "off-white"

A generated back (CD tray card) or spine and flap (cassette) puts a small mark on its spines too.
The mark is spcmn's still wordmark: 138 dots on a 32 x 8 grid, the p descending two rows.
"""

from __future__ import annotations

import math
from typing import Optional

from reportlab.lib.units import mm

LAYOUTS = ("title", "list", "centred", "columns", "compact", "blank")
TRACKLISTS = ("list", "centred", "columns", "compact")
LOGOS = ("none", "small", "medium", "large")
DEFAULT_LOGO = "small"

# spcmn's dot letters, one # per dot on a grid of 1 (dots 0.9 across), as on the spcmn site: s p c, then m n
_LETTERS = (
    ("s", 0, ".####|#####|###..|..###|#####|####."),
    ("p", 6, "####.|#####|##.##|##.##|#####|####.|##...|##..."),
    ("c", 12, ".####|#####|##...|##...|#####|.####"),
    ("m", 18, "#.#####.|########|##.##.##|##.##.##|##.##.##|##.##.##"),
    ("n", 27, "#.##.|#####|##.##|##.##|##.##|##.##"),
)
LOGO_DOTS = [(letter, x0 + x + 0.5, y + 0.5) for letter, x0, rows in _LETTERS
             for y, row in enumerate(rows.split("|")) for x, ch in enumerate(row) if ch == "#"]
LOGO_W, LOGO_H, LOGO_CAP = 32.0, 8.0, 6.0          # grid units; the letters stand 6 high, the p hangs 2 below


def _rgb(hexcode: str) -> tuple:
    return tuple(int(hexcode[i:i + 2], 16) / 255 for i in (1, 3, 5))


CHARCOAL, OFF_WHITE = _rgb("#2E2E33"), _rgb("#F6F4EE")
SOLID_INKS = {"charcoal": CHARCOAL, "off-white": OFF_WHITE}
INKS = ("auto", "charcoal", "off-white")


def logos_for(layout: str) -> tuple:
    """The mark's sizes a layout has room for (the app offers all but "none"): large only where there is no tracklist."""
    return LOGOS if layout in ("title", "blank") else LOGOS[:3]


def logo_colours(ink: str, bg: tuple):
    """letter -> rgb for one of INKS on a panel of colour `bg`: charcoal or off-white, whichever reads (or as forced)."""
    from .jewelcase import text_colour
    if ink not in SOLID_INKS:
        ink = "charcoal" if text_colour(bg) == (0, 0, 0) else "off-white"
    rgb = SOLID_INKS[ink]
    return lambda letter: rgb


def draw_logo(c, x: float, y: float, width: float, bg: tuple, ink: str = "auto") -> None:
    """The spcmn mark, `width` mm wide, its letters' baseline-left at (x, y) mm in the canvas's current frame (the p
    descends below y), coloured by `ink` (one of INKS) for a panel of colour `bg`."""
    k = width / LOGO_W
    colour = logo_colours(ink, bg)
    c.saveState()
    for letter, gx, gy in LOGO_DOTS:
        c.setFillColorRGB(*colour(letter))
        c.circle((x + gx * k) * mm, (y + (LOGO_CAP - gy) * k) * mm, 0.45 * k * mm, stroke=0, fill=1)
    c.restoreState()


def _fill(sh, x, y, w, h, bg):
    b = sh.opts.bleed
    sh.c.saveState()
    sh.c.setFillColorRGB(*bg)
    sh.c.rect((x - b) * mm, (y - b) * mm, (w + 2 * b) * mm, (h + 2 * b) * mm, stroke=0, fill=1)
    sh.c.restoreState()


def logo_place(layout: str, size: str, x: float, y: float, w: float, h: float) -> Optional[tuple]:
    """(left, baseline, width) of the mark on a panel, or None. Small sits in a corner (bottom-left beside a list,
    whose total runs bottom-right); medium centred low beside text, centred on a blank; large centred, near full width."""
    if size not in LOGOS or size == "none":
        return None
    if size == "large" and layout not in ("title", "blank"):
        size = "medium"
    tail = lambda lw: 2 * lw / LOGO_W                    # the p hangs below the baseline
    middle = lambda lw, top=h: y + top / 2 - (LOGO_CAP / 2 - 1) * lw / LOGO_W
    if size == "small":
        lw = min(w * 0.24, 28.0)
        if layout == "centred":
            return x + (w - lw) / 2, y + 6 + tail(lw), lw
        left = x + 8 if layout in ("list", "columns") else x + w - 7 - lw
        return left, y + 6 + tail(lw), lw
    if size == "medium":
        lw = min(w * 0.44, 52.0)
        if layout == "blank":
            return x + (w - lw) / 2, middle(lw), lw
        return x + (w - lw) / 2, y + 8 + tail(lw), lw
    lw = min(w * 0.86, h * 3)
    return x + (w - lw) / 2, middle(lw, h - 24 if layout == "title" else h), lw   # title: centred below the title


def logo_reserve(layout: str, size: str, w: float, h: float) -> float:
    """How much of a panel's foot the mark takes, so a tracklist stops above it."""
    place = logo_place(layout, size, 0, 0, w, h)
    if place is None or layout not in TRACKLISTS:
        return 0.0
    _, base, lw = place
    return base + LOGO_CAP * lw / LOGO_W + 3


def draw_design(sh, x: float, y: float, w: float, h: float, layout: str, artist: str, album: str, year: str,
                tracks: list, bg: tuple, logo: str = "none", ink: str = "auto") -> None:
    """Draw a generated part: its layout on the colour `bg`, then the spcmn mark if one is chosen."""
    from .jewelcase import draw_tracklist
    reserve = logo_reserve(layout, logo, w, h)
    if layout == "blank":
        _fill(sh, x, y, w, h, bg)
    elif layout == "title":
        draw_tracklist(sh, x, y, w, h, artist, album, year, [], bg)
    elif layout == "centred":
        draw_centred(sh, x, y, w, h, artist, album, year, tracks, bg, reserve)
    elif layout == "compact":
        draw_compact(sh, x, y, w, h, artist, album, year, tracks, bg, reserve)
    else:
        draw_tracklist(sh, x, y, w, h, artist, album, year, tracks, bg, columns=2 if layout == "columns" else None,
                       reserve=reserve)
    place = logo_place(layout, logo, x, y, w, h)
    if place:
        draw_logo(sh.c, *place, bg, ink)


def draw_centred(sh, x, y, w, h, artist, album, year, tracks, bg, reserve: float = 0.0) -> None:
    from .jewelcase import fmt_len, font, text_colour
    c = sh.c
    regular, bold = font()
    _fill(sh, x, y, w, h, bg)
    c.saveState()
    c.setFillColorRGB(*text_colour(bg))
    cx = x + w / 2
    name, size, t = sh.fit_text(album, 14, w - 20, bold=True)
    c.setFont(name, size)
    c.drawCentredString(cx * mm, (y + h - 15) * mm, t)
    name, size, t = sh.fit_text(artist + (f" · {year}" if year else ""), 9, w - 20)
    c.setFont(name, size)
    c.drawCentredString(cx * mm, (y + h - 21) * mm, t)
    rows = sorted(tracks, key=lambda t: (t["disc"], t["track"]))
    line = min(4.8, (h - 38 - reserve) / max(len(rows), 1))
    fsize = max(4.5, min(8.0, line * 2.0))
    multi = len({t["disc"] for t in rows}) > 1
    for i, tr in enumerate(rows):
        num = f"{tr['disc']}-{tr['track']}" if multi else f"{tr['track']}"
        dur = fmt_len(tr.get("length"))
        _, s2, text = sh.fit_text(f"{num}  {tr['title']}" + (f"  {dur}" if dur else ""), fsize, w - 16)
        c.setFont(regular, s2)
        c.drawCentredString(cx * mm, (y + h - 30 - i * line) * mm, text)
    c.restoreState()


def draw_compact(sh, x, y, w, h, artist, album, year, tracks, bg, reserve: float = 0.0) -> None:
    from .jewelcase import font, text_colour
    c = sh.c
    regular, bold = font()
    _fill(sh, x, y, w, h, bg)
    c.saveState()
    c.setFillColorRGB(*text_colour(bg))
    name, size, t = sh.fit_text(album, 10, w - 16, bold=True)
    c.setFont(name, size)
    c.drawString((x + 8) * mm, (y + h - 11) * mm, t)
    name, size, t = sh.fit_text(artist + (f" · {year}" if year else ""), 7, w - 16)
    c.setFont(name, size)
    c.drawString((x + 8) * mm, (y + h - 15.5) * mm, t)
    rows = sorted(tracks, key=lambda t: (t["disc"], t["track"]))
    columns = 1 if len(rows) <= 22 else 2
    per = math.ceil(len(rows) / columns) if rows else 1
    line = min(3.6, (h - 34 - reserve) / per)
    fsize = max(4.2, min(6.5, line * 1.9))
    col_w = (w - 16) / columns
    for i, tr in enumerate(rows):
        col, row = divmod(i, per)
        _, s2, text = sh.fit_text(f"{tr['track']:>2}  {tr['title']}", fsize, col_w - 3)
        c.setFont(regular, s2)
        c.drawString((x + 8 + col * col_w) * mm, (y + h - 22 - row * line) * mm, text)
    c.restoreState()


def spine_logo_width(spine_w: float, length: float) -> float:
    """A spine's small mark: as wide (along the spine) as fits its narrow height, and never a fifth of its length."""
    return min(length * 0.2, (spine_w - 1.6) * LOGO_W / LOGO_H)
