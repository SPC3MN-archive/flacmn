"""The fallback when an album has no FLAC on the Internet Archive: WAV files (lossless; converted to FLAC when saved)
and IA's 'VBR MP3' files (lossy; kept as MP3 and labelled as such).

Only headers are read here, over HTTP Range requests, like the FLAC probe. There is no decode test or spectrum check
for these: the download is checked against archive.org's md5/sha1, and a WAV is verified again when it is encoded.
"""

from __future__ import annotations

import io
import struct
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import provenance as prov
from .ia_client import IAError

SECTOR = 588                       # samples per CD sector
HEAD_BYTES = 65536
PCM_TAGS = (1, 3, 0xFFFE)          # PCM, IEEE float, extensible


@dataclass
class AltProbe:
    ok: bool
    kind: str                       # "wav" | "mp3" | what the file really is
    detail: str = ""
    sample_rate: int = 0
    channels: int = 0
    bits: int = 0
    samples: int = 0                # wav: frames in the data chunk
    kbps: float = 0.0               # mp3: average bitrate
    mode: str = ""                  # mp3: VBR / CBR / ABR / unknown

    @property
    def cd_format(self) -> bool:
        return self.kind == "wav" and (self.sample_rate, self.bits, self.channels) == (44100, 16, 2)

    @property
    def sector_aligned(self) -> bool:
        return self.cd_format and self.samples > 0 and self.samples % SECTOR == 0

    def describe(self) -> str:
        if not self.ok:
            return f"not {self.kind}: {self.detail}"
        if self.kind == "wav":
            return f"{self.sample_rate / 1000:g} kHz / {self.bits}-bit / {'stereo' if self.channels == 2 else f'{self.channels} ch'}"
        return f"MP3 {self.mode or 'VBR'} ~{self.kbps:.0f} kbps, {self.sample_rate / 1000:g} kHz"


def sniff(head: bytes) -> str:
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "mp3"
    if head[:4] == b"OggS":
        return "ogg"
    return "unknown"


