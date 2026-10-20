"""The static UI files: the animated loading logo must stay in step with the logo it replaces, and the redesign's
foundations (flacmn's Albums, Artists and Playlists next to Album Search and Case Art, the playing display, contrast, focus and the type scale) must hold."""

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


class Redesign(unittest.TestCase):
    def setUp(self):
        self.page = (STATIC / "index.html").read_text()
        self.css = (STATIC / "app.css").read_text()

    def test_flacmns_library_places_first_then_album_search_and_case_art(self):
        tabs = re.findall(r'class="tab[^"]*" data-view="(\w+)"', self.page)
        self.assertEqual(tabs, ["albums", "artists", "playlists", "search", "cdart"])
        self.assertNotIn('data-view="health"', self.page)
        self.assertNotIn('data-view="send"', self.page, "no Send to app here")
        self.assertIn('id="view-albums" aria-label="Albums"', self.page)
        self.assertNotIn('class="pill"', (STATIC / "js" / "library.js").read_text(), "no filtering by health, as in flacmn")

    def test_the_library_keeps_its_ways_to_the_internet_archive(self):
        health = (STATIC / "js" / "health.js").read_text()
        for word in ("/health-check/search", "/health-check/fetch", "/health-check/replace", "/covers/find"):
            self.assertIn(word, health)
        self.assertIn("openSearch", (STATIC / "js" / "library.js").read_text())

    def test_the_top_bar_has_the_playing_display_and_the_now_playing_card(self):
        self.assertIn('id="np-mini"', self.page)
        self.assertIn('id="np-pop"', self.page)

    def test_grey_text_passes_contrast_and_focus_shows(self):
        self.assertIn("--text-3: #6B6A70;", self.css)            # 5:1 on the panels; #77767D was 4.3:1
        self.assertRegex(self.css, r":focus-visible \{ outline: 2px solid var\(--primary\)")

    def test_text_sizes_come_from_the_scale(self):
        sizes = set(re.findall(r"font-size:\s*([^;]+);", self.css))
        loose = {s for s in sizes if not s.startswith("var(--fs-")}
        self.assertTrue(all(float(s.rstrip("px")) <= 10 for s in loose), f"sizes off the scale: {loose}")   # only tiny badges
