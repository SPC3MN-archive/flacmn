"""V3: does the audio look as if it was ever squeezed through a lossy codec?

We fetch a few ~1.5 MB slices from the middle of tracks (HTTP Range), decode each with ffmpeg after
prepending a minimal FLAC header (FLAC frames are self-contained, so a slice decodes on its own), and
look for the brickwall lowpass that MP3/AAC/Vorbis encoders leave behind: real music decays smoothly
toward Nyquist, a transcode falls off a cliff at 16 to 20 kHz.

The measure is the largest downward "step" in the average spectrum between 12 kHz and Nyquist. On 13 s
of a real EAC-verified CD rip it was 6 dB; the same audio rebuilt as MP3 128/192/256/320 CBR and V2 was
63-70 dB. Known blind spots (also in the README): MP3 V0 and other very high bitrates leave almost no
cliff (6.6 dB in the same test), and band-limited old masters can look like a transcode, which is why
"SUSPECT" is a request to look at the spectrogram rather than a rejection.
"""

from __future__ import annotations

import math
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from scipy.signal import welch

from . import binaries
from .flac_probe import FlacProbe, make_stream_header, probe_header
from .model import ItemInfo

SLICE_BYTES = 1_500_000
WINDOWS = 3
SKIP_SECONDS = 0.5              # drop the resync junk at the start of a slice
MIN_WINDOW_SECONDS = 4.0
MIN_LEVEL_DBFS = -70.0          # quieter than this carries no evidence
NPERSEG = 8192
FMIN_HZ = 12_000.0
STEP_CLIFF_DB = 30.0
STEP_MILD_DB = 15.0
LOSSY_MAX_CUTOFF_HZ = 19_500.0  # MP3 128-224k / AAC / Vorbis lowpasses sit below this
BRICKWALL_MAX_HZ = 21_000.0     # 256-320k lowpasses sit up to here, but so do some band-limited masters
CUTOFF_AGREEMENT_HZ = 700.0


class SpectralUnavailable(RuntimeError):
    pass


@dataclass
class Window:
    track: str
    cutoff_hz: float
    step_db: float
    seconds: float
    level_dbfs: float
    flag: str                   # clean | suspect | lossy | upsampled | unusable


@dataclass
class SpectralResult:
    verdict: str                # PASS | SUSPECT | LIKELY_LOSSY | UPSAMPLED | INCONCLUSIVE | UNAVAILABLE
    detail: str
    sample_rate: int = 0
    windows: list = field(default_factory=list)
    png: Optional[str] = None

    def summary(self) -> str:
        return f"{self.verdict}: {self.detail}"

    def to_dict(self) -> dict:
        return asdict(self)


# ---- the measurement -------------------------------------------------------------------------

