"""What is already in a music library folder: one entry per album, with its format, quality and tag problems.

An album is a folder that holds audio files, or whose disc subfolders (CD1/, Disc 2/) do. Names come from the
files' own ALBUMARTIST/ALBUM tags when they agree, else from the <Artist>/<Album> folder names. Reading every file's
tags is the slow part, so each album is cached by its files' sizes and modification times and only re-read when
something in it changes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import matching, paths
from .events import CancelCheck, check
from .names import clean_album_title
from .tagging import COVER_NAMES, DISC_DIR, read_track, validate

AUDIO = {".flac", ".mp3", ".m4a", ".wav", ".ogg", ".opus", ".aiff", ".aif", ".wv", ".ape"}
LOSSLESS = {"FLAC", "WAV", "AIFF", "AIF", "WV", "APE", "ALAC"}      # ALAC: an .m4a whose codec is ALAC (tagging.read_track)
CHECK_FAILED = "could not check tags"
CACHE_VERSION = 4          # bump when the checks change, so albums cached with the old checks are read again
YEAR_SUFFIX = re.compile(r"\s*[(\[]((?:19|20)\d\d)[)\]]\s*$")


@dataclass
class AlbumEntry:
    folder: str                    # relative to the library, with forward slashes: "Miles Davis/Kind of Blue (1959)"
    artist: str
    album: str
    year: str = ""
    tracks: int = 0
    discs: int = 1
    formats: dict = field(default_factory=dict)      # {"FLAC 16/44.1": 9, "MP3 320": 1}
    lossless: bool = False
    size: int = 0
    seconds: float = 0.0
    has_cover: bool = False
    issues: list = field(default_factory=list)
    signature: str = ""
    # for the tag search: what the tracks say beyond the album's own names
    genres: list = field(default_factory=list)
    labels: list = field(default_factory=list)       # record labels and catalogue numbers
    songs: list = field(default_factory=list)        # track titles, in file order
    guests: list = field(default_factory=list)       # track artists other than the album artist (features, compilations)

    @property
    def format_label(self) -> str:
        if not self.formats:
            return "?"
        top = max(self.formats, key=self.formats.get)
        return top + (" (mixed)" if len(self.formats) > 1 else "")

    @property
    def quality(self) -> str:
        """"lossless" (up to 16 bit and 48 kHz), "hires" (lossless above that) or "lossy"; "" when the format isn't
        known. Lossless and hi-res are both high quality: neither is ranked above the other."""
        if not self.formats:
            return ""
        if not self.lossless:
            return "lossy"
        for f in self.formats:
            m = re.search(r"(\d+)/(\d+(?:\.\d+)?)$", f)
            if m and (int(m.group(1)) > 16 or float(m.group(2)) > 48):
                return "hires"
        return "lossless"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["format_label"] = self.format_label
        d["quality"] = self.quality
        return d


@dataclass
class LibraryIndex:
    root: str
    albums: list = field(default_factory=list)       # [AlbumEntry], sorted by artist then album
    scanned_at: float = 0.0

    def artists(self) -> list:
        return sorted({a.artist for a in self.albums}, key=str.casefold)

    def to_dict(self) -> dict:
        return {"root": self.root, "scanned_at": self.scanned_at, "albums": [a.to_dict() for a in self.albums]}

    def find(self, artist: str, album: str, threshold: float = 0.85) -> Optional[AlbumEntry]:
        """The album you already have, or None: the artist must match closely and the album closely, with
        volume/part numbers agreeing (matching.similarity handles that)."""
        best, best_score = None, 0.0
        for entry in self.albums:
            if matching.similarity(artist, entry.artist) < 0.9:
                continue
            score = matching.similarity(album, entry.album)
            if score > best_score:
                best, best_score = entry, score
        return best if best_score >= threshold else None


def _audio_files(folder: Path) -> list:
    try:
        return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO and not p.name.startswith("."))
    except OSError:
        return []


def album_folders(root: Path, *, found: Optional[Callable[[int], None]] = None, cancel: Optional[CancelCheck] = None) -> list:
    """[(album folder, [audio files])]. Disc subfolders are folded into their parent album. `found` hears how many
    albums the walk has turned up so far."""
    albums: dict = {}
    for dirpath, dirnames, _ in os.walk(root):
        check(cancel)
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        folder = Path(dirpath)
        files = _audio_files(folder)
        if not files:
            continue
        parent = folder.parent
        if folder != root and DISC_DIR.search(folder.name) and parent != root and not _audio_files(parent):
            albums.setdefault(parent, []).extend(files)
        else:
            albums.setdefault(folder, []).extend(files)
        if found is not None:
            found(len(albums))
    return sorted(albums.items(), key=lambda kv: str(kv[0]).casefold())


def with_covers(folder: Path, files: list) -> list:
    """The album's audio files plus its cover files, so adding or replacing a cover counts as a change."""
    return list(files) + [folder / n for n in COVER_NAMES if (folder / n).is_file()]


def signature(files: list) -> str:
    h = hashlib.sha1()
    for p in files:
        try:
            st = p.stat()
        except OSError:
            continue
        h.update(f"{p.name}\0{st.st_size}\0{int(st.st_mtime)}\n".encode())
    return h.hexdigest()


