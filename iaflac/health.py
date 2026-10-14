"""Library Health Check: which albums in the music folder are missing songs, are in a low-quality format, or have no
cover art, and where on the Internet Archive the missing pieces can be found.

Three kinds of problem, each a Finding:

  missing_tracks   gaps in the track numbers, fewer tracks than TRACKTOTAL / DISCTOTAL say, or (with MusicBrainz on)
                   tracks of the album's release that are not in the folder
  low_quality      lossy files (MP3, AAC, Ogg...), lossy files below 220 kbps, lossless below CD resolution, files that
                   don't read, one album mixing lossless and lossy tracks
  missing_art      no embedded cover and no cover.jpg/folder.jpg, or a cover under 500 px
  duplicates       the same album in more than one folder (an MP3 copy and a FLAC copy, say), or the same song twice
                   in one album

Duplicates are merged (plan_merge, apply_merge) into one folder holding the best copy of every song: lossless over
lossy, then the higher resolution or bitrate. The worse copies and the emptied folders go to the Trash (or a hidden
.iaflac-replaced/ folder in the library when there is no usable Trash), never deleted, and a merge is refused if the
folders changed after its plan was shown.

Checking reads only tags and headers, offline, except the MusicBrainz tracklist comparison (opt-in: one search and a
few lookups per album at MusicBrainz's one request a second, cached for a week). Each album's offline facts are cached
by its files' sizes and modification times, like the library index, so a second check only re-reads what changed.

Searching the Internet Archive for an album's problems (search_archive) is a separate, per-album step, and fixing is
separate again: fetch_tracks downloads chosen tracks from an upload into the album folder and save_cover writes a
cover.jpg. Both only ever add files: neither replaces, renames or deletes anything already there.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from . import binaries, matching, paths
from .events import CancelCheck, check
from .library_index import AUDIO, LOSSLESS, YEAR_SUFFIX, _majority, album_folders
from .model import natural_key
from .names import clean_album_title, folder_component
from .quality import HIGH_MP3_KBPS
from .tagging import COVER_NAMES, DISC_DIR, folder_cover, number_from_name, parse_number, read_track, title_from_name

MISSING, QUALITY, ART, DUPES = "missing_tracks", "low_quality", "missing_art", "duplicates"
KINDS = (MISSING, QUALITY, ART, DUPES)
LABEL = {MISSING: "Missing songs", QUALITY: "Low quality", ART: "Missing art", DUPES: "Duplicates"}
LOSSY = {"MP3", "M4A", "AAC", "OGG", "OPUS", "WMA"}
TAGGED = (".flac", ".mp3", ".m4a", ".ogg", ".opus")
MIN_COVER_PX = 500                   # below this a cover looks soft on a tablet or TV
TITLE_MATCH = 0.85
CACHE_VERSION = 3          # 2: track MusicBrainz IDs; 3: a release MusicBrainz matched is never cached as the tags' own


class HealthError(RuntimeError):
    pass


@dataclass
class Finding:
    kind: str                        # one of KINDS
    severity: str                    # "bad" or "warn"
    text: str                        # one sentence for people
    missing: list = field(default_factory=list)      # missing_tracks: [{"disc", "track", "title"}] (title "" when unknown)
    folders: list = field(default_factory=list)      # duplicates: every folder holding the album, this one included
                                                     # (empty for a song twice inside this one folder)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AlbumHealth:
    folder: str                      # relative to the library, forward slashes
    artist: str
    album: str
    year: str = ""
    tracks: list = field(default_factory=list)       # [{"name", "disc", "track", "title", "format", "kbps", "bits",
                                                     #   "rate", "length", "error"}], name relative to the album folder
    has_art: bool = False
    cover_px: int = 0                # the cover's shorter side in pixels, 0 when unknown or absent
    cover_file: str = ""             # the cover image beside the tracks ("cover.jpg"), "" when there is none
    tracktotal: dict = field(default_factory=dict)   # disc -> the largest TRACKTOTAL the tags claim
    disctotal: int = 0
    mb_release_id: str = ""          # the release ID in the tags (MUSICBRAINZ_ALBUMID), "" when untagged
    mb_match: str = ""               # the release the MusicBrainz comparison chose (not cached: worked out each check)
    genre: str = ""
    date: str = ""
    findings: list = field(default_factory=list)     # [Finding]
    musicbrainz: str = ""            # what the MusicBrainz comparison said, when it ran
    signature: str = ""

    @property
    def kinds(self) -> set:
        return {f.kind for f in self.findings}

    @property
    def ok(self) -> bool:
        return not self.findings

    def missing(self) -> list:
        return [m for f in self.findings if f.kind == MISSING for m in f.missing]

    def to_dict(self, tracks: bool = False) -> dict:
        d = asdict(self)
        if not tracks:
            d.pop("tracks")
        d["track_count"] = len(self.tracks)
        d["kinds"] = sorted(self.kinds)
        d["formats"] = dict(Counter(describe(t) for t in self.tracks))
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "AlbumHealth":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k != "findings"}
        known["tracktotal"] = {int(k): v for k, v in (known.get("tracktotal") or {}).items()}
        return cls(**known, findings=[Finding(**f) for f in data.get("findings", [])])


@dataclass
class HealthReport:
    root: str
    albums: list = field(default_factory=list)       # [AlbumHealth], every album checked, sorted by artist and album
    checked_at: float = 0.0
    musicbrainz: bool = False

    def counts(self) -> dict:
        out = {"albums": len(self.albums), "healthy": sum(1 for a in self.albums if a.ok)}
        for k in KINDS:
            out[k] = sum(1 for a in self.albums if k in a.kinds)
        return out

    def get(self, folder: str) -> Optional[AlbumHealth]:
        return next((a for a in self.albums if a.folder == folder), None)

    def to_dict(self) -> dict:
        return {"root": self.root, "checked_at": self.checked_at, "musicbrainz": self.musicbrainz,
                "counts": self.counts(), "albums": [a.to_dict() for a in self.albums]}


# ---- reading an album --------------------------------------------------------------------------------------------

def describe(t: dict) -> str:
    fmt = t.get("format") or "?"
    if fmt in LOSSLESS and t.get("bits") and t.get("rate"):
        return f"{fmt} {t['bits']}/{t['rate'] / 1000:g}"
    if fmt in LOSSY and t.get("kbps"):
        return f"{fmt} {t['kbps']}"
    return fmt


COVER_STEM = re.compile(r"^(cover|folder|front|albumart.*|album art.*|albumartsmall)$", re.I)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def cover_files(folder: Path) -> list:
    """The image files beside the tracks that players show as the album's cover: cover.jpg, folder.jpg, front.png,
    Windows Media Player's AlbumArt_{...}_Large.jpg and the like, best first; or a lone image there, whatever its
    name. Scans in subfolders (Artwork/, Scans/) are not covers."""
    try:
        images = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS
                  and not p.name.startswith(".")]
    except OSError:
        return []
    named = [p for p in images if COVER_STEM.match(p.stem)]
    if not named:
        return images if len(images) == 1 else []
    order = {n: i for i, n in enumerate(COVER_NAMES)}
    return sorted(named, key=lambda p: (order.get(p.name.lower(), len(order)), "small" in p.name.lower(), p.name.lower()))


def signature(files: list) -> str:
    h = hashlib.sha1()
    for p in files:
        try:
            st = p.stat()
        except OSError:
            continue
        h.update(f"{p.name}\0{st.st_size}\0{int(st.st_mtime)}\n".encode())
    return h.hexdigest()


def image_px(data: bytes) -> int:
    """The shorter side of an image in pixels, 0 when it can't be read. Only the header is parsed."""
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as img:
            return min(img.size)
    except Exception:
        return 0


