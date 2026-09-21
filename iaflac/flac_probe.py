"""Identify a FLAC from its first few KB, without downloading the file.

`probe_header(fetch)` takes `fetch(start, length) -> bytes`, so it runs against HTTP Range
requests in production and against in-memory bytes in tests.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Callable, Optional

Fetch = Callable[[int, int], bytes]

CD_SECTOR_SAMPLES = 588  # one CD sector: 44100 Hz / 75 sectors per second

STREAMINFO, PADDING, APPLICATION, SEEKTABLE, VORBIS_COMMENT, CUESHEET, PICTURE = range(7)

FIRST_READ = 32 * 1024
MAX_COMMENT_BYTES = 1 << 20
MAX_BLOCKS = 32


@dataclass
class StreamInfo:
    min_blocksize: int
    max_blocksize: int
    min_framesize: int
    max_framesize: int
    sample_rate: int
    channels: int
    bits_per_sample: int
    total_samples: int
    md5: str  # hex; all zeros means the encoder did not record it

    @property
    def md5_set(self) -> bool:
        return self.md5 != "0" * 32

    @property
    def duration(self) -> float:
        return self.total_samples / self.sample_rate if self.sample_rate else 0.0

    @property
    def pcm_bytes(self) -> float:
        return self.total_samples * self.channels * self.bits_per_sample / 8

    @property
    def cd_native(self) -> bool:
        return self.sample_rate == 44100 and self.bits_per_sample == 16 and self.channels == 2

    @property
    def sector_aligned(self) -> bool:
        """Track length is a whole number of CD sectors (only meaningful at 44.1 kHz)."""
        return (self.sample_rate == 44100 and self.total_samples > 0
                and self.total_samples % CD_SECTOR_SAMPLES == 0)


@dataclass
class FlacProbe:
    kind: str                      # "flac", or what the bytes really are ("mp3", "wav", "html/text", ...)
    ok: bool                       # native FLAC, sane STREAMINFO, frame sync where audio should start
    detail: str = ""               # why ok is False
    id3_prefix: int = 0
    streaminfo: Optional[StreamInfo] = None
    blocks: list = field(default_factory=list)      # [(block_type, length)]
    audio_offset: Optional[int] = None              # absolute offset of the first audio frame
    vendor: str = ""
    tags: dict = field(default_factory=dict)        # upper-cased Vorbis comment keys
    warnings: list = field(default_factory=list)

    def has_block(self, block_type: int) -> bool:
        return any(t == block_type for t, _ in self.blocks)

    def size_ratio(self, file_size: int) -> Optional[float]:
        """Compressed audio bytes / raw PCM bytes. Real FLAC is roughly 0.3-0.9."""
        si = self.streaminfo
        if not si or not si.total_samples or not file_size:
            return None
        return (file_size - (self.audio_offset or 0)) / si.pcm_bytes

    def describe(self) -> str:
        si = self.streaminfo
        if not self.ok or not si:
            return f"NOT FLAC ({self.kind}): {self.detail}"
        return f"{si.sample_rate / 1000:g} kHz/{si.bits_per_sample}-bit/{si.channels}ch"


def sniff_type(head: bytes) -> str:
    """Name what a file really is from its first bytes."""
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"OggS":
        inner = head[28:40]
        if inner.startswith(b"\x7fFLAC"):
            return "ogg-flac"
        if inner.startswith(b"\x01vorbis"):
            return "ogg-vorbis"
        if inner.startswith(b"OpusHead"):
            return "opus"
        return "ogg"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if head[4:8] == b"ftyp":
        return "mp4/m4a"
    if head[:4] == b"MAC ":
        return "ape"
    if head[:4] == b"wvpk":
        return "wavpack"
    if head[:4] == b"ajkg":
        return "shorten"
    if head[:4] == b"TTA1":
        return "tta"
    if head[:3] == b"ID3":
        return "mp3"
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return "aac-adts" if (head[1] & 0x06) == 0 else "mp3"
    if head[:4] == b"PK\x03\x04":
        return "zip"
    if head.lstrip()[:1] in (b"<", b"{"):
        return "html/text"
    if head and not any(head[:64]):
        return "zeros"
    return "unknown"


def _syncsafe(b: bytes) -> int:
    return (b[0] & 0x7F) << 21 | (b[1] & 0x7F) << 14 | (b[2] & 0x7F) << 7 | (b[3] & 0x7F)


def parse_streaminfo(body: bytes) -> StreamInfo:
    if len(body) < 34:
        raise ValueError(f"STREAMINFO is {len(body)} bytes, expected 34")
    min_bs, max_bs = struct.unpack(">HH", body[0:4])
    packed = int.from_bytes(body[10:18], "big")
    return StreamInfo(
        min_blocksize=min_bs, max_blocksize=max_bs,
        min_framesize=int.from_bytes(body[4:7], "big"), max_framesize=int.from_bytes(body[7:10], "big"),
        sample_rate=packed >> 44, channels=((packed >> 41) & 0x7) + 1,
        bits_per_sample=((packed >> 36) & 0x1F) + 1, total_samples=packed & ((1 << 36) - 1),
        md5=body[18:34].hex())


def parse_vorbis_comment(body: bytes) -> tuple:
    if len(body) < 8:
        raise ValueError("VORBIS_COMMENT block too short")
    vlen = int.from_bytes(body[0:4], "little")
    vendor = body[4:4 + vlen].decode("utf-8", "replace")
    pos = 4 + vlen
    count = int.from_bytes(body[pos:pos + 4], "little")
    pos += 4
    tags: dict = {}
    for _ in range(min(count, 4096)):
        if pos + 4 > len(body):
            break
        clen = int.from_bytes(body[pos:pos + 4], "little")
        text = body[pos + 4:pos + 4 + clen].decode("utf-8", "replace")
        pos += 4 + clen
        if "=" in text:
            key, value = text.split("=", 1)
            tags.setdefault(key.upper(), value)
    return vendor, tags


def _check_streaminfo(si: StreamInfo, warnings: list) -> list:
    problems = []
    if si.sample_rate == 0:
        problems.append("STREAMINFO sample rate is 0")
    elif si.sample_rate > 655350:
        warnings.append(f"sample rate {si.sample_rate} Hz is above the FLAC limit of 655350")
    if si.bits_per_sample < 4:
        problems.append(f"STREAMINFO bit depth {si.bits_per_sample} is below the FLAC minimum of 4")
    if si.total_samples == 0:
        warnings.append("STREAMINFO total_samples is 0 (length not recorded)")
    if not si.md5_set:
        warnings.append("STREAMINFO MD5 is unset, so the audio hash cannot be verified")
    return problems


def _walk_blocks(fetch: Fetch, buf: bytes, base: int, probe: FlacProbe) -> None:
    """Walk metadata blocks. `buf` holds the bytes at absolute offset `base`; anything beyond it
    (a big PICTURE block, say) is skipped by reading just the next 4-byte block header."""

    def read(rel: int, n: int) -> bytes:
        return buf[rel:rel + n] if rel + n <= len(buf) else fetch(base + rel, n)

    pos, walked_to_end = 4, False
    for index in range(MAX_BLOCKS):
        hdr = read(pos, 4)
        if len(hdr) < 4:
            probe.detail = "metadata ends mid-header (file truncated?)"
            return
        last, btype, blen = hdr[0] >> 7, hdr[0] & 0x7F, int.from_bytes(hdr[1:4], "big")
        if index == 0 and btype != STREAMINFO:
            probe.detail = "first metadata block is not STREAMINFO"
            return
        if btype == 127:
            probe.detail = "invalid metadata block type 127"
            return
        probe.blocks.append((btype, blen))
        if btype == STREAMINFO or (btype == VORBIS_COMMENT and blen <= MAX_COMMENT_BYTES):
            body = read(pos + 4, blen)
            if len(body) < blen:
                probe.detail = f"metadata block type {btype} truncated ({len(body)}/{blen} bytes)"
                return
            try:
                if btype == STREAMINFO:
                    probe.streaminfo = parse_streaminfo(body)
                else:
                    probe.vendor, probe.tags = parse_vorbis_comment(body)
            except ValueError as exc:
                probe.detail = str(exc)
                return
        pos += 4 + blen
        if last:
            walked_to_end = True
            break
    if not walked_to_end:
        probe.warnings.append(f"more than {MAX_BLOCKS} metadata blocks; stopped walking")

    si = probe.streaminfo
    if si is None:
        probe.detail = "no STREAMINFO block"
        return
    problems = _check_streaminfo(si, probe.warnings)
    if problems:
        probe.detail = "; ".join(problems)
        return
    if walked_to_end:
        probe.audio_offset = base + pos
        frame = read(pos, 2)
        if not (len(frame) == 2 and frame[0] == 0xFF and (frame[1] & 0xFE) == 0xF8):
            probe.detail = "no FLAC frame sync where the audio should start (truncated or corrupt?)"
            return
    probe.ok = True


def probe_header(fetch: Fetch, first_read: int = FIRST_READ) -> FlacProbe:
    head = fetch(0, first_read)
    if len(head) < 4:
        return FlacProbe(kind="empty", ok=False, detail=f"only {len(head)} byte(s) readable")

    base = 0
    if head[:3] == b"ID3" and len(head) >= 10:
        skip = 10 + _syncsafe(head[6:10]) + (10 if head[5] & 0x10 else 0)
        after = fetch(skip, first_read)
        if after[:4] != b"fLaC":
            kind = sniff_type(after) if after else "id3-only"
            kind = "id3-unknown" if kind == "unknown" else kind
            return FlacProbe(kind=kind, ok=False, id3_prefix=skip,
                             detail=f"ID3v2 tag ({skip} bytes) followed by {kind}, not FLAC")
        head, base = after, skip

    if head[:4] != b"fLaC":
        kind = sniff_type(head)
        return FlacProbe(kind=kind, ok=False, detail=f"content looks like {kind}, not FLAC")

    probe = FlacProbe(kind="flac", ok=False, id3_prefix=base)
    _walk_blocks(fetch, head, base, probe)
    return probe


def cross_check(probe: FlacProbe, file_size: int, ia_length: Optional[float]) -> list:
    """Sanity checks that need a second source: IA's own size and duration for the same file."""
    issues = []
    si = probe.streaminfo
    if not probe.ok or not si:
        return issues
    if ia_length and si.total_samples and abs(si.duration - ia_length) > max(1.5, 0.01 * ia_length):
        issues.append(f"STREAMINFO says {si.duration:.1f}s but IA reports {ia_length:.1f}s")
    ratio = probe.size_ratio(file_size)
    if ratio is not None:
        if ratio > 1.02:
            issues.append(f"file is {ratio:.2f}x its raw PCM size; FLAC should never expand like this")
        elif ratio < 0.02:
            issues.append(f"file is only {ratio:.3f}x its raw PCM size (digital silence, or truncated)")
    return issues


def make_stream_header(si: StreamInfo) -> bytes:
    """Minimal valid FLAC header (STREAMINFO only) for decoding an isolated slice of frames.
    Length and MD5 are zeroed so a partial decode doesn't trip end-of-stream or hash checks."""
    body = (struct.pack(">HH", si.min_blocksize, si.max_blocksize)
            + si.min_framesize.to_bytes(3, "big") + si.max_framesize.to_bytes(3, "big"))
    packed = (si.sample_rate << 44) | ((si.channels - 1) << 41) | ((si.bits_per_sample - 1) << 36)
    return b"fLaC" + bytes([0x80 | STREAMINFO]) + (34).to_bytes(3, "big") + body + packed.to_bytes(8, "big") + bytes(16)
