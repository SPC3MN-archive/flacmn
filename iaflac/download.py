"""`get`: gated, resumable download of one item's FLACs and rip sidecars.

Nothing is written unless the caller passes yes=True; without it we only return the plan (files and
sizes). Every file goes to `<name>.part`, is checked against archive.org's own md5/sha1, and only then
moved into place; FLACs are also decode-tested with `flac -t`. Only whitelisted file types are fetched,
and no name from the archive can place a file outside the destination folder.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .ia_client import IAClient, IAError
from .model import FileInfo, ItemInfo, file_kind
from .names import folder_component

# roles we will fetch. Cover art is included by default but only originals (never IA's derived thumbnails).
AUDIO_ROLES = {"flac"}
RIP_ROLES = {"log", "cue", "accurip", "ffp", "md5", "sfv", "aucdtect", "playlist", "text"}
ART_ROLES = {"art"}
SKIP_NAMES = re.compile(r"(__ia_thumb\.jpg|_spectrogram\.png|_meta\.(xml|sqlite)|_files\.xml|_reviews\.xml|\.torrent)$", re.I)
CHUNK = 1 << 20
IGNORABLE = {".DS_Store"}
INCOMPLETE = ".iaflac-incomplete"                    # marker left in an album folder until every file has verified
LIB_RIP_ROLES = {"log", "cue", "accurip", "aucdtect"}    # provenance evidence worth keeping next to the audio
COVER_EXTS = (".jpg", ".jpeg", ".png")
NOT_FRONT = re.compile(r"back|inlay|disc|cd\d*|tray|book|inside|inner|scan|spine|obi|sticker", re.I)


class DownloadError(RuntimeError):
    pass


@dataclass
class PlannedFile:
    info: FileInfo
    path: Path


@dataclass
class GetResult:
    identifier: str
    folder: Path
    planned: list = field(default_factory=list)      # [PlannedFile]
    skipped: list = field(default_factory=list)      # [str] why a file was left out
    downloaded: int = 0
    already_had: int = 0
    failures: list = field(default_factory=list)     # [str]
    warnings: list = field(default_factory=list)     # [str] kept but worth knowing
    executed: bool = False
    artist: str = ""                                  # library layout only
    album: str = ""
    cover: Optional[FileInfo] = None

    @property
    def total_bytes(self) -> int:
        return sum(p.info.size for p in self.planned)

    @property
    def ok(self) -> bool:
        return self.executed and not self.failures


def clean_component(part: str) -> str:
    part = re.sub(r"[\x00-\x1f<>:\"|?*\\]", "_", part).strip().rstrip(".")
    return part[:150]


def safe_relative(name: str) -> Optional[Path]:
    """The archive's file name as a relative path, or None if it could escape (absolute, '..', empty parts)."""
    if not name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    cleaned = [clean_component(p) for p in parts]
    if any(not p for p in cleaned):
        return None
    return Path(*cleaned)


def folder_name(item: ItemInfo) -> str:
    """One flat folder name from an uploader-controlled title: no separators, never '.' or '..'."""
    base = clean_component((item.title or item.identifier).replace("/", "-")).lstrip(". ")
    ident = clean_component(item.identifier.replace("/", "-")) or "item"
    return f"{base[:80] or ident} [{ident}]"


def plan_get(item: ItemInfo, dest: Path, *, art: bool = True) -> GetResult:
    folder = dest / folder_name(item)
    res = GetResult(identifier=item.identifier, folder=folder)
    wanted = AUDIO_ROLES | RIP_ROLES | (ART_ROLES if art else set())
    root = folder.resolve()
    if dest.resolve() not in root.parents:
        raise ValueError(f"refusing to use {folder}: it is not inside {dest}")
    entries = []
    for f in sorted(item.files, key=lambda f: f.name):
        role = file_kind(f.name)
        if role not in wanted:
            continue
        if role != "flac" and f.source == "derivative":
            continue
        if SKIP_NAMES.search(f.name):
            continue
        rel = safe_relative(f.name)
        if rel is None:
            res.skipped.append(f"{f.name!r}: unsafe path, refused")
            continue
        entries.append((f, rel))
    # many items keep everything inside one album-named subfolder; don't nest it inside our own album folder.
    # (Multi-disc items have several top folders, so their Disc 1/, Disc 2/ layout is kept.)
    tops = {rel.parts[0] for _, rel in entries}
    if len(tops) == 1 and entries and all(len(rel.parts) > 1 for _, rel in entries):
        entries = [(f, Path(*rel.parts[1:])) for f, rel in entries]
    for f, rel in entries:
        target = (folder / rel).resolve()
        if root not in target.parents:
            res.skipped.append(f"{f.name!r}: would land outside the destination, refused")
            continue
        res.planned.append(PlannedFile(f, target))
    return res


