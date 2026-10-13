import unittest
from types import SimpleNamespace

from iaflac import genre as g


class Resolve(unittest.TestCase):
    def test_known_genre_expands_and_keeps_its_own_name_first(self):
        terms, exclude = g.resolve("shoegaze")
        self.assertEqual(terms[0], "shoegaze")
        self.assertIn("dream pop", terms)
        self.assertEqual(exclude, [])

    def test_exclusions_come_from_the_file(self):
        _, exclude = g.resolve("ambient")
        self.assertIn("asmr", exclude)

    def test_lookup_ignores_case_and_hyphens(self):
        self.assertIn("coldwave", g.resolve("Post Punk")[0])
        self.assertIn("coldwave", g.resolve("post-punk")[0])

    def test_unknown_genre_searches_the_phrase_itself(self):
        self.assertEqual(g.resolve("math rock", genres={})[0], ["math rock"])

    def test_user_terms_are_added_without_duplicates(self):
        terms, exclude = g.resolve("shoegaze", ["Dream Pop", "nu-gaze"], ["karaoke"])
        self.assertEqual(terms.count("dream pop"), 1)
        self.assertIn("nu-gaze", terms)
        self.assertEqual(exclude, ["karaoke"])

    def test_split_csv(self):
        self.assertEqual(g.split_csv(" a, b ,,c "), ["a", "b", "c"])
        self.assertEqual(g.split_csv(None), [])

    def test_every_entry_in_genres_yml_has_terms(self):
        genres = g.load_genres()
        self.assertGreater(len(genres), 20)
        for name, entry in genres.items():
            self.assertTrue(entry["terms"], name)


class Confidence(unittest.TestCase):
    terms = ["shoegaze", "dream pop"]

    def test_subject_beats_title_beats_description(self):
        self.assertEqual(g.confidence({"subject": ["Shoegaze; noise"], "title": "x"}, self.terms), "subject")
        self.assertEqual(g.confidence({"subject": "rock", "title": "Dream Pop Hits"}, self.terms), "title")
        self.assertEqual(g.confidence({"subject": "rock", "title": "x"}, self.terms), "description")

    def test_pre_score_prefers_a_subject_match_and_no_source(self):
        best = g.GenreHit({"downloads": 10}, "", True, "subject").pre_score()
        self.assertEqual(best, g.GenreHit({"downloads": 10}, "", False, "subject").pre_score())   # rip log or not
        self.assertGreater(best, g.GenreHit({"downloads": 10}, "", True, "title").pre_score())

    def test_matched_subjects(self):
        self.assertEqual(g.matched_subjects(["Shoegaze", "rock", "Dream Pop"], self.terms), ["Shoegaze", "Dream Pop"])


class Diversify(unittest.TestCase):
    def cand(self, ident, artist):
        return SimpleNamespace(identifier=ident, artist_album=(artist, "x"))

    def test_caps_each_artist_and_keeps_order(self):
        cands = [self.cand("1", "Alpha"), self.cand("2", "The Alpha"), self.cand("3", "Alpha"),
                 self.cand("4", "Beta"), self.cand("5", "")]
        out = g.diversify(cands, 2)
        self.assertEqual([c.identifier for c in out], ["1", "2", "4", "5"])

    def test_unknown_artists_are_not_lumped_together(self):
        out = g.diversify([self.cand("1", ""), self.cand("2", ""), self.cand("3", "")], 1)
        self.assertEqual(len(out), 3)


if __name__ == "__main__":
    unittest.main()
