"""The static UI files: the animated loading logo must stay in step with the logo it replaces."""

import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "iaflac" / "gui" / "static"


class LoadingLogo(unittest.TestCase):
    def setUp(self):
        self.still = (STATIC / "logo.svg").read_text()
        self.moving = (STATIC / "logo-loading.svg").read_text()

    def test_it_draws_the_same_logo_in_the_same_box(self):
        attrs = lambda svg: re.search(r"<svg[^>]*>", svg).group(0)
        for name in ("width", "height", "viewBox"):
            pick = lambda svg: re.search(rf'{name}="([^"]+)"', attrs(svg)).group(1)
            self.assertEqual(pick(self.still), pick(self.moving), name)
        flac = lambda svg: re.search(r'd="(M80[^"]+)"', svg).group(1)
        self.assertEqual(flac(self.still), flac(self.moving), "the 'flac' outline")

    def test_every_dot_of_the_mn_is_animated(self):
        dots = len(re.findall(r"m\s*-?[\d.]+,-?[\d.]+\s*c", self.still))
        self.assertEqual(dots, 62)
        self.assertEqual(len(re.findall(r'<circle class="dot d\d+"', self.moving)), dots)

    def test_it_honours_reduced_motion_and_shows_where_results_load(self):
        self.assertIn("prefers-reduced-motion", self.moving)
        self.assertIn('src="/static/logo.svg"', (STATIC / "index.html").read_text(), "the top-bar logo stays static")
        for view in ("search.js", "cdart.js"):
            self.assertIn("/static/logo-loading.svg", (STATIC / "js" / view).read_text(), view)
