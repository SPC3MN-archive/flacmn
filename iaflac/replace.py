"""Replace an album you already have (typically MP3s) with a verified FLAC version, without ever risking the original.

The new files are downloaded and verified into a hidden staging folder next to the old album. Only when every file
has verified is the old folder moved to the Trash (never deleted) and the staging folder renamed into its place. If
anything fails before that point the old album has not been touched.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import matching
from .names import clean_album_title

LOSSY = (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wma")
OTHER_LOSSLESS = (".wav", ".aiff", ".aif", ".ape", ".wv", ".alac")
AUDIO = LOSSY + OTHER_LOSSLESS + (".flac",)
IGNORED = {".ds_store", "thumbs.db", "desktop.ini"}
STAGING_PREFIX = ".iaflac-new-"
FALLBACK_TRASH = ".iaflac-replaced"


class ReplaceError(RuntimeError):
    pass


@dataclass
class AlbumFolder:
    path: Path
    audio: Counter = field(default_factory=Counter)        # extension -> file count

    @property
    def tracks(self) -> int:
        return sum(self.audio.values())

    @property
    def has_flac(self) -> bool:
        return self.audio.get(".flac", 0) > 0

    @property
    def replaceable(self) -> bool:
        """Has audio and no FLAC at all: this is what 'replace with FLAC' is for."""
        return self.tracks > 0 and not self.has_flac

    def describe(self) -> str:
        return ", ".join(f"{n} {ext[1:].upper()}" for ext, n in self.audio.most_common()) or "no audio"


def survey(folder: Path) -> AlbumFolder:
    audio: Counter = Counter()
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext in AUDIO and not name.startswith("."):
                audio[ext] += 1
    return AlbumFolder(folder, audio)


def scan(library: Path) -> list:
    """Every <Artist>/<Album> folder in the library that holds audio but no FLAC, sorted by artist and album."""
    found = []
    for artist_dir in sorted((d for d in library.iterdir() if d.is_dir() and not d.name.startswith(".")), key=lambda d: d.name.casefold()):
        for album_dir in sorted((d for d in artist_dir.iterdir() if d.is_dir() and not d.name.startswith(".")), key=lambda d: d.name.casefold()):
            info = survey(album_dir)
            if info.replaceable:
                found.append(info)
    return found


def album_name_from_folder(folder_name: str, artist: str) -> str:
    """'Kid A (Deluxe) - Radiohead' -> 'Kid A (Deluxe)': library folders are sometimes 'Album - Artist'."""
    name = re.sub(rf"\s*-\s*{re.escape(artist)}\s*$", "", folder_name, flags=re.I).strip()
    return clean_album_title(name) or folder_name


def find_existing(library: Path, artist: str, album: str, threshold: float = 0.85) -> Optional[AlbumFolder]:
    """The album folder you already have for this artist and album, or None. The artist folder must match exactly
    (ignoring case); the album folder must match closely, with volume/part numbers agreeing."""
    artist_dir = next((d for d in library.iterdir() if d.is_dir() and d.name.casefold() == artist.casefold()), None) \
        if library.is_dir() else None
    if artist_dir is None:
        return None
    best, best_score = None, 0.0
    for d in artist_dir.iterdir():
        if not d.is_dir() or d.name.startswith("."):
            continue
        score = max(matching.similarity(album, d.name), matching.similarity(album, album_name_from_folder(d.name, artist_dir.name)))
        if score > best_score:
            best, best_score = d, score
    return survey(best) if best is not None and best_score >= threshold else None


def resolve_folder(spec: str, library: Path) -> Path:
    """An album folder given as a path, or as 'Artist/Album' inside the library. It must be inside the library."""
    path = Path(spec).expanduser()
    for candidate in ([path] if path.is_absolute() else [Path.cwd() / path, library / path]):
        if candidate.is_dir():
            resolved, root = candidate.resolve(), library.resolve()
            if root not in resolved.parents or resolved.parent.parent != root:
                raise ReplaceError(f"{spec}: expected an album folder inside {library} (<Artist>/<Album>)")
            return candidate
    raise ReplaceError(f"{spec}: no such album folder (give a path, or Artist/Album inside {library})")


def staging_folder(existing: Path, identifier: str) -> Path:
    return existing.parent / f"{STAGING_PREFIX}{re.sub(r'[^A-Za-z0-9._-]+', '-', identifier)[:60]}"


def trash_destination(existing: Path, library: Path, trash: Optional[Path] = None) -> Path:
    """Where the old folder goes: the user's Trash (recoverable), else a hidden folder inside the library."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{existing.name} (replaced by iaflac {stamp})"
    trash = trash or (Path(os.environ["IAFLAC_TRASH"]) if os.environ.get("IAFLAC_TRASH") else None)
    for base in ([trash] if trash is not None else [Path("~/.Trash").expanduser()]):
        if base.is_dir() and os.access(base, os.W_OK):
            return base / name
    return library / FALLBACK_TRASH / existing.parent.name / name


def carry_over(old: Path, new: Path) -> list:
    """Copy what is in the old folder but is not audio (booklets, notes, a cover the new album lacks) into the new
    one, never overwriting a file the new album already has. Returns the relative names copied."""
    copied = []
    for root, dirs, files in os.walk(old):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.lower() in IGNORED or name.startswith(".") or os.path.splitext(name)[1].lower() in AUDIO:
                continue
            src = Path(root) / name
            rel = src.relative_to(old)
            dst = new / rel
            if dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(str(rel))
    return copied


def swap_in(existing: Path, staging: Path, library: Path, trash: Optional[Path] = None) -> tuple:
    """Put the verified staging folder where the old album was. Returns (where the old folder went, files carried over).
    Order matters: the old folder is moved aside first (a rename in the same place, or a move to the Trash), and if the
    new folder cannot then be moved into place the old one is put back."""
    if not existing.is_dir() or not staging.is_dir():
        raise ReplaceError("the old or the new folder is missing; nothing was changed")
    if (staging / ".iaflac-incomplete").exists():
        raise ReplaceError("the new folder is marked incomplete; nothing was changed")
    carried = carry_over(existing, staging)
    dest = trash_destination(existing, library, trash)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(existing), str(dest))
    try:
        os.rename(staging, existing)
    except OSError as exc:
        shutil.move(str(dest), str(existing))               # put the original back
        raise ReplaceError(f"could not move the new album into place ({exc}); the original was put back") from exc
    return dest, carried