def _digest(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def matches_manifest(path: Path, f: FileInfo) -> Optional[bool]:
    """True/False against IA's md5 (sha1 if that's all there is); None if the manifest has neither."""
    if not path.exists():
        return False
    if f.size and path.stat().st_size != f.size:
        return False
    if f.md5:
        return _digest(path, "md5") == f.md5
    if f.sha1:
        return _digest(path, "sha1") == f.sha1
    return None


def flac_test(path: Path) -> tuple:
    flac = shutil.which("flac")
    if not flac:
        return None, "flac not installed, decode test skipped"
    proc = subprocess.run([flac, "-t", "-s", str(path)], capture_output=True)
    if proc.returncode == 0:
        return True, ""
    msg = next((ln.strip() for ln in proc.stderr.decode("utf-8", "replace").splitlines() if ln.strip()), "flac -t failed")
    return False, msg


def _fetch(client: IAClient, identifier: str, pf: PlannedFile) -> None:
    """Download to <name>.part (resuming if a partial exists), leaving the finished bytes in place."""
    part = pf.path.with_name(pf.path.name + ".part")
    pf.path.parent.mkdir(parents=True, exist_ok=True)
    start = part.stat().st_size if part.exists() else 0
    if pf.info.size and start > pf.info.size:
        part.unlink()
        start = 0
    if pf.info.size and start == pf.info.size:
        return
    resp = client.open_stream(identifier, pf.info.name, start=start)
    try:
        mode = "ab" if start and resp.status_code == 206 else "wb"     # a 200 means the server restarted the file
        with open(part, mode) as out:
            for chunk in resp.iter_content(CHUNK):
                out.write(chunk)
                client.bytes_read += len(chunk)
    finally:
        resp.close()


def get_item(client: IAClient, item: ItemInfo, dest: Path, *, yes: bool = False, art: bool = True,
             log: Callable[[str], None] = lambda m: None) -> GetResult:
    res = plan_get(item, dest, art=art)
    if not yes:
        return res
    return _execute(client, item.identifier, res, log)


def _execute(client: IAClient, identifier: str, res: GetResult, log: Callable[[str], None]) -> GetResult:
    res.executed = True
    for i, pf in enumerate(res.planned, 1):
        f, part = pf.info, pf.path.with_name(pf.path.name + ".part")
        if matches_manifest(pf.path, f) is True:
            res.already_had += 1
            log(f"  [{i}/{len(res.planned)}] have {pf.path.name}")
            continue
        log(f"  [{i}/{len(res.planned)}] {pf.path.name} ({f.size / 1e6:.1f} MB)")
        try:
            _fetch(client, identifier, pf)
        except (IAError, OSError) as exc:
            res.failures.append(f"{f.name}: download failed ({exc}); rerun to resume")
            continue
        verdict = matches_manifest(part, f)
        if verdict is False:
            part.unlink(missing_ok=True)
            res.failures.append(f"{f.name}: size/checksum differs from archive.org's manifest; partial file deleted")
            continue
        part.replace(pf.path)
        res.downloaded += 1
        if file_kind(f.name) == "flac":
            ok, msg = flac_test(pf.path)
            if ok is False:
                res.warnings.append(f"{f.name}: matches archive.org's checksum but fails flac -t ({msg}); the archive's copy is damaged")
    return res


# ---- library layout: <library>/<Artist>/<Album>/ ----------------------------------------------------

def pick_cover(item: ItemInfo) -> Optional[FileInfo]:
    """The item's front cover: cover > *cover* > front > folder/albumart > any image that isn't a back/disc/booklet scan."""
    best, best_key = None, None
    for f in item.files:
        if file_kind(f.name) != "art" or f.source == "derivative" or SKIP_NAMES.search(f.name):
            continue
        if not f.name.lower().endswith(COVER_EXTS):
            continue
        base = re.sub(r"\.[^.]+$", "", f.name.rsplit("/", 1)[-1]).lower()
        if base == "cover":
            score = 0
        elif "cover" in base and not NOT_FRONT.search(base.replace("cover", "")):
            score = 1
        elif "front" in base:
            score = 2
        elif base in ("folder", "albumart", "album art", "artwork", "album"):
            score = 3
        elif NOT_FRONT.search(base):
            score = 20
        else:
            score = 9
        key = (score, -min(f.size, 3_000_000))
        if best_key is None or key < best_key:
            best, best_key = f, key
    return best if best_key is not None and best_key[0] < 20 else None     # never a back/disc/booklet scan


def plan_library(item: ItemInfo, library: Path, artist: str, album: str, *, art: str = "cover",
                 rip: str = "keep") -> GetResult:
    """Where everything would go, without touching the disk. art: cover | all | none. rip: keep | skip."""
    a_dir, al_dir = folder_component(artist), folder_component(album)
    if not a_dir or not al_dir:
        raise DownloadError(f"cannot make folder names from artist {artist!r} and album {album!r}")
    folder = library / a_dir / al_dir
    res = GetResult(identifier=item.identifier, folder=folder, artist=artist, album=album)
    root = folder.resolve()
    if library.resolve() not in root.parents:
        raise DownloadError(f"refusing {folder}: it is not inside {library}")
    cover = pick_cover(item) if art in ("cover", "all") else None
    entries = []
    for f in sorted(item.files, key=lambda f: f.name):
        role = file_kind(f.name)
        if SKIP_NAMES.search(f.name) or (role != "flac" and f.source == "derivative"):
            continue
        wanted = (role == "flac" or (rip == "keep" and role in LIB_RIP_ROLES)
                  or (art == "all" and role == "art" and f is not cover))
        if not wanted:
            continue
        rel = safe_relative(f.name)
        if rel is None:
            res.skipped.append(f"{f.name!r}: unsafe path, refused")
            continue
        entries.append((f, rel))
    tops = {rel.parts[0] for _, rel in entries}
    if len(tops) == 1 and entries and all(len(rel.parts) > 1 for _, rel in entries):
        entries = [(f, Path(*rel.parts[1:])) for f, rel in entries]
    for f, rel in entries:
        target = (folder / rel).resolve()
        if root not in target.parents:
            res.skipped.append(f"{f.name!r}: would land outside the album folder, refused")
            continue
        res.planned.append(PlannedFile(f, target))
    if cover is not None:
        res.cover = cover
        res.planned.append(PlannedFile(cover, (folder / f"cover{Path(cover.name).suffix.lower()}").resolve()))
    return res


def library_get(client: IAClient, item: ItemInfo, library: Path, artist: str, album: str, *, yes: bool = False,
                art: str = "cover", rip: str = "keep", log: Callable[[str], None] = lambda m: None) -> GetResult:
    res = plan_library(item, library, artist, album, art=art, rip=rip)
    if not yes:
        return res
    folder = res.folder
    marker = folder / INCOMPLETE
    if folder.exists():
        # Never merge into, or overwrite, something that is not ours. A folder holding exactly this album's own
        # files (a finished run, or an interrupted one) is fine: verified files are reported as "already had".
        wanted = {pf.path: pf.info for pf in res.planned}
        present = [p.resolve() for p in folder.rglob("*")
                   if p.is_file() and p.name not in IGNORABLE and p.name != INCOMPLETE and not p.name.endswith(".part")]
        foreign = [p for p in present if p not in wanted]
        if foreign and not marker.exists():
            raise DownloadError(f"{folder} already has files that are not part of this album ({foreign[0].name}); "
                                "not merging into your album folder")
        clashes = [p for p in present if p in wanted and matches_manifest(p, wanted[p]) is False]
        if clashes:
            raise DownloadError(f"{clashes[0]} already exists and differs from archive.org's copy; not overwriting it")
    folder.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{item.identifier}\n")
    _execute(client, item.identifier, res, log)
    if not res.failures:
        marker.unlink(missing_ok=True)
    return res
