"""`find` and `genre` show the search results first and test only what was chosen, through the real CLI handlers."""

import contextlib
import io
import os
import pathlib
import tempfile
import unittest
from argparse import Namespace
from unittest import mock

from iaflac import cli
from tests.helpers import HAVE_FFMPEG, HAVE_FLAC
from tests.test_pipeline import FakeIA, make_item


def run_cli(func, args, client, answers=(), interactive=False):
    out, err, asked = io.StringIO(), io.StringIO(), []
    pending = iter(answers)

    def fake_ask(prompt):
        asked.append({"prompt": prompt, "shown": out.getvalue(),
                      "file_calls": [c for c in client.calls if c[0] in ("range", "stream")]})
        try:
            return next(pending)
        except StopIteration:
            raise EOFError

    with mock.patch.object(cli, "is_interactive", return_value=interactive), mock.patch.object(cli, "ask", fake_ask), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = func(args, client)
    return code, out.getvalue(), err.getvalue(), asked


def file_reads(client):
    return [c for c in client.calls if c[0] in ("range", "stream")]


@unittest.skipUnless(HAVE_FLAC and HAVE_FFMPEG, "flac and ffmpeg required")
class FindFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = [
            make_item("ta-log", "Test Artist - Test Album", "Test Artist", tracks=6, rip=True, downloads=50, seed=1),
            make_item("ta-dup", "Test Artist - Test Album (FLAC)", "Test Artist", tracks=5, rip=False, downloads=900, seed=2),
            make_item("ta-second", "Test Artist - Second Record", "Test Artist", tracks=6, rip=True, downloads=10, seed=6, album_tag="Second Record"),
            make_item("zz-log", "Zed Zed - Other Album", "Zed Zed", tracks=6, rip=True, downloads=5, seed=7, album_tag="Other Album"),
        ]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = self.tmp.name
        self.client = FakeIA(self.items)

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, *items, **kw):
        base = dict(items=list(items or ["Test Artist - Test Album"]), file=None, top=3, include_live=False, free_only=False,
                    deep="none", no_spectral=True, max_deep_mb=1500, out=self.out, rip_only=False, max_candidates=10,
                    search_only=False, select=None)
        return Namespace(**{**base, **kw})

    def reports(self):
        return sorted(os.listdir(self.out))

    # ---- nothing is tested unless asked ----------------------------------------------------------

    def test_without_a_terminal_it_lists_and_stops(self):
        code, out, err, _ = run_cli(cli.cmd_find, self.args(), self.client, interactive=False)
        self.assertEqual(code, 0)
        self.assertIn("Nothing has been tested yet", out)
        self.assertIn("ta-log", out)
        self.assertIn("Nothing was tested.", out)
        self.assertIn("not an interactive terminal", out)
        self.assertEqual(file_reads(self.client), [], "no file was opened")
        self.assertEqual(self.reports(), [], "no run folder was created")

    def test_search_only_flag_lists_and_stops_even_in_a_terminal(self):
        code, out, _, asked = run_cli(cli.cmd_find, self.args(search_only=True), self.client, interactive=True)
        self.assertEqual(code, 0)
        self.assertEqual(asked, [], "it must not prompt")
        self.assertNotIn("not an interactive terminal", out)
        self.assertEqual(file_reads(self.client), [])

    def test_the_listing_carries_the_facts_but_no_verdict(self):
        _, out, _, _ = run_cli(cli.cmd_find, self.args(search_only=True), self.client)
        for needle in ("Test Artist — Test Album", "exact match", "6 FLAC (FLAC)", "rip files: log, cue", "tracks: Track 1",
                       "https://archive.org/details/ta-log", "another upload or edition of #1"):
            self.assertIn(needle, out)
        self.assertNotRegex(out, r"(?i)\btier [abcd]\b")

    def test_select_tests_exactly_what_was_named(self):
        code, out, _, _ = run_cli(cli.cmd_find, self.args(select="1"), self.client)
        self.assertEqual(code, 0)
        touched = {c[1] for c in file_reads(self.client)}
        self.assertEqual(touched, {"ta-log"})
        self.assertRegex(out, r"\bA\b.*ta-log")
        self.assertEqual(len(self.reports()), 1)
        report = pathlib.Path(self.out, self.reports()[0], "report.md").read_text(encoding="utf-8")
        self.assertIn("Found but not tested", report)

    def test_select_a_tests_everything_listed(self):
        run_cli(cli.cmd_find, self.args(select="a"), self.client)
        self.assertEqual({c[1] for c in file_reads(self.client)}, {"ta-log", "ta-dup"})

    def test_select_out_of_range_is_ignored_with_a_warning(self):
        code, _, err, _ = run_cli(cli.cmd_find, self.args(select="1,9"), self.client)
        self.assertEqual(code, 0)
        self.assertIn("[9] is outside 1-", err)

    def test_contradictory_or_malformed_flags_are_errors(self):
        for kw in (dict(search_only=True, select="1"), dict(select="abc"), dict(select="3-1")):
            code, _, err, _ = run_cli(cli.cmd_find, self.args(**kw), self.client)
            self.assertEqual(code, 2, kw)
            self.assertEqual(file_reads(self.client), [])

    # ---- the prompt ------------------------------------------------------------------------------

    def test_the_prompt_appears_after_the_results_and_before_any_file_is_read(self):
        code, out, _, asked = run_cli(cli.cmd_find, self.args(), self.client, answers=["s"], interactive=True)
        self.assertEqual(code, 0)
        self.assertEqual(len(asked), 1)
        self.assertIn("Test Artist - Test Album", asked[0]["shown"], "the results were already on screen")
        self.assertIn("https://archive.org/details/ta-log", asked[0]["shown"])
        self.assertEqual(asked[0]["file_calls"], [], "no file had been touched when it asked")
        self.assertIn("Test which?", asked[0]["prompt"])

    def test_enter_tests_the_top_listed_item_only(self):
        run_cli(cli.cmd_find, self.args(), self.client, answers=[""], interactive=True)
        self.assertEqual({c[1] for c in file_reads(self.client)}, {"ta-log"})

    def test_picking_the_second_listed_item(self):
        run_cli(cli.cmd_find, self.args(), self.client, answers=["2"], interactive=True)
        self.assertEqual({c[1] for c in file_reads(self.client)}, {"ta-dup"})

    def test_a_bad_answer_is_explained_and_asked_again(self):
        code, out, _, asked = run_cli(cli.cmd_find, self.args(), self.client, answers=["x", "9", "1"], interactive=True)
        self.assertEqual(code, 0)
        self.assertEqual(len(asked), 3)
        self.assertIn("'x' is not a number", out)
        self.assertIn("outside 1-", out)
        self.assertEqual({c[1] for c in file_reads(self.client)}, {"ta-log"})

    def test_skip_tests_nothing(self):
        code, out, _, _ = run_cli(cli.cmd_find, self.args(), self.client, answers=["s"], interactive=True)
        self.assertEqual(code, 0)
        self.assertIn("Nothing was selected, so nothing was tested", out)
        self.assertEqual(file_reads(self.client), [])
        self.assertEqual(self.reports(), [])

    def test_quit_and_end_of_input_test_nothing(self):
        for answers in (["q"], []):
            client = FakeIA(self.items)
            code, out, _, _ = run_cli(cli.cmd_find, self.args(), client, answers=answers, interactive=True)
            self.assertEqual(code, 0)
            self.assertIn("Nothing was tested", out)
            self.assertEqual(file_reads(client), [])

    def test_every_request_is_searched_and_shown_before_any_testing_starts(self):
        args = self.args("Test Artist - Test Album", "Zed Zed - Other Album")
        _, out, _, asked = run_cli(cli.cmd_find, args, self.client, answers=["1", "1"], interactive=True)
        self.assertEqual(len(asked), 2)
        self.assertEqual(asked[1]["file_calls"], [], "the second prompt still came before any testing")
        self.assertEqual({c[1] for c in file_reads(self.client)}, {"ta-log", "zz-log"})
        kinds = [c[0] for c in self.client.calls]
        first_read = min(i for i, k in enumerate(kinds) if k in ("range", "stream"))
        self.assertNotIn("search", kinds[first_read:])
        self.assertNotIn("metadata", kinds[first_read:])

    def test_d_uses_the_default_for_all_remaining_requests(self):
        args = self.args("Test Artist - Test Album", "Zed Zed - Other Album")
        _, _, _, asked = run_cli(cli.cmd_find, args, self.client, answers=["d"], interactive=True)
        self.assertEqual(len(asked), 1, "asked once, then it stopped asking")
        self.assertEqual({c[1] for c in file_reads(self.client)}, {"ta-log", "zz-log"})

    def test_skipping_one_request_still_tests_the_others_and_says_so(self):
        args = self.args("Test Artist - Test Album", "Zed Zed - Other Album")
        _, out, _, _ = run_cli(cli.cmd_find, args, self.client, answers=["s", "1"], interactive=True)
        self.assertIn("not tested (skipped)", out)
        self.assertEqual({c[1] for c in file_reads(self.client)}, {"zz-log"})

    def test_a_request_with_no_match_is_not_prompted_for(self):
        _, out, _, asked = run_cli(cli.cmd_find, self.args("Test Artist - Nonexistent Thing"), self.client, answers=["1"], interactive=True)
        self.assertEqual(asked, [])
        self.assertIn("(no FLAC match)", out)

    def test_the_deep_stream_only_starts_after_the_selection(self):
        run_cli(cli.cmd_find, self.args(deep="top", no_spectral=True, select="2"), self.client)
        streamed = [c[1] for c in self.client.calls if c[0] == "stream"]
        self.assertTrue(streamed and set(streamed) == {"ta-dup"}, streamed)


