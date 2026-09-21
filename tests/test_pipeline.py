"""Orchestration tests against a fake archive that serves real (tiny) FLACs built with the local flac."""

import copy
import hashlib
import re
import unittest

from iaflac import queries
from iaflac.genre import GenreSpec
from iaflac.pipeline import Options, Pipeline
from iaflac.queries import Want
from tests.helpers import HAVE_FFMPEG, HAVE_FLAC, encode_flac, ffmpeg_convert, tone_wav

EAC_LOG = (b"Exact Audio Copy V1.5 from 15. May 2019\nTrack quality 100.0 %\n"
           b"Accurately ripped (confidence 45)\nCopy OK\nNo errors occurred\nAll tracks accurately ripped\n")
SAMPLES = 588 * 100        # 1.33 s, a whole number of CD sectors


class FakeItem:
    def __init__(self, doc, metadata, files):
        self.doc, self.metadata, self.files = doc, metadata, files

    @property
    def rip_shaped(self):
        return any(f["format"] in ("Log", "Cue Sheet") for f in self.metadata["files"])


def make_item(identifier, title, creator, tracks=6, rip=True, subjects=(), downloads=100, extra=None,
              mp3=False, seed=0, artist_tag=None, album_tag="Test Album"):
    files, meta_files = {}, []

    def add(name, data, fmt):
        files[name] = data
        meta_files.append({"name": name, "format": fmt, "source": "original", "size": str(len(data)),
                           "length": None,   # IA sometimes omits it; a 1.3 s "track" would make every album look too short
                           "md5": hashlib.md5(data).hexdigest(), "sha1": hashlib.sha1(data).hexdigest()})

    for i in range(tracks):
        wav = tone_wav(SAMPLES, seed=seed * 100 + i)
        if mp3:
            data = ffmpeg_convert(wav, "wav", ["-c:a", "libmp3lame", "-b:a", "128k"], "mp3")
        else:
            data = encode_flac(wav, f"--tag=ARTIST={artist_tag or creator}", f"--tag=ALBUM={album_tag}")
        add(f"{i + 1:02d} Track {i + 1}.flac", data, "Flac")
    if rip:
        add("rip.log", EAC_LOG, "Log")
        add("album.cue", b'FILE "01.flac" WAVE\n', "Cue Sheet")
    metadata = {"identifier": identifier, "title": title, "creator": creator, "date": "1999", "description": "",
                "collection": ["opensource_audio"], "subject": list(subjects), **(extra or {})}
    doc = {"identifier": identifier, "title": title, "creator": creator, "downloads": downloads,
           "subject": list(subjects)}
    return FakeItem(doc, {"metadata": metadata, "files": meta_files, "item_size": sum(map(len, files.values()))}, files)


def make_alt_item(identifier, title, creator, media="wav", tracks=4, seed=0, samples=SAMPLES, description="", downloads=100):
    """An item with no FLAC: WAV originals (plus IA's derived VBR MP3s, as IA makes them) or VBR MP3 files only."""
    files, meta_files = {}, []

    def add(name, data, fmt, source="original"):
        files[name] = data
        meta_files.append({"name": name, "format": fmt, "source": source, "size": str(len(data)), "length": None,
                           "md5": hashlib.md5(data).hexdigest(), "sha1": hashlib.sha1(data).hexdigest()})

    for i in range(tracks):
        wav = tone_wav(samples, seed=seed * 100 + i)
        if media == "wav":
            add(f"{i + 1:02d} Track {i + 1}.wav", wav, "WAVE")
            add(f"{i + 1:02d} Track {i + 1}.mp3", ffmpeg_convert(wav, "wav", ["-c:a", "libmp3lame", "-q:a", "2"], "mp3"), "VBR MP3", "derivative")
        else:
            add(f"{i + 1:02d} Track {i + 1}.mp3", ffmpeg_convert(wav, "wav", ["-c:a", "libmp3lame", "-q:a", "2"], "mp3"), "VBR MP3", "derivative")
    metadata = {"identifier": identifier, "title": title, "creator": creator, "date": "1999", "description": description,
                "collection": ["opensource_audio"], "subject": []}
    doc = {"identifier": identifier, "title": title, "creator": creator, "downloads": downloads, "subject": []}
    return FakeItem(doc, {"metadata": metadata, "files": meta_files, "item_size": sum(map(len, files.values()))}, files)


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def iter_content(self, size):
        return (self.data[i:i + size] for i in range(0, len(self.data), size))

    def close(self):
        pass


