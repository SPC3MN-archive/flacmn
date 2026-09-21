import unittest

from iaflac import names as n


class AlbumTitles(unittest.TestCase):
    def test_catalogue_numbers_years_barcodes_and_edition_tags_are_dropped(self):
        cases = {
            "Geogaddi (warpcd101)": "Geogaddi",
            "Kind of Blue (1959)": "Kind of Blue",
            "Kind of Blue (1959) [FLAC]": "Kind of Blue",
            "Frances the Mute (075021039773 - 2005-02-21)": "Frances the Mute",
            "Psyence Fiction (1998) {TFCK-87955}": "Psyence Fiction (1998) {TFCK-87955}",   # braces are not handled: left alone, shown for confirmation
            "Milkyway (Lithium - Licd 05; 1992)": "Milkyway (Lithium - Licd 05; 1992)",
            "OK Computer (Deluxe Edition)": "OK Computer",
            "Mm.. Food (TFCK-87955)": "Mm.. Food",
            "2005 - Frances the Mute": "Frances the Mute",
            "The Hunting Party (FLAC)": "The Hunting Party",
        }
        for raw, expected in cases.items():
            self.assertEqual(n.clean_album_title(raw), expected, raw)

    def test_parentheses_that_belong_to_the_title_are_kept(self):
        for title in ("Hot Rats (Live)", "Exit Music (For A Film)", "Songs (of Innocence)", "(What's the Story) Morning Glory?"):
            self.assertEqual(n.clean_album_title(title), title)

    def test_a_title_that_is_only_a_bracket_is_not_emptied(self):
        self.assertEqual(n.clean_album_title("(1959)"), "(1959)")


class Derivation(unittest.TestCase):
    def test_from_the_title_when_there_are_no_tags(self):
        d = n.derive_names("Boards Of Canada - Geogaddi (warpcd101)")
        self.assertEqual((d.artist, d.album, d.source), ("Boards Of Canada", "Geogaddi", "title"))
        self.assertTrue(d.guessed)

    def test_tags_win_over_the_title(self):
        d = n.derive_names("The Mars Volta - 2005 - Frances the Mute (075021039773 - 2005-02-21)", "", "The Mars Volta", "Frances the Mute")
        self.assertEqual((d.artist, d.album, d.source), ("The Mars Volta", "Frances the Mute", "tags"))

    def test_creator_beats_the_title_for_the_artist(self):
        d = n.derive_names("Kind of Blue", "Miles Davis")
        self.assertEqual((d.artist, d.album, d.source), ("Miles Davis", "Kind of Blue", "creator"))

    def test_existing_spelling_is_reused_case_insensitively(self):
        self.assertEqual(n.canonical_artist("Boards Of Canada", ["Radiohead", "Boards of Canada"]), "Boards of Canada")
        self.assertEqual(n.canonical_artist("New Artist", ["Radiohead"]), "New Artist")


class FolderNames(unittest.TestCase):
    def test_folder_components_are_single_and_safe(self):
        self.assertEqual(n.folder_component("AC/DC"), "AC-DC")
        self.assertEqual(n.folder_component('What? "Now": <ok>'), "What_ _Now__ _ok_")
        for hostile in ("..", ".", "../../etc", "/abs", "...", "  ..  ", ""):
            out = n.folder_component(hostile)
            self.assertNotIn("/", out)
            self.assertNotIn(out, ("..", "."), hostile)
        self.assertEqual(n.folder_component("Tyler, The Creator"), "Tyler, The Creator")
        self.assertEqual(n.folder_component("Sigur Rós"), "Sigur Rós")

    def test_override_syntax(self):
        self.assertEqual(n.parse_override("ident-1=The Mars Volta | Frances the Mute"), ("ident-1", "The Mars Volta", "Frances the Mute"))
        for bad in ("no-equals", "id=OnlyArtist", "=A | B", "id= | B"):
            with self.assertRaises(ValueError):
                n.parse_override(bad)


if __name__ == "__main__":
    unittest.main()
