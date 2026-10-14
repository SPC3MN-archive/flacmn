"""Library Health Check: missing songs, low quality, missing art and duplicates, the Internet Archive search, and the
fixes (fetching tracks, saving a cover, merging duplicates). The audio files are built byte by byte (a FLAC STREAMINFO
with no frames, MP3 frames of silence), so these tests need neither flac nor ffmpeg."""

import hashlib
import io
import struct
import tempfile
import unittest
from pathlib import Path

from iaflac import health
from iaflac.model import ItemInfo


def flac_bytes(seconds: float = 2.0, rate: int = 44100, bits: int = 16, channels: int = 2) -> bytes:
    total = int(seconds * rate)
    packed = (rate << 44) | ((channels - 1) << 41) | ((bits - 1) << 36) | total
    info = struct.pack(">HH", 4096, 4096) + b"\0\0\0" + b"\0\0\0" + packed.to_bytes(8, "big") + bytes(16)
    return b"fLaC" + bytes([0x80, 0, 0, 34]) + info


def mp3_bytes(kbps: int = 128, frames: int = 40) -> bytes:
    index = {128: 0x9, 192: 0xB, 256: 0xD, 320: 0xE}[kbps]
    header = bytes([0xFF, 0xFB, index << 4, 0x00])
    size = 144 * kbps * 1000 // 44100
    return (header + bytes(size - 4)) * frames


def jpeg(px: int = 600) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (px, px), (200, 40, 40)).save(out, "JPEG")
    return out.getvalue()


