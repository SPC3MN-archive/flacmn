import os
import subprocess
import tempfile
import unittest

from iaflac.flac_probe import (PICTURE, VORBIS_COMMENT, cross_check, make_stream_header,
                               probe_header, sniff_type)
from tests.helpers import (HAVE_FFMPEG, HAVE_FLAC, encode_flac, ffmpeg_convert, in_memory_fetch,
                           tone_wav)


@unittest.skipUnless(HAVE_FLAC, "flac binary not installed")
class RealFlacTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cd = encode_flac(tone_wav(44100), "--tag=ARTIST=Test Artist", "--tag=ALBUM=Test Album")
        cls.unaligned = encode_flac(tone_wav(44101))
        cls.hires = encode_flac(tone_wav(48000, rate=48000))

    def probe(self, data):
        return probe_header(in_memory_fetch(data))

    def test_parses_streaminfo_and_tags(self):
        p = self.probe(self.cd)
        self.assertTrue(p.ok, p.detail)
        si = p.streaminfo
        self.assertEqual((si.sample_rate, si.channels, si.bits_per_sample, si.total_samples),
                         (44100, 2, 16, 44100))
        self.assertTrue(si.md5_set)
        self.assertTrue(si.cd_native)
        self.assertTrue(si.sector_aligned)
        self.assertAlmostEqual(si.duration, 1.0)
        self.assertEqual(p.tags["ARTIST"], "Test Artist")
        self.assertEqual(p.tags["ALBUM"], "Test Album")
        self.assertIn("libFLAC", p.vendor)
        self.assertTrue(p.has_block(VORBIS_COMMENT))
        self.assertGreater(p.audio_offset, 4)

    def test_non_multiple_of_588_is_not_sector_aligned(self):
        si = self.probe(self.unaligned).streaminfo
        self.assertEqual(si.total_samples, 44101)
        self.assertFalse(si.sector_aligned)

    def test_48k_is_not_cd_native_or_aligned(self):
        si = self.probe(self.hires).streaminfo
        self.assertEqual(si.sample_rate, 48000)
        self.assertFalse(si.cd_native)
        self.assertFalse(si.sector_aligned)  # 48000 % 588 != 0 anyway, and alignment is 44.1 kHz only

    def test_only_one_small_read_for_a_normal_file(self):
        fetch = in_memory_fetch(self.cd)
        probe_header(fetch)
        self.assertEqual(len(fetch.calls), 1)

    def test_id3v2_prefix_is_skipped(self):
        tag_body = b"\x00" * 300
        size = len(tag_body)
        syncsafe = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
        prefixed = b"ID3\x03\x00\x00" + syncsafe + tag_body + self.cd
        p = self.probe(prefixed)
        self.assertTrue(p.ok, p.detail)
        self.assertEqual(p.id3_prefix, 10 + size)
        self.assertEqual(p.streaminfo.total_samples, 44100)

    def test_truncated_inside_streaminfo(self):
        p = self.probe(self.cd[:20])
        self.assertFalse(p.ok)
        self.assertEqual(p.kind, "flac")

    def test_metadata_only_no_audio_frames(self):
        offset = self.probe(self.cd).audio_offset
        p = self.probe(self.cd[:offset])
        self.assertFalse(p.ok)
        self.assertIn("frame sync", p.detail)

    def test_audio_starting_with_garbage_is_rejected(self):
        offset = self.probe(self.cd).audio_offset
        p = self.probe(self.cd[:offset] + b"\x00" * 64)
        self.assertFalse(p.ok)

    def test_huge_picture_block_is_hopped_not_downloaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "a.flac")
            pic = os.path.join(tmp, "cover.jpg")
            with open(src, "wb") as f:
                f.write(self.cd)
            with open(pic, "wb") as f:
                f.write(os.urandom(200_000))
            subprocess.run(["metaflac", f"--import-picture-from=3|image/jpeg||100x100x24|{pic}", src],
                           check=True)
            with open(src, "rb") as f:
                data = f.read()
        fetch = in_memory_fetch(data)
        p = probe_header(fetch)
        self.assertTrue(p.ok, p.detail)
        self.assertTrue(p.has_block(PICTURE))
        self.assertEqual(p.tags["ARTIST"], "Test Artist")  # comment block sits before the picture
        self.assertGreater(p.audio_offset, 200_000)
        self.assertLessEqual(sum(n for _, n in fetch.calls), 32 * 1024 + 64)  # never read the picture body

    def test_ogg_flac_is_named_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = os.path.join(tmp, "a.wav"), os.path.join(tmp, "a.oga")
            with open(src, "wb") as f:
                f.write(tone_wav(4410))
            subprocess.run(["flac", "-s", "--ogg", "-f", "-o", dst, src], check=True)
            with open(dst, "rb") as f:
                data = f.read()
        p = self.probe(data)
        self.assertFalse(p.ok)
        self.assertEqual(p.kind, "ogg-flac")

    def test_stream_header_round_trips(self):
        si = self.probe(self.cd).streaminfo
        header = make_stream_header(si)
        self.assertEqual(len(header), 4 + 4 + 34)
        offset = self.probe(self.cd).audio_offset
        p = self.probe(header + self.cd[offset:])
        self.assertTrue(p.ok, p.detail)
        self.assertEqual((p.streaminfo.sample_rate, p.streaminfo.channels, p.streaminfo.bits_per_sample),
                         (44100, 2, 16))
        self.assertFalse(p.streaminfo.md5_set)  # zeroed on purpose, for partial decodes

    def test_cross_check_flags_length_and_size_mismatches(self):
        p = self.probe(self.cd)
        self.assertEqual(cross_check(p, len(self.cd), 1.0), [])
        self.assertTrue(any("IA reports" in i for i in cross_check(p, len(self.cd), 30.0)))
        self.assertTrue(any("expand" in i for i in cross_check(p, len(self.cd) * 10, 1.0)))


