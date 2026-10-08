#!/usr/bin/env python3
"""
iaflac mockups, round 2 (Option A, simplified), built on the components in build_mockups.py.

    python design/build_v2.py

Redraws A1 Album Search, A2 CD Art, A3 Library and A4 Settings and splices them into the user's
Inkscape file (design/edited-by-user.svg, never modified), writing:
  design/iaflac-mockups-v2.svg      combined multi-page file (the user's other pages kept as they are)
  design/A1-search.svg, A2-cd-art.svg, A3-library.svg, A4-settings.svg   single screens
"""
import math
import os
import random
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_mockups as bm  # noqa: E402
from build_mockups import (AMBER, BORDER, BORDER_SOFT, GOOD, GOOD_TEXT, GOOD_TINT, GREEN, INDIGO, INK,  # noqa: E402
                           LAVENDER, PRIMARY, PRIMARY_TEXT, PRIMARY_TINT, PRIMARY_TINT_2, RED, SURFACE_ALT,
                           SURFACE_SUNK, TEXT, TEXT_2, TEXT_3, WARN_LINE, WARN_TEXT, WARN_TINT, BAD_TEXT, BAD_TINT,
                           WHITE, W, H, Doc, button, card, case_page_outside, chip, cover, field, filter_chip,
                           checkbox, fit, icon, icon_button, level_chip, mini_chip, mix, play_button, progress,
                           quality_badge, scan, section_label, status_icon, tier_chip, track_picker, tw,
                           verdict_chip, chrome, smooth_noise, fmt, segmented, LIBRARY, OKC_TRACKS, PARTS, RESULTS)

HERE = os.path.dirname(os.path.abspath(__file__))
USER_FILE = os.path.join(HERE, "edited-by-user.svg")
OUT_FILE = os.path.join(HERE, "iaflac-mockups-v2.svg")

# The user's sidebar colour (set in Inkscape on A1), applied to every screen.
NAVY = "#102542"
NAV_ACTIVE = PRIMARY
NAV_TEXT = mix(LAVENDER, WHITE, 0.45)
NAV_MUTED = mix(LAVENDER, NAVY, 0.45)
NAV_LINE = mix(NAVY, WHITE, 0.12)

X0, XW = 216, 1208          # content area right of the sidebar
TOP, BOTTOM = 52, 884


# --------------------------------------------------------------------------
# Chrome
# --------------------------------------------------------------------------
def sidebar(d, active):
    with d.layer("Navigation"):
        with d.group("Sidebar"):
            d.rect(0, 36, 200, H - 36, fill=NAVY, label="Sidebar background")
            y = 56
            for name, ic, badge in (("Album Search", "search", None), ("CD Art", "disc", None),
                                    ("Library", "library", "5")):
                nav(d, 12, y, name, ic, name == active, badge)
                y += 44
            nav(d, 12, 812, "Settings", "settings", active == "Settings", None)


def nav(d, x, y, name, ic, on, badge):
    with d.group("Nav · %s" % name):
        if on:
            d.rect(x, y, 176, 40, fill=NAV_ACTIVE, rx=10)
        icon(d, ic, x + 14, y + 12, 16, WHITE if on else NAV_MUTED, 1.6)
        d.text(x + 42, y + 25, name, 13.5, 600 if on else 500, WHITE if on else NAV_TEXT)
        if badge:
            chip(d, x + 164, y + 11, badge, WHITE if on else NAV_LINE, PRIMARY_TEXT if on else NAV_TEXT,
                 h=18, size=10.5, pad=6, anchor="end")


def frame(d, title, active):
    chrome(d, title)
    sidebar(d, active)


# --------------------------------------------------------------------------
# 3D spectrogram (waterfall): time slices recede into the page, height = loudness
# --------------------------------------------------------------------------
LOUD = [(0.0, mix(NAVY, INDIGO, 0.25)), (0.35, INDIGO), (0.62, RED), (0.85, AMBER), (1.0, mix(AMBER, WHITE, 0.55))]


def loud_colour(t):
    t = max(0.0, min(1.0, t))
    for (t0, c0), (t1, c1) in zip(LOUD, LOUD[1:]):
        if t <= t1:
            return mix(c0, c1, (t - t0) / (t1 - t0))
    return LOUD[-1][1]