def write_track(path: Path, data: bytes, title: str, track: int, artist="Test Artist", album="Test Album",
                total: int = 0, disc: int = 0, art: bytes = None, tags: dict = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if path.suffix == ".flac":
        from mutagen.flac import FLAC, Picture
        f = FLAC(str(path))
        f.add_tags()
        f["TITLE"], f["ARTIST"], f["ALBUMARTIST"], f["ALBUM"] = title, artist, artist, album
        f["TRACKNUMBER"] = str(track)
        if total:
            f["TRACKTOTAL"] = str(total)
        if disc:
            f["DISCNUMBER"] = str(disc)
        if art:
            pic = Picture()
            pic.type, pic.mime, pic.data = 3, "image/jpeg", art
            f.add_picture(pic)
        for k, v in (tags or {}).items():
            f[k] = v
        f.save()
    else:
        from mutagen.id3 import ID3, TALB, TIT2, TPE1, TPE2, TRCK
        id3 = ID3()
        for frame in (TIT2(encoding=3, text=title), TPE1(encoding=3, text=artist), TPE2(encoding=3, text=artist),
                      TALB(encoding=3, text=album), TRCK(encoding=3, text=f"{track}/{total}" if total else str(track))):
            id3.add(frame)
        id3.save(str(path))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Music"
        self.root.mkdir()
        self.trash = Path(self.tmp.name) / "Trash"
        self.trash.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def album(self, rel: str, tracks: list, fmt: str = "flac", cover: bytes = None, **kw) -> Path:
        """tracks: [(number, title)]"""
        folder = self.root / rel
        for n, title in tracks:
            data = flac_bytes() if fmt == "flac" else mp3_bytes(kw.get("kbps", 128))
            write_track(folder / f"{n:02d} - {title}.{fmt}", data, title, n,
                        **{k: v for k, v in kw.items() if k in ("artist", "album", "total", "art")})
        if cover:
            (folder / "cover.jpg").write_bytes(cover)
        return folder

    def scan(self, **kw):
        return health.scan(self.root, cache=Path(self.tmp.name) / "health.json", **kw)


class Checks(Base):
    def test_a_complete_flac_album_with_a_cover_is_healthy(self):
        self.album("Test Artist/Test Album", [(1, "One"), (2, "Two"), (3, "Three")], cover=jpeg())
        a = self.scan().albums[0]
        self.assertEqual(a.findings, [], a.findings)
        self.assertEqual((a.artist, a.album, len(a.tracks)), ("Test Artist", "Test Album", 3))

    def test_gaps_and_tracktotal_are_missing_songs(self):
        self.album("Test Artist/Test Album", [(1, "One"), (2, "Two"), (4, "Four")], total=6, cover=jpeg())
        a = self.scan().albums[0]
        f = [f for f in a.findings if f.kind == health.MISSING][0]
        self.assertEqual([m["track"] for m in f.missing], [3, 5, 6])
        self.assertIn("3, 5, 6 of 6", f.text.replace("tracks ", ""))

    def test_unnumbered_tracks_are_not_judged_on_numbering(self):
        folder = self.root / "Test Artist" / "Loose"
        write_track(folder / "Song.flac", flac_bytes(), "Song", 0)
        a = self.scan().albums[0]
        self.assertNotIn(health.MISSING, a.kinds)

    def test_lossy_low_bitrate_and_mixed_formats(self):
        self.album("A/Lossy", [(1, "One"), (2, "Two")], fmt="mp3", artist="A", album="Lossy", cover=jpeg())
        folder = self.album("B/Mixed", [(1, "One")], artist="B", album="Mixed", cover=jpeg())
        write_track(folder / "02 - Two.mp3", mp3_bytes(320), "Two", 2, artist="B", album="Mixed")
        by = {a.folder: a for a in self.scan().albums}
        lossy = [f for f in by["A/Lossy"].findings if f.kind == health.QUALITY][0]
        self.assertEqual(lossy.severity, "bad")
        self.assertIn("128 kbps", lossy.text)
        mixed = [f for f in by["B/Mixed"].findings if f.kind == health.QUALITY][0]
        self.assertTrue(mixed.text.startswith("Mixed formats"), mixed.text)
        self.assertEqual(mixed.severity, "warn", "a 320 kbps MP3 is high bitrate")

    def test_below_cd_resolution(self):
        folder = self.root / "C" / "Low"
        write_track(folder / "01 - One.flac", flac_bytes(rate=22050, bits=8), "One", 1, artist="C", album="Low")
        (folder / "cover.jpg").write_bytes(jpeg())
        a = self.scan().albums[0]
        self.assertTrue(any("below CD" in f.text for f in a.findings), a.findings)

    def test_missing_small_and_embedded_art(self):
        self.album("A/None", [(1, "One")], artist="A", album="None")
        self.album("B/Small", [(1, "One")], artist="B", album="Small", cover=jpeg(200))
        self.album("C/Embedded", [(1, "One")], artist="C", album="Embedded", art=jpeg(800))
        by = {a.folder: a for a in self.scan().albums}
        self.assertEqual([f.severity for f in by["A/None"].findings if f.kind == health.ART], ["bad"])
        self.assertIn("200 px", [f.text for f in by["B/Small"].findings if f.kind == health.ART][0])
        self.assertNotIn(health.ART, by["C/Embedded"].kinds)

    def test_unchanged_albums_come_from_the_cache_and_a_new_cover_is_noticed(self):
        folder = self.album("A/Album", [(1, "One")], artist="A", album="Album")
        self.assertIn(health.ART, self.scan().albums[0].kinds)
        from unittest import mock
        with mock.patch.object(health, "read_album", side_effect=AssertionError("re-read")):
            self.assertIn(health.ART, self.scan().albums[0].kinds)
        (folder / "cover.jpg").write_bytes(jpeg())
        self.assertNotIn(health.ART, self.scan().albums[0].kinds, "adding a cover changes the album's signature")


class FakeMB:
    def __init__(self, releases):
        self.releases = {r["id"]: r for r in releases}
        self.looked_up = []

    def search_releases(self, artist, album, limit=15):
        return [{"id": r["id"], "title": r["title"], "score": 100, "status": "Official",
                 "artist-credit": r["artist-credit"], "track-count": sum(len(m["tracks"]) for m in r["media"])}
                for r in self.releases.values()]

    def release(self, rid):
        self.looked_up.append(rid)
        return self.releases[rid]

    def art_listing(self, rid):
        return [{"types": ["Front"], "image": f"https://coverartarchive.org/{rid}/front.jpg", "thumbnails": {}}]


def mb_release(rid, titles, title="Test Album", artist="Test Artist"):
    return {"id": rid, "title": title, "date": "2001", "country": "US", "status": "Official",
            "artist-credit": [{"name": artist}],
            "media": [{"format": "CD", "tracks": [{"title": t, "length": 200000} for t in titles]}]}


class MusicBrainz(Base):
    def test_names_the_missing_songs_from_the_smallest_release_that_fits(self):
        self.album("Test Artist/Test Album", [(1, "One"), (2, "Two"), (3, "Four")], cover=jpeg())
        mb = FakeMB([mb_release("std", ["One", "Two", "Three", "Four"]),
                     mb_release("dlx", ["One", "Two", "Three", "Four", "Bonus", "Demo"])])
        a = self.scan(mb_client=mb).albums[0]
        f = [f for f in a.findings if f.kind == health.MISSING][0]
        self.assertEqual([m["title"] for m in f.missing], ["Three"], "the standard edition, not the deluxe one")
        self.assertIn("compared with Test Album", a.musicbrainz)

    def test_a_complete_album_clears_a_numbering_guess(self):
        self.album("Test Artist/Test Album", [(1, "One"), (3, "Two")], cover=jpeg())
        mb = FakeMB([mb_release("std", ["One", "Two"])])
        a = self.scan(mb_client=mb).albums[0]
        self.assertNotIn(health.MISSING, a.kinds)
        self.assertTrue(a.musicbrainz.startswith("complete"))


class Duplicates(Base):
    def test_the_same_album_in_two_folders(self):
        self.album("Test Artist/Test Album", [(1, "One"), (2, "Two")], fmt="mp3", cover=jpeg())
        self.album("Test Artist/Test Album (Deluxe Edition)", [(1, "One"), (2, "Two"), (3, "Bonus")], cover=jpeg())
        albums = self.scan().albums
        self.assertTrue(all(health.DUPES in a.kinds for a in albums))
        f = [f for f in albums[0].findings if f.kind == health.DUPES][0]
        self.assertEqual(len(f.folders), 2)

    def test_a_song_twice_in_one_folder_but_not_three_interludes(self):
        folder = self.album("A/Album", [(1, "One"), (2, "Interlude"), (3, "Interlude")], artist="A", album="Album",
                            cover=jpeg())
        write_track(folder / "01 - One.mp3", mp3_bytes(), "One", 1, artist="A", album="Album")
        a = self.scan().albums[0]
        dupes = [f for f in a.findings if f.kind == health.DUPES]
        self.assertEqual(len(dupes), 1)
        self.assertIn("1 song twice", dupes[0].text)

    def test_merge_keeps_the_best_copy_of_every_song_and_trashes_the_rest(self):
        mp3 = self.album("Test Artist/Test Album", [(1, "One"), (2, "Two"), (3, "Three")], fmt="mp3", cover=jpeg())
        (mp3 / "booklet.pdf").write_bytes(b"%PDF")
        flac = self.album("Test Artist/Test Album [FLAC]", [(1, "One"), (2, "Two")], album="Test Album [FLAC]")
        folders = ["Test Artist/Test Album", "Test Artist/Test Album [FLAC]"]
        plan = health.plan_merge(self.root, folders)
        self.assertEqual(plan["target"], "Test Artist/Test Album [FLAC]", "the folder with lossless songs stays")
        self.assertEqual((plan["moves"], plan["drops"]), (1, 2))
        three = next(s for s in plan["songs"] if s["title"] == "Three")
        self.assertEqual(three["action"], "move", "a song only the MP3 copy has is kept, not lost")

        result = health.apply_merge(self.root, folders, plan["token"], trash=self.trash)
        names = sorted(p.name for p in flac.iterdir())
        self.assertEqual(names, ["01 - One.flac", "02 - Two.flac", "03 - Three.mp3", "booklet.pdf", "cover.jpg"])
        self.assertFalse(mp3.exists())
        self.assertEqual(result["trashed_folders"], ["Test Artist/Test Album"])
        trashed = [p for p in self.trash.iterdir()]
        self.assertEqual(len(trashed), 1)
        self.assertEqual(sorted(p.name for p in trashed[0].iterdir() if p.suffix == ".mp3"), ["01 - One.mp3", "02 - Two.mp3"],
                         "the worse copies are in the Trash, not deleted")
        from iaflac.tagging import read_track
        self.assertEqual(read_track(flac / "03 - Three.mp3")["album"], "Test Album [FLAC]", "re-tagged to the album")

    def test_merging_one_folder_trashes_its_worse_copies(self):
        folder = self.album("A/Album", [(1, "One"), (2, "Two")], artist="A", album="Album", cover=jpeg())
        write_track(folder / "01 - One.mp3", mp3_bytes(), "One", 1, artist="A", album="Album")
        plan = health.plan_merge(self.root, ["A/Album"])
        health.apply_merge(self.root, ["A/Album"], plan["token"], trash=self.trash)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["01 - One.flac", "02 - Two.flac", "cover.jpg"])
        self.assertEqual([p.name for p in next(self.trash.iterdir()).iterdir()], ["01 - One.mp3"])

    def test_a_stale_plan_is_refused(self):
        self.album("A/Album", [(1, "One")], artist="A", album="Album")
        folder = self.album("A/Album (2)", [(1, "One")], artist="A", album="Album")
        plan = health.plan_merge(self.root, ["A/Album", "A/Album (2)"])
        write_track(folder / "02 - Two.flac", flac_bytes(), "Two", 2, artist="A", album="Album")
        with self.assertRaises(health.HealthError):
            health.apply_merge(self.root, ["A/Album", "A/Album (2)"], plan["token"], trash=self.trash)
        self.assertTrue((self.root / "A" / "Album").is_dir(), "nothing was moved")


