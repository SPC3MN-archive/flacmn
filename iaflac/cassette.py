"""Print sheets for a compact cassette's J-card, as a PDF at true size (print at 100% / "actual size", not "fit").

The standard J-card is 4 in (101.6 mm) tall and 4 1/8 in (104.8 mm) wide unfolded, in three panels:

    back flap  1 1/16 in  27.0 mm      tucks behind the tape; seen through the back of the case: the tracklist
    spine      1/2 in     12.7 mm      artist and title, read on the shelf
    front      2 9/16 in  65.1 mm      the album art

Page 1 is the outside (flap | spine | front, as it lies flat, front upright) with crop marks and fold marks; page 2 is
the inside, printed on the back of page 1 (double-sided, flip on the long edge): the full tracklist split into side A
and side B behind the front panel. Sides are balanced by running time, and the sheet suggests the shortest standard
tape (C46, C60, C90, C120) that holds the longer side.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .jewelcase import (PAPERS, SOFT_DPI, Art, Colour, Piece, PrintOptions, PrintReport, Sheet, edge_colour, fit_cover,
                        fmt_len, font, reader, text_colour)

JCARD_H = 101.6
FLAP_W, SPINE_W, FRONT_W = 27.0, 12.7, 65.1
JCARD_W = FLAP_W + SPINE_W + FRONT_W          # 104.8
TAPES = ((23 * 60, "C46"), (30 * 60, "C60"), (45 * 60, "C90"), (60 * 60, "C120"))   # minutes per side
SQUARE_ART = 0.8                               # wider than this (w/h) is a square cover: art on top, title below


def split_sides(tracks: list) -> tuple:
    """(side A, side B): the album in order, cut where the two sides' running times are closest."""
    tracks = sorted(tracks, key=lambda t: (t.get("disc") or 1, t.get("track") or 0))
    if len(tracks) < 2:
        return tracks, []
    lengths = [t.get("length") or 0 for t in tracks]
    total = sum(lengths)
    best, best_gap, run = 1, None, 0.0
    for i in range(1, len(tracks)):
        run += lengths[i - 1]
        gap = abs(total - 2 * run)
        if best_gap is None or gap < best_gap:
            best, best_gap = i, gap
    return tracks[:best], tracks[best:]


def tape_for(side_a: list, side_b: list) -> str:
    """The shortest standard tape whose sides hold the longer side ("" when lengths are unknown or too long)."""
    longest = max(sum(t.get("length") or 0 for t in side) for side in (side_a, side_b))
    if not longest:
        return ""
    return next((name for seconds, name in TAPES if longest <= seconds), "")


def layout(opts: Optional[PrintOptions] = None) -> tuple:
    """(left, bottom) of the J-card on the page in mm (reportlab's origin is bottom-left), centred near the top so
    the inside on page 2 lands right behind it when printed double-sided."""
    opts = opts or PrintOptions()
    pw, ph = (v / mm for v in PAPERS.get(opts.paper, LETTER))
    return (pw - JCARD_W) / 2, ph - 14 - JCARD_H


def part_boxes(opts: Optional[PrintOptions] = None) -> dict:
    """{part: (page index, left mm, top mm, width mm, height mm)} from the page's top-left, as render() draws them."""
    left, _ = layout(opts)
    top = 14.0
    return {"outside": (0, left, top, JCARD_W, JCARD_H),
            "inside": (1, left, top, JCARD_W, JCARD_H),
            "spine": (0, left + FLAP_W, top, SPINE_W, JCARD_H),
            "front": (0, left + FLAP_W + SPINE_W, top, FRONT_W, JCARD_H),
            "flap": (0, left, top, FLAP_W, JCARD_H)}


def part_images(pdf: Path, opts: Optional[PrintOptions] = None, px_per_mm: float = 6.0) -> dict:
    """{part: PNG bytes}: the J-card's outside, inside, spine, front and flap cut out of the print PDF at trim size."""
    import io
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf))
    try:
        pages, out = {}, {}
        for part, (page, x, y, w, h) in part_boxes(opts).items():
            if page not in pages:
                pages[page] = doc[page].render(scale=px_per_mm * 25.4 / 72).to_pil().convert("RGB")
            crop = pages[page].crop(tuple(round(v * px_per_mm) for v in (x, y, x + w, y + h)))
            buf = io.BytesIO()
            crop.save(buf, "PNG")
            out[part] = buf.getvalue()
        return out
    finally:
        doc.close()


