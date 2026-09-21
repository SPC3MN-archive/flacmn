"""Genre search helpers: term expansion, match confidence, per-artist diversification."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .matching import normalize, tokens
from .model import as_list

GENRES_FILE = Path(__file__).resolve().parents[1] / "genres.yml"
CONFIDENCE = {"subject": 3.0, "title": 2.0, "description": 1.0}


@dataclass
class GenreSpec:
    genre: str
    terms: list
    exclude: list = field(default_factory=list)
    years: Optional[tuple] = None
    cd_only: bool = True
    min_tracks: int = 5
    per_artist: int = 2
    top: int = 25
    enrich_n: int = 45              # search hits whose metadata is fetched (the slow call)
    probe_n: int = 30               # of those, how many get header probes and rip-log reads
    collections: tuple = ()
    include_live: bool = False
    free_only: bool = False


@dataclass
class GenreHit:
    doc: dict
    via: str
    rip_shaped: bool
    conf: str

    def pre_score(self) -> float:
        downloads = int(self.doc.get("downloads") or 0)
        return CONFIDENCE[self.conf] + (2.0 if self.rip_shaped else 0.0) + math.log10(1 + downloads)


@dataclass
class GenreResult:
    spec: GenreSpec
    searched: list = field(default_factory=list)    # [(label, hits, query)]
    candidates: list = field(default_factory=list)
    excluded: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


def load_genres(path=None) -> dict:
    try:
        raw = yaml.safe_load(Path(path or GENRES_FILE).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return {normalize(str(k)): {"terms": [str(t) for t in (v or {}).get("terms", [])],
                                "exclude": [str(t) for t in (v or {}).get("exclude", [])]}
            for k, v in raw.items()}


def _merge(*groups) -> list:
    seen, out = set(), []
    for group in groups:
        for term in group:
            term = term.strip()
            if term and term.casefold() not in seen:
                seen.add(term.casefold())
                out.append(term)
    return out


def split_csv(text: Optional[str]) -> list:
    return [t.strip() for t in (text or "").split(",") if t.strip()]


def resolve(name: str, extra_terms=(), extra_exclude=(), genres: Optional[dict] = None) -> tuple:
    """(terms, exclude) for a genre name: the file's entry (if any) + the name itself + user additions."""
    genres = load_genres() if genres is None else genres
    entry = genres.get(normalize(name), {})
    return (_merge([name], entry.get("terms", []), extra_terms), _merge(entry.get("exclude", []), extra_exclude))


def confidence(doc: dict, terms: list, default: str = "description") -> str:
    """How directly the search hit names the genre: a subject tag beats a title word beats a description."""
    subjects = " ; ".join(as_list(doc.get("subject"))).casefold()
    title = " ".join(as_list(doc.get("title"))).casefold()
    if any(t.casefold() in subjects for t in terms):
        return "subject"
    if any(t.casefold() in title for t in terms):
        return "title"
    return default


def matched_subjects(subjects: list, terms: list, limit: int = 3) -> list:
    return [s for s in subjects if any(t.casefold() in s.casefold() for t in terms)][:limit]


def diversify(candidates: list, per_artist: int) -> list:
    """Keep at most `per_artist` items per artist so one prolific uploader can't fill the list."""
    seen: Counter = Counter()
    out = []
    for cand in candidates:
        key = " ".join(tokens(cand.artist_album[0])) or cand.identifier
        if seen[key] >= per_artist:
            continue
        seen[key] += 1
        out.append(cand)
    return out
