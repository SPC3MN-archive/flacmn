"""Query builders for archive.org's advancedsearch (Lucene syntax)."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .matching import normalize, tokens

BASE = "mediatype:audio AND format:Flac AND -access-restricted-item:true"
# IA labels rip sidecars as item formats, so "ships a rip log or cue sheet" can be filtered server-side (only when
# the user asks for it: any genuine lossless source is as welcome as a CD rip)
RIP_SHAPED = '(format:Log OR format:"Cue Sheet")'
OTHER_LOSSLESS_Q = "format:Shorten"          # lossless formats that are not searched for (the rest are: ALT_FORMAT)
SEPARATOR = re.compile(r"\s+(?:--|::|[-–—])\s+|\t")


@dataclass
class Want:
    raw: str
    artist: str
    album: str = ""

    @property
    def label(self) -> str:
        return f"{self.artist} — {self.album}" if self.album else self.artist


@dataclass
class Rung:
    label: str
    query: str
    rows: int = 30


def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def phrase(s: str) -> str:
    return f'"{esc(s.strip())}"'


def clean_title(s: str) -> str:
    """Drop edition/format tags and a trailing year so 'Kind of Blue (Legacy Edition) [FLAC]' searches as 'Kind of Blue'."""
    out = re.sub(r"[(\[]([^)\]]*)[)\]]", lambda m: " " if normalize(m.group(0)) == "" else m.group(0), s)
    out = re.sub(r"\s*[(\[]?\b(19|20)\d\d\b[)\]]?\s*$", " ", out)
    return re.sub(r"\s+", " ", out).strip()


def _and_tokens(text: str) -> str:
    return " AND ".join(tokens(text))


def parse_wanted(text: str, csv_format: bool = False) -> list:
    """Lines of 'Artist - Album' or just 'Artist' ('#' comments and blanks ignored), or CSV artist,album."""
    wants = []
    if csv_format:
        rows = list(csv.reader(io.StringIO(text)))
        if rows and [c.strip().lower() for c in rows[0][:2]] == ["artist", "album"]:
            rows = rows[1:]
        for row in rows:
            row = [c.strip() for c in row]
            if row and row[0] and not row[0].startswith("#"):
                wants.append(Want(raw=", ".join(row[:2]), artist=row[0], album=row[1] if len(row) > 1 else ""))
        return wants
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = SEPARATOR.split(line, maxsplit=1)
        wants.append(Want(raw=line, artist=parts[0].strip(), album=parts[1].strip() if len(parts) == 2 else ""))
    return wants


def read_wanted(path: str) -> list:
    p = Path(path).expanduser()
    return parse_wanted(p.read_text(encoding="utf-8-sig"), csv_format=p.suffix.lower() == ".csv")


def _artist_clause(artist: str) -> str:
    return f"(creator:{phrase(artist)} OR title:{phrase(artist)})"


def album_rungs(artist: str, album: str, rip_only: bool = False) -> list:
    artist, album = clean_title(artist), clean_title(album)
    exact = f"{BASE} AND {_artist_clause(artist)} AND title:{phrase(album)}"
    if rip_only:
        return [Rung("exact title, with a rip log or cue sheet", f"{exact} AND {RIP_SHAPED}")]
    rungs = [Rung("exact title", exact, rows=60)]
    at, bt = _and_tokens(artist), _and_tokens(album)
    if at and bt:
        rungs.append(Rung("relaxed words in title/description",
                          f"{BASE} AND (creator:({at}) OR title:({at})) AND (title:({bt}) OR description:({bt}))"))
    return rungs


def artist_rungs(artist: str, rip_only: bool = False) -> list:
    artist = clean_title(artist)
    base = f"{BASE} AND {_artist_clause(artist)}"
    if rip_only:
        return [Rung("artist, with a rip log or cue sheet", f"{base} AND {RIP_SHAPED}", rows=50)]
    return [Rung("artist, any FLAC", base, rows=100)]


def near_miss_rung(artist: str) -> Rung:
    return Rung("same artist, any FLAC (to show what does exist)", f"{BASE} AND {_artist_clause(clean_title(artist))}", rows=25)


def other_lossless_rung(artist: str, album: str) -> Rung:
    q = f"mediatype:audio AND -access-restricted-item:true AND {OTHER_LOSSLESS_Q} AND {_artist_clause(clean_title(artist))}"
    if album:
        q += f" AND title:{phrase(clean_title(album))}"
    return Rung("other lossless formats (SHN)", q, rows=10)


# every format looked for next to FLAC, lossless first: search key -> archive.org format field, name, ItemInfo.media
ALT_FORMAT = {"wav": "format:WAVE", "aiff": "format:AIFF",
              "m4a": '(format:"Apple Lossless Audio" OR format:"MPEG-4 Audio")',
              "ape": 'format:"Monkey\'s Audio"', "wv": "format:WavPack", "vbr": 'format:"VBR MP3"'}
ALT_NAME = {"wav": "WAV", "aiff": "AIFF", "m4a": "M4A", "ape": "APE", "wv": "WavPack", "vbr": "VBR MP3"}
ALT_MEDIA = {"wav": "wav", "aiff": "aiff", "m4a": "m4a", "ape": "ape", "wv": "wv", "vbr": "mp3"}
ALL_ALT = ("wav", "aiff", "m4a", "ape", "wv", "vbr")


def alt_rungs(artist: str, album: str, media: str) -> list:
    """Uploads in another format: WAV, AIFF, ALAC (.m4a), APE or WavPack (lossless, converted to FLAC when saved), or
    AAC (.m4a) and IA's 'VBR MP3' files (lossy, kept as they are). Same artist/title matching as for FLAC."""
    artist, album = clean_title(artist), clean_title(album)
    base = f"mediatype:audio AND -access-restricted-item:true AND {ALT_FORMAT[media]}"
    name = ALT_NAME[media]
    if not album:
        return [Rung(f"artist, {name}", f"{base} AND {_artist_clause(artist)}", rows=30)]
    rungs = [Rung(f"exact title, {name}", f"{base} AND {_artist_clause(artist)} AND title:{phrase(album)}")]
    at, bt = _and_tokens(artist), _and_tokens(album)
    if at and bt:
        rungs.append(Rung(f"relaxed words in title/description, {name}",
                          f"{base} AND (creator:({at}) OR title:({at})) AND (title:({bt}) OR description:({bt}))"))
    return rungs


def parse_years(spec: str) -> tuple:
    m = re.fullmatch(r"\s*(\d{4})\s*(?:[-:]\s*(\d{4}))?\s*", spec or "")
    if not m:
        raise ValueError(f"years must look like 1988-1996 or 1994, got {spec!r}")
    lo = int(m.group(1))
    return lo, int(m.group(2) or lo)


def genre_query(terms: list, *, fields=("subject", "title"), years: Optional[tuple] = None,
                rip_shaped: bool = True, exclude: tuple = (), collections: tuple = ()) -> str:
    any_term = " OR ".join(phrase(t) for t in terms)
    q = f"{BASE} AND (" + " OR ".join(f"{f}:({any_term})" for f in fields) + ")"
    if years:
        q += f" AND year:[{years[0]} TO {years[1]}]"
    if collections:
        q += " AND collection:(" + " OR ".join(collections) + ")"
    if rip_shaped:
        q += f" AND {RIP_SHAPED}"
    for word in exclude:
        q += f" AND -subject:{phrase(word)} AND -title:{phrase(word)}"
    return q