def spectrum_slices(n_t, n_f, cutoff, nyq, seed):
    rng = random.Random(seed)
    env = smooth_noise(rng, n_t, 5, 0.55, 1.0)
    harm = [rng.uniform(0.6, 1.0) for _ in range(8)]
    out = []
    for i in range(n_t):
        row = []
        bass = 0.9 + 0.1 * math.sin(i * 0.7)
        for j in range(n_f):
            f = j / (n_f - 1) * nyq
            if f > cutoff:
                v = 0.015 * rng.random()
            else:
                v = (1 - (f / nyq) ** 0.45) * 0.78 * env[i] * bass
                for k, hgt in enumerate(harm):          # a few tonal peaks (the trumpet's harmonics)
                    fk = 0.45 + k * 1.15
                    v += 0.22 * hgt * env[i] * math.exp(-((f - fk) / 0.22) ** 2) * (1 - f / nyq)
                v += rng.uniform(-0.03, 0.03)
                if f > cutoff - 1.2:
                    v *= 0.55
            row.append(max(0.0, min(1.0, v)))
        out.append(row)
    return out


def spectrogram_3d(d, x, y, w, h, cutoff=21.8, nyq=22.05, seed=11, n_t=28, n_f=96, playhead=0.34,
                   duration="11:33", now="3:58", label="3D spectrogram"):
    """Waterfall plot inside the box (x, y, w, h). Front edge = start of the track, back = end."""
    depth_x, depth_y = w * 0.15, h * 0.30        # how far the last slice sits up and to the right
    base_w = w - depth_x - 110                   # frequency axis length of one slice
    amp_h = h * 0.56                             # height of a full-scale peak
    fx0, fy0 = x + 60, y + h - 30                # front-left corner of the floor
    data = spectrum_slices(n_t, n_f, cutoff, nyq, seed)
    gid = d.uid("loudness")

    def pt(ti, fj, a):
        tt = ti / (n_t - 1)
        return (fx0 + fj / (n_f - 1) * base_w + tt * depth_x, fy0 - tt * depth_y - a * amp_h)

    with d.group(label):
        # gradient defined inline so the group can be copied between files
        stops = "".join('<stop offset="%s" stop-color="%s"/>' % (fmt(o), c) for o, c in LOUD)
        d.raw('<defs><linearGradient id="%s" x1="0" y1="1" x2="0" y2="0">%s</linearGradient></defs>' % (gid, stops))
        with d.group("Floor and axes"):
            corners = [pt(0, 0, 0), pt(0, n_f - 1, 0), pt(n_t - 1, n_f - 1, 0), pt(n_t - 1, 0, 0)]
            d.path("M" + " L".join("%s %s" % (fmt(round(a, 1)), fmt(round(b, 1))) for a, b in corners) + " Z",
                   fill=mix(WHITE, LAVENDER, 0.35), stroke=BORDER, sw=1, label="Floor")
            for khz in (0, 5, 10, 15, 20):
                fj = khz / nyq * (n_f - 1)
                a0, a1 = pt(0, fj, 0), pt(n_t - 1, fj, 0)
                d.line(a0[0], a0[1], a1[0], a1[1], stroke=mix(WHITE, LAVENDER, 0.9), sw=1)
                d.text(a0[0], a0[1] + 16, "%d kHz" % khz if khz else "0", 10, 500, TEXT_3, anchor="middle")
            # time labels along the right edge
            for tt, lab in ((0, "0:00"), (1, duration)):
                p = pt(tt * (n_t - 1), n_f - 1, 0)
                d.text(p[0] + 10, p[1] + 4, lab, 10, 500, TEXT_3)
            # loudness axis on the front-left edge
            b0 = pt(0, 0, 0)
            d.line(b0[0] - 10, b0[1], b0[0] - 10, b0[1] - amp_h, stroke=BORDER, sw=1)
            for frac, lab in ((0, "−90 dB"), (0.5, "−45"), (1, "0 dB")):
                py = b0[1] - frac * amp_h
                d.line(b0[0] - 14, py, b0[0] - 10, py, stroke=TEXT_3, sw=1)
                d.text(b0[0] - 17, py + 3.5, lab, 9.5, 500, TEXT_3, anchor="end")
        with d.group("Cutoff marker"):
            fj = cutoff / nyq * (n_f - 1)
            a0, a1 = pt(0, fj, 0), pt(n_t - 1, fj, 0)
            d.line(a0[0], a0[1], a1[0], a1[1], stroke=AMBER, sw=2, dash="5 3")
        with d.group("Time slices"):
            play_i = round(playhead * (n_t - 1))
            for ti in range(n_t - 1, -1, -1):              # back to front, so nearer slices cover farther ones
                row = data[ti]
                top = [pt(ti, fj, row[fj]) for fj in range(n_f)]
                floor_r, floor_l = pt(ti, n_f - 1, 0), pt(ti, 0, 0)
                dpath = "M%s %s " % (fmt(round(floor_l[0], 1)), fmt(round(floor_l[1], 1)))
                dpath += " ".join("L%s %s" % (fmt(round(a, 1)), fmt(round(b, 1))) for a, b in top)
                dpath += " L%s %s Z" % (fmt(round(floor_r[0], 1)), fmt(round(floor_r[1], 1)))
                played = ti <= play_i
                with d.group("Slice %d%s" % (ti + 1, " · playhead" if ti == play_i else "")):
                    d.raw('<path d="%s" fill="url(#%s)" fill-opacity="%s" stroke="%s" stroke-width="%s" '
                          'stroke-linejoin="round"/>' % (dpath, gid, "0.96" if played else "0.82",
                                                         WHITE if ti == play_i else mix(WHITE, INDIGO, 0.35),
                                                         "1.6" if ti == play_i else "0.5"))
        with d.group("Playhead label"):
            p = pt(play_i, n_f - 1, 0)
            d.box(p[0] + 8, p[1] - 10, 44, 20, fill=TEXT, stroke=None, rx=5)
            d.text(p[0] + 30, p[1] + 4, now, 10.5, 700, WHITE, anchor="middle")
        with d.group("Cutoff label"):
            fj = cutoff / nyq * (n_f - 1)
            p = pt(0, fj, 0)
            t = "cutoff %s kHz" % cutoff
            tw_ = tw(t, 10, 700) + 12
            d.box(p[0] - tw_ + 18, p[1] + 22, tw_, 18, fill=mix(AMBER, WHITE, 0.15), stroke=None, rx=5)
            d.text(p[0] + 12, p[1] + 34.5, t, 10, 700, TEXT, anchor="end")


