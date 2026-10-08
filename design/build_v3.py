#!/usr/bin/env python3
"""
iaflac mockups, round 3: Option A with a top bar (original dark indigo) instead of the sidebar, and a tag viewer
on Album Search. Reuses the components of build_mockups.py and the 3D spectrogram of build_v2.py.

    python design/build_v3.py

Splices A1-A4 into the user's Inkscape file (design/edited-by-user.svg, never modified) and writes
design/iaflac-mockups-v3.svg plus the single screens design/A1-search.svg ... A4-settings.svg.
"""
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_mockups as bm  # noqa: E402
import build_v2 as v2  # noqa: E402
from build_mockups import (AMBER, BORDER, BORDER_SOFT, GOOD_TEXT, GOOD_TINT, PRIMARY, PRIMARY_TEXT,  # noqa: E402
                           PRIMARY_TINT, PRIMARY_TINT_2, SIDEBAR, SIDEBAR_LINE, SIDEBAR_MUTED, SIDEBAR_TEXT,
                           SURFACE_ALT, SURFACE_SUNK, TEXT, TEXT_2, TEXT_3, WARN_LINE, WARN_TEXT, WARN_TINT, WHITE,
                           W, H, Doc, button, card, case_page_outside, chip, chrome, cover, field, filter_chip,
                           checkbox, fit, icon, icon_button, level_chip, mini_chip, play_button, progress,
                           quality_badge, scan, section_label, segmented, source_badge, status_icon, tier_chip,
                           track_picker, tw, verdict_chip, KOB_TRACKS, LIBRARY, OKC_TRACKS, PARTS, RESULTS)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_FILE = os.path.join(HERE, "iaflac-mockups-v3.svg")

TOPBAR = SIDEBAR            # the original dark indigo (#252047), restored
BAR_Y, BAR_H = 36, 48
X0, XW = 16, W - 32         # full width under the top bar
TOP, BOTTOM = BAR_Y + BAR_H + 16, 884


# --------------------------------------------------------------------------
# Top bar
# --------------------------------------------------------------------------
def topbar(d, active):
    with d.layer("Navigation"):
        with d.group("Top bar"):
            d.rect(0, BAR_Y, W, BAR_H, fill=TOPBAR, label="Top bar background")
            x = 16
            for name, ic, badge in (("Album Search", "search", None), ("CD Art", "disc", None),
                                    ("Library", "library", "5")):
                x += tab(d, x, BAR_Y + 8, name, ic, name == active, badge) + 6
            with d.group("Queue indicator"):
                qx = W - 16 - 32 - 10
                w = chip(d, qx, BAR_Y + 14, "Queue · 1 downloading", SIDEBAR_LINE, SIDEBAR_TEXT, h=22, size=11,
                         dot=AMBER, anchor="end", rx=6)
            with d.group("Nav · Settings"):
                on = active == "Settings"
                d.box(W - 16 - 32, BAR_Y + 8, 32, 32, fill=PRIMARY if on else None, stroke=None, rx=8)
                icon(d, "settings", W - 16 - 24, BAR_Y + 16, 16, WHITE if on else SIDEBAR_MUTED, 1.6)


def tab(d, x, y, name, ic, on, badge):
    w = 16 + 16 + 8 + tw(name, 13.5, 600) + 16 + (30 if badge else 0)
    with d.group("Nav · %s" % name):
        if on:
            d.rect(x, y, w, 32, fill=PRIMARY, rx=8)
        icon(d, ic, x + 14, y + 8, 16, WHITE if on else SIDEBAR_MUTED, 1.6)
        d.text(x + 38, y + 21, name, 13.5, 600 if on else 500, WHITE if on else SIDEBAR_TEXT)
        if badge:
            chip(d, x + w - 12, y + 7, badge, WHITE if on else SIDEBAR_LINE, PRIMARY_TEXT if on else SIDEBAR_TEXT,
                 h=18, size=10.5, pad=6, anchor="end")
    return w


def frame(d, title, active):
    chrome(d, title)
    topbar(d, active)


