"""Every audio format next to FLAC: real files of each (made with ffmpeg) are probed from their headers the way an
archive.org upload is (Range reads), labelled lossless or lossy, converted to FLAC without losing a sample, and an AAC
.m4a keeps its tags. An .m4a is told apart by its codec: ALAC is lossless, AAC is not."""

import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from iaflac import altmedia, quality
from iaflac.download import convert_to_flac, is_lossless_file

FLAC = shutil.which("flac")
NEEDED = ("alac", "aac", "wavpack")


def encoders(ffmpeg: str) -> str:
    return subprocess.run([ffmpeg, "-v", "error", "-encoders"], capture_output=True, text=True).stdout


def maker() -> str | None:
    """An ffmpeg that can make the test files. The app's bundled one only decodes, so look past it too."""
    for f in (shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
        if f and Path(f).exists() and all(f" {e} " in encoders(f) for e in NEEDED):
            return f
    return None


FFMPEG = maker()
NEED = unittest.skipUnless(FFMPEG, "needs an ffmpeg with the alac, aac and wavpack encoders")


def make(path: Path, *args, seconds=3.0, rate=44100) -> Path:
    """A stereo test tone of `seconds` (a whole number of CD sectors at 44.1 kHz by default)."""
    samples = int(seconds * rate) // 588 * 588 if rate == 44100 else int(seconds * rate)
    subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=440:sample_rate={rate}:duration={samples / rate}", "-ac", "2", *args, str(path)],
                   check=True)
    return path


def fetcher(path: Path):
    data = path.read_bytes()
    return (lambda start, n: data[start:start + n]), len(data)


def pcm_md5(path: Path) -> str:
    """The audio itself, decoded: the same for a file and its FLAC when nothing was lost."""
    out = subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "md5", "-"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


@NEED
class Probes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        d = cls.dir
        cls.alac = make(d / "alac.m4a", "-c:a", "alac", "-sample_fmt", "s16p")
        cls.alac_fast = make(d / "alac-fast.m4a", "-c:a", "alac", "-sample_fmt", "s16p", "-movflags", "+faststart")
        cls.alac24 = make(d / "alac24.m4a", "-c:a", "alac", "-sample_fmt", "s32p", "-bits_per_raw_sample", "24", rate=96000)
        cls.aac = make(d / "aac.m4a", "-c:a", "aac", "-b:a", "256k")
        cls.aiff = make(d / "tone.aiff", "-c:a", "pcm_s24be", rate=96000)
        cls.wv = make(d / "tone.wv", "-c:a", "wavpack")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def probe(self, media, path):
        fetch, size = fetcher(path)
        return altmedia.probe_file(media, fetch, size, None)

    def test_alac_is_lossless_with_its_depth_rate_and_length(self):
        for path in (self.alac, self.alac_fast):                  # 'moov' at the end, and at the front
            p = self.probe("m4a", path)
            self.assertTrue(p.ok, p.detail)
            self.assertEqual((p.codec, p.lossless, p.bits, p.sample_rate, p.channels), ("alac", True, 16, 44100, 2))
            self.assertTrue(p.sector_aligned)
        p = self.probe("m4a", self.alac24)
        self.assertEqual((p.codec, p.bits, p.sample_rate), ("alac", 24, 96000))

    def test_aac_is_lossy_with_its_bitrate(self):
        p = self.probe("m4a", self.aac)
        self.assertTrue(p.ok, p.detail)
        self.assertEqual((p.codec, p.lossless), ("aac", False))
        self.assertGreater(p.kbps, 150)

    def test_aiff_and_wavpack(self):
        p = self.probe("aiff", self.aiff)
        self.assertEqual((p.ok, p.lossless, p.bits, p.sample_rate), (True, True, 24, 96000))
        p = self.probe("wv", self.wv)
        self.assertEqual((p.ok, p.lossless, p.sample_rate, p.channels), (True, True, 44100, 2))

    def test_monkeys_audio_header(self):
        # a version 3.99 descriptor and header: 16-bit stereo 44.1 kHz, 3 frames of 73728*4 blocks, the last 1000
        desc = b"MAC " + struct.pack("<HH", 3990, 0) + struct.pack("<I", 52) + bytes(40)
        head = struct.pack("<HHIIIHHI", 2000, 0, 73728 * 4, 1000, 3, 16, 2, 44100)
        p = altmedia.probe_ape(desc + head + bytes(64))
        self.assertEqual((p.ok, p.lossless, p.bits, p.channels, p.sample_rate, p.samples),
                         (True, True, 16, 2, 44100, 2 * 73728 * 4 + 1000))

    def test_a_file_that_is_not_what_it_claims_is_caught(self):
        p = self.probe("m4a", self.aiff)
        self.assertFalse(p.ok)
        self.assertEqual(p.kind, "aiff")

    def test_an_album_of_alac_is_lossless_and_one_of_aac_is_lossy(self):
        item = SimpleNamespace(description="", title="", identifier="x", collections=[], subjects=[], extra={},
                               is_live=False, files_of=lambda *a, **k: [])
        alac = altmedia.build_stats("m4a", {f"{i}.m4a": self.probe("m4a", self.alac) for i in range(3)})
        aac = altmedia.build_stats("m4a", {f"{i}.m4a": self.probe("m4a", self.aac) for i in range(3)})
        self.assertTrue(altmedia.is_lossless("m4a", alac))
        self.assertFalse(altmedia.is_lossless("m4a", aac))
        self.assertFalse(altmedia.is_lossless("m4a", None), "an .m4a not read yet is not called lossless")
        tier_alac = altmedia.assess(item, alac, []).tier
        self.assertIn(tier_alac, ("B", "C"))
        self.assertEqual(altmedia.assess(item, aac, []).tier, "L")

    def test_quality_labels(self):
        def cand(media, stats, tier):
            return SimpleNamespace(item=SimpleNamespace(media=media), tier=tier, alt_stats=stats, stats=None)
        alac = altmedia.build_stats("m4a", {"1.m4a": self.probe("m4a", self.alac24)})
        aac = altmedia.build_stats("m4a", {"1.m4a": self.probe("m4a", self.aac)})
        q = quality.assess(cand("m4a", alac, "C"))
        self.assertEqual((q.rank, q.label, q.format, q.lossless), (quality.LOSSLESS, "Hi-res", "ALAC 24/96", True))
        q = quality.assess(cand("m4a", aac, "L"))
        self.assertEqual((q.rank, q.lossless), (quality.MP3_HIGH, False))
        self.assertTrue(q.format.startswith("AAC ~"))
        aiff = altmedia.build_stats("aiff", {"1.aiff": self.probe("aiff", self.aiff)})
        self.assertEqual(quality.assess(cand("aiff", aiff, "C")).format, "AIFF 24/96")


