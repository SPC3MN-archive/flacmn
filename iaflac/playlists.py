"""Playlists: named lists of songs, as in flacmn on the phone, kept in one JSON file in the app's data folder.

A song is its path relative to the music folder, so a playlist keeps working when the library's drive is mounted
somewhere else. A song that is no longer there (moved, renamed, deleted) stays in its playlist, shown as missing.

Its cover: by default the covers of its first four albums, tiled (worked out when shown, so it follows the songs);
or one album's cover, chosen by its folder; or an image you uploaded, kept beside the file as covers/<id>.<ext>.

    {"version": 1, "playlists": [{"id": "3f9c0a1b", "name": "Late night", "tracks": ["Artist/Album/01 - Song.flac"],
                                  "cover": {"album": "Artist/Album"}}]}
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

from . import paths

_lock = threading.Lock()


class PlaylistError(ValueError):
    pass


@dataclass
class Playlist:
    id: str
    name: str
    tracks: list = field(default_factory=list)       # paths relative to the music folder, "/" separators
    cover: dict = field(default_factory=dict)        # {} tiles of the first four albums, {"album": folder} or {"image": file}

    def to_dict(self) -> dict:
        return asdict(self)


def store_path() -> Path:
    return paths.data_dir() / "playlists.json"


def load(path: Optional[Path] = None) -> list:
    try:
        data = json.loads((path or store_path()).read_text(encoding="utf-8"))
        return [Playlist(str(p["id"]), str(p["name"]), [str(t) for t in p.get("tracks", [])],
                         p.get("cover") if isinstance(p.get("cover"), dict) else {})
                for p in data.get("playlists", [])]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []                                    # none made yet, or a file that can't be read


def save(lists: list, path: Optional[Path] = None) -> None:
    path = path or store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"version": 1, "playlists": [p.to_dict() for p in lists]}, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def clean_name(name: str) -> str:
    name = " ".join(str(name or "").split())
    if not name:
        raise PlaylistError("a playlist needs a name")
    return name[:120]


def clean_track(rel: str) -> str:
    """A song's path inside the music folder; anything absolute or climbing out of it is refused."""
    p = PurePosixPath(str(rel).replace("\\", "/"))
    if p.is_absolute() or not p.parts or ".." in p.parts:
        raise PlaylistError(f"{rel!r} is not a song in the music folder")
    return p.as_posix()


class Store:
    """The playlists file, read and written whole under a lock (it is small: names and paths)."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path

    def all(self) -> list:
        return load(self.path)

    def get(self, pid: str) -> Playlist:
        p = next((p for p in self.all() if p.id == pid), None)
        if p is None:
            raise LookupError(f"no playlist {pid}")
        return p

    def _change(self, fn):
        with _lock:
            lists = load(self.path)
            out = fn(lists)
            save(lists, self.path)
            return out

    def create(self, name: str, tracks: Optional[list] = None) -> Playlist:
        name, tracks = clean_name(name), [clean_track(t) for t in tracks or []]

        def go(lists):
            p = Playlist(secrets.token_hex(4), name, tracks)
            lists.append(p)
            return p
        return self._change(go)

    def update(self, pid: str, name: Optional[str] = None, tracks: Optional[list] = None,
               cover: Optional[dict] = None) -> Playlist:
        """Rename, set the whole song list (the order, a song removed), and/or choose the cover: {} for tiles of the
        first four albums, {"album": folder} for one album's cover. An uploaded image is set with set_image."""
        name = clean_name(name) if name is not None else None
        tracks = [clean_track(t) for t in tracks] if tracks is not None else None
        if cover is not None:
            if cover and set(cover) != {"album"}:
                raise PlaylistError("a playlist cover is the album tiles, one album's cover, or an uploaded image")
            cover = {"album": clean_track(cover["album"])} if cover else {}

        def go(lists):
            p = next((p for p in lists if p.id == pid), None)
            if p is None:
                raise LookupError(f"no playlist {pid}")
            if name is not None:
                p.name = name
            if tracks is not None:
                p.tracks = tracks
            if cover is not None:
                self._drop_image(p)
                p.cover = cover
            return p
        return self._change(go)

    def add(self, pid: str, tracks: list) -> Playlist:
        tracks = [clean_track(t) for t in tracks]
        p = self.get(pid)
        return self.update(pid, tracks=p.tracks + tracks)

    def delete(self, pid: str) -> None:
        def go(lists):
            gone = [p for p in lists if p.id == pid]
            if not gone:
                raise LookupError(f"no playlist {pid}")
            self._drop_image(gone[0])
            lists[:] = [p for p in lists if p.id != pid]
        self._change(go)

    # ---- an uploaded cover image ------------------------------------------------------------------------------
    def images_dir(self) -> Path:
        return (Path(self.path).parent if self.path else store_path().parent) / "playlist-covers"

    def image_path(self, p: Playlist) -> Optional[Path]:
        name = p.cover.get("image")
        return self.images_dir() / name if name else None

    def _drop_image(self, p: Playlist) -> None:
        old = self.image_path(p)
        if old is not None:
            old.unlink(missing_ok=True)

    def set_image(self, pid: str, data: bytes) -> Playlist:
        """Keep an uploaded picture (PNG, JPEG, WebP or GIF, up to 15 MB) as the playlist's cover."""
        ext = image_ext(data)
        if not ext:
            raise PlaylistError("that file is not a PNG, JPEG, WebP or GIF picture")
        if len(data) > MAX_IMAGE:
            raise PlaylistError("that picture is over 15 MB")

        def go(lists):
            p = next((p for p in lists if p.id == pid), None)
            if p is None:
                raise LookupError(f"no playlist {pid}")
            self._drop_image(p)
            name = f"{p.id}-{secrets.token_hex(3)}.{ext}"           # a new name each time: no stale cached picture
            self.images_dir().mkdir(parents=True, exist_ok=True)
            tmp = self.images_dir() / f".{name}.tmp"
            tmp.write_bytes(data)
            os.replace(tmp, self.images_dir() / name)
            p.cover = {"image": name}
            return p
        return self._change(go)


MAX_IMAGE = 15 << 20


def image_ext(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] == b"GIF8":
        return "gif"
    return ""
