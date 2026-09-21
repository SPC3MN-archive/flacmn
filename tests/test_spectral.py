import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy.signal import ellip, sosfilt

from iaflac import spectral
from iaflac.flac_probe import probe_header
from iaflac.model import FileInfo, ItemInfo
from tests.helpers import (HAVE_FFMPEG, HAVE_FLAC, encode_flac, ffmpeg_convert, in_memory_fetch, pcm_wav,
                           pink_noise)

FS = 44100


class FakeClient:
    """Serves byte ranges from memory, like archive.org's Range support."""

    def __init__(self, files: dict):
        self.files = files
        self.ranges = []

    def range_get(self, identifier, name, start, length):
        self.ranges.append((name, start, length))
        return self.files[name][start:start + length]


def stereo(x: np.ndarray) -> np.ndarray:
    return np.column_stack([x, x])


def brickwall(x: np.ndarray, hz: float, fs: int) -> np.ndarray:
    """A steep lowpass (90 dB stopband, transition well under 1 kHz): what a lossy encoder's filter looks like."""
    return sosfilt(ellip(10, 0.5, 90, hz / (fs / 2), output="sos"), x)


def analyse(flac_bytes: bytes, png=None, name="a.flac", seconds=24.0):
    item = ItemInfo(identifier="test", files=[FileInfo(name, size=len(flac_bytes), length=seconds)])
    probes = {name: probe_header(in_memory_fetch(flac_bytes))}
    return spectral.analyze_item(FakeClient({name: flac_bytes}), item, probes, png)


class StepDetector(unittest.TestCase):
    def test_full_band_noise_has_no_cliff(self):
        _, step = spectral.step_detect(pink_noise(10, FS), FS)
        self.assertLess(step, spectral.STEP_MILD_DB)

    def test_lowpass_is_found_where_it_was_put(self):
        cutoff, step = spectral.step_detect(brickwall(pink_noise(10, FS), 16_000, FS), FS)
        self.assertGreater(step, 40)
        self.assertAlmostEqual(cutoff, 16_500, delta=1_000)      # the skirt sits just above the corner

    def test_flagging_rules(self):
        f = spectral.flag_window
        self.assertEqual(f(16_700, 70, 44100), "lossy")           # MP3 128k
        self.assertEqual(f(18_700, 65, 44100), "lossy")           # MP3 192k / V2
        self.assertEqual(f(20_190, 63, 44100), "suspect")         # MP3 320k or a band-limited master
        self.assertEqual(f(21_500, 60, 44100), "clean")           # ordinary anti-alias slope
        self.assertEqual(f(17_900, 6, 44100), "clean")            # real CD audio measured at 6 dB
        self.assertEqual(f(22_000, 60, 96000), "upsampled")
        self.assertEqual(f(45_000, 60, 96000), "clean")           # genuine ultrasonic content