# ---- drawing --------------------------------------------------------------------------------------------------------

def _fill(sh: Sheet, x: float, y: float, w: float, h: float, rgb: tuple, bleed: tuple = (1, 1, 1, 1)) -> None:
    """A solid panel; bleed=(left, right, bottom, top) says which edges run past the cut line."""
    b = sh.opts.bleed
    l, r, bt, t = (b * e for e in bleed)
    sh.c.saveState()
    sh.c.setFillColorRGB(*rgb)
    sh.c.rect((x - l) * mm, (y - bt) * mm, (w + l + r) * mm, (h + bt + t) * mm, stroke=0, fill=1)
    sh.c.restoreState()


def _sides_text(sh: Sheet, x: float, y: float, w: float, h: float, side_a: list, side_b: list, fg: tuple,
                size: float, with_lengths: bool, tape: str = "") -> None:
    """Side A then side B as a compact list, shrunk until it fits the box."""
    regular, bold = font()
    c = sh.c
    lines = len(side_a) + len(side_b) + (2 if side_b else 1) + 1
    size = max(3.6, min(size, (h - 4) / lines / 0.42))
    line = size * 0.42                                   # mm per line at this point size
    c.saveState()
    c.setFillColorRGB(*fg)
    ty = y + h - 2 - line
    for name, side in (("SIDE A", side_a), ("SIDE B", side_b)):
        if not side:
            continue
        total = sum(t.get("length") or 0 for t in side)
        c.setFont(bold, size)
        c.drawString(x * mm, ty * mm, name)
        if with_lengths and total:
            c.setFont(regular, size)
            c.drawRightString((x + w) * mm, ty * mm, fmt_len(total))
        ty -= line
        for n, t in enumerate(side, 1):
            c.setFont(regular, size)
            label = f"{n}. " if not with_lengths else f"{n:>2}  "
            right = fmt_len(t.get("length")) if with_lengths else ""
            room = w - (pdf_width(right, size) + 2 if right else 0)
            _, s2, title = sh.fit_text(label + (t.get("title") or ""), size, room)
            c.setFont(regular, s2)
            c.drawString(x * mm, ty * mm, title)
            if right:
                c.setFont(regular, size)
                c.drawRightString((x + w) * mm, ty * mm, right)
            ty -= line
        ty -= line * 0.6
    if tape and with_lengths:
        c.setFont(regular, size)
        c.drawRightString((x + w) * mm, (y + 2) * mm, f"fits a {tape}")
    c.restoreState()


def pdf_width(text: str, size: float) -> float:
    from reportlab.pdfbase import pdfmetrics
    return pdfmetrics.stringWidth(text, font()[0], size) / mm


def draw_front(sh: Sheet, x: float, y: float, art, bg: tuple, artist: str, album: str, year: str,
               layout: str = "title", logo: str = "none", ink: str = "auto") -> Optional[int]:
    """The front panel: a portrait image fills it; a square cover sits on top with the title below on the album colour;
    with no image it is generated: the title (layout "title") or nothing ("blank"), and the spcmn mark if chosen.
    Returns the image's print dpi (None when generated)."""
    from .designs import draw_logo, logo_place
    c = sh.c
    img = art.image() if isinstance(art, Art) else None
    if img is not None and img.size[0] / img.size[1] < SQUARE_ART:
        return sh.image(img, x, y, FRONT_W, JCARD_H)
    _fill(sh, x, y, FRONT_W, JCARD_H, bg, (0, 1, 1, 1))
    fg = text_colour(bg)
    dpi = None
    text_top = y + JCARD_H - 10
    if img is not None:
        b = sh.opts.bleed
        fitted, dpi = fit_cover(img, FRONT_W + b, FRONT_W + b)        # bleeds off the top and the right edge
        c.drawImage(reader(fitted), x * mm, (y + JCARD_H - FRONT_W) * mm, (FRONT_W + b) * mm, (FRONT_W + b) * mm)
        text_top = y + JCARD_H - FRONT_W - 9
    if img is not None or layout != "blank":
        regular, bold = font()
        c.saveState()
        c.setFillColorRGB(*fg)
        name, size, t = sh.fit_text(album, 12, FRONT_W - 10, bold=True)
        c.setFont(name, size)
        c.drawString((x + 5) * mm, text_top * mm, t)
        name, size, t = sh.fit_text(artist + (f" · {year}" if year else ""), 8, FRONT_W - 10)
        c.setFont(name, size)
        c.drawString((x + 5) * mm, (text_top - 6) * mm, t)
        c.restoreState()
    if img is None:
        place = logo_place(layout, logo, x, y, FRONT_W, JCARD_H)
        if place:
            draw_logo(c, *place, bg, ink)
    return dpi