def loudness_legend(d, x, y, w):
    with d.group("Loudness legend"):
        gid = d.uid("legend")
        stops = "".join('<stop offset="%s" stop-color="%s"/>' % (fmt(o), c) for o, c in LOUD)
        d.raw('<defs><linearGradient id="%s" x1="0" y1="0" x2="1" y2="0">%s</linearGradient></defs>' % (gid, stops))
        d.raw('<rect x="%s" y="%s" width="%s" height="8" rx="4" fill="url(#%s)"/>' % (fmt(float(x)), fmt(float(y)),
                                                                                      fmt(float(w)), gid))
        d.text(x, y + 22, "quiet", 10, 500, TEXT_3)
        d.text(x + w, y + 22, "loud", 10, 500, TEXT_3, anchor="end")


# --------------------------------------------------------------------------
# A1 · Album Search
# --------------------------------------------------------------------------
RES_COLS = [  # (key, header, x offset from card left, anchor)
    ("quality", "Quality", 16, "start"), ("tier", "Tier", 160, "start"), ("fmt", "Format", 194, "start"),
    ("ident", "Upload", 334, "start"), ("source", "Source", 606, "start"), ("match", "Match", 690, "start"),
    ("tracks", "Tracks", 788, "end"), ("length", "Length", 850, "end"), ("size", "Size", 916, "end"),
    ("year", "Year", 934, "start"), ("rip", "Rip files", 984, "start"), ("level", "Tested", 1072, "start"),
    ("inlib", "Library", 1124, "start"),
]


def results_table(d, x, y, w):
    with d.group("Results table"):
        for key, head, off, an in RES_COLS:
            d.text(x + off, y + 12, head.upper(), 10, 700, TEXT_3, anchor=None if an == "start" else an, ls=.5)
        d.hline(x + 16, x + w - 16, y + 20, BORDER)
        ry = y + 22
        for i, r in enumerate(RESULTS):
            sel = r.get("sel", False)
            with d.group("Result · %s" % r["ident"]):
                if sel:
                    d.rect(x + 8, ry + 1, w - 16, 34, fill=PRIMARY_TINT, rx=7)
                    d.rect(x + 8, ry + 9, 3, 18, fill=PRIMARY, rx=1.5)
                cy = ry + 23
                quality_badge(d, x + 16, ry + 8, r["cls"], h=20, size=10.5)
                tier_chip(d, x + 160, ry + 8, r["tier"], 20)
                d.text(x + 194, cy, r["fmt"], 12.5, 650, TEXT)
                d.text(x + 334, cy, fit(r["ident"], 12.5, 260, 500), 12.5, 500, TEXT if sel else TEXT_2)
                d.text(x + 606, cy, r["source"], 12, 400, TEXT_2)
                d.text(x + 690, cy, r["match"], 12, 400, TEXT_2)
                d.text(x + 788, cy, str(r["tracks"]), 12, 400, TEXT_2, anchor="end")
                d.text(x + 850, cy, r["length"], 12, 400, TEXT_2, anchor="end")
                d.text(x + 916, cy, r["size"], 12, 400, TEXT_2, anchor="end")
                d.text(x + 934, cy, r["year"], 12, 400, TEXT_2)
                rx_ = x + 984
                for f in r["rip"]:
                    rx_ += mini_chip(d, rx_, ry + 10, f) + 4
                level_chip(d, x + 1072, ry + 9, r["level"])
                if r.get("inlib"):
                    chip(d, x + 1124, ry + 9, "Have it", GOOD_TINT, GOOD_TEXT, h=18, size=10, pad=6, icon_="check", rx=5)
                if not sel and i < len(RESULTS) - 1 and not RESULTS[i + 1].get("sel"):
                    d.hline(x + 16, x + w - 16, ry + 36, BORDER_SOFT)
            ry += 36
    return ry