@unittest.skipUnless(HAVE_FLAC and HAVE_FFMPEG, "flac and ffmpeg required")
class EndToEndOnSyntheticAudio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.noise = pink_noise(24, FS)
        cls.real = encode_flac(pcm_wav(stereo(cls.noise), FS))

    def transcode(self, codec_args, fmt="mp3", source=None):
        wav = pcm_wav(stereo(self.noise if source is None else source), FS)
        lossy = ffmpeg_convert(wav, "wav", codec_args, fmt)
        raw = ffmpeg_convert(lossy, fmt, ["-ac", "2", "-ar", str(FS)], "f32le")
        return encode_flac(pcm_wav(np.frombuffer(raw, dtype="<f4").reshape(-1, 2), FS))

    def test_genuine_full_band_audio_passes(self):
        r = analyse(self.real)
        self.assertEqual(r.verdict, "PASS", r.detail)
        self.assertEqual(len([w for w in r.windows if w.flag != "unusable"]), 3)

    def test_mp3_128k_transcode_is_caught(self):
        r = analyse(self.transcode(["-c:a", "libmp3lame", "-b:a", "128k"]))
        self.assertEqual(r.verdict, "LIKELY_LOSSY", r.detail)

    def test_mp3_192k_transcode_is_caught(self):
        r = analyse(self.transcode(["-c:a", "libmp3lame", "-b:a", "192k"]))
        self.assertEqual(r.verdict, "LIKELY_LOSSY", r.detail)

    def test_mp3_320k_is_flagged_for_a_look_at_least(self):
        r = analyse(self.transcode(["-c:a", "libmp3lame", "-b:a", "320k"]))
        self.assertIn(r.verdict, ("SUSPECT", "LIKELY_LOSSY"), r.detail)

    def test_flac_lowpassed_at_16k_is_caught(self):
        x = brickwall(self.noise, 16_000, FS)
        self.assertEqual(analyse(encode_flac(pcm_wav(stereo(x), FS))).verdict, "LIKELY_LOSSY")

    def test_known_blind_spot_mp3_v0(self):
        """MP3 V0 leaves almost no cliff (6.6 dB when first measured), so this passes. It is a documented
        limitation: if this ever starts failing because the detector got better, update the README."""
        r = analyse(self.transcode(["-c:a", "libmp3lame", "-q:a", "0"]))
        self.assertEqual(r.verdict, "PASS", r.detail)

    def test_hires_file_whose_content_stops_at_21k_is_called_upsampled(self):
        up = brickwall(pink_noise(24, 96_000), 21_500, 96_000)     # what a clean 44.1 -> 96 kHz upsample looks like
        r = analyse(encode_flac(pcm_wav(stereo(up), 96_000)))
        self.assertEqual(r.verdict, "UPSAMPLED", r.detail)

    def test_genuine_hires_passes(self):
        wide = pink_noise(24, 96_000)
        self.assertEqual(analyse(encode_flac(pcm_wav(stereo(wide), 96_000))).verdict, "PASS")

    def test_silence_is_inconclusive_not_a_pass(self):
        r = analyse(encode_flac(pcm_wav(np.zeros((FS * 12, 2)), FS)), seconds=12.0)
        self.assertEqual(r.verdict, "INCONCLUSIVE")

    def test_only_slices_are_read_not_the_whole_file(self):
        item = ItemInfo(identifier="test", files=[FileInfo("a.flac", size=len(self.real), length=24.0)])
        client = FakeClient({"a.flac": self.real})
        probes = {"a.flac": probe_header(in_memory_fetch(self.real))}
        spectral.analyze_item(client, item, probes)
        self.assertEqual(len(client.ranges), 3)
        self.assertLessEqual(max(n for _, _, n in client.ranges), spectral.SLICE_BYTES)

    def test_spectrogram_png_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "out" / "test.png"
            r = analyse(self.real, png=png)
            self.assertEqual(r.png, str(png))
            self.assertEqual(png.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            self.assertGreater(png.stat().st_size, 5_000)


class SlicePlan(unittest.TestCase):
    def files(self, n, length=200.0):
        return [FileInfo(f"{i:02d}.flac", size=10_000_000, length=length) for i in range(n)]

    def test_twelve_tracks_gives_three_distinct_tracks(self):
        plan = spectral.slice_plan(self.files(12))
        self.assertEqual(len({f.name for f, _ in plan}), 3)

    def test_two_tracks_and_one_track_still_give_three_windows(self):
        self.assertEqual(len(spectral.slice_plan(self.files(2))), 3)
        one = spectral.slice_plan(self.files(1))
        self.assertEqual(len(one), 3)
        self.assertEqual(len({frac for _, frac in one}), 3)

    def test_short_tracks_are_skipped_when_there_are_better_ones(self):
        files = self.files(6) + [FileInfo("skit.flac", size=1000, length=8.0)]
        self.assertNotIn("skit.flac", {f.name for f, _ in spectral.slice_plan(files)})

    def test_empty(self):
        self.assertEqual(spectral.slice_plan([]), [])


if __name__ == "__main__":
    unittest.main()
