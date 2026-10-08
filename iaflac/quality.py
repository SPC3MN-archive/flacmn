"""One ranking across every format, best audio first, for lists that mix FLAC, WAV and MP3 results.

The provenance tier (A-D) says how sure we are that a lossless upload came from a CD; this module adds the format
around it so a list reads FLAC/WAV CD rips, then other real lossless (hi-res before plain 16/44.1), then the best
MP3s, then weaker MP3s, then anything that failed. Before an item is tested (no header read yet) its class comes from
archive.org's own file list: a FLAC or WAV that ships a rip log or cue sheet sorts with the CD rips.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

TIER_RANK = {"A": 0, "B": 1, "C": 2, "L": 3, "D": 4, "?": 5}
HIGH_MP3_KBPS = 220            # V0 / 256k / 320k territory; below this an MP3 is audibly weaker on good gear

# quality classes, best first
CD_LOSSLESS, HIRES_LOSSLESS, LOSSLESS, MP3_HIGH, MP3_LOW, FAILED = range(6)
CLASS_LABEL = {
    CD_LOSSLESS: "Lossless · CD rip",
    HIRES_LOSSLESS: "Lossless · hi-res",
    LOSSLESS: "Lossless",
    MP3_HIGH: "Lossy · high bitrate",
    MP3_LOW: "Lossy",
    FAILED: "Failed / suspect",
}


@dataclass
class Quality:
    rank: int                      # one of the classes above
    label: str                     # "Lossless · CD rip"
    format: str                    # "FLAC 16/44.1", "WAV 24/96", "MP3 VBR ~245 kbps", "FLAC" (untested)
    lossless: bool
    tested: bool                   # headers have been read (V1), so `format` is measured, not guessed

    def to_dict(self) -> dict:
        return {"rank": self.rank, "label": self.label, "format": self.format, "lossless": self.lossless,
                "tested": self.tested}


def _rip_shaped(item) -> bool:
    return bool(item.files_of("log") or item.files_of("cue"))


def _pcm_format(media: str, bits: int, rate: int) -> str:
    name = "FLAC" if media == "flac" else "WAV"
    if not bits or not rate:
        return name
    return f"{name} {bits}/{rate / 1000:g}"


def _measured(cand) -> tuple:
    """(bits, sample rate, mp3 kbps, mp3 mode) from the header probes, zeros when not tested."""
    media = cand.item.media
    if media == "flac" and cand.stats and cand.stats.ok:
        st = cand.stats
        return st.depths.most_common(1)[0][0], st.rates.most_common(1)[0][0], 0.0, ""
    alt = cand.alt_stats
    if alt is not None and alt.ok:
        bits, rate = _alt_pcm(alt)
        return bits, rate, alt.kbps, alt.mode or "VBR"
    return 0, 0, 0.0, ""


def _alt_pcm(alt) -> tuple:
    # "FLAC (16-bit/44.1kHz)" is how altmedia records what a WAV will become
    m = re.search(r"(\d+)-bit/([\d.]+)kHz", alt.flac_type or "")
    return (int(m.group(1)), int(float(m.group(2)) * 1000)) if m and alt.media == "wav" else (0, 0)


def assess(cand) -> Quality:
    item, tier = cand.item, cand.tier
    media = item.media
    tested = tier != "?"
    bits, rate, kbps, mode = _measured(cand)
    if media in ("flac", "wav"):
        fmt = _pcm_format(media, bits, rate)
        if tier == "D":
            rank = FAILED
        elif tier in ("A", "B") or (not tested and _rip_shaped(item)):
            rank = CD_LOSSLESS
        elif bits > 16 or rate > 48_000:
            rank = HIRES_LOSSLESS
        else:
            rank = LOSSLESS
        return Quality(rank, CLASS_LABEL[rank], fmt, rank != FAILED, tested)
    # mp3
    fmt = f"MP3 {mode} ~{kbps:.0f} kbps" if kbps else "MP3"
    if tier == "D":
        rank = FAILED
    elif kbps and kbps < HIGH_MP3_KBPS:
        rank = MP3_LOW
    else:
        rank = MP3_HIGH                     # untested MP3s sit here until their headers say otherwise
    return Quality(rank, CLASS_LABEL[rank], fmt, False, tested)


def sort_key(cand) -> tuple:
    """Best audio first; inside a class, the better match to the request, then the stronger provenance, then
    the more complete-looking album, then popularity."""
    q = assess(cand)
    m = cand.match
    return (q.rank, m.bucket if m else 2, TIER_RANK.get(cand.tier, 5), -round(m.score if m else 0, 1),
            -cand.album_likeness, -(cand.provenance.score if cand.provenance else 0), -cand.item.downloads)


def rank(cands: list) -> list:
    return sorted(cands, key=sort_key)


def best(cands: list) -> Optional[object]:
    ranked = rank(cands)
    return ranked[0] if ranked else None
