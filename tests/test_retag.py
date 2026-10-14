"""Library → Edit tags: reading an album's tags, filling them from MusicBrainz, previewing, and writing only what
changed. Files are built byte by byte (see test_health), so no flac or ffmpeg is needed."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from iaflac import retag
from iaflac.tagging import read_track
from tests.test_health import FakeMB, flac_bytes, jpeg, mb_release, mp3_bytes, write_track


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Music"
        self.folder = self.root / "Test Artist" / "Test Albm"
        for n, title in ((1, "One"), (2, "Too"), (3, "Three")):
            write_track(self.folder / f"{n:02d} {title}.flac", flac_bytes(), title, n, album="Test Albm", total=4,
                        art=jpeg(300), tags={"REPLAYGAIN_TRACK_GAIN": "-6.5 dB", "COMMENT": "ripped by me", "LABEL": "Blue Note"})

    def tearDown(self):
        self.tmp.cleanup()


class ReadAndWrite(Base):
    def test_read(self):
        meta, skipped = retag.read_album(self.folder)
        self.assertEqual((meta.albumartist, meta.album, meta.label, len(meta.tracks)), ("Test Artist", "Test Albm", "Blue Note", 3))
        self.assertEqual([t.source for t in meta.tracks], ["01 One.flac", "02 Too.flac", "03 Three.flac"])
        self.assertEqual(skipped, [])

    def test_only_the_edited_tags_change_and_everything_else_stays(self):
        meta, _ = retag.read_album(self.folder)
        meta.album = "Test Album"
        meta.tracks[1].title = "Two"
        old = {p.name: p.stat().st_mtime_ns for p in self.folder.iterdir()}
        diff = retag.preview(retag.read_album(self.folder)[0], meta)
        self.assertEqual({(d["field"], d["new"]) for d in diff}, {("album", "Test Album"), ("title", "Two")})
        out = retag.write(self.folder, meta)
        self.assertEqual(sorted(out["written"]), ["01 One.flac", "02 Too.flac", "03 Three.flac"], "the album name is in every file")
        from mutagen.flac import FLAC
        f = FLAC(str(self.folder / "02 Too.flac"))
        self.assertEqual((f["TITLE"][0], f["ALBUM"][0]), ("Two", "Test Album"))
        self.assertEqual((f["REPLAYGAIN_TRACK_GAIN"][0], f["COMMENT"][0], f["LABEL"][0]), ("-6.5 dB", "ripped by me", "Blue Note"))
        self.assertEqual(len(f.pictures), 1, "the embedded cover stays")
        self.assertEqual(f["TRACKTOTAL"][0], "4", "the album still says it has 4 tracks: one is missing")
        self.assertTrue(all(old[n] for n in old))

    def test_files_that_do_not_change_are_not_touched(self):
        meta, _ = retag.read_album(self.folder)
        retag.write(self.folder, meta)                           # once, so the tags are in their written form
        before = {p.name: p.stat().st_mtime_ns for p in self.folder.iterdir()}
        time.sleep(0.02)
        meta, _ = retag.read_album(self.folder)
        meta.tracks[0].title = "Uno"
        out = retag.write(self.folder, meta)
        self.assertEqual((out["written"], out["unchanged"]), (["01 One.flac"], 2))
        after = {p.name: p.stat().st_mtime_ns for p in self.folder.iterdir()}
        self.assertEqual(before["02 Too.flac"], after["02 Too.flac"])

    def test_mp3_too(self):
        folder = self.root / "B" / "Mp3"
        write_track(folder / "01 a.mp3", mp3_bytes(), "a", 1, artist="B", album="Mp3")
        meta, _ = retag.read_album(folder)
        meta.tracks[0].title, meta.genre = "Alpha", "Jazz"
        retag.write(folder, meta)
        t = read_track(folder / "01 a.mp3")
        self.assertEqual((t["title"], t["genre"], t["album"]), ("Alpha", "Jazz", "Mp3"))

    def test_rename_follows_the_albums_style_and_never_overwrites(self):
        meta, _ = retag.read_album(self.folder)
        meta.tracks[1].title = "Two"
        (self.folder / "03 Three (copy).flac").write_bytes(b"not a track")   # an unrelated file with a name nearby
        out = retag.write(self.folder, meta, rename=True)
        self.assertEqual(out["renamed"], [["02 Too.flac", "02 Two.flac"]])
        self.assertTrue((self.folder / "02 Two.flac").is_file())

    def test_a_file_outside_the_album_is_refused(self):
        meta, _ = retag.read_album(self.folder)
        meta.tracks[0].source = "../../elsewhere.flac"
        with self.assertRaises(retag.RetagError):
            retag.write(self.folder, meta)


class FromMusicBrainz(Base):
    def test_a_release_fills_the_tags_and_its_ids(self):
        meta, _ = retag.read_album(self.folder)
        rel = mb_release("rel-1", ["One", "Two", "Three"], title="Test Album", artist="Test Artist")
        new, warnings = retag.from_release(meta, rel)
        self.assertEqual(warnings, [])
        diff = retag.preview(meta, new)
        self.assertIn(("album", "Test Album"), {(d["field"], d["new"]) for d in diff})
        self.assertIn(("title", "Two"), {(d["field"], d["new"]) for d in diff})
        retag.write(self.folder, new)
        t = read_track(self.folder / "02 Too.flac")
        self.assertEqual((t["title"], t["album"], t["mb_release_id"]), ("Two", "Test Album", "rel-1"))
        self.assertEqual(t["tracktotal"], 3, "MusicBrainz's count of the release")

    def test_a_release_with_another_track_count_changes_only_the_album_fields(self):
        meta, _ = retag.read_album(self.folder)
        new, warnings = retag.from_release(meta, mb_release("rel-2", ["One", "Two", "Three", "Four", "Five"], title="Big"))
        self.assertTrue(warnings)
        self.assertEqual([t.title for t in new.tracks], ["One", "Too", "Three"])
        self.assertEqual(new.album, "Big")


class Api(Base):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        from iaflac.gui.server import create_app
        from iaflac.gui.service import App
        from iaflac.gui.settings import Settings
        from tests.test_health import FakeIA
        base = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"IAFLAC_CONFIG": str(base / "config.json"), "IAFLAC_CACHE": str(base / "cache"),
                                                "IAFLAC_DATA": str(base / "data")})
        self.env.start()
        self.core = App(settings=Settings(library=str(self.root)), client=FakeIA([]),
                        mb_client=FakeMB([mb_release("rel-1", ["One", "Two", "Three"], title="Test Album")]),
                        queue_store=base / "queue.json")
        self.http = TestClient(create_app(self.core, token="t"))
        self.http.get("/?token=t")

    def tearDown(self):
        self.http.close()
        self.core.shutdown()
        self.env.stop()
        super().tearDown()

    def test_read_preview_save(self):
        folder = "Test Artist/Test Albm"
        meta = self.http.get("/api/library/tags", params={"folder": folder}).json()["meta"]
        r = self.http.post("/api/library/tags/preview", json={"folder": folder, "meta": meta, "release_id": "rel-1"}).json()
        self.assertTrue(any(d["field"] == "album" for d in r["diff"]))
        saved = self.http.put("/api/library/tags", json={"folder": folder, "meta": r["meta"], "rename": True}).json()
        self.assertEqual(len(saved["written"]), 3)
        self.assertEqual(saved["renamed"], [["02 Too.flac", "02 Two.flac"]])
        again = self.http.get("/api/library/tags", params={"folder": folder}).json()["meta"]
        self.assertEqual((again["album"], again["mb_release_id"]), ("Test Album", "rel-1"))
        bad = self.http.put("/api/library/tags", json={"folder": "../..", "meta": meta})
        self.assertIn(bad.status_code, (400, 404))


if __name__ == "__main__":
    unittest.main()
