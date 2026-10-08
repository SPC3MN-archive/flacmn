import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from iaflac import cdart, jewelcase as jc, tagging
from iaflac.tagging import AlbumMeta, TrackMeta
from tests.helpers import HAVE_FFMPEG, HAVE_FLAC, encode_flac, tone_wav
from tests.test_pipeline import FakeIA, make_item

NEED = unittest.skipUnless(HAVE_FLAC and HAVE_FFMPEG, "flac and ffmpeg required")


def jpeg(size=(1200, 1200), color=(200, 30, 30)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


class Names(unittest.TestCase):
    def test_scan_names(self):
        cases = {
            "Scans/Back.jpg": "back", "inlay.jpg": "back", "Tray Card.png": "back", "Rear.JPG": "back",
            "Front.jpg": "front", "cover.jpg": "front", "folder.jpg": "front",
            "CD.jpg": "disc", "Disc 2.jpg": "disc", "cd1.png": "disc",
            "Booklet 03.jpg": "booklet", "Booklet/04.jpg": "booklet", "inside.jpg": "booklet", "02.jpg": "booklet",
            "Spine.jpg": "spine", "Tray.jpg": "tray", "Matrix.jpg": "other", "OBI.jpg": "other",
            "Scans/CD/front.jpg": "front", "random photo.jpg": "other",
        }
        for name, kind in cases.items():
            self.assertEqual(cdart.classify_name(name), kind, name)


class Imposition(unittest.TestCase):
    def test_eight_page_booklet(self):
        self.assertEqual(jc.impose([1, 2, 3, 4, 5, 6, 7, 8]),
                         [((8, 1), (2, 7)), ((6, 3), (4, 5))])

    def test_padding_with_blanks(self):
        self.assertEqual(jc.impose([1, 2, 3]), [((None, 1), (2, 3))])

    def test_spreads_split_into_pages(self):
        from PIL import Image
        self.assertEqual(len(jc.split_spread(Image.new("RGB", (2400, 1200)))), 2)
        self.assertEqual(len(jc.split_spread(Image.new("RGB", (1200, 1200)))), 1)


class Siblings(unittest.TestCase):
    def test_same_tracklist_cd_pressings_with_art_best_first(self):
        def r(rid, tracks, fmt="CD", count=0, back=False):
            return {"id": rid, "media": [{"format": fmt, "track-count": t} for t in tracks],
                    "cover-art-archive": {"count": count, "back": back}}
        group = [r("mine", [6], count=1), r("few", [6], count=3, back=True), r("many", [6], count=20, back=True),
                 r("noart", [6]), r("bonus", [7], count=30, back=True), r("vinyl", [6], fmt='12" Vinyl', count=9),
                 r("frontonly", [6], count=40)]
        self.assertEqual([x["id"] for x in cdart.siblings(group, [6], exclude="mine")], ["many", "few", "frontonly"])


class FakeMB:
    def __init__(self):
        self.release_data = {"id": "rel-1", "title": "Test Album", "date": "1999", "country": "US",
                             "release-group": {"id": "rg-1"},
                             "media": [{"format": "CD", "track-count": 2}]}
        self.listings = {
            "rel-1": [{"types": ["Front"], "image": "https://caa/rel-1/front.jpg", "thumbnails": {"500": "https://caa/rel-1/front-500.jpg"}}],
            "rel-2": [{"types": ["Front"], "image": "https://caa/rel-2/front.jpg", "thumbnails": {}},
                      {"types": ["Back", "Spine"], "image": "https://caa/rel-2/back.jpg", "thumbnails": {}},
                      {"types": ["Booklet"], "image": "https://caa/rel-2/b1.jpg", "thumbnails": {}, "comment": "pages 2-3"},
                      {"types": ["Booklet"], "image": "https://caa/rel-2/b2.jpg", "thumbnails": {}},
                      {"types": ["Medium"], "image": "https://caa/rel-2/cd.jpg", "thumbnails": {}},
                      {"types": ["Matrix/Runout"], "image": "https://caa/rel-2/matrix.jpg", "thumbnails": {}}],
        }
        self.fetched = []

    def release(self, rid):
        return self.release_data

    def group_releases(self, rgid):
        return [{"id": "rel-1", "media": [{"format": "CD", "track-count": 2}], "cover-art-archive": {"count": 1}},
                {"id": "rel-2", "date": "1997", "country": "AU", "media": [{"format": "CD", "track-count": 2}],
                 "cover-art-archive": {"count": 6, "back": True}}]

    def art_listing(self, rid):
        return self.listings.get(rid, [])

    def search_releases(self, artist, album, limit=15):
        return [{"id": "rel-1", "track-count": 2, "score": 100}]

    def image_size(self, url):
        return (1500, 1180) if "back" in url else (1200, 1200)

    def image(self, url):
        self.fetched.append(url)
        return jpeg((1500, 1180) if "back" in url else (1200, 1200))


@NEED
class FindArt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flac = encode_flac(tone_wav(44100))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.library = Path(self.tmp.name)
        self.folder = self.library / "Test Artist" / "Test Album (1999)"
        self.folder.mkdir(parents=True)
        paths = {}
        for i in (1, 2):
            p = self.folder / f"{i:02d}.flac"
            p.write_bytes(self.flac)
            paths[p.name] = p
        self.meta = AlbumMeta(albumartist="Test Artist", album="Test Album", date="1999", mb_release_id="rel-1",
                              mb_releasegroup_id="rg-1", source_url="https://archive.org/details/scans-1",
                              tracks=[TrackMeta("01.flac", "One", 1), TrackMeta("02.flac", "Two", 2)])
        tagging.apply(self.folder, self.meta, paths, jpeg((600, 600)))
        # the archive.org upload it came from ships a back scan and a disc scan
        self.item = make_item("scans-1", "Test Artist - Test Album", "Test Artist", tracks=2, rip=False)
        for name, data in (("Scans/Back.jpg", jpeg((1510, 1180))), ("Scans/CD.jpg", jpeg((1000, 1000)))):
            self.item.files[name] = data
            self.item.metadata["files"].append({"name": name, "format": "JPEG", "source": "original", "size": str(len(data)),
                                                "md5": hashlib.md5(data).hexdigest()})
        self.ia = FakeIA([self.item])
        self.mb = FakeMB()

    def tearDown(self):
        self.tmp.cleanup()

    def find(self, **kw):
        return cdart.find_art(self.folder, mb_client=self.mb, ia_client=self.ia, library_root=self.library, **kw)

    def test_every_source_is_gathered_and_classified(self):
        art = self.find()
        self.assertEqual((art.artist, art.album, art.folder), ("Test Artist", "Test Album", "Test Artist/Test Album (1999)"))
        sources = {(i.source, i.kind) for i in art.images}
        self.assertIn(("folder", "front"), sources)
        self.assertIn(("archive.org", "back"), sources)
        self.assertIn(("archive.org", "disc"), sources)
        self.assertIn(("coverartarchive", "front"), sources)
        self.assertIn(("coverartarchive-sibling", "booklet"), sources)
        self.assertEqual([r["id"] for r in art.releases], ["rel-1", "rel-2"])
        self.assertEqual(art.completeness()["booklet"], 2)
        self.assertEqual(self.mb.fetched, [], "nothing downloaded at full size while searching")

    def test_sizes_are_read_without_downloading(self):
        art = self.find()
        back = next(i for i in art.images if i.ref == "https://caa/rel-2/back.jpg")
        self.assertEqual((back.width, back.height, cdart.print_dpi(back)), (1500, 1180, 252))
        ia_back = next(i for i in art.images if i.source == "archive.org" and i.kind == "back")
        self.assertEqual((ia_back.width, ia_back.height), (1510, 1180), "read from a 64 KB range request")
        self.assertEqual(self.mb.fetched, [])

    def test_the_case_comes_from_one_scan_set_when_it_is_sharp_enough(self):
        art = self.find()
        sel = cdart.default_selection(art)
        self.assertEqual([art.get(i).ref for i in sel["booklet"]], ["https://caa/rel-2/b1.jpg", "https://caa/rel-2/b2.jpg"])
        for kind in ("front", "back", "disc"):
            self.assertEqual(art.get(sel[kind]).release_id, "rel-2", f"{kind} matches the booklet's pressing")

    def test_a_much_sharper_scan_wins_over_the_matching_set(self):
        (self.folder / "Artwork").mkdir()
        (self.folder / "Artwork" / "front.jpg").write_bytes(jpeg((3000, 3000)))
        art = self.find()
        sel = cdart.default_selection(art)
        self.assertEqual(art.get(sel["front"]).ref, str(self.folder / "Artwork" / "front.jpg"))

    def test_round_trip_and_a_print_from_the_selection(self):
        art = cdart.ArtSet.from_dict(self.find().to_dict())
        sel = cdart.default_selection(art)
        fetcher = cdart.ArtFetcher(self.mb, self.ia, cache=self.library / ".art-cache")

        def get(kind):
            im = art.get(sel[kind]) if sel[kind] else None
            return jc.Art(fetcher.get(im), im.label) if im else None

        out = self.library / "print.pdf"
        report = jc.render(out, artist=art.artist, album=art.album, year=art.year, tracks=art.tracks,
                           front=get("front"), back=get("back"), disc=get("disc"),
                           booklet=[jc.Art(fetcher.get(art.get(i)), art.get(i).label) for i in sel["booklet"]])
        from pypdf import PdfReader
        pdf = PdfReader(str(out))
        self.assertEqual(len(pdf.pages), report.pages)
        self.assertEqual(report.pages, 3, "tray+disc, booklet outside, booklet inside")
        letter = (round(float(pdf.pages[0].mediabox.width)), round(float(pdf.pages[0].mediabox.height)))
        self.assertEqual(letter, (612, 792))
        self.assertGreater(float(pdf.pages[1].mediabox.width), float(pdf.pages[1].mediabox.height), "booklet pages are landscape")
        names = [p.name for p in report.pieces]
        self.assertEqual(names, ["tray card", "disc label", "booklet sheet 1 (outside)", "booklet sheet 1 (inside)"])
        tray = report.pieces[0]
        self.assertNotIn("spines generated", tray.source, "the 1510x1180 scan includes its spines")

    def test_save_scans_into_the_album_folder(self):
        art = self.find()
        fetcher = cdart.ArtFetcher(self.mb, self.ia, cache=self.library / ".art-cache")
        written = cdart.save_to_folder(art, cdart.default_selection(art), self.folder, fetcher)
        names = sorted(Path(p).name for p in written)
        self.assertEqual(names, ["back.jpg", "booklet-01.jpg", "booklet-02.jpg", "disc.jpg", "front.jpg"])
        self.assertEqual(len(self.mb.fetched), 5, "only the chosen scans were downloaded")
        self.assertTrue((self.folder / "cover.jpg").exists(), "the player's cover is left alone")

    def test_an_untagged_album_is_matched_on_musicbrainz_first(self):
        from iaflac import musicbrainz as mb

        class Matching(FakeMB):
            def release(self, rid):
                rel = dict(self.release_data)
                rel["artist-credit"] = [{"name": "Test Artist", "artist": {"id": "a"}}]
                rel["media"] = [{"format": "CD", "track-count": 2,
                                 "tracks": [{"title": "One", "length": 1000}, {"title": "Two", "length": 1000}]}]
                return rel

        self.mb = Matching()
        folder2 = self.library / "Test Artist" / "Plain"
        folder2.mkdir()
        for i in (1, 2):
            (folder2 / f"{i:02d}.flac").write_bytes(self.flac)
        tagging.apply(folder2, AlbumMeta(albumartist="Test Artist", album="Test Album",
                                         tracks=[TrackMeta("01.flac", "One", 1), TrackMeta("02.flac", "Two", 2)]),
                      {n: folder2 / n for n in ("01.flac", "02.flac")})
        art = cdart.find_art(folder2, mb_client=self.mb, ia_client=None, library_root=self.library)
        self.assertEqual(art.mb_release_id, "rel-1")
        self.assertTrue(any("matched on MusicBrainz" in n for n in art.notes), art.notes)

    def test_nothing_found_says_what_will_be_generated(self):
        empty = self.library / "Nobody" / "Nothing"
        empty.mkdir(parents=True)
        (empty / "01.flac").write_bytes(self.flac)
        art = cdart.find_art(empty, mb_client=None, ia_client=None, library_root=self.library)
        self.assertTrue(any("tracklist tray card" in n for n in art.notes), art.notes)
        report = jc.render(self.library / "gen.pdf", artist="Nobody", album="Nothing", tracks=art.tracks)
        self.assertEqual([p.source.split(":")[0] for p in report.pieces], ["generated", "generated", "generated"])


if __name__ == "__main__":
    unittest.main()
