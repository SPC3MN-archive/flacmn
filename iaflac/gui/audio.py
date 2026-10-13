"""Audio for the player: a local cache of archive.org tracks, waveform peaks, spectrogram pictures and a per-track
lossy-codec check.

The player streams a track while it plays (the server forwards HTTP Range requests to archive.org), and in the
background the whole file is fetched into a size-capped cache, checked against archive.org's md5. The waveform, the
spectrogram and the analysis are computed from that cached copy with ffmpeg, so they appear a few seconds after
playback starts and are instant the next time.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .. import binaries, paths, spectral
from ..events import CancelCheck, check
from ..ia_client import IAError

PEAK_RATE = 11025                  # decode rate for waveform peaks: plenty for min/max per pixel
ANALYSIS_SECONDS = 12.0
ANALYSIS_POINTS = (0.3, 0.55, 0.8)
CHUNK = 1 << 20


class AudioError(RuntimeError):
    pass


def _ffmpeg() -> str:
    exe = binaries.ffmpeg()
    if not exe:
        raise AudioError("ffmpeg was not found. Install it (it must be on PATH) or put it in packaging/bin; "
                         "Settings > Tools shows what the app found")
    return exe


class AudioCache:
    """Whole tracks from archive.org, cached on disk by identifier and file name, verified against the manifest."""

    def __init__(self, client, root: Optional[Path] = None, max_bytes: int = 2 << 30):
        self.client = client
        self.root = Path(root) if root else paths.cache_dir() / "audio"
        self.max_bytes = max_bytes
        self._locks: dict = {}
        self._guard = threading.Lock()

    def path_for(self, identifier: str, name: str) -> Path:
        key = hashlib.sha1(f"{identifier}\0{name}".encode()).hexdigest()
        return self.root / key[:2] / f"{key}{Path(name).suffix.lower()}"

    def cached(self, identifier: str, name: str) -> Optional[Path]:
        p = self.path_for(identifier, name)
        return p if p.exists() else None

    def _lock(self, key: Path) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(str(key), threading.Lock())

    def ensure(self, identifier: str, f, progress: Optional[Callable[[int, int], None]] = None,
               cancel: Optional[CancelCheck] = None) -> Path:
        """The cached copy of file `f` (a model.FileInfo), downloading it first if needed. One download per file at a
        time; a second caller waits for the first."""
        target = self.path_for(identifier, f.name)
        with self._lock(target):
            if target.exists():
                os.utime(target)                    # most recently used
                return target
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(target.name + ".part")
            md5 = hashlib.md5()
            got = 0
            try:
                resp = self.client.open_stream(identifier, f.name)
            except IAError as exc:
                raise AudioError(f"could not fetch {f.name}: {exc}") from exc
            try:
                with open(part, "wb") as out:
                    for chunk in resp.iter_content(CHUNK):
                        check(cancel)
                        out.write(chunk)
                        md5.update(chunk)
                        got += len(chunk)
                        if progress is not None:
                            progress(got, f.size)
            finally:
                resp.close()
            if f.md5 and md5.hexdigest() != f.md5:
                part.unlink(missing_ok=True)
                raise AudioError(f"{f.name}: the download does not match archive.org's checksum")
            part.replace(target)
        self.prune()
        return target

    def prune(self) -> None:
        """Drop the least recently used tracks (and their derived pictures) once the cache is over its size cap."""
        try:
            files = [p for p in self.root.rglob("*") if p.is_file() and not p.name.endswith(".part")]
        except OSError:
            return
        total = sum(p.stat().st_size for p in files)
        if total <= self.max_bytes:
            return
        for p in sorted(files, key=lambda p: p.stat().st_mtime):
            if total <= self.max_bytes * 0.9:
                break
            try:
                size = p.stat().st_size
                p.unlink()
                total -= size
            except OSError:
                pass


# ---- derived data: computed from a local file and cached next to it ---------------------------------------------

def _derived(path: Path, kind: str, ext: str) -> Path:
    """Where a picture or JSON derived from `path` is kept: beside a cached track, or in the cache for library files."""
    stat = path.stat()
    key = hashlib.sha1(f"{path.resolve()}\0{stat.st_size}\0{int(stat.st_mtime)}\0{kind}".encode()).hexdigest()
    return paths.cache_dir() / "derived" / key[:2] / f"{key}.{ext}"


def duration_of(path: Path) -> float:
    from mutagen import File
    audio = File(str(path))
    return float(getattr(getattr(audio, "info", None), "length", 0) or 0)


def peaks(path: Path, n: int = 1600) -> dict:
    """{"duration", "n", "peaks": [max0, min0, max1, min1, ...]} in -1..1, n pairs across the whole track."""
    n = max(64, min(int(n), 20000))
    out_path = _derived(path, f"peaks{n}", "json")
    if out_path.exists():
        return json.loads(out_path.read_text(encoding="utf-8"))
    duration = duration_of(path)
    cmd = [_ffmpeg(), "-nostdin", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(PEAK_RATE), "-f", "f32le", "pipe:1"]
    proc = subprocess.run(cmd, capture_output=True, timeout=300, **binaries.NO_WINDOW)
    if proc.returncode != 0:
        raise AudioError(proc.stderr.decode("utf-8", "replace").strip()[:200] or "ffmpeg could not decode the track")
    x = np.frombuffer(proc.stdout, dtype="<f4")
    if not len(x):
        raise AudioError("no audio decoded")
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    starts = np.linspace(0, len(x), n + 1).astype(int)[:-1]
    hi, lo = np.maximum.reduceat(x, starts), np.minimum.reduceat(x, starts)
    pairs = np.empty(2 * n, dtype=np.float32)
    pairs[0::2], pairs[1::2] = hi, lo
    data = {"duration": round(duration or len(x) / PEAK_RATE, 3), "n": n,
            "peaks": [round(float(v), 4) for v in pairs]}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data), encoding="utf-8")
    return data


def spectrogram_png(path: Path, width: int = 1200, height: int = 400, legend: bool = False) -> bytes:
    """The whole track as a spectrogram picture (time across, frequency up to Nyquist, linear), via ffmpeg."""
    width, height = max(200, min(int(width), 4000)), max(100, min(int(height), 2000))
    out_path = _derived(path, f"spec{width}x{height}{int(legend)}", "png")
    if out_path.exists():
        return out_path.read_bytes()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.stem + f".{os.getpid()}.{threading.get_ident()}.png")
    filt = (f"showspectrumpic=s={width}x{height}:mode=combined:color=intensity:scale=log:fscale=lin"
            f":legend={1 if legend else 0}:gain=1:drange=120")
    cmd = [_ffmpeg(), "-nostdin", "-v", "error", "-y", "-i", str(path), "-lavfi", filt, "-frames:v", "1", str(tmp)]
    proc = subprocess.run(cmd, capture_output=True, timeout=300, **binaries.NO_WINDOW)
    if proc.returncode != 0 or not tmp.exists():
        raise AudioError(proc.stderr.decode("utf-8", "replace").strip()[:200] or "ffmpeg could not draw the spectrogram")
    tmp.replace(out_path)
    return out_path.read_bytes()


def spectrum3d(path: Path, slices: int = 40, bins: int = 128) -> dict:
    """The track as a waterfall for the 3D spectrogram: `slices` moments spread over the whole track, each the
    spectrum (0 Hz to Nyquist, `bins` linear bands) of about 0.2 s of audio, scaled to 0..1 over -90..0 dB.
    {"duration", "sample_rate", "nyquist_hz", "slices", "bins", "levels": [[...bins] * slices], "times": [...]}"""
    slices, bins = max(8, min(int(slices), 120)), max(32, min(int(bins), 512))
    out_path = _derived(path, f"spec3d{slices}x{bins}", "json")
    if out_path.exists():
        return json.loads(out_path.read_text(encoding="utf-8"))
    duration, rate = duration_of(path), sample_rate_of(path)
    if not duration or not rate:
        raise AudioError("cannot read the track's length or sample rate")
    n_fft = 8192
    hops = 4                                       # averaged FFT frames per slice (~0.2 s at 44.1 kHz)
    need = n_fft * hops
    times = [duration * (i + 0.5) / slices for i in range(slices)]
    starts = [max(0, min(int(t * rate) - need // 2, int(duration * rate) - need)) for t in times]
    window = np.hanning(n_fft).astype(np.float32)
    edges = np.linspace(0, n_fft // 2, bins + 1).astype(int)
    cmd = [_ffmpeg(), "-nostdin", "-v", "error", "-i", str(path), "-ac", "1", "-f", "f32le", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **binaries.NO_WINDOW)
    levels, pos, buf = [], 0, np.zeros(0, dtype=np.float32)
    try:
        for start in starts:                       # read the decoded stream once, keeping only the bits we need
            while pos + len(buf) < start + need:
                chunk = proc.stdout.read(CHUNK * 4)
                if not chunk:
                    break
                buf = np.concatenate([buf, np.frombuffer(chunk, dtype="<f4")])
                if pos + len(buf) < start:         # skip ahead: drop what lies before this slice
                    pos += len(buf)
                    buf = np.zeros(0, dtype=np.float32)
            lo = max(0, start - pos)
            seg = buf[lo:lo + need]
            if len(seg) < need:
                seg = np.pad(seg, (0, need - len(seg)))
            power = np.zeros(n_fft // 2 + 1)
            for h in range(hops):
                frame = seg[h * n_fft:(h + 1) * n_fft] * window
                power += np.abs(np.fft.rfft(frame)) ** 2
            power /= hops
            band = np.array([power[a:max(a + 1, b)].mean() for a, b in zip(edges[:-1], edges[1:])])
            db = 10 * np.log10(band / (np.sum(window) ** 2 / 4) + 1e-12)
            levels.append([round(float(v), 3) for v in np.clip((db + 90) / 90, 0, 1)])
            drop = max(0, start + need // 2 - pos)          # keep the tail: the next slice may overlap
            buf, pos = buf[drop:], pos + drop
    finally:
        proc.kill()
        proc.wait()
    data = {"duration": round(duration, 3), "sample_rate": rate, "nyquist_hz": rate / 2, "slices": slices, "bins": bins,
            "levels": levels, "times": [round(t, 2) for t in times]}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _decode_window(path: Path, start: float, seconds: float) -> tuple:
    cmd = [_ffmpeg(), "-nostdin", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{seconds:.2f}", "-i", str(path),
           "-ac", "1", "-f", "f32le", "pipe:1"]
    proc = subprocess.run(cmd, capture_output=True, timeout=120, **binaries.NO_WINDOW)
    return np.frombuffer(proc.stdout, dtype="<f4")


def sample_rate_of(path: Path) -> int:
    from mutagen import File
    audio = File(str(path))
    return int(getattr(getattr(audio, "info", None), "sample_rate", 0) or 0)


def analyze(path: Path) -> dict:
    """The V3 check on one whole local track: three windows, the same measurement and verdicts as spectral.py.
    {"verdict", "detail", "sample_rate", "cutoff_hz", "step_db", "windows": [...]}"""
    out_path = _derived(path, "analysis", "json")
    if out_path.exists():
        return json.loads(out_path.read_text(encoding="utf-8"))
    duration, rate = duration_of(path), sample_rate_of(path)
    if not duration or not rate:
        raise AudioError("cannot read the track's length or sample rate")
    seconds = min(ANALYSIS_SECONDS, max(4.5, duration / 4))
    windows = []
    for frac in ANALYSIS_POINTS:
        start = max(0.0, min(duration - seconds, duration * frac - seconds / 2))
        x = _decode_window(path, start, seconds)
        windows.append(spectral.analyze_samples(path.name, x, rate))
    result = spectral.classify(windows, rate)
    flagged = [w for w in result.windows if w.flag != "unusable" and w.step_db >= spectral.STEP_MILD_DB]
    worst = max(flagged, key=lambda w: w.step_db) if flagged else None
    data = {"verdict": result.verdict, "detail": result.detail, "sample_rate": rate,
            "nyquist_hz": rate / 2, "cutoff_hz": round(worst.cutoff_hz) if worst else None,
            "step_db": round(worst.step_db, 1) if worst else None,
            "windows": [{"cutoff_hz": round(w.cutoff_hz), "step_db": round(w.step_db, 1), "flag": w.flag,
                         "seconds": round(w.seconds, 1)} for w in result.windows],
            "analysed_at": time.time()}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data), encoding="utf-8")
    return data


def transcode_wav(path: Path):
    """A WAV stream of a local track, for a web view that cannot play FLAC itself. Yields bytes."""
    proc = subprocess.Popen([_ffmpeg(), "-nostdin", "-v", "error", "-i", str(path), "-f", "wav", "pipe:1"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **binaries.NO_WINDOW)
    try:
        while True:
            chunk = proc.stdout.read(CHUNK)
            if not chunk:
                break
            yield chunk
    finally:
        proc.kill()
        proc.wait()


def inside(path: Path, roots: list) -> bool:
    """Is `path` a file inside one of `roots` (after resolving links and '..')? Guards the local-file endpoints."""
    try:
        real = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    for root in roots:
        try:
            if real.is_relative_to(Path(root).resolve()):
                return real.is_file()
        except (OSError, RuntimeError):
            continue
    return False
