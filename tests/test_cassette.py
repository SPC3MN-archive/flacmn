import io
import tempfile
import unittest
from pathlib import Path

from iaflac import cassette as cs, jewelcase as jc


def jpeg(size=(1200, 1200), color=(200, 30, 30)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def near(rgb, want, tol=6) -> bool:
    """JPEG re-encoding shifts colours by a few levels."""
    return all(abs(a - b) <= tol for a, b in zip(rgb, want))


def tracks(lengths):
    return [{"disc": 1, "track": i, "title": f"Song {i}", "length": s} for i, s in enumerate(lengths, 1)]


class Sizes(unittest.TestCase):
    def test_the_standard_j_card(self):
        self.assertEqual((cs.FLAP_W, cs.SPINE_W, cs.FRONT_W, cs.JCARD_H), (27.0, 12.7, 65.1, 101.6))
        self.assertAlmostEqual(cs.JCARD_W, 104.8, places=1)          # 4 1/8 in unfolded


class Sides(unittest.TestCase):
    def test_sides_are_balanced_by_running_time_in_album_order(self):
        a, b = cs.split_sides(tracks([600, 100, 100, 100, 300, 300]))
        self.assertEqual([t["track"] for t in a], [1, 2])
        self.assertEqual([t["track"] for t in b], [3, 4, 5, 6])

    def test_the_shortest_tape_that_holds_the_longer_side(self):
        self.assertEqual(cs.tape_for(*cs.split_sides(tracks([600] * 4))), "C46")      # 20 min a side
        self.assertEqual(cs.tape_for(*cs.split_sides(tracks([600] * 6))), "C60")      # 30
        self.assertEqual(cs.tape_for(*cs.split_sides(tracks([600] * 8))), "C90")      # 40
        self.assertEqual(cs.tape_for(*cs.split_sides(tracks([0, 0]))), "")            # lengths unknown

    def test_one_track_is_all_side_a(self):
        self.assertEqual(cs.split_sides(tracks([300])), (tracks([300]), []))


class Render(unittest.TestCase):
    def test_two_pages_and_every_panel_where_it_prints(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            for paper in ("letter", "a4"):
                opts = jc.PrintOptions(paper=paper, crop_marks=False)
                pdf = Path(tmp) / f"{paper}.pdf"
                report = cs.render(pdf, artist="A", album="B", tracks=tracks([200] * 10), front=jc.Art(jpeg((600, 1000)), "scan"),
                                   back=jc.Colour("#00ff00"), inside=jc.Colour("#0000ff"), opts=opts)
                self.assertEqual(report.pages, 2)
                self.assertEqual([p.name for p in report.pieces], ["back flap", "front", "spine", "inside"])
                parts = cs.part_images(pdf, opts, px_per_mm=4)
                size = lambda part: Image.open(io.BytesIO(parts[part])).size
                for got, want in zip(size("outside"), (cs.JCARD_W * 4, cs.JCARD_H * 4)):
                    self.assertAlmostEqual(got, want, delta=1, msg="trim size, to the pixel")
                front = Image.open(io.BytesIO(parts["front"])).convert("RGB")
                self.assertTrue(near(front.getpixel((front.size[0] // 2, front.size[1] // 2)), (200, 30, 30)), f"{paper}: a portrait scan fills the front")
                inside = Image.open(io.BytesIO(parts["inside"])).convert("RGB")
                self.assertEqual(inside.getpixel((inside.size[0] - 3, 3)), (0, 0, 255), f"{paper}: the inside colour")
                spine = Image.open(io.BytesIO(parts["spine"])).convert("RGB")
                self.assertEqual(spine.getpixel((1, 2)), (0, 255, 0), f"{paper}: the spine colour")

    def test_a_square_cover_sits_on_top_with_the_title_below(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            opts = jc.PrintOptions(crop_marks=False)
            pdf = Path(tmp) / "j.pdf"
            cs.render(pdf, artist="A", album="B", front=jc.Art(jpeg(), "cover"), back=jc.Colour("#00ff00"), opts=opts)
            front = Image.open(io.BytesIO(cs.part_images(pdf, opts, px_per_mm=4)["front"])).convert("RGB")
            w, h = front.size
            self.assertTrue(near(front.getpixel((w // 2, int(h * 0.3))), (200, 30, 30)), "the cover at the top")
            self.assertEqual(front.getpixel((w - 3, h - 3)), (0, 255, 0), "the album colour under it")


if __name__ == "__main__":
    unittest.main()
