"""Normalise and compare artist/album strings, and cluster duplicate uploads of the same album."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

STOP = {"the", "a", "an", "of", "and"}
# words that mark a bracketed group as an edition/format tag rather than part of the title
EDITION_WORDS = {
    "deluxe", "remaster", "remastered", "edition", "expanded", "bonus", "anniversary", "reissue",
    "limited", "special", "japan", "japanese", "import", "flac", "cd", "lossless", "mp3", "24bit",
    "16bit", "24", "16", "bit", "320kbps", "v0", "eac", "xld", "vinyl", "rip", "digipak", "legacy",
    "collector", "collectors", "mono", "stereo", "explicit", "clean", "version", "disc", "cds",
}
YEAR = re.compile(r"\b(19|20)\d\d\b")
# a short number or roman numeral marks a volume/part ("Vol. 2", "Part III", "II"): a different one is a different album
NUMBER = re.compile(r"(\d{1,3}|x{0,3}(ix|iv|v?i{0,3}))")
ARTIST_GATE, ALBUM_GATE = 0.6, 0.7
# letters NFKD leaves alone, folded so "Agaetis Byrjun" finds "Ágætis byrjun" and "Royksopp" finds "Røyksopp"
FOLD = str.maketrans({"æ": "ae", "œ": "oe", "ø": "o", "ß": "ss", "đ": "d", "ð": "d", "ł": "l", "þ": "th",
                      "Æ": "ae", "Œ": "oe", "Ø": "o", "Đ": "d", "Ð": "d", "Ł": "l", "Þ": "th"})


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").translate(FOLD))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.casefold().replace("&", " and ").replace("’", "'")

    def bracket(m):
        inner = m.group(1)
        return " " if set(re.findall(r"[a-z0-9]+", inner)) & EDITION_WORDS else f" {inner} "

    s = re.sub(r"[(\[]([^)\]]*)[)\]]", bracket, s)
    s = s.replace("'", "")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(s: str) -> list:
    return [t for t in normalize(s).split() if t not in STOP]


def containment(want: str, have: str) -> float:
    """Fraction of the wanted tokens that appear in `have`."""
    want_t, have_t = tokens(want), set(tokens(have))
    return sum(t in have_t for t in want_t) / len(want_t) if want_t else 0.0


def _numbers(text: str) -> set:
    return {t for t in tokens(text) if NUMBER.fullmatch(t)}


def similarity(want: str, have: str) -> float:
    a, b = normalize(want), normalize(have)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    seq = SequenceMatcher(None, a, b).ratio()
    cont = containment(a, b)
    score = max(seq, 0.92) if cont == 1.0 else max(seq, 0.7 * cont)
    if not _numbers(a) <= _numbers(b):
        score = min(score, 0.45)      # "Vol. 1" is not "Vol. 2", "II" is not "IV"
    return score


def strip_years(s: str) -> str:
    return re.sub(r"\s+", " ", YEAR.sub(" ", s)).strip()


def split_artist_album(title: str) -> tuple:
    """'Artist - 2005 - Album (barcode - date)' -> ('Artist', 'Album'). No separator -> ('', title)."""
    parts = re.split(r"\s+(?:--|[-–—:])\s+", title, maxsplit=1)
    if len(parts) != 2:
        return "", title.strip()
    artist, album = parts[0].strip(), parts[1].strip()
    album = re.sub(r"^[(\[]?(19|20)\d\d[)\]]?\s*[-–—]\s*", "", album)
    album = re.sub(r"\s*[(\[][^)\]]*\d{6,}[^)\]]*[)\]]\s*$", "", album)
    return artist, album


def guess_artist_album(title: str, creator: str, tag_artist: str = "", tag_album: str = "") -> tuple:
    t_artist, t_album = split_artist_album(title)
    return (tag_artist or creator or t_artist), (tag_album or t_album)


def cluster_key(artist: str, album: str) -> tuple:
    return (" ".join(tokens(artist)), " ".join(tokens(strip_years(album))))


def album_likeness(n_flac: int, runtime_seconds: Optional[float]) -> float:
    """1.0 for a normal album, lower for singles, EPs, huge dumps and single-image rips."""
    minutes = runtime_seconds / 60 if runtime_seconds else None
    if n_flac == 1:
        return 0.6 if minutes and 25 <= minutes <= 100 else 0.2
    if 5 <= n_flac <= 30 and (minutes is None or 25 <= minutes <= 130):
        return 1.0
    if 3 <= n_flac <= 4 and (minutes is None or 8 <= minutes <= 40):
        return 0.6
    if n_flac > 30:
        return 0.5 if minutes is not None and minutes <= 400 else 0.3   # a box set is an album, a 20-hour discography is not
    return 0.3


@dataclass
class MatchResult:
    score: float
    artist_score: float
    album_score: float

    @property
    def bucket(self) -> int:
        """0 = exact, 1 = plausible, 2 = near miss."""
        return 0 if self.score >= 0.85 else 1 if self.score >= 0.6 else 2


def _without(text: str, words: str) -> str:
    drop = set(tokens(words))
    return " ".join(t for t in tokens(text) if t not in drop)


def match_fields(want_artist: str, want_album: str, title: str, creator: str = "",
                 tag_artist: str = "", tag_album: str = "") -> MatchResult:
    """Score how well an archive item (its title/creator, plus FLAC tags when known) fits a request."""
    if want_artist:
        artist = max([similarity(want_artist, c) for c in (creator, tag_artist) if c] or [0.0])
        artist = max(artist, 0.95 * containment(want_artist, title))
    else:
        artist = 1.0
    if want_album:
        rest = _without(title, want_artist) if want_artist else title
        album = max([similarity(want_album, x) for x in (rest, title, tag_album) if x] or [0.0])
    else:
        album = 1.0
    if want_artist and want_album:
        score = 0.45 * artist + 0.55 * album
        if artist < ARTIST_GATE or album < ALBUM_GATE:
            score = min(score, 0.55)
    else:
        score = artist if want_artist else album
    return MatchResult(score=round(score, 3), artist_score=round(artist, 3), album_score=round(album, 3))