def screen_A1(d):
    frame(d, "Option A · Workspace · Album Search", "Album Search")
    with d.layer("Search and results"):
        with d.group("Card · Search and results"):
            card(d, X0, TOP, XW, 400)
            # search row
            field(d, X0 + 16, 68, 300, "Miles Davis", prefix="Artist", icon_="search", focused=True, label="Field · Artist")
            field(d, X0 + 328, 68, 300, "Kind of Blue", prefix="Album", label="Field · Album (optional)")
            button(d, X0 + 640, 68, "Search", "primary", w=96, h=34, icon_="search")
            cx = X0 + 760
            for f in ("FLAC", "WAV", "MP3"):
                cx += filter_chip(d, cx, 72, f, True) + 6
            field(d, X0 + XW - 16 - 150, 70, 150, "Quality", prefix="Sort", h=30, size=12, chevron=True,
                  label="Dropdown · Sort (Quality, Match, Popularity)")
            # options + summary row
            cx = X0 + 16
            cx += checkbox(d, cx, 120, False, "Lossless only") + 22
            cx += checkbox(d, cx, 120, False, "Hide albums I already have") + 22
            with d.group("Search notes"):
                icon(d, "info", X0 + XW - 16 - 470, 120, 15, PRIMARY, 1.5)
                d.spans(X0 + XW - 16 - 448, 132, [("7 uploads", 12, 650, TEXT),
                                                  ("  ·  2 near misses  ·  1 ALAC + 1 APE also found  ·  3 left out",
                                                   12, 400, TEXT_2)])
            d.hline(X0 + 16, X0 + XW - 16, 150, BORDER_SOFT)
            results_table(d, X0, 162, XW)
    with d.layer("Deep test"):
        with d.group("Card · Deep test"):
            DY = 468
            card(d, X0, DY, XW, BOTTOM - DY)
            d.text(X0 + 16, DY + 28, "Deep test", 15, 650, TEXT)
            d.text(X0 + 100, DY + 28, "miles-davis-kind-of-blue-1959 · downloads 380 MB, decodes every track and "
                                      "checks the spectrum", 12, 400, TEXT_3)
            bw = button(d, X0 + XW - 16, DY + 12, "Add to library", "primary", h=32, icon_="download", anchor="end")
            bw2 = button(d, X0 + XW - 28 - bw, DY + 12, "Run again", "secondary", h=32, icon_="refresh", anchor="end")
            chip(d, X0 + XW - 40 - bw - bw2, DY + 18, "Done · 6 of 6 tracks", GOOD_TINT, GOOD_TEXT, h=20, size=11,
                 icon_="check", anchor="end")
            # player row
            play_button(d, X0 + 16, DY + 52, 32, playing=True)
            track_picker(d, X0 + 58, DY + 53, 230, "04 · All Blues · 11:33")
            d.text(X0 + 302, DY + 73, "3:58 / 11:33", 12.5, 600, TEXT_2)
            chip(d, X0 + 400, DY + 57, "44.1 kHz · 16-bit · stereo", SURFACE_SUNK, TEXT, h=22, size=11, rx=6)
            # the 3D spectrogram
            SX, SY, SW, SH = X0 + 16, DY + 96, 860, BOTTOM - DY - 112
            d.box(SX, SY, SW, SH, fill=SURFACE_ALT, stroke=None, rx=10)
            spectrogram_3d(d, SX + 8, SY + 14, SW - 24, SH - 20)
            loudness_legend(d, SX + 20, SY + 16, 150)
            # verdict panel
            VX, VW = X0 + 892, XW - 908
            with d.group("Verdict"):
                verdict_chip(d, VX, SY, "PASS", "21.8", h=30, size=13)
                d.text(VX, SY + 54, "Full-band content up to 21.8 kHz.", 12.5, 500, TEXT)
                d.text(VX, SY + 72, "Looks like a real CD rip, not an MP3", 12.5, 400, TEXT_2)
                d.text(VX, SY + 90, "turned into FLAC.", 12.5, 400, TEXT_2)
                d.hline(VX, VX + VW, SY + 108, BORDER_SOFT)
                section_label(d, VX, SY + 132, "Cutoff per track")
                vals = [("So What", 21.8), ("Freddie Freeloader", 21.8), ("Blue in Green", 21.7), ("All Blues", 21.8),
                        ("Flamenco Sketches", 21.9), ("Flamenco Sketches (alt)", 21.6)]
                for i, (t, v) in enumerate(vals):
                    yy = SY + 146 + i * 22
                    sel = i == 3
                    if sel:
                        d.rect(VX - 6, yy - 1, VW + 6, 21, fill=PRIMARY_TINT, rx=5)
                    d.text(VX, yy + 14, "%d" % (i + 1), 11, 600, TEXT_3)
                    d.text(VX + 16, yy + 14, fit(t, 11.5, 140, 500), 11.5, 600 if sel else 500, TEXT)
                    progress(d, VX + 166, yy + 7, VW - 222, v / 22.05, GOOD, h=6)
                    d.text(VX + VW, yy + 14, "%.1f" % v, 11, 600, TEXT_2, anchor="end")