@NEED
@unittest.skipUnless(FLAC, "needs flac")
class ToFlac(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def check(self, src: Path):
        out = src.with_suffix(".flac")
        self.assertEqual(convert_to_flac(src, out), "")
        self.assertEqual(pcm_md5(src), pcm_md5(out), f"{src.name}: the FLAC must hold exactly the same samples")

    def test_every_lossless_format_becomes_the_same_audio_in_flac(self):
        d = self.dir
        self.check(make(d / "a.m4a", "-c:a", "alac", "-sample_fmt", "s16p"))
        self.check(make(d / "b.m4a", "-c:a", "alac", "-sample_fmt", "s32p", "-bits_per_raw_sample", "24", rate=96000))
        self.check(make(d / "c.aiff", "-c:a", "pcm_s16be"))
        self.check(make(d / "d.wv", "-c:a", "wavpack"))

    def test_only_lossless_files_are_converted(self):
        d = self.dir
        self.assertTrue(is_lossless_file(make(d / "e.m4a", "-c:a", "alac", "-sample_fmt", "s16p")))
        self.assertFalse(is_lossless_file(make(d / "f.m4a", "-c:a", "aac", "-b:a", "256k")))
        self.assertTrue(is_lossless_file(make(d / "g.wv", "-c:a", "wavpack")))


@NEED
class AacTags(unittest.TestCase):
    def test_an_aac_m4a_keeps_its_tags_and_says_what_it_is(self):
        from iaflac.tagging import read_track, write_m4a
        d = Path(tempfile.mkdtemp())
        try:
            aac = make(d / "01 - Song.m4a", "-c:a", "aac", "-b:a", "256k")
            write_m4a(aac, {"TITLE": "Song", "ARTIST": "Someone", "ALBUMARTIST": "Someone", "ALBUM": "An Album",
                            "DATE": "1999", "TRACKNUMBER": "1", "TRACKTOTAL": "9", "DISCNUMBER": "1",
                            "MUSICBRAINZ_ALBUMID": "abc-123", "IAFLAC_SOURCE": "https://archive.org/details/x"})
            t = read_track(aac)
            self.assertEqual((t["format"], t["title"], t["album"], t["albumartist"], t["track"], t["tracktotal"]),
                             ("AAC", "Song", "An Album", "Someone", 1, 9))
            self.assertEqual((t["mb_release_id"], t["source_url"]), ("abc-123", "https://archive.org/details/x"))
            alac = make(d / "02 - Other.m4a", "-c:a", "alac", "-sample_fmt", "s16p")
            self.assertEqual(read_track(alac)["format"], "ALAC")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
