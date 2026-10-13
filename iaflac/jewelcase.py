"""Print sheets for a standard CD jewel case, as a PDF at true size (print at 100% / "actual size", not "fit").

    front insert   120 x 120 mm         printed both sides: front cover outside, inside of the insert on the back
    tray card      151 x 118 mm         = two 6.5 mm spines + 138 mm back; on its reverse, the under-tray image
    disc label     118 mm across, with a centre hole (41 mm for most sticker labels; 23 mm or less for printable discs)

Every piece has crop marks; images run 1.5 mm past the cut line (bleed) so a slightly-off cut leaves no white edge.
Page 1 is the outside of the case, page 2 the inside (printed on the back of page 1), page 3 the disc label.
Anything missing is generated: a tracklist tray card or inside panel, spines with the artist and album, a disc label
cut from the front cover. Each piece reports the resolution its image prints at; under 200 dpi it will look soft.
"""

from __future__ import annotations

import io
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FRONT_W = FRONT_H = 120.0
TRAY_W, TRAY_H, SPINE = 151.0, 118.0, 6.5
DISC_OUTER = 118.0
PRINT_DPI = 300
SOFT_DPI = 200
MARK_LEN, MARK_GAP = 5.0, 2.0
SPINE_RATIO = TRAY_W / TRAY_H         # 1.28: a back scan this wide includes its spines
BACK_RATIO = (TRAY_W - 2 * SPINE) / TRAY_H
PAPERS = {"letter": LETTER, "a4": A4}


@dataclass
class PrintOptions:
    paper: str = "letter"            # letter | a4
    bleed: float = 1.5               # mm of image past the cut line
    disc_inner: float = 41.0         # centre hole diameter in mm
    disc_outer: float = DISC_OUTER
    crop_marks: bool = True
    discs: int = 1                   # disc labels to print (a 2-CD set needs 2)


@dataclass
class Piece:
    name: str                        # "front cover", "back + spines", "inside of front insert", "under-tray image", "disc label"
    page: int
    source: str                      # image label, or "generated: ..."
    dpi: Optional[int] = None
    warning: str = ""


@dataclass
class PrintReport:
    pages: int = 0
    paper: str = "letter"
    pieces: list = field(default_factory=list)          # [Piece]
    instructions: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"pages": self.pages, "paper": self.paper, "pieces": [asdict(p) for p in self.pieces],
                "instructions": list(self.instructions)}


@dataclass
class Art:
    """One image to print: its bytes and where it came from."""
    data: bytes
    label: str

    def image(self) -> Image.Image:
        img = Image.open(io.BytesIO(self.data))
        img.load()
        return img.convert("RGB")


@dataclass
class Colour:
    """A solid fill in one of the album's colours, for a part with no scan; generated text goes on top of it."""
    hex: str                                             # "#rrggbb"

    @property
    def rgb(self) -> tuple:
        return tuple(int(self.hex[i:i + 2], 16) / 255 for i in (1, 3, 5))

    @property
    def label(self) -> str:
        return f"album colour {self.hex}"


# ---- fonts -------------------------------------------------------------------------------------------------------

_FONT = None


def font() -> tuple:
    """(regular, bold) font names. DejaVu (shipped with matplotlib) covers accents and most scripts; Helvetica otherwise."""
    global _FONT
    if _FONT is None:
        try:
            import matplotlib
            base = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
            pdfmetrics.registerFont(TTFont("DejaVu", str(base / "DejaVuSans.ttf")))
            pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(base / "DejaVuSans-Bold.ttf")))
            _FONT = ("DejaVu", "DejaVu-Bold")
        except Exception:
            _FONT = ("Helvetica", "Helvetica-Bold")
    return _FONT


# ---- image helpers -----------------------------------------------------------------------------------------------