def step_detect(x: np.ndarray, fs: int, fmin: float = FMIN_HZ) -> tuple:
    """(frequency, size in dB) of the largest downward step in the average spectrum above fmin."""
    f, p = welch(x, fs=fs, nperseg=NPERSEG, window="hann", noverlap=NPERSEG // 2)
    db = 10 * np.log10(p + 1e-24)
    df = f[1] - f[0]
    w1, w2 = int(200 / df), int(1000 / df)
    idx = np.arange(int(fmin / df), len(f) - w2 - 1)
    if len(idx) == 0:
        return 0.0, 0.0
    cs = np.concatenate(([0.0], np.cumsum(db)))
    lo = (cs[idx - w1] - cs[idx - w2]) / (w2 - w1)     # mean level 0.2-1.0 kHz below the candidate
    hi = (cs[idx + w2] - cs[idx + w1]) / (w2 - w1)     # mean level 0.2-1.0 kHz above it
    step = lo - hi
    k = int(np.argmax(step))
    return float(f[idx[k]]), float(step[k])


def flag_window(cutoff: float, step: float, fs: int) -> str:
    nyquist = fs / 2
    if step < STEP_MILD_DB:
        return "clean"
    if fs > 48_000 and 19_500 < cutoff <= 24_500 and step >= STEP_CLIFF_DB:
        return "upsampled"                              # hi-res file whose content stops near 22 kHz
    if cutoff > nyquist - 1_500:
        return "clean"                                  # the ordinary anti-alias slope at Nyquist
    if step >= STEP_CLIFF_DB:
        if cutoff <= LOSSY_MAX_CUTOFF_HZ:
            return "lossy"
        return "suspect" if cutoff <= BRICKWALL_MAX_HZ else "clean"
    return "suspect" if cutoff <= LOSSY_MAX_CUTOFF_HZ else "clean"


def analyze_samples(track: str, x: np.ndarray, fs: int) -> Window:
    x = x[int(SKIP_SECONDS * fs):]
    seconds = len(x) / fs
    level = 20 * math.log10(float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) + 1e-12) if len(x) else -120.0
    if seconds < MIN_WINDOW_SECONDS or level < MIN_LEVEL_DBFS:
        return Window(track, 0.0, 0.0, seconds, level, "unusable")
    cutoff, step = step_detect(x, fs)
    return Window(track, cutoff, step, seconds, level, flag_window(cutoff, step, fs))


def classify(windows: list, fs: int) -> SpectralResult:
    usable = [w for w in windows if w.flag != "unusable"]
    if len(usable) < 2:
        return SpectralResult("INCONCLUSIVE", f"only {len(usable)} usable window(s): too short, too quiet or undecodable",
                              fs, windows)
    n = len(usable)
    lossy = [w for w in usable if w.flag == "lossy"]
    upsampled = [w for w in usable if w.flag == "upsampled"]
    suspect = [w for w in usable if w.flag in ("suspect", "lossy")]
    if len(lossy) >= 2 and max(w.cutoff_hz for w in lossy) - min(w.cutoff_hz for w in lossy) <= CUTOFF_AGREEMENT_HZ:
        fc = sum(w.cutoff_hz for w in lossy) / len(lossy)
        step = sum(w.step_db for w in lossy) / len(lossy)
        return SpectralResult("LIKELY_LOSSY", f"{step:.0f} dB brickwall at {fc / 1000:.1f} kHz in {len(lossy)}/{n} windows "
                              "(the lowpass MP3/AAC encoders leave)", fs, windows)
    if len(upsampled) >= 2:
        fc = sum(w.cutoff_hz for w in upsampled) / len(upsampled)
        return SpectralResult("UPSAMPLED", f"content ends at {fc / 1000:.1f} kHz although the sample rate is {fs / 1000:g} kHz "
                              "(resampled from a lower-rate source)", fs, windows)
    if suspect:
        w = max(suspect, key=lambda w: w.step_db)
        return SpectralResult("SUSPECT", f"{w.step_db:.0f} dB step at {w.cutoff_hz / 1000:.1f} kHz in {len(suspect)}/{n} window(s); "
                              "could be a band-limited master or high-bitrate lossy, so check the spectrogram", fs, windows)
    worst = max(w.step_db for w in usable)
    return SpectralResult("PASS", f"no lossy-codec lowpass (largest spectral step {worst:.0f} dB across {n} windows)", fs, windows)


# ---- fetching and decoding slices -----------------------------------------------------------

def decode_slice(header: bytes, body: bytes) -> np.ndarray:
    ffmpeg = binaries.ffmpeg()
    if not ffmpeg:
        raise SpectralUnavailable("ffmpeg is not installed")
    proc = subprocess.run([ffmpeg, "-nostdin", "-v", "quiet", "-f", "flac", "-i", "pipe:0", "-ac", "1",
                           "-f", "f32le", "pipe:1"], input=header + body, capture_output=True, timeout=120)
    return np.frombuffer(proc.stdout, dtype="<f4")