class FakeItem:
    def __init__(self, identifier, title, creator, files: dict, fmt="Flac"):
        self.identifier, self.files = identifier, files
        self.doc = {"identifier": identifier, "title": title, "creator": creator, "downloads": 10}
        meta = [{"name": n, "format": fmt if n.endswith((".flac", ".mp3")) else "JPEG", "source": "original",
                 "size": str(len(d)), "md5": hashlib.md5(d).hexdigest(),
                 **({"title": n[5:].rsplit(".", 1)[0], "track": n[:2]} if n.endswith(".flac") else {})}
                for n, d in files.items()]
        self.metadata = {"metadata": {"identifier": identifier, "title": title, "creator": creator},
                         "files": meta}


class FakeResp:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def iter_content(self, n):
        yield self.data

    def close(self):
        pass


class FakeIA:
    max_concurrency = 2
    bytes_read = 0

    def __init__(self, items):
        self.items = {i.identifier: i for i in items}

    def search_iter(self, q, *, fields=None, sort=None, max_hits=100, page_size=100):
        for i in self.items.values():
            if "format:Flac" in q and not any(n.endswith(".flac") for n in i.files):
                continue
            yield {**i.doc, "format": ["Flac"]}

    def metadata(self, identifier):
        return self.items[identifier].metadata

    def open_stream(self, identifier, name, start=0):
        return FakeResp(self.items[identifier].files[name][start:])