def embedded_cover(path: Path) -> Optional[bytes]:
    from mutagen import File, MutagenError
    try:
        audio = File(str(path))
    except (MutagenError, OSError):
        return None
    if audio is None:
        return None
    pictures = getattr(audio, "pictures", None)
    if pictures:
        front = next((p for p in pictures if p.type == 3), pictures[0])
        return front.data
    tags = getattr(audio, "tags", None) or {}
    try:
        apic = next((tags[k] for k in tags.keys() if str(k).startswith("APIC")), None)
    except (AttributeError, TypeError):
        apic = None
    if apic is not None:
        return apic.data
    covr = tags.get("covr") if hasattr(tags, "get") else None         # MP4
    return bytes(covr[0]) if covr else None


def _disc_of(folder: Path, path: Path) -> int:
    """The disc a file sits in from its folder ("CD2/01.flac" -> 2), 0 when it is directly in the album folder."""
    rel = path.relative_to(folder)
    for part in rel.parts[:-1]:
        m = DISC_DIR.search(part)
        if m:
            return int(m.group(1))
    return 0


def read_album(root: Path, folder: Path, files: list) -> AlbumHealth:
    """The offline facts of one album folder. Findings are worked out from them by local_findings."""
    rel = folder.relative_to(root).as_posix()
    raw, tracks = [], []
    for p in files:
        name = p.relative_to(folder).as_posix()
        if p.suffix.lower() in TAGGED:
            try:
                t = read_track(p)
            except Exception:                           # one unreadable file must not hide the album
                t = {"format": p.suffix.lstrip(".").upper(), "error": "unreadable"}
        else:
            t = {"format": p.suffix.lstrip(".").upper()}
        raw.append(t)
        disc_from_tag, total_from_tag = t.get("disc") or 0, t.get("tracktotal") or 0
        n_disc, n_track = number_from_name(name)
        tracks.append({"name": name, "disc": disc_from_tag or _disc_of(folder, p) or n_disc or 1,
                       "track": t.get("track") or n_track or 0,
                       "title": t.get("title") or title_from_name(name), "format": t.get("format", "?"),
                       "kbps": t.get("kbps") or 0, "bits": t.get("bits") or 0, "rate": t.get("rate") or 0,
                       "length": t.get("length") or 0, "error": t.get("error", ""), "tracktotal": total_from_tag,
                       "disctotal": t.get("disctotal") or 0, "has_art": bool(t.get("has_art")),
                       "mb_recording_id": t.get("mb_recording_id", ""), "mb_track_id": t.get("mb_track_id", "")})
    parts = Path(rel).parts
    folder_artist = parts[0] if len(parts) > 1 else ""
    folder_album = parts[-1] if parts else folder.name
    year_m = YEAR_SUFFIX.search(folder_album)
    artist = _majority([t.get("albumartist") for t in raw]) or _majority([t.get("artist") for t in raw]) or folder_artist
    album = _majority([t.get("album") for t in raw]) or clean_album_title(YEAR_SUFFIX.sub("", folder_album)) or folder_album
    date = _majority([str(t.get("date") or "") for t in raw])
    year = (re.match(r"\d{4}", date).group(0) if re.match(r"\d{4}", date) else "") or (year_m.group(1) if year_m else "")

    covers = cover_files(folder)
    has_embedded = any(t["has_art"] for t in tracks)
    cover_px = 0
    if covers:
        try:
            cover_px = image_px(covers[0].read_bytes())
        except OSError:
            cover_px = 0
    elif has_embedded:
        first = next(p for p, t in zip(files, tracks) if t["has_art"])
        data = embedded_cover(first)
        cover_px = image_px(data) if data else 0
    totals: dict = {}
    for t in tracks:
        if t["tracktotal"]:
            totals[t["disc"]] = max(totals.get(t["disc"], 0), t["tracktotal"])
    entry = AlbumHealth(folder=rel, artist=artist or "?", album=album, year=year, tracks=tracks,
                        has_art=bool(covers) or has_embedded, cover_px=cover_px,
                        cover_file=covers[0].name if covers else "", tracktotal=totals,
                        disctotal=max([t["disctotal"] for t in tracks] or [0]),
                        mb_release_id=_majority([t.get("mb_release_id") for t in raw]),
                        genre=_majority([t.get("genre") for t in raw]), date=date,
                        signature=signature(files + covers))
    entry.findings = local_findings(entry)
    return entry


# ---- the checks ----------------------------------------------------------------------------------------------------

def _numbers_text(numbers: list) -> str:
    """[3, 4, 5, 9] -> '3-5, 9'"""
    out, run = [], []
    for n in sorted(numbers):
        if run and n == run[-1] + 1:
            run.append(n)
            continue
        if run:
            out.append(f"{run[0]}-{run[-1]}" if len(run) > 2 else ", ".join(map(str, run)))
        run = [n]
    if run:
        out.append(f"{run[0]}-{run[-1]}" if len(run) > 2 else ", ".join(map(str, run)))
    return ", ".join(out)


def missing_by_numbers(h: AlbumHealth) -> Optional[Finding]:
    """Gaps in the track numbering, tracks past the last one up to TRACKTOTAL, and whole discs up to DISCTOTAL.
    Tracks with no number at all (neither a tag nor a leading number in the name) make the numbering unreliable, so
    such an album is not judged on it."""
    if not h.tracks or any(not t["track"] for t in h.tracks):
        return None
    missing = []
    discs = sorted({t["disc"] for t in h.tracks})
    for disc in discs:
        numbers = {t["track"] for t in h.tracks if t["disc"] == disc}
        last = max(max(numbers), h.tracktotal.get(disc, 0))
        if last > 200:                                   # a catalogue number in the name, not a track number
            return None
        missing += [{"disc": disc, "track": n, "title": ""} for n in range(1, last + 1) if n not in numbers]
    gone_discs = [d for d in range(1, (h.disctotal or 0) + 1) if d not in discs] if h.disctotal <= 20 else []
    if not missing and not gone_discs:
        return None
    multi = len(discs) > 1 or h.disctotal > 1
    parts = []
    for disc in discs:
        nums = [m["track"] for m in missing if m["disc"] == disc]
        if nums:
            total = max(max(t["track"] for t in h.tracks if t["disc"] == disc), h.tracktotal.get(disc, 0))
            parts.append((f"disc {disc}: " if multi else "") + f"track{'s' if len(nums) > 1 else ''} "
                         f"{_numbers_text(nums)} of {total}")
    if gone_discs:
        parts.append(f"disc{'s' if len(gone_discs) > 1 else ''} {_numbers_text(gone_discs)} of {h.disctotal}")
    return Finding(MISSING, "bad", "Missing " + "; ".join(parts), missing)