def probe_wav(head: bytes, file_size: int) -> AltProbe:
    if sniff(head) != "wav":
        return AltProbe(False, sniff(head), "no RIFF/WAVE header")
    pos, fmt, data_at, data_len = 12, None, None, 0
    while pos + 8 <= len(head):
        tag, length = head[pos:pos + 4], struct.unpack("<I", head[pos + 4:pos + 8])[0]
        if tag == b"fmt " and pos + 8 + 16 <= len(head):
            fmt = struct.unpack("<HHIIHH", head[pos + 8:pos + 24])
        elif tag == b"data":
            data_at, data_len = pos + 8, length
            break
        pos += 8 + length + (length & 1)
    if fmt is None:
        return AltProbe(False, "wav", "no fmt chunk in the first 64 KB")
    tag, channels, rate, _byte_rate, block, bits = fmt
    if tag not in PCM_TAGS:
        return AltProbe(False, "wav", f"not PCM (format tag {tag}: a compressed WAV)")
    if data_at is None:
        return AltProbe(False, "wav", "no data chunk in the first 64 KB")
    available = max(0, file_size - data_at) if file_size else data_len
    if data_len in (0, 0xFFFFFFFF) or (file_size and data_len > available):
        data_len = available                 # streamed/unfinalised header: trust the file size
    if not (block and channels and rate and bits):
        return AltProbe(False, "wav", "incomplete fmt chunk")
    return AltProbe(True, "wav", sample_rate=rate, channels=channels, bits=bits, samples=data_len // block)


def probe_mp3(head: bytes, file_size: int, length: Optional[float]) -> AltProbe:
    if sniff(head) != "mp3":
        return AltProbe(False, sniff(head), "no MP3 header")
    from mutagen.mp3 import MP3, HeaderNotFoundError
    try:
        info = MP3(io.BytesIO(head)).info
    except (HeaderNotFoundError, Exception) as exc:            # mutagen raises several types on damaged input
        return AltProbe(False, "mp3", f"unreadable MP3 header ({exc})")
    mode = str(getattr(info.bitrate_mode, "name", "") or "").upper()
    kbps = (file_size * 8 / length / 1000) if file_size and length else (info.bitrate or 0) / 1000
    return AltProbe(True, "mp3", sample_rate=info.sample_rate, channels=info.channels, kbps=kbps,
                    mode="VBR" if mode in ("", "UNKNOWN") else mode)


def probe_file(media: str, fetch: Callable[[int, int], bytes], size: int, length: Optional[float]) -> AltProbe:
    try:
        head = fetch(0, HEAD_BYTES)
    except IAError as exc:
        return AltProbe(False, "error", str(exc))
    return probe_wav(head, size) if media == "wav" else probe_mp3(head, size, length)


@dataclass
class AltStats:
    media: str
    n: int = 0
    ok: int = 0
    bad: list = field(default_factory=list)          # [(name, kind, detail)]
    aligned: int = 0
    cd_format: int = 0
    formats: Counter = field(default_factory=Counter)
    kbps: float = 0.0

    def format_label(self) -> str:
        return self.formats.most_common(1)[0][0] if self.formats else "?"

    @property
    def aligned_ratio(self) -> float:
        return self.aligned / self.ok if self.ok else 0.0

    flac_type: str = "FLAC (16-bit/44.1kHz)"         # what a WAV becomes, from the probed bit depth and rate

    @property
    def file_type(self) -> str:
        """The inventory's File Type for what will be saved (a WAV is saved as FLAC)."""
        return self.flac_type if self.media == "wav" else f"MP3 (VBR {round(self.kbps)}kbps)"


def build_stats(media: str, probes: dict) -> AltStats:
    st = AltStats(media, n=len(probes))
    kbps, types = [], Counter()
    for name, p in probes.items():
        if not p.ok:
            st.bad.append((name, p.kind, p.detail))
            continue
        st.ok += 1
        st.formats[p.describe()] += 1
        if media == "wav":
            st.aligned += p.sector_aligned
            st.cd_format += p.cd_format
            types[(p.bits, p.sample_rate)] += 1
        else:
            kbps.append(p.kbps)
    if types:
        (bits, rate), _ = types.most_common(1)[0]
        st.flac_type = f"FLAC ({bits}-bit/{rate / 1000:.1f}kHz)"
    if kbps:
        st.kbps = sum(kbps) / len(kbps)
    return st


def assess(item, stats: AltStats, issues: list) -> prov.Provenance:
    """Tier for a WAV / VBR MP3 candidate: B when the WAVs look like CD rips (CD format, sector-aligned tracks), C for
    other real lossless, L for MP3 (lossy: usable, but not lossless), D when the files are not what they claim to be."""
    reasons, hard = [], []
    if stats.bad:
        kinds = ", ".join(sorted({k for _, k, _ in stats.bad}))
        hard.append(f"{len(stats.bad)}/{stats.n} files are not real {'WAV' if stats.media == 'wav' else 'MP3'} files ({kinds})")
    if stats.media == "wav":
        if stats.ok:
            reasons.append(f"✓ {stats.ok}/{stats.n} headers are real PCM WAV ({stats.format_label()}); will be encoded to FLAC")
        if stats.ok >= prov.ALIGNED_MIN_TRACKS and stats.aligned_ratio >= prov.ALIGNED_MIN_RATIO:
            reasons.append(f"✓ {stats.aligned}/{stats.ok} tracks are a whole number of CD sectors (588 samples), CD format")
            tier, score = "B", 30
        else:
            reasons.append(f"• not shown to be a CD rip ({stats.aligned}/{stats.ok} tracks sector-aligned)")
            tier, score = "C", 10
    else:
        reasons.append(f"• lossy: {stats.format_label()}; it can never be better than the MP3 it is")
        tier, score = "L", 0
    claims = prov.scan_claims(item)
    if claims["cd"]:
        reasons.append("• description mentions " + ", ".join(claims["cd"][:3]))
    if claims["other"]:
        reasons.append("• description points to " + ", ".join(claims["other"]))
    if claims["fake"]:
        reasons.append("• description mentions " + ", ".join(claims["fake"]))
    reasons += [f"• {i}" for i in issues]
    if hard:
        return prov.Provenance("D", 0, [f"✗ {h}" for h in hard] + reasons, hard)
    return prov.Provenance(tier, score, reasons, [])