class Archive(Base):
    def setUp(self):
        super().setUp()
        self.album("Test Artist/Test Album", [(1, "One"), (2, "Two"), (4, "Four")], total=4)
        files = {f"{n:02d} - {t}.flac": flac_bytes(seconds=1 + n) for n, t in ((1, "One"), (2, "Two"), (3, "Three"), (4, "Four"))}
        files["cover.jpg"] = jpeg()
        self.ia = FakeIA([FakeItem("test-album-flac", "Test Artist - Test Album", "Test Artist", files)])
        self.h = self.scan().albums[0]

    def test_search_says_which_upload_has_the_missing_song_and_a_cover(self):
        out = health.search_archive(self.ia, self.h)
        s = out["suggestions"][0]
        self.assertEqual(s["identifier"], "test-album-flac")
        self.assertEqual([p["file"] for p in s["provides"]], ["03 - Three.flac"])
        self.assertTrue(s["provides_all"])
        self.assertIn(health.MISSING, s["fixes"])
        self.assertIn(health.ART, s["fixes"])
        self.assertTrue(any(a["identifier"] == "test-album-flac" for a in out["art"]))

    def test_fetch_adds_the_track_named_and_tagged_like_the_album(self):
        item = ItemInfo.from_metadata(self.ia.metadata("test-album-flac"))
        result = health.fetch_tracks(self.ia, self.root, self.h, item, ["03 - Three.flac"])
        self.assertEqual((result["added"], result["failures"]), (["03 - Three.flac"], []))
        from iaflac.tagging import read_track
        t = read_track(self.root / "Test Artist" / "Test Album" / "03 - Three.flac")
        self.assertEqual((t["title"], t["track"], t["album"], t["albumartist"]), ("Three", 3, "Test Album", "Test Artist"))
        self.assertNotIn(health.MISSING, self.scan().albums[0].kinds)
        with self.assertRaises(health.HealthError):            # never twice over an existing file
            health.fetch_tracks(self.ia, self.root, self.h, item, ["03 - Three.flac"])

    def test_a_checksum_mismatch_adds_nothing(self):
        item = ItemInfo.from_metadata(self.ia.metadata("test-album-flac"))
        self.ia.items["test-album-flac"].files["03 - Three.flac"] = b"corrupt"
        result = health.fetch_tracks(self.ia, self.root, self.h, item, ["03 - Three.flac"])
        self.assertEqual(result["added"], [])
        self.assertIn("checksum", result["failures"][0])
        self.assertEqual(sorted(p.name for p in (self.root / "Test Artist" / "Test Album").iterdir()),
                         ["01 - One.flac", "02 - Two.flac", "04 - Four.flac"], "no leftovers")

    def test_save_cover_never_replaces_one_unless_asked(self):
        self.assertEqual(health.save_cover(self.root, self.h, jpeg())["written"], "cover.jpg")
        with self.assertRaises(health.HealthError):
            health.save_cover(self.root, self.h, jpeg())


