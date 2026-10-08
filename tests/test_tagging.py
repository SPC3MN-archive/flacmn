import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from iaflac import download, tagging
from iaflac.model import FileInfo, ItemInfo
from iaflac.pipeline import Candidate
from iaflac.tagging import AlbumMeta, TrackMeta
from tests.helpers import HAVE_FFMPEG, HAVE_FLAC, encode_flac, ffmpeg_convert, tone_wav

NEED = unittest.skipUnless(HAVE_FLAC and HAVE_FFMPEG, "flac and ffmpeg required")


def jpeg(size=(1600, 1600), color=(200, 30, 30)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


class Numbers(unittest.TestCase):
    def test_track_and_disc_from_archive_names(self):
        self.assertEqual(tagging.number_from_name("Album/03 - Song.flac"), (0, 3))
        self.assertEqual(tagging.number_from_name("1-05 Goodbye.flac"), (1, 5))
        self.assertEqual(tagging.number_from_name("Album/CD2/04. Song.flac"), (2, 4))
        self.assertEqual(tagging.number_from_name("Disc 3/01 Song.flac"), (3, 1))
        self.assertEqual(tagging.number_from_name("Frankie's First Affair.flac"), (0, 0))

    def test_titles_from_names_lose_the_number_and_the_artist(self):
        self.assertEqual(tagging.title_from_name("01 - Miles Davis - So What.flac", "Miles Davis"), "So What")
        self.assertEqual(tagging.title_from_name("CD1/02. Freddie Freeloader.flac"), "Freddie Freeloader")
        self.assertEqual(tagging.title_from_name("Blue_in_Green.flac"), "Blue in Green")

    def test_parse_number(self):
        self.assertEqual(tagging.parse_number("3/12"), (3, 12))
        self.assertEqual(tagging.parse_number("07"), (7, 0))
        self.assertEqual(tagging.parse_number(None), (0, 0))


class Proposal(unittest.TestCase):
    def cand(self, names, title="Miles Davis - Kind of Blue", year=1959, creator="Miles Davis", tags=None):
        files = [FileInfo(n, size=1, length=300, tags=(tags or {}).get(n, {})) for n in names]
        return Candidate(item=ItemInfo(identifier="kob", title=title, creator=creator, year=year, files=files))

    def test_single_disc_from_file_names_and_archive_fields(self):
        meta = tagging.from_candidate(self.cand(["01 So What.flac", "02 Freddie Freeloader.flac"],
                                                tags={"02 Freddie Freeloader.flac": {"title": "Freddie Freeloader (Take 2)"}}))
        self.assertEqual((meta.albumartist, meta.album, meta.date), ("Miles Davis", "Kind of Blue", "1959"))
        self.assertEqual([(t.disc, t.track, t.title) for t in meta.tracks],
                         [(1, 1, "So What"), (1, 2, "Freddie Freeloader (Take 2)")])

    def test_disc_folders_become_disc_numbers(self):
        meta = tagging.from_candidate(self.cand(["CD1/01 A.flac", "CD1/02 B.flac", "CD2/01 C.flac"]))
        self.assertEqual([(t.disc, t.track) for t in meta.tracks], [(1, 1), (1, 2), (2, 1)])
        self.assertEqual(meta.disctotal, 2)
        self.assertEqual(meta.tracktotal(1), 2)

    def test_unnumbered_or_clashing_tracks_are_numbered_in_file_order(self):
        meta = tagging.from_candidate(self.cand(["Intro.flac", "Song.flac", "Outro.flac"]))
        self.assertEqual([t.track for t in meta.tracks], [1, 2, 3])


class Layout(unittest.TestCase):
    def meta(self, discs=1):
        tracks = [TrackMeta(f"s{d}{n}", title=f"Song {n}", track=n, disc=d) for d in range(1, discs + 1) for n in (1, 2)]
        return AlbumMeta(albumartist="Miles Davis", album="Kind of Blue", date="1959-08-17", tracks=tracks)

    def test_folder_is_artist_album_year(self):
        self.assertEqual(tagging.album_folder(Path("/lib"), self.meta()), Path("/lib/Miles Davis/Kind of Blue (1959)"))

    def test_a_year_already_in_the_album_name_is_not_repeated_and_names_are_made_safe(self):
        meta = AlbumMeta(albumartist="AC/DC", album="Back: In Black (1980)", date="1980")
        self.assertEqual(tagging.album_folder(Path("/lib"), meta), Path("/lib/AC-DC/Back_ In Black (1980)"))

    def test_track_names_single_and_multi_disc(self):
        self.assertEqual(list(tagging.planned_names(self.meta(), {}).values()), ["01 - Song 1.flac", "02 - Song 2.flac"])
        names = tagging.planned_names(self.meta(2), {k: ".flac" for k in ("s11", "s12", "s21", "s22")})
        self.assertEqual(list(names.values()), ["1-01 - Song 1.flac", "1-02 - Song 2.flac", "2-01 - Song 1.flac", "2-02 - Song 2.flac"])

    def test_duplicate_titles_never_share_a_file_name(self):
        meta = AlbumMeta(albumartist="A", album="B", tracks=[TrackMeta("x", "Same", 1), TrackMeta("y", "Same", 1)])
        names = tagging.planned_names(meta, {"x": ".flac", "y": ".flac"})
        self.assertEqual(len(set(names.values())), 2)


class Diff(unittest.TestCase):
    def test_changed_album_and_track_fields_are_listed(self):
        old = AlbumMeta(albumartist="miles davis", album="Kind Of Blue", tracks=[TrackMeta("a", "so what", 1)])
        new = AlbumMeta(albumartist="Miles Davis", album="Kind Of Blue", date="1959", tracks=[TrackMeta("a", "So What", 1)])
        changes = {(c["scope"], c["field"]) for c in tagging.diff(old, new)}
        self.assertEqual(changes, {("album", "albumartist"), ("album", "date"), ("track", "title")})

    def test_round_trip_through_dict(self):
        meta = AlbumMeta(albumartist="A", album="B", tracks=[TrackMeta("x", "T", 1, 2)])
        self.assertEqual(AlbumMeta.from_dict(meta.to_dict()), meta)


@NEED
class Writing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / "Miles Davis" / "Kind of Blue (1959)"
        (self.folder / "CD1").mkdir(parents=True)
        (self.folder / "CD2").mkdir()
        wav = tone_wav(44100 * 2)
        self.flac_bytes = encode_flac(wav)
        self.paths = {}
        for src in ("CD1/01 so what.flac", "CD1/02 freddie.flac", "CD2/01 blue.flac"):
            p = self.folder / src
            p.write_bytes(self.flac_bytes)
            self.paths[src] = p
        self.meta = AlbumMeta(albumartist="Miles Davis", album="Kind of Blue", date="1959", genre="Jazz",
                              mb_release_id="abc-123", tracks=[
                                  TrackMeta("CD1/01 so what.flac", "So What", 1, 1),
                                  TrackMeta("CD1/02 freddie.flac", "Freddie Freeloader", 2, 1),
                                  TrackMeta("CD2/01 blue.flac", "Blue in Green", 1, 2, artist="Bill Evans")])

    def tearDown(self):
        self.tmp.cleanup()

    def decoded(self, path):
        return subprocess.run(["flac", "-d", "-s", "-c", str(path)], capture_output=True, check=True).stdout

    def test_untagged_files_fail_validation(self):
        issues = tagging.validate(self.folder)
        self.assertTrue(any("TITLE" in i for i in issues), issues)
        self.assertTrue(any("cover art" in i for i in issues), issues)

    def test_apply_tags_renames_flattens_and_keeps_the_audio_bit_identical(self):
        before = self.decoded(self.paths["CD1/01 so what.flac"])
        res = tagging.apply(self.folder, self.meta, self.paths, jpeg(), verify=download.flac_test)
        self.assertEqual(res.problems, [])
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()),
                         ["1-01 - So What.flac", "1-02 - Freddie Freeloader.flac", "2-01 - Blue in Green.flac", "cover.jpg"])
        first = self.folder / "1-01 - So What.flac"
        self.assertEqual(self.decoded(first), before, "audio unchanged")
        t = tagging.read_track(first)
        self.assertEqual((t["title"], t["albumartist"], t["album"], t["track"], t["tracktotal"], t["disc"], t["disctotal"],
                          t["date"], t["genre"], t["has_art"], t["mb_release_id"]),
                         ("So What", "Miles Davis", "Kind of Blue", 1, 2, 1, 2, "1959", "Jazz", True, "abc-123"))
        self.assertEqual(tagging.read_track(self.folder / "2-01 - Blue in Green.flac")["artist"], "Bill Evans")
        self.assertEqual(tagging.validate(self.folder), [])

    def test_embedded_art_is_scaled_down_but_cover_jpg_keeps_the_original(self):
        from mutagen.flac import FLAC
        big = jpeg((2400, 2400))
        tagging.apply(self.folder, self.meta, self.paths, big)
        pic = FLAC(str(self.folder / "1-01 - So What.flac")).pictures[0]
        self.assertEqual((pic.type, pic.width, pic.height, pic.mime), (3, 1000, 1000, "image/jpeg"))
        self.assertEqual((self.folder / "cover.jpg").read_bytes(), big)

    def test_retagging_replaces_old_values_and_keeps_unrelated_ones(self):
        from mutagen.flac import FLAC
        p = self.paths["CD1/01 so what.flac"]
        audio = FLAC(str(p))
        audio["TITLE"], audio["COMMENT"] = "old title", "EAC rip"
        audio.save()
        tagging.apply(self.folder, self.meta, self.paths)
        tags = FLAC(str(self.folder / "1-01 - So What.flac"))
        self.assertEqual(tags["TITLE"], ["So What"])
        self.assertEqual(tags["COMMENT"], ["EAC rip"])

    def test_mp3_gets_id3v24_tags(self):
        mp3 = ffmpeg_convert(tone_wav(44100 * 2), "wav", ["-codec:a", "libmp3lame", "-q:a", "2"], "mp3")
        p = Path(self.tmp.name) / "01.mp3"
        p.write_bytes(mp3)
        meta = AlbumMeta(albumartist="A", album="B", date="2001", tracks=[TrackMeta("01.mp3", "Song", 1)])
        tagging.apply(p.parent, meta, {"01.mp3": p}, jpeg((300, 300)))
        t = tagging.read_track(p.parent / "01 - Song.mp3")
        self.assertEqual((t["title"], t["albumartist"], t["album"], t["track"], t["tracktotal"], t["date"], t["has_art"]),
                         ("Song", "A", "B", 1, 1, "2001", True))

    def test_a_cue_sheet_is_flagged(self):
        tagging.apply(self.folder, self.meta, self.paths, jpeg((300, 300)))
        (self.folder / "album.cue").write_text("FILE x WAVE")
        self.assertTrue(any(".cue" in i for i in tagging.validate(self.folder)))


if __name__ == "__main__":
    unittest.main()
