import tempfile
import unittest
from pathlib import Path

from iaflac import musicbrainz as mb
from iaflac.tagging import AlbumMeta, TrackMeta

KOB = [("So What", 562), ("Freddie Freeloader", 586), ("Blue in Green", 337), ("All Blues", 693), ("Flamenco Sketches", 566)]


def release(rid, title="Kind of Blue", artist="Miles Davis", tracks=KOB, discs=None, date="1959-08-17", status="Official",
            fmt="CD", extra_len=0.0):
    discs = discs or [tracks]
    return {
        "id": rid, "title": title, "date": date, "country": "US", "status": status,
        "artist-credit": [{"name": artist, "artist": {"id": "art-1", "name": artist}, "joinphrase": ""}],
        "release-group": {"id": f"rg-{rid}", "genres": [{"name": "jazz", "count": 9}, {"name": "modal jazz", "count": 3}]},
        "label-info": [{"label": {"name": "Columbia"}, "catalog-number": "CL 1355"}],
        "media": [{"format": fmt, "track-count": len(d),
                   "tracks": [{"id": f"t-{rid}-{n}-{i}", "title": t, "length": int((s + extra_len) * 1000),
                               "recording": {"id": f"r-{t}", "title": t}} for i, (t, s) in enumerate(d, 1)]}
                  for n, d in enumerate(discs, 1)],
    }


def ours(lengths=None, titles=None):
    lengths = lengths or [s + 0.4 for _, s in KOB]
    titles = titles or [f"track {i}" for i in range(1, len(lengths) + 1)]
    return AlbumMeta(albumartist="Miles Davis", album="Kind of Blue", date="1959",
                     tracks=[TrackMeta(f"{i:02d}.flac", t, i, 1, length=l) for i, (t, l) in enumerate(zip(titles, lengths), 1)])


class FakeResponse:
    def __init__(self, status, data=None, content=b""):
        self.status_code, self._data, self.content = status, data, content

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, releases, search):
        self.headers, self.releases, self.search, self.calls = {}, releases, search, []

    def get(self, url, params=None, timeout=None, allow_redirects=True):
        self.calls.append(url)
        if url.endswith("/release/"):
            return FakeResponse(200, {"releases": self.search})
        if "/ws/2/release/" in url:
            rid = url.rsplit("/", 1)[1]
            return FakeResponse(200, self.releases[rid]) if rid in self.releases else FakeResponse(404)
        if "coverartarchive" in url:
            return FakeResponse(200, content=b"JPEGDATA") if "/release/good" in url else FakeResponse(404)
        return FakeResponse(404)


class Scoring(unittest.TestCase):
    def test_same_tracks_and_lengths_score_high(self):
        m = mb.score_release(ours(), release("r1"))
        self.assertGreater(m.score, 0.9)
        self.assertAlmostEqual(m.length_delta, 0.4, places=2)
        self.assertEqual((m.label, m.catalog, m.formats, m.discs), ("Columbia", "CL 1355", "CD", [5]))

    def test_a_bonus_track_edition_scores_lower(self):
        exact = mb.score_release(ours(), release("r1"))
        bonus = mb.score_release(ours(), release("r2", tracks=KOB + [("Flamenco Sketches (alt)", 563)]))
        self.assertLess(bonus.score, exact.score - 0.3)
        self.assertIn("6 tracks on MusicBrainz vs 5 here", bonus.reasons)

    def test_different_lengths_score_lower_than_matching_ones(self):
        close = mb.score_release(ours(), release("r1"))
        remaster = mb.score_release(ours(), release("r3", extra_len=12))
        self.assertLess(remaster.score, close.score - 0.3)

    def test_multi_disc_split_is_recognised(self):
        two = release("r4", discs=[KOB[:3], KOB[3:]], fmt="CD")
        m = mb.score_release(ours(), two)
        self.assertEqual(m.formats, "2×CD")
        self.assertTrue(any("split into discs differently" in r for r in m.reasons))


class Applying(unittest.TestCase):
    def test_tracks_take_musicbrainz_titles_numbers_and_ids(self):
        new, warnings = mb.apply_release(ours(), release("r1"))
        self.assertEqual(warnings, [])
        self.assertEqual([t.title for t in new.tracks], [t for t, _ in KOB])
        self.assertEqual((new.albumartist, new.album, new.date, new.genre, new.label, new.catalog),
                         ("Miles Davis", "Kind of Blue", "1959-08-17", "Jazz", "Columbia", "CL 1355"))
        self.assertEqual((new.mb_release_id, new.mb_releasegroup_id, new.mb_albumartist_id), ("r1", "rg-r1", "art-1"))
        self.assertEqual(new.tracks[0].mb_recording_id, "r-So What")
        self.assertEqual(new.sources["album"], "musicbrainz")

    def test_a_reissue_keeps_the_albums_original_year(self):
        reissue = release("r9", date="1997-03-25")
        reissue["release-group"]["first-release-date"] = "1959-08-17"
        new, _ = mb.apply_release(ours(), reissue)
        self.assertEqual(new.date, "1959-08-17")
        del reissue["release-group"]["first-release-date"]
        self.assertEqual(mb.apply_release(ours(), reissue)[0].date, "1997-03-25")

    def test_disc_layout_follows_musicbrainz(self):
        new, _ = mb.apply_release(ours(), release("r4", discs=[KOB[:3], KOB[3:]]))
        self.assertEqual([(t.disc, t.track) for t in new.tracks], [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2)])

    def test_a_different_track_count_only_changes_album_fields(self):
        before = ours()
        new, warnings = mb.apply_release(before, release("r2", tracks=KOB + [("Bonus", 100)]))
        self.assertTrue(warnings)
        self.assertEqual([t.title for t in new.tracks], [t.title for t in before.tracks])
        self.assertEqual(new.mb_release_id, "r2")

    def test_the_original_is_not_modified(self):
        before = ours()
        mb.apply_release(before, release("r1"))
        self.assertEqual(before.tracks[0].title, "track 1")


class Client(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.releases = {"r1": release("r1"), "r2": release("r2", tracks=KOB + [("Bonus", 100)]), "good": release("good")}
        search = [{"id": "r2", "score": 100, "track-count": 6}, {"id": "r1", "score": 90, "track-count": 5}]
        self.session = FakeSession(self.releases, search)
        self.client = mb.MBClient(cache_dir=Path(self.tmp.name), session=self.session, min_interval=0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_find_matches_looks_up_and_ranks_by_our_score(self):
        matches = mb.find_matches(self.client, ours())
        self.assertEqual([m.release_id for m in matches], ["r1", "r2"])
        self.assertIn("iaflac/", self.session.headers["User-Agent"])

    def test_answers_are_cached(self):
        mb.find_matches(self.client, ours())
        n = len(self.session.calls)
        mb.find_matches(self.client, ours())
        self.assertEqual(len(self.session.calls), n, "second run served from the disk cache")

    def test_cover_art_falls_back_to_none(self):
        self.assertEqual(self.client.cover("good"), b"JPEGDATA")
        self.assertIsNone(self.client.cover("missing", "rg-missing"))

    def test_missing_release_raises(self):
        with self.assertRaises(mb.MBError):
            self.client.release("nope")


if __name__ == "__main__":
    unittest.main()
