"""Test helpers: build real audio files with the locally installed flac / ffmpeg."""

from __future__ import annotations

import io
import math
import os
import random
import shutil
import struct
import subprocess
import tempfile
import wave

import numpy as np

HAVE_FLAC = shutil.which("flac") is not None
HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def tone_wav(samples: int = 44100, rate: int = 44100, channels: int = 2, seed: int = 1) -> bytes:
    """A 16-bit WAV: a 440 Hz tone plus light noise (so FLAC has something to compress)."""
    rng = random.Random(seed)
    frames = bytearray()
    for i in range(samples):
        v = int(8000 * math.sin(2 * math.pi * 440 * i / rate) + rng.randint(-500, 500))
        frames += struct.pack("<h", v) * channels
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def pcm_wav(x: np.ndarray, rate: int) -> bytes:
    """16-bit mono/stereo WAV from a float array in [-1, 1] (shape (n,) or (n, ch))."""
    x = np.clip(x, -1.0, 1.0)
    data = (x * 32767).astype("<i2")
    channels = 1 if data.ndim == 1 else data.shape[1]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())
    return buf.getvalue()


def encode_flac(wav: bytes, *flac_args: str) -> bytes:
    """Encode WAV bytes to FLAC bytes via a temp file (so STREAMINFO gets its MD5)."""
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = os.path.join(tmp, "in.wav"), os.path.join(tmp, "out.flac")
        with open(src, "wb") as f:
            f.write(wav)
        subprocess.run(["flac", "-s", "-f", *flac_args, "-o", dst, src], check=True)
        with open(dst, "rb") as f:
            return f.read()


def ffmpeg_convert(data: bytes, in_fmt: str, out_args: list, out_fmt: str) -> bytes:
    proc = subprocess.run(["ffmpeg", "-nostdin", "-v", "quiet", "-f", in_fmt, "-i", "pipe:0",
                           *out_args, "-f", out_fmt, "pipe:1"], input=data, capture_output=True, check=True)
    return proc.stdout


def in_memory_fetch(data: bytes):
    """A `fetch(start, length)` over bytes, like a Range request; counts its calls."""
    def fetch(start: int, length: int) -> bytes:
        fetch.calls.append((start, length))
        return data[start:start + length]
    fetch.calls = []
    return fetch


def pink_noise(seconds: float, rate: int, seed: int = 0) -> np.ndarray:
    """Full-band pink-ish noise (1/f power) normalised to about -12 dBFS."""
    rng = np.random.default_rng(seed)
    n = int(seconds * rate)
    spectrum = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1 / rate)
    freqs[0] = freqs[1]
    spectrum /= np.sqrt(freqs)
    x = np.fft.irfft(spectrum, n)
    return (0.25 * x / np.max(np.abs(x))).astype(np.float64)


# What macOS writes beside every file on a FAT32/exFAT drive: `._<name>`, same extension, Finder metadata only.
APPLEDOUBLE = (b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X        ").ljust(4096, b"\x00")


def add_appledouble(folder) -> None:
    """Give every file in `folder` (recursively) its macOS `._` sidecar, as copying it to a FAT32 drive would."""
    from pathlib import Path
    for p in [p for p in Path(folder).rglob("*") if p.is_file() and not p.name.startswith(".")]:
        (p.parent / f"._{p.name}").write_bytes(APPLEDOUBLE)
