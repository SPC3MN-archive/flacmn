"""The search phase: what is shown before anything is tested, how a selection is parsed, and that no file is
touched until something has been selected."""

import unittest

from iaflac import preview as pv
from iaflac.model import FileInfo, ItemInfo
from iaflac.pipeline import Candidate, Options, Pipeline
from iaflac.queries import Want
from tests.helpers import HAVE_FFMPEG, HAVE_FLAC
from tests.test_pipeline import FakeIA, make_item


class ParseSelection(unittest.TestCase):
    def parse(self, text, n=5, **kw):
        return pv.parse_selection(text, n, **kw)

    def test_enter_takes_the_default(self):
        s = self.parse("")
        self.assertEqual((s.action, s.indexes), ("test", [0]))
        self.assertEqual(self.parse("", default="1-3").indexes, [0, 1, 2])

    def test_numbers_ranges_and_mixes(self):
        self.assertEqual(self.parse("2").indexes, [1])
        self.assertEqual(self.parse("1,3").indexes, [0, 2])
        self.assertEqual(self.parse("1 3").indexes, [0, 2])
        self.assertEqual(self.parse("2-4").indexes, [1, 2, 3])
        self.assertEqual(self.parse("1,3-5").indexes, [0, 2, 3, 4])
        self.assertEqual(self.parse(" 3 , 1 ,3 ").indexes, [0, 2], "sorted and de-duplicated")

    def test_all_skip_quit_and_case(self):
        self.assertEqual(self.parse("a").indexes, [0, 1, 2, 3, 4])
        self.assertEqual(self.parse("ALL").indexes, [0, 1, 2, 3, 4])
        for word in ("s", "skip", "n", "none"):
            self.assertEqual(self.parse(word).action, "skip")
        for word in ("q", "Quit"):
            self.assertEqual(self.parse(word).action, "quit")

    def test_d_means_the_default_for_this_and_every_remaining_request(self):
        s = self.parse("d")
        self.assertEqual((s.action, s.indexes, s.default_for_rest), ("test", [0], True))

    def test_bad_answers_are_rejected_with_a_reason(self):
        for text, why in (("0", "outside"), ("6", "outside"), ("1,9", "outside"), ("x", "not a number"), ("3-1", "backwards"),
                          ("1;2", "not a number")):
            with self.assertRaisesRegex(pv.SelectionError, why, msg=text):
                self.parse(text)

    def test_lenient_mode_for_the_select_flag_drops_out_of_range_numbers(self):
        s = self.parse("1,9", n=3, strict=False)
        self.assertEqual((s.action, s.indexes, s.dropped), ("test", [0], [9]))
        s = self.parse("7", n=3, strict=False)
        self.assertEqual((s.action, s.dropped), ("skip", [7]))
        with self.assertRaises(pv.SelectionError):
            self.parse("abc", n=3, strict=False)


