"""Turn an archive item's title and tags into the Artist and Album names a music library uses.

archive.org titles are free-form ("Boards Of Canada - Geogaddi (warpcd101)", "The Mars Volta - 2005 - Frances the
Mute (075021039773 - 2005-02-21)"), so names are derived in this order and always shown for confirmation:
an explicit override, then the FLAC tags, then the item's creator, then the title split at " - ".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from .matching import guess_artist_album, normalize, split_artist_album

CATALOGUE = re.compile(r"^[A-Za-z]{2,}[-_ ]?\d{2,}[A-Za-z0-9]*$")     # warpcd101, LICD051992, TFCK-87955
BRACKETED = re.compile(r"\s*[(\[]([^)\]]*)[)\]]\s*$")
YEAR_ONLY = re.compile(r"^(?:19|20)\d\d$")
ILLEGAL = re.compile(r'[\x00-\x1f<>:"|?*\\]')


@dataclass
class Names:
    artist: str
    album: str
    source: str            # "override" | "tags" | "title" | "creator"

    @property
    def guessed(self) -> bool:
        return self.source != "override"


def clean_album_title(album: str) -> str:
    """Drop trailing years, catalogue numbers, barcodes and edition/format tags:
    'Geogaddi (warpcd101)' -> 'Geogaddi', 'Kind of Blue (1959) [FLAC]' -> 'Kind of Blue'. A parenthesis that is part
    of the title ('Hot Rats (Live)') is kept."""
    text = album.strip()
    while True:
        m = BRACKETED.search(text)
        if not m or m.start() == 0:
            break
        inner = m.group(1).strip()
        droppable = (YEAR_ONLY.match(inner) or CATALOGUE.match(inner) or re.search(r"\d{6,}", inner)
                     or normalize(f"({inner})") == "")
        if not droppable:
            break
        text = text[:m.start()].rstrip()
    text = re.sub(r"^(?:19|20)\d\d\s*[-–—]\s*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def derive_names(title: str, creator: str = "", tag_artist: str = "", tag_album: str = "") -> Names:
    artist, album = guess_artist_album(title, creator, tag_artist, tag_album)
    if tag_artist and tag_album:
        source = "tags"
    elif creator and tag_album:
        source = "tags"
    elif creator:
        source = "creator"
    else:
        source = "title"
    return Names(artist.strip(), clean_album_title(album), source)


def canonical_artist(artist: str, known: Iterable[str]) -> str:
    """Use the spelling already in the library/inventory ('Boards of Canada', not 'Boards Of Canada')."""
    for existing in known:
        if existing.casefold() == artist.casefold():
            return existing
    return artist


def folder_component(name: str) -> str:
    """A single safe folder name: no separators, no illegal characters, never '.', '..' or empty."""
    cleaned = ILLEGAL.sub("_", name.replace("/", "-")).strip().rstrip(".").lstrip(". ")
    return re.sub(r"\s+", " ", cleaned)[:150]


def parse_override(spec: str) -> tuple:
    """'the-mars-volta-...=The Mars Volta | Frances the Mute' -> (identifier, artist, album)."""
    ident, sep, rest = spec.partition("=")
    artist, bar, album = rest.partition("|")
    if not (sep and bar and ident.strip() and artist.strip() and album.strip()):
        raise ValueError(f"--as expects IDENTIFIER=Artist | Album, got {spec!r}")
    return ident.strip(), artist.strip(), album.strip()