def slice_plan(files: list, windows: int = WINDOWS) -> list:
    """[(file, fraction of the way through)]: distinct tracks spread over the album when possible."""
    longish = [f for f in files if (f.length or 60) >= 20] or list(files)
    n = len(longish)
    if n >= windows:
        return [(longish[min(n - 1, int((k + 0.5) * n / windows))], 0.5) for k in range(windows)]
    if n == 2:
        return [(longish[0], 0.4), (longish[1], 0.5), (longish[0], 0.75)]
    return [(longish[0], 0.3), (longish[0], 0.55), (longish[0], 0.8)][:windows] if n == 1 else []


def analyze_item(client, item: ItemInfo, probes: dict, png_path: Optional[Path] = None,
                 log: Callable[[str], None] = lambda m: None) -> SpectralResult:
    plan = slice_plan(item.flacs)
    if not plan:
        return SpectralResult("INCONCLUSIVE", "no FLAC files to sample")
    windows, samples, rate = [], [], 0
    for f, frac in plan:
        probe: Optional[FlacProbe] = probes.get(f.name)
        if probe is None:
            probe = probe_header(lambda s, n, name=f.name: client.range_get(item.identifier, name, s, n))
        if not probe.ok or not probe.streaminfo:
            windows.append(Window(f.name, 0, 0, 0, -120, "unusable"))
            continue
        si = probe.streaminfo
        start_min = probe.audio_offset or 0
        start = start_min + max(0, int((f.size - start_min - SLICE_BYTES) * frac))
        log(f"    spectral slice: {f.name.rsplit('/', 1)[-1]} bytes {start}-{start + SLICE_BYTES}")
        body = client.range_get(item.identifier, f.name, start, min(SLICE_BYTES, f.size - start))
        try:
            x = decode_slice(make_stream_header(si), body)
        except SpectralUnavailable as exc:
            return SpectralResult("UNAVAILABLE", str(exc))
        except (subprocess.SubprocessError, ValueError):
            x = np.zeros(0, dtype="<f4")
        w = analyze_samples(f.name.rsplit("/", 1)[-1], x, si.sample_rate)
        windows.append(w)
        rate = si.sample_rate
        if w.flag != "unusable":
            samples.append((w, x[int(SKIP_SECONDS * si.sample_rate):]))
    result = classify(windows, rate)
    if png_path is not None and samples:
        try:
            write_spectrogram(png_path, samples, rate, result, item.identifier)
            result.png = str(png_path)
        except Exception as exc:                        # a chart must never sink the verdict
            result.detail += f" (spectrogram not written: {type(exc).__name__})"
    return result


def write_spectrogram(path: Path, samples: list, fs: int, result: SpectralResult, identifier: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from scipy.signal import spectrogram

    path.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, len(samples), figsize=(4.6 * len(samples), 4.2), squeeze=False, sharey=True)
    for ax, (w, x) in zip(axes[0], samples):
        f, t, sxx = spectrogram(x, fs=fs, nperseg=2048, noverlap=1024, mode="psd")
        img = ax.pcolormesh(t, f / 1000, 10 * np.log10(sxx + 1e-20), vmin=-150, vmax=-50, cmap="magma", shading="auto")
        if w.step_db >= STEP_MILD_DB:
            ax.axhline(w.cutoff_hz / 1000, color="cyan", lw=0.8, ls="--")
        ax.set_title(f"{w.track[:28]}\nstep {w.step_db:.0f} dB @ {w.cutoff_hz / 1000:.1f} kHz", fontsize=8)
        ax.set_xlabel("seconds", fontsize=8)
    axes[0][0].set_ylabel("kHz", fontsize=8)
    fig.suptitle(f"{identifier}: {result.verdict}", fontsize=10)
    fig.colorbar(img, ax=axes.ravel().tolist(), label="dB", shrink=0.9)
    fig.savefig(path, dpi=100)
    plt.close(fig)