class Formatting(unittest.TestCase):
    def item(self, **kw):
        files = [FileInfo(f"{i:02d} - Miles Davis - Song {i}.flac", format=kw.get("fmt", "Flac"), size=50_000_000, length=300.0)
                 for i in range(1, kw.get("tracks", 6) + 1)]
        files += [FileInfo("rip.log", format="Log", source="original", size=9000), FileInfo("a.cue", format="Cue Sheet", source="original", size=300),
                  FileInfo("cover.jpg", format="JPEG", source="original", size=200_000)]
        return ItemInfo(identifier="ident-1", title="Miles Davis - Kind of Blue (1959)", creator="Miles Davis", year=1959,
                        description=kw.get("description", ""), collections=["opensource_audio"], downloads=1234,
                        licenseurl=kw.get("licence", ""), files=files)

    def test_facts_come_from_the_records_alone(self):
        f = pv.facts(Candidate(item=self.item()), "Miles Davis")
        self.assertEqual((f["flac_files"], f["flac_mb"], f["runtime_min"], f["downloads"]), (6, 300, 30.0, 1234))
        self.assertEqual((f["rip_files"], f["cover"], f["format"]), (["log", "cue"], True, "FLAC"))
        self.assertEqual(f["tracks"], "Song 1 · Song 2 · Song 3 · Song 4 · Song 5 · Song 6", "artist prefix stripped from names")

    def test_long_track_lists_show_the_first_three_and_the_last(self):
        self.assertEqual(pv.facts(Candidate(item=self.item(tracks=12)), "Miles Davis")["tracks"],
                         "Song 1 · Song 2 · Song 3 · … · Song 12")

    def test_hires_label_is_archive_orgs_and_flagged_as_such(self):
        self.assertEqual(pv.format_label(self.item(fmt="24bit Flac")), "24-bit FLAC")

    def test_licence_is_shortened(self):
        self.assertEqual(pv.short_licence("https://creativecommons.org/licenses/by-nc-sa/3.0/"), "CC BY-NC-SA 3.0")
        self.assertEqual(pv.short_licence("https://creativecommons.org/publicdomain/zero/1.0/"), "public domain")
        self.assertEqual(pv.short_licence(""), "")

    def test_description_claims_are_reported_as_claims(self):
        text = pv.description_hints(self.item(description="Ripped with EAC from my CD. Not upsampled."))
        self.assertIn("mentions eac", text)
        self.assertNotIn("WARNING", text, "a negated 'not upsampled' is not a warning")
        self.assertIn("WARNING", pv.description_hints(self.item(description="converted from mp3")))

    def test_candidate_block_has_the_facts_and_no_verdict(self):
        cand = Candidate(item=self.item(description="EAC rip"))
        text = "\n".join(pv.candidate_lines(1, cand, "Miles Davis", width=100))
        for needle in ("Kind of Blue", "6 FLAC (FLAC)", "30.0 min", "300 MB", "1959", "1,234 downloads", "rip files: log, cue",
                       "cover art: yes", "https://archive.org/details/ident-1", "tracks: Song 1"):
            self.assertIn(needle, text)
        self.assertNotRegex(text, r"(?i)\btier\b|\bverified\b|\bpass\b|sample rate")

    def test_an_artist_prefix_shared_by_every_track_is_stripped_once(self):
        files = [FileInfo(f"Solange Plexus - Drum Charged - {i:02d} Song {i}.flac") for i in (1, 2, 3, 4)]
        tracks = pv.facts(Candidate(item=ItemInfo(identifier="x", files=files)), "Solange")["tracks"]
        self.assertEqual(tracks, "Song 1 · Song 2 · Song 3 · Song 4")

    def test_artist_only_requests_do_not_call_everything_an_exact_match(self):
        it = lambda title, creator="": ItemInfo(identifier="x", title=title, creator=creator, files=[FileInfo("a.flac")])
        self.assertEqual(pv.artist_label("Solange", it("A Seat at the Table", "Solange")), "creator is this artist")
        self.assertEqual(pv.artist_label("Solange", it("Drum Charged", "Solange Plexus")), "name is part of the creator")
        self.assertEqual(pv.artist_label("Solange", it("Carmen (Bizet) - Solange Michel")), "name only appears in the title")
        self.assertEqual(pv.artist_label("Solange", it("Something else entirely")), "weak match")

    def test_the_creator_is_shown_when_the_title_does_not_say_it(self):
        item = ItemInfo(identifier="x", title="Drum Charged", creator="Solange Plexus", files=[FileInfo("01 a.flac")])
        text = "\n".join(pv.candidate_lines(1, Candidate(item=item), "Solange", label="name is part of the creator", width=100))
        self.assertIn("by Solange Plexus", text)
        self.assertIn("name is part of the creator", text)
        item.title = "Solange Plexus - Drum Charged"
        self.assertNotIn("by Solange Plexus", "\n".join(pv.candidate_lines(1, Candidate(item=item), "Solange", width=100)))

    def test_long_track_names_end_in_an_ellipsis_so_they_do_not_look_complete(self):
        item = ItemInfo(identifier="x", files=[FileInfo("01 - " + "Very Long Live Version Title " * 4 + ".flac")])
        tracks = pv.facts(Candidate(item=item), "A")["tracks"]
        self.assertTrue(tracks.endswith("…") and len(tracks) <= 44, tracks)

    def test_long_titles_without_spaces_are_cut_not_blanked(self):
        cand = Candidate(item=ItemInfo(identifier="x", title="x" * 300, files=[FileInfo("01.flac")]))
        line = pv.candidate_lines(1, cand, width=80)[0]
        self.assertTrue(line.startswith("  1. xxxx") and len(line) <= 80 and line.endswith("…"), line)


