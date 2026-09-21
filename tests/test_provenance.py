import copy
import unittest

from iaflac import provenance as prov
from iaflac.flac_probe import FlacProbe
from iaflac.model import FileInfo, ItemInfo
from tests.fixtures_loader import load

EAC_VERIFIED = """Exact Audio Copy V1.5 from 15. May 2019
Track quality 100.0 %
Test CRC 5E1B43A9
Copy CRC 5E1B43A9
Accurately ripped (confidence 45)  [B9A0C1FE]  (AR v2)
Copy OK
No errors occurred
All tracks accurately ripped
End of status report
"""

XLD_ZERO_COUNTS = """X Lossless Decoder version 20200308 (150.3)
Statistics
    Read error                           : 0
    Skipped (treated as error)           : 0
    Damaged sector count                 : 0
Track 01 : OK (v1+v2, confidence 12/12)
No errors occurred
"""

EAC_UNVERIFIED = """Exact Audio Copy V1.3 from 2. September 2016
Track quality 100.0 %
Track not present in AccurateRip database
Copy OK
No errors occurred
"""


def assess(key, **kw):
    item, probes, logs, aucdtect = load(key)
    return prov.assess(item, prov.probe_stats(probes), logs, aucdtect, **kw)


class CalibrationItems(unittest.TestCase):
    """Four real archive.org items whose true nature we established by hand."""

    def test_mars_volta_is_tier_a(self):
        pv = assess("mars_volta")
        self.assertEqual(pv.tier, "A", pv.reasons)
        self.assertGreaterEqual(pv.score, 90)
        text = " ".join(pv.reasons)
        for expected in ("EAC", "12/12 tracks", "cue sheet", ".accurip"):
            self.assertIn(expected, text)
        self.assertFalse(any("live recording" in r for r in pv.reasons), "release date is not a concert date")

    def test_final_fantasy_viii_multi_disc_is_tier_a(self):
        pv = assess("ff8_ost")
        self.assertEqual(pv.tier, "A", pv.reasons)
        self.assertIn("auCDtect", " ".join(pv.reasons))

    def test_hunting_party_is_lossless_but_not_cd(self):
        pv = assess("hunting_party")
        self.assertEqual(pv.tier, "C", pv.reasons)
        text = " ".join(pv.reasons)
        self.assertIn("not the CD format", text)
        self.assertIn("description says cd rip, but the files are 48 kHz", text)  # claim contradicted, no credit

    def test_netlabel_release_is_tier_c(self):
        self.assertEqual(assess("tanox_netlabel").tier, "C")

    def test_probe_stats_for_mars_volta(self):
        _, probes, _, _ = load("mars_volta")
        st = prov.probe_stats(probes)
        self.assertEqual((st.n, st.ok, st.aligned, st.cd_native), (12, 12, 12, 12))
        self.assertEqual(st.tag_artist, "The Mars Volta")
        self.assertEqual(st.tag_album, "Frances the Mute")
        self.assertEqual(st.format_label(), "44.1 kHz/16-bit/stereo")


class TierRules(unittest.TestCase):
    def test_aligned_cd_tracks_alone_are_tier_b(self):
        item, probes, _, _ = load("mars_volta")
        item.files = [f for f in item.files if f.name.endswith(".flac")]   # no log, cue, accurip
        item.description = ""
        pv = prov.assess(item, prov.probe_stats(probes), [], {"cdda": 0, "mpeg": 0, "mpeg_max": 0})
        self.assertEqual(pv.tier, "B", pv.reasons)

    def test_a_real_flac_named_like_an_mp3_rip_forces_tier_d(self):
        item, probes, logs, auc = load("mars_volta")
        probes = copy.deepcopy(probes)
        first = next(iter(probes))
        probes[first] = FlacProbe(kind="mp3", ok=False, detail="content looks like mp3, not FLAC")
        pv = prov.assess(item, prov.probe_stats(probes), logs, auc)
        self.assertEqual(pv.tier, "D")
        self.assertIn("1 of 12", pv.hard_fail[0])

    def test_integrity_failure_forces_tier_d(self):
        self.assertEqual(assess("mars_volta", integrity_ok=False).tier, "D")

    def test_lossy_spectrum_forces_tier_d_but_suspect_only_costs_points(self):
        self.assertEqual(assess("mars_volta", spectral_verdict="LIKELY_LOSSY").tier, "D")
        clean, suspect = assess("mars_volta", spectral_verdict="PASS"), assess("mars_volta", spectral_verdict="SUSPECT")
        self.assertEqual(clean.tier, "A")
        self.assertEqual(suspect.score, clean.score + prov.WEIGHTS["spectral_suspect"])

    def test_auCDtect_mpeg_verdict_forces_tier_d(self):
        item, probes, logs, _ = load("mars_volta")
        pv = prov.assess(item, prov.probe_stats(probes), logs, {"cdda": 3, "mpeg": 2, "mpeg_max": 97})
        self.assertEqual(pv.tier, "D")


