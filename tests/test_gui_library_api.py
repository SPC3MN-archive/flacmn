"""The library over HTTP, as in flacmn: the music folder, covers (a file or the picture in the tracks), playback and settings."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from iaflac import tagging
from iaflac.tagging import AlbumMeta, TrackMeta
from tests.helpers import HAVE_FFMPEG, HAVE_FLAC, encode_flac, tone_wav

NEED = unittest.skipUnless(HAVE_FLAC and HAVE_FFMPEG, "flac and ffmpeg required")
TOKEN = "test-token"


@NEED
class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flac = encode_flac(tone_wav(44100 * 6))

    def setUp(self):
        from fastapi.testclient import TestClient
        from iaflac.gui.server import create_app
        from iaflac.gui.service import App
        from iaflac.gui.settings import Settings

        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.library = base / "Music"
        self.album = self.library / "Test Artist" / "Test Album (2001)"
        self.album.mkdir(parents=True)
        paths = {}
        for i in (1, 2, 3):
            p = self.album / f"{i:02d}.flac"
            p.write_bytes(self.flac)
            paths[p.name] = p
        tagging.apply(self.album, AlbumMeta(albumartist="Test Artist", album="Test Album", date="2001",
                                            tracks=[TrackMeta(f"{i:02d}.flac", f"Track {i}", i) for i in (1, 2, 3)]), paths)
        self.env = mock.patch.dict(os.environ, {"IAFLAC_CONFIG": str(base / "config.json"), "IAFLAC_CACHE": str(base / "cache"),
                                                "IAFLAC_DATA": str(base / "data")})
        self.env.start()
        self.core = App(settings=Settings(library=str(self.library)), mb_client=object(), queue_store=base / "queue.json")
        self.http = TestClient(create_app(self.core, token=TOKEN))
        self.http.get(f"/?token={TOKEN}")             # sets the cookie, like the window does

    def tearDown(self):
        self.http.close()
        self.core.shutdown()
        self.env.stop()
        self.tmp.cleanup()

    def wait(self, job_id, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            j = self.http.get(f"/api/jobs/{job_id}").json()
            if j["state"] in ("done", "failed", "cancelled"):
                self.assertEqual(j["state"], "done", j.get("error"))
                return j["result"]
            time.sleep(0.05)
        self.fail(f"job {job_id} did not finish")

    def test_a_cover_embedded_in_the_tracks_is_the_albums_cover_when_there_is_no_cover_file(self):
        import io
        from mutagen.flac import FLAC, Picture
        from PIL import Image

        def picture(colour):
            out = io.BytesIO()
            Image.new("RGB", (300, 300), colour).save(out, "JPEG")
            return out.getvalue()

        art = picture((40, 90, 200))
        for f in sorted(self.album.glob("*.flac")):
            audio, pic = FLAC(f), Picture()
            pic.type, pic.mime, pic.data = 3, "image/jpeg", art
            audio.add_picture(pic)
            audio.save()
        folder = "Test Artist/Test Album (2001)"
        album = self.http.get("/api/library/album", params={"folder": folder}).json()
        first = sorted(self.album.glob("*.flac"))[0].name
        self.assertEqual(album["cover"], f"{folder}/{first}")           # the first track carrying a picture
        for params in ({"path": album["cover"]}, {"folder": folder}):
            r = self.http.get("/api/library/cover", params=params)
            self.assertEqual((r.status_code, r.headers["content-type"], r.content), (200, "image/jpeg", art), params)

        own = picture((200, 40, 40))                                     # a cover file wins over the embedded one
        (self.album / "cover.jpg").write_bytes(own)
        album = self.http.get("/api/library/album", params={"folder": folder}).json()
        self.assertEqual(album["cover"], f"{folder}/cover.jpg")
        self.assertEqual(self.http.get("/api/library/cover", params={"folder": folder}).content, own)

    def test_an_album_with_no_cover_at_all_has_none(self):
        folder = "Test Artist/Test Album (2001)"
        self.assertIsNone(self.http.get("/api/library/album", params={"folder": folder}).json()["cover"])
        self.assertEqual(self.http.get("/api/library/cover", params={"folder": folder}).status_code, 404)
        first = sorted(self.album.glob("*.flac"))[0].name
        self.assertEqual(self.http.get("/api/library/cover", params={"path": f"{folder}/{first}"}).status_code, 404)
        self.assertEqual(self.http.get("/api/library/cover", params={"path": "../../etc/hosts"}).status_code, 404)

    def first_track(self):
        album = self.http.get("/api/library/album", params={"folder": "Test Artist/Test Album (2001)"}).json()
        return album, album["tracks"][0]["path"]

    # ---- guard -----------------------------------------------------------------------------------------------
    def test_the_api_needs_the_token(self):
        from fastapi.testclient import TestClient
        from iaflac.gui.server import create_app
        bare = TestClient(create_app(self.core, token=TOKEN))
        self.assertEqual(bare.get("/api/health").status_code, 401)
        self.assertEqual(bare.get("/api/health", headers={"x-iaflac-token": "wrong"}).status_code, 401)
        self.assertEqual(bare.get("/api/health", headers={"x-iaflac-token": TOKEN}).status_code, 200)

    def test_other_hosts_are_refused(self):
        self.assertEqual(self.http.get("/api/health", headers={"host": "evil.example:80"}).status_code, 403)

    # ---- library ---------------------------------------------------------------------------------------------
    def test_scan_lists_the_album_and_its_tracks(self):
        self.wait(self.http.post("/api/library/scan").json()["job_id"])
        lib = self.http.get("/api/library").json()
        self.assertEqual([(a["artist"], a["album"], a["year"], a["tracks"]) for a in lib["albums"]],
                         [("Test Artist", "Test Album", "2001", 3)])
        album, _ = self.first_track()
        self.assertEqual([t["title"] for t in album["tracks"]], ["Track 1", "Track 2", "Track 3"])
        self.assertIn("no cover art (embedded or cover.jpg)", album["issues"])

    def test_library_needs_a_folder(self):
        self.core.settings.library = ""
        r = self.http.post("/api/library/scan")
        self.assertEqual(r.status_code, 400)
        self.assertIn("library", r.json()["error"])

    def test_library_paths_cannot_escape(self):
        self.assertEqual(self.http.get("/api/library/file", params={"path": "../config.json"}).status_code, 404)
        self.assertEqual(self.http.get("/api/audio", params={"path": "../config.json"}).status_code, 404)

    # ---- audio -----------------------------------------------------------------------------------------------
    def test_peaks_spectrogram_analysis_and_playback_of_a_library_track(self):
        _, path = self.first_track()
        q = {"path": path}
        peaks = self.http.get("/api/track/peaks", params={**q, "n": 200}).json()
        self.assertEqual((peaks["n"], len(peaks["peaks"])), (200, 400))
        self.assertTrue(max(peaks["peaks"]) > 0.1)
        png = self.http.get("/api/track/spectrogram.png", params={**q, "w": 400, "h": 200})
        self.assertEqual((png.status_code, png.content[:4]), (200, b"\x89PNG"))
        analysis = self.http.get("/api/track/analysis", params=q).json()
        self.assertEqual(analysis["sample_rate"], 44100)
        self.assertIn("verdict", analysis)
        wf = self.http.get("/api/track/spectrum3d", params={**q, "slices": 12, "bins": 48}).json()
        self.assertEqual((wf["slices"], wf["bins"], len(wf["levels"]), len(wf["levels"][0])), (12, 48, 12, 48))
        full = self.http.get("/api/audio", params=q)
        self.assertEqual((full.status_code, full.headers["content-type"]), (200, "audio/flac"))
        part = self.http.get("/api/audio", params=q, headers={"range": "bytes=0-99"})
        self.assertEqual((part.status_code, len(part.content)), (206, 100))
        self.assertEqual(full.content, (self.library / path).read_bytes())
        wav = self.http.get("/api/audio", params={**q, "wav": True})
        self.assertEqual((wav.status_code, wav.content[:4]), (200, b"RIFF"))

    # ---- settings and events ---------------------------------------------------------------------------------
    def test_settings_round_trip_and_reject_bad_values(self):
        other = Path(self.tmp.name) / "Other"
        r = self.http.put("/api/settings", json={"library": str(other), "nope": 1}).json()
        self.assertEqual(r["settings"]["library"], str(other))
        self.assertEqual(r["rejected"], ["nope"])
        self.assertEqual(self.http.get("/api/settings").json()["library"], str(other))
        r = self.http.put("/api/settings", json={"idle_scope": 45}).json()        # Now playing's idle timer
        self.assertEqual(r["settings"]["idle_scope"], 45)
        self.assertEqual(self.http.put("/api/settings", json={"idle_scope": "soon"}).json()["rejected"], ["idle_scope"])

    def test_events_arrive_over_the_websocket(self):
        with self.http.websocket_connect("/api/events") as ws:
            self.assertEqual(ws.receive_json()["type"], "hello")
            self.http.post("/api/library/scan")
            kinds = set()
            for _ in range(200):
                e = ws.receive_json()
                kinds.add(e["type"])
                if e["type"] == "library":
                    break
            self.assertIn("job", kinds)
            self.assertIn("library", kinds)


if __name__ == "__main__":
    unittest.main()