def quality_findings(h: AlbumHealth) -> list:
    out = []
    broken = [t["name"] for t in h.tracks if t["error"]]
    if broken:
        out.append(Finding(QUALITY, "bad", f"{len(broken)} file{'s' if len(broken) > 1 else ''} can't be read "
                                           f"({PurePosixPath(broken[0]).name}{', ...' if len(broken) > 1 else ''})"))
    readable = [t for t in h.tracks if not t["error"]]
    lossy = [t for t in readable if t["format"] in LOSSY]
    lossless = [t for t in readable if t["format"] in LOSSLESS]
    if lossy:
        kinds = Counter(t["format"] for t in lossy)
        rates = sorted(t["kbps"] for t in lossy if t["kbps"])
        low = [r for r in rates if r < HIGH_MP3_KBPS]
        what = ", ".join(f"{n} {fmt}" for fmt, n in kinds.most_common())
        rate = (f" at {rates[0]} kbps" if rates[0] == rates[-1] else f" at {rates[0]}-{rates[-1]} kbps") if rates else ""
        if lossless:
            out.append(Finding(QUALITY, "bad" if low else "warn",
                               f"Mixed formats: {what}{rate} among {len(lossless)} lossless track{'s' if len(lossless) > 1 else ''}"))
        else:
            out.append(Finding(QUALITY, "bad" if low else "warn",
                               f"Lossy: {what}{rate}" + (" (low bitrate)" if low else "")))
    below_cd = [t for t in lossless if (t["bits"] and t["bits"] < 16) or (t["rate"] and t["rate"] < 44100)]
    if below_cd:
        t = below_cd[0]
        out.append(Finding(QUALITY, "warn", f"{len(below_cd)} lossless track{'s' if len(below_cd) > 1 else ''} below CD "
                                            f"quality ({t['bits']}-bit / {t['rate'] / 1000:g} kHz)"))
    return out


def art_findings(h: AlbumHealth) -> list:
    if not h.has_art:
        return [Finding(ART, "bad", "No cover art (none embedded and no cover.jpg or folder.jpg)")]
    if 0 < h.cover_px < MIN_COVER_PX:
        return [Finding(ART, "warn", f"Small cover ({h.cover_px} px; {MIN_COVER_PX} px or more looks sharp)")]
    return []


def local_findings(h: AlbumHealth) -> list:
    out = []
    gap = missing_by_numbers(h)
    if gap:
        out.append(gap)
    return out + quality_findings(h) + art_findings(h) + inner_duplicates(h)


# ---- duplicates ------------------------------------------------------------------------------------------------------

def quality_key(t: dict) -> tuple:
    """Higher is better: any readable lossless copy beats any lossy one; then the higher resolution (lossless) or
    bitrate (lossy). An unreadable file is worst of all."""
    if t.get("error"):
        return (0, 0, 0, 0)
    if t["format"] in LOSSLESS:
        return (2, t.get("bits") or 0, t.get("rate") or 0, 0)
    return (1, 0, 0, t.get("kbps") or 0)


def song_groups(albums: list) -> list:
    """The songs of one or more copies of an album: [[(AlbumHealth, track), ...] per song], in the first album's track
    order. Copies are the same song when their titles match. Inside one folder they must also have the same track
    number (when both have one), so an album's three different 'Interlude's stay three songs; across folders a title
    that occurs more than once in its own folder must match the number too."""
    groups = []
    for a in albums:
        repeats = Counter(matching.normalize(t["title"]) for t in a.tracks)
        for t in sorted_tracks(a):
            generic = repeats[matching.normalize(t["title"])] > 1
            numbered = bool(t["track"])
            home = None
            for g in groups:
                if not _title_matches(t["title"], g[0][1]["title"]):
                    continue
                mine = next((x[1] for x in g if x[0] is a), None)
                rep = mine or g[0][1]
                same_number = numbered and rep["track"] and (t["disc"], t["track"]) == (rep["disc"], rep["track"])
                if mine is not None:
                    if same_number or not (numbered and rep["track"]):
                        home = g
                        break
                    continue
                if not generic or same_number:
                    home = g
                    break
            if home is None:
                groups.append([(a, t)])
            else:
                home.append((a, t))
    return groups


def inner_duplicates(h: AlbumHealth) -> list:
    twice = [g for g in song_groups([h]) if len(g) > 1]
    if not twice:
        return []
    first = twice[0]
    kinds = " and ".join(sorted({describe(t) for _, t in first}))
    return [Finding(DUPES, "warn", f"{len(twice)} song{'s' if len(twice) > 1 else ''} twice in this folder "
                                   f"(“{first[0][1]['title']}”: {kinds})")]


def duplicate_albums(albums: list) -> list:
    """[[AlbumHealth, ...]] of two or more folders holding the same album (edition words such as 'Deluxe' or
    'Remastered' and years are ignored, so a deluxe copy counts; its extra songs are kept by a merge)."""
    by_key: dict = {}
    for a in albums:
        if a.album and a.artist and a.artist != "?":
            by_key.setdefault(matching.cluster_key(a.artist, a.album), []).append(a)
    return [g for k, g in by_key.items() if len(g) > 1 and all(k)]


def mark_duplicates(report: HealthReport) -> None:
    """Add a duplicates Finding to every album that is also in another folder (and drop stale ones)."""
    for a in report.albums:
        a.findings = [f for f in a.findings if not (f.kind == DUPES and f.folders)]
    for group in duplicate_albums(report.albums):
        folders = [a.folder for a in group]
        for a in group:
            others = [f"{o.folder} ({_format_summary(o)})" for o in group if o is not a]
            a.findings.append(Finding(DUPES, "warn", "Also in " + "; ".join(others), folders=folders))


def _format_summary(a: AlbumHealth) -> str:
    formats = Counter(describe(t) for t in a.tracks)
    return ", ".join(f"{n} {f}" for f, n in formats.most_common(2)) or "no audio"


# ---- MusicBrainz: which songs the album should have ----------------------------------------------------------------

def _title_matches(a: str, b: str) -> bool:
    return bool(a and b) and matching.similarity(a, b) >= TITLE_MATCH


def _same_track(local: dict, theirs: tuple) -> bool:
    """A file and a release track (mb.release_tracks' tuple) are the same song: the same release-track or recording
    ID when both are known, else the same title."""
    title, _secs, track_id, recording_id, _artist = theirs
    if local.get("mb_track_id") and track_id:
        if local["mb_track_id"] == track_id:
            return True
    if local.get("mb_recording_id") and recording_id:
        if local["mb_recording_id"] == recording_id:
            return True
    return _title_matches(local.get("title", ""), title)