def draw_spine(sh: Sheet, x: float, y: float, bg: tuple, artist: str, album: str, logo_ink: Optional[str] = None) -> None:
    """Artist and album along the spine, reading top to bottom (as English-language book and tape spines do); with
    `logo_ink`, a small spcmn mark at the foot, where the text finishes reading."""
    from .designs import LOGO_CAP, LOGO_W, draw_logo, spine_logo_width
    c = sh.c
    _fill(sh, x, y, SPINE_W, JCARD_H, bg, (0, 0, 1, 1))
    lw = spine_logo_width(SPINE_W * 0.5, JCARD_H) if logo_ink else 0.0
    room = JCARD_H - 10 - (lw + 4 if lw else 0)
    shift = -(lw + 4) / 2 if lw else 0.0
    regular, bold = font()
    c.saveState()
    c.setFillColorRGB(*text_colour(bg))
    c.translate((x + SPINE_W / 2) * mm, (y + JCARD_H / 2) * mm)
    c.rotate(-90)
    name, size, t = sh.fit_text(album, 9, room, bold=True)
    c.setFont(name, size)
    c.drawCentredString(shift * mm, 1.0 * mm, t)
    name, size, t = sh.fit_text(artist, 7, room)
    c.setFont(name, size)
    c.drawCentredString(shift * mm, -3.2 * mm, t)
    if lw:
        k = lw / LOGO_W
        draw_logo(c, JCARD_H / 2 - 5 - lw, -(LOGO_CAP / 2 - 1) * k, lw, bg, logo_ink)
    c.restoreState()


