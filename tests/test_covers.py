"""Covers from the Cover Art Archive, found through MusicBrainz (covers.py and the /api/covers routes)."""

import os
import time
import unittest
from pathlib import Path
from unittest import mock

from iaflac import covers, health
from tests.test_health import Base, FakeMB, jpeg, mb_release


class CoverMB(FakeMB):
    """MusicBrainz and the Cover Art Archive: per release, its images; `nofront` releases say they have no front."""

    def __init__(self, releases, images, nofront=()):
        super().__init__(releases)
        self.images, self.nofront, self.listed = images, set(nofront), []

    def release(self, rid):
        r = super().release(rid)
        return {**r, "cover-art-archive": {"front": rid not in self.nofront}}

    def art_listing(self, rid):
        self.listed.append(rid)
        return self.images.get(rid, [])

    def group_releases(self, group):
        return [{**r, "cover-art-archive": {"front": r["id"] not in self.nofront}} for r in self.releases.values()
                if (r.get("release-group") or {}).get("id") == group]

    def image(self, url):
        return jpeg(1200 if "1200" in url else 500)


def front(name, approved=True):
    url = f"https://coverartarchive.org/release/x/{name}.jpg"
    return {"front": True, "types": ["Front"], "image": url, "approved": approved,
            "thumbnails": {"500": url + "-500", "1200": url + "-1200"}}


class Find(Base):
    def setUp(self):
        super().setUp()
        self.album("Test Artist/Test Album", [(1, "One"), (2, "Two")])
        self.h = self.scan().albums[0]

    def test_one_front_cover_per_release_with_the_other_pressings(self):
        back = {"front": False, "types": ["Back"], "image": "https://coverartarchive.org/b.jpg", "thumbnails": {}}
        group = {"release-group": {"id": "rg"}}
        mb = CoverMB([{**mb_release("a", ["One", "Two"]), **group}, {**mb_release("b", ["One", "Two"]), **group}],
                     {"a": [front("one"), back, front("one-obi")], "b": [front("two", approved=False)]})
        found = covers.find(mb, self.h)
        self.assertEqual([c["image"].rsplit("/", 1)[1] for c in found["covers"]], ["one.jpg", "two.jpg"])
        first = found["covers"][0]
        self.assertTrue(first["large"].endswith("-1200"), "the 1200 px version is what gets saved")
        self.assertTrue(first["thumb"].endswith("-500"))
        self.assertEqual((first["title"], first["country"], first["formats"]), ("Test Album", "US", "CD"))
        self.assertIn("Cover Art Archive", found["credit"])
        self.assertEqual(found["releases"], 2, "the second pressing came from the release group")

    def test_a_release_without_a_front_cover_is_not_asked_for_its_images(self):
        mb = CoverMB([mb_release("a", ["One", "Two"])], {"a": [front("one")]}, nofront={"a"})
        self.assertEqual(covers.find(mb, self.h)["covers"], [])
        self.assertEqual(mb.listed, [])

    def test_the_release_the_tags_name_comes_first(self):
        mb = CoverMB([mb_release("a", ["One", "Two"]), mb_release("mine", ["One", "Two"])],
                     {"a": [front("other")], "mine": [front("mine")]})
        self.h.mb_release_id = "mine"
        found = covers.find(mb, self.h)["covers"]
        self.assertEqual((found[0]["release_id"], found[0]["exact"]), ("mine", True))


class Api(Base):
    """Find, show and use a cover over HTTP, before any health check has run."""

    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        from iaflac.gui.server import create_app
        from iaflac.gui.service import App
        from iaflac.gui.settings import Settings
        base = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"IAFLAC_CONFIG": str(base / "config.json"), "IAFLAC_CACHE": str(base / "cache"),
                                                "IAFLAC_DATA": str(base / "data"), "IAFLAC_TRASH": str(self.trash)})
        self.env.start()
        self.folder = self.album("Test Artist/Test Album", [(1, "One"), (2, "Two")])
        mb = CoverMB([mb_release("a", ["One", "Two"])], {"a": [front("one")]})
        self.core = App(settings=Settings(library=str(self.root)), mb_client=mb)
        self.http = TestClient(create_app(self.core, token="t"))
        self.http.get("/?token=t")

    def tearDown(self):
        self.http.close()
        self.core.shutdown()
        self.env.stop()
        super().tearDown()

    def wait(self, job_id):
        for _ in range(200):
            j = self.http.get(f"/api/jobs/{job_id}").json()
            if j["state"] in ("done", "failed", "cancelled"):
                self.assertEqual(j["state"], "done", j.get("error"))
                return j["result"]
            time.sleep(0.05)
        self.fail("job did not finish")

    def test_find_show_use_then_replace_only_when_asked(self):
        folder = "Test Artist/Test Album"
        found = self.wait(self.http.post("/api/covers/find", json={"folder": folder}).json()["job_id"])
        cid = found["covers"][0]["id"]
        img = self.http.get(f"/api/covers/image/{cid}")
        self.assertEqual((img.status_code, img.headers["content-type"]), (200, "image/jpeg"))
        r = self.http.post("/api/covers/use", json={"folder": folder, "id": cid}).json()
        self.assertEqual((r["written"], r["px"]), ("cover.jpg", 1200))
        self.assertEqual(health.image_px((self.folder / "cover.jpg").read_bytes()), 1200)
        again = self.http.post("/api/covers/use", json={"folder": folder, "id": cid})
        self.assertEqual(again.status_code, 400, "an album with a cover keeps it unless asked to replace it")
        r = self.http.post("/api/covers/use", json={"folder": folder, "id": cid, "replace": True}).json()
        self.assertTrue(Path(r["trash"], "cover.jpg").is_file(), "the old cover went to the Trash")
        self.assertEqual(self.http.get("/api/covers/image/nope").status_code, 404)


if __name__ == "__main__":
    unittest.main()
