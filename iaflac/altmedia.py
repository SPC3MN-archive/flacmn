"""Every audio format other than FLAC: the lossless ones (WAV, AIFF, ALAC in .m4a, Monkey's Audio, WavPack), which are
converted to FLAC when saved, and the lossy ones (MP3, AAC in .m4a), which are kept as they are and labelled lossy.

Only headers are read here, over HTTP Range requests, like the FLAC probe (an .m4a's codec sits in its 'moov' box,
which may be at the end of the file: then the end is read too). There is no decode test or spectrum check for these
until they are downloaded: the download is checked against archive.org's md5/sha1, and a lossless file is verified
again when it is converted to FLAC.
"""

from __future__ import annotations

import io
import math
import struct
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import provenance as prov
from .ia_client import IAError

SECTOR = 588                       # samples per CD sector
HEAD_BYTES = 65536
TAIL_BYTES = 1 << 20               # an .m4a whose 'moov' box is at the end: read this much of the end
PCM_TAGS = (1, 3, 0xFFFE)          # PCM, IEEE float, extensible


@dataclass(frozen=True)
class Media:
    key: str                       # ItemInfo.media
    name: str                      # how it is named to people
    exts: tuple                    # the track files' extensions
    query: str                     # archive.org's format field for it
    lossless: Optional[bool]       # None: decided per file (an .m4a is ALAC or AAC)


# the formats looked for next to FLAC, lossless first (no lossless source or format is preferred over another)
MEDIA = {
    "wav": Media("wav", "WAV", (".wav", ".wave"), "format:WAVE", True),
    "aiff": Media("aiff", "AIFF", (".aiff", ".aif", ".aifc"), "format:AIFF", True),
    "m4a": Media("m4a", "M4A", (".m4a",), '(format:"Apple Lossless Audio" OR format:"MPEG-4 Audio")', None),
    "ape": Media("ape", "APE", (".ape",), 'format:"Monkey\'s Audio"', True),
    "wv": Media("wv", "WavPack", (".wv",), "format:WavPack", True),
    "mp3": Media("mp3", "MP3", (".mp3",), 'format:"VBR MP3"', False),
}
CODEC_NAME = {"pcm": "PCM", "alac": "ALAC", "aac": "AAC", "ape": "APE", "wavpack": "WavPack", "mp3": "MP3"}


@dataclass
class AltProbe:
    ok: bool
    kind: str                       # the media it was probed as, or what the file really is
    detail: str = ""
    sample_rate: int = 0
    channels: int = 0
    bits: int = 0
    samples: int = 0                # frames of audio, when the header says
    kbps: float = 0.0               # lossy: average bitrate
    mode: str = ""                  # mp3: VBR / CBR / ABR / unknown
    codec: str = ""                 # pcm | alac | aac | ape | wavpack | mp3
    lossless: bool = False

    @property
    def cd_format(self) -> bool:
        return self.lossless and (self.sample_rate, self.bits, self.channels) == (44100, 16, 2)

    @property
    def sector_aligned(self) -> bool:
        return self.cd_format and self.samples > 0 and self.samples % SECTOR == 0

    def describe(self) -> str:
        if not self.ok:
            return f"not {self.kind}: {self.detail}"
        if self.lossless:
            ch = "stereo" if self.channels == 2 else f"{self.channels} ch"
            return f"{CODEC_NAME.get(self.codec, self.codec.upper())} {self.sample_rate / 1000:g} kHz / {self.bits}-bit / {ch}"
        if self.codec == "aac":
            return f"AAC ~{self.kbps:.0f} kbps, {self.sample_rate / 1000:g} kHz"
        return f"MP3 {self.mode or 'VBR'} ~{self.kbps:.0f} kbps, {self.sample_rate / 1000:g} kHz"


def sniff(head: bytes) -> str:
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if head[4:8] == b"ftyp":
        return "m4a"
    if head[:4] == b"MAC ":
        return "ape"
    if head[:4] == b"wvpk":
        return "wv"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "mp3"
    if head[:4] == b"OggS":
        return "ogg"
    return "unknown"