# --------------------------------------------------------------------------
# A2 · CD Art
# --------------------------------------------------------------------------
SCANS = [("Cover Art Archive · 1997 XE CD", "595 dpi", "2835 × 2835 px", False, 5),
         ("Cover Art Archive · 1997 XE CD", "583 dpi", "3470 × 2710 px", False, 3),
         ("Cover Art Archive · 1997 XE CD", "580 dpi", "2740 × 2740 px", False, 4),
         ("Cover Art Archive · 1997 XE CD", "583 dpi", "3170 × 2710 px", False, 2),
         ("archive.org upload · scans/cd.jpg", "150 dpi", "690 × 690 px", True, 3)]


def screen_A2(d):
    frame(d, "Option A · Workspace · CD Art", "CD Art")
    LW = 792
    with d.layer("Album and scans"):
        with d.group("Card · Album and scans"):
            card(d, X0, TOP, LW, BOTTOM - TOP)
            with d.group("Library album picker"):
                d.box(X0 + 16, 68, 340, 48, fill=WHITE, stroke=PRIMARY, rx=10)
                cover(d, X0 + 24, 74, 36, "kob", 5)
                d.text(X0 + 70, 89, "Kind of Blue", 13.5, 650, TEXT)
                d.text(X0 + 70, 106, "Miles Davis · 1959 · from your library", 11.5, 400, TEXT_2)
                icon(d, "chev_down", X0 + 330, 84, 16, TEXT_2)
            button(d, X0 + 368, 76, "Find art", "primary", w=104, h=32, icon_="search")
            with d.group("Find-art status"):
                d.text(X0 + 488, 88, "Done · 7 pressings searched · 14 s", 11.5, 600, GOOD_TEXT)
                progress(d, X0 + 488, 98, LW - 504, 1.0, GOOD)
            with d.group("Find-art notes"):
                d.box(X0 + 16, 128, LW - 32, 32, fill=SURFACE_ALT, stroke=None, rx=8)
                icon(d, "info", X0 + 26, 136, 15, PRIMARY, 1.5)
                d.spans(X0 + 48, 148, [("Matched ", 11.5, 400, TEXT_2), ("Columbia CK 64935 · 1997 US CD", 11.5, 650, TEXT),
                                       ("   ·   all five parts from the 1997 XE pressing   ·   ", 11.5, 400, TEXT_2),
                                       ("disc label is soft", 11.5, 650, WARN_TEXT)])
            d.hline(X0 + 16, X0 + LW - 16, 172, BORDER_SOFT)
            for i, ((pk, pn), (src, dpi, px, warn, nalt)) in enumerate(zip(PARTS, SCANS)):
                yy = 180 + i * 140
                with d.group("Scan row · %s" % pn):
                    if warn:
                        d.box(X0 + 10, yy, LW - 20, 134, fill=WARN_TINT, stroke=WARN_LINE, rx=10)
                    tw_, th = (112, 112) if pk not in ("back", "tray") else (140, 110)
                    tx, ty = X0 + 22, yy + 12
                    d.box(tx - 3, ty - 3, tw_ + 6, th + 6, fill=None, stroke=PRIMARY, rx=7, sw=2)
                    scan(d, tx, ty, tw_, th, pk, 0, rx=4, soft=warn)
                    ix = X0 + 186
                    d.text(ix, yy + 30, pn, 14, 650, TEXT)
                    d.text(ix, yy + 50, src, 12, 500, TEXT_2)
                    cx = ix
                    if warn:
                        cx += chip(d, cx, yy + 60, dpi + " · below 200", AMBER, TEXT, h=20, size=11, icon_="alert", rx=5) + 8
                    else:
                        cx += chip(d, cx, yy + 60, dpi, GOOD_TINT, GOOD_TEXT, h=20, size=11, rx=5) + 8
                    d.text(cx, yy + 74, px, 11, 400, TEXT_3)
                    section_label(d, ix, yy + 106, "Other scans")
                    ax = ix + 92
                    for k in range(nalt):
                        aw, ah = (40, 40) if pk not in ("back", "tray") else (52, 40)
                        scan(d, ax, yy + 84, aw, ah, pk, k + 1, rx=3)
                        d.box(ax, yy + 84, aw, ah, fill=None, stroke=BORDER, rx=3)
                        ax += aw + 8
                    d.text(ax + 4, yy + 109, "+ search more", 11, 600, PRIMARY)
                    if i < 4 and not warn and not SCANS[i + 1][3]:
                        d.hline(X0 + 16, X0 + LW - 16, yy + 138, BORDER_SOFT)
    RX, RW = X0 + LW + 16, XW - LW - 16
    with d.layer("Case preview"):
        with d.group("Card · Case preview"):
            card(d, RX, TOP, RW, BOTTOM - TOP, "Case preview")
            segmented(d, RX + RW - 16 - 206, 62, ["Outside", "Inside", "Disc"], "Outside", h=28,
                      widths=[72, 64, 64], label="Preview page")
            case_page_outside(d, RX + 16, 104, RW - 32, 560)
            with d.group("Preview warnings"):
                chip(d, RX + 16, 680, "Disc label is soft (150 dpi)", WARN_TINT, WARN_TEXT, h=22, size=11, icon_="alert", rx=6)
            d.hline(RX + 16, RX + RW - 16, 718, BORDER_SOFT)
            section_label(d, RX + 16, 744, "Print")
            d.text(RX + RW - 16, 744, "Letter · 1.5 mm bleed · 3 pages", 11, 500, TEXT_3, anchor="end")
            half = (RW - 40) / 2
            button(d, RX + 16, 758, "Make PDF", "primary", w=half, h=36, icon_="printer")
            button(d, RX + 24 + half, 758, "Open PDF", "secondary", w=half, h=36, icon_="external")
            button(d, RX + 16, 804, "Save scans to album folder", "secondary", w=RW - 32, h=36, icon_="folder")


