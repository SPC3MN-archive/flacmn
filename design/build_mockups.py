#!/usr/bin/env python3
"""
iaflac UI mockups -- generator for Inkscape-friendly SVG screens.

    python design/build_mockups.py

Writes one SVG per screen into design/ plus the combined multi-page file
design/iaflac-mockups.svg.  Standard library only.

Inkscape notes
  * every screen is split into layers  <g inkscape:groupmode="layer">
  * every component is a named group   <g inkscape:label="Button · Search">
  * all text is real <text>/<tspan>, colours and fonts are presentation
    attributes (no CSS classes), no filters / masks / external images.
"""
import math
import os
import random
import re
from contextlib import contextmanager
from xml.sax.saxutils import escape, quoteattr

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1440, 900
FONT = "Inter, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif"

# --------------------------------------------------------------------------
# Palette (the five chosen colours + black / white)
# --------------------------------------------------------------------------
LAVENDER = "#DFD9E2"   # app background, secondary surfaces, borders
AMBER = "#F4B73E"      # warnings, hi-res highlight, in-progress
RED = "#BC3843"        # lossy, failed, errors, destructive
INDIGO = "#5C53A2"     # primary: navigation, buttons, selection, focus
GREEN = "#20A76B"      # lossless, PASS, done, good
WHITE = "#FFFFFF"
BLACK = "#000000"
INK = "#13102A"        # very dark indigo used for shades


def _rgb(c):
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def mix(a, b, t):
    """Mix colour a towards b by t (0..1)."""
    ra, rb = _rgb(a), _rgb(b)
    return "#" + "".join("%02X" % round(x + (y - x) * t) for x, y in zip(ra, rb))


# Role colours ------------------------------------------------------------
APP_BG = LAVENDER
SURFACE = WHITE
SURFACE_ALT = mix(WHITE, LAVENDER, 0.25)      # quiet inner panels
SURFACE_SUNK = mix(WHITE, LAVENDER, 0.55)     # wells, neutral chips
BORDER = mix(LAVENDER, INDIGO, 0.12)
BORDER_SOFT = mix(WHITE, LAVENDER, 0.7)
TEXT = "#0F0D1A"                              # near-black body text
TEXT_2 = mix(TEXT, INDIGO, 0.38)              # secondary text
TEXT_3 = mix(TEXT, LAVENDER, 0.52)            # muted / captions
PRIMARY = INDIGO
PRIMARY_DARK = mix(INDIGO, INK, 0.3)
PRIMARY_TINT = mix(WHITE, INDIGO, 0.09)
PRIMARY_TINT_2 = mix(WHITE, INDIGO, 0.22)
PRIMARY_TEXT = mix(INDIGO, INK, 0.25)
SIDEBAR = mix(INDIGO, INK, 0.76)
SIDEBAR_LINE = mix(INDIGO, INK, 0.58)
SIDEBAR_TEXT = mix(LAVENDER, WHITE, 0.4)
SIDEBAR_MUTED = mix(LAVENDER, INDIGO, 0.5)
TITLEBAR = mix(INDIGO, INK, 0.9)
TITLE_TEXT = mix(LAVENDER, INDIGO, 0.35)
GOOD = GREEN
GOOD_TINT = mix(WHITE, GREEN, 0.13)
GOOD_TEXT = mix(GREEN, INK, 0.45)
WARN = AMBER
WARN_TINT = mix(WHITE, AMBER, 0.2)
WARN_LINE = mix(WHITE, AMBER, 0.6)
WARN_TEXT = mix(AMBER, INK, 0.6)
BAD = RED
BAD_TINT = mix(WHITE, RED, 0.1)
BAD_LINE = mix(WHITE, RED, 0.45)
BAD_TEXT = mix(RED, INK, 0.25)
SCRIM = INK
WAVE_REST = mix(WHITE, INDIGO, 0.3)
SPEC_STOPS = [(0.0, mix(INK, BLACK, 0.3)), (0.32, mix(INDIGO, INK, 0.55)),
              (0.52, INDIGO), (0.72, RED), (0.88, AMBER), (1.0, mix(AMBER, WHITE, 0.6))]


def ramp(t):
    t = max(0.0, min(1.0, t))
    for (t0, c0), (t1, c1) in zip(SPEC_STOPS, SPEC_STOPS[1:]):
        if t <= t1:
            return mix(c0, c1, (t - t0) / (t1 - t0) if t1 > t0 else 0)
    return SPEC_STOPS[-1][1]


SPEC_LEVELS = [ramp(i / 8) for i in range(9)]

# --------------------------------------------------------------------------
# Low-level helpers
# --------------------------------------------------------------------------


def fmt(v):
    if isinstance(v, float):
        s = ("%.2f" % v).rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    return str(v)


def attrs(**kw):
    out = []
    for k, v in kw.items():
        if v is None:
            continue
        k = k.rstrip("_").replace("__", ":").replace("_", "-")
        out.append("%s=%s" % (k, quoteattr(fmt(v))))
    return " ".join(out)


def slug(s):
    s = s.lower().replace("·", " ").replace("×", "x")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:48] or "item"


_NARROW = set("il.,:;|!'’`ıj")
_SEMI = set("frt()[]/-I ")
_WIDE = set("mwMW@")


def tw(s, size, weight=400):
    """Approximate rendered text width (Segoe UI / Inter metrics)."""
    w = 0.0
    for c in s:
        if c in _NARROW:
            w += 0.26
        elif c in _SEMI:
            w += 0.34
        elif c in _WIDE:
            w += 0.84
        elif c in "·":
            w += 0.28
        elif c in "→…":
            w += 0.9
        elif c.isdigit():
            w += 0.56
        elif c.isupper():
            w += 0.64
        else:
            w += 0.53
    return w * size * (1.12 if weight >= 600 else 1.07)


def fit(s, size, width, weight=400):
    if tw(s, size, weight) <= width:
        return s
    while s and tw(s + "…", size, weight) > width:
        s = s[:-1]
    return s.rstrip() + "…"


# --------------------------------------------------------------------------
# Document builder
# --------------------------------------------------------------------------


class Doc:
    def __init__(self, key, title):
        self.key, self.title = key, title
        self.parts, self.defs, self._ids = [], [], {}

    def uid(self, label):
        base = "%s-%s" % (self.key.lower(), slug(label))
        n = self._ids.get(base, 0)
        self._ids[base] = n + 1
        return base if n == 0 else "%s-%d" % (base, n + 1)

    def raw(self, s):
        self.parts.append(s)

    @contextmanager
    def layer(self, label):
        self.raw('<g %s>' % attrs(inkscape__groupmode="layer", id=self.uid("layer " + label),
                                  inkscape__label=label))
        yield
        self.raw("</g>")

    @contextmanager
    def group(self, label, transform=None):
        self.raw('<g %s>' % attrs(id=self.uid(label), inkscape__label=label, transform=transform))
        yield
        self.raw("</g>")

    # primitives -----------------------------------------------------------
    def rect(self, x, y, w, h, fill=None, stroke=None, sw=1, rx=0, opacity=None, dash=None, label=None):
        self.raw("<rect %s/>" % attrs(
            id=self.uid(label) if label else None, inkscape__label=label,
            x=float(x), y=float(y), width=float(max(w, 0)), height=float(max(h, 0)),
            rx=float(rx) if rx else None, fill=fill or "none", fill_opacity=opacity,
            stroke=stroke, stroke_width=float(sw) if stroke else None, stroke_dasharray=dash))

    def box(self, x, y, w, h, fill=SURFACE, stroke=BORDER, rx=12, sw=1, dash=None, label=None):
        """Rect with a crisp 1px border (half-pixel inset)."""
        if stroke:
            self.rect(x + .5, y + .5, w - 1, h - 1, fill=fill, stroke=stroke, sw=sw, rx=rx, dash=dash, label=label)
        else:
            self.rect(x, y, w, h, fill=fill, rx=rx, label=label)

    def circle(self, cx, cy, r, fill=None, stroke=None, sw=1, opacity=None):
        self.raw("<circle %s/>" % attrs(cx=float(cx), cy=float(cy), r=float(r), fill=fill or "none",
                                        fill_opacity=opacity, stroke=stroke,
                                        stroke_width=float(sw) if stroke else None))

    def line(self, x1, y1, x2, y2, stroke=BORDER, sw=1, dash=None, cap=None):
        self.raw("<line %s/>" % attrs(x1=float(x1), y1=float(y1), x2=float(x2), y2=float(y2),
                                      stroke=stroke, stroke_width=float(sw), stroke_dasharray=dash,
                                      stroke_linecap=cap))

    def hline(self, x1, x2, y, stroke=BORDER_SOFT):
        self.line(x1, y + .5, x2, y + .5, stroke=stroke)

    def path(self, d, fill=None, stroke=None, sw=1, cap="round", join="round", opacity=None, label=None):
        self.raw("<path %s/>" % attrs(id=self.uid(label) if label else None, inkscape__label=label,
                                      d=d, fill=fill or "none", fill_opacity=opacity, stroke=stroke,
                                      stroke_width=float(sw) if stroke else None,
                                      stroke_linecap=cap if stroke else None,
                                      stroke_linejoin=join if stroke else None))

    def text(self, x, y, s, size=13, weight=400, fill=None, anchor=None, ls=None, italic=False, deco=None):
        self.raw("<text %s>%s</text>" % (attrs(
            x=float(x), y=float(y), font_family=FONT, font_size=size, font_weight=weight,
            fill=fill or TEXT, text_anchor=anchor, letter_spacing=ls,
            font_style="italic" if italic else None, text_decoration=deco,
            xml__space="preserve"), escape(s)))

    def spans(self, x, y, runs, anchor=None):
        """runs: list of (text, size, weight, fill)."""
        inner = "".join("<tspan %s>%s</tspan>" % (attrs(font_size=sz, font_weight=wt, fill=fl), escape(t))
                        for t, sz, wt, fl in runs)
        self.raw("<text %s>%s</text>" % (attrs(x=float(x), y=float(y), font_family=FONT,
                                               text_anchor=anchor, xml__space="preserve"), inner))

    def body(self):
        return "\n".join(self.parts)


# --------------------------------------------------------------------------
# Icons (16-unit line icons, scaled into place)
# --------------------------------------------------------------------------
ICONS = {
    "search": "M11.5 7 A4.5 4.5 0 1 1 2.5 7 A4.5 4.5 0 1 1 11.5 7 Z M10.4 10.4 L14 14",
    "disc": "M14 8 A6 6 0 1 1 2 8 A6 6 0 1 1 14 8 Z M10 8 A2 2 0 1 1 6 8 A2 2 0 1 1 10 8 Z",
    "library": "M3 2.5 V13.5 M6.5 2.5 V13.5 M9.5 3.2 L13 12.8",
    "settings": "M2 4.5 H4 M7.2 4.5 H14 M2 11.5 H8.8 M12 11.5 H14 M7.2 4.5 A1.6 1.6 0 1 1 4 4.5 A1.6 1.6 0 1 1 7.2 4.5 Z M12 11.5 A1.6 1.6 0 1 1 8.8 11.5 A1.6 1.6 0 1 1 12 11.5 Z",
    "folder": "M2 4.5 A1 1 0 0 1 3 3.5 H6.5 L8 5 H13 A1 1 0 0 1 14 6 V12 A1 1 0 0 1 13 13 H3 A1 1 0 0 1 2 12 Z",
    "download": "M8 2.5 V10.5 M4.8 7.5 L8 10.7 L11.2 7.5 M3 13.5 H13",
    "check": "M3.5 8.5 L6.5 11.5 L12.5 4.5",
    "x": "M4.5 4.5 L11.5 11.5 M11.5 4.5 L4.5 11.5",
    "alert": "M8 2.5 L14.2 13.2 H1.8 Z M8 6.5 V9.3 M8 11.3 V11.4",
    "info": "M14 8 A6 6 0 1 1 2 8 A6 6 0 1 1 14 8 Z M8 7.5 V11 M8 5 V5.1",
    "chev_down": "M4.5 6.5 L8 10 L11.5 6.5",
    "chev_up": "M4.5 9.5 L8 6 L11.5 9.5",
    "chev_right": "M6.5 4.5 L10 8 L6.5 11.5",
    "chev_left": "M9.5 4.5 L6 8 L9.5 11.5",
    "external": "M9 2.5 H13.5 V7 M13.5 2.5 L7.5 8.5 M11.5 9.5 V13.5 H2.5 V4.5 H6.5",
    "refresh": "M13 8 A5 5 0 1 1 11.3 4.2 M13 2.5 V5.2 H10.3",
    "printer": "M4.5 6 V2.5 H11.5 V6 M4.5 11.5 H2.5 V6 H13.5 V11.5 H11.5 M4.5 9.5 H11.5 V13.5 H4.5 Z",
    "image": "M2.5 3.5 H13.5 V12.5 H2.5 Z M2.5 10.5 L6 7 L9 10 L11 8 L13.5 10.5",
    "music": "M6 12 V3.5 L13 2.5 V10.5 M6 12 A2 2 0 1 1 2 12 A2 2 0 1 1 6 12 Z M13 10.5 A2 2 0 1 1 9 10.5 A2 2 0 1 1 13 10.5 Z",
    "grid": "M2.5 2.5 H7 V7 H2.5 Z M9 2.5 H13.5 V7 H9 Z M2.5 9 H7 V13.5 H2.5 Z M9 9 H13.5 V13.5 H9 Z",
    "list": "M5.5 4 H13.5 M5.5 8 H13.5 M5.5 12 H13.5 M2.5 4 H3 M2.5 8 H3 M2.5 12 H3",
    "queue": "M8 2.5 L14 5.5 L8 8.5 L2 5.5 Z M2 8.5 L8 11.5 L14 8.5 M2 11.2 L8 14.2 L14 11.2",
    "tag": "M2.5 2.5 H8 L13.5 8 L8 13.5 L2.5 8 Z M5.5 5.5 V5.6",
    "wave": "M1.5 8 H3.5 L5 4 L7 12 L9 3 L11 13 L12.5 8 H14.5",
    "plus": "M8 3.5 V12.5 M3.5 8 H12.5",
    "minus": "M3.5 8 H12.5",
    "trash": "M3 4.5 H13 M6.5 4.5 V2.5 H9.5 V4.5 M4.5 4.5 L5 13.5 H11 L11.5 4.5",
    "filter": "M2 3 H14 L9.5 8.5 V13 L6.5 11.5 V8.5 Z",
    "square": "M3.5 3.5 H12.5 V12.5 H3.5 Z",
    "spectrum": "M2.5 13.5 V9 M5.5 13.5 V5 M8.5 13.5 V3 M11.5 13.5 V7 M14 13.5 V10.5",
    "sparkle": "M8 2 L9.4 6.6 L14 8 L9.4 9.4 L8 14 L6.6 9.4 L2 8 L6.6 6.6 Z",
    "clock": "M14 8 A6 6 0 1 1 2 8 A6 6 0 1 1 14 8 Z M8 4.8 V8 L10.3 9.6",
    "file": "M4 2.5 H9.5 L12.5 5.5 V13.5 H4 Z M9.5 2.5 V5.5 H12.5",
    "keyboard": "M2 4.5 H14 V11.5 H2 Z M4.5 9 H11.5",
}
FILLED = {
    "play": "M5 3 L13 8 L5 13 Z",
    "pause": "M4.5 3 H7 V13 H4.5 Z M9 3 H11.5 V13 H9 Z",
    "dot": "M11 8 A3 3 0 1 1 5 8 A3 3 0 1 1 11 8 Z",
}
_TOK = re.compile(r"[MLHVCSQTAZ]|-?\d*\.?\d+")


def _scale_path(dstr, x, y, s):
    k = s / 16.0
    out, cmd, idx = [], None, 0
    for t in _TOK.findall(dstr):
        if t.isalpha():
            cmd, idx = t, 0
            out.append(t)
            continue
        v = float(t)
        if cmd == "H":
            v = x + v * k
        elif cmd == "V":
            v = y + v * k
        elif cmd == "A":
            p = idx % 7
            if p in (0, 1):
                v = v * k
            elif p == 5:
                v = x + v * k
            elif p == 6:
                v = y + v * k
        else:
            v = x + v * k if idx % 2 == 0 else y + v * k
        out.append(fmt(round(v, 2)) if cmd != "A" or idx % 7 not in (2, 3, 4) else str(int(v)))
        idx += 1
    return " ".join(out)


def icon(d, name, x, y, size=16, color=TEXT_2, sw=1.5):
    if name in FILLED:
        d.path(_scale_path(FILLED[name], x, y, size), fill=color)
    else:
        d.path(_scale_path(ICONS[name], x, y, size), stroke=color, sw=sw)


# --------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------


def chip_w(text, size=11, weight=600, pad=8, icon_=None, dot=False):
    return tw(text, size, weight) + 2 * pad + (size + 4 if icon_ else 0) + (10 if dot else 0)


def chip(d, x, y, text, fill, color, stroke=None, h=20, size=11, weight=600, icon_=None, dot=None,
         pad=8, rx=None, anchor="start", label=None):
    w = chip_w(text, size, weight, pad, icon_, bool(dot))
    if anchor == "end":
        x -= w
    with d.group(label or "Chip · %s" % text):
        d.box(x, y, w, h, fill=fill, stroke=stroke, rx=h / 2 if rx is None else rx)
        cx = x + pad
        if dot:
            d.circle(cx + 3, y + h / 2, 3, fill=dot)
            cx += 10
        if icon_:
            icon(d, icon_, cx - 1, y + (h - size - 2) / 2, size + 2, color, 1.6)
            cx += size + 4
        d.text(cx, y + h / 2 + size * 0.36, text, size, weight, color)
    return w


QUALITY = {
    "cd": ("Lossless · CD rip", GOOD, WHITE, None, None, "disc"),
    "hires": ("Lossless · hi-res", GOOD_TINT, GOOD_TEXT, None, AMBER, None),
    "lossless": ("Lossless", GOOD_TINT, GOOD_TEXT, None, GOOD, None),
    "lossy_hi": ("Lossy · high bitrate", WHITE, BAD_TEXT, BAD_LINE, RED, None),
    "lossy": ("Lossy", BAD_TINT, BAD_TEXT, None, RED, None),
    "failed": ("Failed", BAD, WHITE, None, None, "x"),
}


def quality_badge(d, x, y, cls, h=20, size=11, anchor="start"):
    text, fill, color, stroke, dot, ic = QUALITY[cls]
    return chip(d, x, y, text, fill, color, stroke=stroke, h=h, size=size, dot=dot, icon_=ic,
                anchor=anchor, rx=6, label="Quality badge · %s" % text)


