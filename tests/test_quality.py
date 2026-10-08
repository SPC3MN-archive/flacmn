import unittest
from collections import Counter

from iaflac import altmedia, matching, provenance as prov, quality
from iaflac.events import Cancelled
from iaflac.model import FileInfo, ItemInfo
from iaflac.pipeline import Candidate, Options, Pipeline
from iaflac.queries import Want
from tests.helpers import HAVE_FFMPEG, HAVE_FLAC
from tests.test_pipeline import FakeIA, make_alt_item, make_item


def item(ident, media="flac", files=(), downloads=0):
    it = ItemInfo(identifier=ident, title=f"Artist - Album {ident}", files=list(files), downloads=downloads)
    it.media = media
    return it


def flacs(n=10, log=False):
    out = [FileInfo(f"{i:02d}.flac", size=30_000_000, length=240) for i in range(1, n + 1)]
    return out + ([FileInfo("rip.log", size=4000)] if log else [])


def mp3s(n=10):
    return [FileInfo(f"{i:02d}.mp3", format="VBR MP3", source="derivative", size=8_000_000, length=240)
            for i in range(1, n + 1)]


def cand(it, tier=None, bits=16, rate=44100, kbps=0.0, mode="VBR", match_bucket=0):
    c = Candidate(item=it)
    score = 1.0 if match_bucket == 0 else 0.7
    c.match = matching.MatchResult(score=score, artist_score=score, album_score=score)
    if tier:
        c.provenance = prov.Provenance(tier, {"A": 60, "B": 30, "C": 10, "L": 0, "D": 0}[tier], [], [])
        if it.media == "flac":
            c.stats = prov.ProbeStats(n=1, ok=1, rates=Counter({rate: 1}), depths=Counter({bits: 1}))
        else:
            c.alt_stats = altmedia.AltStats(it.media, n=1, ok=1, kbps=kbps, mode=mode,
                                            flac_type=f"FLAC ({bits}-bit/{rate / 1000:.1f}kHz)")
    return c


class Classes(unittest.TestCase):
    def test_a_cd_rip_is_lossless_cd(self):
        q = quality.assess(cand(item("a", files=flacs(log=True)), "A"))
        self.assertEqual((q.rank, q.format, q.lossless, q.tested), (quality.CD_LOSSLESS, "FLAC 16/44.1", True, True))

    def test_hi_res_ranks_above_plain_16_44_lossless_of_unknown_source(self):
        hi = quality.assess(cand(item("h", files=flacs()), "C", bits=24, rate=96000))
        plain = quality.assess(cand(item("p", files=flacs()), "C"))
        self.assertEqual(hi.rank, quality.HIRES_LOSSLESS)
        self.assertEqual(hi.format, "FLAC 24/96")
        self.assertEqual(plain.rank, quality.LOSSLESS)

    def test_untested_items_are_classed_from_the_file_list(self):
        self.assertEqual(quality.assess(cand(item("r", files=flacs(log=True)))).rank, quality.CD_LOSSLESS)
        self.assertEqual(quality.assess(cand(item("f", files=flacs()))).rank, quality.LOSSLESS)
        untested = quality.assess(cand(item("m", "mp3", mp3s())))
        self.assertEqual((untested.rank, untested.tested, untested.format), (quality.MP3_HIGH, False, "MP3"))

    def test_mp3_bitrate_splits_high_from_low(self):
        v0 = quality.assess(cand(item("v0", "mp3", mp3s()), "L", kbps=245))
        low = quality.assess(cand(item("lo", "mp3", mp3s()), "L", kbps=128, mode="CBR"))
        self.assertEqual((v0.rank, v0.format), (quality.MP3_HIGH, "MP3 VBR ~245 kbps"))
        self.assertEqual((low.rank, low.format), (quality.MP3_LOW, "MP3 CBR ~128 kbps"))

    def test_failed_is_last_whatever_the_format(self):
        self.assertEqual(quality.assess(cand(item("d", files=flacs(log=True)), "D")).rank, quality.FAILED)

    def test_wav_reports_its_pcm_format(self):
        q = quality.assess(cand(item("w", "wav", [FileInfo("01.wav", size=50_000_000)]), "B"))
        self.assertEqual((q.rank, q.format), (quality.CD_LOSSLESS, "WAV 16/44.1"))


