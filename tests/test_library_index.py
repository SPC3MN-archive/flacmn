import tempfile
import unittest
from pathlib import Path
from unittest import mock

from iaflac import library_index as li, tagging
from iaflac.tagging import AlbumMeta, TrackMeta
from tests.helpers import HAVE_FFMPEG, HAVE_FLAC, encode_flac, ffmpeg_convert, tone_wav

NEED = unittest.skipUnless(HAVE_FLAC and HAVE_FFMPEG, "flac and ffmpeg required")


@NEED
class Scan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flac = encode_flac(tone_wav(44100))
        cls.mp3 = ffmpeg_convert(tone_wav(44100), "wav", ["-codec:a", "libmp3lame", "-b:a", "320k"], "mp3")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Music"
        # a tagged FLAC album in the Volumio layout
        kob = self.root / "Miles Davis" / "Kind of Blue (1959)"
        kob.mkdir(parents=True)
        paths = {}
        for i in (1, 2):
            p = kob / f"{i:02d}.flac"
            p.write_bytes(self.flac)
            paths[p.name] = p
        meta = AlbumMeta(albumartist="Miles Davis", album="Kind of Blue", date="1959",
                         tracks=[TrackMeta("01.flac", "So What", 1), TrackMeta("02.flac", "Freddie Freeloader", 2)])
        tagging.apply(kob, meta, paths)
        # an untagged MP3 album with disc folders
        for d in (1, 2):
            disc = self.root / "Radiohead" / "OK Computer" / f"CD{d}"
            disc.mkdir(parents=True)
            (disc / "01 Airbag.mp3").write_bytes(self.mp3)
        self.cache = Path(self.tmp.name) / "index.json"

    def tearDown(self):
        self.tmp.cleanup()

    def scan(self, **kw):
        return li.scan(self.root, cache=self.cache, **kw)

    def test_albums_names_formats_and_issues(self):
        index = self.scan()
        by_folder = {a.folder: a for a in index.albums}
        self.assertEqual(sorted(by_folder), ["Miles Davis/Kind of Blue (1959)", "Radiohead/OK Computer"])
        kob = by_folder["Miles Davis/Kind of Blue (1959)"]
        self.assertEqual((kob.artist, kob.album, kob.year, kob.tracks, kob.lossless, kob.format_label),
                         ("Miles Davis", "Kind of Blue", "1959", 2, True, "FLAC 16/44.1"))
        self.assertTrue(any("cover art" in i for i in kob.issues))
        okc = by_folder["Radiohead/OK Computer"]
        self.assertEqual((okc.artist, okc.album, okc.tracks, okc.lossless), ("Radiohead", "OK Computer", 2, False))
        self.assertTrue(okc.format_label.startswith("MP3 3"), okc.format_label)
        self.assertTrue(any("TITLE" in i for i in okc.issues), okc.issues)

    def test_find_matches_what_you_already_have(self):
        index = self.scan()
        self.assertEqual(index.find("miles davis", "Kind Of Blue").folder, "Miles Davis/Kind of Blue (1959)")
        self.assertIsNone(index.find("Miles Davis", "Sketches of Spain"))
        self.assertIsNone(index.find("Radiohead", "Kid A"))

    def test_unchanged_albums_come_from_the_cache(self):
        self.scan()
        with mock.patch.object(li, "read_album", side_effect=AssertionError("re-read")):
            self.assertEqual(len(self.scan().albums), 2)

    def test_a_changed_album_is_read_again(self):
        self.scan()
        (self.root / "Radiohead" / "OK Computer" / "CD2" / "02 Paranoid Android.mp3").write_bytes(self.mp3)
        calls = []
        real = li.read_album
        with mock.patch.object(li, "read_album", side_effect=lambda *a: calls.append(a[1]) or real(*a)):
            index = self.scan()
        self.assertEqual([p.name for p in calls], ["OK Computer"])
        self.assertEqual({a.folder: a.tracks for a in index.albums}["Radiohead/OK Computer"], 3)

    def test_a_failed_check_is_tried_again_instead_of_remembered(self):
        with mock.patch.object(li, "validate", side_effect=OSError("drive went away")):
            first = self.scan()
        self.assertTrue(all(a.issues[0].startswith(li.CHECK_FAILED) for a in first.albums if a.issues), first.albums)
        again = self.scan()
        self.assertFalse(any(i.startswith(li.CHECK_FAILED) for a in again.albums for i in a.issues),
                         "the next scan checks those albums again rather than reusing the failure")

    def test_progress_events(self):
        events = []
        self.scan(progress=events.append)
        self.assertEqual([e["index"] for e in events], [1, 2])

    def test_missing_folder(self):
        with self.assertRaises(FileNotFoundError):
            li.scan(self.root / "nope", cache=self.cache)


if __name__ == "__main__":
    unittest.main()


class Quality(unittest.TestCase):
    """The album quality the Library's Quality column and Now playing show: any high-quality audio is the goal, so
    lossless and hi-res are labelled apart but neither ranks above the other."""

    def test_lossless_hires_lossy_and_unknown(self):
        from iaflac.library_index import AlbumEntry
        q = lambda formats, lossless: AlbumEntry("A/B", "A", "B", formats=formats, lossless=lossless).quality
        self.assertEqual(q({"FLAC 16/44.1": 10}, True), "lossless")
        self.assertEqual(q({"FLAC 24/96": 10}, True), "hires")
        self.assertEqual(q({"FLAC 16/48": 9, "FLAC 16/88.2": 1}, True), "hires")
        self.assertEqual(q({"MP3 320": 10}, False), "lossy")
        self.assertEqual(q({}, False), "")