if __name__ == "__main__":
    unittest.main()


class Api(Base):
    """The GUI's /api/health-check routes, end to end over HTTP: check, search, add a track, merge."""

    def setUp(self):
        super().setUp()
        import os
        from unittest import mock
        from fastapi.testclient import TestClient
        from iaflac.gui.server import create_app
        from iaflac.gui.service import App
        from iaflac.gui.settings import Settings

        base = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"IAFLAC_CONFIG": str(base / "config.json"), "IAFLAC_CACHE": str(base / "cache"),
                                                "IAFLAC_DATA": str(base / "data"), "IAFLAC_TRASH": str(self.trash)})
        self.env.start()
        self.album("Test Artist/Test Album", [(1, "One"), (2, "Two"), (4, "Four")], total=4, cover=jpeg())
        self.album("Test Artist/Test Album (MP3)", [(1, "One"), (2, "Two")], fmt="mp3", album="Test Album (MP3)")
        files = {f"{n:02d} - {t}.flac": flac_bytes(seconds=1 + n) for n, t in ((1, "One"), (2, "Two"), (3, "Three"), (4, "Four"))}
        self.core = App(settings=Settings(library=str(self.root)), client=FakeIA([FakeItem("t-flac", "Test Artist - Test Album", "Test Artist", files)]),
                        mb_client=FakeMB([]), queue_store=base / "queue.json")
        self.http = TestClient(create_app(self.core, token="t"))
        self.http.get("/?token=t")

    def tearDown(self):
        self.http.close()
        self.core.shutdown()
        self.env.stop()
        super().tearDown()

    def wait(self, job_id):
        import time
        for _ in range(200):
            j = self.http.get(f"/api/jobs/{job_id}").json()
            if j["state"] in ("done", "failed", "cancelled"):
                self.assertEqual(j["state"], "done", j.get("error"))
                return j["result"]
            time.sleep(0.05)
        self.fail("job did not finish")

    def test_check_search_fetch_and_merge(self):
        self.assertFalse(self.http.get("/api/health-check").json()["checked"])
        self.wait(self.http.post("/api/health-check/scan", json={}).json()["job_id"])
        report = self.http.get("/api/health-check").json()
        self.assertEqual(report["counts"]["duplicates"], 2)
        self.assertEqual(self.http.get("/api/health").status_code, 200, "the app's own status route still works")

        folder = "Test Artist/Test Album"
        found = self.wait(self.http.post("/api/health-check/search", json={"folder": folder}).json()["job_id"])
        self.assertEqual(found["suggestions"][0]["provides"][0]["file"], "03 - Three.flac")
        r = self.http.post("/api/health-check/fetch", json={"folder": folder, "identifier": "t-flac", "files": ["03 - Three.flac"]})
        self.assertEqual(self.wait(r.json()["job_id"])["added"], ["03 - Three.flac"])
        album = self.http.get("/api/health-check/album", params={"folder": folder}).json()
        self.assertNotIn("missing_tracks", album["kinds"])
        bad = self.http.post("/api/health-check/fetch", json={"folder": folder, "identifier": "t-flac", "files": ["03 - Three.flac"]})
        self.assertEqual(bad.status_code, 400, "a file already there is refused at once")

        folders = [folder, "Test Artist/Test Album (MP3)"]
        plan = self.http.post("/api/health-check/merge-plan", json={"folders": folders}).json()
        self.assertEqual((plan["target"], plan["drops"]), (folder, 2))
        self.wait(self.http.post("/api/health-check/merge", json={"folders": folders, "token": plan["token"]}).json()["job_id"])
        report = self.http.get("/api/health-check").json()
        self.assertEqual([a["folder"] for a in report["albums"]], [folder])
        self.assertEqual(report["counts"]["duplicates"], 0)