# --------------------------------------------------------------------------
# A3 · Library
# --------------------------------------------------------------------------
LIB_ISSUES = {
    "boc": [], "kob": [],
    "okc": [("warn", "Track 7 has no track number"), ("warn", ".cue file may double-list it")],
    "sol": [("warn", "No cover art"), ("warn", "Tracks disagree on DATE")],
    "tanox": [("bad", "Lossy MP3: a FLAC copy may exist")],
}


def issue_chip(d, x, y, kind, text):
    if kind == "bad":
        return chip(d, x, y, text, BAD_TINT, BAD_TEXT, h=22, size=11, dot=RED, rx=6)
    return chip(d, x, y, text, WARN_TINT, WARN_TEXT, h=22, size=11, icon_="alert", rx=6)


def screen_A3(d):
    frame(d, "Option A · Workspace · Library", "Library")
    with d.layer("Library"):
        with d.group("Card · Library"):
            LH = 418
            card(d, X0, TOP, XW, LH, "Library", "5 albums · 71 tracks · 2.4 GB · D:\\Music")
            field(d, X0 + XW - 16 - 112 - 12 - 260, 62, 260, "Filter albums…", h=30, icon_="search", placeholder=True,
                  size=12, label="Field · Filter")
            button(d, X0 + XW - 16, 62, "Rescan", "secondary", w=112, h=30, size=12, icon_="refresh", anchor="end")
            cols = [("Artist", 72), ("Album", 236), ("Year", 438), ("Format", 486), ("Tracks", 668), ("Cover", 700),
                    ("Issues", 760)]
            for t, off in cols:
                d.text(X0 + off, 124, t.upper(), 10, 700, TEXT_3, anchor="end" if t == "Tracks" else None, ls=.5)
            d.hline(X0 + 16, X0 + XW - 16, 132, BORDER)
            for i, a in enumerate(LIBRARY):
                yy = 134 + i * 64
                with d.group("Library row · %s" % a["album"]):
                    sel = a["key"] == "okc"
                    if sel:
                        d.rect(X0 + 8, yy + 2, XW - 16, 60, fill=PRIMARY_TINT, rx=8)
                        d.rect(X0 + 8, yy + 16, 3, 32, fill=PRIMARY, rx=1.5)
                    if a["cover"]:
                        cover(d, X0 + 16, yy + 10, 44, a["key"], 5)
                    else:
                        d.box(X0 + 16, yy + 10, 44, 44, fill=SURFACE_SUNK, stroke=BORDER, rx=5, dash="3 2")
                        icon(d, "image", X0 + 30, yy + 24, 16, TEXT_3)
                    d.text(X0 + 72, yy + 37, a["artist"], 13, 500, TEXT)
                    d.text(X0 + 236, yy + 37, a["album"], 13, 650, TEXT)
                    d.text(X0 + 438, yy + 37, a["year"], 12.5, 400, TEXT_2)
                    col = GOOD_TEXT if a["fmt"].startswith("FLAC") else BAD_TEXT
                    d.text(X0 + 486, yy + 37, a["fmt"], 12, 600, col)
                    d.text(X0 + 668, yy + 37, str(a["tracks"]), 12.5, 400, TEXT_2, anchor="end")
                    status_icon(d, X0 + 708, yy + 25, True if a["cover"] else False, 14)
                    issues = LIB_ISSUES[a["key"]]
                    if issues:
                        cx = X0 + 760
                        for kind, t in issues:
                            cx += issue_chip(d, cx, yy + 21, kind, t) + 8
                        button(d, X0 + XW - 16, yy + 18, "Fix", "secondary", h=28, size=12, anchor="end")
                    else:
                        status_icon(d, X0 + 760, yy + 25, True, 14)
                        d.text(X0 + 782, yy + 37, "No issues", 12, 500, GOOD_TEXT)
                    if i < 4 and not sel and LIBRARY[i + 1]["key"] != "okc":
                        d.hline(X0 + 16, X0 + XW - 16, yy + 64, BORDER_SOFT)
    with d.layer("Album view"):
        with d.group("Card · Album view"):
            AY = TOP + 418 + 16
            card(d, X0, AY, XW, BOTTOM - AY)
            cover(d, X0 + 16, AY + 16, 56, "okc", 7)
            d.text(X0 + 88, AY + 38, "OK Computer", 19, 700, TEXT)
            d.text(X0 + 88, AY + 58, "Radiohead · 1997 · 12 tracks · 53:21 · Music/Radiohead/OK Computer (1997)/",
                   12.5, 400, TEXT_2)
            cx = X0 + 88 + tw("OK Computer", 19, 700) + 14
            cx += quality_badge(d, cx, AY + 24, "cd", h=20, size=10.5) + 6
            chip(d, cx, AY + 24, "FLAC 16/44.1", SURFACE_SUNK, TEXT_2, h=20, size=10.5, rx=5)
            icon_button(d, X0 + XW - 16 - 32, AY + 18, "folder", 32, label="Button · Open folder")
            button(d, X0 + XW - 56, AY + 18, "CD art", "secondary", h=32, icon_="disc", anchor="end")
            with d.group("Tracks"):
                half = (XW - 48) / 2
                for col in (0, 1):
                    bx = X0 + 16 + col * (half + 16)
                    ty = AY + 88
                    for t, off, an in (("#", 0, None), ("Title", 26, None), ("Artist", half - 210, None),
                                       ("Length", half - 60, "end"), ("Tags", half, "end")):
                        d.text(bx + off, ty + 12, t.upper(), 10, 700, TEXT_3, anchor=an, ls=.5)
                    d.hline(bx, bx + half, ty + 20, BORDER)
                    for k in range(6):
                        i = col * 6 + k
                        t, ln = OKC_TRACKS[i]
                        yy = ty + 22 + k * 30
                        bad = i == 6
                        if bad:
                            d.rect(bx - 6, yy + 2, half + 12, 28, fill=WARN_TINT, rx=6)
                        d.text(bx, yy + 21, "–" if bad else str(i + 1), 12, 600 if bad else 500, WARN_TEXT if bad else TEXT_3)
                        d.text(bx + 26, yy + 21, fit(t, 12.5, half - 250, 500), 12.5, 500, TEXT)
                        if bad:
                            chip(d, bx + 26 + tw(t, 12.5, 500) + 10, yy + 7, "no track number", AMBER, TEXT, h=18,
                                 size=10, pad=6, rx=5)
                        d.text(bx + half - 210, yy + 21, "Radiohead", 12, 400, TEXT_2)
                        d.text(bx + half - 60, yy + 21, ln, 12, 400, TEXT_2, anchor="end")
                        if bad:
                            status_icon(d, bx + half - 14, yy + 9, "warn", 14)
                        else:
                            status_icon(d, bx + half - 14, yy + 9, True, 14)