@unittest.skipUnless(HAVE_FLAC and HAVE_FFMPEG, "flac and ffmpeg required")
class GenreFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sg = ("shoegaze", "dream pop")
        cls.items = [make_item(f"g{i}", f"Artist{i} - Record {i}", f"Artist{i}", subjects=sg, downloads=1000 - i * 100, seed=i,
                               album_tag=f"Record {i}") for i in range(1, 5)]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = FakeIA(self.items)

    def tearDown(self):
        self.tmp.cleanup()

    def args(self, **kw):
        base = dict(genre="shoegaze", terms=None, exclude=None, years=None, any_flac=False, min_tracks=5, per_artist=2, top=25,
                    probe=30, collections=None, include_live=False, free_only=False, deep="none", no_spectral=True,
                    max_deep_mb=1500, out=self.tmp.name, search_only=False, select=None)
        return Namespace(**{**base, **kw})

    def test_lists_a_table_and_stops_without_a_terminal(self):
        code, out, _, _ = run_cli(cli.cmd_genre, self.args(), self.client)
        self.assertEqual(code, 0)
        self.assertIn("Nothing has been tested yet", out)
        self.assertIn("genre match", out)
        for i in range(1, 5):
            self.assertIn(f"[g{i}]", out)
        self.assertIn("log+cue", out)
        self.assertIn("Nothing was tested.", out)
        self.assertEqual(file_reads(self.client), [])
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_the_prompt_shows_the_default_and_comes_before_any_file_read(self):
        _, out, _, asked = run_cli(cli.cmd_genre, self.args(), self.client, answers=["q"], interactive=True)
        self.assertEqual(len(asked), 1)
        self.assertIn("[g1]", asked[0]["shown"])
        self.assertEqual(asked[0]["file_calls"], [])
        self.assertIn("Enter = 1-4", asked[0]["prompt"])
        self.assertEqual(file_reads(self.client), [])

    def test_enter_inspects_the_default_batch_and_reports_tiers(self):
        code, out, _, _ = run_cli(cli.cmd_genre, self.args(), self.client, answers=[""], interactive=True)
        self.assertEqual(code, 0)
        self.assertEqual({c[1] for c in file_reads(self.client)}, {"g1", "g2", "g3", "g4"})
        self.assertRegex(out, r"\bA\b")
        self.assertEqual(len(os.listdir(self.tmp.name)), 1)

    def test_a_chosen_subset_is_all_that_is_inspected(self):
        _, _, err, _ = run_cli(cli.cmd_genre, self.args(select="2-3"), self.client)
        self.assertEqual({c[1] for c in file_reads(self.client)}, {"g2", "g3"})
        self.assertIn("2 more candidate(s) were listed but not inspected", err)

    def test_bad_flags(self):
        self.assertEqual(run_cli(cli.cmd_genre, self.args(select="zzz"), self.client)[0], 2)
        self.assertEqual(run_cli(cli.cmd_genre, self.args(search_only=True, select="1"), self.client)[0], 2)


if __name__ == "__main__":
    unittest.main()