class Replace(Base):
    """Replacing a cover or the whole album with what the Internet Archive search found: the old one goes to the Trash."""

    def test_replace_cover_trashes_the_old_one_and_re_embeds(self):
        self.album("A/Album", [(1, "One"), (2, "Two")], artist="A", album="Album", cover=jpeg(200), art=jpeg(200))
        h = self.scan().albums[0]
        with self.assertRaises(health.HealthError):
            health.save_cover(self.root, h, jpeg(900))           # not without being asked to replace
        out = health.save_cover(self.root, h, jpeg(900), replace=True, trash=self.trash)
        self.assertEqual((out["written"], out["embedded"]), ("cover.jpg", 2))
        trashed = sorted(p.name for p in Path(out["trash"]).iterdir())
        self.assertEqual(trashed, ["cover.jpg", "embedded cover.jpg"], "the old cover can be put back")
        self.assertEqual(health.image_px(Path(out["trash"], "cover.jpg").read_bytes()), 200)
        again = self.scan().albums[0]
        self.assertEqual(again.cover_px, 900)
        self.assertEqual(health.image_px(health.embedded_cover(self.root / "A/Album/01 - One.flac")), 900)

    def test_replace_album_downloads_verifies_then_swaps(self):
        folder = self.album("Test Artist/Test Album", [(1, "One"), (2, "Two")], fmt="mp3", cover=jpeg())
        (folder / "booklet.pdf").write_bytes(b"%PDF")
        files = {f"{n:02d} - {t}.flac": flac_bytes(seconds=1 + n) for n, t in ((1, "One"), (2, "Two"), (3, "Three"))}
        ia = FakeIA([FakeItem("t-flac", "Test Artist - Test Album (2009 Remaster)", "Test Artist", files)])
        from iaflac.pipeline import Pipeline
        cand = Pipeline(ia).load("t-flac")
        h = self.scan().albums[0]
        out = health.replace_album(ia, self.root, h, cand, trash=self.trash)
        self.assertTrue(out["ok"], out)
        names = sorted(p.name for p in folder.iterdir())
        self.assertEqual([n for n in names if n.endswith(".flac")], ["01 - One.flac", "02 - Two.flac", "03 - Three.flac"])
        self.assertIn("booklet.pdf", names, "what is not audio comes along")
        self.assertIn("cover.jpg", names)
        from iaflac.tagging import read_track
        self.assertEqual(read_track(folder / "03 - Three.flac")["album"], "Test Album", "your album name is kept")
        old = Path(out["trash"])
        self.assertEqual(sorted(p.name for p in old.iterdir() if p.suffix == ".mp3"), ["01 - One.mp3", "02 - Two.mp3"])
        self.assertFalse(any(p.name.startswith(".iaflac-new") for p in folder.parent.iterdir()), "no staging left")

    def test_a_failed_download_leaves_the_album_alone(self):
        folder = self.album("Test Artist/Test Album", [(1, "One")], fmt="mp3")
        files = {"01 - One.flac": flac_bytes()}
        item = FakeItem("t-flac", "Test Artist - Test Album", "Test Artist", files)
        ia = FakeIA([item])
        from iaflac.pipeline import Pipeline
        cand = Pipeline(ia).load("t-flac")
        item.files["01 - One.flac"] = b"corrupt"                 # what arrives no longer matches the checksum
        out = health.replace_album(ia, self.root, self.scan().albums[0], cand, trash=self.trash)
        self.assertFalse(out["ok"])
        self.assertEqual([p.name for p in folder.iterdir()], ["01 - One.mp3"])
        self.assertEqual(list(self.trash.iterdir()), [])

    def test_a_lossy_upload_never_replaces_an_album(self):
        self.album("Test Artist/Test Album", [(1, "One")])
        ia = FakeIA([FakeItem("t-mp3", "Test Artist - Test Album", "Test Artist", {"01 - One.mp3": mp3_bytes()}, fmt="VBR MP3")])
        from iaflac.pipeline import Pipeline
        cand = Pipeline(ia).load("t-mp3", media="mp3")
        with self.assertRaises(health.HealthError):
            health.replace_album(ia, self.root, self.scan().albums[0], cand, trash=self.trash)