TIERS = {"A": (GOOD, WHITE, None), "B": (GOOD_TINT, GOOD_TEXT, mix(WHITE, GREEN, 0.4)),
         "C": (WARN_TINT, WARN_TEXT, WARN_LINE), "L": (BAD_TINT, BAD_TEXT, BAD_LINE),
         "D": (BAD, WHITE, None), "?": (SURFACE_SUNK, TEXT_2, BORDER)}


def tier_chip(d, x, y, letter, s=20):
    fill, color, stroke = TIERS[letter]
    with d.group("Tier chip · %s" % letter):
        d.box(x, y, s, s, fill=fill, stroke=stroke, rx=5)
        d.text(x + s / 2, y + s / 2 + s * 0.2, letter, round(s * 0.55), 700, color, anchor="middle")
    return s


VERDICTS = {"PASS": (GOOD, WHITE, None, "check"), "SUSPECT": (AMBER, TEXT, None, "alert"),
            "LIKELY LOSSY": (BAD, WHITE, None, "x"), "UPSAMPLED": (BAD_TINT, BAD_TEXT, BAD_LINE, "alert")}


def verdict_chip(d, x, y, verdict, khz=None, h=24, size=11.5, anchor="start"):
    fill, color, stroke, ic = VERDICTS[verdict]
    t = verdict + (" · %s kHz" % khz if khz else "")
    return chip(d, x, y, t, fill, color, stroke=stroke, h=h, size=size, weight=700, icon_=ic, rx=6,
                anchor=anchor, label="Verdict chip · %s" % t)


def level_chip(d, x, y, lvl, anchor="start", h=18):
    return chip(d, x, y, lvl, PRIMARY_TINT, PRIMARY_TEXT, stroke=PRIMARY_TINT_2, h=h, size=10, weight=700,
                pad=6, rx=5, anchor=anchor, label="Tested level · %s" % lvl)


SOURCES = {"file": (SURFACE_SUNK, TEXT_2, None), "ia": (WHITE, TEXT_2, BORDER),
           "MB": (PRIMARY_TINT_2, PRIMARY_TEXT, None), "you": (PRIMARY, WHITE, None)}


def source_badge(d, x, y, src, anchor="start"):
    fill, color, stroke = SOURCES[src]
    return chip(d, x, y, src, fill, color, stroke=stroke, h=16, size=9.5, weight=700, pad=5, rx=4,
                anchor=anchor, label="Source badge · %s" % src)


def mini_chip(d, x, y, text, anchor="start"):
    return chip(d, x, y, text, SURFACE_SUNK, TEXT_2, h=16, size=9.5, weight=600, pad=5, rx=4,
                anchor=anchor, label="Rip file · %s" % text)


def button(d, x, y, label, kind="primary", w=None, h=32, size=13, icon_=None, icon_right=None, anchor="start"):
    styles = {
        "primary": (PRIMARY, WHITE, None),
        "secondary": (WHITE, TEXT, BORDER),
        "destructive": (BAD, WHITE, None),
        "danger_outline": (WHITE, BAD_TEXT, BAD_LINE),
        "ghost": (None, PRIMARY, None),
        "success": (GOOD_TINT, GOOD_TEXT, None),
        "dark": (SIDEBAR_LINE, WHITE, None),
    }
    fill, color, stroke = styles[kind]
    tw_ = tw(label, size, 600)
    iw = (size + 6) if icon_ else 0
    irw = (size + 4) if icon_right else 0
    bw = w or (tw_ + iw + irw + (24 if kind != "ghost" else 12))
    if anchor == "end":
        x -= bw
    with d.group("Button · %s" % label):
        if fill or stroke:
            d.box(x, y, bw, h, fill=fill, stroke=stroke, rx=8)
        cx = x + (bw - tw_ - iw - irw) / 2
        if icon_:
            icon(d, icon_, cx, y + (h - size - 2) / 2, size + 2, color, 1.6)
            cx += iw
        d.text(cx, y + h / 2 + size * 0.36, label, size, 600, color)
        if icon_right:
            icon(d, icon_right, cx + tw_ + 4, y + (h - size) / 2, size, color, 1.6)
    return bw


def icon_button(d, x, y, name, s=28, color=TEXT_2, fill=None, stroke=BORDER, label=None, isz=None):
    with d.group(label or "Icon button · %s" % name):
        d.box(x, y, s, s, fill=fill or WHITE, stroke=stroke, rx=8)
        isz = isz or s * 0.55
        icon(d, name, x + (s - isz) / 2, y + (s - isz) / 2, isz, color, 1.6)
    return s


def play_button(d, x, y, s=32, playing=False):
    with d.group("Button · Play/pause"):
        d.circle(x + s / 2, y + s / 2, s / 2, fill=PRIMARY)
        isz = s * 0.44
        icon(d, "pause" if playing else "play", x + (s - isz) / 2 + (0 if playing else 1), y + (s - isz) / 2, isz, WHITE)
    return s


def field(d, x, y, w, value, h=34, prefix=None, icon_=None, focused=False, placeholder=False, chevron=False,
          size=13, src=None, label=None, weight=400):
    with d.group(label or "Field · %s" % (prefix or value)):
        if focused:
            d.box(x - 3, y - 3, w + 6, h + 6, fill=None, stroke=PRIMARY_TINT_2, rx=11, sw=3)
        d.box(x, y, w, h, fill=WHITE, stroke=PRIMARY if focused else BORDER, rx=8)
        cx = x + 10
        if icon_:
            icon(d, icon_, cx, y + (h - 15) / 2, 15, TEXT_3)
            cx += 22
        if prefix:
            d.text(cx, y + h / 2 + size * 0.36, prefix, size - 1, 500, TEXT_3)
            cx += tw(prefix, size - 1, 500) + 8
        right = x + w - 10
        if chevron:
            icon(d, "chev_down", right - 14, y + (h - 14) / 2, 14, TEXT_2)
            right -= 20
        if src:
            sw_ = chip_w(src, 9.5, 700, 5)
            source_badge(d, right, y + (h - 16) / 2, src, anchor="end")
            right -= sw_ + 6
        d.text(cx, y + h / 2 + size * 0.36, fit(value, size, right - cx, weight), size, weight,
               TEXT_3 if placeholder else TEXT)
        if focused:
            cxe = cx + tw(value, size) * 0.94 + 2
            d.line(cxe, y + 9, cxe, y + h - 9, stroke=PRIMARY, sw=1.5)


def toggle(d, x, y, on, label=None, size=13, color=TEXT):
    with d.group("Toggle · %s" % (label or "")):
        d.rect(x, y, 32, 18, fill=PRIMARY if on else mix(LAVENDER, INDIGO, 0.25), rx=9)
        d.circle(x + (23 if on else 9), y + 9, 7, fill=WHITE)
        if label:
            d.text(x + 40, y + 9 + size * 0.36, label, size, 400, color)
    return 40 + (tw(label, size) if label else 0)


def checkbox(d, x, y, checked, label, size=12, color=TEXT):
    with d.group("Checkbox · %s" % label):
        if checked:
            d.rect(x, y, 16, 16, fill=PRIMARY, rx=4)
            icon(d, "check", x + 1, y + 1, 14, WHITE, 2)
        else:
            d.box(x, y, 16, 16, fill=WHITE, stroke=mix(LAVENDER, INDIGO, 0.35), rx=4)
        d.text(x + 22, y + 8 + size * 0.36, label, size, 400, color)
    return 22 + tw(label, size)


def filter_chip(d, x, y, label, on, h=26):
    w = chip_w(label, 12, 600, 10, "check" if on else None)
    if on:
        chip(d, x, y, label, PRIMARY_TINT, PRIMARY_TEXT, stroke=PRIMARY_TINT_2, h=h, size=12, pad=10,
             icon_="check", label="Filter · %s (on)" % label)
    else:
        chip(d, x, y, label, WHITE, TEXT_2, stroke=BORDER, h=h, size=12, pad=10, label="Filter · %s (off)" % label)
    return w


def segmented(d, x, y, options, active, h=30, size=12, label="Segmented", widths=None):
    ws = widths or [tw(o, size, 600) + 24 for o in options]
    total = sum(ws) + 6
    with d.group("%s · %s" % (label, active)):
        d.box(x, y, total, h, fill=SURFACE_SUNK, stroke=None, rx=9)
        cx = x + 3
        for o, w in zip(options, ws):
            if o == active:
                d.box(cx, y + 3, w, h - 6, fill=WHITE, stroke=BORDER, rx=7)
            d.text(cx + w / 2, y + h / 2 + size * 0.36, o, size, 600 if o == active else 500,
                   TEXT if o == active else TEXT_2, anchor="middle")
            cx += w
    return total


def tabs(d, x, y, options, active, width, size=13, gap=28, counts=None):
    with d.group("Tabs · %s" % active):
        d.hline(x, x + width, y + 32, BORDER)
        cx = x
        for o in options:
            w = tw(o, size, 600)
            on = o == active
            d.text(cx, y + 20, o, size, 600, PRIMARY if on else TEXT_2)
            if counts and o in counts:
                cw = chip(d, cx + w + 6, y + 8, counts[o], PRIMARY_TINT if on else SURFACE_SUNK,
                          PRIMARY_TEXT if on else TEXT_2, h=16, size=10, pad=6)
                w += cw + 6
            if on:
                d.rect(cx, y + 30, w, 3, fill=PRIMARY, rx=1.5)
            cx += w + gap


def progress(d, x, y, w, frac, color=PRIMARY, h=6, track=None, label="Progress"):
    with d.group("%s · %d%%" % (label, round(frac * 100))):
        d.rect(x, y, w, h, fill=track or SURFACE_SUNK, rx=h / 2)
        if frac > 0:
            d.rect(x, y, max(h, w * frac), h, fill=color, rx=h / 2)


def section_label(d, x, y, text, color=None):
    d.text(x, y, text.upper(), 10.5, 700, color or TEXT_3, ls=0.7)


def card(d, x, y, w, h, title=None, sub=None, label=None, fill=SURFACE, stroke=BORDER, rx=12, tsize=15):
    d.box(x, y, w, h, fill=fill, stroke=stroke, rx=rx, label=label)
    if title:
        d.text(x + 16, y + 28, title, tsize, 650 if tsize < 18 else 700, TEXT)
        if sub:
            d.text(x + 16 + tw(title, tsize, 650) + 10, y + 28, sub, 12, 400, TEXT_3)


def kbd(d, x, y, text):
    w = tw(text, 10.5, 600) + 12
    d.box(x, y, w, 20, fill=SURFACE_ALT, stroke=BORDER, rx=5)
    d.text(x + w / 2, y + 14, text, 10.5, 600, TEXT_2, anchor="middle")
    return w


def art_dots(d, x, y, have, r=4, gap=11, label="Art completeness"):
    """five dots: front, back, inside, under-tray, disc."""
    with d.group(label):
        for i, h_ in enumerate(have):
            cx = x + i * gap + r
            if h_ == 1:
                d.circle(cx, y, r, fill=GOOD)
            elif h_ == 2:
                d.circle(cx, y, r, fill=AMBER)
            else:
                d.circle(cx, y, r - .5, fill=WHITE, stroke=mix(LAVENDER, INDIGO, 0.35), sw=1)
    return 5 * gap


def status_icon(d, x, y, ok, s=14):
    if ok is True:
        d.circle(x + s / 2, y + s / 2, s / 2, fill=GOOD_TINT)
        icon(d, "check", x + 1, y + 1, s - 2, GOOD, 1.8)
    elif ok == "warn":
        d.circle(x + s / 2, y + s / 2, s / 2, fill=WARN_TINT)
        icon(d, "alert", x + 1.5, y + 1, s - 3, WARN_TEXT, 1.6)
    else:
        d.circle(x + s / 2, y + s / 2, s / 2, fill=BAD_TINT)
        icon(d, "x", x + 1, y + 1, s - 2, BAD, 1.8)


# --------------------------------------------------------------------------
# Visualisations
# --------------------------------------------------------------------------


