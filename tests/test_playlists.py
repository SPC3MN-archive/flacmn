"""Playlists (playlists.py and the /api/playlists routes): named song lists kept by the app, by path in the music folder."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from iaflac import playlists
from tests.test_health import Base, FakeMB


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = playlists.Store(Path(self.tmp.name) / "playlists.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_make_add_reorder_rename_delete(self):
        p = self.store.create("  Late   night ", ["A/B/01 - One.flac"])
        self.assertEqual(p.name, "Late night")
        self.store.add(p.id, ["A/B/02 - Two.flac"])
        self.store.update(p.id, name="Later", tracks=["A/B/02 - Two.flac", "A/B/01 - One.flac"])
        again = self.store.get(p.id)
        self.assertEqual((again.name, again.tracks), ("Later", ["A/B/02 - Two.flac", "A/B/01 - One.flac"]))
        self.store.delete(p.id)
        self.assertEqual(self.store.all(), [])
        with self.assertRaises(LookupError):
            self.store.delete(p.id)

    def test_songs_stay_inside_the_music_folder_and_names_are_needed(self):
        for bad in ("/etc/passwd", "../x.flac", "A/../../x.flac"):
            with self.assertRaises(playlists.PlaylistError):
                self.store.create("x", [bad])
        with self.assertRaises(playlists.PlaylistError):
            self.store.create("   ")
        self.assertEqual(self.store.create("w", ["A\\B\\01.flac"]).tracks, ["A/B/01.flac"], "Windows separators")

    def test_an_uploaded_picture_replaces_the_last_and_goes_with_its_playlist(self):
        from tests.test_health import jpeg
        p = self.store.create("x")
        first = self.store.image_path(self.store.set_image(p.id, jpeg(300)))
        second = self.store.image_path(self.store.set_image(p.id, jpeg(400)))
        self.assertFalse(first.exists(), "the old picture is not left behind")
        self.assertTrue(second.is_file())
        with self.assertRaises(playlists.PlaylistError):
            self.store.set_image(p.id, b"not a picture")
        self.store.update(p.id, cover={})                   # back to the album tiles: the picture goes
        self.assertFalse(second.exists())
        with self.assertRaises(playlists.PlaylistError):
            self.store.update(p.id, cover={"image": "../../x"})
        self.store.delete(p.id)

    def test_an_unreadable_file_means_no_playlists_not_a_crash(self):
        Path(self.store.path).write_text("{not json")
        self.assertEqual(self.store.all(), [])


class Api(Base):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        from iaflac.gui.server import create_app
        from iaflac.gui.service import App
        from iaflac.gui.settings import Settings
        from tests.test_health import jpeg
        base = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"IAFLAC_CONFIG": str(base / "config.json"), "IAFLAC_CACHE": str(base / "cache"),
                                                "IAFLAC_DATA": str(base / "data")})
        self.env.start()
        self.album("Test Artist/Test Album", [(1, "One"), (2, "Two")], cover=jpeg())
        self.core = App(settings=Settings(library=str(self.root)), mb_client=FakeMB([]))
        self.http = TestClient(create_app(self.core, token="t"))
        self.http.get("/?token=t")

    def tearDown(self):
        self.http.close()
        self.core.shutdown()
        self.env.stop()
        super().tearDown()

    def test_a_playlist_over_http(self):
        one, two = "Test Artist/Test Album/01 - One.flac", "Test Artist/Test Album/02 - Two.flac"
        p = self.http.post("/api/playlists", json={"name": "Mix", "tracks": [two]}).json()
        self.assertEqual(self.http.post(f"/api/playlists/{p['id']}/add", json={"tracks": [one, "Gone/01.flac"]}).json()["count"], 3)
        got = self.http.get(f"/api/playlists/{p['id']}").json()
        self.assertEqual([t["path"] for t in got["tracks"]], [two, one, "Gone/01.flac"])
        self.assertEqual((got["tracks"][0]["title"], got["tracks"][0]["cover"]), ("Two", "Test Artist/Test Album/cover.jpg"))
        self.assertTrue(got["tracks"][2]["missing"], "a song that is gone stays, marked missing")
        self.assertEqual(self.http.put(f"/api/playlists/{p['id']}", json={"name": "Mix 2"}).json()["name"], "Mix 2")
        listed = self.http.get("/api/playlists").json()["playlists"]
        self.assertEqual([(x["name"], x["count"], x["cover"]) for x in listed],
                         [("Mix 2", 3, {"kind": "tiles", "tiles": ["Test Artist/Test Album/cover.jpg"]})])
        self.assertEqual(self.http.post("/api/playlists", json={"name": "x", "tracks": ["../out.flac"]}).status_code, 400)
        self.assertEqual(self.http.delete(f"/api/playlists/{p['id']}").status_code, 200)
        self.assertEqual(self.http.get(f"/api/playlists/{p['id']}").status_code, 404)
        self.assertTrue((self.root / one).is_file(), "deleting a playlist never touches the songs")

    def test_its_cover_tiles_one_album_or_a_picture(self):
        import base64
        from tests.test_health import jpeg
        for n in range(2, 6):
            self.album(f"Artist {n}/Album {n}", [(1, "One")], cover=jpeg())
        self.album("No Cover/Album", [(1, "One")])
        songs = ["No Cover/Album/01 - One.flac"] + [f"Artist {n}/Album {n}/01 - One.flac" for n in range(2, 6)]
        p = self.http.post("/api/playlists", json={"name": "Five", "tracks": songs}).json()
        self.assertEqual(p["cover"]["tiles"], [f"Artist {n}/Album {n}/cover.jpg" for n in range(2, 6)],
                         "the first four albums that have a cover, in song order")
        got = self.http.get(f"/api/playlists/{p['id']}").json()
        self.assertEqual(len(got["albums"]), 4)
        r = self.http.put(f"/api/playlists/{p['id']}", json={"cover": {"album": "Artist 3/Album 3"}}).json()
        self.assertEqual(r["cover"], {"kind": "album", "album": "Artist 3/Album 3", "cover": "Artist 3/Album 3/cover.jpg"})
        self.assertEqual(self.http.get(f"/api/playlists/{p['id']}/image").status_code, 404)
        data = "data:image/jpeg;base64," + base64.b64encode(jpeg(320)).decode()
        r = self.http.post(f"/api/playlists/{p['id']}/image", json={"data": data}).json()
        self.assertEqual(r["cover"]["kind"], "image")
        img = self.http.get(f"/api/playlists/{p['id']}/image")
        self.assertEqual((img.status_code, img.headers["content-type"]), (200, "image/jpeg"))
        self.assertEqual(self.http.post(f"/api/playlists/{p['id']}/image", json={"data": "bm90IGFuIGltYWdl"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