@unittest.skipUnless(HAVE_FLAC and HAVE_FFMPEG, "flac and ffmpeg required")
class ImposterTests(unittest.TestCase):
    def test_mp3_named_flac(self):
        mp3 = ffmpeg_convert(tone_wav(44100), "wav", ["-c:a", "libmp3lame", "-b:a", "128k"], "mp3")
        p = probe_header(in_memory_fetch(mp3))
        self.assertFalse(p.ok)
        self.assertEqual(p.kind, "mp3")

    def test_mp3_with_id3_tag_named_flac(self):
        mp3 = ffmpeg_convert(tone_wav(44100), "wav", ["-c:a", "libmp3lame", "-b:a", "128k",
                                                       "-id3v2_version", "3"], "mp3")
        with_tag = b"ID3\x03\x00\x00\x00\x00\x00\x0a" + b"\x00" * 10 + mp3
        p = probe_header(in_memory_fetch(with_tag))
        self.assertFalse(p.ok)
        self.assertEqual(p.kind, "mp3")
        self.assertGreater(p.id3_prefix, 0)

    def test_wav_named_flac(self):
        p = probe_header(in_memory_fetch(tone_wav(44100)))
        self.assertFalse(p.ok)
        self.assertEqual(p.kind, "wav")

    def test_html_error_page_saved_as_flac(self):
        p = probe_header(in_memory_fetch(b"<html><body>503 Service Unavailable</body></html>"))
        self.assertFalse(p.ok)
        self.assertEqual(p.kind, "html/text")


class SniffTests(unittest.TestCase):
    def test_magic_table(self):
        cases = {
            b"fLaC\x00\x00": "flac", b"RIFF\x00\x00\x00\x00WAVEfmt ": "wav",
            b"FORM\x00\x00\x00\x00AIFF": "aiff", b"\x00\x00\x00\x20ftypM4A ": "mp4/m4a",
            b"MAC \x96\x0f": "ape", b"wvpk\x00": "wavpack", b"ajkg\x02": "shorten",
            b"\xff\xfb\x90\x00": "mp3", b"\xff\xf1\x50\x80": "aac-adts", b"PK\x03\x04": "zip",
            b"\x00" * 64: "zeros", b"\x01\x02\x03\x04\x05": "unknown",
        }
        for head, kind in cases.items():
            self.assertEqual(sniff_type(head), kind, head[:12])

    def test_ogg_variants(self):
        self.assertEqual(sniff_type(b"OggS" + b"\0" * 24 + b"\x7fFLAC" + b"\0" * 8), "ogg-flac")
        self.assertEqual(sniff_type(b"OggS" + b"\0" * 24 + b"\x01vorbis" + b"\0" * 8), "ogg-vorbis")
        self.assertEqual(sniff_type(b"OggS" + b"\0" * 24 + b"OpusHead" + b"\0" * 8), "opus")


if __name__ == "__main__":
    unittest.main()
