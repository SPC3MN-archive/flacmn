import unittest

from iaflac import matching as m


class Normalising(unittest.TestCase):
    def test_edition_tags_disappear_but_real_parentheses_stay(self):
        self.assertEqual(m.normalize("Kind of Blue (Legacy Edition) [FLAC]"), "kind of blue")
        self.assertIn("live", m.normalize("Hot Rats (Live)"))
        self.assertEqual(m.normalize("Sigur Rós – Ágætis byrjun"), "sigur ros agaetis byrjun")
        self.assertEqual(m.normalize("Røyksopp"), "royksopp")
        self.assertEqual(m.similarity("Agaetis Byrjun", "Ágætis byrjun"), 1.0)

    def test_apostrophes_and_ampersands(self):
        self.assertEqual(m.normalize("Guns N’ Roses"), "guns n roses")
        self.assertEqual(m.tokens("Crosby, Stills & Nash"), ["crosby", "stills", "nash"])
        self.assertEqual(m.normalize("Isn't"), m.normalize("Isn’t"))


class Similarity(unittest.TestCase):
    def test_edition_tags_do_not_matter(self):
        self.assertEqual(m.similarity("Kind of Blue", "Kind of Blue (Legacy Edition)"), 1.0)

    def test_wanted_words_inside_a_longer_title(self):
        self.assertGreaterEqual(m.similarity("Kind of Blue", "Miles Davis - Kind of Blue (1959)"), 0.92)

    def test_unrelated(self):
        self.assertLess(m.similarity("Kind of Blue", "OK Computer"), 0.5)


class VolumesAndParts(unittest.TestCase):
    def test_different_volume_or_part_is_a_different_album(self):
        self.assertLess(m.similarity("Greatest Hits Vol. 1", "Greatest Hits Vol. 2"), 0.5)
        self.assertLess(m.similarity("Led Zeppelin II", "Led Zeppelin IV"), 0.5)
        self.assertLess(m.similarity("IV", "II"), 0.5)

    def test_same_volume_still_matches(self):
        self.assertEqual(m.similarity("Greatest Hits Vol. 2", "Greatest Hits Vol. 2 (Remastered)"), 1.0)
        self.assertGreaterEqual(m.similarity("Volume 2", "Vol 2 - Deluxe"), 0.4)   # wording differs, but not rejected on numbers

    def test_ordinary_words_are_not_mistaken_for_numerals(self):
        for word in ("mix", "civil", "dim", "livid", "vivid"):
            self.assertIsNone(m.NUMBER.fullmatch(word), word)
        for numeral in ("i", "ii", "iii", "iv", "v", "vi", "ix", "xi", "xx", "12", "182"):
            self.assertTrue(m.NUMBER.fullmatch(numeral), numeral)

    def test_years_are_not_volume_numbers(self):
        self.assertGreaterEqual(m.similarity("Greatest Hits", "Greatest Hits 2004"), 0.9)


class Matching(unittest.TestCase):
    def test_wrong_album_by_the_right_artist_is_a_near_miss_even_when_a_word_is_shared(self):
        for wanted, have in (("Missing Record", "Second Record"), ("IV", "II"), ("Kind of Blue", "Sketches of Spain")):
            r = m.match_fields("Some Artist", wanted, f"Some Artist - {have}", "Some Artist")
            self.assertEqual(r.bucket, 2, (wanted, have, r))

    def test_reasonable_variants_still_match(self):
        for wanted, have in (("Frances Mute", "Frances the Mute"), ("Dark Side", "The Dark Side of the Moon"),
                             ("Sgt Peppers Lonely Hearts Club Band", "Sgt. Pepper's Lonely Hearts Club Band"),
                             ("Francis the Mute", "Frances the Mute")):
            r = m.match_fields("Some Artist", wanted, f"Some Artist - {have}", "Some Artist")
            self.assertLess(r.bucket, 2, (wanted, have, r))

    def test_creator_missing_artist_only_in_title(self):
        r = m.match_fields("The Mars Volta", "Frances the Mute",
                           "The Mars Volta - 2005 - Frances the Mute (075021039773 - 2005-02-21)", "")
        self.assertGreaterEqual(r.score, 0.9)
        self.assertEqual(r.bucket, 0)

    def test_exact_item(self):
        r = m.match_fields("Miles Davis", "Kind of Blue", "Miles Davis - Kind of Blue (1959)", "Miles Davis")
        self.assertGreaterEqual(r.score, 0.95)

    def test_right_album_wrong_artist_is_a_near_miss(self):
        r = m.match_fields("Miles Davis", "Kind of Blue", "Kind of Blue Tribute", "Bill Evans Trio")
        self.assertLessEqual(r.score, 0.55)
        self.assertEqual(r.bucket, 2)

    def test_right_artist_wrong_album_is_a_near_miss(self):
        r = m.match_fields("Miles Davis", "Kind of Blue", "Miles Davis - Bitches Brew", "Miles Davis")
        self.assertEqual(r.bucket, 2)

    def test_flac_tags_rescue_an_uninformative_title(self):
        r = m.match_fields("Radiohead", "OK Computer", "my rip 2", "someuploader",
                           tag_artist="Radiohead", tag_album="OK Computer")
        self.assertGreaterEqual(r.score, 0.9)

    def test_artist_only_request(self):
        r = m.match_fields("Boards of Canada", "", "Boards of Canada - Geogaddi", "Boards of Canada")
        self.assertGreaterEqual(r.score, 0.95)


class Splitting(unittest.TestCase):
    def test_split_artist_album(self):
        self.assertEqual(m.split_artist_album("Miles Davis - Kind of Blue (1959)"), ("Miles Davis", "Kind of Blue (1959)"))
        self.assertEqual(m.split_artist_album("The Mars Volta - 2005 - Frances the Mute (075021039773 - 2005-02-21)"),
                         ("The Mars Volta", "Frances the Mute"))
        self.assertEqual(m.split_artist_album("Just A Title"), ("", "Just A Title"))
        self.assertEqual(m.split_artist_album("Linkin Park -- The Hunting Party (FLAC)"), ("Linkin Park", "The Hunting Party (FLAC)"))

    def test_duplicate_uploads_share_a_cluster_key(self):
        a = m.cluster_key(*m.guess_artist_album("Miles Davis - Kind of Blue (1959)", ""))
        b = m.cluster_key(*m.guess_artist_album("whatever", "Miles Davis", tag_album="Kind of Blue"))
        c = m.cluster_key(*m.guess_artist_album("x", "", tag_artist="Miles Davis", tag_album="Kind of Blue (Legacy Edition)"))
        self.assertEqual(a, b)
        self.assertEqual(b, c)


class AlbumLikeness(unittest.TestCase):
    def test_shapes(self):
        self.assertEqual(m.album_likeness(12, 3600), 1.0)
        self.assertLess(m.album_likeness(1, 200), 0.5)          # a single track
        self.assertEqual(m.album_likeness(1, 3000), 0.6)        # one-file image rip
        self.assertEqual(m.album_likeness(4, 1200), 0.6)        # EP
        self.assertEqual(m.album_likeness(74, 15000), 0.5)      # multi-disc box set (250 min)
        self.assertLess(m.album_likeness(261, 71550), 0.5)      # a 24-release, 20-hour discography is not an album
        self.assertLess(m.album_likeness(60, None), 0.5)        # many files and no runtime: don't guess


if __name__ == "__main__":
    unittest.main()