class FakeIA:
    """Just enough of IAClient, with a naive emulation of the Lucene queries we send."""
    max_concurrency = 2

    def __init__(self, items, corrupt_stream=None):
        self.items = {i.doc["identifier"]: i for i in items}
        self.corrupt_stream = corrupt_stream or {}
        self.queries, self.bytes_read = [], 0
        self.calls = []          # (kind, ...) in the order they happened: search / metadata / range / stream

    def stats(self):
        return "fake"

    @staticmethod
    def phrases(q):
        return [p.casefold() for p in re.findall(r'"([^"]+)"', q.replace('format:"Cue Sheet"', "").replace('format:"VBR MP3"', ""))]

    def search_iter(self, q, *, fields=None, sort=None, max_hits=100, page_size=100):
        self.queries.append(q)
        self.calls.append(("search",))
        want_rip = queries.RIP_SHAPED in q
        phrases = self.phrases(q)
        count = 0
        for item in self.items.values():
            doc = item.doc
            if want_rip and not item.rip_shaped:
                continue
            have = {f["format"] for f in item.metadata["files"]}
            if any(clause in q and fmt not in have for clause, fmt in
                   (("format:Flac", "Flac"), ("format:WAVE", "WAVE"), ('format:"VBR MP3"', "VBR MP3"))):
                continue
            title_creator = f"{doc['title']} {doc['creator']}".casefold()
            if "subject:(" in q:                      # genre query: any phrase in subject or title
                hay = f"{' '.join(doc['subject'])} {doc['title']}".casefold()
                ok = any(p in hay for p in phrases)
            elif "description:(" in q and "title:" not in q:
                ok = False
            else:                                     # album / artist query: every phrase in title+creator
                ok = all(p in title_creator for p in phrases)
            if ok:
                yield doc
                count += 1
                if count >= max_hits:
                    return

    def metadata(self, identifier):
        self.calls.append(("metadata", identifier))
        return copy.deepcopy(self.items[identifier].metadata)

    def range_get(self, identifier, name, start, length):
        self.calls.append(("range", identifier, name))
        return self.items[identifier].files[name][start:start + length]

    def get_bytes(self, identifier, name, max_bytes=1 << 20):
        return self.range_get(identifier, name, 0, max_bytes)

    def open_stream(self, identifier, name, start=0):
        self.calls.append(("stream", identifier, name))
        data = self.items[identifier].files[name]
        if self.corrupt_stream.get(identifier) == name:
            damaged = bytearray(data)
            damaged[len(damaged) // 2] ^= 0xFF
            data = bytes(damaged)
        return FakeResponse(data)


@unittest.skipUnless(HAVE_FLAC and HAVE_FFMPEG, "flac and ffmpeg required")
class FindFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = [
            make_item("ta-log", "Test Artist - Test Album", "Test Artist", tracks=6, rip=True, downloads=50, seed=1),
            make_item("ta-dup", "Test Artist - Test Album (FLAC)", "Test Artist", tracks=5, rip=False, downloads=900, seed=2),
            make_item("ta-live", "Test Artist - Test Album 1999-05-05 live", "Test Artist", tracks=6, rip=False,
                      extra={"venue": "The Roxy"}, seed=3),
            make_item("ta-fake", "Test Artist - Test Album [FLAC]", "Test Artist", tracks=6, rip=False, mp3=True, seed=4),
            make_item("other-1", "Someone Else - Another Record", "Someone Else", tracks=6, rip=True, seed=5),
            make_item("ta-second", "Test Artist - Second Record", "Test Artist", tracks=6, rip=True, downloads=10,
                      seed=6, album_tag="Second Record"),
        ]

    def pipe(self, **kw):
        return Pipeline(FakeIA(self.items, **kw))

    def find(self, want=Want("Test Artist - Test Album", "Test Artist", "Test Album"), **opts):
        opts.setdefault("deep", "none")
        return self.pipe().find_one(want, Options(**opts))

    def test_best_copy_first_and_duplicates_become_alternates(self):
        res = self.find()
        self.assertEqual([c.identifier for c in res.candidates], ["ta-log"])
        best = res.candidates[0]
        self.assertEqual(best.tier, "A", best.provenance.reasons)
        alts = {a["identifier"]: a["tier"] for a in best.alternates}
        self.assertEqual(set(alts), {"ta-dup", "ta-fake"})
        self.assertEqual(alts["ta-fake"], "D", "an MP3 named .flac must be flagged, not silently listed as good")
        self.assertNotEqual(alts["ta-dup"], "D")

    def test_ladder_tries_the_rip_shaped_query_first(self):
        pipe = self.pipe()
        pipe.find_one(Want("x", "Test Artist", "Test Album"), Options(deep="none"))
        self.assertIn(queries.RIP_SHAPED, pipe.client.queries[0])
        self.assertNotIn(queries.RIP_SHAPED, pipe.client.queries[1])

    def test_live_tapes_are_excluded_unless_asked_for(self):
        res = self.find()
        self.assertTrue(any(k.startswith("live recording") for k in res.excluded))
        self.assertNotIn("ta-live", [a["identifier"] for a in res.candidates[0].alternates])
        res = self.find(include_live=True)
        self.assertIn("ta-live", [a["identifier"] for a in res.candidates[0].alternates])

    def test_asking_for_a_live_album_by_name_keeps_live_items(self):
        res = self.find(want=Want("Test Artist - Test Album live", "Test Artist", "Test Album"))
        self.assertFalse(any(k.startswith("live recording") for k in res.excluded))

    def test_unmatched_request_reports_near_misses_instead_of_silence(self):
        res = self.find(want=Want("Test Artist - Missing Record", "Test Artist", "Missing Record"))
        self.assertEqual(res.candidates, [])
        self.assertIn("no FLAC candidate matched this request", res.notes)
        self.assertTrue(res.near_misses, "the artist's other FLAC items should be listed")

    def test_artist_only_lists_albums(self):
        res = self.find(want=Want("Test Artist", "Test Artist", ""))
        self.assertTrue(res.candidates)
        self.assertEqual(res.candidates[0].identifier, "ta-log")

    def test_deep_stage_verifies_the_top_pick(self):
        res = self.find(deep="top", spectral=False)
        best = res.candidates[0]
        self.assertEqual(best.identifier, "ta-log")
        self.assertIn("V2", best.levels)
        self.assertEqual(len(best.integrity), 6)

    def test_corrupt_download_demotes_to_tier_d_and_stops_at_the_first_bad_file(self):
        pipe = self.pipe(corrupt_stream={"ta-log": "03 Track 3.flac"})
        res = pipe.find_one(Want("x", "Test Artist", "Test Album"), Options(deep="top", spectral=False, top=1))
        best = res.candidates[0]
        self.assertEqual((best.identifier, best.tier), ("ta-log", "D"))
        self.assertTrue(any("first failure" in n for n in best.notes))
        self.assertEqual(len(best.integrity), 3, "testing stops at the first failing file")

    def test_deep_stage_falls_through_to_the_next_album_when_the_top_pick_fails(self):
        want = Want("Test Artist", "Test Artist", "")
        clean = self.pipe().find_one(want, Options(deep="none", top=5))
        first, second = clean.candidates[0].identifier, clean.candidates[1].identifier
        res = self.pipe(corrupt_stream={first: "03 Track 3.flac"}).find_one(want, Options(deep="top", spectral=False, top=5))
        by_id = {c.identifier: c for c in res.candidates}
        self.assertEqual(by_id[first].tier, "D")
        self.assertIn("V2", by_id[second].levels, "the next album was deep-checked once the first failed")
        self.assertEqual(res.candidates[0].identifier, second, "the failed album sinks below the verified one")


class Prefilter(unittest.TestCase):
    """Search-result docs are screened before any (slow, ~3 s cold) metadata call."""
    doc = {"identifier": "x", "title": "Alpha - One", "creator": "Alpha", "item_size": 300_000_000, "files_count": 40}

    def check(self, **changes):
        return Pipeline.prefilter({**self.doc, **changes}, 5, False)

    def test_an_ordinary_album_passes(self):
        self.assertEqual(self.check(), "")

    def test_live_tapes_by_metadata_title_or_collection(self):
        for changes in ({"venue": "The Roxy"}, {"taper": "Bob"}, {"title": "Alpha 1999-05-05"},
                        {"title": "Alpha - Live at the Roxy"}, {"collection": ["etree", "x"]}):
            self.assertTrue(self.check(**changes).startswith("live recording"), changes)
        self.assertEqual(Pipeline.prefilter({**self.doc, "venue": "x"}, 5, include_live=True), "")

    def test_tiny_and_short_items(self):
        self.assertIn("not album-shaped", self.check(item_size=12_000_000))
        self.assertIn("fewer than 5 FLAC files", self.check(files_count=3))

    def test_missing_size_fields_are_not_grounds_for_dropping(self):
        self.assertEqual(Pipeline.prefilter({"identifier": "x", "title": "Alpha - One"}, 5, False), "")


@unittest.skipUnless(HAVE_FLAC and HAVE_FFMPEG, "flac and ffmpeg required")
class GenreFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sg = ("shoegaze", "dream pop")
        cls.items = [
            make_item("a1", "Alpha - One", "Alpha", subjects=sg, downloads=500, seed=1, album_tag="One"),
            make_item("a2", "Alpha - Two", "Alpha", subjects=sg, downloads=300, seed=2, album_tag="Two"),
            make_item("a3", "Alpha - Three", "Alpha", subjects=sg, downloads=100, seed=3, album_tag="Three"),
            make_item("b1", "Beta - Tiny Ep", "Beta", tracks=3, subjects=sg, seed=4),
            make_item("g1", "Gamma - Live 2001-02-03", "Gamma", subjects=sg, seed=5),
            make_item("d1", "Delta - Jazz Record", "Delta", subjects=("jazz",), seed=6),
            make_item("e1", "Epsilon - Ripless", "Epsilon", rip=False, subjects=sg, seed=7, album_tag="Ripless"),
        ]

    def run_genre(self, **kw):
        spec = GenreSpec(genre="shoegaze", terms=["shoegaze", "dream pop"], top=kw.pop("top", 10), **kw)
        return Pipeline(FakeIA(self.items)).genre(spec, Options(deep="none"))

    def test_per_artist_cap_min_tracks_and_live_filtering(self):
        res = self.run_genre(per_artist=2)
        ids = [c.identifier for c in res.candidates]
        self.assertEqual(ids[:2], ["a1", "a2"], "two Alpha items survive, the most-downloaded first")
        self.assertNotIn("a3", ids)
        self.assertNotIn("b1", ids)          # 3 tracks: an EP, below --min-tracks
        self.assertNotIn("g1", ids)          # live
        self.assertNotIn("d1", ids)          # wrong genre
        self.assertTrue(any("fewer than 5" in k for k in res.excluded))
        self.assertTrue(any(k.startswith("live recording") for k in res.excluded))

    def test_cd_only_widens_to_any_flac_with_a_visible_note(self):
        res = self.run_genre()
        self.assertIn("e1", [c.identifier for c in res.candidates])
        self.assertTrue(any("widened to any FLAC" in n for n in res.notes))
        tiers = [c.tier for c in res.candidates]
        self.assertEqual(tiers, sorted(tiers), "better tiers are listed first")
        self.assertEqual(res.candidates[-1].identifier, "e1")

    def test_any_flac_mode_needs_no_widening(self):
        res = self.run_genre(cd_only=False)
        self.assertFalse(any("widened" in n for n in res.notes))

    def test_match_confidence_is_recorded(self):
        res = self.run_genre()
        self.assertEqual(res.candidates[0].genre_conf, "subject")
        self.assertTrue(any(n.startswith("genre match via subject") for n in res.candidates[0].notes))


if __name__ == "__main__":
    unittest.main()