# ---- WAV ----------------------------------------------------------------------------------------------------------
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
    return AltProbe(True, "wav", sample_rate=rate, channels=channels, bits=bits, samples=data_len // block,
                    codec="pcm", lossless=True)


# ---- AIFF ---------------------------------------------------------------------------------------------------------
def _extended(b: bytes) -> float:
    """An 80-bit IEEE 754 extended float (AIFF's sample rate)."""
    exp, mant = struct.unpack(">HQ", b[:10])
    sign = -1 if exp & 0x8000 else 1
    exp &= 0x7FFF
    if exp == 0 and mant == 0:
        return 0.0
    return sign * mant * math.pow(2, exp - 16383 - 63)


def probe_aiff(head: bytes) -> AltProbe:
    if sniff(head) != "aiff":
        return AltProbe(False, sniff(head), "no FORM/AIFF header")
    aifc = head[8:12] == b"AIFC"
    pos = 12
    while pos + 8 <= len(head):
        tag, length = head[pos:pos + 4], struct.unpack(">I", head[pos + 4:pos + 8])[0]
        if tag == b"COMM" and pos + 8 + 18 <= len(head):
            channels, frames, bits = struct.unpack(">HIH", head[pos + 8:pos + 16])
            rate = round(_extended(head[pos + 16:pos + 26]))
            if aifc:
                comp = head[pos + 26:pos + 30]
                if comp not in (b"NONE", b"sowt", b"twos", b"in24", b"in32", b"fl32", b"FL32"):
                    return AltProbe(False, "aiff", f"compressed AIFF-C ({comp.decode('latin-1', 'replace')})")
            if not (channels and rate and bits):
                return AltProbe(False, "aiff", "incomplete COMM chunk")
            return AltProbe(True, "aiff", sample_rate=rate, channels=channels, bits=bits, samples=frames,
                            codec="pcm", lossless=True)
        pos += 8 + length + (length & 1)
    return AltProbe(False, "aiff", "no COMM chunk in the first 64 KB")


# ---- M4A: ALAC (lossless) or AAC (lossy), read from the 'moov' box ---------------------------------------------------
def _boxes(buf: bytes, start: int = 0, end: Optional[int] = None):
    """(type, payload start, payload end) for each box in buf[start:end]."""
    pos, end = start, len(buf) if end is None else end
    while pos + 8 <= end:
        size, kind = struct.unpack(">I4s", buf[pos:pos + 8])
        head = 8
        if size == 1 and pos + 16 <= end:
            size, head = struct.unpack(">Q", buf[pos + 8:pos + 16])[0], 16
        elif size == 0:
            size = end - pos
        if size < head:
            return
        yield kind, pos + head, min(pos + size, end)
        pos += size


def _child(buf: bytes, start: int, end: int, path: list) -> Optional[tuple]:
    for kind, a, b in _boxes(buf, start, end):
        if kind == path[0]:
            return (a, b) if len(path) == 1 else _child(buf, a, b, path[1:])
    return None


def _find_moov(fetch: Callable[[int, int], bytes], head: bytes, file_size: int) -> Optional[bytes]:
    """The 'moov' box: in the head, or wherever the top-level boxes say it is (usually after 'mdat', at the end)."""
    pos = 0
    while True:
        if pos + 16 > len(head):
            if not file_size or pos >= file_size:
                return None
            chunk = fetch(pos, 16)
            size, kind = struct.unpack(">I4s", chunk[:8])
            if size == 1:
                size = struct.unpack(">Q", chunk[8:16])[0]
        else:
            size, kind = struct.unpack(">I4s", head[pos:pos + 8])
            if size == 1:
                size = struct.unpack(">Q", head[pos + 8:pos + 16])[0]
        if size < 8:
            return None
        if kind == b"moov":
            if size > 16 * TAIL_BYTES:
                return None
            if pos + size <= len(head):
                return head[pos:pos + size]
            return fetch(pos, size)
        pos += size