class RipLogParsing(unittest.TestCase):
    def test_eac_verified(self):
        ev = prov.parse_rip_log("a.log", EAC_VERIFIED.encode())
        self.assertEqual((ev.ripper, ev.strength), ("EAC", "verified"))
        self.assertTrue(ev.all_accurate and ev.no_errors and not ev.has_errors)

    def test_xld_zero_error_counts_are_not_errors(self):
        ev = prov.parse_rip_log("a.log", XLD_ZERO_COUNTS.encode())
        self.assertEqual((ev.ripper, ev.has_errors), ("XLD", False))
        self.assertEqual(ev.strength, "verified")

    def test_nonzero_xld_error_counts_are_errors(self):
        ev = prov.parse_rip_log("a.log", XLD_ZERO_COUNTS.replace("Read error                           : 0",
                                                                 "Read error                           : 3")
                                .replace("No errors occurred", "").encode())
        self.assertTrue(ev.has_errors)

    def test_unverified_eac_rip_is_clean_not_verified(self):
        ev = prov.parse_rip_log("a.log", EAC_UNVERIFIED.encode())
        self.assertEqual(ev.strength, "clean")
        self.assertTrue(ev.not_in_db)

    def test_cannot_be_verified_as_accurate_is_not_counted(self):
        text = "Exact Audio Copy V1.5\nCannot be verified as accurate (confidence 1)  [AB12]\nThere were errors\n"
        ev = prov.parse_rip_log("a.log", text.encode())
        self.assertEqual(ev.accurate_tracks, 0)
        self.assertEqual(ev.strength, "errors")

    def test_utf16_log_with_bom(self):
        ev = prov.parse_rip_log("a.log", EAC_VERIFIED.encode("utf-16"))
        self.assertEqual(ev.ripper, "EAC")
        self.assertEqual(ev.strength, "verified")

    def test_log_from_an_unknown_tool_earns_little(self):
        ev = prov.parse_rip_log("convert.log", b"converted 12 files in 3.2 s\n")
        self.assertEqual(ev.strength, "unrecognised")

    def test_aucdtect_parsing(self):
        text = "FILE: 01.flac\n  Conclusion: CDDA 100%\nFILE: 02.flac\n  Conclusion: MPEG 96%\n"
        self.assertEqual(prov.parse_aucdtect(text.encode()), {"cdda": 1, "mpeg": 1, "mpeg_max": 96})


class Claims(unittest.TestCase):
    def item(self, description, title="Album"):
        return ItemInfo(identifier="x", title=title, description=description)

    def test_negated_fake_terms_are_not_flagged(self):
        self.assertEqual(prov.scan_claims(self.item("Lossless. No transcodes, no upsampling."))["fake"], [])

    def test_real_fake_terms_are_flagged(self):
        self.assertIn("converted from mp3", prov.scan_claims(self.item("FLAC converted from mp3 files"))["fake"])

    def test_cd_and_other_sources(self):
        c = prov.scan_claims(self.item("Vinyl rip, 24-bit. Ripped with EAC"))
        self.assertIn("vinyl", c["other"])
        self.assertIn("eac", c["cd"])

    def test_amazon_link_alone_is_not_a_web_source(self):
        self.assertEqual(prov.scan_claims(self.item("Buy the CD at Amazon"))["other"], [])


class LiveDetection(unittest.TestCase):
    def test_release_date_next_to_a_barcode_is_not_live(self):
        item = ItemInfo(identifier="x", title="The Mars Volta - 2005 - Frances the Mute (075021039773 - 2005-02-21)")
        self.assertFalse(item.is_live)

    def test_concert_date_or_venue_is_live(self):
        self.assertTrue(ItemInfo(identifier="x", title="Led Zeppelin 1973-01-23 Southampton").is_live)
        self.assertTrue(ItemInfo(identifier="x", title="Something", extra={"venue": "The Fillmore"}).is_live)
        self.assertTrue(ItemInfo(identifier="x", title="Something", collections=["etree"]).is_live)

    def test_live_word_placeholder_dates_and_radio_sessions_are_live(self):
        for title in ("Swirlies Live - 1992-0x-xx WERS Studios, Boston", "Slowdive - Peel Session",
                      "Some Band - Live at the Marquee", "Band 1994.05.06 SBD"):
            self.assertTrue(ItemInfo(identifier="x", title=title).is_live, title)

    def test_studio_album_is_not_live(self):
        self.assertFalse(ItemInfo(identifier="x", title="Miles Davis - Kind of Blue (1959)").is_live)


class ItemParsing(unittest.TestCase):
    def test_length_formats(self):
        from iaflac.model import parse_length
        self.assertEqual(parse_length("170.25"), 170.25)
        self.assertEqual(parse_length("3:12"), 192.0)
        self.assertEqual(parse_length("1:02:03"), 3723.0)
        self.assertIsNone(parse_length("n/a"))

    def test_only_original_sidecars_and_all_flacs(self):
        item = ItemInfo(identifier="x", files=[
            FileInfo("d/01.flac", source="derivative"), FileInfo("d/02.flac", source="original"),
            FileInfo("a.log", source="original"), FileInfo("b.log", source="derivative")])
        self.assertEqual(len(item.flacs), 2)
        self.assertEqual([f.name for f in item.files_of("log")], ["a.log"])


if __name__ == "__main__":
    unittest.main()
