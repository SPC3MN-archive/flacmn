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


class Spreads(unittest.TestCase):
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
        self.assertEqual(sorted(sel), ["back", "disc", "front", "inside", "tray"])
        self.assertEqual(art.get(sel["inside"]).ref, "https://caa/rel-2/b1.jpg", "the first booklet page")
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
                           **{k: get(k) for k in cdart.PARTS})
        from pypdf import PdfReader
        pdf = PdfReader(str(out))
        self.assertEqual(len(pdf.pages), report.pages)
        self.assertEqual(report.pages, 3, "outside, inside, disc")
        sizes = {(round(float(pg.mediabox.width)), round(float(pg.mediabox.height))) for pg in pdf.pages}
        self.assertEqual(sizes, {(612, 792)}, "Letter portrait throughout, so the inside lines up with the outside")
        self.assertEqual([(p.page, p.name) for p in report.pieces],
                         [(1, "front cover"), (1, "back + spines"), (2, "inside of front insert"), (2, "under-tray image"),
                          (3, "disc label")])
        self.assertNotIn("spines generated", report.pieces[1].source, "the 1500x1180 scan includes its spines")
        self.assertEqual(report.pieces[3].source, "none found: left blank", "no spcmn mark under the tray")

    def test_save_scans_into_the_album_folder(self):
        art = self.find()
        fetcher = cdart.ArtFetcher(self.mb, self.ia, cache=self.library / ".art-cache")
        written = cdart.save_to_folder(art, cdart.default_selection(art), self.folder, fetcher)
        names = sorted(Path(p).name for p in written)
        self.assertEqual(names, ["back.jpg", "disc.jpg", "front.jpg", "inside.jpg"])
        self.assertEqual(len(self.mb.fetched), 4, "only the chosen scans were downloaded")
        self.assertTrue((self.folder / "cover.jpg").exists(), "the player's cover is left alone")

    def test_saving_scans_skips_colours_and_images_borrowed_from_another_part(self):
        art = self.find()
        sel = cdart.default_selection(art)
        sel.update(tray=sel["front"], inside="colour:#123456")       # the front cover fills the under-tray gap
        fetcher = cdart.ArtFetcher(self.mb, self.ia, cache=self.library / ".art-cache")
        names = sorted(Path(p).name for p in cdart.save_to_folder(art, sel, self.folder, fetcher))
        self.assertEqual(names, ["back.jpg", "disc.jpg", "front.jpg"], "no tray.jpg that is really the front cover")

    def test_the_palette_comes_from_the_chosen_front_cover(self):
        art = self.find()
        sel = cdart.default_selection(art)
        self.assertEqual(cdart.palette_source(art, sel).id, sel["front"])
        self.assertEqual(cdart.palette_source(art, {**sel, "front": None}).kind, "front", "else the best front scan")

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
        self.assertEqual([p.source.split(":")[0] for p in report.pieces],
                         ["generated", "generated", "generated", "none found", "generated"], "no mark under the tray, so it stays blank")



