import hashlib
import tempfile
import unittest
from pathlib import Path

from iaflac import download
from iaflac.model import FileInfo, ItemInfo
from tests.helpers import HAVE_FLAC, encode_flac, tone_wav


def finfo(name, data=b"", source="original", fmt="", **kw):
    return FileInfo(name=name, format=fmt, source=source, size=len(data),
                    md5=hashlib.md5(data).hexdigest(), sha1=hashlib.sha1(data).hexdigest(), **kw)


class FakeResp:
    def __init__(self, data, status):
        self.data, self.status_code = data, status

    def iter_content(self, size):
        return (self.data[i:i + size] for i in range(0, len(self.data), size))

    def close(self):
        pass


class FakeClient:
    def __init__(self, files, honor_range=True):
        self.files, self.honor_range, self.calls, self.bytes_read = files, honor_range, [], 0

    def open_stream(self, identifier, name, start=0):
        self.calls.append((name, start))
        data = self.files[name]
        if start and self.honor_range:
            return FakeResp(data[start:], 206)
        return FakeResp(data, 200)


FLAC_A = b"fLaC" + b"A" * 3000       # not decodable, which is fine when flac isn't the point
LOG = b"Exact Audio Copy V1.5\nNo errors occurred\n"


def make_item(extra_files=(), title="Artist - Album (1999)"):
    files = [finfo("01 One.flac", FLAC_A, fmt="Flac"), finfo("disc/02 Two.flac", FLAC_A + b"2", fmt="Flac"),
             finfo("rip.log", LOG, fmt="Log"), finfo("cover.jpg", b"jpeg", fmt="JPEG"), *extra_files]
    return ItemInfo(identifier="item-1", title=title, files=files)


def contents(item):
    return {f.name: {"01 One.flac": FLAC_A, "disc/02 Two.flac": FLAC_A + b"2", "rip.log": LOG,
                     "cover.jpg": b"jpeg"}.get(f.name, b"x") for f in item.files}