def probe_m4a(fetch: Callable[[int, int], bytes], head: bytes, file_size: int, length: Optional[float]) -> AltProbe:
    if sniff(head) != "m4a":
        return AltProbe(False, sniff(head), "no MP4 'ftyp' header")
    try:
        moov = _find_moov(fetch, head, file_size)
    except (IAError, struct.error) as exc:
        return AltProbe(False, "m4a", f"could not read the file's index ({exc})")
    if not moov:
        return AltProbe(False, "m4a", "no 'moov' box: not a playable MP4")
    for kind, a, b in _boxes(moov, 8):
        if kind != b"trak":
            continue
        mdhd = _child(moov, a, b, [b"mdia", b"mdhd"])
        stsd = _child(moov, a, b, [b"mdia", b"minf", b"stbl", b"stsd"])
        if not stsd:
            continue
        entry = stsd[0] + 8                                     # FullBox header + entry count
        if entry + 36 > stsd[1]:
            continue
        codec = moov[entry + 4:entry + 8]
        if codec not in (b"alac", b"mp4a"):
            continue
        channels, bits = struct.unpack(">HH", moov[entry + 24:entry + 28])
        rate = struct.unpack(">I", moov[entry + 32:entry + 36])[0] >> 16
        seconds, samples = 0.0, 0
        if mdhd:
            v = moov[mdhd[0]]
            if v == 1:
                scale, dur = struct.unpack(">IQ", moov[mdhd[0] + 20:mdhd[0] + 32])
            else:
                scale, dur = struct.unpack(">II", moov[mdhd[0] + 12:mdhd[0] + 20])
            if scale:
                seconds = dur / scale
                samples = dur if scale == rate else round(seconds * rate)
        if codec == b"alac":
            cfg = _child(moov, entry + 36, stsd[1], [b"alac"])  # ALACSpecificConfig, after the FullBox header
            if cfg and cfg[1] - cfg[0] >= 28:
                c = cfg[0] + 4
                bits = moov[c + 5]
                channels = moov[c + 9]
                rate = struct.unpack(">I", moov[c + 20:c + 24])[0] or rate
            return AltProbe(True, "m4a", sample_rate=rate, channels=channels, bits=bits, samples=samples,
                            codec="alac", lossless=True)
        secs = seconds or length or 0
        kbps = (file_size * 8 / secs / 1000) if file_size and secs else 0.0
        return AltProbe(True, "m4a", sample_rate=rate, channels=channels, kbps=kbps, codec="aac", lossless=False)
    return AltProbe(False, "m4a", "no ALAC or AAC audio track")


# ---- Monkey's Audio -----------------------------------------------------------------------------------------------
def probe_ape(head: bytes) -> AltProbe:
    if sniff(head) != "ape":
        return AltProbe(False, sniff(head), "no 'MAC ' header")
    version = struct.unpack("<H", head[4:6])[0]
    try:
        if version >= 3980:                                  # APE_DESCRIPTOR, then APE_HEADER
            desc = struct.unpack("<I", head[8:12])[0]
            _level, _flags, per_frame, final, frames, bits, channels, rate = struct.unpack("<HHIIIHHI", head[desc:desc + 24])
        else:
            _level, flags, channels, rate = struct.unpack("<HHHI", head[6:16])
            frames, final = struct.unpack("<II", head[24:32])
            bits = 8 if flags & 1 else 24 if flags & 8 else 16
            per_frame = 73728 * 4 if version >= 3950 else 73728 if version >= 3900 else 9216
    except struct.error:
        return AltProbe(False, "ape", "incomplete header")
    if not (channels and rate and bits):
        return AltProbe(False, "ape", "incomplete header")
    samples = (frames - 1) * per_frame + final if frames else 0
    return AltProbe(True, "ape", sample_rate=rate, channels=channels, bits=bits, samples=samples, codec="ape", lossless=True)


# ---- WavPack ------------------------------------------------------------------------------------------------------
WV_RATES = (6000, 8000, 9600, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000, 64000, 88200, 96000, 192000)


