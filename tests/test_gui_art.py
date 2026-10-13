import io
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

    def test_the_album_list_never_waits_for_the_music_folder(self):
        first = self.http.get("/api/library").json()
        if first["scanning"]:                                   # the first call starts the read and answers at once
            self.wait(first["scanning"])
        lib = self.http.get("/api/library").json()
        self.assertEqual((lib["scanning"], lib["scanned"]), (None, True))
        self.assertEqual([a["folder"] for a in lib["albums"]], ["Test Artist/Test Album (1999)"])

    def test_an_album_folder_outside_the_music_folder_can_be_opened_and_printed(self):
        import shutil
        outside = Path(self.tmp.name) / "Elsewhere" / "Test Album"
        shutil.copytree(self.folder, outside)
        bad = self.http.post("/api/library/open", json={"path": str(Path(self.tmp.name) / "nope")})
        self.assertEqual(bad.status_code, 400)
        empty = Path(self.tmp.name) / "Empty"
        empty.mkdir()
        self.assertIn("no audio", self.http.post("/api/library/open", json={"path": str(empty)}).json()["error"])
        entry = self.http.post("/api/library/open", json={"path": str(outside)}).json()
        folder = entry["folder"]
        self.assertEqual((entry["artist"], entry["album"], entry["opened"]), ("Test Artist", "Test Album", True))
        self.assertEqual(self.http.get("/api/library").json()["albums"][0]["folder"], folder, "opened albums come first")
        cover = self.http.get("/api/library/file", params={"path": f"{folder}/cover.jpg"})
        self.assertEqual(cover.status_code, 200)
        self.assertEqual(self.http.get("/api/library/file", params={"path": str(Path(self.tmp.name) / "config.json")}).status_code, 404)
        art = self.wait(self.http.post("/api/art/find", json={"folder": folder}).json()["job_id"])
        self.assertEqual(art["artist"], "Test Artist")
        printed = self.wait(self.http.post("/api/art/print", json={"folder": folder}).json()["job_id"])
        self.assertTrue(Path(printed["pdf"]).exists())
        from iaflac.gui.settings import Settings
        self.assertEqual(Settings.load().opened, [folder], "remembered for the next launch")
        self.assertIn("opened", self.http.put("/api/settings", json={"opened": []}).json()["rejected"])

    def test_the_cassette_j_card_prints_and_previews(self):
        folder = "Test Artist/Test Album (1999)"
        self.wait(self.http.post("/api/art/find", json={"folder": folder}).json()["job_id"])
        bad = self.http.post("/api/art/print", json={"folder": folder, "format": "lp"})
        self.assertEqual(bad.status_code, 400)
        printed = self.wait(self.http.post("/api/art/print", json={"folder": folder, "format": "cassette"}).json()["job_id"])
        self.assertTrue(printed["pdf"].endswith("Test Artist - Test Album (1999) - cassette J-card.pdf"))
        self.assertEqual((printed["format"], printed["pages"]), ("cassette", 2))
        state = self.http.get("/api/art", params={"folder": folder}).json()
        self.assertEqual((state["pdfs"]["cassette"], state["pdfs"]["cd"]), (printed["pdf"], None))
        pdf = self.http.get("/api/art/pdf", params={"folder": folder, "format": "cassette"})
        self.assertEqual((pdf.status_code, pdf.content[:5]), (200, b"%PDF-"))
        self.assertEqual(self.http.get("/api/art/pdf", params={"folder": folder}).status_code, 404, "no CD sheet made yet")
        done = self.wait(self.http.post("/api/art/preview3d", json={"folder": folder, "format": "cassette"}).json()["job_id"])
        self.assertEqual(done["parts"], ["flap", "front", "inside", "outside", "spine"])
        tex = self.http.get("/api/art/texture", params={"folder": folder, "part": "outside", "format": "cassette"})
        self.assertEqual((tex.status_code, tex.headers["content-type"]), (200, "image/png"))
        self.assertEqual(self.http.get("/api/art/texture", params={"folder": folder, "part": "outside"}).status_code, 404,
                         "the CD's preview is separate")

    def test_an_image_of_your_own_can_be_uploaded_for_any_part(self):
        import base64
        folder = "Test Artist/Test Album (1999)"
        before = sorted(p.name for p in self.folder.rglob("*"))
        self.wait(self.http.post("/api/art/find", json={"folder": folder}).json()["job_id"])
        png = io.BytesIO()
        from PIL import Image
        Image.new("RGB", (1772, 1394), (10, 200, 90)).save(png, "PNG")             # 300 dpi for the 151 x 118 tray card
        body = {"folder": folder, "part": "back", "name": "my back.png", "data": base64.b64encode(png.getvalue()).decode()}
        state = self.http.post("/api/art/upload", json=body).json()
        chosen = next(i for i in state["images"] if i["id"] == state["selection"]["back"])
        self.assertEqual((chosen["source"], chosen["kind"], chosen["label"]), ("upload", "back", "your upload: my back.png"))
        self.assertEqual((chosen["width"], chosen["height"], chosen["dpi"]), (1772, 1394, 298))
        img = self.http.get(f"/api/art/image/{chosen['id']}")
        self.assertEqual((img.status_code, img.headers["content-type"]), (200, "image/png"))
        printed = self.wait(self.http.post("/api/art/print", json={"folder": folder}).json()["job_id"])
        self.assertEqual(printed["pieces"][1]["source"], "your upload: my back.png")
        again = self.wait(self.http.post("/api/art/find", json={"folder": folder}).json()["job_id"])
        self.assertIn(chosen["id"], [i["id"] for i in again["images"]], "kept when the art is found again")
        for bad in ({**body, "data": base64.b64encode(b"not an image").decode()}, {**body, "data": "%%%"},
                    {**body, "part": "sleeve"}):
            self.assertEqual(self.http.post("/api/art/upload", json=bad).status_code, 400, bad.get("part"))
        self.assertEqual(sorted(p.name for p in self.folder.rglob("*")), before, "nothing written to the album folder")

    def test_generated_parts_have_designs(self):
        folder = "Test Artist/Test Album (1999)"
        self.wait(self.http.post("/api/art/find", json={"folder": folder}).json()["job_id"])
        put = lambda part, **kw: self.http.put("/api/art/style", json={"folder": folder, "part": part, **kw})
        state = put("back", layout="blank", logo="large", ink="charcoal").json()
        self.assertEqual(state["styles"], {"back": "blank", "back:logo": "large", "back:ink": "charcoal"})
        self.assertEqual(put("back", layout="list").json()["styles"]["back:logo"], "medium", "large does not fit a tracklist")
        self.assertEqual(put("back", logo="large").status_code, 400)
        put("inside", layout="centred", logo="small")
        self.assertEqual(put("inside", ink="green").status_code, 400, "the mark is charcoal or off-white only")
        put("tray", logo="small")
        put("disc", logo="medium")
        put("front", layout="title", logo="large")
        self.assertEqual(put("tray", layout="centred").status_code, 400, "the under-tray image has no tracklist")
        self.assertEqual(put("back", layout="poster").status_code, 400)
        self.assertEqual(put("back", ink="gold").status_code, 400)
        self.assertEqual(put("sleeve", logo="small").status_code, 400)
        sel = {**state["selection"], "front": None, "back": "colour:#203060", "inside": None, "tray": "colour:#203060",
               "disc": "colour:#203060"}
        self.http.put("/api/art/selection", json={"folder": folder, "selection": sel})
        printed = self.wait(self.http.post("/api/art/print", json={"folder": folder}).json()["job_id"])
        sources = {p["name"]: p["source"] for p in printed["pieces"]}
        self.assertEqual(sources["back + spines"], "generated: tracklist with the spcmn mark (medium) on the album colour #203060")
        self.assertEqual(sources["inside of front insert"], "generated: centred with the spcmn mark (small) on white")
        self.assertIn("with the spcmn mark (small)", sources["under-tray image"])
        self.assertIn("with the spcmn mark (medium)", sources["disc label"])
        self.assertIn("title with the spcmn mark (large)", sources["front cover"])
        put("inside", layout="blank", logo="medium")
        cassette = self.wait(self.http.post("/api/art/print", json={"folder": folder, "format": "cassette"}).json()["job_id"])
        cs = {p["name"]: p["source"] for p in cassette["pieces"]}
        self.assertEqual(cs["inside"], "generated: blank on white with the spcmn mark (medium)")
        self.assertTrue(cs["spine"].endswith("with the spcmn mark (small)"), "a marked flap marks the spine")
        self.assertTrue(cs["back flap"].endswith("with the spcmn mark (medium)"))

    def test_art_choices_designs_and_uploads_survive_a_restart(self):
        import base64
        from PIL import Image
        from iaflac.gui.service import App
        folder = "Test Artist/Test Album (1999)"
        self.wait(self.http.post("/api/art/find", json={"folder": folder}).json()["job_id"])
        png = io.BytesIO()
        Image.new("RGB", (900, 900), (10, 200, 90)).save(png, "PNG")
        up = self.http.post("/api/art/upload", json={"folder": folder, "part": "inside", "name": "mine.png",
                                                     "data": base64.b64encode(png.getvalue()).decode()}).json()
        sel = {**up["selection"], "back": "colour:#5c53a2"}
        self.http.put("/api/art/selection", json={"folder": folder, "selection": sel})
        self.http.put("/api/art/style", json={"folder": folder, "part": "back", "layout": "centred", "logo": "medium"})
        again = App(settings=self.core.settings, mb_client=self.mb)              # the next launch
        state = again.art_state(folder)
        self.assertEqual(state["selection"], sel)
        self.assertEqual(state["styles"], {"back": "centred", "back:logo": "medium"})
        mine = next(i for i in state["images"] if i["id"] == sel["inside"])
        self.assertEqual((mine["source"], mine["label"]), ("upload", "your upload: mine.png"))
        self.assertEqual(again.uploads[folder][0].id, mine["id"], "kept as an upload, so Find again keeps it")
        # a damaged file is set aside, and the app starts empty rather than failing
        store = again._albums_file()
        store.write_text("{not json", encoding="utf-8")
        fresh = App(settings=self.core.settings, mb_client=self.mb)
        self.assertEqual(fresh.arts, {})
        self.assertTrue(store.with_suffix(".unreadable.json").exists())

    def test_3d_preview_textures_are_the_printed_parts(self):
        import io
        from PIL import Image
        folder = "Test Artist/Test Album (1999)"
        self.assertEqual(self.http.get("/api/art/texture", params={"folder": folder, "part": "front"}).status_code, 404)
        self.wait(self.http.post("/api/art/find", json={"folder": folder}).json()["job_id"])
        done = self.wait(self.http.post("/api/art/preview3d", json={"folder": folder}).json()["job_id"])
        self.assertEqual(done["parts"], ["back", "disc", "front", "inside", "tray"])
        sizes = {}
        for part in done["parts"]:
            r = self.http.get("/api/art/texture", params={"folder": folder, "part": part})
            self.assertEqual((r.status_code, r.headers["content-type"]), (200, "image/png"))
            sizes[part] = Image.open(io.BytesIO(r.content)).size
        self.assertEqual(sizes, {"front": (720, 720), "inside": (720, 720), "back": (906, 708), "tray": (828, 708),
                                 "disc": (708, 708)}, "trim sizes at 6 px/mm")
        self.assertFalse(any(Path(self.core.settings.print_folder).glob("*.pdf")), "a preview is not a print")

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
        self.assertEqual(src["inside of front insert"], "generated: tracklist with the spcmn mark (small) on the album colour #000000")
        self.assertEqual(src["under-tray image"], f"album colour {main} with the spcmn mark (small)")
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
