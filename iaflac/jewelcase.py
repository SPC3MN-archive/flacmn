"""Print sheets for a standard CD jewel case, as a PDF at true size (print at 100% / "actual size", not "fit").

    front insert   120 x 120 mm         booklet spread 240 x 120 mm (folds in the middle)
    tray card      151 x 118 mm         = two 6.5 mm spines + 138 mm back, folds on the dashed lines
    disc label     118 mm across, with a centre hole (41 mm for most sticker labels; 23 mm or less for printable discs)

Every piece has crop marks; images run 1.5 mm past the cut line (bleed) so a slightly-off cut leaves no white edge.
A booklet is imposed for double-sided printing (flip on the short edge), so the folded sheets read in page order.
Anything missing is generated: a tracklist tray card, spines with the artist and album, a disc label cut from the
front cover. Each piece reports the resolution its image prints at; under 200 dpi it will look soft.
"""

from __future__ import annotations

import io
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image
from reportlab.lib.pagesizes import A4, LETTER, landscape
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
    name: str                        # "front insert", "tray card", "disc label", "booklet sheet 1 (outside)"
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
    """A booklet scan that shows two pages side by side becomes two pages."""
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


def impose(pages: list) -> list:
    """Saddle-stitch order for a folded booklet. pages: page 1 (front cover) .. page n. Returns sheets as
    [(outside_left, outside_right), (inside_left, inside_right)], padded with None to a multiple of 4."""
    pages = list(pages) + [None] * (-len(pages) % 4)
    n = len(pages)
    sheets = []
    for i in range(n // 4):
        outside = (pages[n - 1 - 2 * i], pages[2 * i])
        inside = (pages[2 * i + 1], pages[n - 2 - 2 * i])
        sheets.append((outside, inside))
    return sheets


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


def draw_spines(sh: Sheet, x: float, y: float, text: str, bg: tuple) -> None:
    c = sh.c
    fg = text_colour(bg)
    for sx, angle in ((x, 90), (x + TRAY_W - SPINE, -90)):
        c.saveState()
        c.setFillColorRGB(*bg)
        b = sh.opts.bleed
        c.rect((sx - (b if sx == x else 0)) * mm, (y - b) * mm, (SPINE + b) * mm, (TRAY_H + 2 * b) * mm, stroke=0, fill=1)
        c.setFillColorRGB(*fg)
        name, size, t = sh.fit_text(text, 8.5, TRAY_H - 8, bold=True)
        c.setFont(name, size)
        if angle == 90:                              # left spine reads bottom to top
            c.translate((sx + SPINE / 2 + size * 0.35 / mm) * mm, (y + TRAY_H / 2) * mm)
        else:                                        # right spine reads top to bottom
            c.translate((sx + SPINE / 2 - size * 0.35 / mm) * mm, (y + TRAY_H / 2) * mm)
        c.rotate(angle)
        c.drawCentredString(0, 0, t)
        c.restoreState()


def fmt_len(seconds) -> str:
    if not seconds:
        return ""
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def draw_tracklist(sh: Sheet, x: float, y: float, w: float, h: float, artist: str, album: str, year: str, tracks: list,
                   bg: tuple = (1, 1, 1)) -> None:
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
    columns = 1 if rows <= 16 else 2
    per_col = math.ceil(rows / columns)
    line = min(5.0, (h - 32) / max(per_col, 1))
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


def draw_disc(sh: Sheet, img: Optional[Image.Image], cx: float, cy: float, title: str, subtitle: str) -> Optional[int]:
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
        c.setFillColorRGB(0.95, 0.95, 0.95)
        c.rect((cx - r_out - 2) * mm, (cy - r_out - 2) * mm, (2 * r_out + 4) * mm, (2 * r_out + 4) * mm, stroke=0, fill=1)
        c.setFillColorRGB(0.1, 0.1, 0.1)
        name, size, t = sh.fit_text(title, 12, o.disc_outer * 0.7, bold=True)
        c.setFont(name, size)
        c.drawCentredString(cx * mm, (cy + r_in + 10) * mm, t)
        name, size, t = sh.fit_text(subtitle, 9, o.disc_outer * 0.7)
        c.setFont(name, size)
        c.drawCentredString(cx * mm, (cy + r_in + 4) * mm, t)
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
           front: Optional[Art] = None, back: Optional[Art] = None, disc: Optional[Art] = None,
           tray: Optional[Art] = None, booklet: Optional[list] = None, opts: Optional[PrintOptions] = None) -> PrintReport:
    """Write the print PDF to `path`. booklet: [Art] of the pages after the front cover, in reading order."""
    opts = opts or PrintOptions()
    tracks = tracks or []
    booklet = booklet or []
    report = PrintReport(paper=opts.paper)
    page_size = PAPERS.get(opts.paper, LETTER)
    pw, ph = page_size[0] / mm, page_size[1] / mm
    c = canvas.Canvas(str(path), pagesize=page_size)
    c.setTitle(f"{artist} - {album}: CD jewel case")
    c.setAuthor("iaflac")
    sh = Sheet(c, opts)
    page = 1
    title = f"{artist} — {album}" + (f" ({year})" if year else "")
    front_img = front.image() if front else None

    def piece(name, source, dpi=None):
        warn = f"{dpi} dpi: will print soft" if dpi is not None and dpi < SOFT_DPI else ""
        report.pieces.append(Piece(name, page, source, dpi, warn))

    def footer(text):
        sh.label(f"{title}  ·  {text}  ·  print at 100% (actual size)", 12, 8)

    def tray_card(x, y):
        if back is not None:
            img = back.image()
            ratio = img.size[0] / img.size[1]
            if ratio >= (SPINE_RATIO + BACK_RATIO) / 2:          # the scan includes its spines
                dpi = sh.image(img, x, y, TRAY_W, TRAY_H)
                piece("tray card", back.label, dpi)
            else:
                dpi = sh.image(img, x + SPINE, y, TRAY_W - 2 * SPINE, TRAY_H)
                draw_spines(sh, x, y, f"{artist}  ·  {album}", edge_colour(img))
                piece("tray card", f"{back.label} (spines generated)", dpi)
        else:
            bg = edge_colour(front_img) if front_img is not None else (1, 1, 1)
            draw_tracklist(sh, x + SPINE, y, TRAY_W - 2 * SPINE, TRAY_H, artist, album, year, tracks, bg)
            draw_spines(sh, x, y, f"{artist}  ·  {album}", bg)
            piece("tray card", "generated: tracklist from your tags")
        sh.marks(x, y, TRAY_W, TRAY_H)
        sh.fold(x + SPINE, y, TRAY_H)
        sh.fold(x + TRAY_W - SPINE, y, TRAY_H)
        sh.label("tray card · fold on the dashed marks", x, y + TRAY_H + MARK_GAP + opts.bleed + MARK_LEN + 1.5)

    def front_insert(x, y):
        if front_img is not None:
            piece("front insert", front.label, sh.image(front_img, x, y, FRONT_W, FRONT_H))
        else:
            draw_tracklist(sh, x, y, FRONT_W, FRONT_H, artist, album, year, [], (1, 1, 1))
            piece("front insert", "generated: title only (no front cover found)")
        sh.marks(x, y, FRONT_W, FRONT_H)
        sh.label("front insert", x, y + FRONT_H + MARK_GAP + opts.bleed + MARK_LEN + 1.5)

    disc_img = disc.image() if disc else None

    def disc_labels(top_y, count, first=0):
        img = disc_img if disc_img is not None else front_img
        src = disc.label if disc else ("generated: cut from the front cover" if front_img else "generated: title only")
        several = opts.discs > 1
        for k in range(count):
            n = first + k
            cy = top_y - opts.disc_outer / 2 - k * (opts.disc_outer + 14)
            dpi = draw_disc(sh, img, pw / 2, cy, album, artist + (f" · disc {n + 1}" if several else ""))
            name = f"disc label{f' {n + 1}' if several else ''}"
            piece(name, src, dpi)
            sh.label(f"{name} · {opts.disc_outer:g} mm, hole {opts.disc_inner:g} mm",
                     pw / 2 - opts.disc_outer / 2, cy + opts.disc_outer / 2 + 2)

    # Each portrait page holds one large piece at the top and, below it, a disc label (or nothing).
    top = ph - 14
    discs_left = list(range(max(1, opts.discs)))

    def disc_below(piece_y: float) -> str:
        if not discs_left:
            return ""
        disc_labels(piece_y - 14, 1, discs_left.pop(0))
        return " + disc label"

    y = top - TRAY_H
    tray_card((pw - TRAY_W) / 2, y)
    footer("tray card" + disc_below(y))
    c.showPage()
    page += 1
    if not booklet:
        y = top - FRONT_H
        front_insert((pw - FRONT_W) / 2, y)
        footer("front insert" + disc_below(y))
        c.showPage()
        page += 1
    while discs_left:
        disc_labels(top - 8, 1, discs_left.pop(0))
        footer("disc label")
        c.showPage()
        page += 1
    if booklet:
        pages = [front_img]
        page_src = [front.label if front else "blank"]
        for art in booklet:
            for part in split_spread(art.image()):
                pages.append(part)
                page_src.append(art.label)
        sheets = impose(list(range(len(pages))))
        lw, lh = landscape(page_size)[0] / mm, landscape(page_size)[1] / mm
        for s, (outside, inside) in enumerate(sheets, 1):
            for side, pair in (("outside", outside), ("inside", inside)):
                c.setPageSize(landscape(page_size))
                x0, y0 = (lw - 2 * FRONT_W) / 2, (lh - FRONT_H) / 2
                dpis = []
                for k, idx in enumerate(pair):
                    if idx is not None and pages[idx] is not None:
                        dpis.append(sh.image(pages[idx], x0 + k * FRONT_W, y0, FRONT_W, FRONT_H))
                sh.marks(x0, y0, 2 * FRONT_W, FRONT_H)
                sh.fold(x0 + FRONT_W, y0, FRONT_H)
                nums = " | ".join(str(i + 1) if i is not None and i < len(pages) else "blank" for i in pair)
                sh.label(f"booklet sheet {s} ({side}) · pages {nums} · fold on the dashed marks", x0,
                         y0 + FRONT_H + MARK_GAP + opts.bleed + MARK_LEN + 1.5)
                srcs = {page_src[i] for i in pair if i is not None and i < len(page_src)}
                piece(f"booklet sheet {s} ({side})", "; ".join(sorted(srcs)) or "blank", min(dpis) if dpis else None)
                sh.label(f"{title}  ·  booklet sheet {s} {side}  ·  print double-sided, flip on the short edge, "
                         f"at 100% (actual size)", 12, 8)
                c.showPage()
                page += 1
        report.instructions.append(f"Booklet: {len(sheets)} sheet(s). Print those pages double-sided, flipping on the "
                                   "short edge, then fold each sheet and nest them in order.")
    if tray is not None:
        c.setPageSize(page_size)
        x, y = (pw - (TRAY_W - 2 * SPINE)) / 2, top - TRAY_H
        dpi = sh.image(tray.image(), x, y, TRAY_W - 2 * SPINE, TRAY_H)
        sh.marks(x, y, TRAY_W - 2 * SPINE, TRAY_H)
        sh.label("under-tray card (seen through the clear tray) · glue to the back of the tray card or cut separately",
                 x, y + TRAY_H + MARK_GAP + opts.bleed + MARK_LEN + 1.5)
        piece("under-tray card", tray.label, dpi)
        footer("inside of the tray")
        c.showPage()
        page += 1
    c.save()
    report.pages = page - 1
    report.instructions.insert(0, "Print at 100% / Actual size (turn off 'Fit to page'), then cut on the crop marks.")
    report.instructions.append("Tray card: fold the two spines back on the dashed marks before sliding it under the tray.")
    return report