class Ordering(unittest.TestCase):
    def test_flac_wav_vbr_mp3_then_failed(self):
        cd = cand(item("cd", files=flacs(log=True)), "A")
        wav = cand(item("wav", "wav", [FileInfo("01.wav", size=1)]), "C")
        vbr = cand(item("vbr", "mp3", mp3s()), "L", kbps=245)
        cbr = cand(item("cbr", "mp3", mp3s()), "L", kbps=128, mode="CBR")
        bad = cand(item("bad", files=flacs()), "D")
        ranked = quality.rank([bad, cbr, vbr, wav, cd])
        self.assertEqual([c.identifier for c in ranked], ["cd", "wav", "vbr", "cbr", "bad"])

    def test_inside_a_class_popularity_breaks_ties(self):
        a = cand(item("a", files=flacs(), downloads=10), "C")
        b = cand(item("b", files=flacs(), downloads=900), "C")
        self.assertEqual([c.identifier for c in quality.rank([a, b])], ["b", "a"])


class Mp3Tracks(unittest.TestCase):
    def test_the_uploaders_own_mp3s_win_over_ias_vbr_derivatives(self):
        own = [FileInfo(f"{i:02d}.mp3", format="320Kbps MP3", source="original") for i in (1, 2)]
        it = item("m", "mp3", own + mp3s(2))
        self.assertEqual([f.format for f in it.flacs], ["320Kbps MP3", "320Kbps MP3"])

    def test_only_derivatives_means_the_vbr_files(self):
        self.assertEqual(len(item("m", "mp3", mp3s(3)).flacs), 3)


@unittest.skipUnless(HAVE_FLAC and HAVE_FFMPEG, "flac and ffmpeg required")
class AllFormats(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flac = make_item("flac-1", "Test Artist - Test Album", "Test Artist", tracks=4, rip=True, seed=3)
        cls.wav = make_alt_item("wav-1", "Test Artist - Test Album", "Test Artist", "wav", seed=1)
        cls.vbr = make_alt_item("vbr-1", "Test Artist - Test Album", "Test Artist", "mp3", seed=2)
        cls.want = Want("Test Artist - Test Album", "Test Artist", "Test Album")

    def test_flac_alone_hides_the_others_by_default(self):
        res = Pipeline(FakeIA([self.flac, self.wav, self.vbr])).search_one(self.want, Options(fallback=("wav", "vbr")))
        self.assertEqual([c.identifier for c in res.found], ["flac-1"])

    def test_all_formats_lists_every_upload_and_ranks_by_quality(self):
        events = []
        pipe = Pipeline(FakeIA([self.vbr, self.wav, self.flac]), progress=events.append)
        opts = Options(all_formats=True, deep="none", spectral=False)
        res = pipe.search_one(self.want, opts)
        self.assertEqual({c.identifier for c in res.found}, {"flac-1", "wav-1", "vbr-1"})
        self.assertFalse(any("no FLAC found" in n for n in res.notes))
        pipe.inspect_all(res.found, "t", self.want)
        self.assertEqual([c.identifier for c in quality.rank(res.found)], ["flac-1", "wav-1", "vbr-1"])
        stages = {e["stage"] for e in events}
        self.assertTrue({"search", "found", "inspected"} <= stages, stages)

    def test_cancel_stops_the_search(self):
        pipe = Pipeline(FakeIA([self.flac]), cancel=lambda: True)
        with self.assertRaises(Cancelled):
            pipe.search_one(self.want, Options(all_formats=True))


if __name__ == "__main__":
    unittest.main()