class FakeMBIds:
    """MusicBrainz where the album is called something else ("The Test Album (Remastered)"), so a search by the
    album's name finds nothing; only the recording IDs or the song titles lead to it."""

    def __init__(self):
        titles = ["One", "Two", "Three", "Four"]
        self.album = mb_release("rel-album", titles, title="Test Album: The Remastered Collection")
        self.single = mb_release("rel-single", ["One"], title="One (single)")
        for rel in (self.album, self.single):
            for t in rel["media"][0]["tracks"]:
                t["id"] = f"trk-{rel['id']}-{t['title']}"
                t["recording"] = {"id": f"rec-{t['title']}", "title": t["title"]}
        self.calls = []

    def search_releases(self, artist, album, limit=15):
        self.calls.append("search_releases")
        return []

    def recording_releases(self, rid):
        self.calls.append(("recording", rid))
        out = [{"id": "rel-single", "title": "One (single)", "release-group": {"primary-type": "Single"}, "media": [{"track-count": 1}]}]
        return out + [{"id": "rel-album", "title": self.album["title"], "status": "Official",
                       "release-group": {"primary-type": "Album"}, "media": [{"track-count": 4}]}]

    def releases_with_track(self, tid):
        self.calls.append(("track", tid))
        return []

    def search_recordings(self, artist, title, limit=15):
        self.calls.append(("search_recordings", title))
        return [{"id": f"rec-{title}", "releases": [{"id": "rel-album", "title": self.album["title"],
                                                    "media": [{"track-count": 4}]}]}]

    def release(self, rid):
        self.calls.append(("release", rid))
        return {"rel-album": self.album, "rel-single": self.single}[rid]