def fit_cover(img: Image.Image, w_mm: float, h_mm: float) -> tuple:
    """Crop `img` to the target shape (centred) and scale it to PRINT_DPI. Returns (image, effective dpi)."""
    target = w_mm / h_mm
    iw, ih = img.size
    if iw / ih > target:
        nw = int(ih * target)
        img = img.crop(((iw - nw) // 2, 0, (iw - nw) // 2 + nw, ih))
    else:
        nh = int(iw / target)
        img = img.crop((0, (ih - nh) // 2, iw, (ih - nh) // 2 + nh))
    dpi = int(img.size[0] / (w_mm / 25.4))
    px = (max(1, int(w_mm / 25.4 * PRINT_DPI)), max(1, int(h_mm / 25.4 * PRINT_DPI)))
    if img.size[0] > px[0]:
        img = img.resize(px, Image.LANCZOS)
    return img, dpi


def reader(img: Image.Image) -> ImageReader:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    buf.seek(0)
    return ImageReader(buf)


def split_spread(img: Image.Image) -> list:
    """A scan that shows two pages side by side (an opened booklet) becomes two pages."""
    w, h = img.size
    if w / h < 1.6:
        return [img]
    return [img.crop((0, 0, w // 2, h)), img.crop((w // 2, 0, w, h))]


def edge_colour(img: Image.Image) -> tuple:
    small = img.resize((32, 32))
    px = [small.getpixel((x, y)) for x in (0, 31) for y in range(32)]
    return tuple(sum(p[i] for p in px) / len(px) / 255 for i in range(3))


def text_colour(rgb: tuple) -> tuple:
    lum = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    return (0, 0, 0) if lum > 0.55 else (1, 1, 1)


# ---- drawing -----------------------------------------------------------------------------------------------------

class Sheet:
    def __init__(self, c: canvas.Canvas, opts: PrintOptions):
        self.c, self.opts = c, opts

    def marks(self, x: float, y: float, w: float, h: float) -> None:
        if not self.opts.crop_marks:
            return
        c, g, L = self.c, MARK_GAP + self.opts.bleed, MARK_LEN
        c.saveState()
        c.setLineWidth(0.25)
        c.setStrokeColorRGB(0, 0, 0)
        for cx, sx in ((x, -1), (x + w, 1)):
            for cy, sy in ((y, -1), (y + h, 1)):
                c.line(cx * mm, (cy + sy * g) * mm, cx * mm, (cy + sy * (g + L)) * mm)
                c.line((cx + sx * g) * mm, cy * mm, (cx + sx * (g + L)) * mm, cy * mm)
        c.restoreState()

    def fold(self, x: float, y: float, h: float) -> None:
        """A dashed fold mark just outside the piece, above and below (never across the art)."""
        c, g = self.c, MARK_GAP + self.opts.bleed
        c.saveState()
        c.setLineWidth(0.25)
        c.setDash(1.2 * mm, 0.8 * mm)
        c.line(x * mm, (y - g) * mm, x * mm, (y - g - MARK_LEN) * mm)
        c.line(x * mm, (y + h + g) * mm, x * mm, (y + h + g + MARK_LEN) * mm)
        c.restoreState()

    def image(self, img: Image.Image, x: float, y: float, w: float, h: float) -> int:
        b = self.opts.bleed
        fitted, dpi = fit_cover(img, w + 2 * b, h + 2 * b)
        self.c.drawImage(reader(fitted), (x - b) * mm, (y - b) * mm, (w + 2 * b) * mm, (h + 2 * b) * mm)
        return dpi

    def label(self, text: str, x: float, y: float, size: float = 7) -> None:
        self.c.saveState()
        self.c.setFont(font()[0], size)
        self.c.setFillColorRGB(0.35, 0.35, 0.35)
        self.c.drawString(x * mm, y * mm, text)
        self.c.restoreState()

    def fit_text(self, text: str, size: float, max_mm: float, bold: bool = False) -> tuple:
        name = font()[1 if bold else 0]
        while size > 4 and pdfmetrics.stringWidth(text, name, size) > max_mm * mm:
            size -= 0.25
        while pdfmetrics.stringWidth(text, name, size) > max_mm * mm and len(text) > 4:
            text = text[:-2].rstrip() + "…"
        return name, size, text


MARKED_PARTS = ("front", "back", "disc")   # the CD parts that may carry the spcmn mark (the back puts it on its spines too);
                                           # the inside of the front insert and the under-tray image never do


def draw_spines(sh: Sheet, x: float, y: float, text: str, bg: tuple, logo_ink: Optional[str] = None) -> None:
    """Both spines of the tray card, with the artist and album; with `logo_ink`, a small spcmn mark at the end of each
    (where its text finishes reading), the text centred in what is left."""
    from .designs import LOGO_CAP, LOGO_W, draw_logo, spine_logo_width
    c = sh.c
    fg = text_colour(bg)
    lw = spine_logo_width(SPINE, TRAY_H) if logo_ink else 0.0
    room = TRAY_H - 8 - (lw + 4 if lw else 0)
    for sx, angle in ((x, 90), (x + TRAY_W - SPINE, -90)):
        c.saveState()
        c.setFillColorRGB(*bg)
        b = sh.opts.bleed
        c.rect((sx - (b if sx == x else 0)) * mm, (y - b) * mm, (SPINE + b) * mm, (TRAY_H + 2 * b) * mm, stroke=0, fill=1)
        c.translate((sx + SPINE / 2) * mm, (y + TRAY_H / 2) * mm)    # the spine's middle; rotated, x runs along it
        c.rotate(angle)                                               # left reads bottom to top, right top to bottom
        c.setFillColorRGB(*fg)
        name, size, t = sh.fit_text(text, 8.5, room, bold=True)
        c.setFont(name, size)
        shift = -(lw + 4) / 2 if lw else 0.0
        c.drawCentredString(shift * mm, -size * 0.35, t)
        if lw:
            k = lw / LOGO_W
            draw_logo(c, TRAY_H / 2 - 4 - lw, -(LOGO_CAP / 2 - 1) * k, lw, bg, logo_ink)
        c.restoreState()


def fmt_len(seconds) -> str:
    if not seconds:
        return ""
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def draw_tracklist(sh: Sheet, x: float, y: float, w: float, h: float, artist: str, album: str, year: str, tracks: list,
                   bg: tuple = (1, 1, 1), columns: Optional[int] = None, reserve: float = 0.0) -> None:
    c = sh.c
    regular, bold = font()
    c.saveState()
    b = sh.opts.bleed
    c.setFillColorRGB(*bg)
    c.rect((x - b) * mm, (y - b) * mm, (w + 2 * b) * mm, (h + 2 * b) * mm, stroke=0, fill=1)
    fg = text_colour(bg)
    c.setFillColorRGB(*fg)
    name, size, t = sh.fit_text(album, 13, w - 16, bold=True)
    c.setFont(name, size)
    c.drawString((x + 8) * mm, (y + h - 13) * mm, t)
    name, size, t = sh.fit_text(artist + (f" · {year}" if year else ""), 9, w - 16)
    c.setFont(name, size)
    c.drawString((x + 8) * mm, (y + h - 19) * mm, t)
    multi = len({tr["disc"] for tr in tracks}) > 1
    rows = len(tracks)
    columns = columns or (1 if rows <= 16 else 2)
    per_col = math.ceil(rows / columns)
    line = min(5.0, (h - 32 - reserve) / max(per_col, 1))
    fsize = max(5.0, min(8.5, line * 2.0))
    col_w = (w - 16) / columns
    for i, tr in enumerate(sorted(tracks, key=lambda t: (t["disc"], t["track"]))):
        col, row = divmod(i, per_col)
        tx, ty = x + 8 + col * col_w, y + h - 28 - row * line
        num = f"{tr['disc']}-{tr['track']:02d}" if multi else f"{tr['track']:2d}"
        dur = fmt_len(tr.get("length"))
        c.setFont(regular, fsize)
        c.drawString(tx * mm, ty * mm, num)
        _, s2, title = sh.fit_text(tr["title"], fsize, col_w - 22)
        c.setFont(regular, s2)
        c.drawString((tx + (9 if multi else 7)) * mm, ty * mm, title)
        c.setFont(regular, fsize)
        c.drawRightString((tx + col_w - 3) * mm, ty * mm, dur)
    total = sum(tr.get("length") or 0 for tr in tracks)
    if total:
        c.setFont(regular, 7)
        c.drawRightString((x + w - 8) * mm, (y + 6) * mm, f"{rows} tracks · {fmt_len(total)}")
    c.restoreState()


def draw_disc(sh: Sheet, img: Optional[Image.Image], cx: float, cy: float, title: str, subtitle: str,
              bg: Optional[tuple] = None, logo: str = "none", ink: str = "auto") -> Optional[int]:
    """A disc label centred on (cx, cy): the image clipped to the ring, plus cut circles."""
    c, o = sh.c, sh.opts
    r_out, r_in = o.disc_outer / 2, o.disc_inner / 2
    dpi = None
    c.saveState()
    p = c.beginPath()
    p.circle(cx * mm, cy * mm, (r_out + o.bleed) * mm)
    p.circle(cx * mm, cy * mm, max(r_in - o.bleed, 1) * mm)
    c.clipPath(p, stroke=0, fill=0, fillMode=0)       # even-odd: keeps the ring, cuts out the hole
    if img is not None:
        d = o.disc_outer + 2 * o.bleed
        fitted, dpi = fit_cover(img, d, d)
        c.drawImage(reader(fitted), (cx - d / 2) * mm, (cy - d / 2) * mm, d * mm, d * mm)
    else:
        c.setFillColorRGB(*(bg or (0.95, 0.95, 0.95)))
        c.rect((cx - r_out - 2) * mm, (cy - r_out - 2) * mm, (2 * r_out + 4) * mm, (2 * r_out + 4) * mm, stroke=0, fill=1)
        c.setFillColorRGB(*(text_colour(bg) if bg else (0.1, 0.1, 0.1)))
        name, size, t = sh.fit_text(title, 12, o.disc_outer * 0.7, bold=True)
        c.setFont(name, size)
        c.drawCentredString(cx * mm, (cy + r_in + 10) * mm, t)
        name, size, t = sh.fit_text(subtitle, 9, o.disc_outer * 0.7)
        c.setFont(name, size)
        c.drawCentredString(cx * mm, (cy + r_in + 4) * mm, t)
        if logo in ("small", "medium", "large"):                # under the hole, inside the ring
            from .designs import LOGO_CAP, LOGO_W, draw_logo
            ring = r_out - r_in
            lw = min({"small": 22.0, "medium": 34.0, "large": 46.0}[logo], (ring - 6) * LOGO_W / LOGO_CAP * 0.9)
            draw_logo(c, cx - lw / 2, cy - r_in - 4 - LOGO_CAP * lw / LOGO_W, lw, bg or (0.95, 0.95, 0.95), ink)
    c.restoreState()
    c.saveState()
    c.setLineWidth(0.25)
    c.setDash(1 * mm, 1 * mm)
    c.circle(cx * mm, cy * mm, r_out * mm, stroke=1, fill=0)
    c.circle(cx * mm, cy * mm, r_in * mm, stroke=1, fill=0)
    c.restoreState()
    return dpi


# ---- the document ------------------------------------------------------------------------------------------------

def render(path: Path, *, artist: str, album: str, year: str = "", tracks: Optional[list] = None,
           front=None, back=None, inside=None, tray=None, disc=None, opts: Optional[PrintOptions] = None,
           styles: Optional[dict] = None) -> PrintReport:
    """Write the print PDF to `path`:
      page 1  OUTSIDE  front cover (front insert) above the tray card (back + both spines)
      page 2  INSIDE   print on the back of page 1: the inside of the front insert behind the front cover, and the
                       under-tray image (seen through the clear tray) behind the tray card's back panel
      page 3  DISC     the disc label(s), for label stock or a printable disc
    Both inserts are centred, so a double-sided print flipped on the long edge lines the inside up with the outside.
    Each part is an Art (an image: a scan of it, or another of the album's images filling the gap), a Colour (a solid
    album colour, with the title or tracklist generated on it) or None (generated on white, as before)."""
    from .designs import DEFAULT_LOGO, draw_design
    opts = opts or PrintOptions()
    tracks = tracks or []
    styles = styles or {}
    report = PrintReport(paper=opts.paper)
    page_size = PAPERS.get(opts.paper, LETTER)
    pw, ph = page_size[0] / mm, page_size[1] / mm
    c = canvas.Canvas(str(path), pagesize=page_size)
    c.setTitle(f"{artist} - {album}: CD jewel case")
    c.setAuthor("flacmn")
    sh = Sheet(c, opts)
    title = f"{artist} — {album}" + (f" ({year})" if year else "")
    front_img = front.image() if isinstance(front, Art) else None
    page = [1]

    def design(part, default):
        """(layout, logo, ink) chosen for a generated part; no mark on the parts outside MARKED_PARTS."""
        logo = styles.get(f"{part}:logo", DEFAULT_LOGO) if part in MARKED_PARTS else "none"
        return styles.get(part, default), logo, styles.get(f"{part}:ink", "auto")

    def said(layout, logo, on):
        what = {"list": "tracklist", "title": "title"}.get(layout, layout)
        return f"generated: {what}{' with the spcmn mark (' + logo + ')' if logo != 'none' else ''} on {on}"

    def piece(name, source, dpi=None):
        warn = f"{dpi} dpi: will print soft" if dpi is not None and dpi < SOFT_DPI else ""
        report.pieces.append(Piece(name, page[0], source, dpi, warn))

    def footer(text):
        sh.label(f"{title}  ·  {text}  ·  print at 100% (actual size)", 12, 6)

    def next_page():
        c.showPage()
        page[0] += 1

    # the two inserts share one position on both sides of the sheet
    fx, fy = (pw - FRONT_W) / 2, ph - 12 - FRONT_H
    tx, ty = (pw - TRAY_W) / 2, fy - 10 - TRAY_H

    # ---- page 1: outside
    if front_img is not None:
        piece("front cover", front.label, sh.image(front_img, fx, fy, FRONT_W, FRONT_H))
    else:
        layout, logo, ink = design("front", "title")
        bg = front.rgb if isinstance(front, Colour) else (1, 1, 1)
        draw_design(sh, fx, fy, FRONT_W, FRONT_H, layout, artist, album, year, [], bg, logo, ink)
        piece("front cover", said(layout, logo, f"the {front.label}" if isinstance(front, Colour) else "white (no front cover found)"))
    sh.marks(fx, fy, FRONT_W, FRONT_H)
    layout, logo, ink = design("back", "list")
    spine_logo = ink if logo != "none" else None                  # a marked back puts the mark on its spines too
    if isinstance(back, Colour):
        draw_design(sh, tx + SPINE, ty, TRAY_W - 2 * SPINE, TRAY_H, layout, artist, album, year, tracks, back.rgb, logo, ink)
        draw_spines(sh, tx, ty, f"{artist}  ·  {album}", back.rgb, spine_logo)
        piece("back + spines", said(layout, logo, f"the {back.label}"))
    elif back is not None:
        img = back.image()
        if img.size[0] / img.size[1] >= (SPINE_RATIO + BACK_RATIO) / 2:       # the scan includes its spines
            piece("back + spines", back.label, sh.image(img, tx, ty, TRAY_W, TRAY_H))
        else:
            dpi = sh.image(img, tx + SPINE, ty, TRAY_W - 2 * SPINE, TRAY_H)
            draw_spines(sh, tx, ty, f"{artist}  ·  {album}", edge_colour(img))
            piece("back + spines", f"{back.label} (spines generated)", dpi)
    else:
        bg = edge_colour(front_img) if front_img is not None else (1, 1, 1)
        draw_design(sh, tx + SPINE, ty, TRAY_W - 2 * SPINE, TRAY_H, layout, artist, album, year, tracks, bg, logo, ink)
        draw_spines(sh, tx, ty, f"{artist}  ·  {album}", bg, spine_logo)
        piece("back + spines", "generated: tracklist from your tags" if (layout, logo) == ("list", "none")
              else said(layout, logo, "the cover's edge colour" if front_img is not None else "white"))
    sh.marks(tx, ty, TRAY_W, TRAY_H)
    sh.fold(tx + SPINE, ty, TRAY_H)
    sh.fold(tx + TRAY_W - SPINE, ty, TRAY_H)
    footer("OUTSIDE (page 1)")
    next_page()

    # ---- page 2: inside, on the back of page 1 (no crop marks: cut from the outside)
    saved_marks, opts.crop_marks = opts.crop_marks, False
    layout, logo, ink = design("inside", "list")
    if isinstance(inside, Colour):
        draw_design(sh, fx, fy, FRONT_W, FRONT_H, layout, artist, album, year, tracks, inside.rgb, logo, ink)
        piece("inside of front insert", said(layout, logo, f"the {inside.label}"))
    elif inside is not None:
        piece("inside of front insert", inside.label, sh.image(split_spread(inside.image())[0], fx, fy, FRONT_W, FRONT_H))
    else:
        draw_design(sh, fx, fy, FRONT_W, FRONT_H, layout, artist, album, year, tracks, (1, 1, 1), logo, ink)
        piece("inside of front insert", "generated: tracklist from your tags" if (layout, logo) == ("list", "none")
              else said(layout, logo, "white"))
    _, logo, ink = design("tray", "blank")
    if isinstance(tray, Colour):
        draw_design(sh, tx + SPINE, ty, TRAY_W - 2 * SPINE, TRAY_H, "blank", artist, album, year, [], tray.rgb, logo, ink)
        piece("under-tray image", tray.label + ("" if logo == "none" else f" with the spcmn mark ({logo})"))
    elif tray is not None:
        piece("under-tray image", tray.label, sh.image(tray.image(), tx + SPINE, ty, TRAY_W - 2 * SPINE, TRAY_H))
    elif logo != "none":
        draw_design(sh, tx + SPINE, ty, TRAY_W - 2 * SPINE, TRAY_H, "blank", artist, album, year, [], (1, 1, 1), logo, ink)
        piece("under-tray image", f"generated: the spcmn mark ({logo}) on white")
    else:
        piece("under-tray image", "none found: left blank")
    opts.crop_marks = saved_marks
    footer("INSIDE (page 2): print on the back of page 1, double-sided, flip on the long edge")
    next_page()

    # ---- page 3: disc label(s)
    disc_bg = disc.rgb if isinstance(disc, Colour) else None
    _, disc_logo, disc_ink = design("disc", "title")
    disc_img = disc.image() if isinstance(disc, Art) else (None if disc_bg else front_img)
    src = (disc.label if isinstance(disc, Art) else f"generated: title on the {disc.label}" if disc_bg
           else "generated: cut from the front cover" if front_img else "generated: title only")
    if disc_img is None and design("disc", "title")[1] != "none":
        src += f" with the spcmn mark ({design('disc', 'title')[1]})"
    count = max(1, opts.discs)
    per_page = 2 if ph - 26 >= 2 * opts.disc_outer + 14 else 1
    for n in range(count):
        k = n % per_page
        cy = ph - 14 - opts.disc_outer / 2 - k * (opts.disc_outer + 14)
        name = f"disc label{f' {n + 1}' if count > 1 else ''}"
        dpi = draw_disc(sh, disc_img, pw / 2, cy, album, artist + (f" · disc {n + 1}" if count > 1 else ""), disc_bg,
                        disc_logo, disc_ink)
        piece(name, src, dpi)
        sh.label(f"{name} · {opts.disc_outer:g} mm, hole {opts.disc_inner:g} mm", pw / 2 - opts.disc_outer / 2,
                 cy + opts.disc_outer / 2 + 2)
        if k == per_page - 1 or n == count - 1:
            footer("DISC (page 3): print on CD label stock or a printable disc")
            next_page()
    c.save()
    report.pages = page[0] - 1
    report.instructions = [
        "Print at 100% / Actual size (turn off 'Fit to page').",
        "Pages 1 and 2 are one sheet: print double-sided, flipping on the long edge, so the inside lands behind the "
        "outside. Then cut on the crop marks of page 1.",
        "Tray card: fold the two spines back on the dashed marks; the printed back faces out of the case and the "
        "under-tray image faces in, behind the clear tray.",
        "Disc label: print page 3 on CD label stock or a printable disc"
        + (f" (hole {opts.disc_inner:g} mm; change it in Settings to match your labels)." if opts.disc_inner else "."),
    ]
    return report


# ---- the printed parts as pictures, for the 3D preview -------------------------------------------------------------

def part_boxes(opts: Optional[PrintOptions] = None) -> dict:
    """Where each part sits in render()'s PDF, as {part: (page index, left mm, top mm, width mm, height mm)} measured
    from the page's top-left corner (the same positions render() draws at)."""
    opts = opts or PrintOptions()
    pw, ph = (v / mm for v in PAPERS.get(opts.paper, LETTER))
    left_f, top_f = (pw - FRONT_W) / 2, 12.0
    left_t, top_t = (pw - TRAY_W) / 2, top_f + FRONT_H + 10
    d = opts.disc_outer
    return {"front": (0, left_f, top_f, FRONT_W, FRONT_H),
            "back": (0, left_t, top_t, TRAY_W, TRAY_H),                  # tray card: left spine, back, right spine
            "inside": (1, left_f, top_f, FRONT_W, FRONT_H),
            "tray": (1, left_t + SPINE, top_t, TRAY_W - 2 * SPINE, TRAY_H),
            "disc": (2, (pw - d) / 2, 14.0, d, d)}


def part_images(pdf: Path, opts: Optional[PrintOptions] = None, px_per_mm: float = 6.0) -> dict:
    """{part: PNG bytes}: each part cut out of the print PDF at its trim size, exactly as it prints."""
    import pypdfium2 as pdfium
    boxes = part_boxes(opts)
    doc = pdfium.PdfDocument(str(pdf))
    try:
        pages = {}
        out = {}
        for part, (page, x, y, w, h) in boxes.items():
            if page not in pages:
                pages[page] = doc[page].render(scale=px_per_mm * 25.4 / 72).to_pil().convert("RGB")
            img = pages[page]
            crop = img.crop(tuple(round(v * px_per_mm) for v in (x, y, x + w, y + h)))
            buf = io.BytesIO()
            crop.save(buf, "PNG")
            out[part] = buf.getvalue()
        return out
    finally:
        doc.close()