def _covered(local: list, flat: list) -> bool:
    """Every song that is here is on the release (one title may differ in an album of five or more, which a remaster
    note or a typo in a tag easily causes)."""
    found = sum(1 for t in local if any(_same_track(t, x) for x in flat))
    return found == len(local) or (len(local) >= 5 and found >= len(local) - 1)


def _release_count(r: dict) -> int:
    return r.get("track-count") or sum(m.get("track-count", 0) for m in r.get("media", [])) or 0


def candidate_releases(mb_client, h: AlbumHealth) -> list:
    """[(release id, exact)] to try, best first. Exact ones come from the tags: the album's release ID, the release a
    release-track ID belongs to. Then the releases the tagged recordings are on, the releases whose name is like the
    album's (loosely: a release only counts later if it holds the songs that are here), and, when nothing else turned
    up, the releases that a few of the songs, searched by title, are on."""
    from . import musicbrainz as mb

    out, seen = [], set()

    def add(rid, exact=False):
        if rid and rid not in seen:
            seen.add(rid)
            out.append((rid, exact))

    add(h.mb_release_id, True)
    for tid in list(dict.fromkeys(t["mb_track_id"] for t in h.tracks if t.get("mb_track_id")))[:2]:
        for r in mb_client.releases_with_track(tid):
            add(r.get("id"), True)
    scored: dict = {}                                   # release id -> [hits, release dict]

    def consider(r: dict, weight: int = 1) -> None:
        if r.get("id") and r["id"] not in seen:
            entry = scored.setdefault(r["id"], [0, r])
            entry[0] += weight

    tracks = sorted_tracks(h)
    recordings = list(dict.fromkeys(t["mb_recording_id"] for t in tracks if t.get("mb_recording_id")))
    for rid in [recordings[i] for i in sorted({0, len(recordings) // 2, len(recordings) - 1})] if recordings else []:
        for r in mb_client.recording_releases(rid):
            consider(r, 2)
    names = [h.album]
    base = re.split(r"\s+[-–—:]\s+|:\s+", h.album, maxsplit=1)[0].strip()
    if base and base.casefold() != h.album.casefold() and len(base) >= 3:
        names.append(base)                          # "Kind of Blue - 50th Anniversary": the plain album is wanted too
    for r in [r for name in names for r in mb_client.search_releases(h.artist, name, limit=15)]:
        artist_ok =matching.similarity(h.artist, mb.credit_name(r.get("artist-credit") or [])) >= 0.7
        title = r.get("title", "")
        if artist_ok and (matching.similarity(h.album, title) >= 0.6 or matching.containment(h.album, title) >= 0.8
                          or matching.containment(title, h.album) >= 0.8):      # "Kind of Blue - 50th Anniversary"
            consider(r)
    if not scored:
        titled = [t["title"] for t in tracks if t["title"]]
        for title in list(dict.fromkeys(titled[i] for i in sorted({0, len(titled) // 2})))[:2]:
            for rec in mb_client.search_recordings(h.artist, title, limit=10):
                for r in rec.get("releases", []):
                    consider(r)

    def rank(item):
        hits, r = item
        group = r.get("release-group") or {}
        return (-hits, matching.similarity(h.album, r.get("title", "")) < 0.6,
                (group.get("primary-type") or "Album").lower() != "album", (r.get("status") or "official").lower() != "official",
                _release_count(r) or 999)

    tried = set()
    for hits, r in sorted(scored.values(), key=rank):
        shape = (_release_count(r), matching.normalize(r.get("title", "")))
        if shape in tried and shape[0]:                 # the same tracklist pressed again: one lookup is enough
            continue
        tried.add(shape)
        add(r["id"])
    return out


def compare_musicbrainz(mb_client, h: AlbumHealth, lookups: int = 5) -> Optional[Finding]:
    """The album's MusicBrainz release, and which of its songs are not in the folder. Releases are found by the
    MusicBrainz IDs in the tags when there are any (so an album named a little differently from MusicBrainz is still
    found), else by a loose name search or by its songs' titles; a release counts only if it holds every song that is
    here. The tags' own release wins; otherwise the smallest such release (the standard edition you have, not the
    deluxe one with bonus tracks). Sets h.musicbrainz to what happened. Returns a missing_tracks Finding, or None when
    nothing is missing or no release fits."""
    from . import musicbrainz as mb

    local = [t for t in h.tracks if t["title"] or t.get("mb_recording_id") or t.get("mb_track_id")]
    if not local:
        h.musicbrainz = "no track titles or IDs to compare"
        return None
    fits = []
    try:
        candidates = candidate_releases(mb_client, h)
        for rid, exact in candidates[:lookups]:
            release = mb_client.release(rid)
            flat = [(d, i, t) for d, tracks in enumerate(mb.release_tracks(release), 1) for i, t in enumerate(tracks, 1)]
            if flat and _covered(local, [x for _, _, x in flat]):
                fits.append((not exact, len(flat), release, flat))
    except mb.MBError as exc:
        h.musicbrainz = f"MusicBrainz: {exc}"
        return None
    if not fits:
        h.musicbrainz = "no MusicBrainz release has every song that is here" if candidates else "not found on MusicBrainz"
        return None
    _, _, release, flat = min(fits, key=lambda f: (f[0], f[1]))
    name = f"{release.get('title', '')} ({release.get('date', '')[:4] or 'undated'}" \
           f"{', ' + release.get('country') if release.get('country') else ''}, {len(flat)} tracks)"
    absent = [{"disc": d, "track": i, "title": x[0]} for d, i, x in flat if not any(_same_track(t, x) for t in local)]
    h.mb_match = release.get("id", "")
    if not absent:
        h.musicbrainz = f"complete: every song of {name}"
        return None
    h.musicbrainz = f"compared with {name}"
    shown = ", ".join(f"\u201c{m['title']}\u201d" for m in absent[:4]) + (", ..." if len(absent) > 4 else "")
    return Finding(MISSING, "bad", f"Missing {len(absent)} of {len(flat)} songs on MusicBrainz: {shown}", absent)


def apply_musicbrainz(mb_client, h: AlbumHealth) -> None:
    """Replace the numbering guess with MusicBrainz's answer when a release fits: it names the missing songs, and an
    album it calls complete is not flagged for odd numbering (that is a tag problem, shown in Library)."""
    found = compare_musicbrainz(mb_client, h)
    if found is not None or h.musicbrainz.startswith("complete"):
        h.findings = [f for f in h.findings if f.kind != MISSING] + ([found] if found else [])
        h.findings.sort(key=lambda f: KINDS.index(f.kind))


# ---- the whole library -----------------------------------------------------------------------------------------------

def cache_path(root: Path) -> Path:
    key = hashlib.sha1(str(Path(root).resolve()).encode()).hexdigest()[:16]
    return paths.data_dir() / ".library-index" / f"health-{key}.json"


def _load_cache(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") == CACHE_VERSION:
            return {a["folder"]: a for a in data.get("albums", [])}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {}


def scan(root: Path, *, mb_client=None, cache: Optional[Path] = None, use_cache: bool = True,
         progress: Optional[Callable[[dict], None]] = None, cancel: Optional[CancelCheck] = None) -> HealthReport:
    """Check every album in the library. With `mb_client`, each album's tracklist is also compared with MusicBrainz."""
    root = Path(root).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"{root} is not a folder")
    cache = cache or cache_path(root)
    known = _load_cache(cache) if use_cache else {}
    folders = album_folders(root)
    report = HealthReport(root=str(root), checked_at=time.time(), musicbrainz=mb_client is not None)
    for i, (folder, files) in enumerate(folders, 1):
        check(cancel)
        rel = folder.relative_to(root).as_posix()
        old = known.get(rel)
        if old and old.get("signature") == signature(files + cover_files(folder)):
            entry = AlbumHealth.from_dict(old)
            entry.findings = local_findings(entry)       # the checks may have changed since; the facts have not
            entry.musicbrainz = ""
        else:
            entry = read_album(root, folder, files)
        if progress is not None:
            progress({"stage": "health", "index": i, "total": len(folders), "folder": rel,
                      "step": "MusicBrainz" if mb_client is not None else "reading"})
        if mb_client is not None:
            apply_musicbrainz(mb_client, entry)
        report.albums.append(entry)
    report.albums.sort(key=lambda a: (a.artist.casefold(), a.year, a.album.casefold()))
    mark_duplicates(report)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(cache.name + ".tmp")
        facts = [{**asdict(a), "findings": [], "musicbrainz": "", "mb_match": ""} for a in report.albums]
        tmp.write_text(json.dumps({"version": CACHE_VERSION, "root": str(root), "albums": facts}), encoding="utf-8")
        os.replace(tmp, cache)
    except OSError:
        pass
    return report


def check_one(root: Path, rel: str, mb_client=None) -> AlbumHealth:
    """Check one album folder again (after a fix), without reading the rest of the library."""
    root = Path(root).expanduser()
    folder = (root / rel).resolve()
    if not folder.is_relative_to(root.resolve()) or not folder.is_dir():
        raise HealthError(f"no album folder {rel!r} in the library")
    pairs = [(f, files) for f, files in album_folders(folder) if f == folder]
    files = pairs[0][1] if pairs else []
    entry = read_album(root.resolve(), folder, files)
    entry.folder = rel
    if mb_client is not None:
        apply_musicbrainz(mb_client, entry)
    return entry


# ---- the Internet Archive: where the missing pieces are --------------------------------------------------------------

def _file_title(f) -> str:
    return (f.tags.get("title") or title_from_name(f.name)).strip()


def _file_number(f) -> tuple:
    """(disc, track) of an archive.org file, from the tags archive.org read, else its name."""
    disc, _ = parse_number(f.tags.get("disc"))
    track, _ = parse_number(f.tags.get("track"))
    n_disc, n_track = number_from_name(f.name)
    return (disc or n_disc or 1), (track or n_track)


def match_missing(h: AlbumHealth, item_tracks: list) -> list:
    """[(missing entry, FileInfo)] for the missing tracks this upload has. A missing song with a title (from
    MusicBrainz) is matched by title. One known only by number is matched by number, and only when the upload is the
    same album: as many tracks as the album should have, and the songs that are here have the same numbers there."""
    missing = h.missing()
    if not missing or not item_tracks:
        return []
    out, used = [], set()
    by_number = {}
    for f in item_tracks:
        by_number.setdefault(_file_number(f), f)
    expected = len(h.tracks) + len(missing)
    same_numbering = len(item_tracks) == expected and all(
        _title_matches(t["title"], _file_title(by_number[(t["disc"], t["track"])]))
        for t in h.tracks if t["title"] and (t["disc"], t["track"]) in by_number) \
        and sum(1 for t in h.tracks if (t["disc"], t["track"]) in by_number) >= max(1, len(h.tracks) // 2)
    for m in missing:
        f = None
        if m["title"]:
            f = next((x for x in item_tracks if x.name not in used and _title_matches(m["title"], _file_title(x))), None)
        if f is None and same_numbering:
            f = by_number.get((m["disc"], m["track"]))
            if f is not None and f.name in used:
                f = None
        if f is not None:
            used.add(f.name)
            out.append((m, f))
    return out


def suggestion(h: AlbumHealth, cand) -> dict:
    """What one archive.org upload offers this album: which missing songs it has, whether it is a lossless copy of the
    whole album (for a low-quality one), and whether it ships a cover."""
    from .download import pick_cover
    from .quality import assess

    item = cand.item
    tracks = item.flacs
    lossless = item.media in ("flac", "wav")
    provides = [{"disc": m["disc"], "track": m["track"], "title": m["title"] or _file_title(f), "file": f.name,
                 "size": f.size, "format": PurePosixPath(f.name).suffix.lstrip(".").upper()}
                for m, f in match_missing(h, tracks)]
    cover = pick_cover(item)
    fixes = []
    want = len(h.missing())
    if want and provides:
        fixes.append(MISSING)
    if QUALITY in h.kinds and lossless and len(tracks) >= len(h.tracks):
        fixes.append(QUALITY)
    if ART in h.kinds and cover is not None:
        fixes.append(ART)
    m = cand.match
    return {"identifier": item.identifier, "title": item.title, "creator": item.creator, "year": item.year,
            "url": item.url, "media": item.media, "lossless": lossless, "format": assess(cand).format,
            "tracks": len(tracks), "bytes": sum(f.size for f in tracks), "downloads": item.downloads,
            "match": ("exact" if m.bucket == 0 else "close") if m else "", "fixes": fixes,
            "provides": provides, "provides_all": bool(want) and len(provides) == want,
            "cover": {"name": cover.name, "size": cover.size} if cover is not None else None,
            "licence": item.licenseurl}


def suggestions_for(h: AlbumHealth, cands: list) -> list:
    """What each upload offers this album, best first."""
    return sorted((suggestion(h, c) for c in cands), key=_suggestion_key)


def _suggestion_key(s: dict) -> tuple:
    return (-len(s["fixes"]), not s["provides_all"], -len(s["provides"]), not s["lossless"], s["match"] != "exact",
            -s["downloads"])


def search_archive(client, h: AlbumHealth, *, mb_client=None, max_candidates: int = 8, found: Optional[list] = None,
                   progress: Optional[Callable[[dict], None]] = None, cancel: Optional[CancelCheck] = None) -> dict:
    """Search the Internet Archive for this album and say what each upload could fix. Only item records are read
    (no audio): {"query", "suggestions": [...best first], "art": [ArtImage dicts], "notes": [...]}. The art list is
    the front covers of the uploads, plus the Cover Art Archive's (hosted by the Internet Archive) when the album's
    MusicBrainz release is known, for a missing cover or to replace the one the album has. The uploads themselves
    (Candidates) are added to `found` when it is given, so the suggestions can be worked out again after a fix."""
    from . import cdart
    from .pipeline import Options, Pipeline
    from .queries import Want

    want = Want(raw=f"{h.artist} - {h.album}", artist=h.artist, album=h.album)
    opts = Options(top=50, deep="none", max_candidates=max_candidates, fallback=("wav", "vbr"))
    pipe = Pipeline(client, progress=progress, cancel=cancel)
    res = pipe.search_one(want, opts)
    if found is not None:
        found.extend(res.found)
    suggestions = suggestions_for(h, res.found)
    art = []                                             # for every album: a missing or small cover, or one to swap
    for c in res.found:
        fronts = [im for im in cdart.from_archive_item(c.item) if im.kind == "front"]
        cover = next((s["cover"] for s in suggestions if s["identifier"] == c.identifier and s["cover"]), None)
        if cover and not fronts:                         # a lone image the name rules call "other"
            fronts = [im for im in cdart.from_archive_item(c.item) if im.ref.endswith("/" + cover["name"])]
        art += fronts[:2]
    release_id = h.mb_release_id or h.mb_match
    if mb_client is not None and release_id:
        check(cancel)
        try:
            listing = mb_client.art_listing(release_id)
            art += [im for im in cdart.from_caa(listing, {"id": release_id}, "coverartarchive") if im.kind == "front"]
        except Exception as exc:                         # the cover search is extra: never lose the uploads over it
            res.notes.append(f"Cover Art Archive: {exc}")
    return {"folder": h.folder, "query": want.label, "suggestions": suggestions,
            "art": [im.to_dict() for im in art], "notes": list(res.notes), "searched_at": time.time()}


# ---- fixing: only ever adding files ---------------------------------------------------------------------------------

STYLE = re.compile(r"^(?P<disc>\d{1,2}-)?(?P<num>\d{1,3})(?P<sep>\s*-\s*|\.\s*|\s+|_)")


def track_name(existing: list, disc: int, track: int, title: str, ext: str, multi_disc: bool) -> str:
    """A file name in the style the album already uses ('01 - Title', '01. Title', '1-01 Title', '01 Title')."""
    styles = Counter()
    for name in existing:
        m = STYLE.match(PurePosixPath(name).name)
        if m:
            styles[(bool(m.group("disc")), len(m.group("num")), m.group("sep"))] += 1
    with_disc, width, sep = styles.most_common(1)[0][0] if styles else (multi_disc, 2, " - ")
    number = (f"{disc}-" if with_disc else "") + str(track).zfill(width)
    return f"{number}{sep}{folder_component(title) or 'Track'}{ext.lower()}"


def _target_dir(folder: Path, h: AlbumHealth, disc: int) -> Path:
    """Where a track of this disc goes: beside the disc's other tracks (a CD2/ folder, say), else the album folder."""
    dirs = Counter(str(PurePosixPath(t["name"]).parent) for t in h.tracks if t["disc"] == disc)
    if dirs:
        return folder / dirs.most_common(1)[0][0]
    sub = Counter(str(PurePosixPath(t["name"]).parent) for t in h.tracks)
    if len(sub) > 1 or (sub and "." not in sub):         # the album keeps each disc in its own folder
        return folder / f"CD{disc}"
    return folder


def plan_fetch(root: Path, h: AlbumHealth, item, files: list) -> list:
    """[{"file", "size", "path", "disc", "track", "title"}] for the chosen files of an upload: where each would go.
    Refuses names that are not the upload's tracks, and any target that already exists."""
    folder = (Path(root) / h.folder).resolve()
    by_name = {f.name: f for f in item.flacs}
    wanted = {(m["disc"], m["track"]): m for m in h.missing()}
    multi = len({t["disc"] for t in h.tracks}) > 1 or h.disctotal > 1
    plan, taken = [], set()
    matched = {f.name: m for m, f in match_missing(h, item.flacs)}
    for name in files:
        f = by_name.get(name)
        if f is None:
            raise HealthError(f"{name!r} is not one of the tracks of {item.identifier}")
        pair = (matched[name], f) if name in matched else None
        if pair is not None:
            disc, track, title = pair[0]["disc"], pair[0]["track"], pair[0]["title"] or _file_title(f)
        else:                                             # a track you chose that no missing slot was matched to
            disc, track = _file_number(f)
            title = _file_title(f)
            if (disc, track) not in wanted and any(t["disc"] == disc and t["track"] == track for t in h.tracks):
                raise HealthError(f"track {track} is already in the album; {name} was not added")
        ext = PurePosixPath(f.name).suffix
        ext = ".flac" if ext.lower() in (".wav", ".wave") else ext
        target_dir = _target_dir(folder, h, disc)
        existing = [t["name"] for t in h.tracks] + [p["path"] for p in plan]
        target = target_dir / track_name(existing, disc, track, title, ext, multi)
        if not target.resolve().is_relative_to(folder):
            raise HealthError(f"{name}: the file name would leave the album folder")
        if target.exists() or str(target).casefold() in taken:
            raise HealthError(f"{target.name} is already in the album folder; nothing was added")
        taken.add(str(target).casefold())
        plan.append({"file": name, "size": f.size, "path": target.relative_to(folder).as_posix(), "disc": disc,
                     "track": track, "title": title})
    return plan


def _album_tags(h: AlbumHealth, disc: int, track: int, title: str, source_url: str) -> dict:
    total = h.tracktotal.get(disc) or (len([t for t in h.tracks if t["disc"] == disc]) + 1)
    tags = {"TITLE": title, "ARTIST": h.artist, "ALBUMARTIST": h.artist, "ALBUM": h.album, "TRACKNUMBER": str(track),
            "TRACKTOTAL": str(max(total, track)), "DISCNUMBER": str(disc), "DATE": h.date or h.year, "GENRE": h.genre,
            "MUSICBRAINZ_ALBUMID": h.mb_release_id, "IAFLAC_SOURCE": source_url}
    if h.disctotal or len({t["disc"] for t in h.tracks}) > 1:
        tags["DISCTOTAL"] = str(max(h.disctotal, disc, *(t["disc"] for t in h.tracks)))
    return {k: v for k, v in tags.items() if v}


def _encode_wav(wav: Path, out: Path) -> None:
    flac = binaries.flac()
    if not flac:
        raise HealthError("flac is not installed, so a WAV track cannot be added as FLAC")
    proc = subprocess.run([flac, "--best", "--verify", "-s", "-f", "-o", str(out), str(wav)], capture_output=True,
                          **binaries.NO_WINDOW)
    if proc.returncode != 0 or not out.exists():
        out.unlink(missing_ok=True)
        msg = next((ln.strip() for ln in proc.stderr.decode("utf-8", "replace").splitlines() if ln.strip()), "flac failed")
        raise HealthError(f"{wav.name} could not be encoded to FLAC ({msg})")


def fetch_tracks(client, root: Path, h: AlbumHealth, item, files: list, *, progress: Optional[Callable[[dict], None]] = None,
                 cancel: Optional[CancelCheck] = None) -> dict:
    """Download chosen tracks of an archive.org upload into the album folder. Each file goes to a hidden .part name
    in the album folder, must match archive.org's checksum (and pass `flac -t` when it is FLAC), is tagged with the
    album's names, and only then gets its final name. Nothing that is already in the folder is touched."""
    from .download import PlannedFile, _fetch, flac_test, matches_manifest
    from .tagging import prepare_cover, write_flac, write_mp3

    emit = progress or (lambda e: None)
    folder = (Path(root) / h.folder).resolve()
    plan = plan_fetch(root, h, item, files)
    by_name = {f.name: f for f in item.flacs}
    covers = cover_files(folder)
    cover = None
    if covers and any(t.get("has_art") for t in h.tracks):     # the album embeds its cover: so does the new track
        try:
            cover = prepare_cover(covers[0].read_bytes())
        except Exception:
            cover = None
    added, failures = [], []
    total = sum(p["size"] for p in plan)
    done = 0
    for i, p in enumerate(plan, 1):
        check(cancel)
        f = by_name[p["file"]]
        final = folder / p["path"]
        staging = final.with_name(f".iaflac-fetch-{i}{PurePosixPath(f.name).suffix.lower()}")
        emit({"stage": "health-fetch", "index": i, "total": len(plan), "file": f.name, "bytes": done, "size": total})
        pf = PlannedFile(info=f, path=staging)
        try:
            _fetch(client, item.identifier, pf,
                   on_bytes=lambda n, base=done: emit({"stage": "health-fetch", "index": i, "total": len(plan),
                                                       "file": f.name, "bytes": base + n, "size": total}),
                   cancel=cancel)
            part = staging.with_name(staging.name + ".part")
            part.replace(staging)
            if matches_manifest(staging, f) is False:
                raise HealthError(f"{f.name} does not match archive.org's checksum")
            if staging.suffix == ".flac":
                ok, msg = flac_test(staging)
                if ok is False:
                    raise HealthError(f"{f.name} failed the decode test: {msg}")
            if staging.suffix in (".wav", ".wave"):
                encoded = staging.with_suffix(".flac")
                _encode_wav(staging, encoded)
                staging.unlink()
                staging = encoded
            tags = _album_tags(h, p["disc"], p["track"], p["title"], item.url)
            if staging.suffix == ".flac":
                write_flac(staging, tags, cover)
            elif staging.suffix == ".mp3":
                write_mp3(staging, tags, cover)
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.exists():
                raise HealthError(f"{final.name} appeared in the album folder meanwhile; it was left alone")
            os.replace(staging, final)
            added.append(p["path"])
        except Exception as exc:
            if type(exc).__name__ == "Cancelled":
                raise
            failures.append(f"{f.name}: {exc}")
            for leftover in (staging, staging.with_name(staging.name + ".part")):
                leftover.unlink(missing_ok=True)
        done += p["size"]
    return {"folder": h.folder, "identifier": item.identifier, "added": added, "failures": failures}


def _is_png(data: bytes) -> bool:
    return data[:8] == b"\x89PNG\r\n\x1a\n"


def save_cover(root: Path, h: AlbumHealth, data: bytes, *, replace: bool = False, trash: Optional[Path] = None) -> dict:
    """Write the cover as cover.jpg (or cover.png) beside the tracks. An album that already has a cover is refused
    unless `replace`: then its cover files go to the Trash first (with a copy of the embedded cover, if any), the new
    one is written, and tracks that carried an embedded cover get the new one embedded instead (only their picture
    changes). Returns {"written", "trash", "embedded"}."""
    root = Path(root).expanduser().resolve()
    folder = (root / h.folder).resolve()
    if not folder.is_relative_to(root) or not folder.is_dir():
        raise HealthError(f"no album folder {h.folder!r} in the library")
    if not image_px(data):
        raise HealthError("that is not an image that can be read")
    old_files = cover_files(folder)
    embedded = [folder / t["name"] for t in h.tracks if t.get("has_art") and (folder / t["name"]).is_file()]
    if (old_files or embedded) and not replace:
        raise HealthError(f"{h.folder} already has a cover; it was left alone")
    trash_dir = None
    if old_files or embedded:
        trash_dir = _trash_place(folder, root, trash, "cover replaced")
        trash_dir.mkdir(parents=True, exist_ok=True)
        for f in old_files:
            shutil.move(str(f), str(trash_dir / f.name))
        if embedded:
            old = embedded_cover(embedded[0])
            if old:
                (trash_dir / ("embedded cover.png" if _is_png(old) else "embedded cover.jpg")).write_bytes(old)
    target = folder / ("cover.png" if _is_png(data) else "cover.jpg")
    tmp = folder / f".{target.name}.iaflac-tmp"
    tmp.write_bytes(data)
    os.replace(tmp, target)
    done = 0
    if embedded:
        from .tagging import prepare_cover
        picture = prepare_cover(data)
        for path in embedded:
            if embed_cover(path, picture):
                done += 1
    return {"written": target.name, "trash": str(trash_dir) if trash_dir else "", "embedded": done}


def embed_cover(path: Path, picture: tuple) -> bool:
    """Swap the embedded front cover of one FLAC or MP3 for `picture` (tagging.prepare_cover's tuple). Nothing else in
    the file changes. False for a format that is not handled."""
    data, mime, width, height = picture
    if path.suffix.lower() == ".flac":
        from mutagen.flac import FLAC, Picture
        audio = FLAC(str(path))
        audio.clear_pictures()
        pic = Picture()
        pic.type, pic.mime, pic.desc, pic.width, pic.height, pic.depth, pic.data = 3, mime, "Front cover", width, height, 24, data
        audio.add_picture(pic)
        audio.save()
        return True
    if path.suffix.lower() == ".mp3":
        from mutagen.id3 import APIC, ID3
        id3 = ID3(str(path))
        id3.delall("APIC")
        id3.add(APIC(encoding=3, mime=mime, type=3, desc="Front cover", data=data))
        id3.save(str(path), v2_version=4)
        return True
    return False


def replace_album(client, root: Path, h: AlbumHealth, cand, *, opts=None, mb_client=None, trash: Optional[Path] = None,
                  progress: Optional[Callable[[dict], None]] = None, cancel: Optional[CancelCheck] = None) -> dict:
    """Replace the album with a lossless archive.org upload of it, never risking the original: the upload is downloaded
    into a hidden staging folder beside the album, checked against archive.org's checksums and tagged with your album's
    artist and title. Only when every file has verified does the old folder go to the Trash (anything in it that is not
    audio is carried over first) and the new one take its place, under the old folder's name. A failed or cancelled
    download leaves the original untouched; the next try resumes it."""
    from . import save, tagging
    from .replace import ReplaceError, staging_folder, swap_in

    if cand.item.media not in ("flac", "wav"):
        raise HealthError("that upload is lossy (MP3): an album is only replaced with a lossless copy")
    root = Path(root).expanduser().resolve()
    existing = (root / h.folder).resolve()
    if not existing.is_relative_to(root) or existing == root or not existing.is_dir():
        raise HealthError(f"no album folder {h.folder!r} in the library")
    meta = tagging.from_candidate(cand)
    meta.albumartist, meta.album = h.artist, h.album              # your names stay, whatever the upload calls itself
    meta.date = meta.date or h.date or h.year
    plan = save.plan_save(cand, root, meta, opts)
    plan.folder = staging_folder(existing, cand.identifier)
    result = save.apply_save(client, cand, plan, opts, mb_client=mb_client, progress=progress, cancel=cancel)
    if not result.ok:
        return {"ok": False, "folder": h.folder, "failures": result.failures,
                "note": "your album was not touched; trying again resumes the download"}
    try:
        moved_to, carried = swap_in(existing, plan.folder, root, trash)
    except (ReplaceError, OSError) as exc:
        raise HealthError(f"could not swap the album in ({exc}); the verified download is in {plan.folder.name}") from exc
    return {"ok": True, "folder": h.folder, "trash": str(moved_to), "carried": carried, "tracks": len(result.files),
            "problems": result.problems}


def sorted_tracks(h: AlbumHealth) -> list:
    return sorted(h.tracks, key=lambda t: (t["disc"], t["track"], natural_key(t["name"])))


# ---- merging duplicates: the best copy of every song in one folder ----------------------------------------------------

def _copy(a: AlbumHealth, t: dict) -> dict:
    return {"folder": a.folder, "name": t["name"], "format": describe(t), "quality": list(quality_key(t))}


def _merge_target(albums: list) -> AlbumHealth:
    """The folder the album stays in: the one with the most lossless songs, then the most songs, then a cover."""
    return max(albums, key=lambda a: (sum(1 for t in a.tracks if t["format"] in LOSSLESS and not t["error"]),
                                      len(a.tracks), a.has_art, -len(a.folder)))


def plan_merge(root: Path, folders: list) -> dict:
    """What merging these album folders (or de-duplicating one) would do, without doing any of it:
    {"target", "folders", "artist", "album", "songs": [{"title", "disc", "track", "action": "keep" | "move",
     "keep": copy, "drop": [copies]}], "trash_folders", "moves", "drops", "token"}. `token` changes whenever any of the
    folders' audio or cover files change, so apply_merge can refuse a plan that is out of date."""
    folders = list(dict.fromkeys(folders))
    if not folders:
        raise HealthError("no album folders to merge")
    albums = [check_one(root, f) for f in folders]
    target = _merge_target(albums)
    order = [target] + [a for a in albums if a is not target]
    songs = []
    for g in song_groups(order):
        copies = sorted(g, key=lambda c: (quality_key(c[1]), c[0] is target), reverse=True)
        best_album, best = copies[0]
        songs.append({"title": best["title"], "disc": best["disc"], "track": best["track"],
                      "action": "keep" if best_album is target else "move", "keep": _copy(best_album, best),
                      "drop": [_copy(a, t) for a, t in copies[1:]]})
    token = hashlib.sha1(json.dumps([(a.folder, a.signature) for a in order]).encode()).hexdigest()[:16]
    return {"target": target.folder, "folders": [a.folder for a in order], "artist": target.artist,
            "album": target.album, "songs": songs, "trash_folders": [a.folder for a in order[1:]],
            "moves": sum(1 for s in songs if s["action"] == "move"), "drops": sum(len(s["drop"]) for s in songs),
            "token": token}


def _unique(path: Path) -> Path:
    n, out = 2, path
    while out.exists():
        out = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return out


def _trash_place(existing: Path, library: Path, trash: Optional[Path], why: str = "merged") -> Path:
    from .replace import trash_destination
    dest = trash_destination(existing, library, trash)
    dest = dest.with_name(dest.name.replace("(replaced by iaflac", f"({why} by iaflac"))
    n, out = 2, dest
    while out.exists():
        out = dest.with_name(f"{dest.name} {n}")
        n += 1
    return out


def _retag_album(path: Path, artist: str, album: str) -> None:
    """Give a moved track the album's own ALBUM and ALBUMARTIST, so players show one album, not two."""
    from mutagen import File
    if path.suffix.lower() == ".flac":
        from mutagen.flac import FLAC
        audio = FLAC(str(path))
        if audio.tags is None:
            audio.add_tags()
        audio["ALBUM"], audio["ALBUMARTIST"] = album, artist
        audio.save()
    elif path.suffix.lower() == ".mp3":
        from mutagen.id3 import ID3, ID3NoHeaderError, TALB, TPE2
        try:
            id3 = ID3(str(path))
        except ID3NoHeaderError:
            id3 = ID3()
        id3.setall("TALB", [TALB(encoding=3, text=album)])
        id3.setall("TPE2", [TPE2(encoding=3, text=artist)])
        id3.save(str(path), v2_version=4)
    elif File(str(path)) is None:
        raise HealthError(f"{path.name} can't be tagged")


def apply_merge(root: Path, folders: list, token: str, *, trash: Optional[Path] = None,
                progress: Optional[Callable[[dict], None]] = None) -> dict:
    """Carry out plan_merge(root, folders), which must still have this `token`. In order: the target folder's own
    worse copies go to the Trash; the best copies from the other folders move into it (named as they were, in the
    disc folder they belong to) and take its album names; anything else in the other folders that is not audio
    (booklets, a cover the target lacks) is copied across; then each other folder goes to the Trash whole, with the
    worse copies still in it. Nothing is deleted."""
    from .replace import carry_over
    import shutil

    emit = progress or (lambda e: None)
    root = Path(root).expanduser().resolve()
    plan = plan_merge(root, folders)
    if plan["token"] != token:
        raise HealthError("the album folders changed after the merge plan was made: look at the new plan and merge again")
    target_h = check_one(root, plan["target"])
    target = root / plan["target"]
    moved, trashed_files, trashed_folders, notes = [], [], [], []
    file_trash = None
    own_drops = [d for s in plan["songs"] for d in s["drop"] if d["folder"] == plan["target"]]
    for i, d in enumerate(own_drops, 1):
        emit({"stage": "merge", "step": "trash", "index": i, "total": len(own_drops), "file": d["name"]})
        file_trash = file_trash or _trash_place(target, root, trash)
        dst = file_trash / d["name"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target / d["name"]), str(dst))
        trashed_files.append(d["name"])
    moves = [s for s in plan["songs"] if s["action"] == "move"]
    for i, s in enumerate(moves, 1):
        k = s["keep"]
        emit({"stage": "merge", "step": "move", "index": i, "total": len(moves), "file": k["name"]})
        src = root / k["folder"] / k["name"]
        dst = _unique(_target_dir(target, target_h, s["disc"]) / PurePosixPath(k["name"]).name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved.append(dst.relative_to(target).as_posix())
        try:
            _retag_album(dst, plan["artist"], plan["album"])
        except Exception as exc:
            notes.append(f"{dst.name}: moved, but its album tags were not changed ({exc})")
    for folder in plan["trash_folders"]:
        src = root / folder
        if not src.is_dir():
            continue
        emit({"stage": "merge", "step": "folder", "folder": folder})
        for name in carry_over(src, target):
            notes.append(f"copied {name} from {folder}")
        dst = _trash_place(src, root, trash)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        trashed_folders.append(folder)
        try:
            src.parent.rmdir()                       # the artist folder, if that was its only album
        except OSError:
            pass
    return {"target": plan["target"], "moved": moved, "trashed_files": trashed_files,
            "trashed_folders": trashed_folders, "trash": str(file_trash or (_trash_place(target, root, trash).parent)),
            "notes": notes}