class MusicBrainzByIds(Base):
    def test_track_ids_find_an_album_named_differently(self):
        folder = self.root / "Test Artist" / "Test Album (2011 Remaster)"
        for n, t in ((1, "One"), (2, "Two"), (4, "Four")):
            write_track(folder / f"{n:02d} {t}.flac", flac_bytes(), t, n, album="Test Album (2011 Remaster)",
                        tags={"MUSICBRAINZ_TRACKID": f"rec-{t}"})
        (folder / "cover.jpg").write_bytes(jpeg())
        mb = FakeMBIds()
        a = self.scan(mb_client=mb).albums[0]
        f = [f for f in a.findings if f.kind == health.MISSING][0]
        self.assertEqual([m["title"] for m in f.missing], ["Three"])
        self.assertIn(("recording", "rec-One"), mb.calls)
        self.assertIn("Remastered Collection", a.musicbrainz)
        self.assertEqual(a.mb_match, "rel-album", "the album, not the one-track single")
        self.assertEqual(a.mb_release_id, "", "a match is never taken for the tags' own release")

    def test_song_titles_find_it_when_there_are_no_ids(self):
        self.album("Test Artist/Test Album (2011 Remaster)", [(1, "One"), (2, "Two"), (3, "Three"), (4, "Four")],
                   album="Test Album (2011 Remaster)", cover=jpeg())
        mb = FakeMBIds()
        a = self.scan(mb_client=mb).albums[0]
        self.assertTrue(a.musicbrainz.startswith("complete"), a.musicbrainz)
        self.assertTrue(any(c[0] == "search_recordings" for c in mb.calls if isinstance(c, tuple)))

    def test_ids_match_even_when_a_title_is_spelled_differently(self):
        folder = self.root / "Test Artist" / "Test Album"
        for n, t in ((1, "One"), (2, "Two"), (3, "3"), (4, "Four")):          # "3" is "Three" on MusicBrainz
            write_track(folder / f"{n:02d} {t}.flac", flac_bytes(), t, n,
                        tags={"MUSICBRAINZ_TRACKID": f"rec-{'Three' if t == '3' else t}"})
        a = self.scan(mb_client=FakeMBIds()).albums[0]
        self.assertTrue(a.musicbrainz.startswith("complete"), a.musicbrainz)


class CoverRefresh(Api):
    """Replacing a cover from the Health Check: the old file goes, the new one is what the album shows, and the
    window is never left showing the old image from its cache."""

    def test_replace_cover_shows_the_new_one(self):
        folder = "Test Artist/Test Album"
        album_dir = self.root / folder
        (album_dir / "cover.jpg").unlink()
        (album_dir / "AlbumArt_{ABC}_Large.jpg").write_bytes(jpeg(200))      # what Windows Media Player leaves
        self.wait(self.http.post("/api/health-check/scan", json={}).json()["job_id"])
        before = self.http.get("/api/health-check/album", params={"folder": folder}).json()
        self.assertEqual(before["cover_file"], "AlbumArt_{ABC}_Large.jpg")
        self.core.health_art["img1"] = __import__("iaflac.cdart", fromlist=["ArtImage"]).ArtImage(
            id="img1", kind="front", source="upload", ref=str(Path(self.tmp.name) / "new.jpg"))
        Path(self.tmp.name, "new.jpg").write_bytes(jpeg(900))
        r = self.http.post("/api/health-check/cover", json={"folder": folder, "image_id": "img1", "replace": True})
        self.assertEqual(r.status_code, 200, r.text)
        after = self.http.get("/api/health-check/album", params={"folder": folder}).json()
        self.assertEqual(after["cover_file"], "cover.jpg")
        self.assertEqual(after["cover_px"], 900)
        self.assertNotIn("missing_art", after["kinds"])
        self.assertNotEqual(before["signature"], after["signature"], "the cover URL's version changes")
        self.assertFalse((album_dir / "AlbumArt_{ABC}_Large.jpg").exists(), "the old cover is gone from the album")
        img = self.http.get("/api/library/file", params={"path": f"{folder}/cover.jpg"})
        self.assertEqual(img.headers.get("cache-control"), "no-cache")
        self.assertEqual(health.image_px(img.content), 900)