def probe_wv(head: bytes) -> AltProbe:
    if sniff(head) != "wv":
        return AltProbe(False, sniff(head), "no 'wvpk' block")
    try:
        _size, _ver, idx_hi, tot_hi, total, _idx, _block, flags = struct.unpack("<IHBBIIII", head[4:28])
    except struct.error:
        return AltProbe(False, "wv", "incomplete block header")
    if flags & 0x8:                                          # HYBRID_FLAG: lossy unless a .wvc correction file is used
        return AltProbe(False, "wv", "hybrid WavPack (lossy without its .wvc correction file)")
    rate_i = (flags >> 23) & 0xF
    rate = WV_RATES[rate_i] if rate_i < len(WV_RATES) else 0
    bits = ((flags & 3) + 1) * 8
    channels = 1 if flags & 0x4 else 2
    samples = total + (tot_hi << 32) if total != 0xFFFFFFFF else 0
    if not rate:
        return AltProbe(False, "wv", "sample rate not in the header")
    return AltProbe(True, "wv", sample_rate=rate, channels=channels, bits=bits, samples=samples, codec="wavpack", lossless=True)


# ---- MP3 ----------------------------------------------------------------------------------------------------------
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
                    mode="VBR" if mode in ("", "UNKNOWN") else mode, codec="mp3")


def probe_file(media: str, fetch: Callable[[int, int], bytes], size: int, length: Optional[float]) -> AltProbe:
    try:
        head = fetch(0, HEAD_BYTES)
    except IAError as exc:
        return AltProbe(False, "error", str(exc))
    if media == "wav":
        return probe_wav(head, size)
    if media == "aiff":
        return probe_aiff(head)
    if media == "m4a":
        return probe_m4a(fetch, head, size, length)
    if media == "ape":
        return probe_ape(head)
    if media == "wv":
        return probe_wv(head)
    end = id3_end(head)
    if end and end + 4096 > len(head):              # a big ID3 tag (cover art) before the audio: read just past it
        try:
            head = fetch(end, HEAD_BYTES).lstrip(b"\x00")   # some taggers pad past the stated size
        except IAError as exc:
            return AltProbe(False, "error", str(exc))
    return probe_mp3(head, size, length)


def id3_end(head: bytes) -> int:
    """Where the audio starts after an ID3v2 tag (its size is four 7-bit bytes, plus 10 for the header and a footer)."""
    if len(head) < 10 or head[:3] != b"ID3":
        return 0
    size = (head[6] & 0x7F) << 21 | (head[7] & 0x7F) << 14 | (head[8] & 0x7F) << 7 | (head[9] & 0x7F)
    return 10 + size + (10 if head[5] & 0x10 else 0)


# ---- the album --------------------------------------------------------------------------------------------------
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
    mode: str = ""                                   # mp3: the most common of VBR / CBR / ABR
    codec: str = ""                                  # the most common codec: pcm, alac, aac, ape, wavpack, mp3
    lossless: bool = False                           # every readable file is lossless
    bits: int = 0                                    # lossless: the most common bit depth and sample rate
    rate: int = 0

    def format_label(self) -> str:
        return self.formats.most_common(1)[0][0] if self.formats else "?"

    @property
    def aligned_ratio(self) -> float:
        return self.aligned / self.ok if self.ok else 0.0

    flac_type: str = "FLAC (16-bit/44.1kHz)"         # what a lossless upload becomes, from the probed bit depth and rate

    @property
    def file_type(self) -> str:
        """The inventory's File Type for what will be saved (a lossless file is saved as FLAC)."""
        if self.lossless:
            return self.flac_type
        if self.codec == "aac":
            return f"AAC ({round(self.kbps)}kbps)"
        return f"MP3 ({self.mode or 'VBR'} {round(self.kbps)}kbps)"


