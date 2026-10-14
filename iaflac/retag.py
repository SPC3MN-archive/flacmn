"""Edit the tags of an album that is already in the library: by hand, or from a MusicBrainz release.

The album is read into an AlbumMeta whose tracks are keyed by their path inside the album folder. A MusicBrainz
release is applied to it the same way as for a download (musicbrainz.apply_release: album fields always, track
titles and numbers when the counts agree), and `preview` lists every value that would change, so nothing is written
before it has been seen. `write` then changes only the tags the editor knows about (titles, artists, album, numbers,
date, genre, label, catalogue number and the MusicBrainz IDs): every other tag in each file (ReplayGain, comments,
lyrics...) and the embedded cover stay as they were, and only the files whose tags actually change are touched.
Optionally the files are renamed to match ("03 - Title.flac", in the album's own naming style); a name already taken
is never overwritten.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import paths
from .model import natural_key
from .tagging import AlbumMeta, TrackMeta, diff, read_track, write_flac, write_mp3

EDITABLE = (".flac", ".mp3")
OTHER_AUDIO = (".m4a", ".ogg", ".opus", ".wav", ".aiff", ".aif", ".wv", ".ape", ".wma", ".aac")
ALBUM_KEYS = ("albumartist", "album", "date", "genre", "label", "catalog")


class RetagError(RuntimeError):
    pass


def _majority(values: list) -> str:
    values = [v for v in values if v]
    return max(set(values), key=values.count) if values else ""


def album_files(folder: Path) -> tuple:
    """([editable track paths], [other audio files that cannot be tagged here]), relative to the folder, in order."""
    files = sorted((p for p in folder.rglob("*") if p.is_file() and not paths.is_hidden(p, folder)),
                   key=lambda p: natural_key(p.relative_to(folder).as_posix()))
    edit = [p.relative_to(folder).as_posix() for p in files if p.suffix.lower() in EDITABLE]
    other = [p.relative_to(folder).as_posix() for p in files if p.suffix.lower() in OTHER_AUDIO]
    return edit, other


def read_album(folder: Path) -> tuple:
    """(AlbumMeta of the album as its files say, [files that could not be read or tagged]). Each track's `source` is
    its path inside the album folder."""
    folder = Path(folder)
    edit, other = album_files(folder)
    tags, skipped = [], list(other)
    for rel in edit:
        t = read_track(folder / rel)
        if t.get("error"):
            skipped.append(rel)
            continue
        t["rel"] = rel
        tags.append(t)
    if not tags:
        raise RetagError("no FLAC or MP3 files whose tags can be edited" + (f" ({len(other)} other audio files)" if other else ""))
    meta = AlbumMeta(albumartist=_majority([t.get("albumartist") for t in tags]) or _majority([t.get("artist") for t in tags]),
                     album=_majority([t.get("album") for t in tags]), date=_majority([str(t.get("date") or "") for t in tags]),
                     genre=_majority([t.get("genre") for t in tags]), label=_majority([t.get("label") for t in tags]),
                     catalog=_majority([t.get("catalog") for t in tags]),
                     mb_release_id=_majority([t.get("mb_release_id") for t in tags]),
                     mb_releasegroup_id=_majority([t.get("mb_releasegroup_id") for t in tags]),
                     mb_albumartist_id=_majority([t.get("mb_albumartist_id") for t in tags]),
                     source_url=_majority([t.get("source_url") for t in tags]))
    for i, t in enumerate(tags, 1):
        artist = t.get("artist") or ""
        meta.tracks.append(TrackMeta(source=t["rel"], title=t.get("title") or "", track=t.get("track") or 0,
                                     disc=t.get("disc") or 1, artist="" if artist == meta.albumartist else artist,
                                     length=t.get("length") or None, mb_track_id=t.get("mb_track_id") or "",
                                     mb_recording_id=t.get("mb_recording_id") or ""))
    meta.sources = {k: "tags" for k in ALBUM_KEYS}
    return meta, skipped


def from_release(meta: AlbumMeta, release: dict) -> tuple:
    """(new AlbumMeta, warnings): `meta` with a MusicBrainz release applied. Tracks keep their files."""
    from . import musicbrainz as mb
    return mb.apply_release(meta, release)


def preview(old: AlbumMeta, new: AlbumMeta) -> list:
    """Every value that would change, as tagging.diff gives them (tracks paired by their file)."""
    return diff(old, new)


# ---- writing ------------------------------------------------------------------------------------------------------

def _current(path: Path) -> dict:
    """The tags of one file that `write` manages, as Vorbis-style keys, so the ones the editor does not change are
    written back as they were."""
    if path.suffix.lower() == ".flac":
        from mutagen.flac import FLAC
        from .tagging import MANAGED
        audio = FLAC(str(path))
        return {k.upper(): v[0] for k, v in (audio.tags or {}).items() if k.upper() in MANAGED and v} if audio.tags else {}
    t = read_track(path)
    out = {"TITLE": t.get("title"), "ARTIST": t.get("artist"), "ALBUMARTIST": t.get("albumartist"), "ALBUM": t.get("album"),
           "TRACKNUMBER": str(t.get("track") or ""), "TRACKTOTAL": str(t.get("tracktotal") or ""),
           "DISCNUMBER": str(t.get("disc") or ""), "DISCTOTAL": str(t.get("disctotal") or ""), "DATE": str(t.get("date") or ""),
           "GENRE": t.get("genre"), "LABEL": t.get("label"), "MUSICBRAINZ_ALBUMID": t.get("mb_release_id"),
           "MUSICBRAINZ_RELEASEGROUPID": t.get("mb_releasegroup_id"), "MUSICBRAINZ_ALBUMARTISTID": t.get("mb_albumartist_id"),
           "IAFLAC_SOURCE": t.get("source_url")}
    return {k: v for k, v in out.items() if v}


def _wanted(meta: AlbumMeta, t: TrackMeta, totals: dict, disctotal: int) -> dict:
    """The values the editor sets for one track ("" removes a tag)."""
    return {"TITLE": t.title, "ARTIST": t.artist or meta.albumartist, "ALBUMARTIST": meta.albumartist, "ALBUM": meta.album,
            "TRACKNUMBER": str(t.track) if t.track else "", "TRACKTOTAL": str(totals.get(t.disc) or ""),
            "DISCNUMBER": str(t.disc) if disctotal > 1 or t.disc > 1 else "", "DISCTOTAL": str(disctotal) if disctotal > 1 else "",
            "DATE": meta.date, "GENRE": meta.genre, "LABEL": meta.label, "CATALOGNUMBER": meta.catalog,
            "MUSICBRAINZ_ALBUMID": meta.mb_release_id, "MUSICBRAINZ_RELEASEGROUPID": meta.mb_releasegroup_id,
            "MUSICBRAINZ_ALBUMARTISTID": meta.mb_albumartist_id, "MUSICBRAINZ_RELEASETRACKID": t.mb_track_id,
            "MUSICBRAINZ_TRACKID": t.mb_recording_id}


ALIASES = {"ALBUMARTIST": "ALBUM ARTIST", "TRACKTOTAL": "TOTALTRACKS", "DISCTOTAL": "TOTALDISCS", "DATE": "YEAR"}


def write(folder: Path, meta: AlbumMeta, *, rename: bool = False) -> dict:
    """Write `meta` into the album's files. Only the managed tags change, and only in files where one of them actually
    differs. TRACKTOTAL is the larger of what the tags said and the number of tracks there, so an incomplete album keeps
    saying it is incomplete. With `rename`, files are renamed to '<number> - <title>' in the album's own style.
    Returns {"written": [files], "unchanged": n, "renamed": [[old, new]], "failures": [...]}."""
    folder = Path(folder)
    known = {t.source for t in meta.tracks}
    for src in known:
        p = (folder / src).resolve()
        if not p.is_relative_to(folder.resolve()) or not p.is_file() or p.suffix.lower() not in EDITABLE:
            raise RetagError(f"{src!r} is not one of this album's FLAC or MP3 files")
    old_totals: dict = {}
    for src in known:
        t = read_track(folder / src)
        old_totals[t.get("disc") or 1] = max(old_totals.get(t.get("disc") or 1, 0), t.get("tracktotal") or 0)
    totals = {}
    for t in meta.tracks:
        count = sum(1 for x in meta.tracks if x.disc == t.disc)
        top = max((x.track for x in meta.tracks if x.disc == t.disc), default=0)
        mb_count = meta.sources.get("tracks") == "musicbrainz"
        totals[t.disc] = count if mb_count else max(count, top, old_totals.get(t.disc, 0))
    disctotal = max((t.disc for t in meta.tracks), default=1)
    out = {"written": [], "unchanged": 0, "renamed": [], "failures": []}
    for t in meta.tracks:
        path = folder / t.source
        try:
            current = _current(path)
            merged = dict(current)
            for key, value in _wanted(meta, t, totals, disctotal).items():
                if value:
                    merged[key] = value
                else:
                    merged.pop(key, None)
                merged.pop(ALIASES.get(key, "\0"), None)        # one spelling of each tag, not two that disagree
            if merged == current:
                out["unchanged"] += 1
                continue
            (write_flac if path.suffix.lower() == ".flac" else write_mp3)(path, merged)
            out["written"].append(t.source)
        except Exception as exc:                                   # one file's trouble never stops the others
            out["failures"].append(f"{t.source}: {type(exc).__name__}: {exc}")
    if rename:
        out["renamed"] = rename_files(folder, meta)
    return out


def _other_file(new: Path, old: Path) -> bool:
    """Is the name `new` taken by a file other than `old`? On Windows and macOS a change of letter case only
    ("so what" -> "So What") names the same file, which is not a clash."""
    return new.exists() and not os.path.samefile(new, old)


def plan_renames(folder: Path, meta: AlbumMeta) -> list:
    """[[old, new]]: the files that renaming would rename to '<number> - <title><ext>', in the style the album already
    uses, inside their own subfolder. A name taken by another file is left out (that file is never overwritten)."""
    from .health import track_name
    existing = [t.source for t in meta.tracks]
    multi = len({t.disc for t in meta.tracks}) > 1
    plan, taken = [], set()
    for t in sorted(meta.tracks, key=lambda x: (x.disc, x.track)):
        if not t.title or not t.track:
            continue
        old = Path(folder) / t.source
        new = old.with_name(track_name(existing, t.disc, t.track, t.title, old.suffix, multi))
        if new.name == old.name or new.name.casefold() in taken:
            continue
        if _other_file(new, old):
            continue
        taken.add(new.name.casefold())
        plan.append([t.source, new.relative_to(folder).as_posix()])
    return plan


def rename_files(folder: Path, meta: AlbumMeta) -> list:
    """Carry out plan_renames. Returns [[old, new]] for the files renamed."""
    by_source = {t.source: t for t in meta.tracks}
    done = []
    for old, new in plan_renames(folder, meta):
        if _other_file(Path(folder) / new, Path(folder) / old):    # appeared meanwhile: never overwritten
            continue
        os.replace(Path(folder) / old, Path(folder) / new)
        by_source[old].source = new
        done.append([old, new])
    return done


def meta_from_dict(data: dict) -> AlbumMeta:
    m = AlbumMeta.from_dict(data)
    for t in m.tracks:
        t.track, t.disc = int(t.track or 0), int(t.disc or 1)
    return m
