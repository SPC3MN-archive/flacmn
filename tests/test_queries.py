import unittest

from iaflac import queries as q


class AlbumLadder(unittest.TestCase):
    def test_rungs_in_order_and_shape(self):
        rungs = q.album_rungs("Miles Davis", "Kind of Blue (Legacy Edition)")
        self.assertEqual(len(rungs), 3)
        first, second, third = (r.query for r in rungs)
        self.assertTrue(first.startswith(q.BASE))
        self.assertIn(q.RIP_SHAPED, first)
        self.assertNotIn(q.RIP_SHAPED, second)
        self.assertEqual(first, f"{second} AND {q.RIP_SHAPED}")
        self.assertIn('(creator:"Miles Davis" OR title:"Miles Davis")', second)
        self.assertIn('title:"Kind of Blue"', second)          # edition tag stripped
        self.assertNotIn("Legacy", second)
        self.assertIn("miles AND davis", third)                 # relaxed rung ANDs plain words
        self.assertIn("kind AND blue", third)

    def test_artist_is_searched_in_title_as_well_as_creator(self):
        # the Mars Volta item has no creator field at all
        self.assertIn("creator:", q.album_rungs("The Mars Volta", "Frances the Mute")[0].query)
        self.assertIn('title:"The Mars Volta"', q.album_rungs("The Mars Volta", "Frances the Mute")[0].query)

    def test_rip_only_stops_at_the_first_rung(self):
        self.assertEqual(len(q.album_rungs("A", "B", rip_only=True)), 1)

    def test_quotes_are_escaped(self):
        query = q.album_rungs('"Weird" Al Yankovic', "Even Worse")[0].query
        self.assertIn(r'\"Weird\" Al Yankovic', query)

    def test_artist_only_ladder(self):
        rungs = q.artist_rungs("Boards of Canada")
        self.assertEqual(len(rungs), 2)
        self.assertIn(q.RIP_SHAPED, rungs[0].query)
        self.assertNotIn("title:\"\"", rungs[1].query)

    def test_other_lossless_rung_excludes_flac_requirement(self):
        query = q.other_lossless_rung("Miles Davis", "Kind of Blue").query
        self.assertNotIn("format:Flac", query)
        self.assertIn("Apple Lossless Audio", query)


class GenreQuery(unittest.TestCase):
    def test_full_query(self):
        query = q.genre_query(["shoegaze", "dream pop"], years=(1988, 1996), exclude=("karaoke",))
        self.assertTrue(query.startswith(q.BASE))
        self.assertIn('subject:("shoegaze" OR "dream pop")', query)
        self.assertIn('title:("shoegaze" OR "dream pop")', query)
        self.assertIn("year:[1988 TO 1996]", query)
        self.assertIn(q.RIP_SHAPED, query)
        self.assertIn('-subject:"karaoke"', query)

    def test_any_flac_and_collections(self):
        query = q.genre_query(["ambient"], rip_shaped=False, collections=("netlabels", "hifidelity"))
        self.assertNotIn(q.RIP_SHAPED, query)
        self.assertIn("collection:(netlabels OR hifidelity)", query)

    def test_years(self):
        self.assertEqual(q.parse_years("1988-1996"), (1988, 1996))
        self.assertEqual(q.parse_years("1994"), (1994, 1994))
        with self.assertRaises(ValueError):
            q.parse_years("nineties")


class WantedLists(unittest.TestCase):
    def test_text_lines(self):
        wants = q.parse_wanted("""
            # my list
            Miles Davis - Kind of Blue
            Boards of Canada
            Sigur Rós – Ágætis byrjun
            The Mars Volta -- Frances the Mute
            Godspeed You! Black Emperor — Lift Your Skinny Fists Like Antennas to Heaven
        """)
        self.assertEqual([(w.artist, w.album) for w in wants], [
            ("Miles Davis", "Kind of Blue"), ("Boards of Canada", ""), ("Sigur Rós", "Ágætis byrjun"),
            ("The Mars Volta", "Frances the Mute"),
            ("Godspeed You! Black Emperor", "Lift Your Skinny Fists Like Antennas to Heaven")])

    def test_hyphenated_names_are_not_split(self):
        wants = q.parse_wanted("Jay-Z - The Blueprint\nWu-Tang Clan")
        self.assertEqual([(w.artist, w.album) for w in wants], [("Jay-Z", "The Blueprint"), ("Wu-Tang Clan", "")])

    def test_csv_with_and_without_header(self):
        with_header = q.parse_wanted("artist,album\nRadiohead,OK Computer\n\"Crosby, Stills & Nash\",\n", csv_format=True)
        self.assertEqual([(w.artist, w.album) for w in with_header],
                         [("Radiohead", "OK Computer"), ("Crosby, Stills & Nash", "")])
        no_header = q.parse_wanted("Radiohead,OK Computer\n", csv_format=True)
        self.assertEqual(no_header[0].album, "OK Computer")


if __name__ == "__main__":
    unittest.main()