# --------------------------------------------------------------------------
# A4 · Settings: the original screen with the new sidebar
# --------------------------------------------------------------------------
def screen_A4(d):
    inner = Doc(d.key, d.title)
    bm.screen_A4(inner)
    body = inner.body()
    # swap the old sidebar layer for the navy one
    start = body.find('inkscape:label="Navigation"')
    if start != -1:
        g0 = body.rfind("<g ", 0, start)
        depth, i = 0, g0
        while True:
            o, c = body.find("<g", i + 1), body.find("</g>", i + 1)
            if o != -1 and o < c:
                depth += 1
                i = o
            else:
                if depth == 0:
                    end = c + 4
                    break
                depth -= 1
                i = c
        nav_doc = Doc(d.key + "n", d.title)
        sidebar(nav_doc, "Settings")
        body = body[:g0] + nav_doc.body() + body[end:]
    d.raw(body)


# --------------------------------------------------------------------------
# Output: single screens + splice into the user's multi-page file
# --------------------------------------------------------------------------
V2 = [("A1", "A1-search.svg", "Option A · Workspace · Album Search", screen_A1),
      ("A2", "A2-cd-art.svg", "Option A · Workspace · CD Art", screen_A2),
      ("A3", "A3-library.svg", "Option A · Workspace · Library", screen_A3),
      ("A4", "A4-settings.svg", "Option A · Workspace · Settings", screen_A4)]

