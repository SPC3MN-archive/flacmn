"""Smoke tests against the real archive.org. Off by default:

    IAFLAC_LIVE=1 python3 -m unittest tests.test_live -v

They rely on four items whose nature was established by hand (see tests/record_fixtures.py); if one of them
is removed from the archive the corresponding test will say so rather than pass silently.
"""

import os
import unittest

from iaflac.genre import GenreSpec
from iaflac.ia_client import IAClient
from iaflac.pipeline import Options, Pipeline
from iaflac.queries import Want

LIVE = os.environ.get("IAFLAC_LIVE") == "1"
MARS_VOLTA = "the-mars-volta-2005-frances-the-mute-075021039773-2005-02-21"


@unittest.skipUnless(LIVE, "set IAFLAC_LIVE=1 to talk to archive.org")
class LiveSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipe = Pipeline(IAClient())

    def test_verify_a_known_cd_rip(self):
        cand = self.pipe.inspect(self.pipe.load(MARS_VOLTA))
        self.assertEqual(cand.tier, "A", cand.provenance.reasons)
        self.assertGreaterEqual(cand.stats.ok, 10)                  # the first pass samples up to 10 tracks
        self.assertEqual(cand.stats.aligned, cand.stats.ok)         # every sampled track is a whole number of CD sectors
        self.assertIn("V1", cand.levels)

    def test_verify_a_lossless_but_not_cd_item(self):
        cand = self.pipe.inspect(self.pipe.load("thehuntingpartflac"))
        self.assertEqual(cand.tier, "C", cand.provenance.reasons)
        self.assertEqual(cand.stats.rates.most_common(1)[0][0], 48000)

    def test_find_by_name_puts_the_verified_rip_first(self):
        res = self.pipe.find_one(Want("The Mars Volta - Frances the Mute", "The Mars Volta", "Frances the Mute"),
                                 Options(deep="none"))
        self.assertTrue(res.candidates, res.notes)
        self.assertEqual(res.candidates[0].identifier, MARS_VOLTA)
        self.assertEqual(res.candidates[0].tier, "A")

    def test_genre_search_returns_tiered_albums(self):
        spec = GenreSpec(genre="shoegaze", terms=["shoegaze", "dream pop"], top=5, probe_n=8)
        res = self.pipe.genre(spec, Options(deep="none"))
        self.assertGreaterEqual(len(res.candidates), 3, res.notes)
        for cand in res.candidates:
            self.assertIn(cand.tier, "ABCD")
            self.assertGreaterEqual(len(cand.item.flacs), spec.min_tracks - 1)


if __name__ == "__main__":
    unittest.main()