@unittest.skipUnless(HAVE_FLAC and HAVE_FFMPEG, "flac and ffmpeg required")
class SearchPhaseTouchesNoFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = [
            make_item("ta-log", "Test Artist - Test Album", "Test Artist", tracks=6, rip=True, downloads=50, seed=1),
            make_item("ta-dup", "Test Artist - Test Album (FLAC)", "Test Artist", tracks=5, rip=False, downloads=900, seed=2),
            make_item("ta-live", "Test Artist - Test Album 1999-05-05 live", "Test Artist", tracks=6, rip=False,
                      extra={"venue": "The Roxy"}, seed=3),
            make_item("ta-second", "Test Artist - Second Record", "Test Artist", tracks=6, rip=True, downloads=10, seed=6, album_tag="Second Record"),
        ]

    def setUp(self):
        self.client = FakeIA(self.items)
        self.pipe = Pipeline(self.client)
        self.want = Want("Test Artist - Test Album", "Test Artist", "Test Album")

    def test_the_search_phase_reads_records_but_never_a_file(self):
        res = self.pipe.search_one(self.want, Options())
        self.assertGreaterEqual(len(res.found), 2)
        kinds = {c[0] for c in self.client.calls}
        self.assertEqual(kinds, {"search", "metadata"}, "no range read, no log read, no stream")
        self.assertEqual(self.client.bytes_read, 0)
        for cand in res.found:
            self.assertIsNone(cand.provenance)
            self.assertIsNone(cand.stats)
            self.assertEqual(cand.levels, ["V0"])

    def test_found_items_are_listed_in_preview_order_with_a_quick_match(self):
        res = self.pipe.search_one(self.want, Options())
        self.assertEqual(res.found[0].identifier, "ta-log", "rip-shaped copy first")
        self.assertTrue(all(c.match is not None for c in res.found))
        self.assertNotIn("ta-live", [c.identifier for c in res.found])
        self.assertTrue(any(k.startswith("live recording") for k in res.excluded))

    def test_the_preview_text_lists_duplicates_and_hidden_items(self):
        text = pv.format_find_preview(self.pipe.search_one(self.want, Options()), 1, 1)
        self.assertIn("[1/1] Test Artist — Test Album", text)
        self.assertIn("another upload or edition of #1", text)
        self.assertIn("hidden: 1 x live recording", text)
        self.assertIn("searched:", text)
        self.assertNotRegex(text, r"(?i)\btier\b")

    def test_nothing_found_shows_what_does_exist(self):
        res = self.pipe.search_one(Want("Test Artist - Missing Record", "Test Artist", "Missing Record"), Options())
        self.assertEqual(res.found, [])
        text = pv.format_find_preview(res, 1, 1)
        self.assertIn("(no FLAC match)", text)
        self.assertIn("did not match well enough", text)

    def test_only_the_selected_candidates_are_tested(self):
        res = self.pipe.search_one(self.want, Options())
        chosen = [res.found[0]]
        self.client.calls.clear()
        self.pipe.verify_selected(res, chosen, Options(deep="none"))
        touched = {c[1] for c in self.client.calls if c[0] == "range"}
        self.assertEqual(touched, {"ta-log"}, "only the chosen item's files were read")
        self.assertTrue(res.tested)
        self.assertEqual({c.identifier for c in res.untested}, {c.identifier for c in res.found[1:]})
        for cand in res.untested:
            self.assertIsNone(cand.provenance)

    def test_choosing_nothing_tests_nothing(self):
        res = self.pipe.search_one(self.want, Options())
        self.client.calls.clear()
        self.pipe.verify_selected(res, [], Options())
        self.assertEqual(self.client.calls, [])
        self.assertFalse(res.tested)
        self.assertEqual(res.candidates, [])
        self.assertEqual(len(res.untested), len(res.found))

    def test_deep_checks_only_touch_the_best_of_the_selected(self):
        res = self.pipe.search_one(self.want, Options())
        second = next(c for c in res.found if c.identifier == "ta-dup")
        self.client.calls.clear()
        self.pipe.verify_selected(res, [second], Options(deep="top", spectral=False))
        streamed = {c[1] for c in self.client.calls if c[0] == "stream"}
        self.assertEqual(streamed, {"ta-dup"})

    def test_find_one_still_searches_then_tests_everything_found(self):
        res = self.pipe.find_one(self.want, Options(deep="none"))
        self.assertEqual(res.candidates[0].identifier, "ta-log")
        self.assertEqual(len(res.candidates[0].alternates), 1)
        self.assertTrue(res.tested)

    def test_every_search_and_record_call_precedes_the_first_file_read(self):
        self.pipe.find_one(self.want, Options(deep="none"))
        kinds = [c[0] for c in self.client.calls]
        first_read = kinds.index("range")
        self.assertNotIn("search", kinds[first_read:])
        self.assertNotIn("metadata", kinds[first_read:])


if __name__ == "__main__":
    unittest.main()
