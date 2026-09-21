import hashlib
import unittest

from iaflac.integrity import test_stream as stream_test        # not collected as a test itself
from tests.helpers import HAVE_FFMPEG, HAVE_FLAC, encode_flac, ffmpeg_convert, tone_wav


def chunked(data: bytes, size: int = 4096):
    return (data[i:i + size] for i in range(0, len(data), size))


@unittest.skipUnless(HAVE_FLAC, "flac binary not installed")
class StreamedDecodeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flac = encode_flac(tone_wav(44100 * 3))
        cls.md5 = hashlib.md5(cls.flac).hexdigest()
        cls.sha1 = hashlib.sha1(cls.flac).hexdigest()

    def test_good_file_passes_with_manifest_checks(self):
        r = stream_test(chunked(self.flac), expected_size=len(self.flac), expected_md5=self.md5,
                        expected_sha1=self.sha1, audio_md5_set=True)
        self.assertTrue(r.ok, r.message)
        self.assertTrue(r.md5_ok and r.sha1_ok)
        self.assertTrue(r.audio_md5_checked)
        self.assertEqual(r.bytes_read, len(self.flac))
        self.assertIn("audio MD5 verified", r.message)

    def test_truncated_download_fails(self):
        r = stream_test(chunked(self.flac[: len(self.flac) // 2]))
        self.assertFalse(r.ok)
        self.assertIn("failed", r.message)

    def test_flipped_byte_in_the_audio_fails(self):
        damaged = bytearray(self.flac)
        damaged[len(damaged) // 2] ^= 0xFF
        r = stream_test(chunked(bytes(damaged)))
        self.assertFalse(r.ok, "a corrupted frame must not pass")

    def test_manifest_md5_mismatch_fails_even_if_it_decodes(self):
        r = stream_test(chunked(self.flac), expected_md5="0" * 32)
        self.assertFalse(r.ok)
        self.assertFalse(r.md5_ok)
        self.assertIn("md5 differs", r.message)

    def test_size_mismatch_fails(self):
        r = stream_test(chunked(self.flac), expected_size=len(self.flac) + 1)
        self.assertFalse(r.ok)
        self.assertIn("IA lists", r.message)

    def test_connection_drop_is_reported_not_raised(self):
        def dropping():
            yield self.flac[:5000]
            raise ConnectionError("reset by peer")
        r = stream_test(dropping())
        self.assertFalse(r.ok)
        self.assertIn("transfer failed after 5000 bytes", r.message)

    def test_empty_stream_fails(self):
        self.assertFalse(stream_test(iter([])).ok)

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg required")
    def test_mp3_named_flac_fails(self):
        mp3 = ffmpeg_convert(tone_wav(44100), "wav", ["-c:a", "libmp3lame", "-b:a", "128k"], "mp3")
        self.assertFalse(stream_test(chunked(mp3)).ok)


if __name__ == "__main__":
    unittest.main()
