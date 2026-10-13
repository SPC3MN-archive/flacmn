"""One ranking across every format, best audio first, for lists that mix FLAC, WAV and MP3 results.

The goal is any high-quality audio. All real lossless sorts together, whatever its source or resolution (CD rip,
download, vinyl or tape transfer; 16/44.1 or hi-res): it is labelled Lossless or Hi-res, and neither ranks above the
other. Then the best MP3s, then weaker MP3s, then anything that failed. Inside a class the provenance tier (A-D),
how sure we are the audio is genuinely lossless, breaks ties after the match to the request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

TIER_RANK = {"A": 0, "B": 1, "C": 2, "L": 3, "D": 4, "?": 5}
HIGH_MP3_KBPS = 220            # V0 / 256k / 320k territory; below this an MP3 is audibly weaker on good gear

# quality classes, best first (lossless and hi-res are one class: labelled apart, ranked together)
LOSSLESS, MP3_HIGH, MP3_LOW, FAILED = range(4)
CLASS_LABEL = {
    LOSSLESS: "Lossless",
    MP3_HIGH: "Lossy · high bitrate",
    MP3_LOW: "Lossy",
    FAILED: "Failed / suspect",
}
HIRES_LABEL = "Hi-res"


@dataclass
class Quality:
    rank: int                      # one of the classes above
    label: str                     # "Lossless", "Hi-res", "Lossy · high bitrate", ...
    format: str                    # "FLAC 16/44.1", "WAV 24/96", "MP3 VBR ~245 kbps", "FLAC" (untested)
    lossless: bool
    tested: bool                   # headers have been read (V1), so `format` is measured, not guessed

    def to_dict(self) -> dict:
        return {"rank": self.rank, "label": self.label, "format": self.format, "lossless": self.lossless,
                "tested": self.tested}


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
            return Quality(FAILED, CLASS_LABEL[FAILED], fmt, False, tested)
        label = HIRES_LABEL if bits > 16 or rate > 48_000 else CLASS_LABEL[LOSSLESS]
        return Quality(LOSSLESS, label, fmt, True, tested)
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