def smooth_noise(rng, n, k=6, lo=0.0, hi=1.0):
    pts = [rng.random() for _ in range(n // k + 3)]
    out = []
    for i in range(n):
        f = i / k
        a, b = pts[int(f)], pts[int(f) + 1]
        t = f - int(f)
        t = t * t * (3 - 2 * t)
        out.append(lo + (hi - lo) * (a + (b - a) * t))
    return out


def waveform(d, x, y, w, h, played=0.25, seed=3, bar=2.0, gap=1.2, label="Waveform"):
    rng = random.Random(seed)
    n = int(w / (bar + gap))
    env = smooth_noise(rng, n, 9, 0.25, 1.0)
    fine = smooth_noise(rng, n, 2, 0.45, 1.0)
    p_play, p_rest = [], []
    cy = y + h / 2
    for i in range(n):
        a = max(1.0, env[i] * fine[i] * (h / 2 - 1))
        bx = x + i * (bar + gap)
        seg = "M%s %s h%s V%s h-%s Z" % (fmt(round(bx, 2)), fmt(round(cy - a, 2)), fmt(bar),
                                         fmt(round(cy + a, 2)), fmt(bar))
        (p_play if i / n < played else p_rest).append(seg)
    with d.group(label):
        d.rect(x, y, w, h, fill=WHITE, opacity=0)
        if p_play:
            d.path(" ".join(p_play), fill=PRIMARY, label="Waveform played")
        d.path(" ".join(p_rest), fill=WAVE_REST, label="Waveform remaining")
        px = x + w * played
        d.line(px, y - 4, px, y + h + 4, stroke=TEXT, sw=1.5)
        d.circle(px, y - 4, 4, fill=TEXT)


def spectrogram(d, x, y, w, h, cutoff=21.8, nyq=22.05, seed=7, cols=110, rows=40, playhead=0.24,
                show_cutoff=True, label="Spectrogram", axis=True, progress_done=1.0):
    rng = random.Random(seed)
    env = smooth_noise(rng, cols, 7, 0.45, 1.0)
    onset = [1.0 + (0.35 if rng.random() < 0.08 else 0) for _ in range(cols)]
    band = smooth_noise(rng, rows, 3, 0.85, 1.15)
    cw, rh = w / cols, h / rows
    with d.group(label):
        d.rect(x, y, w, h, fill=SPEC_LEVELS[0])
        with d.group("Spectrogram cells"):
            for r in range(rows):
                f = (rows - 1 - r + 0.5) / rows * nyq
                run_s, run_l = 0, None
                cells = []
                for c in range(cols):
                    if c / cols > progress_done:
                        lvl = 0
                    elif f > cutoff:
                        lvl = 1 if rng.random() < 0.08 else 0
                    else:
                        base = 1 - (f / min(nyq, 24.0)) ** 0.5 * 0.7
                        v = base * env[c] * band[r] * onset[c] + rng.uniform(-0.06, 0.06)
                        if f > cutoff - 1.5:
                            v *= 0.75
                        lvl = max(1, min(8, int(v * 9.4)))
                    if lvl != run_l:
                        if run_l:
                            cells.append((run_s, c, run_l))
                        run_s, run_l = c, lvl
                if run_l:
                    cells.append((run_s, cols, run_l))
                for s, e, lv in cells:
                    d.rect(round(x + s * cw, 2), round(y + r * rh, 2), round((e - s) * cw + 0.4, 2),
                           round(rh + 0.4, 2), fill=SPEC_LEVELS[lv])
        if show_cutoff:
            cyl = y + h * (1 - cutoff / nyq)
            d.line(x, cyl, x + w, cyl, stroke=AMBER, sw=1.5, dash="5 3")
            t = "cutoff %s kHz" % cutoff
            tws = tw(t, 10, 700) + 10
            d.rect(x + w - tws - 6, cyl + 4, tws, 16, fill=mix(INK, BLACK, 0.2), rx=4, opacity=0.85)
            d.text(x + w - 11, cyl + 15.5, t, 10, 700, AMBER, anchor="end")
        if playhead is not None:
            px = x + w * playhead
            d.line(px, y, px, y + h, stroke=WHITE, sw=1.5)
            d.path("M%s %s l5 -6 h-10 Z" % (fmt(px), fmt(y + 6)), fill=WHITE)
        if axis:
            for khz in (0, 5, 10, 15, 20):
                if khz > nyq:
                    continue
                yy = y + h * (1 - khz / nyq)
                d.text(x - 6, min(yy + 3.5, y + h), "%dk" % khz if khz else "0", 9.5, 500, TEXT_3, anchor="end")


# --------------------------------------------------------------------------
# Album covers and CD-art scans (abstract placeholders)
# --------------------------------------------------------------------------


def cover(d, x, y, s, key, rx=6, soft=False):
    names = {"kob": "Kind of Blue", "okc": "OK Computer", "tanox": "Astronomía de Balcón",
             "boc": "Geogaddi", "sol": "When I Get Home"}
    with d.group("Cover · %s" % names.get(key, key)):
        if key == "kob":
            d.rect(x, y, s, s, fill=mix(INDIGO, INK, 0.5), rx=rx)
            d.circle(x + s * .66, y + s * .40, s * .25, fill=INDIGO)
            d.circle(x + s * .66, y + s * .40, s * .09, fill=LAVENDER)
            d.rect(x + s * .12, y + s * .74, s * .46, max(1, s * .04), fill=AMBER)
            d.rect(x + s * .12, y + s * .83, s * .30, max(1, s * .028), fill=mix(LAVENDER, INDIGO, .35))
        elif key == "okc":
            d.rect(x, y, s, s, fill=mix(WHITE, LAVENDER, .85), rx=rx)
            d.path("M%s %s L%s %s L%s %s L%s %s Z" % (fmt(x + s * .1), fmt(y + s * .78), fmt(x + s * .55),
                   fmt(y + s * .18), fmt(x + s * .9), fmt(y + s * .3), fmt(x + s * .42), fmt(y + s * .9)), fill=INDIGO)
            d.rect(x + s * .62, y + s * .56, s * .16, s * .16, fill=RED)
            d.rect(x + s * .1, y + s * .1, s * .34, max(1, s * .03), fill=TEXT)
            d.rect(x + s * .1, y + s * .17, s * .22, max(1, s * .03), fill=TEXT_2)
        elif key == "tanox":
            d.rect(x, y, s, s, fill=mix(GREEN, INK, .68), rx=rx)
            d.circle(x + s * .7, y + s * .3, s * .13, fill=AMBER)
            for i, (a, b) in enumerate([(.18, .2), (.32, .12), (.45, .28), (.24, .4), (.86, .52), (.55, .1)]):
                d.circle(x + s * a, y + s * b, max(.8, s * .012 * (1 + i % 2)), fill=LAVENDER)
            d.rect(x, y + s * .7, s, max(1, s * .03), fill=mix(GREEN, INK, .9))
            for i in range(9):
                d.rect(x + s * (.06 + i * .11), y + s * .7, max(1, s * .022), s * .3 - rx * .3, fill=mix(GREEN, INK, .9))
        elif key == "boc":
            d.rect(x, y, s, s, fill=mix(WHITE, GREEN, .28), rx=rx)
            for i, r in enumerate((.38, .29, .2, .11)):
                d.circle(x + s * .5, y + s * .5, s * r, fill=mix(WHITE, GREEN, .28 + .16 * (i + 1)) if i % 2 == 0 else mix(WHITE, GREEN, .25))
            d.circle(x + s * .5, y + s * .5, s * .04, fill=mix(GREEN, INK, .5))
        elif key == "sol":
            d.rect(x, y, s, s, fill=AMBER, rx=rx)
            d.path("M%s %s A%s %s 0 0 1 %s %s Z" % (fmt(x + s * .14), fmt(y + s * .78), fmt(s * .36), fmt(s * .36),
                   fmt(x + s * .86), fmt(y + s * .78)), fill=RED)
            d.circle(x + s * .5, y + s * .3, s * .07, fill=INDIGO)
            d.rect(x + s * .14, y + s * .84, s * .72, max(1, s * .025), fill=mix(RED, INK, .3))
        if soft:
            d.rect(x, y, s, s, fill=WHITE, rx=rx, opacity=0.28)


def scan(d, x, y, w, h, part, variant=0, rx=4, soft=False):
    """Placeholder scan thumbnails for the five CD-art parts."""
    tone = [0, .12, -.1, .2][variant % 4]
    with d.group("Scan · %s" % part):
        if part == "front":
            cover(d, x, y, min(w, h), "kob", rx=rx)
        elif part == "back":
            bg = mix(LAVENDER, WHITE, .35 + tone)
            d.rect(x, y, w, h, fill=bg, rx=rx)
            sp = max(2, w * .045)
            d.rect(x, y, sp, h, fill=mix(INDIGO, INK, .5))
            d.rect(x + w - sp, y, sp, h, fill=mix(INDIGO, INK, .5))
            for i in range(6):
                d.rect(x + w * .18, y + h * (.18 + i * .1), w * (.42 if i % 2 else .52), max(1, h * .035), fill=TEXT_3)
            d.rect(x + w * .66, y + h * .7, w * .18, h * .14, fill=TEXT, opacity=.6)
        elif part == "inside":
            d.rect(x, y, w, h, fill=mix(WHITE, LAVENDER, .25 + tone), rx=rx)
            for col in (0, 1):
                for i in range(7):
                    d.rect(x + w * (.1 + col * .45), y + h * (.14 + i * .1), w * (.34 if i % 3 else .26),
                           max(1, h * .025), fill=TEXT_3)
            d.rect(x + w * .55, y + h * .72, w * .34, h * .18, fill=mix(INDIGO, INK, .3))
        elif part == "tray":
            d.rect(x, y, w, h, fill=mix(INDIGO, INK, .3 + tone), rx=rx)
            d.circle(x + w * .32, y + h * .52, min(w, h) * .26, fill=mix(LAVENDER, INDIGO, .4))
            d.rect(x + w * .62, y + h * .3, w * .26, max(1, h * .04), fill=LAVENDER)
            d.rect(x + w * .62, y + h * .4, w * .18, max(1, h * .04), fill=mix(LAVENDER, INDIGO, .4))
        elif part == "disc":
            cx, cy, r = x + w / 2, y + h / 2, min(w, h) / 2 - 1
            d.circle(cx, cy, r, fill=mix(LAVENDER, WHITE, .2 + tone), stroke=BORDER)
            d.circle(cx, cy, r * .72, fill=mix(INDIGO, INK, .4))
            d.circle(cx, cy, r * .5, fill=INDIGO)
            d.rect(cx - r * .3, cy + r * .55, r * .6, max(1, r * .06), fill=AMBER)
            d.circle(cx, cy, r * .2, fill=WHITE, stroke=BORDER)
        if soft:
            d.rect(x, y, w, h, fill=WHITE, rx=rx, opacity=.35)


# --------------------------------------------------------------------------
# Sample content
# --------------------------------------------------------------------------
KOB_TRACKS = [("So What", "9:25", "64.6 MB"), ("Freddie Freeloader", "9:49", "67.4 MB"),
              ("Blue in Green", "5:37", "38.5 MB"), ("All Blues", "11:33", "79.3 MB"),
              ("Flamenco Sketches", "9:26", "64.7 MB"), ("Flamenco Sketches (alt take)", "9:32", "65.4 MB")]

RESULTS = [
    dict(cls="cd", fmt="FLAC 16/44.1", tier="A", ident="KindOfBlue_Legacy_1997_EAC", match="close", tracks=6,
         length="55:24", size="375 MB", year="1997", rip=["log", "cue"], source="CD rip", level="V3", inlib=True),
    dict(cls="cd", fmt="FLAC 16/44.1", tier="B", ident="miles-davis-kind-of-blue-1959", match="exact", tracks=6,
         length="55:22", size="380 MB", year="1959", rip=["log", "cue"], source="CD rip", level="V2", sel=True),
    dict(cls="hires", fmt="FLAC 24/192", tier="C", ident="Miles-Davis-Sextet-Kind-of-Blue-LP-192", match="close",
         tracks=6, length="55:40", size="1.6 GB", year="1959", rip=[], source="vinyl rip", level="V1"),
    dict(cls="lossless", fmt="WAV 16/44.1", tier="?", ident="kind-of-blue-wav-2009", match="exact", tracks=6,
         length="55:22", size="586 MB", year="2009", rip=["cue"], source="plain", level="V0"),
    dict(cls="lossy_hi", fmt="MP3 VBR ~245 kbps", tier="L", ident="miles-davis-kind-of-blue_202512", match="exact",
         tracks=6, length="55:22", size="101 MB", year="1959", rip=[], source="web", level="V1"),
    dict(cls="lossy", fmt="MP3 128 kbps", tier="L", ident="kindofblue-mp3-128", match="close", tracks=5,
         length="45:50", size="44 MB", year="—", rip=[], source="plain", level="V0"),
    dict(cls="failed", fmt="FLAC (corrupt)", tier="D", ident="kob-1959-flac-partial", match="close", tracks=4,
         length="—", size="210 MB", year="1959", rip=["log"], source="CD rip", level="V1"),
]

LIBRARY = [
    dict(key="boc", artist="Boards of Canada", album="Geogaddi", year="2002", fmt="FLAC 16/44.1", tracks=23,
         cover=True, issues=0, art=[1, 1, 1, 0, 1], cls="cd", tier="A"),
    dict(key="kob", artist="Miles Davis", album="Kind of Blue", year="1959", fmt="FLAC 16/44.1", tracks=6,
         cover=True, issues=0, art=[1, 1, 2, 1, 2], cls="cd", tier="B"),
    dict(key="okc", artist="Radiohead", album="OK Computer", year="1997", fmt="FLAC 16/44.1", tracks=12,
         cover=True, issues=2, art=[1, 1, 0, 0, 0], cls="cd", tier="A"),
    dict(key="sol", artist="Solange", album="When I Get Home", year="2019", fmt="FLAC 24/48", tracks=19,
         cover=False, issues=1, art=[0, 0, 0, 0, 0], cls="hires", tier="B"),
    dict(key="tanox", artist="Tanox", album="Astronomía de Balcón", year="2006", fmt="MP3 VBR ~245 kbps",
         tracks=11, cover=True, issues=0, art=[1, 0, 0, 0, 0], cls="lossy_hi", tier="L"),
]

OKC_TRACKS = [("Airbag", "4:44"), ("Paranoid Android", "6:23"), ("Subterranean Homesick Alien", "4:27"),
              ("Exit Music (For a Film)", "4:24"), ("Let Down", "4:59"), ("Karma Police", "4:21"),
              ("Fitter Happier", "1:57"), ("Electioneering", "3:50"), ("Climbing Up the Walls", "4:45"),
              ("No Surprises", "3:48"), ("Lucky", "4:19"), ("The Tourist", "5:24")]

PARTS = [("front", "Front cover"), ("back", "Back + spines"), ("inside", "Inside of front insert"),
         ("tray", "Under-tray"), ("disc", "Disc label")]

# --------------------------------------------------------------------------
# App chrome
# --------------------------------------------------------------------------


def chrome(d, label):
    with d.layer("Background"):
        d.rect(0, 0, W, H, fill=APP_BG, label="App background")
    with d.layer("Title bar"):
        with d.group("Window title bar"):
            d.rect(0, 0, W, 36, fill=TITLEBAR)
            icon(d, "disc", 14, 10, 16, AMBER, 1.8)
            d.text(38, 23, "iaflac", 13, 700, WHITE)
            d.text(W / 2, 23, label, 12, 500, TITLE_TEXT, anchor="middle")
            icon(d, "minus", 1334, 10, 16, TITLE_TEXT, 1.3)
            icon(d, "square", 1370, 10, 16, TITLE_TEXT, 1.3)
            icon(d, "x", 1406, 10, 16, TITLE_TEXT, 1.3)


def activity_strip(d, x0, jobs):
    with d.layer("Activity strip"):
        with d.group("Activity strip"):
            d.rect(x0, 868, W - x0, 32, fill=WHITE)
            d.hline(x0, W, 868, BORDER)
            section_label(d, x0 + 16, 888, "Activity")
            x = x0 + 92
            for text, frac, state in jobs:
                with d.group("Job · %s" % text):
                    if state == "done":
                        status_icon(d, x, 877, True)
                    elif state == "failed":
                        status_icon(d, x, 877, False)
                    else:
                        d.circle(x + 7, 884, 4.5, fill=AMBER)
                    x += 20
                    d.text(x, 888, text, 12, 500, TEXT)
                    x += tw(text, 12, 500) + 10
                    if state == "running":
                        progress(d, x, 881, 64, frac, AMBER)
                        d.text(x + 70, 888, "%d%%" % round(frac * 100), 11.5, 600, TEXT_2)
                        icon(d, "x", x + 98, 877, 14, TEXT_3, 1.6)
                        x += 128
                    else:
                        x += 12
                    d.line(x, 878, x, 890, stroke=BORDER)
                    x += 16
            with d.group("Tool status"):
                status_icon(d, 1306, 877, True)
                d.text(1326, 888, "flac", 12, 500, TEXT_2)
                status_icon(d, 1366, 877, True)
                d.text(1386, 888, "ffmpeg", 12, 500, TEXT_2)


def sidebar_a(d, active):
    with d.layer("Navigation"):
        with d.group("Sidebar"):
            d.rect(0, 36, 200, H - 36, fill=SIDEBAR)
            items = [("Album Search", "search", "2"), ("CD Art", "disc", None), ("Library", "library", "3")]
            y = 56
            for name, ic, badge in items:
                nav_item(d, 12, y, name, ic, name == active, badge)
                y += 44
            d.hline(24, 176, 200, SIDEBAR_LINE)
            section_label(d, 24, 226, "Library", SIDEBAR_MUTED)
            d.text(24, 248, "5 albums · 2.4 GB", 12, 500, SIDEBAR_TEXT)
            d.text(24, 266, "D:\\Music", 12, 400, SIDEBAR_MUTED)
            section_label(d, 24, 302, "Art", SIDEBAR_MUTED)
            d.text(24, 324, "2 of 5 cases complete", 12, 500, SIDEBAR_TEXT)
            progress(d, 24, 334, 152, 0.4, GOOD, h=4, track=SIDEBAR_LINE)
            nav_item(d, 12, 812, "Settings", "settings", active == "Settings", None)


def nav_item(d, x, y, name, ic, on, badge):
    with d.group("Nav · %s" % name):
        if on:
            d.rect(x, y, 176, 40, fill=PRIMARY, rx=10)
        icon(d, ic, x + 14, y + 12, 16, WHITE if on else SIDEBAR_MUTED, 1.6)
        d.text(x + 42, y + 25, name, 13.5, 600 if on else 500, WHITE if on else SIDEBAR_TEXT)
        if badge:
            chip(d, x + 164, y + 11, badge, WHITE if on else SIDEBAR_LINE, PRIMARY_TEXT if on else SIDEBAR_TEXT,
                 h=18, size=10.5, pad=6, anchor="end")


def queue_bar(d, x, y, w):
    with d.layer("Queue"):
        with d.group("Queue drawer · collapsed"):
            d.box(x, y, w, 44, fill=WHITE, stroke=BORDER, rx=10)
            icon(d, "chev_up", x + 14, y + 14, 16, TEXT_2, 1.8)
            icon(d, "queue", x + 38, y + 14, 16, PRIMARY, 1.6)
            d.text(x + 62, y + 27, "Download queue", 13, 650, TEXT)
            cx = x + 180
            cx += chip(d, cx, y + 12, "1 downloading", WARN_TINT, WARN_TEXT, h=20, size=11, dot=AMBER) + 6
            cx += chip(d, cx, y + 12, "2 done", GOOD_TINT, GOOD_TEXT, h=20, size=11, dot=GOOD) + 14
            d.line(cx, y + 12, cx, y + 32, stroke=BORDER)
            cx += 14
            cover(d, cx, y + 10, 24, "boc", rx=4)
            d.text(cx + 32, y + 27, "Geogaddi", 12.5, 600, TEXT)
            progress(d, cx + 100, y + 19, 72, 0.43, AMBER)
            d.text(cx + 182, y + 27, "212 / 498 MB · file 9 of 23", 12, 400, TEXT_2)
            button(d, x + w - 14, y + 8, "Clear finished", "ghost", h=28, size=12, anchor="end")


# --------------------------------------------------------------------------
# Shared blocks
# --------------------------------------------------------------------------


def result_row(d, x, y, w, r, h=62, selected=False, last=False):
    with d.group("Result · %s" % r["ident"]):
        if selected:
            d.rect(x, y, w, h, fill=PRIMARY_TINT, rx=8)
            d.rect(x, y + 12, 3, h - 24, fill=PRIMARY, rx=1.5)
        px, right = x + 12, x + w - 10
        bw = quality_badge(d, px, y + 9, r["cls"], h=19, size=10.5)
        tier_chip(d, px + bw + 6, y + 9, r["tier"], 19)
        d.text(px + bw + 32, y + 23, r["fmt"], 12.5, 650, TEXT)
        lw_ = level_chip(d, right, y + 10, r["level"], anchor="end")
        right2 = right
        if r.get("inlib"):
            chip(d, right - lw_ - 6, y + 10, "In library", GOOD_TINT, GOOD_TEXT, h=18, size=10, pad=6,
                 icon_="check", anchor="end", rx=5)
        d.text(px, y + 40, fit(r["ident"], 12, right2 - px), 12, 500, TEXT_2)
        meta = "%s · %s · %d tracks · %s · %s · %s" % (r["source"], r["match"], r["tracks"], r["length"],
                                                        r["size"], r["year"])
        rx_ = right
        for f in reversed(r["rip"]):
            rx_ -= mini_chip(d, rx_, y + 44, f, anchor="end") + 4
        d.text(px, y + 55, fit(meta, 11, rx_ - px - 4), 11, 400, TEXT_3)
        if not selected and not last:
            d.hline(x + 8, x + w - 8, y + h, BORDER_SOFT)


def search_notes(d, x, y, w, h=80):
    with d.group("Search notes"):
        d.box(x, y, w, h, fill=SURFACE_ALT, stroke=BORDER, rx=10)
        icon(d, "info", x + 12, y + 12, 15, PRIMARY, 1.5)
        d.text(x + 34, y + 24, "Search notes", 12, 650, TEXT)
        lines = [("2 near misses:", " Kind of Blue (Live 1960), Kinda Blue"),
                 ("Other lossless:", " 1 ALAC, 1 APE upload found"),
                 ("3 left out:", " no audio files (2), stream only (1)")]
        for i, (a, b) in enumerate(lines):
            d.spans(x + 34, y + 42 + i * 16, [(a, 11.5, 600, TEXT_2), (b, 11.5, 400, TEXT_2)])


def track_picker(d, x, y, w, text, h=30):
    field(d, x, y, w, text, h=h, chevron=True, size=12, label="Track picker", icon_="music")


# --------------------------------------------------------------------------
# Option A · Workspace
# --------------------------------------------------------------------------


def screen_A1(d):
    chrome(d, "Option A · Workspace · Album Search")
    sidebar_a(d, "Album Search")
    LX, LW = 216, 376
    CX, CW = 608, 480
    RX, RW = 1104, 320

    with d.layer("Search"):
        with d.group("Card · Search"):
            card(d, LX, 52, LW, 176)
            field(d, LX + 16, 68, LW - 32, "Miles Davis", prefix="Artist", icon_="search", focused=True,
                  label="Field · Artist")
            field(d, LX + 16, 110, LW - 124, "Kind of Blue", prefix="Album", label="Field · Album (optional)")
            d.text(LX + LW - 120, 131, "", 11)
            button(d, LX + LW - 16, 110, "Search", "primary", w=84, h=34, anchor="end")
            cx = LX + 16
            for f in ("FLAC", "WAV", "MP3"):
                cx += filter_chip(d, cx, 156, f, True) + 6
            field(d, LX + LW - 144, 155, 128, "Quality", prefix="Sort", h=28, size=12, chevron=True,
                  label="Dropdown · Sort (Quality, Match, Popularity)")
            checkbox(d, LX + 16, 196, False, "Lossless only")
            checkbox(d, LX + 140, 196, False, "Hide albums I already have")
        search_notes(d, LX, 240, LW, 78)

    with d.layer("Results"):
        with d.group("Card · Results"):
            card(d, LX, 330, LW, 470, "Results", "7 uploads · best quality first")
            icon_button(d, LX + LW - 44, 342, "filter", 26)
            y = 366
            for i, r in enumerate(RESULTS):
                result_row(d, LX + 6, y, LW - 12, r, 60, r.get("sel", False), i == len(RESULTS) - 1)
                y += 61

    with d.layer("Album detail"):
        with d.group("Card · Album detail"):
            card(d, CX, 52, CW, 392)
            cover(d, CX + 16, 68, 84, "kob", 8)
            d.text(CX + 116, 90, "Kind of Blue", 21, 700, TEXT)
            d.text(CX + 116, 111, "Miles Davis · 1959 · Columbia", 13, 500, TEXT_2)
            icon(d, "external", CX + 116, 121, 13, PRIMARY, 1.5)
            d.text(CX + 134, 132, "archive.org/details/miles-davis-kind-of-blue-1959", 12, 500, PRIMARY)
            cx = CX + 116
            cx += chip(d, cx, 142, "CC BY-NC-ND 4.0", SURFACE_SUNK, TEXT_2, h=20, size=10.5, rx=5) + 6
            cx += chip(d, cx, 142, "Uploader: “EAC secure rip”", WHITE, TEXT_2, stroke=BORDER, h=20, size=10.5, rx=5) + 6
            quality_badge(d, CX + CW - 16, 68, "cd", anchor="end")
            tier_chip(d, CX + CW - 16 - 20, 94, "B", 20)
            d.text(CX + CW - 42, 109, "Tier", 11, 600, TEXT_3, anchor="end")
            # verdict
            with d.group("Quality verdict"):
                d.box(CX + 16, 172, CW - 32, 76, fill=GOOD_TINT, stroke=None, rx=10)
                d.spans(CX + 30, 193, [("Quality verdict  ", 11, 700, GOOD_TEXT),
                                       ("Tier B · good CD rip, checksum not verified", 12.5, 600, TEXT)])
                ev = [(True, "EAC · all tracks accurately ripped", CX + 30, 214),
                      (True, "6 / 6 tracks decode", CX + 270, 214),
                      (False, "Log checksum missing", CX + 30, 234),
                      (False, "Track 6 not in AccurateRip", CX + 270, 234)]
                for ok, t, ex, ey in ev:
                    status_icon(d, ex, ey - 11, ok, 14)
                    d.text(ex + 20, ey, t, 12, 400, TEXT)
            # tracks
            with d.group("Track list"):
                ty = 260
                cols = [("#", CX + 16, "start"), ("Title", CX + 40, "start"), ("Length", CX + 250, "end"),
                        ("Size", CX + 320, "end"), ("Rate / bits", CX + 394, "end"), ("Decode", CX + CW - 16, "end")]
                for t, xx, an in cols:
                    d.text(xx, ty + 14, t.upper(), 10, 700, TEXT_3, anchor=an, ls=.5)
                d.hline(CX + 16, CX + CW - 16, ty + 22, BORDER)
                for i, (t, ln, sz) in enumerate(KOB_TRACKS):
                    yy = ty + 24 + i * 23
                    if i == 0:
                        d.rect(CX + 10, yy + 1, CW - 20, 22, fill=PRIMARY_TINT, rx=6)
                    d.text(CX + 16, yy + 16, "%d" % (i + 1), 12, 500, TEXT_3)
                    d.text(CX + 40, yy + 16, fit(t, 12.5, 160, 500), 12.5, 500 if i else 600, TEXT)
                    d.text(CX + 250, yy + 16, ln, 12, 400, TEXT_2, anchor="end")
                    d.text(CX + 320, yy + 16, sz, 12, 400, TEXT_2, anchor="end")
                    d.text(CX + 394, yy + 16, "44.1 / 16", 12, 400, TEXT_2, anchor="end")
                    status_icon(d, CX + CW - 30, yy + 5, True, 14)
            with d.group("Other uploads of this album"):
                d.text(CX + 16, 432, "Other uploads", 11.5, 600, TEXT_3)
                cx = CX + 104
                for t in ("A · FLAC 16/44.1", "C · FLAC 24/192", "L · MP3 VBR", "+3 more"):
                    cx += chip(d, cx, 419, t, WHITE, TEXT_2, stroke=BORDER, h=20, size=10.5, rx=5) + 6

    with d.layer("Quick test"):
        with d.group("Card · Quick test"):
            card(d, CX, 456, CW, 132, "Quick test", "headers + rip log · 2 s", tsize=14)
            bw_ = button(d, CX + CW - 16, 463, "Run again", "secondary", h=26, size=12, icon_="refresh", anchor="end")
            chip(d, CX + CW - 24 - bw_, 466, "Done", GOOD_TINT, GOOD_TEXT, h=20, size=11, icon_="check", anchor="end")
            play_button(d, CX + 16, 498, 30, playing=False)
            track_picker(d, CX + 54, 498, 180, "01 · So What")
            d.text(CX + 246, 518, "2:14 / 9:25", 12, 500, TEXT_2)
            chip(d, CX + CW - 16, 503, "44.1 kHz · 16-bit · stereo", SURFACE_SUNK, TEXT, h=22, size=11,
                 rx=6, anchor="end", label="Readout")
            waveform(d, CX + 16, 538, CW - 32, 36, played=0.24, seed=4)

    with d.layer("Deep test"):
        with d.group("Card · Deep test"):
            card(d, CX, 600, CW, 200, "Deep test", "full decode + spectrum", tsize=14)
            d.text(CX + CW - 52, 622, "42% · track 3 of 6", 11.5, 600, WARN_TEXT, anchor="end")
            progress(d, CX + CW - 60 - tw("42% · track 3 of 6", 11.5, 600) - 88, 616, 80, 0.42, AMBER)
            icon_button(d, CX + CW - 42, 607, "x", 26, label="Button · Cancel deep test")
            play_button(d, CX + 16, 642, 30, playing=True)
            track_picker(d, CX + 54, 642, 180, "01 · So What")
            d.text(CX + 246, 662, "2:14 / 9:25", 12, 500, TEXT_2)
            verdict_chip(d, CX + CW - 16, 645, "PASS", "21.8", anchor="end")
            spectrogram(d, CX + 40, 684, 320, 104, cutoff=21.8, seed=11, cols=96, rows=34, playhead=0.24)
            with d.group("Album spectrum summary"):
                ax = CX + 376
                section_label(d, ax, 694, "Album")
                vals = [(21.8, "done"), (21.8, "done"), (21.7, "run"), (0, "wait"), (0, "wait"), (0, "wait")]
                for i, (v, st) in enumerate(vals):
                    yy = 704 + i * 15
                    d.text(ax, yy + 9, str(i + 1), 10, 600, TEXT_3)
                    bw_ = 50
                    d.rect(ax + 12, yy + 3, bw_, 6, fill=SURFACE_SUNK, rx=3)
                    if st == "done":
                        d.rect(ax + 12, yy + 3, bw_ * v / 22.05, 6, fill=GOOD, rx=3)
                        d.text(ax + 88, yy + 9, "%.1f" % v, 10, 600, TEXT_2, anchor="end")
                    elif st == "run":
                        d.rect(ax + 12, yy + 3, bw_ * .45, 6, fill=AMBER, rx=3)
                        d.text(ax + 88, yy + 9, "…", 10, 600, TEXT_3, anchor="end")
                    else:
                        d.text(ax + 88, yy + 9, "–", 10, 600, TEXT_3, anchor="end")

    with d.layer("Metadata"):
        with d.group("Card · Metadata"):
            card(d, RX, 52, RW, 504, "Metadata")
            toggle(d, RX + RW - 48, 66, True)
            d.text(RX + RW - 56, 79, "Auto MusicBrainz", 11.5, 500, TEXT_2, anchor="end")
            section_label(d, RX + 16, 106, "MusicBrainz matches")
            mbs = [("98", "Kind of Blue", "1997-03-25 · US · CD · +0:01", True),
                   ("91", "Kind of Blue (Legacy Edition)", "2009 · EU · 2×CD · +14:20", False),
                   ("84", "Kind of Blue", "2015 · XW · Digital · ±0:00", False)]
            for i, (sc, rel, meta, applied) in enumerate(mbs):
                yy = 114 + i * 32
                with d.group("MusicBrainz match · %s %s" % (sc, rel)):
                    if applied:
                        d.rect(RX + 8, yy, RW - 16, 30, fill=PRIMARY_TINT, rx=7)
                    sfill = GOOD_TINT if int(sc) >= 95 else SURFACE_SUNK
                    d.box(RX + 16, yy + 5, 26, 20, fill=sfill, stroke=None, rx=5)
                    d.text(RX + 29, yy + 19, sc, 11, 700, GOOD_TEXT if int(sc) >= 95 else TEXT_2, anchor="middle")
                    d.text(RX + 50, yy + 13, fit(rel, 12, 180, 600), 12, 600, TEXT)
                    d.text(RX + 50, yy + 26, meta, 10.5, 400, TEXT_3)
                    if applied:
                        chip(d, RX + RW - 16, yy + 6, "Applied", GOOD_TINT, GOOD_TEXT, h=18, size=10.5,
                             icon_="check", anchor="end", pad=6, rx=5)
                    else:
                        button(d, RX + RW - 16, yy + 4, "Apply", "secondary", h=22, size=11, anchor="end")
            section_label(d, RX + 16, 226, "Album tags")
            lx = RX + RW - 16
            for s in reversed(("file", "ia", "MB", "you")):
                lx -= source_badge(d, lx, 215, s, anchor="end") + 4
            flds = [("Album artist", "Miles Davis", "MB"), ("Album", "Kind of Blue", "MB"),
                    ("Date", "1959", "you"), ("Genre", "Jazz", "ia"),
                    ("Label", "Columbia", "MB"), ("Catalog #", "CK 64935", "file")]
            for i, (lab, val, src) in enumerate(flds):
                fx = RX + 16 + (i % 2) * 148
                fy = 236 + (i // 2) * 46
                d.text(fx, fy + 10, lab, 11, 600, TEXT_2)
                field(d, fx, fy + 15, 140, val, h=26, size=12, src=src, label="Field · %s" % lab)
            section_label(d, RX + 16, 392, "Track tags")
            d.text(RX + RW - 16, 392, "2 changed", 11, 600, PRIMARY, anchor="end")
            with d.group("Track tag table"):
                ty = 400
                for t, xx in (("Disc", RX + 16), ("#", RX + 48), ("Title", RX + 70), ("Track artist", RX + 222)):
                    d.text(xx, ty + 13, t.upper(), 10, 700, TEXT_3, ls=.5)
                d.hline(RX + 16, RX + RW - 16, ty + 20, BORDER)
                for i, (t, _, _) in enumerate(KOB_TRACKS):
                    yy = ty + 21 + i * 21
                    changed = i == 5
                    if changed:
                        d.rect(RX + 10, yy + 1, RW - 20, 20, fill=WARN_TINT, rx=5)
                    d.text(RX + 24, yy + 15, "1", 11.5, 400, TEXT_2, anchor="middle")
                    d.text(RX + 48, yy + 15, "%d" % (i + 1), 11.5, 400, TEXT_2)
                    d.text(RX + 70, yy + 15, fit(t, 11.5, 146), 11.5, 500, TEXT)
                    d.text(RX + 222, yy + 15, "Miles Davis", 11.5, 400, TEXT_2)

    with d.layer("Save"):
        with d.group("Card · Save"):
            card(d, RX, 568, RW, 292, "Save to library")
            d.text(RX + RW - 16, 596, "380 MB · 7 files", 12, 500, TEXT_3, anchor="end")
            with d.group("Destination path"):
                d.box(RX + 16, 610, RW - 32, 30, fill=SURFACE_ALT, stroke=BORDER, rx=8)
                icon(d, "folder", RX + 26, 617, 16, PRIMARY, 1.5)
                d.text(RX + 50, 630, "Music/Miles Davis/Kind of Blue (1959)/", 12, 600, TEXT)
            d.text(RX + 16, 658, "01 - So What.flac · 02 - Freddie Freeloader.flac", 11, 400, TEXT_2)
            d.text(RX + 16, 673, "… 06 - Flamenco Sketches (alt take).flac · cover.jpg", 11, 400, TEXT_2)
            section_label(d, RX + 16, 698, "Tag changes")
            with d.group("Tag changes"):
                for i, (k, a, b) in enumerate([("date", "1959-08-17", "1959"),
                                               ("title 6", "Flamenco Sketches alt", "… (alt take)")]):
                    yy = 716 + i * 18
                    d.text(RX + 16, yy, k, 11, 600, TEXT_3)
                    d.text(RX + 66, yy, a, 11, 400, BAD_TEXT, deco="line-through")
                    ax = RX + 66 + tw(a, 11) + 6
                    d.text(ax, yy, "→", 11, 400, TEXT_3)
                    d.text(ax + 16, yy, b, 11, 600, GOOD_TEXT)
            with d.group("Cover picker"):
                for i, (lab, sz, sel) in enumerate([("Upload's cover", "600 px", False),
                                                    ("Cover Art Archive", "1400 px", True)]):
                    bx = RX + 16 + i * 148
                    d.box(bx, 742, 140, 34, fill=PRIMARY_TINT if sel else WHITE, stroke=PRIMARY if sel else BORDER,
                          rx=8, sw=1.5 if sel else 1)
                    cover(d, bx + 5, 747, 24, "kob", 4, soft=not sel)
                    d.text(bx + 35, 757, lab, 10.5, 600, TEXT)
                    d.text(bx + 35, 769, sz, 10, 400, TEXT_3)
            chip(d, RX + 16, 822, "Folder exists · will merge", WARN_TINT, WARN_TEXT, h=22, size=10.5,
                 icon_="alert", rx=6)
            button(d, RX + RW - 16, 816, "Add to queue", "primary", h=32, icon_="download", anchor="end")
            d.hline(RX + 16, RX + RW - 16, 798, BORDER_SOFT)

    queue_bar(d, LX, 816, CX + CW - LX)
    activity_strip(d, 200, [("Deep test · Kind of Blue", .42, "running"),
                            ("Download · Geogaddi", .43, "running"), ("Quick test · 7 uploads", 1, "done")])


def screen_A2(d):
    chrome(d, "Option A · Workspace · CD Art")
    sidebar_a(d, "CD Art")
    LX, LW = 216, 320
    CX, CW = 552, 448
    RX, RW = 1016, 408

    with d.layer("Album"):
        with d.group("Card · Album"):
            card(d, LX, 52, LW, 300, "Album")
            with d.group("Library album picker"):
                d.box(LX + 16, 96, LW - 32, 52, fill=WHITE, stroke=PRIMARY, rx=10)
                cover(d, LX + 24, 102, 40, "kob", 5)
                d.text(LX + 74, 119, "Kind of Blue", 13.5, 650, TEXT)
                d.text(LX + 74, 137, "Miles Davis · 1959", 12, 400, TEXT_2)
                icon(d, "chev_down", LX + LW - 40, 114, 16, TEXT_2)
            button(d, LX + 16, 160, "Find art", "primary", w=96, h=32, icon_="search")
            progress(d, LX + 124, 174, LW - 140, 1.0, GOOD)
            d.text(LX + 124, 166, "Done · 7 pressings · 14 s", 11, 600, GOOD_TEXT)
            with d.group("Find-art notes"):
                d.box(LX + 16, 208, LW - 32, 128, fill=SURFACE_ALT, stroke=BORDER, rx=10)
                notes = [("info", PRIMARY, "Matched release", "Columbia CK 64935 · 1997 · US CD"),
                         ("search", PRIMARY, "Pressings searched", "7 · 1997 XE CD, 1999 Legacy, 2009…"),
                         ("alert", WARN_TEXT, "Not found", "Inside of insert for 1997 → using 2009")]
                for i, (ic, col, a, b) in enumerate(notes):
                    yy = 222 + i * 38
                    icon(d, ic, LX + 28, yy, 15, col, 1.5)
                    d.text(LX + 50, yy + 11, a, 11.5, 650, TEXT)
                    d.text(LX + 50, yy + 27, b, 11.5, 400, TEXT_2)

    with d.layer("Library art checklist"):
        with d.group("Card · Library art checklist"):
            card(d, LX, 364, LW, 436, "Library art")
            d.text(LX + LW - 16, 392, "2 of 5 complete", 11.5, 500, TEXT_3, anchor="end")
            heads = ["Front", "Back", "Inside", "Tray", "Disc"]
            hx = [LX + 160 + i * 33 for i in range(5)]
            for h_, xx in zip(heads, hx):
                d.text(xx, 424, h_.upper(), 8.5, 700, TEXT_3, anchor="middle", ls=.2)
            d.hline(LX + 16, LX + LW - 16, 432, BORDER)
            rows = LIBRARY
            for i, a in enumerate(rows):
                yy = 434 + i * 50
                with d.group("Checklist · %s" % a["album"]):
                    if a["key"] == "kob":
                        d.rect(LX + 8, yy + 2, LW - 16, 46, fill=PRIMARY_TINT, rx=8)
                    cover(d, LX + 16, yy + 10, 30, a["key"], 4)
                    d.text(LX + 54, yy + 22, fit(a["album"], 12, 100, 600), 12, 600, TEXT)
                    d.text(LX + 54, yy + 37, fit(a["artist"], 11, 100), 11, 400, TEXT_3)
                    for v, xx in zip(a["art"], hx):
                        if v == 1:
                            status_icon(d, xx - 7, yy + 18, True, 14)
                        elif v == 2:
                            status_icon(d, xx - 7, yy + 18, "warn", 14)
                        else:
                            d.text(xx, yy + 30, "–", 13, 600, TEXT_3, anchor="middle")
                    if i < len(rows) - 1:
                        d.hline(LX + 16, LX + LW - 16, yy + 50, BORDER_SOFT)
            d.text(LX + 16, 714, "✓ found  ·  amber = soft or generated  ·  – missing", 10.5, 400, TEXT_3)
            button(d, LX + 16, 744, "Find art for whole library", "secondary", w=LW - 32, h=36, icon_="search")

    with d.layer("Found scans"):
        with d.group("Card · Found scans"):
            card(d, CX, 52, CW, 748, "Found scans", "5 parts · 3 sources")
            info = [("Cover Art Archive · 1997 XE CD", "595 dpi", "2835 × 2835 px", False, 4),
                    ("Cover Art Archive · 1997 XE CD", "410 dpi", "2440 × 1906 px", False, 2),
                    ("Cover Art Archive · 2009 Legacy", "300 dpi", "1417 × 1417 px", False, 3),
                    ("archive.org upload · scans/tray.jpg", "240 dpi", "1427 × 1115 px", False, 1),
                    ("archive.org upload · scans/cd.jpg", "150 dpi", "690 × 690 px", True, 2)]
            for i, ((pk, pn), (src, dpi, px, warn, nalt)) in enumerate(zip(PARTS, info)):
                yy = 92 + i * 140
                with d.group("Scan row · %s" % pn):
                    if warn:
                        d.box(CX + 10, yy, CW - 20, 132, fill=WARN_TINT, stroke=WARN_LINE, rx=10)
                    d.text(CX + 16 + 4, yy + 22, pn, 13.5, 650, TEXT)
                    tw_ = 104 if pk != "back" else 104
                    th = 104 if pk not in ("back", "tray") else 81
                    tx, ty = CX + 20, yy + 34
                    d.box(tx - 3, ty - 3, tw_ + 6, th + 6, fill=None, stroke=PRIMARY, rx=7, sw=2)
                    scan(d, tx, ty, tw_, th, pk, 0, rx=4, soft=warn)
                    ix = CX + 140
                    d.text(ix, yy + 52, fit(src, 12, CW - 160), 12, 500, TEXT_2)
                    cx = ix
                    if warn:
                        cx += chip(d, cx, yy + 62, dpi + " · below 200", AMBER, TEXT, h=20, size=11,
                                   icon_="alert", rx=5) + 6
                    else:
                        cx += chip(d, cx, yy + 62, dpi, GOOD_TINT, GOOD_TEXT, h=20, size=11, rx=5) + 6
                    d.text(cx + 2, yy + 76, px, 11, 400, TEXT_3)
                    d.text(ix, yy + 104, "Alternatives", 10.5, 600, TEXT_3)
                    ax = ix + 76
                    for k in range(nalt):
                        aw = 34 if pk not in ("back", "tray") else 40
                        ah = 34 if pk not in ("back", "tray") else 31
                        scan(d, ax, yy + 112 - ah + 8 - 4, aw, ah, pk, k + 1, rx=3)
                        d.box(ax, yy + 112 - ah + 4, aw, ah, fill=None, stroke=BORDER, rx=3)
                        ax += aw + 8
                    if ax + 80 < CX + CW - 16:
                        d.text(ax + 2, yy + 106, "+ search more", 10.5, 600, PRIMARY)
                    if i < 4 and not warn and not info[i + 1][3]:
                        d.hline(CX + 16, CX + CW - 16, yy + 136, BORDER_SOFT)

    with d.layer("Case preview"):
        with d.group("Card · Case preview"):
            card(d, RX, 52, RW, 748, "Case preview")
            segmented(d, RX + RW - 16 - 206, 62, ["Outside", "Inside", "Disc"], "Outside", h=28,
                      widths=[72, 64, 64], label="Preview page")
            case_page_outside(d, RX + 16, 104, RW - 32, 480)
            with d.group("Preview warnings"):
                chip(d, RX + 16, 596, "Disc label is soft (150 dpi)", WARN_TINT, WARN_TEXT, h=22, size=11,
                     icon_="alert", rx=6)
                chip(d, RX + 16, 624, "Inside of insert from 2009 pressing", WARN_TINT, WARN_TEXT, h=22, size=11,
                     icon_="alert", rx=6)
            d.hline(RX + 16, RX + RW - 16, 660, BORDER_SOFT)
            section_label(d, RX + 16, 686, "Print")
            d.text(RX + RW - 16, 686, "A4 · 3 mm bleed · 3 pages", 11, 500, TEXT_3, anchor="end")
            half = (RW - 40) / 2
            button(d, RX + 16, 700, "Make PDF", "primary", w=half, h=36, icon_="printer")
            button(d, RX + 24 + half, 700, "Open PDF", "secondary", w=half, h=36, icon_="external")
            button(d, RX + 16, 746, "Save scans to album folder", "secondary", w=RW - 32, h=36, icon_="folder")

    queue_bar(d, LX, 816, W - 16 - LX)
    activity_strip(d, 200, [("Find art · Kind of Blue", 1, "done"), ("Download · Geogaddi", .43, "running")])


def case_page_outside(d, x, y, w, h):
    """Page 1: front cover (120x120 mm) above the 151x118 mm tray card."""
    with d.group("Page 1 · Outside"):
        d.box(x, y, w, h, fill=SURFACE_ALT, stroke=None, rx=10)
        k = (h - 24) / 297.0
        pw, ph = 210 * k, 297 * k
        px, py = x + (w - pw) / 2, y + 12
        d.box(px, py, pw, ph, fill=WHITE, stroke=BORDER, rx=2, label="A4 page")
        fx, fy = px + (pw - 120 * k) / 2, py + 14 * k
        crop_marks(d, fx, fy, 120 * k, 120 * k)
        cover(d, fx, fy, 120 * k, "kob", 0)
        d.text(fx + 120 * k + 6, fy + 12, "Front", 10, 600, TEXT_2)
        d.text(fx + 120 * k + 6, fy + 25, "120 × 120", 9.5, 400, TEXT_3)
        tx, ty = px + (pw - 151 * k) / 2, fy + 120 * k + 12 * k
        crop_marks(d, tx, ty, 151 * k, 118 * k)
        scan(d, tx, ty, 151 * k, 118 * k, "back", 0, rx=0)
        sp = 6.5 * k
        d.line(tx + sp, ty, tx + sp, ty + 118 * k, stroke=WHITE, sw=1, dash="3 2")
        d.line(tx + 151 * k - sp, ty, tx + 151 * k - sp, ty + 118 * k, stroke=WHITE, sw=1, dash="3 2")
        d.text(tx + 151 * k / 2, ty + 118 * k + 14, "Tray card 151 × 118 mm · 2 spines", 9.5, 500, TEXT_3, anchor="middle")
        d.text(x + 10, y + h - 8, "Page 1 of 3", 9.5, 600, TEXT_3)


def crop_marks(d, x, y, w, h, m=5):
    for (cx, cy, sx, sy) in ((x, y, -1, -1), (x + w, y, 1, -1), (x, y + h, -1, 1), (x + w, y + h, 1, 1)):
        d.line(cx + sx * 2, cy, cx + sx * (2 + m), cy, stroke=TEXT_3, sw=.6)
        d.line(cx, cy + sy * 2, cx, cy + sy * (2 + m), stroke=TEXT_3, sw=.6)


def case_page_inside(d, x, y, w, h):
    with d.group("Page 2 · Inside"):
        d.box(x, y, w, h, fill=SURFACE_ALT, stroke=None, rx=10)
        k = (h - 24) / 297.0
        pw, ph = 210 * k, 297 * k
        px, py = x + (w - pw) / 2, y + 12
        d.box(px, py, pw, ph, fill=WHITE, stroke=BORDER, rx=2, label="A4 page")
        fx, fy = px + (pw - 120 * k) / 2, py + 14 * k
        crop_marks(d, fx, fy, 120 * k, 120 * k)
        scan(d, fx, fy, 120 * k, 120 * k, "inside", 0, rx=0)
        d.text(fx + 120 * k + 6, fy + 12, "Insert", 10, 600, TEXT_2)
        d.text(fx + 120 * k + 6, fy + 25, "inside", 9.5, 400, TEXT_3)
        tx, ty = px + (pw - 151 * k) / 2, fy + 120 * k + 12 * k
        crop_marks(d, tx, ty, 151 * k, 118 * k)
        scan(d, tx, ty, 151 * k, 118 * k, "tray", 0, rx=0)
        d.text(tx + 151 * k / 2, ty + 118 * k + 14, "Under-tray 151 × 118 mm", 9.5, 500, TEXT_3, anchor="middle")
        d.text(x + 10, y + h - 8, "Page 2 of 3", 9.5, 600, TEXT_3)


def case_page_disc(d, x, y, w, h):
    with d.group("Page 3 · Disc"):
        d.box(x, y, w, h, fill=SURFACE_ALT, stroke=None, rx=10)
        k = (h - 24) / 297.0
        pw, ph = 210 * k, 297 * k
        px, py = x + (w - pw) / 2, y + 12
        d.box(px, py, pw, ph, fill=WHITE, stroke=BORDER, rx=2, label="A4 page")
        r = 58.5 * k
        cx, cy = px + pw / 2, py + 20 * k + r
        d.circle(cx, cy, r + 2, fill=None, stroke=TEXT_3, sw=.6)
        scan(d, cx - r, cy - r, 2 * r, 2 * r, "disc", 0, soft=True)
        d.circle(cx, cy, 7.5 * k, fill=WHITE, stroke=TEXT_3, sw=.8)
        d.line(cx - 4, cy, cx + 4, cy, stroke=TEXT_3, sw=.6)
        d.line(cx, cy - 4, cx, cy + 4, stroke=TEXT_3, sw=.6)
        d.text(cx, cy + r + 18, "Disc label Ø 117 mm · centre hole Ø 15 mm", 9.5, 500, TEXT_3, anchor="middle")
        d.text(x + 10, y + h - 8, "Page 3 of 3", 9.5, 600, TEXT_3)


def screen_A3(d):
    chrome(d, "Option A · Workspace · Library")
    sidebar_a(d, "Library")
    LX, LW = 216, 688
    RX, RW = 920, 504
    with d.layer("Album table"):
        with d.group("Card · Library"):
            card(d, LX, 52, LW, 484, "Library", "5 albums · 2.4 GB · D:\\Music")
            field(d, LX + 300, 62, 200, "Filter albums…", h=30, icon_="search", placeholder=True, size=12,
                  label="Field · Filter")
            with d.group("View toggle"):
                d.box(LX + 512, 62, 64, 30, fill=SURFACE_SUNK, stroke=None, rx=8)
                d.box(LX + 515, 65, 29, 24, fill=WHITE, stroke=BORDER, rx=6)
                icon(d, "list", LX + 522, 69, 15, TEXT, 1.6)
                icon(d, "grid", LX + 552, 69, 15, TEXT_3, 1.5)
            button(d, LX + LW - 16, 62, "Rescan", "secondary", h=30, size=12, icon_="refresh", anchor="end")
            cols = [("", LX + 16), ("Artist", LX + 64), ("Album", LX + 210), ("Year", LX + 384), ("Format", LX + 426),
                    ("Tracks", LX + 584), ("Cover", LX + 616), ("Issues", LX + LW - 16)]
            for t, xx in cols:
                an = {"Issues": "end", "Tracks": "end", "Cover": "middle"}.get(t)
                d.text(xx, 124, t.upper(), 10, 700, TEXT_3, anchor=an, ls=.5)
            d.hline(LX + 16, LX + LW - 16, 132, BORDER)
            for i, a in enumerate(LIBRARY):
                yy = 134 + i * 60
                with d.group("Library row · %s" % a["album"]):
                    sel = a["key"] == "okc"
                    if sel:
                        d.rect(LX + 8, yy + 2, LW - 16, 56, fill=PRIMARY_TINT, rx=8)
                        d.rect(LX + 8, yy + 14, 3, 32, fill=PRIMARY, rx=1.5)
                    if a["cover"]:
                        cover(d, LX + 16, yy + 10, 40, a["key"], 5)
                    else:
                        d.box(LX + 16, yy + 10, 40, 40, fill=SURFACE_SUNK, stroke=BORDER, rx=5, dash="3 2")
                        icon(d, "image", LX + 28, yy + 22, 16, TEXT_3)
                    d.text(LX + 64, yy + 35, a["artist"], 13, 500, TEXT)
                    d.text(LX + 210, yy + 35, a["album"], 13, 650, TEXT)
                    d.text(LX + 384, yy + 35, a["year"], 12.5, 400, TEXT_2)
                    quality_badge(d, LX + 450, yy + 21, a["cls"], h=19, size=10) if False else None
                    col = GOOD_TEXT if a["fmt"].startswith("FLAC") else BAD_TEXT
                    d.text(LX + 426, yy + 35, a["fmt"], 12, 600, col)
                    d.text(LX + 584, yy + 35, str(a["tracks"]), 12.5, 400, TEXT_2, anchor="end")
                    status_icon(d, LX + 609, yy + 23, True if a["cover"] else False, 14)
                    if a["issues"]:
                        chip(d, LX + LW - 16, yy + 20, "%d" % a["issues"], WARN_TINT, WARN_TEXT, h=20, size=11,
                             icon_="alert", anchor="end", pad=7)
                    else:
                        d.text(LX + LW - 22, yy + 35, "0", 12.5, 400, TEXT_3, anchor="end")
                    if i < 4 and not sel and LIBRARY[i + 1]["key"] != "okc":
                        d.hline(LX + 16, LX + LW - 16, yy + 60, BORDER_SOFT)
            with d.group("Library health"):
                d.hline(LX + 16, LX + LW - 16, 456, BORDER_SOFT)
                cx = LX + 16
                cx += chip(d, cx, 474, "71 tracks", SURFACE_SUNK, TEXT_2, h=22, size=11) + 6
                cx += chip(d, cx, 474, "3 tag issues", WARN_TINT, WARN_TEXT, h=22, size=11, icon_="alert") + 6
                cx += chip(d, cx, 474, "1 album without cover", WARN_TINT, WARN_TEXT, h=22, size=11) + 6
                cx += chip(d, cx, 474, "1 lossy album", BAD_TINT, BAD_TEXT, h=22, size=11, dot=RED) + 6
                d.text(LX + LW - 16, 489, "Scanned 2 min ago", 11, 400, TEXT_3, anchor="end")

    with d.layer("Album view"):
        with d.group("Card · Album view"):
            card(d, RX, 52, RW, 484)
            cover(d, RX + 16, 68, 64, "okc", 7)
            d.text(RX + 96, 88, "OK Computer", 19, 700, TEXT)
            d.text(RX + 96, 108, "Radiohead · 1997 · 12 tracks · 53:21", 12.5, 400, TEXT_2)
            cx = RX + 96
            cx += quality_badge(d, cx, 116, "cd", h=19, size=10.5) + 6
            cx += chip(d, cx, 116, "FLAC 16/44.1", SURFACE_SUNK, TEXT_2, h=19, size=10.5, rx=5) + 6
            icon_button(d, RX + RW - 44, 66, "folder", 28, label="Button · Open folder")
            button(d, RX + RW - 52, 66, "CD art", "secondary", h=28, size=12, icon_="disc", anchor="end")
            with d.group("Tag issues"):
                d.box(RX + 16, 148, RW - 32, 84, fill=WARN_TINT, stroke=WARN_LINE, rx=10)
                d.text(RX + 30, 168, "2 tag issues", 12.5, 650, WARN_TEXT)
                issues = ["Track 7 “Fitter Happier”: missing track number",
                          ".cue file may make Volumio list it twice"]
                for i, t in enumerate(issues):
                    yy = 186 + i * 22
                    icon(d, "alert", RX + 30, yy - 1, 14, WARN_TEXT, 1.5)
                    d.text(RX + 50, yy + 10, t, 12, 400, TEXT)
                    button(d, RX + RW - 26, yy - 4, "Fix", "secondary", h=22, size=11, anchor="end")
            with d.group("Tracks with tags"):
                ty = 244
                for t, xx, an in (("#", RX + 16, None), ("Title", RX + 44, None), ("Track artist", RX + 290, None),
                                  ("Length", RX + 440, "end"), ("Tags", RX + RW - 16, "end")):
                    d.text(xx, ty + 12, t.upper(), 10, 700, TEXT_3, anchor=an, ls=.5)
                d.hline(RX + 16, RX + RW - 16, ty + 20, BORDER)
                for i, (t, ln) in enumerate(OKC_TRACKS):
                    yy = ty + 21 + i * 22
                    bad = i == 6
                    if bad:
                        d.rect(RX + 10, yy + 1, RW - 20, 21, fill=WARN_TINT, rx=5)
                    d.text(RX + 16, yy + 15, "–" if bad else str(i + 1), 11.5, 500, WARN_TEXT if bad else TEXT_3)
                    d.text(RX + 44, yy + 15, fit(t, 12, 236, 500), 12, 500, TEXT)
                    d.text(RX + 290, yy + 15, "Radiohead", 12, 400, TEXT_2)
                    d.text(RX + 440, yy + 15, ln, 12, 400, TEXT_2, anchor="end")
                    status_icon(d, RX + RW - 30, yy + 4, "warn" if bad else True, 14)

    with d.layer("Queue"):
        with d.group("Queue drawer · expanded"):
            QX, QY, QW = LX, 548, W - 16 - LX
            d.box(QX, QY, QW, 312, fill=WHITE, stroke=BORDER, rx=12)
            icon(d, "chev_down", QX + 14, QY + 14, 16, TEXT_2, 1.8)
            icon(d, "queue", QX + 38, QY + 14, 16, PRIMARY, 1.6)
            d.text(QX + 62, QY + 27, "Download queue", 14, 650, TEXT)
            cx = QX + 192
            cx += chip(d, cx, QY + 12, "1 downloading", WARN_TINT, WARN_TEXT, h=20, size=11, dot=AMBER) + 6
            cx += chip(d, cx, QY + 12, "1 tagging", PRIMARY_TINT, PRIMARY_TEXT, h=20, size=11) + 6
            cx += chip(d, cx, QY + 12, "1 paused", SURFACE_SUNK, TEXT_2, h=20, size=11) + 6
            cx += chip(d, cx, QY + 12, "1 failed", BAD_TINT, BAD_TEXT, h=20, size=11, dot=RED) + 6
            cx += chip(d, cx, QY + 12, "1 done", GOOD_TINT, GOOD_TEXT, h=20, size=11, dot=GOOD) + 6
            d.text(QX + QW - 140, QY + 27, "2 at a time", 11.5, 400, TEXT_3, anchor="end")
            button(d, QX + QW - 14, QY + 8, "Clear finished", "secondary", h=28, size=12, anchor="end")
            for t, xx in (("Album", QX + 16), ("State", QX + 330), ("Progress", QX + 450), ("", QX + QW - 16)):
                d.text(xx, QY + 58, t.upper(), 10, 700, TEXT_3, ls=.5)
            d.hline(QX + 16, QX + QW - 16, QY + 66, BORDER)
            rows = [("kob", "Kind of Blue", "Miles Davis · miles-davis-kind-of-blue-1959", "downloading", .40,
                     "152 / 380 MB · file 3 of 6", ["pause", "x"]),
                    ("boc", "Geogaddi", "Boards of Canada · boc-geogaddi-flac", "tagging", .96,
                     "23 / 23 files · writing tags", ["x"]),
                    ("sol", "When I Get Home", "Solange · solange-wigh-2019-24bit", "paused", .28,
                     "88 / 310 MB · file 5 of 19", ["play", "x"]),
                    ("okc", "OK Computer", "Radiohead · radiohead-okc-1997-eac", "failed", .55,
                     "Network error at file 7 of 12 · 3 tries", ["refresh", "trash"]),
                    ("tanox", "Astronomía de Balcón", "Tanox · tanox-astronomia-balcon", "done", 1,
                     "Saved · 1 problem: no cover found", ["folder", "trash"])]
            st = {"downloading": (WARN_TINT, WARN_TEXT, AMBER), "tagging": (PRIMARY_TINT, PRIMARY_TEXT, PRIMARY),
                  "paused": (SURFACE_SUNK, TEXT_2, mix(LAVENDER, INDIGO, .4)), "failed": (BAD_TINT, BAD_TEXT, RED),
                  "done": (GOOD_TINT, GOOD_TEXT, GOOD)}
            for i, (k, alb, sub, state, frac, ptxt, acts) in enumerate(rows):
                yy = QY + 68 + i * 48
                with d.group("Queue row · %s" % alb):
                    cover(d, QX + 16, yy + 8, 32, k, 4)
                    d.text(QX + 58, yy + 22, alb, 12.5, 650, TEXT)
                    d.text(QX + 58, yy + 37, fit(sub, 11, 260), 11, 400, TEXT_3)
                    f_, c_, bar = st[state]
                    chip(d, QX + 330, yy + 14, state, f_, c_, h=20, size=11, dot=bar)
                    progress(d, QX + 450, yy + 16, 300, frac, bar)
                    pc = BAD_TEXT if state == "failed" else (WARN_TEXT if "problem" in ptxt else TEXT_2)
                    if "problem" in ptxt:
                        icon(d, "alert", QX + 450, yy + 26, 13, WARN_TEXT, 1.5)
                        d.text(QX + 468, yy + 37, ptxt, 11.5, 500, pc)
                    else:
                        d.text(QX + 450, yy + 38, ptxt, 11.5, 500 if state == "failed" else 400, pc)
                    d.text(QX + 762, yy + 24, "%d%%" % round(frac * 100), 11.5, 600, TEXT_2)
                    ax = QX + QW - 16
                    for a_ in reversed(acts):
                        ax -= 28
                        icon_button(d, ax, yy + 10, a_, 28, color=BAD_TEXT if a_ in ("trash",) else TEXT_2,
                                    label="Button · %s %s" % ({"pause": "Pause", "x": "Cancel", "play": "Resume",
                                                               "refresh": "Retry", "trash": "Remove",
                                                               "folder": "Open folder"}[a_], alb))
                        ax -= 6
                    if i < 4:
                        d.hline(QX + 16, QX + QW - 16, yy + 48, BORDER_SOFT)

    activity_strip(d, 200, [("Download · Kind of Blue", .40, "running"), ("Tagging · Geogaddi", .96, "running"),
                            ("Rescan library", 1, "done")])


SHOW_QUICK_TEST = True   # round 3+ drops the quick test


def screen_A4(d):
    chrome(d, "Option A · Workspace · Settings")
    sidebar_a(d, "Settings")
    with d.layer("Settings"):
        d.text(232, 88, "Settings", 24, 700, TEXT)
        d.text(232, 110, "Changes are saved automatically.", 13, 400, TEXT_2)
        CWD = 392
        xs = [216, 216 + CWD + 16, 216 + 2 * (CWD + 16)]

        def row_label(x, y, t, sub=None):
            d.text(x, y, t, 13, 600, TEXT)
            if sub:
                d.text(x, y + 17, sub, 11.5, 400, TEXT_3)

        def stepper(x, y, val):
            with d.group("Stepper · %s" % val):
                d.box(x, y, 104, 30, fill=WHITE, stroke=BORDER, rx=8)
                icon(d, "minus", x + 8, y + 7, 16, TEXT_2)
                d.text(x + 52, y + 20, val, 13, 600, TEXT, anchor="middle")
                icon(d, "plus", x + 80, y + 7, 16, TEXT_2)
                d.line(x + 32.5, y + 1, x + 32.5, y + 29, stroke=BORDER)
                d.line(x + 72.5, y + 1, x + 72.5, y + 29, stroke=BORDER)

        # column 1
        x = xs[0]
        with d.group("Card · Library & files"):
            card(d, x, 132, CWD, 352, "Library & files")
            row_label(x + 16, 182, "Library folder")
            field(d, x + 16, 192, CWD - 120, "D:\\Music", icon_="folder", label="Field · Library folder")
            button(d, x + CWD - 16, 192, "Browse…", "secondary", h=34, anchor="end")
            d.text(x + 16, 246, "Layout: Artist/Album (Year)/NN - Title.flac (Volumio)", 11.5, 400, TEXT_3)
            d.hline(x + 16, x + CWD - 16, 262, BORDER_SOFT)
            row_label(x + 16, 288, "Rip files", "Keep these next to the audio")
            checkbox(d, x + 16, 318, True, "Keep .log files", 12.5)
            checkbox(d, x + 16, 346, False, "Keep .cue files", 12.5)
            chip(d, x + 150, 344, "Volumio may list albums twice", WARN_TINT, WARN_TEXT, h=20, size=10.5, icon_="alert", rx=5)
            checkbox(d, x + 16, 374, True, "Keep CD scans in a “scans” folder", 12.5)
            d.hline(x + 16, x + CWD - 16, 404, BORDER_SOFT)
            row_label(x + 16, 430, "Cover art", "Used when saving an album")
            field(d, x + 196, 420, CWD - 212, "Prefer Cover Art Archive", h=30, size=12, chevron=True,
                  label="Dropdown · Cover art")
        with d.group("Card · Downloads & cache"):
            card(d, x, 496, CWD, 356, "Downloads & cache")
            row_label(x + 16, 546, "Simultaneous downloads")
            stepper(x + CWD - 120, 532, "2")
            d.hline(x + 16, x + CWD - 16, 576, BORDER_SOFT)
            row_label(x + 16, 602, "Cache size", "Deep-test downloads are reused")
            progress(d, x + 16, 640, CWD - 32, 0.32, PRIMARY, h=8)
            d.text(x + 16, 666, "3.2 GB used of 10 GB", 12, 500, TEXT_2)
            field(d, x + CWD - 136, 590, 120, "10 GB", h=30, size=12, chevron=True, label="Dropdown · Cache limit")
            button(d, x + CWD - 16, 652, "Clear cache", "danger_outline", h=28, size=12, icon_="trash", anchor="end")
            d.hline(x + 16, x + CWD - 16, 700, BORDER_SOFT)
            row_label(x + 16, 726, "When a download finishes")
            checkbox(d, x + 16, 744, True, "Fix tags with MusicBrainz first", 12.5)
            checkbox(d, x + 16, 772, True, "Re-scan the library", 12.5)
            checkbox(d, x + 16, 800, False, "Find CD art automatically", 12.5)

        # column 2
        x = xs[1]
        with d.group("Card · Search & tests"):
            card(d, x, 132, CWD, 420 if SHOW_QUICK_TEST else 362, "Search & tests")
            if SHOW_QUICK_TEST:
                row_label(x + 16, 182, "Quick-test count", "Uploads tested after each search")
                stepper(x + CWD - 120, 170, "3")
                d.hline(x + 16, x + CWD - 16, 214, BORDER_SOFT)
        with d.group("Search & tests rows", transform=None if SHOW_QUICK_TEST else "translate(0,-58)"):
            row_label(x + 16, 240, "Search options")
            checkbox(d, x + 16, 258, True, "Show near misses in search notes", 12.5)
            checkbox(d, x + 16, 286, True, "Report other lossless formats (ALAC, APE)", 12.5)
            checkbox(d, x + 16, 314, False, "Include uploads without a year", 12.5)
            field(d, x + CWD - 160, 228, 144, "Quality", prefix="Sort", h=30, size=12, chevron=True,
                  label="Dropdown · Default sort")
            d.hline(x + 16, x + CWD - 16, 344, BORDER_SOFT)
            row_label(x + 16, 370, "Auto MusicBrainz", "Apply the best match above 95")
            toggle(d, x + CWD - 48, 360, True)
            d.hline(x + 16, x + CWD - 16, 404, BORDER_SOFT)
            row_label(x + 16, 430, "Deep test", "Spectrum cutoff thresholds")
            field(d, x + CWD - 160, 420, 144, "19.5 kHz", prefix="Suspect <", h=30, size=12,
                  label="Field · Suspect threshold")
            field(d, x + CWD - 160, 458, 144, "17 kHz", prefix="Lossy <", h=30, size=12,
                  label="Field · Lossy threshold")
            d.text(x + 16, 512, "Uploads above 20 kHz are marked PASS.", 11.5, 400, TEXT_3)
        with d.group("Card · Tools", transform=None if SHOW_QUICK_TEST else "translate(0,-58)"):
            card(d, x, 564, CWD, 288, "Tools")
            button(d, x + CWD - 16, 572, "Check again", "secondary", h=28, size=12, icon_="refresh", anchor="end")
            tools = [("flac", "1.4.3", True, "decoding and tag writing"), ("ffmpeg", "7.1", True, "MP3/WAV, spectrum"),
                     ("metaflac", "1.4.3", True, "tags and pictures"), ("Ghostscript", "–", "warn", "optional · PDF preview")]
            for i, (n, v, ok, sub) in enumerate(tools):
                yy = 612 + i * 56
                with d.group("Tool · %s" % n):
                    status_icon(d, x + 16, yy + 6, ok, 18)
                    d.text(x + 44, yy + 15, n, 13, 650, TEXT)
                    d.text(x + 44, yy + 32, sub, 11.5, 400, TEXT_3)
                    if ok is True:
                        chip(d, x + CWD - 16, yy + 4, v + " · found", GOOD_TINT, GOOD_TEXT, h=20, size=11, anchor="end")
                    else:
                        chip(d, x + CWD - 16, yy + 4, "not installed", WARN_TINT, WARN_TEXT, h=20, size=11, anchor="end")
                    if i < 3:
                        d.hline(x + 16, x + CWD - 16, yy + 46, BORDER_SOFT)

        # column 3
        x = xs[2]
        with d.group("Card · CD art & print"):
            card(d, x, 132, CWD, 720, "CD art & print")
            section_label(d, x + 16, 178, "Art options")
            row_label(x + 16, 206, "Minimum resolution", "Warn below this")
            field(d, x + CWD - 136, 194, 120, "200 dpi", h=30, size=12, chevron=True, label="Dropdown · Min dpi")
            row_label(x + 16, 254, "Search other pressings")
            toggle(d, x + CWD - 48, 242, True)
            row_label(x + 16, 290, "Allow generated pieces", "e.g. spines from the front cover")
            toggle(d, x + CWD - 48, 280, True)
            d.hline(x + 16, x + CWD - 16, 324, BORDER_SOFT)
            section_label(d, x + 16, 350, "CD print")
            row_label(x + 16, 382, "Paper")
            segmented(d, x + CWD - 16 - 146, 366, ["A4", "Letter"], "A4", widths=[60, 80], label="Paper")
            row_label(x + 16, 426, "Bleed")
            field(d, x + CWD - 136, 410, 120, "3 mm", h=30, size=12, chevron=True, label="Dropdown · Bleed")
            row_label(x + 16, 470, "Disc hole", "Cut line for the centre hole")
            segmented(d, x + CWD - 16 - 196, 456, ["15 mm", "41 mm", "None"], "15 mm", widths=[64, 64, 62],
                      label="Disc hole")
            row_label(x + 16, 518, "Crop marks")
            toggle(d, x + CWD - 48, 506, True)
            d.hline(x + 16, x + CWD - 16, 544, BORDER_SOFT)
            row_label(x + 16, 570, "Output folder")
            field(d, x + 16, 582, CWD - 32, "D:\\Music\\_CD prints", icon_="folder", label="Field · Output folder")
            checkbox(d, x + 16, 630, True, "Also copy the PDF into the album folder", 12.5)
            with d.group("Print size note"):
                d.box(x + 16, 668, CWD - 32, 72, fill=SURFACE_ALT, stroke=BORDER, rx=10)
                icon(d, "info", x + 28, 680, 15, PRIMARY)
                d.text(x + 50, 692, "Print at 100 % (actual size).", 12, 600, TEXT)
                d.text(x + 50, 710, "Front 120 × 120 mm · tray card 151 × 118 mm", 11.5, 400, TEXT_2)
                d.text(x + 50, 726, "Disc label Ø 117 mm", 11.5, 400, TEXT_2)
            button(d, x + 16, 800, "Print a test page", "secondary", h=34, icon_="printer")
    activity_strip(d, 200, [("Idle", 1, "done")])


# --------------------------------------------------------------------------
# Option B · Guided steps
# --------------------------------------------------------------------------


def header_b(d, active):
    with d.layer("Navigation"):
        with d.group("Top navigation"):
            d.rect(0, 36, W, 56, fill=WHITE)
            d.hline(0, W, 91, BORDER)
            x = 24
            for name, ic in (("Album Search", "search"), ("CD Art", "disc"), ("Library", "library"),
                             ("Settings", "settings")):
                w = tw(name, 13.5, 600) + 48
                with d.group("Nav · %s" % name):
                    if name == active:
                        d.rect(x, 48, w, 32, fill=PRIMARY, rx=9)
                    icon(d, ic, x + 14, 56, 16, WHITE if name == active else TEXT_2, 1.6)
                    d.text(x + 38, 69, name, 13.5, 600, WHITE if name == active else TEXT_2)
                x += w + 6
            button(d, W - 24, 48, "Queue", "secondary", h=32, icon_="queue", anchor="end")
            chip(d, W - 34, 41, "2", AMBER, TEXT, h=16, size=10, pad=5)


def stepper_b(d, steps, active_idx, y=108):
    with d.layer("Stepper"):
        with d.group("Stepper"):
            n = len(steps)
            span = 260 if n == 4 else 300
            total = span * (n - 1)
            x0 = (W - total) / 2 - 60
            for i, (name, sub) in enumerate(steps):
                cx = x0 + i * span
                done, on = i < active_idx, i == active_idx
                with d.group("Step %d · %s" % (i + 1, name)):
                    if done:
                        d.circle(cx + 15, y + 16, 15, fill=GOOD)
                        icon(d, "check", cx + 6, y + 7, 18, WHITE, 2.2)
                    elif on:
                        d.circle(cx + 15, y + 16, 18, fill=PRIMARY_TINT_2)
                        d.circle(cx + 15, y + 16, 15, fill=PRIMARY)
                        d.text(cx + 15, y + 21, str(i + 1), 13, 700, WHITE, anchor="middle")
                    else:
                        d.circle(cx + 15, y + 16, 14.5, fill=WHITE, stroke=BORDER)
                        d.text(cx + 15, y + 21, str(i + 1), 13, 700, TEXT_3, anchor="middle")
                    d.text(cx + 40, y + 13, name, 14, 650, TEXT if (on or done) else TEXT_2)
                    d.text(cx + 40, y + 31, sub, 11.5, 400, TEXT_3)
                if i < n - 1:
                    lx = cx + 48 + max(tw(name, 14, 650), tw(sub, 11.5)) + 12
                    d.line(lx, y + 16, cx + span - 12, y + 16, stroke=GOOD if done else BORDER, sw=2, cap="round")


def screen_B1(d):
    chrome(d, "Option B · Guided steps · Album Search · 2 Check")
    header_b(d, "Album Search")
    stepper_b(d, [("Find", "7 uploads found"), ("Check", "Test before you save"), ("Tag", "Fix with MusicBrainz"),
                  ("Save", "Add to library")], 1)
    X, Y, CW_, CH = 160, 168, 1120, 684
    with d.layer("Step card"):
        with d.group("Card · Step 2 Check"):
            d.box(X, Y, CW_, CH, fill=WHITE, stroke=BORDER, rx=16)
            cover(d, X + 24, Y + 22, 60, "kob", 8)
            d.text(X + 100, Y + 44, "Check the upload", 20, 700, TEXT)
            d.spans(X + 100, Y + 66, [("Kind of Blue", 13, 600, TEXT), (" · Miles Davis · 1959 · ", 13, 400, TEXT_2),
                                      ("miles-davis-kind-of-blue-1959", 13, 500, PRIMARY)])
            field(d, X + CW_ - 24 - 250, Y + 30, 250, "2 of 7 · FLAC 16/44.1", prefix="Upload", h=34,
                  size=12, chevron=True, label="Dropdown · Change upload")
            bx = X + CW_ - 24 - 250 - 12
            tier_chip(d, bx - 22, Y + 37, "B", 22)
            quality_badge(d, bx - 30, Y + 38, "cd", h=21, anchor="end")
            d.hline(X + 24, X + CW_ - 24, Y + 104, BORDER_SOFT)

    PY = Y + 120
    QX, QW = X + 24, 520
    with d.layer("Quick test"):
        with d.group("Panel · Quick test"):
            d.box(QX, PY, QW, 412, fill=SURFACE_ALT, stroke=BORDER, rx=12)
            d.text(QX + 20, PY + 30, "Quick test", 15, 650, TEXT)
            d.text(QX + 20, PY + 48, "Reads the file headers and the rip log. No full download.", 12, 400, TEXT_3)
            chip(d, QX + QW - 20, PY + 16, "Done · 2 s", GOOD_TINT, GOOD_TEXT, h=22, size=11, icon_="check", anchor="end")
            play_button(d, QX + 20, PY + 66, 34, playing=False)
            track_picker(d, QX + 64, PY + 68, 220, "01 · So What · 9:25")
            d.text(QX + QW - 20, PY + 88, "2:14 / 9:25", 12.5, 600, TEXT_2, anchor="end")
            waveform(d, QX + 20, PY + 122, QW - 40, 96, played=0.24, seed=4, bar=2.4, gap=1.4)
            for i, t in enumerate(("0:00", "2:30", "5:00", "7:30", "9:25")):
                d.text(QX + 20 + (QW - 40) * i / 4, PY + 240, t, 10.5, 500, TEXT_3,
                       anchor="start" if i == 0 else ("end" if i == 4 else "middle"))
            chip(d, QX + 20, PY + 254, "44.1 kHz · 16-bit · stereo · 1,411 kbps", WHITE, TEXT, stroke=BORDER,
                 h=24, size=11.5, rx=6, label="Readout")
            section_label(d, QX + 20, PY + 304, "What the quick test found")
            found = [(True, "6 of 6 file headers OK"), (True, "Rip log: EAC · secure mode · all tracks accurately ripped"),
                     (True, "Cue sheet matches 6 tracks"), ("warn", "Log checksum missing — can't prove the log is unedited")]
            for i, (ok, t) in enumerate(found):
                yy = PY + 318 + i * 22
                status_icon(d, QX + 20, yy, ok, 14)
                d.text(QX + 42, yy + 11, t, 12, 400, TEXT)

    DX, DW = QX + QW + 16, CW_ - 48 - QW - 16
    with d.layer("Deep test"):
        with d.group("Panel · Deep test"):
            d.box(DX, PY, DW, 412, fill=SURFACE_ALT, stroke=BORDER, rx=12)
            d.text(DX + 20, PY + 30, "Deep test", 15, 650, TEXT)
            d.text(DX + 20, PY + 48, "Downloads 380 MB and decodes every track.", 12, 400, TEXT_3)
            chip(d, DX + DW - 20, PY + 16, "Done · 6 / 6 tracks", GOOD_TINT, GOOD_TEXT, h=22, size=11,
                 icon_="check", anchor="end")
            play_button(d, DX + 20, PY + 66, 34, playing=True)
            track_picker(d, DX + 64, PY + 68, 220, "04 · All Blues · 11:33")
            d.text(DX + DW - 20, PY + 88, "3:58 / 11:33", 12.5, 600, TEXT_2, anchor="end")
            spectrogram(d, DX + 44, PY + 116, DW - 64, 150, cutoff=21.8, seed=21, cols=130, rows=44, playhead=0.34)
            for i, t in enumerate(("0:00", "5:46", "11:33")):
                d.text(DX + 44 + (DW - 64) * i / 2, PY + 282, t, 10.5, 500, TEXT_3,
                       anchor="start" if i == 0 else ("end" if i == 2 else "middle"))
            verdict_chip(d, DX + 20, PY + 296, "PASS", "21.8", h=26, size=12)
            d.text(DX + 160, PY + 313, "Full-band content up to 21.8 kHz — looks like a real CD rip.", 12, 400, TEXT)
            section_label(d, DX + 20, PY + 350, "Album spectrum · cutoff per track")
            vals = [21.8, 21.8, 21.7, 21.8, 21.9, 21.6]
            bw_ = (DW - 40 - 5 * 8) / 6
            for i, v in enumerate(vals):
                bx = DX + 20 + i * (bw_ + 8)
                with d.group("Track %d spectrum" % (i + 1)):
                    d.rect(bx, PY + 362, bw_, 10, fill=SURFACE_SUNK, rx=5)
                    d.rect(bx, PY + 362, bw_ * v / 22.05, 10, fill=GOOD, rx=5)
                    d.text(bx, PY + 392, "%d · %.1f kHz" % (i + 1, v), 10.5, 600, TEXT_2)

    with d.layer("Verdict"):
        with d.group("Verdict summary"):
            vy = PY + 428
            d.box(X + 24, vy, CW_ - 48, 50, fill=GOOD_TINT, stroke=None, rx=10)
            status_icon(d, X + 40, vy + 16, True, 18)
            d.spans(X + 68, vy + 30, [("Tier B · Lossless · CD rip", 13.5, 700, GOOD_TEXT),
                                      ("  —  safe to keep. 4 points for, 1 against (log checksum missing).", 13, 400, TEXT)])
            button(d, X + CW_ - 36, vy + 10, "See evidence", "ghost", h=30, size=12.5, icon_right="chev_right", anchor="end")

    with d.layer("Step actions"):
        fy = Y + CH - 56
        d.hline(X + 24, X + CW_ - 24, fy - 12, BORDER_SOFT)
        button(d, X + 24, fy, "Back: Find", "secondary", h=36, icon_="chev_left")
        button(d, X + CW_ - 24, fy, "Next: Tag", "primary", w=140, h=36, icon_right="chev_right", anchor="end")
        button(d, X + CW_ - 180, fy, "Skip tests", "ghost", h=36, anchor="end")
        d.text(X + CW_ / 2, fy + 23, "Step 2 of 4", 12, 500, TEXT_3, anchor="middle")
    activity_strip(d, 0, [("Deep test · Kind of Blue", 1, "done"), ("Download · Geogaddi", .43, "running")])


def screen_B2(d):
    chrome(d, "Option B · Guided steps · CD Art · 2 Choose scans")
    header_b(d, "CD Art")
    stepper_b(d, [("Pick album", "Kind of Blue"), ("Choose scans", "One per part"), ("Print", "True-size PDF")], 1)
    X, Y, CW_, CH = 160, 168, 1120, 684
    with d.layer("Step card"):
        with d.group("Card · Step 2 Choose scans"):
            d.box(X, Y, CW_, CH, fill=WHITE, stroke=BORDER, rx=16)
            cover(d, X + 24, Y + 22, 60, "kob", 8)
            d.text(X + 100, Y + 44, "Choose scans", 20, 700, TEXT)
            d.text(X + 100, Y + 66, "Matched Columbia CK 64935 · 1997 · 7 pressings searched · click a thumbnail to use it",
                   13, 400, TEXT_2)
            button(d, X + CW_ - 24, Y + 32, "Find art again", "secondary", h=32, icon_="refresh", anchor="end")
            d.hline(X + 24, X + CW_ - 24, Y + 104, BORDER_SOFT)

    LX, LW = X + 24, 660
    with d.layer("Scan parts"):
        info = [("Cover Art Archive · 1997 XE CD", "595 dpi", False, [595, 300, 400, 150]),
                ("Cover Art Archive · 1997 XE CD", "410 dpi", False, [410, 300]),
                ("Cover Art Archive · 2009 Legacy", "300 dpi", False, [300, 240, 180]),
                ("archive.org upload · tray.jpg", "240 dpi", False, [240]),
                ("archive.org upload · cd.jpg", "150 dpi", True, [150, 120])]
        for i, ((pk, pn), (src, dpi, warn, alts)) in enumerate(zip(PARTS, info)):
            yy = Y + 120 + i * 92
            with d.group("Part · %s" % pn):
                d.box(LX, yy, LW, 84, fill=WARN_TINT if warn else SURFACE_ALT, stroke=WARN_LINE if warn else BORDER, rx=12)
                d.text(LX + 16, yy + 26, pn, 14, 650, TEXT)
                d.text(LX + 16, yy + 45, fit(src, 11.5, 190), 11.5, 400, TEXT_2)
                if warn:
                    chip(d, LX + 16, yy + 54, dpi + " · soft", AMBER, TEXT, h=20, size=10.5, icon_="alert", rx=5)
                else:
                    chip(d, LX + 16, yy + 54, dpi, GOOD_TINT, GOOD_TEXT, h=20, size=10.5, rx=5)
                ax = LX + 230
                for k, dp in enumerate(alts):
                    wide = pk in ("back", "tray")
                    aw, ah = (78, 61) if wide else (64, 64)
                    ty = yy + (84 - ah) / 2
                    sel = k == 0
                    if sel:
                        d.box(ax - 3, ty - 3, aw + 6, ah + 6, fill=None, stroke=PRIMARY, rx=7, sw=2)
                    scan(d, ax, ty, aw, ah, pk, k, rx=4, soft=dp < 200)
                    if not sel:
                        d.box(ax, ty, aw, ah, fill=None, stroke=BORDER, rx=4)
                    if sel:
                        d.circle(ax + aw - 2, ty + 2, 8, fill=PRIMARY, stroke=WHITE, sw=1.5)
                        icon(d, "check", ax + aw - 8, ty - 4, 12, WHITE, 2)
                    lab = "%d dpi" % dp
                    lw = tw(lab, 9.5, 700) + 8
                    d.rect(ax + 3, ty + ah - 17, lw, 14, fill=AMBER if dp < 200 else mix(INK, BLACK, .2), rx=3,
                           opacity=None if dp < 200 else .8)
                    d.text(ax + 7, ty + ah - 7, lab, 9.5, 700, TEXT if dp < 200 else WHITE)
                    ax += aw + 14
                d.text(ax + 4, yy + 47, "+ search more", 11.5, 600, PRIMARY)

    RX, RW = LX + LW + 16, CW_ - 48 - LW - 16
    with d.layer("Case preview"):
        with d.group("Panel · Live preview"):
            d.box(RX, Y + 120, RW, 452, fill=WHITE, stroke=BORDER, rx=12)
            d.text(RX + 16, Y + 146, "Live preview", 14, 650, TEXT)
            segmented(d, RX + RW - 16 - 196, Y + 130, ["Outside", "Inside", "Disc"], "Inside", h=28,
                      widths=[70, 60, 60], label="Preview page")
            case_page_inside(d, RX + 16, Y + 170, RW - 32, 330)
            chip(d, RX + 16, Y + 512, "Insert from 2009 pressing", WARN_TINT, WARN_TEXT, h=22, size=11, icon_="alert", rx=6)
            chip(d, RX + 16, Y + 540, "Disc label soft at 150 dpi", WARN_TINT, WARN_TEXT, h=22, size=11, icon_="alert", rx=6)

    with d.layer("Step actions"):
        fy = Y + CH - 56
        d.hline(X + 24, X + CW_ - 24, fy - 12, BORDER_SOFT)
        button(d, X + 24, fy, "Back: Pick album", "secondary", h=36, icon_="chev_left")
        button(d, X + CW_ - 24, fy, "Next: Print", "primary", w=150, h=36, icon_right="chev_right", anchor="end")
        d.text(X + CW_ / 2, fy + 23, "Step 2 of 3 · 5 of 5 parts chosen", 12, 500, TEXT_3, anchor="middle")
    activity_strip(d, 0, [("Find art · Kind of Blue", 1, "done")])


# --------------------------------------------------------------------------
# Option C · Library first
# --------------------------------------------------------------------------


def home_c(d, query=None, focused=False):
    with d.layer("Navigation"):
        with d.group("Command bar header"):
            d.rect(0, 36, W, 64, fill=WHITE)
            d.hline(0, W, 99, BORDER)
            d.text(32, 75, "Library", 20, 700, TEXT)
            bx, bw = 380, 680
            with d.group("Command bar · Search archive.org"):
                if focused:
                    d.box(bx - 3, 45, bw + 6, 46, fill=None, stroke=PRIMARY_TINT_2, rx=13, sw=3)
                d.box(bx, 48, bw, 40, fill=SURFACE_ALT if not focused else WHITE, stroke=PRIMARY if focused else BORDER, rx=10)
                icon(d, "search", bx + 14, 60, 16, PRIMARY if focused else TEXT_3, 1.7)
                if query:
                    d.text(bx + 40, 73, query, 14, 500, TEXT)
                    cxe = bx + 40 + tw(query, 14, 500) * 0.94 + 2
                    d.line(cxe, 58, cxe, 78, stroke=PRIMARY, sw=1.5)
                else:
                    d.text(bx + 40, 73, "Search archive.org…   artist, album", 14, 400, TEXT_3)
                chip(d, bx + bw - 60, 58, "archive.org", PRIMARY_TINT, PRIMARY_TEXT, h=20, size=10.5, anchor="end", rx=5)
                kbd(d, bx + bw - 52, 58, "Ctrl K")
            ix = W - 32
            for name, badge in (("settings", None), ("disc", None), ("queue", "2")):
                ix -= 36
                icon_button(d, ix, 52, name, 32, label="Icon button · %s" % {"settings": "Settings", "disc": "CD Art checklist",
                                                                             "queue": "Download queue"}[name])
                if badge:
                    chip(d, ix + 22, 46, badge, AMBER, TEXT, h=16, size=10, pad=5)
                ix -= 4

    with d.layer("Library grid"):
        d.text(32, 136, "5 albums · 2.4 GB · D:\\Music", 13, 500, TEXT_2)
        cx = 250
        for lab, on in (("All", True), ("Lossless", False), ("Needs art", False), ("Tag issues", False)):
            cx += chip(d, cx, 120, lab, PRIMARY if on else WHITE, WHITE if on else TEXT_2,
                       stroke=None if on else BORDER, h=26, size=12, pad=12) + 6
        button(d, W - 32, 118, "Rescan", "secondary", h=30, size=12, icon_="refresh", anchor="end")
        field(d, W - 140 - 150, 118, 150, "Recently added", prefix="Sort", h=30, size=12, chevron=True,
              label="Dropdown · Sort library")
        cw, gap, top = 256, 24, 164
        for i, a in enumerate(LIBRARY):
            x = 32 + i * (cw + gap)
            with d.group("Album tile · %s" % a["album"]):
                d.box(x, top, cw, 340, fill=WHITE, stroke=BORDER, rx=12)
                if a["cover"]:
                    cover(d, x + 12, top + 12, cw - 24, a["key"], 8)
                else:
                    d.box(x + 12, top + 12, cw - 24, cw - 24, fill=SURFACE_SUNK, stroke=BORDER, rx=8, dash="4 3")
                    icon(d, "image", x + cw / 2 - 14, top + 12 + (cw - 24) / 2 - 14, 28, TEXT_3)
                    d.text(x + cw / 2, top + 12 + (cw - 24) / 2 + 34, "No cover", 12, 500, TEXT_3, anchor="middle")
                quality_badge(d, x + 22, top + 22, a["cls"], h=20, size=10.5)
                d.text(x + 14, top + cw + 14, fit(a["album"], 14, cw - 28, 650), 14, 650, TEXT)
                d.text(x + 14, top + cw + 33, "%s · %s" % (a["artist"], a["year"]), 12, 400, TEXT_2)
                d.text(x + 14, top + cw + 60, a["fmt"], 11, 500, TEXT_3)
                art_dots(d, x + cw - 14 - 55, top + cw + 56, a["art"])
                if a["issues"]:
                    chip(d, x + cw - 22, top + 22, "%d" % a["issues"], WARN_TINT, WARN_TEXT, h=20, size=10.5,
                         icon_="alert", anchor="end", pad=6)
        d.text(32, 534, "Art dots: front · back · inside · under-tray · disc", 11.5, 400, TEXT_3)
    with d.layer("Needs attention"):
        with d.group("Card · Needs attention"):
            card(d, 32, 552, W - 64, 300, "Needs attention", "3 albums")
            rows = [("okc", "OK Computer", "2 tag issues · .cue file may make Volumio list it twice", "Fix tags"),
                    ("sol", "When I Get Home", "No cover · CD art missing · 1 tag issue", "Find art"),
                    ("tanox", "Astronomía de Balcón", "Lossy (MP3 VBR ~245 kbps) · a lossless copy may exist", "Find upgrade"),
                    ("kob", "Kind of Blue", "Disc label scan is soft (150 dpi)", "Choose scans")]
            for i, (k, alb, t, act) in enumerate(rows):
                yy = 596 + i * 60
                cover(d, 48, yy + 8, 40, k, 5)
                d.text(100, yy + 24, alb, 13.5, 650, TEXT)
                d.text(100, yy + 42, t, 12, 400, TEXT_2)
                button(d, W - 48, yy + 12, act, "secondary", h=30, size=12, anchor="end")
                if i < 3:
                    d.hline(48, W - 48, yy + 60, BORDER_SOFT)


def scrim(d, y0=100, y1=868):
    with d.layer("Scrim"):
        d.rect(0, y0, W, y1 - y0, fill=SCRIM, opacity=0.38, label="Scrim")


def screen_C1(d):
    chrome(d, "Option C · Library first · Home + search results")
    home_c(d, "miles davis kind of blue", focused=True)
    scrim(d)
    PX, PW = 832, W - 832
    with d.layer("Search results slide-over"):
        with d.group("Slide-over · archive.org results"):
            d.rect(PX, 100, PW, 768, fill=WHITE)
            d.line(PX + .5, 100, PX + .5, 868, stroke=BORDER)
            d.text(PX + 24, 134, "archive.org results", 18, 700, TEXT)
            d.text(PX + 24, 154, "Artist “Miles Davis” · album “Kind of Blue” · 7 uploads", 12.5, 400, TEXT_2)
            icon_button(d, PX + PW - 56, 114, "x", 32, label="Button · Close slide-over")
            cx = PX + 24
            for f in ("FLAC", "WAV", "MP3"):
                cx += filter_chip(d, cx, 172, f, True) + 6
            cx += 8
            cx += filter_chip(d, cx, 172, "Lossless only", False) + 6
            filter_chip(d, cx, 172, "Hide albums I already have", False)
            segmented(d, PX + 24, 208, ["Quality", "Match", "Popularity"], "Quality", h=28, label="Sort")
            d.text(PX + PW - 24, 227, "best quality first", 11.5, 400, TEXT_3, anchor="end")
            with d.group("Search notes (inline)"):
                d.box(PX + 24, 246, PW - 48, 30, fill=SURFACE_ALT, stroke=BORDER, rx=8)
                icon(d, "info", PX + 34, 253, 15, PRIMARY)
                d.text(PX + 56, 265, "2 near misses · 1 ALAC + 1 APE also found · 3 left out (no audio, stream only)",
                       11.5, 400, TEXT_2)
            y = 288
            RW_ = PW - 40
            for i, r in enumerate(RESULTS):
                sel = r.get("sel", False)
                hh = 62 if not sel else 104
                if sel:
                    d.box(PX + 18, y, RW_ + 4, hh, fill=PRIMARY_TINT, stroke=PRIMARY_TINT_2, rx=10)
                result_row(d, PX + 20, y, RW_, r, 62, False, i == len(RESULTS) - 1 or sel or RESULTS[min(i + 1, 6)].get("sel"))
                if sel:
                    with d.group("Result actions"):
                        bx = PX + 32
                        bx += button(d, bx, y + 64, "Quick test", "secondary", h=30, size=12, icon_="wave") + 8
                        bx += button(d, bx, y + 64, "Deep test · 380 MB", "secondary", h=30, size=12, icon_="spectrum") + 8
                        button(d, PX + PW - 32, y + 64, "Open & save", "primary", h=30, size=12, icon_right="chev_right",
                               anchor="end")
                y += hh + 2
    activity_strip(d, 0, [("Quick test · 7 uploads", .71, "running"), ("Download · Geogaddi", .43, "running")])


def screen_C2(d):
    chrome(d, "Option C · Library first · Album sheet · CD Art")
    home_c(d)
    scrim(d, 36, 868)
    SX, SW = 640, W - 640
    with d.layer("Album side sheet"):
        with d.group("Side sheet · Kind of Blue"):
            d.rect(SX, 36, SW, 832, fill=WHITE)
            d.line(SX + .5, 36, SX + .5, 868, stroke=BORDER)
            cover(d, SX + 24, 56, 84, "kob", 8)
            d.text(SX + 124, 82, "Kind of Blue", 22, 700, TEXT)
            d.text(SX + 124, 103, "Miles Davis · 1959 · Columbia · 6 tracks · 55:22", 13, 400, TEXT_2)
            cx = SX + 124
            cx += quality_badge(d, cx, 114, "cd", h=21) + 6
            cx += tier_chip(d, cx, 114, "B", 21) + 6
            cx += chip(d, cx, 114, "FLAC 16/44.1", SURFACE_SUNK, TEXT_2, h=21, size=11, rx=6) + 12
            art_dots(d, cx, 124.5, [1, 1, 2, 1, 2])
            icon_button(d, SX + SW - 56, 52, "x", 32, label="Button · Close sheet")
            icon_button(d, SX + SW - 96, 52, "folder", 32, label="Button · Open album folder")
            tabs(d, SX + 24, 150, ["Tracks", "Upgrade", "CD Art"], "CD Art", SW - 48, size=13.5, counts={"Upgrade": "1"})

    LX, LW = SX + 24, 384
    with d.layer("CD Art tab"):
        with d.group("Find art"):
            button(d, LX, 204, "Find art", "primary", h=32, icon_="search")
            d.text(LX + 104, 216, "Done · 7 pressings searched", 11.5, 600, GOOD_TEXT)
            progress(d, LX + 104, 224, LW - 104, 1, GOOD, h=5)
            d.box(LX, 246, LW, 62, fill=SURFACE_ALT, stroke=BORDER, rx=10)
            d.spans(LX + 14, 266, [("Matched ", 11.5, 400, TEXT_2), ("Columbia CK 64935 · 1997 · US CD", 11.5, 600, TEXT)])
            d.spans(LX + 14, 283, [("Searched ", 11.5, 400, TEXT_2), ("1997 XE CD, 1999 Legacy, 2009 Legacy +4", 11.5, 500, TEXT)])
            d.spans(LX + 14, 300, [("Not found ", 11.5, 400, WARN_TEXT), ("inside of insert (1997) → 2009 scan", 11.5, 500, TEXT)])
        info = [("Cover Art Archive · 1997 XE CD", "595 dpi", False, 3), ("Cover Art Archive · 1997 XE CD", "410 dpi", False, 1),
                ("Cover Art Archive · 2009 Legacy", "300 dpi", False, 2), ("archive.org upload", "240 dpi", False, 0),
                ("archive.org upload", "150 dpi", True, 1)]
        for i, ((pk, pn), (src, dpi, warn, nalt)) in enumerate(zip(PARTS, info)):
            yy = 322 + i * 86
            with d.group("Scan row · %s" % pn):
                d.box(LX, yy, LW, 78, fill=WARN_TINT if warn else WHITE, stroke=WARN_LINE if warn else BORDER, rx=10)
                wide = pk in ("back", "tray")
                tw_, th = (70, 55) if wide else (58, 58)
                d.box(LX + 9, yy + (78 - th) / 2 - 3, tw_ + 6, th + 6, fill=None, stroke=PRIMARY, rx=6, sw=2)
                scan(d, LX + 12, yy + (78 - th) / 2, tw_, th, pk, 0, rx=4, soft=warn)
                tx = LX + 96
                d.text(tx, yy + 26, pn, 13, 650, TEXT)
                d.text(tx, yy + 44, fit(src, 11, 150), 11, 400, TEXT_2)
                if warn:
                    chip(d, tx, yy + 52, dpi + " · soft", AMBER, TEXT, h=17, size=9.5, pad=6, rx=4, icon_="alert")
                else:
                    chip(d, tx, yy + 52, dpi, GOOD_TINT, GOOD_TEXT, h=17, size=9.5, pad=6, rx=4)
                ax = LX + LW - 12
                if nalt == 0:
                    d.text(ax, yy + 44, "no alternatives", 11, 500, TEXT_3, anchor="end")
                for k in range(nalt):
                    aw, ah = (40, 31) if wide else (32, 32)
                    ax -= aw
                    scan(d, ax, yy + 39 - ah / 2, aw, ah, pk, k + 1, rx=3)
                    d.box(ax, yy + 39 - ah / 2, aw, ah, fill=None, stroke=BORDER, rx=3)
                    ax -= 6
                if nalt:
                    d.text(LX + LW - 12, yy + 20, "alternatives", 9.5, 700, TEXT_3, anchor="end", ls=.4)
        d.text(LX, 772, "Chosen scans are saved as scans/front.jpg, back.jpg … in the album folder.", 11, 400, TEXT_3)

    RX, RW = LX + LW + 24, SW - 48 - LW - 24
    with d.layer("Case preview"):
        with d.group("Case preview"):
            d.text(RX, 222, "Case preview", 14, 650, TEXT)
            segmented(d, RX + RW - 186, 204, ["Outside", "Inside", "Disc"], "Disc", h=28, widths=[66, 56, 58],
                      label="Preview page")
            case_page_disc(d, RX, 246, RW, 432)
            chip(d, RX, 690, "Disc label is soft (150 dpi)", WARN_TINT, WARN_TEXT, h=22, size=11, icon_="alert", rx=6)
            d.hline(RX, RX + RW, 726, BORDER_SOFT)
            section_label(d, RX, 750, "Print · A4 · 3 mm bleed")
            bx = RX
            bx += button(d, bx, 764, "Make PDF", "primary", h=34, icon_="printer") + 8
            button(d, bx, 764, "Open PDF", "secondary", h=34, icon_="external")
            button(d, RX, 806, "Save scans to album folder", "secondary", w=RW, h=34, icon_="folder")
    activity_strip(d, 0, [("Find art · Kind of Blue", 1, "done")])


# --------------------------------------------------------------------------
# Style guide / cover page
# --------------------------------------------------------------------------


def screen_00(d):
    with d.layer("Background"):
        d.rect(0, 0, W, H, fill=APP_BG, label="Page background")
    with d.layer("Header"):
        icon(d, "disc", 40, 36, 28, PRIMARY, 2)
        d.text(80, 60, "iaflac", 30, 800, TEXT)
        d.text(180, 60, "UI kit · palette, type, components and three layout options", 15, 400, TEXT_2)
        d.text(W - 40, 60, "Mockups v1 · 1440 × 900 · Inkscape layers", 12, 500, TEXT_3, anchor="end")

    with d.layer("Palette"):
        with d.group("Card · Palette"):
            card(d, 40, 88, 664, 260, "Palette")
            sw = [(LAVENDER, "#DFD9E2", "Background · surfaces · borders"), (INDIGO, "#5C53A2", "Primary · nav · focus"),
                  (GREEN, "#20A76B", "Lossless · PASS · done"), (AMBER, "#F4B73E", "Warning · hi-res · busy"),
                  (RED, "#BC3843", "Lossy · failed · destructive")]
            for i, (c, hx, role) in enumerate(sw):
                x = 56 + i * 128
                with d.group("Swatch · %s" % hx):
                    d.box(x, 128, 116, 68, fill=c, stroke=BORDER if c == LAVENDER else None, rx=10)
                    d.text(x, 216, hx, 13, 700, TEXT)
                    words = role.split(" · ")
                    d.text(x, 232, " · ".join(words[:2]), 10.5, 400, TEXT_2)
                    if len(words) > 2:
                        d.text(x, 246, words[2], 10.5, 400, TEXT_2)
            section_label(d, 56, 274, "Tints & neutrals")
            tints = [(BLACK, "#000000", "Max contrast"), (TEXT, TEXT, "Text"), (TEXT_2, TEXT_2, "Text 2"),
                     (TEXT_3, TEXT_3, "Muted"), (WHITE, "#FFFFFF", "Cards"), (PRIMARY_TINT, PRIMARY_TINT, "Selection"),
                     (GOOD_TINT, GOOD_TINT, "Good tint"), (WARN_TINT, WARN_TINT, "Warn tint"), (BAD_TINT, BAD_TINT, "Bad tint"),
                     (SIDEBAR, SIDEBAR, "Sidebar")]
            for i, (c, hx, n) in enumerate(tints):
                x = 56 + (i % 5) * 128
                yy = 284 + (i // 5) * 30
                d.box(x, yy, 22, 22, fill=c, stroke=BORDER, rx=6)
                d.text(x + 30, yy + 10, n, 10, 600, TEXT_2)
                d.text(x + 30, yy + 21, hx, 9, 400, TEXT_3)

    with d.layer("Type scale"):
        with d.group("Card · Type scale"):
            card(d, 720, 88, 680, 260, "Type scale", "Inter → Segoe UI → Helvetica Neue → Arial")
            samples = [(28, 700, "Display 28 · Kind of Blue"), (20, 700, "Title 20 · Check the upload"),
                       (15, 650, "Heading 15 · Quick test"), (13, 400, "Body 13 · Reads file headers and the rip log."),
                       (12, 400, "Small 12 · archive.org/details/miles-davis-kind-of-blue-1959"),
                       (10.5, 700, "CAPTION 10.5 · MUSICBRAINZ MATCHES")]
            y = 160
            for sz, wt, t in samples:
                d.text(736, y, t, sz, wt, TEXT if sz > 11 else TEXT_3, ls=.7 if sz < 11 else None)
                y += sz + 13

    with d.layer("Components"):
        with d.group("Card · Components"):
            card(d, 40, 364, 1360, 292, "Components")
            # buttons
            section_label(d, 56, 410, "Buttons")
            bx = 56
            bx += button(d, bx, 422, "Search", "primary") + 8
            bx += button(d, bx, 422, "Run again", "secondary", icon_="refresh") + 8
            button(d, bx, 422, "Cancel", "destructive")
            bx = 56
            bx += button(d, bx, 464, "Deep test · 380 MB", "primary", icon_="spectrum") + 8
            bx += button(d, bx, 464, "Clear cache", "danger_outline", icon_="trash") + 8
            button(d, 246, 508, "See evidence", "ghost", icon_right="chev_right")
            play_button(d, 56, 508, 32)
            play_button(d, 96, 508, 32, playing=True)
            icon_button(d, 136, 510, "x", 28)
            icon_button(d, 170, 510, "folder", 28)
            icon_button(d, 204, 510, "refresh", 28)
            section_label(d, 56, 568, "Icons")
            for i, n in enumerate(["search", "disc", "library", "settings", "folder", "download", "queue",
                                   "printer", "image", "spectrum", "wave", "alert"]):
                icon(d, n, 56 + i * 26, 580, 16, TEXT_2, 1.5)
            # badges
            section_label(d, 400, 410, "Quality classes")
            for i, c in enumerate(["cd", "hires", "lossless", "lossy_hi", "lossy", "failed"]):
                quality_badge(d, 400 + (i % 2) * 150, 422 + (i // 2) * 30, c)
            section_label(d, 400, 528, "Tiers")
            for i, t in enumerate("ABCLD?"):
                tier_chip(d, 400 + i * 28, 538, t)
            section_label(d, 400, 588, "Tested level")
            for i, t in enumerate(["V0", "V1", "V2", "V3"]):
                level_chip(d, 400 + i * 32, 598, t)
            d.text(532, 611, "untested · headers · quick · deep", 10.5, 400, TEXT_3)
            # verdicts
            section_label(d, 720, 410, "Verdicts")
            verdict_chip(d, 720, 422, "PASS", "21.8")
            verdict_chip(d, 720, 452, "SUSPECT", "19.2")
            verdict_chip(d, 720, 482, "LIKELY LOSSY", "16.0")
            verdict_chip(d, 720, 512, "UPSAMPLED", "22.0")
            section_label(d, 720, 568, "Tag source")
            sx = 720
            for s in ("file", "ia", "MB", "you"):
                sx += source_badge(d, sx, 578, s) + 6
            d.text(720, 614, "file · archive.org · MusicBrainz · you", 10.5, 400, TEXT_3)
            # inputs
            section_label(d, 960, 410, "Inputs & controls")
            field(d, 960, 422, 220, "Miles Davis", prefix="Artist", icon_="search", focused=True, label="Field · focused")
            field(d, 960, 464, 220, "Album · optional", placeholder=True, label="Field · empty")
            toggle(d, 1200, 430, True, "On", 12)
            toggle(d, 1270, 430, False, "Off", 12)
            checkbox(d, 1200, 472, True, "Lossless only")
            filter_chip(d, 960, 506, "FLAC", True)
            filter_chip(d, 1032, 506, "MP3", False)
            segmented(d, 1110, 504, ["Quality", "Match", "Popularity"], "Quality", h=28)
            section_label(d, 960, 568, "Progress")
            progress(d, 960, 578, 120, .42, AMBER)
            d.text(1088, 584, "running", 10.5, 500, TEXT_3)
            progress(d, 960, 596, 120, 1, GOOD)
            d.text(1088, 602, "done", 10.5, 500, TEXT_3)
            progress(d, 1150, 578, 120, .55, RED)
            d.text(1278, 584, "failed", 10.5, 500, TEXT_3)
            progress(d, 1150, 596, 120, .28, mix(LAVENDER, INDIGO, .4))
            d.text(1278, 602, "paused", 10.5, 500, TEXT_3)
            art_dots(d, 960, 630, [1, 1, 2, 0, 0])
            d.text(1024, 634, "art: front · back · inside · tray · disc", 10.5, 400, TEXT_3)

    with d.layer("Table row sample"):
        with d.group("Card · Result row"):
            card(d, 40, 672, 440, 188, "Result row", "selected + default")
            result_row(d, 48, 712, 424, RESULTS[1], 60, True)
            result_row(d, 48, 776, 424, RESULTS[2], 60, False, True)

    with d.layer("Options"):
        opts = [("A", "Workspace", "Sidebar + three panes: results · album detail with Quick / Deep test · metadata & save. "
                 "Queue is a bottom drawer.", "Fast for power users; everything visible.", "Dense; small waveform."),
                ("B", "Guided steps", "One focused card per step with Back / Next. Find → Check → Tag → Save; "
                 "Pick album → Choose scans → Print.", "Calm, hard to get wrong.", "More clicks to compare uploads."),
                ("C", "Library first", "Home is the cover grid. Ctrl K searches archive.org in a slide-over; albums "
                 "open a sheet: Tracks · Upgrade · CD Art.", "Feels like a music app.", "Search is secondary.")]
        for i, (k, name, desc, pro, con) in enumerate(opts):
            x = 496 + i * 304
            with d.group("Option card · %s %s" % (k, name)):
                d.box(x, 672, 296, 188, fill=WHITE, stroke=BORDER, rx=12)
                d.rect(x + 16, 688, 28, 28, fill=PRIMARY, rx=8)
                d.text(x + 30, 707, k, 14, 800, WHITE, anchor="middle")
                d.text(x + 54, 708, name, 15, 700, TEXT)
                words, line, yy = desc.split(" "), "", 738
                for wd in words:
                    if tw(line + " " + wd, 12) > 264:
                        d.text(x + 16, yy, line.strip(), 12, 400, TEXT_2)
                        yy += 17
                        line = ""
                    line += " " + wd
                d.text(x + 16, yy, line.strip(), 12, 400, TEXT_2)
                status_icon(d, x + 16, 818, True, 14)
                d.text(x + 36, 829, pro, 11.5, 500, TEXT)
                status_icon(d, x + 16, 838, "warn", 14)
                d.text(x + 36, 849, con, 11.5, 500, TEXT)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
SCREENS = [
    ("00", "00-styleguide.svg", "Cover · Style guide", screen_00),
    ("A1", "A1-search.svg", "Option A · Workspace · Album Search", screen_A1),
    ("A2", "A2-cd-art.svg", "Option A · Workspace · CD Art", screen_A2),
    ("A3", "A3-library.svg", "Option A · Workspace · Library", screen_A3),
    ("A4", "A4-settings.svg", "Option A · Workspace · Settings", screen_A4),
    ("B1", "B1-check.svg", "Option B · Guided steps · 2 Check", screen_B1),
    ("B2", "B2-choose-scans.svg", "Option B · Guided steps · 2 Choose scans", screen_B2),
    ("C1", "C1-home-search.svg", "Option C · Library first · Home + search", screen_C1),
    ("C2", "C2-album-cd-art.svg", "Option C · Library first · Album sheet · CD Art", screen_C2),
]

NS = ('xmlns="http://www.w3.org/2000/svg" '
      'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
      'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"')


def svg_doc(inner, w, h, title, namedview, defs=""):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
            '<svg %s width="%d" height="%d" viewBox="0 0 %d %d" version="1.1">\n'
            '<title>%s</title>\n%s\n<defs>%s</defs>\n%s\n</svg>\n') % (NS, w, h, w, h, escape(title), namedview, defs, inner)


def build():
    docs = []
    for key, fname, title, fn in SCREENS:
        d = Doc(key, title)
        fn(d)
        docs.append((key, fname, title, d))
        nv = ('<sodipodi:namedview id="%s-namedview" pagecolor="#ffffff" bordercolor="#666666" '
              'inkscape:document-units="px" inkscape:pagecheckerboard="false">'
              '<inkscape:page x="0" y="0" width="%d" height="%d" id="%s-page" inkscape:label=%s/>'
              '</sodipodi:namedview>') % (key.lower(), W, H, key.lower(), quoteattr(title))
        with open(os.path.join(HERE, fname), "w", encoding="utf-8") as f:
            f.write(svg_doc(d.body(), W, H, "iaflac · " + title, nv, "".join(d.defs)))

    # combined multi-page document: 3 columns, 1540 x 1000 apart
    pages, layers, labels = [], [], []
    for i, (key, fname, title, d) in enumerate(docs):
        x, y = (i % 3) * 1540, (i // 3) * 1000
        pages.append('<inkscape:page x="%d" y="%d" width="%d" height="%d" id="page-%s" inkscape:label=%s/>'
                     % (x, y, W, H, key.lower(), quoteattr("%s · %s" % (key, title))))
        layers.append('<g inkscape:groupmode="layer" id="screen-%s" inkscape:label=%s transform="translate(%d,%d)">\n%s\n</g>'
                      % (key.lower(), quoteattr("%s · %s" % (key, title)), x, y, d.body()))
        labels.append('<text x="%d" y="%d" font-family=%s font-size="22" font-weight="700" fill="%s" xml:space="preserve">%s</text>'
                      % (x, y - 22, quoteattr(FONT), TEXT, escape("%s · %s" % (key, title))))
    nv = ('<sodipodi:namedview id="namedview" pagecolor="#ffffff" bordercolor="#666666" '
          'inkscape:document-units="px" inkscape:pagecheckerboard="false">%s</sodipodi:namedview>') % "".join(pages)
    inner = "\n".join(layers) + '\n<g inkscape:groupmode="layer" id="page-labels" inkscape:label="Page labels">%s</g>' % "".join(labels)
    with open(os.path.join(HERE, "iaflac-mockups.svg"), "w", encoding="utf-8") as f:
        f.write(svg_doc(inner, W, H, "iaflac mockups", nv, "".join("".join(dd.defs) for *_, dd in docs)))
    print("wrote %d screens + iaflac-mockups.svg to %s" % (len(docs), HERE))


if __name__ == "__main__":
    build()
