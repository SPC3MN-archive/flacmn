import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from iaflac import tagging
from iaflac.tagging import AlbumMeta, TrackMeta
from tests.helpers import HAVE_FFMPEG, HAVE_FLAC, encode_flac, tone_wav
from tests.test_cdart import FakeMB, jpeg
from tests.test_pipeline import FakeIA

NEED = unittest.skipUnless(HAVE_FLAC and HAVE_FFMPEG, "flac and ffmpeg required")
TOKEN = "t"


@NEED
class ArtApi(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from iaflac.gui.server import create_app
        from iaflac.gui.service import App
        from iaflac.gui.settings import Settings

        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.library = base / "Music"
        self.folder = self.library / "Test Artist" / "Test Album (1999)"
        self.folder.mkdir(parents=True)
        flac = encode_flac(tone_wav(44100))
        for n in ("01.flac", "02.flac"):
            (self.folder / n).write_bytes(flac)
        tagging.apply(self.folder, AlbumMeta(albumartist="Test Artist", album="Test Album", date="1999", mb_release_id="rel-1",
                                             mb_releasegroup_id="rg-1",
                                             tracks=[TrackMeta("01.flac", "One", 1), TrackMeta("02.flac", "Two", 2)]),
                      {n: self.folder / n for n in ("01.flac", "02.flac")}, jpeg((800, 800)))
        self.env = mock.patch.dict(os.environ, {"IAFLAC_CONFIG": str(base / "config.json"), "IAFLAC_CACHE": str(base / "cache"),
                                                "IAFLAC_DATA": str(base / "data")})
        self.env.start()
        self.mb = FakeMB()
        settings = Settings(library=str(self.library), print_folder=str(base / "prints"), art_search_archive=False)
        self.core = App(settings=settings, client=FakeIA([]), mb_client=self.mb, queue_store=base / "queue.json")
        self.http = TestClient(create_app(self.core, token=TOKEN))
        self.http.get(f"/?token={TOKEN}")

    def tearDown(self):
        self.http.close()
        self.core.shutdown()
        self.env.stop()
        self.tmp.cleanup()

    def wait(self, job_id):
        for _ in range(600):
            j = self.http.get(f"/api/jobs/{job_id}").json()
            if j["state"] in ("done", "failed", "cancelled"):
                self.assertEqual(j["state"], "done", j.get("error"))
                return j["result"]
            time.sleep(0.05)
        self.fail("job did not finish")

    def test_find_choose_print_open_and_save(self):
        folder = "Test Artist/Test Album (1999)"
        art = self.wait(self.http.post("/api/art/find", json={"folder": folder}).json()["job_id"])
        self.assertEqual(art["completeness"]["booklet"], 2)
        self.assertEqual(art["completeness"]["back"], 1)
        sel = art["selection"]
        front_ids = [i["id"] for i in art["images"] if i["kind"] == "front"]
        by_id = {i["id"]: i for i in art["images"]}
        self.assertEqual(by_id[sel["front"]]["release_id"], "rel-2", "the front matches the booklet's pressing")
        self.assertEqual(by_id[sel["front"]]["dpi"], 253)
        # choose the album's own cover.jpg instead and leave the inside blank (a generated tracklist)
        own = next(i for i in front_ids if by_id[i]["source"] == "folder")
        state = self.http.put("/api/art/selection", json={"folder": folder, "selection": {**sel, "front": own, "inside": None}}).json()
        self.assertEqual(state["selection"]["front"], own)
        caa_front = sel["front"]
        bad = self.http.put("/api/art/selection", json={"folder": folder, "selection": {"front": "nope"}})
        self.assertEqual(bad.status_code, 400)
        img = self.http.get(f"/api/art/image/{caa_front}")
        self.assertEqual((img.status_code, img.headers["content-type"]), (200, "image/jpeg"))
        printed = self.wait(self.http.post("/api/art/print", json={"folder": folder}).json()["job_id"])
        self.assertTrue(printed["pdf"].endswith("Test Artist - Test Album (1999) - CD case.pdf"))
        self.assertEqual([p["name"] for p in printed["pieces"]],
                         ["front cover", "back + spines", "inside of front insert", "under-tray image", "disc label"])
        self.assertTrue(printed["pieces"][2]["source"].startswith("generated"))
        pdf = self.http.get("/api/art/pdf", params={"folder": folder})
        self.assertEqual((pdf.status_code, pdf.content[:5]), (200, b"%PDF-"))
        with mock.patch("iaflac.gui.service.open_with_system") as opened:
            self.http.post("/api/art/open", json={"folder": folder})
        opened.assert_called_once()
        saved = self.wait(self.http.post("/api/art/save", json={"folder": folder}).json()["job_id"])
        self.assertEqual(sorted(Path(p).name for p in saved["written"]), ["back.jpg", "disc.jpg", "front.jpg"])

    def test_gaps_are_filled_in_the_albums_colours_and_can_be_changed(self):
        folder = "Test Artist/Test Album (1999)"
        art = self.wait(self.http.post("/api/art/find", json={"folder": folder}).json()["job_id"])
        self.assertTrue(art["palette"], "colours taken from the chosen front cover")
        main = art["palette"][0]
        self.assertEqual(art["completeness"]["tray"], 0)
        self.assertEqual(art["selection"]["tray"], f"colour:{main}", "no under-tray scan: the album's main colour")
        sel = {**art["selection"], "inside": "colour:#000000", "disc": next(i["id"] for i in art["images"] if i["kind"] == "front")}
        state = self.http.put("/api/art/selection", json={"folder": folder, "selection": sel}).json()
        self.assertEqual(state["selection"]["inside"], "colour:#000000")
        self.assertEqual(self.http.put("/api/art/selection", json={"folder": folder, "selection": {"inside": "colour:red"}}).status_code, 400)
        printed = self.wait(self.http.post("/api/art/print", json={"folder": folder}).json()["job_id"])
        src = {p["name"]: p["source"] for p in printed["pieces"]}
        self.assertEqual(src["inside of front insert"], "generated: tracklist on the album colour #000000")
        self.assertEqual(src["under-tray image"], f"album colour {main}")
        self.assertFalse(src["disc label"].startswith("generated"), "a front cover image fills the disc")

    def test_whole_library_checklist(self):
        summary = self.wait(self.http.post("/api/art/scan-all").json()["job_id"])
        row = summary["albums"][0]
        self.assertEqual((row["folder"], row["complete"], row["missing"]), ("Test Artist/Test Album (1999)", False, ["under-tray"]))
        self.assertEqual(self.http.get("/api/art/summary").json()["albums"][0]["folder"], row["folder"])

    def test_folders_outside_the_library_are_refused(self):
        self.assertEqual(self.http.post("/api/art/find", json={"folder": "../.."}).status_code, 404)
        self.assertEqual(self.http.get("/api/art", params={"folder": "Test Artist/Test Album (1999)"}).status_code, 404,
                         "nothing found yet")

    def test_print_settings_are_validated(self):
        r = self.http.put("/api/settings", json={"paper": "a4", "disc_hole_mm": 23, "bleed_mm": 9}).json()
        self.assertEqual((r["settings"]["paper"], r["settings"]["disc_hole_mm"]), ("a4", 23.0))
        self.assertEqual(r["rejected"], ["bleed_mm"])


if __name__ == "__main__":
    unittest.main()