def render(path: Path, *, artist: str, album: str, year: str = "", tracks: Optional[list] = None,
           front=None, back=None, inside=None, opts: Optional[PrintOptions] = None, styles: Optional[dict] = None,
           **_unused) -> PrintReport:
    """Write the J-card PDF to `path`. front: an Art (the cover) or a Colour/None (title only); back: the spine and
    flap's colour (a Colour, or an Art whose edge colour is used); inside: the inside's colour (Colour/Art/None)."""
    from .designs import DEFAULT_LOGO, LOGO_CAP, LOGO_W, draw_logo, logo_place
    opts = opts or PrintOptions()
    tracks = tracks or []
    styles = styles or {}
    report = PrintReport(paper=opts.paper)
    page_size = PAPERS.get(opts.paper, LETTER)
    c = canvas.Canvas(str(path), pagesize=page_size)
    c.setTitle(f"{artist} - {album}: cassette J-card")
    c.setAuthor("flacmn")
    sh = Sheet(c, opts)
    left, bottom = layout(opts)
    title = f"{artist} — {album}" + (f" ({year})" if year else "")
    front_img = front.image() if isinstance(front, Art) else None

    def colour_of(part, fallback):
        if isinstance(part, Colour):
            return part.rgb
        if isinstance(part, Art):
            return edge_colour(part.image())
        return fallback

    main = colour_of(back, edge_colour(front_img) if front_img is not None else (1, 1, 1))
    page = [1]

    def piece(name, source, dpi=None):
        warn = f"{dpi} dpi: will print soft" if dpi is not None and dpi < SOFT_DPI else ""
        report.pieces.append(Piece(name, page[0], source, dpi, warn))

    def footer(text):
        sh.label(f"{title}  ·  {text}  ·  print at 100% (actual size)", 12, 6)

    side_a, side_b = split_sides(tracks)
    tape = tape_for(side_a, side_b)

    def design(part, default):
        return styles.get(part, default), styles.get(f"{part}:logo", DEFAULT_LOGO), styles.get(f"{part}:ink", "auto")

    def with_mark(logo):
        return f" with the spcmn mark ({logo})" if logo != "none" else ""

    # ---- page 1: outside, flat: back flap | spine | front
    fx, sx, frx = left, left + FLAP_W, left + FLAP_W + SPINE_W
    # the flap and spine are always generated, in the "back" part's colour: its layout is the flap's, its mark goes on both
    flap_layout, flap_logo, flap_ink = design("back", "list")
    _fill(sh, fx, bottom, FLAP_W, JCARD_H, main, (1, 0, 1, 1))
    flap_lw = FLAP_W - 7.0
    flap_mark = 0.0 if flap_logo == "none" else LOGO_CAP * flap_lw / LOGO_W + 2 * flap_lw / LOGO_W + 7
    if flap_layout != "blank":
        _sides_text(sh, fx + 2.5, bottom + 3 + flap_mark, FLAP_W - 5, JCARD_H - 6 - flap_mark, side_a, side_b,
                    text_colour(main), 5.5, False)
    if flap_logo != "none":                                     # at the flap's foot, or centred on a blank flap
        base = bottom + JCARD_H / 2 - 2 * flap_lw / LOGO_W if flap_layout == "blank" else bottom + 5 + 2 * flap_lw / LOGO_W
        draw_logo(c, fx + (FLAP_W - flap_lw) / 2, base, flap_lw, main, flap_ink)
    piece("back flap", ("generated: tracklist (sides A and B)" if flap_layout != "blank" else "generated: blank") + with_mark(flap_logo))
    front_layout, front_logo, front_ink = design("front", "title")
    dpi = draw_front(sh, frx, bottom, front, main, artist, album, year, front_layout, front_logo, front_ink)  # before the spine
    piece("front", front.label if isinstance(front, Art)
          else f"generated: {'title only' if front_layout == 'title' else 'blank'} (no front cover chosen){with_mark(front_logo)}", dpi)
    draw_spine(sh, sx, bottom, main, artist, album, flap_ink if flap_logo != "none" else None)
    piece("spine", f"generated: title on {'the ' + back.label if isinstance(back, Colour) else 'the album colour'}{with_mark('small' if flap_logo != 'none' else 'none')}")
    sh.marks(left, bottom, JCARD_W, JCARD_H)
    sh.fold(sx, bottom, JCARD_H)
    sh.fold(frx, bottom, JCARD_H)
    footer("OUTSIDE (page 1)")
    c.showPage()
    page[0] += 1

    # ---- page 2: inside, on the back of page 1 (mirrored: the front panel's inside is on the left)
    saved, opts.crop_marks = opts.crop_marks, False
    inner = colour_of(inside, (1, 1, 1))
    if isinstance(inside, Art):
        img = inside.image()
        piece("inside", inside.label, sh.image(img, left, bottom, JCARD_W, JCARD_H))
        inner = edge_colour(img)
    else:
        _fill(sh, left, bottom, JCARD_W, JCARD_H, inner)
    in_layout, in_logo, in_ink = design("inside", "list")
    if not isinstance(inside, Art):
        on = inside.label if isinstance(inside, Colour) else "white"
        piece("inside", f"generated: {'tracklist' if in_layout != 'blank' else 'blank'} on {on}{with_mark(in_logo)}")
        place = logo_place("centred" if in_layout != "blank" else "blank", in_logo, left, bottom, FRONT_W, JCARD_H)
        reserve = (place[1] - bottom + LOGO_CAP * place[2] / LOGO_W + 3) if place and in_layout != "blank" else 0.0
        if in_layout != "blank":
            regular, bold = font()
            fg = text_colour(inner)
            c.saveState()
            c.setFillColorRGB(*fg)
            name, size, t = sh.fit_text(album, 10, FRONT_W - 10, bold=True)
            c.setFont(name, size)
            c.drawString((left + 5) * mm, (bottom + JCARD_H - 9) * mm, t)
            name, size, t = sh.fit_text(artist + (f" · {year}" if year else ""), 7, FRONT_W - 10)
            c.setFont(name, size)
            c.drawString((left + 5) * mm, (bottom + JCARD_H - 14) * mm, t)
            c.restoreState()
            _sides_text(sh, left + 5, bottom + 4 + reserve, FRONT_W - 10, JCARD_H - 22 - reserve, side_a, side_b, fg, 7, True,
                        tape)
        if place:
            draw_logo(c, *place, inner, in_ink)
    opts.crop_marks = saved
    footer("INSIDE (page 2): print on the back of page 1, double-sided, flip on the long edge")
    c.showPage()
    c.save()
    report.pages = 2
    report.instructions = [
        "Print at 100% / Actual size (turn off 'Fit to page').",
        "Pages 1 and 2 are one sheet: print double-sided, flipping on the long edge, then cut on page 1's crop marks.",
        "Fold on the dashed marks: the front faces out of the case, the spine sits at the case's edge and the flap "
        "tucks behind the tape.",
        f"Side A and side B are balanced by running time{f'; the longer side fits a {tape}' if tape else ''}.",
    ]
    return report