def build_stats(media: str, probes: dict) -> AltStats:
    st = AltStats(media, n=len(probes))
    kbps, types, modes, codecs, lossless = [], Counter(), Counter(), Counter(), []
    for name, p in probes.items():
        if not p.ok:
            st.bad.append((name, p.kind, p.detail))
            continue
        st.ok += 1
        st.formats[p.describe()] += 1
        codecs[p.codec] += 1
        lossless.append(p.lossless)
        if p.lossless:
            st.aligned += p.sector_aligned
            st.cd_format += p.cd_format
            types[(p.bits, p.sample_rate)] += 1
        else:
            kbps.append(p.kbps)
            if p.codec == "mp3":
                modes[p.mode or "VBR"] += 1
    if codecs:
        st.codec = codecs.most_common(1)[0][0]
    st.lossless = bool(lossless) and all(lossless)
    if st.ok and any(lossless) and not all(lossless):
        st.bad.append(("", "mixed", "some files are lossless and some lossy"))
    if modes:
        st.mode = modes.most_common(1)[0][0]
    if types:
        (bits, rate), _ = types.most_common(1)[0]
        st.bits, st.rate = bits, rate
        st.flac_type = f"FLAC ({bits}-bit/{rate / 1000:.1f}kHz)"
    if kbps:
        st.kbps = sum(kbps) / len(kbps)
    return st


def is_lossless(media: str, stats: Optional[AltStats] = None) -> bool:
    """Is this upload lossless? Fixed for most formats; for .m4a it is what the files turned out to be (ALAC or AAC),
    and an untested .m4a is not called lossless."""
    m = MEDIA.get(media)
    if m is None:
        return media == "flac"
    if m.lossless is not None:
        return m.lossless
    return bool(stats and stats.ok and stats.lossless)


def assess(item, stats: AltStats, issues: list) -> prov.Provenance:
    """Tier for an upload in another format: B for real lossless files with some evidence of a clean source
    (tracks a whole number of CD sectors, or a named source of any kind), C for other real lossless, L for lossy (MP3,
    AAC: usable, but never better than they are), D when the files are not what they claim to be. No lossless source
    or format is preferred over another."""
    reasons, hard = [], []
    word = MEDIA[stats.media].name if stats.media in MEDIA else stats.media.upper()
    if stats.bad:
        kinds = ", ".join(sorted({k for _, k, _ in stats.bad}))
        hard.append(f"{len(stats.bad)}/{stats.n} files are not real {word} files ({kinds})")
    if stats.lossless:
        what = word if stats.codec == "pcm" else CODEC_NAME.get(stats.codec, word)      # WAV, AIFF; ALAC, APE, WavPack
        if stats.ok:
            reasons.append(f"✓ {stats.ok}/{stats.n} headers are real lossless {what} ({stats.format_label()}); will be converted to FLAC")
        named = prov.scan_claims(item)
        if stats.ok >= prov.ALIGNED_MIN_TRACKS and stats.aligned_ratio >= prov.ALIGNED_MIN_RATIO:
            reasons.append(f"✓ {stats.aligned}/{stats.ok} tracks are a whole number of CD sectors (588 samples): a clean extraction")
            tier, score = "B", 30
        elif named["cd"] or named["other"]:
            reasons.append("✓ the uploader names the source")
            tier, score = "B", 25
        else:
            reasons.append(f"• {stats.aligned}/{stats.ok} tracks sector-aligned; source not stated")
            tier, score = "C", 10
    else:
        what = "AAC" if stats.codec == "aac" else "MP3"
        reasons.append(f"• lossy: {stats.format_label()}; it can never be better than the {what} it is")
        tier, score = "L", 0
    claims = prov.scan_claims(item)
    if claims["cd"]:
        reasons.append("• description mentions " + ", ".join(claims["cd"][:3]))
    if claims["other"]:
        reasons.append("• description names its source: " + ", ".join(claims["other"]))
    if claims["fake"]:
        reasons.append("• description mentions " + ", ".join(claims["fake"]))
    reasons += [f"• {i}" for i in issues]
    if hard:
        return prov.Provenance("D", 0, [f"✗ {h}" for h in hard] + reasons, hard)
    return prov.Provenance(tier, score, reasons, [])