class Planning(unittest.TestCase):
    def plan(self, item, **kw):
        return download.plan_get(item, Path("/tmp/iaflac-test-dest"), **kw)

    def names(self, res):
        return sorted(pf.info.name for pf in res.planned)

    def test_flacs_rip_files_and_original_art_are_planned(self):
        self.assertEqual(self.names(self.plan(make_item())), ["01 One.flac", "cover.jpg", "disc/02 Two.flac", "rip.log"])

    def test_derivatives_thumbnails_spectrograms_and_torrents_are_left_out(self):
        item = make_item([
            finfo("01 One_spectrogram.png", source="derivative", fmt="Spectrogram"),
            finfo("__ia_thumb.jpg", fmt="Item Tile"), finfo("item_files.xml", fmt="Metadata"),
            finfo("item_meta.xml", fmt="Metadata"), finfo("item_archive.torrent", fmt="Archive BitTorrent"),
            finfo("01 One.mp3", source="derivative", fmt="VBR MP3"), finfo("thumb.jpg", source="derivative", fmt="JPEG"),
            finfo("run.sh"), finfo("setup.exe"), finfo("payload.zip")])
        self.assertEqual(self.names(self.plan(item)), ["01 One.flac", "cover.jpg", "disc/02 Two.flac", "rip.log"])

    def test_derived_flacs_are_kept_since_they_are_real_flac(self):
        item = ItemInfo(identifier="x", files=[finfo("a.flac", b"1", source="derivative", fmt="Flac")])
        self.assertEqual(self.names(self.plan(item)), ["a.flac"])

    def test_no_art_flag(self):
        self.assertEqual(self.names(self.plan(make_item(), art=False)), ["01 One.flac", "disc/02 Two.flac", "rip.log"])

    def test_names_that_could_escape_the_folder_are_refused(self):
        evil = ["../evil.flac", "/etc/cron.d/x.log", "a/../../b.flac", "C:\\win\\x.flac", "ok/./x.flac", "a//b.flac"]
        item = ItemInfo(identifier="x", files=[finfo(n, b"1") for n in evil] + [finfo("fine.flac", b"1")])
        res = self.plan(item)
        self.assertEqual(self.names(res), ["fine.flac"])
        self.assertGreaterEqual(len(res.skipped), 5)
        for pf in res.planned:
            self.assertIn(res.folder.resolve(), pf.path.parents)

    def test_awkward_characters_are_sanitised_not_rejected(self):
        item = ItemInfo(identifier="x", files=[finfo('01: What? "Now".flac', b"1")])
        (pf,) = self.plan(item).planned
        self.assertEqual(pf.path.name, "01_ What_ _Now_.flac")

    def test_folder_name_has_no_path_separators_and_carries_the_identifier(self):
        name = download.folder_name(ItemInfo(identifier="abc", title="AC/DC - Back in Black: (1980)"))
        self.assertNotIn("/", name)
        self.assertTrue(name.endswith("[abc]"))


    def test_a_single_shared_top_folder_is_not_nested_again(self):
        item = ItemInfo(identifier="x", title="Album", files=[
            finfo("Album Dir/01 One.flac", b"1"), finfo("Album Dir/rip.log", b"2"), finfo("Album Dir/Scans/c.jpg", b"3")])
        res = self.plan(item)
        rels = sorted(str(pf.path.relative_to(res.folder.resolve())) for pf in res.planned)
        self.assertEqual(rels, ["01 One.flac", "Scans/c.jpg", "rip.log"])

    def test_multi_disc_layout_is_kept(self):
        item = ItemInfo(identifier="x", title="Box", files=[
            finfo("Disc 1/01.flac", b"1"), finfo("Disc 2/01.flac", b"2"), finfo("Disc 1/rip.log", b"3")])
        res = self.plan(item)
        rels = sorted(str(pf.path.relative_to(res.folder.resolve())) for pf in res.planned)
        self.assertEqual(rels, ["Disc 1/01.flac", "Disc 1/rip.log", "Disc 2/01.flac"])

    def test_files_at_the_top_level_are_left_alone(self):
        item = ItemInfo(identifier="x", title="Flat", files=[finfo("01.flac", b"1"), finfo("Dir/02.flac", b"2")])
        rels = sorted(str(pf.path.relative_to(self.plan(item).folder.resolve())) for pf in self.plan(item).planned)
        self.assertEqual(rels, ["01.flac", "Dir/02.flac"])

    def test_a_hostile_title_cannot_move_the_album_folder_outside_the_destination(self):
        dest = Path("/tmp/iaflac-test-dest")
        for title in ("../../../etc/evil", "..", ".", "/abs/olute", "a/../../b", "...", "  ..  ", ""):
            item = ItemInfo(identifier="x1", title=title, files=[finfo("a.flac", b"1")])
            res = download.plan_get(item, dest)
            self.assertIn(dest.resolve(), res.folder.resolve().parents, title)
            self.assertEqual(res.folder.parent, dest, title)            # one flat component, directly inside dest
            self.assertNotIn(res.folder.name, ("", ".", ".."), title)   # '..' inside a name is harmless; a bare '..' is not