def _majority(values: list) -> str:
    values = [v for v in values if v]
    return Counter(values).most_common(1)[0][0] if values else ""


def _distinct(values) -> list:
    """The non-empty values, each once (ignoring case), in the order first seen."""
    seen, out = set(), []
    for v in values:
        v = str(v or "").strip()
        if v and v.casefold() not in seen:
            seen.add(v.casefold())
            out.append(v)
    return out


def describe_format(t: dict) -> str:
    fmt = t.get("format", "?")
    if fmt in LOSSLESS and t.get("bits") and t.get("rate"):
        return f"{fmt} {t['bits']}/{t['rate'] / 1000:g}"
    if t.get("kbps"):
        return f"{fmt} {t['kbps']}"
    return fmt


def read_album(root: Path, folder: Path, files: list) -> AlbumEntry:
    rel = folder.relative_to(root).as_posix()
    tracks = []
    for p in files:
        if p.suffix.lower() in (".flac", ".mp3", ".m4a", ".ogg", ".opus"):
            try:
                tracks.append(read_track(p))
            except Exception:                           # one unreadable file must not hide the album
                tracks.append({"path": str(p), "format": p.suffix.lstrip(".").upper(), "error": "unreadable"})
        else:
            tracks.append({"path": str(p), "format": p.suffix.lstrip(".").upper()})
    parts = Path(rel).parts
    folder_artist = parts[0] if len(parts) > 1 else ""
    folder_album = parts[-1]
    year_m = YEAR_SUFFIX.search(folder_album)
    artist = _majority([t.get("albumartist") for t in tracks]) or _majority([t.get("artist") for t in tracks]) or folder_artist
    album = _majority([t.get("album") for t in tracks]) or clean_album_title(YEAR_SUFFIX.sub("", folder_album)) or folder_album
    date = _majority([str(t.get("date") or "") for t in tracks])
    year = (re.match(r"\d{4}", date).group(0) if re.match(r"\d{4}", date) else "") or (year_m.group(1) if year_m else "")
    formats = Counter(describe_format(t) for t in tracks)
    entry = AlbumEntry(folder=rel, artist=artist or "?", album=album, year=year, tracks=len(files),
                       discs=max([t.get("disc") or 1 for t in tracks] or [1]), formats=dict(formats),
                       lossless=all(t.get("format") in LOSSLESS for t in tracks),
                       size=sum(p.stat().st_size for p in files if p.exists()),
                       seconds=round(sum(t.get("length") or 0 for t in tracks), 1),
                       has_cover=any(t.get("has_art") for t in tracks) or any((folder / n).exists() for n in COVER_NAMES),
                       signature=signature(with_covers(folder, files)),
                       genres=_distinct(t.get("genre") for t in tracks),
                       labels=_distinct(x for t in tracks for x in (t.get("label"), t.get("catalog"))),
                       songs=[t.get("title") or "" for t in tracks],
                       guests=[a for a in _distinct(t.get("artist") for t in tracks) if a.casefold() != (artist or "").casefold()])
    try:
        entry.issues = validate(folder) if any(p.suffix.lower() in (".flac", ".mp3") for p in files) else []
    except Exception as exc:
        entry.issues = [f"{CHECK_FAILED} ({type(exc).__name__})"]
    return entry


def cache_path(root: Path) -> Path:
    key = hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:16]
    return paths.data_dir() / ".library-index" / f"{key}.json"


def load_cache(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") == CACHE_VERSION:
            return {a["folder"]: a for a in data.get("albums", [])}
    except (OSError, ValueError, KeyError):
        pass
    return {}


def scan(root: Path, *, cache: Optional[Path] = None, use_cache: bool = True,
         progress: Optional[Callable[[dict], None]] = None, cancel: Optional[CancelCheck] = None) -> LibraryIndex:
    root = Path(root).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"{root} is not a folder")
    cache = cache or cache_path(root)
    known = load_cache(cache) if use_cache else {}
    finding = (lambda n: progress({"stage": "library", "step": "finding", "found": n})) if progress is not None else None
    folders = album_folders(root, found=finding, cancel=cancel)
    index = LibraryIndex(root=str(root), scanned_at=time.time())
    fields = set(AlbumEntry.__dataclass_fields__)
    for i, (folder, files) in enumerate(folders, 1):
        check(cancel)
        rel = folder.relative_to(root).as_posix()
        old = known.get(rel)
        failed = any(str(i).startswith(CHECK_FAILED) for i in (old or {}).get("issues", []))   # retried, not remembered
        if old and not failed and old.get("signature") == signature(with_covers(folder, files)):
            entry = AlbumEntry(**{k: v for k, v in old.items() if k in fields})
        else:
            entry = read_album(root, folder, files)
        index.albums.append(entry)
        if progress is not None:
            progress({"stage": "library", "step": "reading", "index": i, "total": len(folders), "folder": rel})
    index.albums.sort(key=lambda a: (a.artist.casefold(), a.year, a.album.casefold()))
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(cache.name + ".tmp")
        tmp.write_text(json.dumps({"version": CACHE_VERSION, "root": str(root),
                                   "albums": [asdict(a) for a in index.albums]}), encoding="utf-8")
        os.replace(tmp, cache)
    except OSError:
        pass
    return index