# --------------------------------------------------------------------------
# A1 · Album Search: search + results, deep test, tags
# --------------------------------------------------------------------------
RES_COLS = [("Quality", 16), ("Tier", 160), ("Format", 194), ("Upload", 334), ("Source", 676), ("Match", 770),
            ("Tracks", 878, "end"), ("Length", 948, "end"), ("Size", 1022, "end"), ("Year", 1046),
            ("Rip files", 1100), ("Tested", 1210), ("Library", 1280)]


def results_table(d, x, y, w, row_h=32):
    with d.group("Results table"):
        for col in RES_COLS:
            head, off = col[0], col[1]
            d.text(x + off, y + 12, head.upper(), 10, 700, TEXT_3, anchor=col[2] if len(col) > 2 else None, ls=.5)
        d.hline(x + 16, x + w - 16, y + 20, BORDER)
        ry = y + 22
        for i, r in enumerate(RESULTS):
            sel = r.get("sel", False)
            with d.group("Result · %s" % r["ident"]):
                if sel:
                    d.rect(x + 8, ry + 1, w - 16, row_h - 2, fill=PRIMARY_TINT, rx=7)
                    d.rect(x + 8, ry + 8, 3, row_h - 16, fill=PRIMARY, rx=1.5)
                cy, by = ry + row_h / 2 + 4.5, ry + (row_h - 20) / 2
                quality_badge(d, x + 16, by, r["cls"], h=20, size=10.5)
                tier_chip(d, x + 160, by, r["tier"], 20)
                d.text(x + 194, cy, r["fmt"], 12.5, 650, TEXT)
                d.text(x + 334, cy, fit(r["ident"], 12.5, 320, 500), 12.5, 500, TEXT if sel else TEXT_2)
                d.text(x + 676, cy, r["source"], 12, 400, TEXT_2)
                d.text(x + 770, cy, r["match"], 12, 400, TEXT_2)
                d.text(x + 878, cy, str(r["tracks"]), 12, 400, TEXT_2, anchor="end")
                d.text(x + 948, cy, r["length"], 12, 400, TEXT_2, anchor="end")
                d.text(x + 1022, cy, r["size"], 12, 400, TEXT_2, anchor="end")
                d.text(x + 1046, cy, r["year"], 12, 400, TEXT_2)
                rx_ = x + 1100
                for f in r["rip"]:
                    rx_ += mini_chip(d, rx_, by + 2, f) + 4
                level_chip(d, x + 1210, by + 1, r["level"])
                if r.get("inlib"):
                    chip(d, x + 1280, by + 1, "Have it", GOOD_TINT, GOOD_TEXT, h=18, size=10, pad=6, icon_="check", rx=5)
                if not sel and i < len(RESULTS) - 1 and not RESULTS[i + 1].get("sel"):
                    d.hline(x + 16, x + w - 16, ry + row_h, BORDER_SOFT)
            ry += row_h
    return ry


TAGS = [("Album artist", "Miles Davis", "MB"), ("Album", "Kind of Blue", "MB"), ("Date", "1959", "you"),
        ("Genre", "Jazz", "ia"), ("Label", "Columbia", "MB"), ("Catalog #", "CK 64935", "file")]