class Downloading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmp.name) / "library"
        self.item = make_item()
        self.client = FakeClient(contents(self.item))

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, **kw):
        return download.get_item(self.client, self.item, self.dest, **kw)

    def test_without_yes_nothing_is_touched(self):
        res = self.get()
        self.assertFalse(res.executed)
        self.assertEqual(len(res.planned), 4)
        self.assertFalse(self.dest.exists(), "not even the destination folder may be created")
        self.assertEqual(self.client.calls, [])

    def test_with_yes_files_land_intact_with_subfolders(self):
        res = self.get(yes=True)
        self.assertTrue(res.ok, res.failures)
        self.assertEqual((res.downloaded, res.already_had), (4, 0))
        folder = res.folder
        self.assertEqual((folder / "disc" / "02 Two.flac").read_bytes(), FLAC_A + b"2")
        self.assertEqual((folder / "rip.log").read_bytes(), LOG)
        self.assertEqual(list(folder.rglob("*.part")), [])

    def test_a_second_run_downloads_nothing(self):
        self.get(yes=True)
        self.client.calls.clear()
        res = self.get(yes=True)
        self.assertEqual((res.downloaded, res.already_had), (0, 4))
        self.assertEqual(self.client.calls, [])

    def test_checksum_mismatch_leaves_no_file_behind(self):
        self.client.files["rip.log"] = b"tampered in transit"
        res = self.get(yes=True)
        self.assertFalse(res.ok)
        self.assertTrue(any("rip.log" in f and "checksum" in f for f in res.failures))
        self.assertFalse((res.folder / "rip.log").exists())
        self.assertFalse((res.folder / "rip.log.part").exists())
        self.assertTrue((res.folder / "01 One.flac").exists(), "other files are unaffected")

    def test_interrupted_download_resumes_from_the_partial(self):
        folder = download.plan_get(self.item, self.dest).folder
        part = folder / "01 One.flac.part"
        part.parent.mkdir(parents=True)
        part.write_bytes(FLAC_A[:1000])
        res = self.get(yes=True)
        self.assertTrue(res.ok, res.failures)
        self.assertIn(("01 One.flac", 1000), self.client.calls)
        self.assertEqual((folder / "01 One.flac").read_bytes(), FLAC_A)

    def test_server_that_ignores_range_restarts_cleanly(self):
        self.client.honor_range = False
        folder = download.plan_get(self.item, self.dest).folder
        part = folder / "01 One.flac.part"
        part.parent.mkdir(parents=True)
        part.write_bytes(FLAC_A[:1000])
        res = self.get(yes=True)
        self.assertTrue(res.ok, res.failures)
        self.assertEqual((folder / "01 One.flac").read_bytes(), FLAC_A)

    def test_a_dropped_connection_is_a_failure_that_keeps_the_partial(self):
        class Dropping(FakeClient):
            def open_stream(self, identifier, name, start=0):
                if name == "rip.log":
                    def broken():
                        yield LOG[:5]
                        raise OSError("connection reset")
                    r = FakeResp(b"", 200)
                    r.iter_content = lambda size: broken()
                    return r
                return super().open_stream(identifier, name, start)
        self.client = Dropping(contents(self.item))
        res = self.get(yes=True)
        self.assertFalse(res.ok)
        self.assertTrue(any("rerun to resume" in f for f in res.failures))
        self.assertEqual((res.folder / "rip.log.part").read_bytes(), LOG[:5])
        self.assertFalse((res.folder / "rip.log").exists())

    @unittest.skipUnless(HAVE_FLAC, "flac binary not installed")
    def test_a_file_that_matches_the_manifest_but_fails_flac_t_is_kept_and_reported(self):
        good = encode_flac(tone_wav(44100))
        damaged = bytearray(good)
        damaged[len(damaged) // 2] ^= 0xFF
        damaged = bytes(damaged)
        item = ItemInfo(identifier="dmg", title="Damaged", files=[finfo("a.flac", damaged, fmt="Flac")])
        res = download.get_item(FakeClient({"a.flac": damaged}), item, self.dest, yes=True)
        self.assertTrue(res.ok)
        self.assertTrue((res.folder / "a.flac").exists())
        self.assertTrue(any("fails flac -t" in w for w in res.warnings), res.warnings)

    @unittest.skipUnless(HAVE_FLAC, "flac binary not installed")
    def test_a_healthy_flac_raises_no_warning(self):
        good = encode_flac(tone_wav(44100))
        item = ItemInfo(identifier="ok", title="Fine", files=[finfo("a.flac", good, fmt="Flac")])
        res = download.get_item(FakeClient({"a.flac": good}), item, self.dest, yes=True)
        self.assertEqual((res.ok, res.warnings), (True, []))


if __name__ == "__main__":
    unittest.main()


def art_item(names, title="Album"):
    files = [finfo(f"01 One.flac", FLAC_A, fmt="Flac"), finfo("02 Two.flac", FLAC_A + b"2", fmt="Flac"),
             finfo("rip.log", LOG, fmt="Log"), finfo("album.cue", b"cue", fmt="Cue Sheet"), finfo("album.accurip", b"ar")]
    for n in names:
        files.append(finfo(n, b"img:" + n.encode()))
    return ItemInfo(identifier="item-1", title=title, files=files)


class Covers(unittest.TestCase):
    def pick(self, *names, **kw):
        cover = download.pick_cover(art_item(names))
        return cover.name if cover else None

    def test_the_front_cover_beats_scans_and_booklets(self):
        self.assertEqual(self.pick("Scans/1 - Cover.jpg", "Scans/Back.jpg", "Scans/Book1.jpg", "Scans/Disc.jpg",
                                   "Scans/Inlay.jpg", "folder.jpg"), "Scans/1 - Cover.jpg")
        self.assertEqual(self.pick("folder.jpg", "Back.jpg"), "folder.jpg")
        self.assertEqual(self.pick("Front.png", "folder.jpg"), "Front.png")
        self.assertEqual(self.pick("folder.jpg", "cover.jpg", "Cover Art.jpg"), "cover.jpg")

    def test_back_disc_and_booklet_scans_are_never_a_cover(self):
        self.assertIsNone(self.pick("Back.jpg", "Disc.jpg", "Inlay.jpg", "Book1.jpg"))
        self.assertIsNone(self.pick())

    def test_derived_thumbnails_and_non_images_are_ignored(self):
        item = art_item(["notes.pdf"])
        item.files.append(finfo("thumb.jpg", b"t", source="derivative"))
        item.files.append(finfo("__ia_thumb.jpg", b"t"))
        self.assertIsNone(download.pick_cover(item))


class LibraryLayout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = Path(self.tmp.name) / "1Music"
        self.lib.mkdir()
        self.item = art_item(["Scans/1 - Cover.jpg", "Scans/Back.jpg", "folder.jpg", "notes.txt", "sums.md5"])
        self.client = FakeClient({f.name: (FLAC_A if f.name == "01 One.flac" else FLAC_A + b"2" if f.name == "02 Two.flac"
                                           else LOG if f.name == "rip.log" else b"cue" if f.name == "album.cue"
                                           else b"ar" if f.name == "album.accurip" else b"img:" + f.name.encode())
                                  for f in self.item.files})

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, **kw):
        return download.plan_library(self.item, self.lib, "The Mars Volta", "Frances the Mute", **kw)

    def rels(self, res):
        return sorted(str(pf.path.relative_to(res.folder.resolve())) for pf in res.planned)

    def test_default_is_audio_cover_and_rip_evidence_in_artist_album_folders(self):
        res = self.plan()
        self.assertEqual(res.folder, self.lib / "The Mars Volta" / "Frances the Mute")
        self.assertEqual(self.rels(res), ["01 One.flac", "02 Two.flac", "album.accurip", "album.cue", "cover.jpg", "rip.log"])
        self.assertEqual(res.cover.name, "Scans/1 - Cover.jpg")

    def test_rip_files_can_be_skipped_and_the_cover_dropped(self):
        self.assertEqual(self.rels(self.plan(rip="skip", art="none")), ["01 One.flac", "02 Two.flac"])
        self.assertEqual(self.rels(self.plan(rip="skip")), ["01 One.flac", "02 Two.flac", "cover.jpg"])

    def test_art_all_keeps_the_other_scans_under_their_own_names_without_fetching_the_cover_twice(self):
        res = self.plan(art="all")
        rels = self.rels(res)
        self.assertIn("cover.jpg", rels)
        self.assertIn("Scans/Back.jpg", rels)          # keeps its subfolder: the FLACs sit at the album root
        self.assertIn("folder.jpg", rels)
        self.assertEqual([pf.info.name for pf in res.planned].count("Scans/1 - Cover.jpg"), 1)

    def test_planning_touches_nothing(self):
        self.plan()
        self.assertEqual(list(self.lib.iterdir()), [])

    def test_hostile_artist_and_album_names_stay_inside_the_library(self):
        for artist, album in (("../../x", "../y"), ("/abs", "a/../../b"), ("AC/DC", "Back in Black")):
            res = download.plan_library(self.item, self.lib, artist, album)
            self.assertIn(self.lib.resolve(), res.folder.resolve().parents, (artist, album))
            self.assertEqual(res.folder.parent.parent, self.lib, "exactly Artist/Album, whatever the names contain")
            for pf in res.planned:
                self.assertIn(res.folder.resolve(), pf.path.parents)
        for artist, album in (("..", "."), ("", "x"), ("x", ""), ("...", "  ")):      # nothing usable is left: refuse
            with self.assertRaises(download.DownloadError, msg=(artist, album)):
                download.plan_library(self.item, self.lib, artist, album)

    def test_download_lands_files_and_removes_the_incomplete_marker(self):
        res = download.library_get(self.client, self.item, self.lib, "The Mars Volta", "Frances the Mute", yes=True)
        self.assertTrue(res.ok, res.failures)
        folder = self.lib / "The Mars Volta" / "Frances the Mute"
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["01 One.flac", "02 Two.flac", "album.accurip", "album.cue", "cover.jpg", "rip.log"])
        self.assertEqual((folder / "cover.jpg").read_bytes(), b"img:Scans/1 - Cover.jpg")
        self.assertFalse((folder / download.INCOMPLETE).exists())

    def test_an_existing_album_folder_is_never_merged_into(self):
        folder = self.lib / "The Mars Volta" / "Frances the Mute"
        folder.mkdir(parents=True)
        (folder / "my own file.flac").write_bytes(b"precious")
        with self.assertRaisesRegex(download.DownloadError, "not merging"):
            download.library_get(self.client, self.item, self.lib, "The Mars Volta", "Frances the Mute", yes=True)
        self.assertEqual([p.name for p in folder.iterdir()], ["my own file.flac"])
        self.assertEqual(self.client.calls, [])

    def test_a_same_named_file_that_differs_from_the_archives_copy_is_never_overwritten(self):
        folder = self.lib / "The Mars Volta" / "Frances the Mute"
        folder.mkdir(parents=True)
        (folder / "01 One.flac").write_bytes(b"my own, different, rip of this track")
        with self.assertRaisesRegex(download.DownloadError, "differs from archive.org"):
            download.library_get(self.client, self.item, self.lib, "The Mars Volta", "Frances the Mute", yes=True)
        self.assertEqual((folder / "01 One.flac").read_bytes(), b"my own, different, rip of this track")
        self.assertEqual(self.client.calls, [])

    def test_a_finished_album_can_be_run_again_and_finder_litter_is_ignored(self):
        first = download.library_get(self.client, self.item, self.lib, "The Mars Volta", "Frances the Mute", yes=True)
        self.assertTrue(first.ok)
        (first.folder / ".DS_Store").write_bytes(b"finder")
        self.client.calls.clear()
        again = download.library_get(self.client, self.item, self.lib, "The Mars Volta", "Frances the Mute", yes=True)
        self.assertTrue(again.ok, again.failures)
        self.assertEqual((again.downloaded, again.already_had), (0, 6))
        self.assertEqual(self.client.calls, [])

    def test_an_empty_existing_album_folder_is_fine(self):
        (self.lib / "The Mars Volta" / "Frances the Mute").mkdir(parents=True)
        self.assertTrue(download.library_get(self.client, self.item, self.lib, "The Mars Volta", "Frances the Mute", yes=True).ok)

    def test_an_interrupted_run_keeps_its_marker_and_a_rerun_resumes(self):
        class Flaky(FakeClient):
            broken = True

            def open_stream(self, identifier, name, start=0):
                if name == "rip.log" and self.broken:
                    def gone():
                        yield LOG[:4]
                        raise OSError("reset")
                    r = FakeResp(b"", 200)
                    r.iter_content = lambda size: gone()
                    return r
                return super().open_stream(identifier, name, start)
        client = Flaky(self.client.files)
        first = download.library_get(client, self.item, self.lib, "The Mars Volta", "Frances the Mute", yes=True)
        folder = first.folder
        self.assertFalse(first.ok)
        self.assertTrue((folder / download.INCOMPLETE).exists(), "the marker says: this folder is mid-download")
        client.broken = False
        second = download.library_get(client, self.item, self.lib, "The Mars Volta", "Frances the Mute", yes=True)
        self.assertTrue(second.ok, second.failures)
        self.assertEqual(second.already_had, 5)
        self.assertFalse((folder / download.INCOMPLETE).exists())
        self.assertEqual((folder / "rip.log").read_bytes(), LOG)
