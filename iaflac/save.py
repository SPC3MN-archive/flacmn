"""Saving an album into the library without any prompts: plan it, then carry the plan out.

    plan = plan_save(cand, library, meta)        # nothing touches the disk; shows folder, file names, size
    result = apply_save(client, cand, plan)      # download + verify, then tag, rename, cover art, check

The download half is download.library_get (every file to a .part name, checked against archive.org's md5/sha1,
resumable, never merging into a folder that holds other files). Only when every file has verified are the tracks
tagged from `meta`, renamed into the Volumio layout (tagging.py) and re-tested with `flac -t`. A cancelled or failed
download leaves its .part files and the incomplete marker, so calling apply_save again resumes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import download, paths, tagging
from .events import CancelCheck, Progress
from .model import track_files
from .tagging import AlbumMeta

MEDIA_WORD = {"flac": "FLAC", "wav": "WAV → FLAC", "aiff": "AIFF → FLAC", "ape": "APE → FLAC", "wv": "WavPack → FLAC",
              "m4a": "M4A", "mp3": "MP3 (lossy)"}


@dataclass
class SaveOptions:
    art: str = "cover"               # cover | all | none: which of the item's images to fetch
    rip: str = "skip"                # skip | keep: rip logs and cue sheets (a .cue makes MPD/Volumio list the album twice)
    embed_art: bool = True
    cover_file: bool = True          # also write cover.jpg beside the tracks
    online_cover: bool = True        # no cover in the upload: use the Cover Art Archive's (needs a MusicBrainz match)


@dataclass
class PlannedTrack:
    source: str                      # archive.org file name
    name: str                        # final file name in the album folder
    size: int
    title: str
    disc: int
    track: int


@dataclass
class SavePlan:
    identifier: str
    media: str
    folder: Path
    meta: AlbumMeta
    tracks: list = field(default_factory=list)       # [PlannedTrack]
    extra_files: list = field(default_factory=list)  # [(name, size)] cover, scans, rip files
    total_bytes: int = 0
    cover: str = ""                  # "archive.org: cover.jpg" | "Cover Art Archive" | ""
    warnings: list = field(default_factory=list)
    folder_exists: bool = False

    def to_dict(self) -> dict:
        return {"identifier": self.identifier, "media": self.media, "media_label": MEDIA_WORD.get(self.media, self.media),
                "folder": str(self.folder), "meta": self.meta.to_dict(),
                "tracks": [t.__dict__ for t in self.tracks], "extra_files": [list(x) for x in self.extra_files],
                "total_bytes": self.total_bytes, "cover": self.cover, "warnings": list(self.warnings),
                "folder_exists": self.folder_exists}


@dataclass
class SaveResult:
    ok: bool
    folder: Path
    files: list = field(default_factory=list)        # final track paths
    downloaded: int = 0
    already_had: int = 0
    failures: list = field(default_factory=list)     # the download did not complete: retry resumes it
    problems: list = field(default_factory=list)     # saved, but something to look at (tagging, art)
    issues: list = field(default_factory=list)       # tagging.validate on the finished folder
    cover: Optional[str] = None

    def to_dict(self) -> dict:
        return {"ok": self.ok, "folder": str(self.folder), "files": [str(p) for p in self.files],
                "downloaded": self.downloaded, "already_had": self.already_had, "failures": list(self.failures),
                "problems": list(self.problems), "issues": list(self.issues), "cover": self.cover}


def _check_meta(meta: AlbumMeta, item) -> list:
    problems = []
    if not meta.albumartist.strip() or not meta.album.strip():
        problems.append("the album needs an artist and a title")
    sources = {f.name for f in track_files(item.files, item.media)}
    unknown = [t.source for t in meta.tracks if t.source not in sources]
    if unknown:
        problems.append(f"{len(unknown)} track(s) are not files of this item ({unknown[0]})")
    missing = sources - {t.source for t in meta.tracks}
    if missing:
        problems.append(f"{len(missing)} of the item's tracks have no tags yet ({sorted(missing)[0]})")
    return problems


def _converts(cand) -> bool:
    """Is this upload converted to FLAC when saved? Every lossless format is; an .m4a only when it holds ALAC."""
    from .altmedia import is_lossless
    media = cand.item.media
    return media != "flac" and is_lossless(media, cand.alt_stats)


def plan_save(cand, library: Path, meta: Optional[AlbumMeta] = None, opts: Optional[SaveOptions] = None) -> SavePlan:
    """What saving `cand` would do, without touching the disk. `meta` defaults to tagging.from_candidate(cand).
    Raises ValueError when the names cannot make a folder, or download.DownloadError for an unsafe destination."""
    opts = opts or SaveOptions()
    meta = meta or tagging.from_candidate(cand)
    item = cand.item
    problems = _check_meta(meta, item)
    if problems:
        raise ValueError("; ".join(problems))
    library = Path(library)
    folder = tagging.album_folder(library, meta)
    convert = _converts(cand)
    res = download.plan_library(item, library, meta.albumartist, meta.album, art=opts.art, rip=opts.rip,
                                track_names="keep", folder=folder, media=item.media, convert=convert)
    ext = ".flac" if convert else None
    ext_of = {t.source: ext or Path(t.source).suffix for t in meta.tracks}
    names = tagging.planned_names(meta, ext_of)
    by_source = {pf.info.name: pf for pf in res.planned}
    plan = SavePlan(identifier=item.identifier, media=item.media, folder=folder, meta=meta, total_bytes=res.total_bytes,
                    warnings=list(res.skipped), folder_exists=folder.exists() and any(not paths.is_hidden(p) for p in folder.iterdir()))
    for t in meta.tracks:
        pf = by_source.get(t.source)
        plan.tracks.append(PlannedTrack(t.source, names[t.source], pf.info.size if pf else 0, t.title, t.disc, t.track))
    tracks = set(names)
    for pf in res.planned:
        if pf.info.name not in tracks:
            plan.extra_files.append((pf.path.relative_to(folder.resolve()).as_posix(), pf.info.size))
    if res.cover is not None:
        plan.cover = f"archive.org: {res.cover.name}"
    elif opts.online_cover and meta.mb_release_id:
        plan.cover = "Cover Art Archive"
    else:
        plan.warnings.append("no cover art: the upload has none" + ("" if meta.mb_release_id else
                             " (match it on MusicBrainz to fetch one from the Cover Art Archive)"))
    if item.media == "mp3":
        plan.warnings.append("lossy: these are MP3 files and stay MP3")
    elif item.media == "m4a" and not convert:
        plan.warnings.append("lossy: these are AAC files (.m4a) and stay AAC")
    if plan.folder_exists:
        plan.warnings.append(f"{folder} already exists and has files: saving resumes an unfinished save, "
                             "or stops rather than mix two albums")
    return plan


def apply_save(client, cand, plan: SavePlan, opts: Optional[SaveOptions] = None, *, mb_client=None,
               progress: Optional[Progress] = None, cancel: Optional[CancelCheck] = None) -> SaveResult:
    """Download, verify, tag and lay out one album. Raises events.Cancelled when cancelled (resumable) and
    download.DownloadError when the folder holds someone else's files."""
    opts = opts or SaveOptions()
    item, meta, folder = cand.item, plan.meta, plan.folder
    library = folder.parent.parent
    emit = progress or (lambda e: None)
    res = download.library_get(client, item, library, meta.albumartist, meta.album, yes=True, art=opts.art,
                               rip=opts.rip, track_names="keep", folder=folder, media=item.media,
                               convert=_converts(cand), progress=progress, cancel=cancel)
    out = SaveResult(ok=False, folder=folder, downloaded=res.downloaded, already_had=res.already_had,
                     failures=list(res.failures), problems=list(res.warnings))
    if res.failures:
        return out
    emit({"stage": "tagging", "identifier": item.identifier})
    paths = {pf.info.name: (pf.final or pf.path) for pf in res.planned}
    cover_bytes, cover_path = None, None
    if res.cover is not None:
        cover_path = next((pf.path for pf in res.planned if pf.info is res.cover), None)
        if cover_path is not None and cover_path.exists():
            cover_bytes = cover_path.read_bytes()
    if cover_bytes is None and opts.online_cover and meta.mb_release_id and mb_client is not None:
        emit({"stage": "cover", "identifier": item.identifier})
        cover_bytes = mb_client.cover(meta.mb_release_id, meta.mb_releasegroup_id)
        if cover_bytes is None:
            out.problems.append("the Cover Art Archive has no front cover for this release")
    applied = tagging.apply(folder, meta, paths, cover_bytes, write_cover_file=opts.cover_file, embed=opts.embed_art,
                            verify=download.flac_test)
    if cover_path is not None and applied.cover is not None and cover_path.resolve() != applied.cover.resolve():
        cover_path.unlink(missing_ok=True)          # cover.png converted to cover.jpg: keep one copy
    out.files, out.problems = applied.files, out.problems + applied.problems
    out.cover = str(applied.cover) if applied.cover else None
    out.issues = tagging.validate(folder)
    out.ok = True
    emit({"stage": "saved", "identifier": item.identifier, "folder": str(folder), "issues": len(out.issues)})
    return out