NSMAP = {"": "http://www.w3.org/2000/svg", "inkscape": "http://www.inkscape.org/namespaces/inkscape",
         "sodipodi": "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"}
INK_LABEL = "{%s}label" % NSMAP["inkscape"]
INK_MODE = "{%s}groupmode" % NSMAP["inkscape"]


def build():
    bodies = {}
    for key, fname, title, fn in V2:
        d = Doc(key, title)
        fn(d)
        bodies[key] = d.body()
        nv = ('<sodipodi:namedview id="%s-namedview" pagecolor="#ffffff" bordercolor="#666666" '
              'inkscape:document-units="px" inkscape:pagecheckerboard="false">'
              '<inkscape:page x="0" y="0" width="%d" height="%d" id="%s-page" inkscape:label="%s"/>'
              '</sodipodi:namedview>') % (key.lower(), W, H, key.lower(), title)
        with open(os.path.join(HERE, fname), "w", encoding="utf-8") as f:
            f.write(bm.svg_doc(d.body(), W, H, "iaflac · " + title, nv, "".join(d.defs)))

    for p, uri in NSMAP.items():
        ET.register_namespace(p, uri)
    tree = ET.parse(USER_FILE)
    root = tree.getroot()
    replaced = []
    for g in root:
        label = g.get(INK_LABEL, "")
        code = label.split(" ")[0]
        if g.get(INK_MODE) == "layer" and code in bodies:
            frag = ET.fromstring('<svg xmlns="%s" xmlns:inkscape="%s" xmlns:sodipodi="%s">%s</svg>'
                                 % (NSMAP[""], NSMAP["inkscape"], NSMAP["sodipodi"], bodies[code]))
            for ch in list(g):
                g.remove(ch)
            for ch in list(frag):
                g.append(ch)
            replaced.append(code)
    # Option A was chosen: the B and C pages were emptied in round 1 (B1's deep-test panel had been dragged onto
    # A1, which now has its own), so drop those pages, their layers and their labels.
    dropped = ("B1", "B2", "C1", "C2")
    for g in list(root):
        if g.get(INK_MODE) == "layer" and g.get(INK_LABEL, "").split(" ")[0] in dropped:
            root.remove(g)
    nv = root.find("{%s}namedview" % NSMAP["sodipodi"])
    for page in list(nv):
        if page.get(INK_LABEL, "").split(" ")[0] in dropped:
            nv.remove(page)
    for g in root:
        if g.get(INK_LABEL) == "Page labels":
            for t in list(g):
                if "".join(t.itertext()).split(" ")[0] in dropped:
                    g.remove(t)
    tree.write(OUT_FILE, encoding="UTF-8", xml_declaration=True)
    print("replaced pages %s; wrote %s and %d single screens" % (", ".join(replaced), OUT_FILE, len(V2)))


if __name__ == "__main__":
    build()
