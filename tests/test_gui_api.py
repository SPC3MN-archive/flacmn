import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import HAVE_FFMPEG, HAVE_FLAC
from tests.test_musicbrainz import KOB, release
from tests.test_pipeline import FakeIA, make_alt_item, make_item

NEED = unittest.skipUnless(HAVE_FLAC and HAVE_FFMPEG, "flac and ffmpeg required")
TOKEN = "test-token"


class FakeMB:
    """MusicBrainz stand-in: one release whose track count matches the fake album (3 tracks)."""

    def __init__(self):
        self.rel = release("rel-1", title="Test Album", artist="Test Artist", tracks=KOB[:3], date="2001-02-03")

    def search_releases(self, artist, album, limit=15):
        return [{"id": "rel-1", "score": 100, "track-count": 3}]

    def release(self, rid):
        return self.rel

    def cover(self, rid, group=""):
        return None


@NEED
class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = [make_item("flac-1", "Test Artist - Test Album", "Test Artist", tracks=3, rip=True, seed=1),
                     make_alt_item("vbr-1", "Test Artist - Test Album", "Test Artist", "mp3", tracks=3, seed=2)]

    def setUp(self):
        from fastapi.testclient import TestClient
        from iaflac.gui.server import create_app
        from iaflac.gui.service import App
        from iaflac.gui.settings import Settings

        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.library = base / "Music"
        self.library.mkdir()
        self.env = mock.patch.dict(os.environ, {"IAFLAC_CONFIG": str(base / "config.json"), "IAFLAC_CACHE": str(base / "cache"),
                                                "IAFLAC_DATA": str(base / "data")})
        self.env.start()
        self.core = App(settings=Settings(library=str(self.library), auto_test_top=2), client=FakeIA(self.items),
                        mb_client=FakeMB(), queue_store=base / "queue.json")
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

    def wait_idle(self, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if all(j.state in ("done", "failed", "cancelled") for j in self.core.jobs.jobs.values()):
                return
            time.sleep(0.05)
        self.fail("jobs still running")

    def search(self):
        r = self.http.post("/api/search", json={"query": "Test Artist - Test Album"}).json()
        self.wait(r["job_id"])
        self.wait_idle()
        return self.http.get(f"/api/search/{r['search_id']}").json()

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

    # ---- search, test, detail --------------------------------------------------------------------------------
    def test_search_lists_every_format_ranked_and_quick_tests_the_top(self):
        res = self.search()
        ids = [r["identifier"] for r in res["results"]]
        self.assertEqual(ids, ["flac-1", "vbr-1"])
        top = res["results"][0]
        self.assertEqual((top["quality"]["label"], top["quality"]["format"], top["tested"]),
                         ("Lossless", "FLAC 16/44.1", True))
        self.assertTrue(res["results"][1]["quality"]["format"].startswith("MP3"))

    def test_item_detail_and_only_the_quick_test(self):
        self.search()
        d = self.http.get("/api/items/flac-1").json()
        self.assertEqual(len(d["track_list"]), 3)
        self.assertEqual(d["track_list"][0]["probe"]["sample_rate"], 44100)
        self.assertTrue(d["provenance"]["reasons"])
        done = self.wait(self.http.post("/api/items/flac-1/test", json={"level": "quick"}).json()["job_id"])
        self.assertEqual(done["tested"], ["flac-1"])
        r = self.http.post("/api/items/flac-1/test", json={"level": "deep"})
        self.assertEqual(r.status_code, 400, "the app has no deep test; a track's spectrogram is drawn on request")
        self.assertEqual(self.http.get("/api/items/flac-1/spectrogram.png").status_code, 404)

    def test_unknown_item_is_404(self):
        with mock.patch.object(self.core.client, "metadata", side_effect=__import__("iaflac").ia_client.IAError("gone")):
            self.assertEqual(self.http.get("/api/items/nope").status_code, 404)

    # ---- metadata, MusicBrainz, saving ---------------------------------------------------------------------
    def test_meta_musicbrainz_plan_and_save_through_the_queue(self):
        self.search()
        meta = self.http.get("/api/items/flac-1/meta").json()
        self.assertEqual((meta["meta"]["albumartist"], meta["meta"]["album"]), ("Test Artist", "Test Album"))
        self.assertTrue(meta["folder"].endswith(str(Path("Test Artist") / "Test Album (1999)")))
        job = self.http.post("/api/items/flac-1/musicbrainz", json={}).json()
        matches = self.wait(job["job_id"])["matches"]
        self.assertEqual(matches[0]["release_id"], "rel-1")
        applied = self.http.post("/api/items/flac-1/musicbrainz/rel-1").json()
        self.assertEqual([t["title"] for t in applied["meta"]["tracks"]], [t for t, _ in KOB[:3]])
        self.assertTrue(any(c["field"] == "date" for c in applied["diff"]))
        edited = applied["meta"]
        edited["genre"] = "Modal Jazz"
        self.assertEqual(self.http.put("/api/items/flac-1/meta", json=edited).json()["meta"]["sources"]["genre"], "you")
        plan = self.http.get("/api/items/flac-1/plan").json()
        self.assertEqual([t["name"] for t in plan["tracks"]],
                         ["01 - So What.flac", "02 - Freddie Freeloader.flac", "03 - Blue in Green.flac"])
        item = self.http.post("/api/queue", json={"identifier": "flac-1"}).json()
        self.assertEqual(self.http.post("/api/queue", json={"identifier": "flac-1"}).status_code, 400, "no double queueing")
        deadline = time.time() + 60
        while self.core.queue.items[item["id"]].state not in ("done", "failed") and time.time() < deadline:
            time.sleep(0.05)
        done = self.http.get("/api/queue").json()[0]
        self.assertEqual(done["state"], "done", done["error"])
        folder = self.library / "Test Artist" / "Test Album (2001)"
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ["01 - So What.flac", "02 - Freddie Freeloader.flac", "03 - Blue in Green.flac"])
        from iaflac import tagging
        t = tagging.read_track(folder / "01 - So What.flac")
        self.assertEqual((t["genre"], t["date"], t["mb_release_id"]), ("Modal Jazz", "2001-02-03", "rel-1"))
        self.wait_idle()
        lib = self.http.get("/api/library").json()
        self.assertEqual([a["folder"] for a in lib["albums"]], ["Test Artist/Test Album (2001)"])
        from tests.helpers import add_appledouble
        clean = self.http.get("/api/library/album", params={"folder": "Test Artist/Test Album (2001)"}).json()
        add_appledouble(folder)                               # as if the library were on a FAT32 drive
        album = self.http.get("/api/library/album", params={"folder": "Test Artist/Test Album (2001)"}).json()
        self.assertEqual(len(album["tracks"]), 3)
        self.assertEqual(album["issues"], clean["issues"], "the ._ files change nothing")
        res = self.search()
        self.assertEqual(res["results"][0]["in_library"]["folder"], "Test Artist/Test Album (2001)")

    def test_saving_needs_a_library(self):
        self.core.settings.library = ""
        r = self.http.post("/api/queue", json={"identifier": "flac-1"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("library", r.json()["error"])

    # ---- audio -----------------------------------------------------------------------------------------------
    def test_peaks_spectrogram_analysis_and_playback_of_a_remote_track(self):
        self.search()
        q = {"identifier": "flac-1", "file": "01 Track 1.flac"}
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
        self.assertTrue(all(0 <= v <= 1 for row in wf["levels"] for v in row))
        self.assertTrue(max(max(row) for row in wf["levels"]) > 0.3)
        full = self.http.get("/api/audio", params=q)
        self.assertEqual((full.status_code, full.headers["content-type"]), (200, "audio/flac"))
        part = self.http.get("/api/audio", params=q, headers={"range": "bytes=0-99"})
        self.assertEqual((part.status_code, len(part.content)), (206, 100))
        self.assertEqual(full.content, self.items[0].files["01 Track 1.flac"])

    def test_library_paths_cannot_escape(self):
        self.assertEqual(self.http.get("/api/library/file", params={"path": "../config.json"}).status_code, 404)

    # ---- settings and events ---------------------------------------------------------------------------------
    def test_settings_round_trip_and_reject_bad_values(self):
        r = self.http.put("/api/settings", json={"auto_test_top": 6, "art": "poster", "nope": 1}).json()
        self.assertEqual(r["settings"]["auto_test_top"], 6)
        self.assertEqual(sorted(r["rejected"]), ["art", "nope"])
        self.assertEqual(self.http.get("/api/settings").json()["auto_test_top"], 6)
        r = self.http.put("/api/settings", json={"idle_scope": 45}).json()        # Now playing's idle timer
        self.assertEqual(r["settings"]["idle_scope"], 45)
        self.assertEqual(self.http.put("/api/settings", json={"idle_scope": "soon"}).json()["rejected"], ["idle_scope"])

    def test_events_arrive_over_the_websocket(self):
        with self.http.websocket_connect("/api/events") as ws:
            self.assertEqual(ws.receive_json()["type"], "hello")
            self.http.post("/api/search", json={"query": "Test Artist - Test Album"})
            kinds = set()
            for _ in range(200):
                e = ws.receive_json()
                kinds.add(e["type"])
                if e["type"] == "results":
                    break
            self.assertIn("job", kinds)
            self.assertIn("results", kinds)
        self.wait_idle()


if __name__ == "__main__":
    unittest.main()


@NEED
class InLibrary(unittest.TestCase):
    def test_a_year_in_the_upload_title_still_matches_the_library_album(self):
        from iaflac.gui import serialize
        from iaflac.library_index import AlbumEntry, LibraryIndex
        from iaflac.pipeline import Pipeline
        item = make_item("kob-1959", "Test Artist - Test Album (2001)", "Test Artist", tracks=2, seed=3)
        cand = Pipeline(FakeIA([item])).load("kob-1959")
        lib = LibraryIndex(root="/music", albums=[AlbumEntry(folder="Test Artist/Test Album (2001)", artist="Test Artist",
                                                             album="Test Album", year="2001")])
        row = serialize.candidate_summary(cand, None, lib)
        self.assertEqual(row["in_library"]["folder"], "Test Artist/Test Album (2001)")

    def test_a_result_carries_its_cover_link_when_it_has_one(self):
        from iaflac.gui import serialize
        from iaflac.pipeline import Pipeline
        item = make_item("kob-1959", "Test Artist - Test Album", "Test Artist", tracks=2, seed=3)
        row = serialize.candidate_summary(Pipeline(FakeIA([item])).load("kob-1959"), None, None)
        self.assertIn("cover_url", row)
        if row["cover"]:
            self.assertTrue(row["cover_url"].startswith("https://archive.org/download/kob-1959/"))
        else:
            self.assertIsNone(row["cover_url"])
