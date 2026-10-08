import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from iaflac import save, tagging
from iaflac.events import Cancelled
from iaflac.pipeline import Pipeline
from tests.helpers import HAVE_FFMPEG, HAVE_FLAC
from tests.test_pipeline import FakeIA, make_alt_item, make_item

NEED = unittest.skipUnless(HAVE_FLAC and HAVE_FFMPEG, "flac and ffmpeg required")


def png(size=(400, 400)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def add_file(fake_item, name, data, fmt):
    fake_item.files[name] = data
    fake_item.metadata["files"].append({"name": name, "format": fmt, "source": "original", "size": str(len(data)),
                                        "md5": hashlib.md5(data).hexdigest()})


class FakeMB:
    def __init__(self, data):
        self.data, self.asked = data, []

    def cover(self, release_id, group_id=""):
        self.asked.append(release_id)
        return self.data


@NEED
class Save(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.item = make_item("kob", "Test Artist - Test Album", "Test Artist", tracks=3, rip=True)
        cls.wav = make_alt_item("wav-1", "Test Artist - Wav Album", "Test Artist", "wav", tracks=2)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.library = Path(self.tmp.name) / "Music"
        self.library.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def cand(self, fake_item, client):
        pipe = Pipeline(client)
        cand = pipe.load(fake_item.doc["identifier"], media="wav" if fake_item is self.wav else "flac")
        pipe.probe(cand)
        return cand

    def test_plan_shows_the_volumio_layout_and_touches_nothing(self):
        client = FakeIA([self.item])
        cand = self.cand(self.item, client)
        plan = save.plan_save(cand, self.library)
        self.assertEqual(plan.folder, self.library / "Test Artist" / "Test Album (1999)")
        self.assertEqual([t.name for t in plan.tracks], ["01 - Track 1.flac", "02 - Track 2.flac", "03 - Track 3.flac"])
        self.assertEqual(plan.extra_files, [], "rip log and cue sheet are skipped by default")
        self.assertTrue(any("no cover art" in w for w in plan.warnings))
        self.assertEqual(list(self.library.iterdir()), [])
        self.assertEqual(plan.to_dict()["tracks"][0]["source"], "01 Track 1.flac")

    def test_apply_downloads_verifies_tags_and_renames(self):
        client = FakeIA([self.item])
        cand = self.cand(self.item, client)
        meta = tagging.from_candidate(cand)
        meta.genre = "Jazz"
        meta.tracks[1].title = "The Second One"
        events = []
        result = save.apply_save(client, cand, save.plan_save(cand, self.library, meta), progress=events.append)
        self.assertTrue(result.ok, result.failures)
        folder = self.library / "Test Artist" / "Test Album (1999)"
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ["01 - Track 1.flac", "02 - The Second One.flac", "03 - Track 3.flac"])
        t = tagging.read_track(folder / "02 - The Second One.flac")
        self.assertEqual((t["title"], t["album"], t["albumartist"], t["track"], t["tracktotal"], t["genre"], t["date"]),
                         ("The Second One", "Test Album", "Test Artist", 2, 3, "Jazz", "1999"))
        self.assertEqual(result.issues, ["no cover art (embedded or cover.jpg)"])
        stages = [e["stage"] for e in events]
        self.assertIn("download", stages)
        self.assertEqual(stages[-1], "saved")

    def test_an_uploaded_png_cover_becomes_cover_jpg_and_is_embedded(self):
        item = make_item("art", "Test Artist - Art Album", "Test Artist", tracks=2, rip=False, seed=4)
        add_file(item, "Front Cover.png", png(), "PNG")
        client = FakeIA([item])
        cand = self.cand(item, client)
        result = save.apply_save(client, cand, save.plan_save(cand, self.library))
        folder = result.folder
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["01 - Track 1.flac", "02 - Track 2.flac", "cover.jpg"])
        self.assertTrue(tagging.read_track(folder / "01 - Track 1.flac")["has_art"])
        self.assertEqual(result.issues, [])

    def test_cover_art_archive_is_used_when_the_upload_has_none(self):
        client = FakeIA([self.item])
        cand = self.cand(self.item, client)
        meta = tagging.from_candidate(cand)
        meta.mb_release_id = "rel-1"
        plan = save.plan_save(cand, self.library, meta)
        self.assertEqual(plan.cover, "Cover Art Archive")
        fake = FakeMB(png((800, 800)))
        result = save.apply_save(client, cand, plan, mb_client=fake)
        self.assertEqual(fake.asked, ["rel-1"])
        self.assertTrue((result.folder / "cover.jpg").exists())

    def test_wav_is_saved_as_tagged_flac(self):
        client = FakeIA([self.wav])
        cand = self.cand(self.wav, client)
        plan = save.plan_save(cand, self.library)
        self.assertEqual([t.name for t in plan.tracks], ["01 - Track 1.flac", "02 - Track 2.flac"])
        result = save.apply_save(client, cand, plan)
        self.assertTrue(result.ok, result.failures)
        self.assertEqual(sorted(p.suffix for p in result.folder.iterdir()), [".flac", ".flac"])
        self.assertEqual(tagging.read_track(result.folder / "01 - Track 1.flac")["album"], "Wav Album")

    def test_cancel_keeps_the_partial_download_and_a_retry_finishes_it(self):
        client = FakeIA([self.item])
        cand = self.cand(self.item, client)
        plan = save.plan_save(cand, self.library)
        seen = []

        def cancel():
            return len(seen) >= 1

        with self.assertRaises(Cancelled):
            save.apply_save(client, cand, plan, progress=lambda e: seen.append(e) if e.get("state") == "verified" else None,
                            cancel=cancel)
        self.assertTrue((plan.folder / ".iaflac-incomplete").exists())
        result = save.apply_save(client, cand, plan)
        self.assertTrue(result.ok)
        self.assertEqual(result.already_had, 1)
        self.assertFalse((plan.folder / ".iaflac-incomplete").exists())

    def test_bad_metadata_is_refused_before_anything_happens(self):
        client = FakeIA([self.item])
        cand = self.cand(self.item, client)
        meta = tagging.from_candidate(cand)
        meta.album = " "
        with self.assertRaises(ValueError):
            save.plan_save(cand, self.library, meta)
        meta = tagging.from_candidate(cand)
        meta.tracks.pop()
        with self.assertRaisesRegex(ValueError, "no tags yet"):
            save.plan_save(cand, self.library, meta)


if __name__ == "__main__":
    unittest.main()