def two_tone(main=(30, 60, 140), accent=(240, 200, 40), split=0.75) -> bytes:
    """A cover mostly in `main` with a band of `accent`."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (400, 400), main)
    ImageDraw.Draw(im).rectangle((0, int(400 * split), 400, 400), fill=accent)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class Gaps(unittest.TestCase):
    def test_the_palette_is_the_covers_colours_most_used_first(self):
        colours = cdart.palette(two_tone())
        self.assertEqual(colours[:2], ["#1e3c8c", "#f0c828"])
        flat = cdart.palette(jpeg(color=(200, 30, 30)))
        self.assertEqual(len(flat), 1, "a flat cover is one colour")
        rgb = [int(flat[0][i:i + 2], 16) for i in (1, 3, 5)]
        self.assertTrue(all(abs(a - b) <= 4 for a, b in zip(rgb, (200, 30, 30))), flat)      # within JPEG's rounding

    def test_missing_parts_take_the_main_colour_and_the_disc_keeps_the_front_cut(self):
        sel = {"front": "f1", "back": None, "inside": None, "tray": None, "disc": None}
        filled = cdart.fill_gaps(sel, ["#1e3c8c", "#f0c828"])
        self.assertEqual(filled, {"front": "f1", "back": "colour:#1e3c8c", "inside": "colour:#1e3c8c",
                                  "tray": "colour:#1e3c8c", "disc": None})
        self.assertEqual(cdart.fill_gaps(sel, []), sel, "no colours: generated on white, as before")
        self.assertTrue(cdart.is_colour("colour:#1e3c8c"))
        self.assertFalse(cdart.is_colour("colour:red"))
        self.assertFalse(cdart.is_colour("f1"))

    def test_colour_fills_print_with_the_title_or_tracklist_on_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "case.pdf"
            blue, gold = jc.Colour("#1e3c8c"), jc.Colour("#f0c828")
            report = jc.render(out, artist="A", album="B", tracks=[{"disc": 1, "track": 1, "title": "One", "length": 60}],
                               front=jc.Art(two_tone(), "cover.png"), back=blue, inside=gold, tray=blue, disc=gold)
            self.assertEqual(out.read_bytes()[:5], b"%PDF-")
        src = {p.name: p.source for p in report.pieces}
        self.assertEqual(src["back + spines"], "generated: tracklist with the spcmn mark (small) on the album colour #1e3c8c")
        self.assertEqual(src["inside of front insert"], "generated: tracklist on the album colour #f0c828", "no mark inside")
        self.assertEqual(src["under-tray image"], "album colour #1e3c8c")
        self.assertEqual(src["disc label"], "generated: title on the album colour #f0c828 with the spcmn mark (small)")
        self.assertEqual(jc.Colour("#ffffff").rgb, (1.0, 1.0, 1.0))


if __name__ == "__main__":
    unittest.main()


class PartImages(unittest.TestCase):
    def test_each_part_is_cut_out_at_its_trim_size_and_place(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            for paper in ("letter", "a4"):
                opts = jc.PrintOptions(paper=paper, crop_marks=False)
                pdf = Path(tmp) / f"{paper}.pdf"
                colours = {"front": "#ff0000", "back": "#00ff00", "inside": "#0000ff", "tray": "#ffff00", "disc": "#ff00ff"}
                jc.render(pdf, artist="A", album="B", opts=opts, **{k: jc.Colour(v) for k, v in colours.items()})
                parts = jc.part_images(pdf, opts, px_per_mm=4)
                for part, hexc in colours.items():
                    im = Image.open(io.BytesIO(parts[part])).convert("RGB")
                    want = tuple(int(hexc[i:i + 2], 16) for i in (1, 3, 5))
                    w, h = im.size
                    probes = [(w // 2, h // 6)] if part == "disc" else [(1, 1), (w - 2, h - 2), (w // 2, h // 2)]
                    for xy in probes:
                        self.assertEqual(im.getpixel(xy), want, f"{paper} {part} at {xy}")


class Designs(unittest.TestCase):
    def test_the_spcmn_mark_has_its_138_dots_and_every_design_draws(self):
        from iaflac import designs
        self.assertEqual(len(designs.LOGO_DOTS), 138)
        self.assertEqual(max(x for _, x, _ in designs.LOGO_DOTS), 31.5)
        tracks = [{"disc": 1, "track": i, "title": f"Song {i}", "length": 200} for i in range(1, 30)]
        with tempfile.TemporaryDirectory() as tmp:
            for layout in designs.TRACKLISTS + ("blank",):
                for logo in designs.logos_for(layout):
                    styles = {"back": layout, "back:logo": logo, "inside": layout, "inside:logo": logo, "tray:logo": logo,
                              "front": "blank" if layout == "blank" else "title", "front:logo": logo, "disc:logo": logo}
                    report = jc.render(Path(tmp) / f"{layout}-{logo}.pdf", artist="A", album="B", tracks=tracks,
                                       back=jc.Colour("#000000"), tray=jc.Colour("#5c53a2"), styles=styles)
                    self.assertEqual(report.pages, 3, (layout, logo))

    def test_large_marks_only_where_there_is_no_tracklist(self):
        from iaflac import designs
        self.assertEqual(designs.logos_for("blank"), ("none", "small", "medium", "large"))
        self.assertEqual(designs.logos_for("list"), ("none", "small", "medium"))
        self.assertIsNone(designs.logo_place("list", "none", 0, 0, 138, 118))
        x, base, lw = designs.logo_place("list", "small", 0, 0, 138, 118)
        self.assertLess(x, 20, "beside a list the small mark sits bottom-left, clear of the total on the right")
        self.assertGreater(designs.logo_reserve("list", "medium", 138, 118), designs.logo_reserve("list", "small", 138, 118))

    def test_a_marked_back_marks_its_spines(self):
        from PIL import Image, ImageChops
        with tempfile.TemporaryDirectory() as tmp:
            opts = jc.PrintOptions(crop_marks=False)
            shots = {}
            for logo in ("none", "small"):
                pdf = Path(tmp) / f"{logo}.pdf"
                jc.render(pdf, artist="A", album="B", back=jc.Colour("#000000"), opts=opts, styles={"back": "blank", "back:logo": logo})
                shots[logo] = Image.open(io.BytesIO(jc.part_images(pdf, opts, px_per_mm=6)["back"])).convert("RGB")
            spine = lambda im: im.crop((0, 0, 39, im.size[1]))        # the left spine, 6.5 mm at 6 px/mm
            changed = ImageChops.difference(spine(shots["none"]), spine(shots["small"])).getbbox()
            self.assertIsNotNone(changed, "the left spine carries a mark")
            self.assertLess(changed[1], shots["small"].size[1] * 0.3, "at the spine's top end, where its text finishes")

    def test_the_mark_in_charcoal_and_off_white(self):
        from iaflac import designs
        light, dark = (0.95, 0.93, 0.9), (0.1, 0.1, 0.12)
        self.assertEqual(designs.INKS, ("auto", "charcoal", "off-white"))
        self.assertEqual(designs.logo_colours("auto", light)("s"), designs.CHARCOAL)
        self.assertEqual(designs.logo_colours("auto", dark)("m"), designs.OFF_WHITE)
        self.assertEqual(designs.logo_colours("off-white", light)("p"), designs.OFF_WHITE)
        self.assertEqual(designs.logo_colours("green", dark)("c"), designs.OFF_WHITE, "anything else is auto")
        self.assertEqual(designs.DEFAULT_LOGO, "small")