def screen_A1(d):
    frame(d, "Option A · Workspace · Album Search", "Album Search")
    with d.layer("Search and results"):
        with d.group("Card · Search and results"):
            card(d, X0, TOP, XW, 346)
            y0 = TOP + 16
            field(d, X0 + 16, y0, 300, "Miles Davis", prefix="Artist", icon_="search", focused=True, label="Field · Artist")
            field(d, X0 + 328, y0, 300, "Kind of Blue", prefix="Album", label="Field · Album (optional)")
            button(d, X0 + 640, y0, "Search", "primary", w=100, h=34, icon_="search")
            cx = X0 + 764
            for f in ("FLAC", "WAV", "MP3"):
                cx += filter_chip(d, cx, y0 + 4, f, True) + 6
            cx += 10
            cx += checkbox(d, cx, y0 + 9, False, "Lossless only") + 18
            checkbox(d, cx, y0 + 9, False, "Hide albums I have")
            field(d, X0 + XW - 16 - 140, y0 + 2, 140, "Quality", prefix="Sort", h=30, size=12, chevron=True,
                  label="Dropdown · Sort (Quality, Match, Popularity)")
            with d.group("Search notes"):
                icon(d, "info", X0 + 16, y0 + 46, 15, PRIMARY, 1.5)
                d.spans(X0 + 38, y0 + 58, [("7 uploads", 12, 650, TEXT),
                                           ("  ·  2 near misses  ·  1 ALAC + 1 APE also found  ·  3 left out",
                                            12, 400, TEXT_2)])
            results_table(d, X0, y0 + 74, XW, row_h=32)

    BY = TOP + 346 + 16                   # bottom row: deep test (left) + tags (right)
    DW = 840
    with d.layer("Deep test"):
        with d.group("Card · Deep test"):
            card(d, X0, BY, DW, BOTTOM - BY)
            d.text(X0 + 16, BY + 28, "Deep test", 15, 650, TEXT)
            d.text(X0 + 100, BY + 28, "miles-davis-kind-of-blue-1959 · 380 MB", 12, 400, TEXT_3)
            bw = button(d, X0 + DW - 16, BY + 12, "Add to library", "primary", h=32, icon_="download", anchor="end")
            bw2 = button(d, X0 + DW - 26 - bw, BY + 12, "Run again", "secondary", h=32, icon_="refresh", anchor="end")
            chip(d, X0 + DW - 36 - bw - bw2, BY + 18, "Done · 6 of 6", GOOD_TINT, GOOD_TEXT, h=20, size=11,
                 icon_="check", anchor="end")
            play_button(d, X0 + 16, BY + 52, 32, playing=True)
            track_picker(d, X0 + 58, BY + 53, 220, "04 · All Blues · 11:33")
            d.text(X0 + 292, BY + 73, "3:58 / 11:33", 12.5, 600, TEXT_2)
            chip(d, X0 + 390, BY + 57, "44.1 kHz · 16-bit · stereo", SURFACE_SUNK, TEXT, h=22, size=11, rx=6)
            SX, SY, SW, SH = X0 + 16, BY + 96, 580, BOTTOM - BY - 112
            d.box(SX, SY, SW, SH, fill=SURFACE_ALT, stroke=None, rx=10)
            v2.spectrogram_3d(d, SX + 6, SY + 12, SW - 16, SH - 18)
            v2.loudness_legend(d, SX + 16, SY + 14, 130)
            VX, VW = SX + SW + 16, DW - SW - 48
            with d.group("Verdict"):
                verdict_chip(d, VX, SY, "PASS", "21.8", h=28, size=12.5)
                d.text(VX, SY + 50, "Full-band content to 21.8 kHz:", 12, 500, TEXT)
                d.text(VX, SY + 66, "a real CD rip, not an MP3", 12, 400, TEXT_2)
                d.text(VX, SY + 82, "turned into FLAC.", 12, 400, TEXT_2)
                d.hline(VX, VX + VW, SY + 98, BORDER_SOFT)
                section_label(d, VX, SY + 120, "Cutoff per track")
                for i, (t, v) in enumerate([("So What", 21.8), ("Freddie Freeloader", 21.8), ("Blue in Green", 21.7),
                                            ("All Blues", 21.8), ("Flamenco Sketches", 21.9), ("Flamenco (alt)", 21.6)]):
                    yy = SY + 132 + i * 21
                    if i == 3:
                        d.rect(VX - 6, yy - 1, VW + 6, 20, fill=PRIMARY_TINT, rx=5)
                    d.text(VX, yy + 13, "%d" % (i + 1), 11, 600, TEXT_3)
                    d.text(VX + 14, yy + 13, fit(t, 11.5, 112, 500), 11.5, 600 if i == 3 else 500, TEXT)
                    progress(d, VX + 132, yy + 6, VW - 166, v / 22.05, bm.GOOD, h=6)
                    d.text(VX + VW, yy + 13, "%.1f" % v, 11, 600, TEXT_2, anchor="end")

    TX, TW = X0 + DW + 16, XW - DW - 16
    with d.layer("Tags"):
        with d.group("Card · Tags"):
            card(d, TX, BY, TW, BOTTOM - BY)
            d.text(TX + 16, BY + 28, "Tags", 15, 650, TEXT)
            d.text(TX + 58, BY + 28, "written when saved", 12, 400, TEXT_3)
            button(d, TX + TW - 16, BY + 12, "Edit tags", "secondary", h=32, icon_="tag", anchor="end")
            with d.group("MusicBrainz match"):
                d.box(TX + 16, BY + 54, TW - 32, 30, fill=PRIMARY_TINT, stroke=None, rx=8)
                d.box(TX + 24, BY + 59, 26, 20, fill=GOOD_TINT, stroke=None, rx=5)
                d.text(TX + 37, BY + 73, "98", 11, 700, GOOD_TEXT, anchor="middle")
                d.spans(TX + 58, BY + 73, [("MusicBrainz  ", 11.5, 600, PRIMARY_TEXT),
                                           ("Kind of Blue · 1997-03-25 · US · CD", 11.5, 500, TEXT)])
                d.text(TX + TW - 26, BY + 73, "Change", 11.5, 600, PRIMARY, anchor="end")
            with d.group("Album tags"):
                colw = (TW - 44) / 2
                for i, (lab, val, src) in enumerate(TAGS):
                    fx = TX + 16 + (i % 2) * (colw + 12)
                    fy = BY + 96 + (i // 2) * 44
                    d.text(fx, fy + 10, lab, 11, 600, TEXT_2)
                    field(d, fx, fy + 15, colw, val, h=26, size=12, src=src, label="Tag · %s" % lab)
            with d.group("Track tags"):
                ty = BY + 236
                for t, off, an in (("Disc", 0, None), ("#", 36, None), ("Title", 62, None), ("Artist", TW - 200, None),
                                   ("Length", TW - 32, "end")):
                    d.text(TX + 16 + off, ty + 12, t.upper(), 10, 700, TEXT_3, anchor=an, ls=.5)
                d.hline(TX + 16, TX + TW - 16, ty + 20, BORDER)
                for i, (t, ln, _) in enumerate(KOB_TRACKS):
                    yy = ty + 22 + i * 26
                    changed = i == 5
                    if changed:
                        d.rect(TX + 10, yy + 2, TW - 20, 24, fill=WARN_TINT, rx=6)
                    d.text(TX + 22, yy + 19, "1", 12, 400, TEXT_2)
                    d.text(TX + 52, yy + 19, str(i + 1), 12, 400, TEXT_2)
                    d.text(TX + 78, yy + 19, fit(t, 12.5, TW - 300, 500), 12.5, 500, TEXT)
                    if changed:
                        chip(d, TX + 78 + tw(t, 12.5, 500) + 8, yy + 6, "changed", AMBER, TEXT, h=18, size=10, pad=6, rx=5)
                    d.text(TX + TW - 184, yy + 19, "Miles Davis", 12, 400, TEXT_2)
                    d.text(TX + TW - 16, yy + 19, ln, 12, 400, TEXT_2, anchor="end")


# --------------------------------------------------------------------------
# A2 · CD Art
# --------------------------------------------------------------------------
def screen_A2(d):
    frame(d, "Option A · Workspace · CD Art", "CD Art")
    LW = 920
    with d.layer("Album and scans"):
        with d.group("Card · Album and scans"):
            card(d, X0, TOP, LW, BOTTOM - TOP)
            y0 = TOP + 16
            with d.group("Library album picker"):
                d.box(X0 + 16, y0, 360, 48, fill=WHITE, stroke=PRIMARY, rx=10)
                cover(d, X0 + 24, y0 + 6, 36, "kob", 5)
                d.text(X0 + 70, y0 + 21, "Kind of Blue", 13.5, 650, TEXT)
                d.text(X0 + 70, y0 + 38, "Miles Davis · 1959 · from your library", 11.5, 400, TEXT_2)
                icon(d, "chev_down", X0 + 350, y0 + 16, 16, TEXT_2)
            button(d, X0 + 388, y0 + 8, "Find art", "primary", w=104, h=32, icon_="search")
            with d.group("Find-art status"):
                d.text(X0 + 508, y0 + 20, "Done · 7 pressings searched · 14 s", 11.5, 600, GOOD_TEXT)
                progress(d, X0 + 508, y0 + 30, LW - 524, 1.0, bm.GOOD)
            with d.group("Find-art notes"):
                d.box(X0 + 16, y0 + 60, LW - 32, 32, fill=SURFACE_ALT, stroke=None, rx=8)
                icon(d, "info", X0 + 26, y0 + 68, 15, PRIMARY, 1.5)
                d.spans(X0 + 48, y0 + 80, [("Matched ", 11.5, 400, TEXT_2), ("Columbia CK 64935 · 1997 US CD", 11.5, 650, TEXT),
                                           ("   ·   all five parts from the 1997 XE pressing   ·   ", 11.5, 400, TEXT_2),
                                           ("disc label is soft", 11.5, 650, WARN_TEXT)])
            d.hline(X0 + 16, X0 + LW - 16, y0 + 104, BORDER_SOFT)
            RH = 128
            for i, ((pk, pn), (src, dpi, px, warn, nalt)) in enumerate(zip(PARTS, v2.SCANS)):
                yy = y0 + 110 + i * RH
                with d.group("Scan row · %s" % pn):
                    if warn:
                        d.box(X0 + 10, yy, LW - 20, RH - 6, fill=WARN_TINT, stroke=WARN_LINE, rx=10)
                    tw_, th = (100, 100) if pk not in ("back", "tray") else (128, 100)
                    tx, ty = X0 + 22, yy + 11
                    d.box(tx - 3, ty - 3, tw_ + 6, th + 6, fill=None, stroke=PRIMARY, rx=7, sw=2)
                    scan(d, tx, ty, tw_, th, pk, 0, rx=4, soft=warn)
                    ix = X0 + 176
                    d.text(ix, yy + 28, pn, 14, 650, TEXT)
                    d.text(ix, yy + 47, src, 12, 500, TEXT_2)
                    cx = ix
                    if warn:
                        cx += chip(d, cx, yy + 56, dpi + " · below 200", AMBER, TEXT, h=20, size=11, icon_="alert", rx=5) + 8
                    else:
                        cx += chip(d, cx, yy + 56, dpi, GOOD_TINT, GOOD_TEXT, h=20, size=11, rx=5) + 8
                    d.text(cx, yy + 70, px, 11, 400, TEXT_3)
                    section_label(d, ix, yy + 100, "Other scans")
                    ax = ix + 92
                    for k in range(nalt):
                        aw, ah = (38, 38) if pk not in ("back", "tray") else (48, 38)
                        scan(d, ax, yy + 80, aw, ah, pk, k + 1, rx=3)
                        d.box(ax, yy + 80, aw, ah, fill=None, stroke=BORDER, rx=3)
                        ax += aw + 8
                    d.text(ax + 4, yy + 103, "+ search more", 11, 600, PRIMARY)
                    if i < 4 and not warn and not v2.SCANS[i + 1][3]:
                        d.hline(X0 + 16, X0 + LW - 16, yy + RH - 3, BORDER_SOFT)
    RX, RW = X0 + LW + 16, XW - LW - 16
    with d.layer("Case preview"):
        with d.group("Card · Case preview"):
            card(d, RX, TOP, RW, BOTTOM - TOP, "Case preview")
            segmented(d, RX + RW - 16 - 206, TOP + 10, ["Outside", "Inside", "Disc"], "Outside", h=28,
                      widths=[72, 64, 64], label="Preview page")
            case_page_outside(d, RX + 16, TOP + 52, RW - 32, 520)
            chip(d, RX + 16, TOP + 586, "Disc label is soft (150 dpi)", WARN_TINT, WARN_TEXT, h=22, size=11, icon_="alert", rx=6)
            d.hline(RX + 16, RX + RW - 16, TOP + 622, BORDER_SOFT)
            section_label(d, RX + 16, TOP + 646, "Print")
            d.text(RX + RW - 16, TOP + 646, "Letter · 1.5 mm bleed · 3 pages", 11, 500, TEXT_3, anchor="end")
            half = (RW - 40) / 2
            button(d, RX + 16, TOP + 660, "Make PDF", "primary", w=half, h=36, icon_="printer")
            button(d, RX + 24 + half, TOP + 660, "Open PDF", "secondary", w=half, h=36, icon_="external")
            button(d, RX + 16, TOP + 706, "Save scans to album folder", "secondary", w=RW - 32, h=36, icon_="folder")


# --------------------------------------------------------------------------
# A3 · Library
# --------------------------------------------------------------------------
def screen_A3(d):
    frame(d, "Option A · Workspace · Library", "Library")
    LH = 402
    with d.layer("Library"):
        with d.group("Card · Library"):
            card(d, X0, TOP, XW, LH, "Library", "5 albums · 71 tracks · 2.4 GB · D:\\Music")
            field(d, X0 + XW - 16 - 112 - 12 - 280, TOP + 10, 280, "Filter albums…", h=30, icon_="search",
                  placeholder=True, size=12, label="Field · Filter")
            button(d, X0 + XW - 16, TOP + 10, "Rescan", "secondary", w=112, h=30, size=12, icon_="refresh", anchor="end")
            cols = [("Artist", 72), ("Album", 250), ("Year", 470), ("Format", 520), ("Tracks", 710), ("Cover", 742),
                    ("Issues", 806)]
            for t, off in cols:
                d.text(X0 + off, TOP + 72, t.upper(), 10, 700, TEXT_3, anchor="end" if t == "Tracks" else None, ls=.5)
            d.hline(X0 + 16, X0 + XW - 16, TOP + 80, BORDER)
            for i, a in enumerate(LIBRARY):
                yy = TOP + 82 + i * 62
                with d.group("Library row · %s" % a["album"]):
                    sel = a["key"] == "okc"
                    if sel:
                        d.rect(X0 + 8, yy + 2, XW - 16, 58, fill=PRIMARY_TINT, rx=8)
                        d.rect(X0 + 8, yy + 15, 3, 32, fill=PRIMARY, rx=1.5)
                    if a["cover"]:
                        cover(d, X0 + 16, yy + 9, 44, a["key"], 5)
                    else:
                        d.box(X0 + 16, yy + 9, 44, 44, fill=SURFACE_SUNK, stroke=BORDER, rx=5, dash="3 2")
                        icon(d, "image", X0 + 30, yy + 23, 16, TEXT_3)
                    d.text(X0 + 72, yy + 36, a["artist"], 13, 500, TEXT)
                    d.text(X0 + 250, yy + 36, a["album"], 13, 650, TEXT)
                    d.text(X0 + 470, yy + 36, a["year"], 12.5, 400, TEXT_2)
                    d.text(X0 + 520, yy + 36, a["fmt"], 12, 600, GOOD_TEXT if a["fmt"].startswith("FLAC") else bm.BAD_TEXT)
                    d.text(X0 + 710, yy + 36, str(a["tracks"]), 12.5, 400, TEXT_2, anchor="end")
                    status_icon(d, X0 + 750, yy + 24, True if a["cover"] else False, 14)
                    issues = v2.LIB_ISSUES[a["key"]]
                    if issues:
                        cx = X0 + 806
                        for kind, t in issues:
                            cx += v2.issue_chip(d, cx, yy + 20, kind, t) + 8
                        button(d, X0 + XW - 16, yy + 17, "Fix", "secondary", h=28, size=12, anchor="end")
                    else:
                        status_icon(d, X0 + 806, yy + 24, True, 14)
                        d.text(X0 + 828, yy + 36, "No issues", 12, 500, GOOD_TEXT)
                    if i < 4 and not sel and LIBRARY[i + 1]["key"] != "okc":
                        d.hline(X0 + 16, X0 + XW - 16, yy + 62, BORDER_SOFT)
    with d.layer("Album view"):
        with d.group("Card · Album view"):
            AY = TOP + LH + 16
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
                    ty = AY + 86
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
                        status_icon(d, bx + half - 14, yy + 9, "warn" if bad else True, 14)


# --------------------------------------------------------------------------
# A4 · Settings: the round-1 screen, moved under the top bar
# --------------------------------------------------------------------------
def _cut_layer(body, label):
    """Remove the layer <g ... inkscape:label="label"> ... </g> (with its nested groups) from an SVG fragment."""
    start = body.find('inkscape:label="%s"' % label)
    if start == -1:
        return body
    g0 = body.rfind("<g ", 0, start)
    depth, i = 0, g0
    while True:
        o, c = body.find("<g ", i + 1), body.find("</g>", i + 1)
        if o != -1 and o < c:
            depth, i = depth + 1, o
        elif depth == 0:
            return body[:g0] + body[c + 4:]
        else:
            depth, i = depth - 1, c


def screen_A4(d):
    inner = Doc(d.key, d.title)
    bm.screen_A4(inner)
    body = inner.body()
    for label in ("Navigation", "Activity strip", "Background", "Title bar"):
        body = _cut_layer(body, label)
    # drop the page heading (the top bar says where you are) and slide the cards up under the bar
    body = body.replace(">Settings</text>", "></text>", 1).replace(">Changes are saved automatically.</text>", "></text>", 1)
    frame(d, "Option A · Workspace · Settings", "Settings")
    d.raw('<g inkscape:groupmode="layer" id="a4-layer-settings-content" inkscape:label="Settings" '
          'transform="translate(-100,-32)">%s</g>' % body)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
SCREENS = [("A1", "A1-search.svg", "Option A · Workspace · Album Search", screen_A1),
           ("A2", "A2-cd-art.svg", "Option A · Workspace · CD Art", screen_A2),
           ("A3", "A3-library.svg", "Option A · Workspace · Library", screen_A3),
           ("A4", "A4-settings.svg", "Option A · Workspace · Settings", screen_A4)]


def build():
    bodies = {}
    for key, fname, title, fn in SCREENS:
        d = Doc(key, title)
        fn(d)
        bodies[key] = d.body()
        nv = ('<sodipodi:namedview id="%s-namedview" pagecolor="#ffffff" bordercolor="#666666" '
              'inkscape:document-units="px" inkscape:pagecheckerboard="false">'
              '<inkscape:page x="0" y="0" width="%d" height="%d" id="%s-page" inkscape:label="%s"/>'
              '</sodipodi:namedview>') % (key.lower(), W, H, key.lower(), title)
        with open(os.path.join(HERE, fname), "w", encoding="utf-8") as f:
            f.write(bm.svg_doc(d.body(), W, H, "iaflac · " + title, nv, "".join(d.defs)))

    for p, uri in v2.NSMAP.items():
        ET.register_namespace(p, uri)
    tree = ET.parse(v2.USER_FILE)
    root = tree.getroot()
    dropped = ("B1", "B2", "C1", "C2")              # emptied by the user in round 1 (Option A chosen)
    for g in list(root):
        code = g.get(v2.INK_LABEL, "").split(" ")[0]
        if g.get(v2.INK_MODE) != "layer":
            continue
        if code in dropped:
            root.remove(g)
        elif code in bodies:
            frag = ET.fromstring('<svg xmlns="%s" xmlns:inkscape="%s" xmlns:sodipodi="%s">%s</svg>'
                                 % (v2.NSMAP[""], v2.NSMAP["inkscape"], v2.NSMAP["sodipodi"], bodies[code]))
            for ch in list(g):
                g.remove(ch)
            for ch in list(frag):
                g.append(ch)
        elif g.get(v2.INK_LABEL) == "Page labels":
            for t in list(g):
                if "".join(t.itertext()).split(" ")[0] in dropped:
                    g.remove(t)
    nv = root.find("{%s}namedview" % v2.NSMAP["sodipodi"])
    for page in list(nv):
        if page.get(v2.INK_LABEL, "").split(" ")[0] in dropped:
            nv.remove(page)
    tree.write(OUT_FILE, encoding="UTF-8", xml_declaration=True)
    print("wrote %s and %d single screens" % (OUT_FILE, len(SCREENS)))


if __name__ == "__main__":
    build()
