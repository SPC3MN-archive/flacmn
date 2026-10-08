"""Find the printed parts of a CD for an album in the library: the outside of the case (front cover, back with its
spines) and the inside (the inside of the front insert, the image under the clear tray, the disc label).

Sources, most trusted first:
  1. images already in the album folder (scans you keep beside the music, e.g. Artwork/ or Scans/)
  2. the archive.org upload the album was saved from (the IAFLAC_SOURCE tag), whose uploader often scanned the case
  3. the Cover Art Archive for the album's MusicBrainz release, and for other pressings of it with the same tracklist
     (art is spread unevenly: one pressing may have only a front while another has the full 16-page booklet)
  4. other archive.org uploads of the same album

Scans are classified by their Cover Art Archive type, or by file name for folder and archive.org images ("Back.jpg",
"Inlay.jpg", "Booklet 03.jpg", "CD.jpg"). Nothing is downloaded at full size until it is chosen for printing.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import unquote

from . import matching, paths, queries
from .events import CancelCheck, check
from .ia_client import IAClient, IAError
from .model import ItemInfo, natural_key
from .tagging import AlbumMeta, TrackMeta, read_track

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".webp", ".bmp")
KINDS = ("front", "back", "booklet", "disc", "tray", "spine", "other")
PRINTED = ("front", "back", "booklet", "disc", "tray")
PARTS = ("front", "back", "inside", "tray", "disc")       # what a print uses; "inside" is chosen from booklet scans
CAA_KIND = {"Front": "front", "Back": "back", "Booklet": "booklet", "Medium": "disc", "Tray": "tray", "Spine": "spine",
            "Liner": "booklet"}
SOURCE_RANK = {"folder": 0, "archive.org": 1, "coverartarchive": 2, "coverartarchive-sibling": 3, "archive.org-search": 4}
SIBLINGS = 4                 # other pressings whose Cover Art Archive images are listed
CD_FORMATS = re.compile(r"\bcd\b|compact disc|sacd|hdcd|blu-spec|shm", re.I)

# file-name rules, first match wins (checked against the name and its parent folder, lower case)
NAME_RULES = (
    ("other", re.compile(r"matrix|runout|obi|sticker|hype|receipt|spectro|thumb|screenshot|log\b")),
    ("spine", re.compile(r"spine")),
    ("back", re.compile(r"\b(back|rear|inlay|tray[\s_-]*card|traycard|backcover|back[\s_-]*cover)\b|^back|_back")),
    ("tray", re.compile(r"\b(tray|under[\s_-]*tray|inside[\s_-]*tray|behind)\b")),
    ("disc", re.compile(r"\b(cd|disc|disk|media|label)\s*\d*\b|^cd\d|^disc\d")),
    ("booklet", re.compile(r"\b(booklet|book|page|pg|insert|inside|inner|liner|leaflet|sheet)\b|^\d{1,2}$|^\d{1,2}[\s_-]")),
    ("front", re.compile(r"\b(front|cover|folder|albumart|artwork|frontcover)\b|^front|^cover")),
)


def classify_name(name: str) -> str:
    """The kind of scan a file name suggests: 'Scans/Back.jpg' -> back, 'Booklet 03.jpg' -> booklet."""
    p = Path(name.replace("\\", "/"))
    stem = re.sub(r"[_]+", " ", p.stem.lower()).strip()
    parent = p.parent.name.lower()
    for kind, rx in NAME_RULES:
        if rx.search(stem):
            return kind
    for kind, rx in NAME_RULES:                    # "Booklet/01.jpg", "Scans/CD/front.jpg"
        if parent and rx.search(parent):
            return kind
    return "other"


@dataclass
class ArtImage:
    id: str
    kind: str
    source: str                 # folder | archive.org | coverartarchive | coverartarchive-sibling | archive.org-search
    ref: str                    # local path, or URL of the full-size image
    preview: str = ""           # a smaller image for the UI (Cover Art Archive thumbnail), else same as ref
    label: str = ""             # where it is from, for people: "Cover Art Archive · 1997 AU CD"
    comment: str = ""
    order: int = 0              # position within its source (booklet page order)
    group: str = ""             # images from one scan set share a group (one release, one archive item, one folder)
    types: list = field(default_factory=list)
    size: int = 0
    width: int = 0
    height: int = 0
    release_id: str = ""
    identifier: str = ""        # archive.org item, for archive.org images

    @property
    def rank(self) -> int:
        return SOURCE_RANK.get(self.source, 9)

    def to_dict(self) -> dict:
        return asdict(self)


def image_id(source: str, ref: str) -> str:
    return hashlib.sha1(f"{source}\0{ref}".encode()).hexdigest()[:16]


@dataclass
class ArtSet:
    folder: str
    artist: str = ""
    album: str = ""
    year: str = ""
    mb_release_id: str = ""
    mb_releasegroup_id: str = ""
    source_url: str = ""
    releases: list = field(default_factory=list)       # pressings whose art was listed: {id, title, date, country, formats, count, why}
    images: list = field(default_factory=list)         # [ArtImage]
    notes: list = field(default_factory=list)
    tracks: list = field(default_factory=list)         # [{disc, track, title, length}] from the files, for a generated tray card

    def of(self, kind: str) -> list:
        return sorted((i for i in self.images if i.kind == kind), key=lambda i: (i.rank, i.group, i.order))

    def completeness(self) -> dict:
        return {k: sum(1 for i in self.images if i.kind == k) for k in KINDS}

    def get(self, image_id_: str) -> Optional[ArtImage]:
        return next((i for i in self.images if i.id == image_id_), None)

    def to_dict(self) -> dict:
        d = asdict(self)
        for row, im in zip(d["images"], self.images):
            row["dpi"] = print_dpi(im)
        d["completeness"] = self.completeness()
        d["selection"] = default_selection(self)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ArtSet":
        data = dict(data)
        images = [ArtImage(**{k: v for k, v in i.items() if k in ArtImage.__dataclass_fields__}) for i in data.pop("images", [])]
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known, images=images)


# ---- what the album is -------------------------------------------------------------------------------------------

def album_facts(folder: Path) -> tuple:
    """(AlbumMeta from the files' tags, tag dict of the first track). Track lengths come along for MusicBrainz."""
    files = sorted((p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in (".flac", ".mp3")),
                   key=lambda p: natural_key(str(p.relative_to(folder))))
    tags = [read_track(p) for p in files]
    tags = [t for t in tags if not t.get("error")]

    def majority(key):
        values = [t.get(key) for t in tags if t.get(key)]
        return max(set(values), key=values.count) if values else ""

    meta = AlbumMeta(albumartist=majority("albumartist") or majority("artist") or folder.parent.name,
                     album=majority("album") or re.sub(r"\s*\((?:19|20)\d\d\)\s*$", "", folder.name),
                     date=str(majority("date") or ""), mb_release_id=majority("mb_release_id"),
                     mb_releasegroup_id=majority("mb_releasegroup_id"), source_url=majority("source_url"))
    for i, t in enumerate(tags, 1):
        meta.tracks.append(TrackMeta(source=Path(t["path"]).name, title=t.get("title") or Path(t["path"]).stem,
                                     track=t.get("track") or i, disc=t.get("disc") or 1, length=t.get("length") or None))
    return meta, (tags[0] if tags else {})


# ---- sources -----------------------------------------------------------------------------------------------------

def from_folder(folder: Path) -> list:
    out = []
    files = sorted((p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS and not p.name.startswith(".")),
                   key=lambda p: natural_key(str(p.relative_to(folder))))
    for n, p in enumerate(files):
        rel = p.relative_to(folder).as_posix()
        kind = classify_name(rel)
        if kind == "other" and p.parent == folder and len(files) == 1:
            kind = "front"                         # a lone image beside the tracks is the cover
        group = f"folder:{p.parent.relative_to(folder).as_posix()}"
        out.append(ArtImage(id=image_id("folder", str(p)), kind=kind, source="folder", ref=str(p), preview=str(p),
                            label=f"album folder: {rel}", order=n, group=group, size=p.stat().st_size))
    return out


def identifier_from_url(url: str) -> str:
    m = re.search(r"archive\.org/(?:details|download)/([^/?#]+)", url or "")
    return m.group(1) if m else ""


def from_archive_item(item: ItemInfo, source: str = "archive.org") -> list:
    out = []
    files = [f for f in item.files if f.name.lower().endswith(IMAGE_EXTS) and f.source != "derivative"
             and not re.search(r"__ia_thumb|_spectrogram|_thumb\.", f.name, re.I)]
    for n, f in enumerate(sorted(files, key=lambda f: natural_key(f.name))):
        url = IAClient.file_url(item.identifier, f.name)
        out.append(ArtImage(id=image_id(source, url), kind=classify_name(f.name), source=source, ref=url, preview=url,
                            label=f"archive.org {item.identifier}: {f.name}", order=n, group=f"ia:{item.identifier}",
                            size=f.size, identifier=item.identifier))
    return out


def from_caa(images: list, release: dict, source: str) -> list:
    out = []
    desc = " ".join(x for x in (release.get("date", "")[:4], release.get("country", ""), release.get("formats", "")) if x)
    for n, im in enumerate(images):
        types = im.get("types") or []
        kinds = [CAA_KIND[t] for t in types if t in CAA_KIND]
        kind = "back" if "back" in kinds else (kinds[0] if kinds else "other")
        thumbs = im.get("thumbnails") or {}
        url = im.get("image", "")
        if not url:
            continue
        out.append(ArtImage(id=image_id(source, url), kind=kind, source=source, ref=url,
                            preview=thumbs.get("500") or thumbs.get("large") or url,
                            label=f"Cover Art Archive · {desc or release.get('id', '')}", comment=im.get("comment") or "",
                            order=n, group=f"caa:{release.get('id', '')}", types=types, release_id=release.get("id", "")))
    return out


def _formats(release: dict) -> str:
    fmts = [m.get("format") or "?" for m in release.get("media", [])]
    if len(fmts) > 1 and len(set(fmts)) == 1:
        return f"{len(fmts)}×{fmts[0]}"
    return " + ".join(fmts)


def siblings(group_releases: list, discs: list, exclude: str = "", n: int = SIBLINGS) -> list:
    """Other pressings that are the same CD (same track count on each disc, a CD format) and have art, best first:
    the ones with a back scan and the most images."""
    def tracks(r):
        return [m.get("track-count", 0) for m in r.get("media", []) if CD_FORMATS.search(m.get("format") or "")]

    same = [r for r in group_releases if r.get("id") != exclude and tracks(r) == discs
            and (r.get("cover-art-archive") or {}).get("count", 0)]
    same.sort(key=lambda r: (not (r.get("cover-art-archive") or {}).get("back"),
                             -(r.get("cover-art-archive") or {}).get("count", 0)))
    return same[:n]


def from_archive_search(client, artist: str, album: str, limit: int = 4) -> list:
    """Other archive.org uploads of this album that ship images."""
    out, seen = [], set()
    for rung in queries.album_rungs(artist, album)[:2]:
        for doc in client.search_iter(rung.query, max_hits=10):
            ident = doc["identifier"]
            if ident in seen:
                continue
            seen.add(ident)
            m = matching.match_fields(artist, album, str(doc.get("title") or ""), str(doc.get("creator") or ""))
            if m.bucket != 0:
                continue
            try:
                item = ItemInfo.from_metadata(client.metadata(ident))
            except IAError:
                continue
            found = from_archive_item(item, source="archive.org-search")
            if any(i.kind != "other" for i in found):
                out += found
            if len({i.identifier for i in out}) >= limit:
                return out
    return out


# ---- putting it together -----------------------------------------------------------------------------------------

def find_art(folder: Path, *, mb_client=None, ia_client=None, release_id: str = "", search_archive: bool = True,
             progress: Optional[Callable[[dict], None]] = None, cancel: Optional[CancelCheck] = None,
             library_root: Optional[Path] = None) -> ArtSet:
    from . import musicbrainz as mb

    emit = progress or (lambda e: None)
    folder = Path(folder)
    meta, _ = album_facts(folder)
    rel = folder.relative_to(library_root).as_posix() if library_root else folder.name
    art = ArtSet(folder=rel, artist=meta.albumartist, album=meta.album, year=meta.year, source_url=meta.source_url,
                 tracks=[{"disc": t.disc, "track": t.track, "title": t.title, "length": t.length} for t in meta.tracks])
    seen: set = set()

    def add(images):
        for im in images:
            if im.id not in seen:
                seen.add(im.id)
                art.images.append(im)

    emit({"stage": "art", "step": "folder"})
    add(from_folder(folder))

    ident = identifier_from_url(meta.source_url)
    if ident and ia_client is not None:
        check(cancel)
        emit({"stage": "art", "step": "archive.org item", "identifier": ident})
        try:
            add(from_archive_item(ItemInfo.from_metadata(ia_client.metadata(ident))))
        except IAError as exc:
            art.notes.append(f"could not read the archive.org upload {ident}: {exc}")

    if mb_client is not None:
        check(cancel)
        emit({"stage": "art", "step": "MusicBrainz"})
        rid = release_id or meta.mb_release_id
        rgid = "" if release_id else meta.mb_releasegroup_id
        try:
            if not rid:
                matches = mb.find_matches(mb_client, meta)
                if matches and matches[0].score >= 0.8:
                    rid, rgid = matches[0].release_id, matches[0].release_group_id
                    art.notes.append(f"matched on MusicBrainz: {matches[0].title} ({matches[0].date} {matches[0].country}, "
                                     f"score {matches[0].score:.2f})")
                else:
                    art.notes.append("no confident MusicBrainz match: tag the album with a release (Metadata) to look up its scans")
            if rid:
                release = mb_client.release(rid)
                rgid = rgid or (release.get("release-group") or {}).get("id", "")
                info = {"id": rid, "title": release.get("title", ""), "date": release.get("date", ""),
                        "country": release.get("country", ""), "formats": _formats(release), "why": "this album's release"}
                check(cancel)
                listing = mb_client.art_listing(rid)
                info["count"] = len(listing)
                art.releases.append(info)
                add(from_caa(listing, info, "coverartarchive"))
                art.mb_release_id, art.mb_releasegroup_id = rid, rgid
                discs = [m.get("track-count", 0) for m in release.get("media", []) if CD_FORMATS.search(m.get("format") or "")] \
                    or [art_disc_count(meta, d) for d in sorted({t.disc for t in meta.tracks})]
                if rgid:
                    for sib in siblings(mb_client.group_releases(rgid), discs, exclude=rid):
                        check(cancel)
                        emit({"stage": "art", "step": "other pressings", "release": sib["id"]})
                        sinfo = {"id": sib["id"], "title": sib.get("title", ""), "date": sib.get("date", ""),
                                 "country": sib.get("country", ""), "formats": _formats(sib),
                                 "count": (sib.get("cover-art-archive") or {}).get("count", 0),
                                 "why": "another pressing with the same tracklist"}
                        art.releases.append(sinfo)
                        add(from_caa(mb_client.art_listing(sib["id"]), sinfo, "coverartarchive-sibling"))
        except mb.MBError as exc:
            art.notes.append(f"MusicBrainz / Cover Art Archive: {exc}")

    if search_archive and ia_client is not None and not all(art.of(k) for k in ("front", "back", "disc")):
        check(cancel)
        emit({"stage": "art", "step": "archive.org search"})
        try:
            add(from_archive_search(ia_client, art.artist, art.album))
        except IAError as exc:
            art.notes.append(f"archive.org search failed: {exc}")
    check(cancel)
    emit({"stage": "art", "step": "measuring"})
    measure_all(art, mb_client, ia_client, cancel)
    missing = [k for k in ("front", "back", "disc") if not art.of(k)]
    if missing:
        art.notes.append("not found: " + ", ".join(missing) + " (the print sheet makes "
                         + " and ".join({"front": "a text cover", "back": "a tracklist tray card",
                                         "disc": "a disc label from the cover"}[k] for k in missing) + ")")
    emit({"stage": "art", "step": "done", "found": art.completeness()})
    return art


def art_disc_count(meta: AlbumMeta, disc: int) -> int:
    return sum(1 for t in meta.tracks if t.disc == disc)


# printed size of each part in mm (width, height); see jewelcase.py
SLOT_MM = {"front": (120.0, 120.0), "back": (151.0, 118.0), "disc": (118.0, 118.0), "tray": (138.0, 118.0),
           "booklet": (120.0, 120.0)}
GOOD_DPI = 300


def print_dpi(im: ArtImage, kind: Optional[str] = None) -> int:
    """The resolution `im` prints at in its part of the case (0 when its size is not known). A booklet scan twice as
    wide as it is tall is a two-page spread."""
    if not im.width or not im.height:
        return 0
    w_mm, h_mm = SLOT_MM.get(kind or im.kind, SLOT_MM["front"])
    if (kind or im.kind) == "booklet" and im.width / im.height >= 1.6:
        w_mm *= 2
    return int(min(im.width / (w_mm / 25.4), im.height / (h_mm / 25.4)))


def measure_all(art: ArtSet, mb_client=None, ia_client=None, cancel: Optional[CancelCheck] = None,
                workers: int = 4) -> None:
    """Fill in width/height for every printable image from its header bytes only (a local read, or one ranged
    request), so the choice can weigh resolution without downloading every scan."""
    from concurrent.futures import ThreadPoolExecutor
    from .musicbrainz import header_size

    def one(im: ArtImage):
        if im.width or im.kind not in PRINTED:
            return
        check(cancel)
        try:
            if im.source == "folder":
                with open(im.ref, "rb") as fh:
                    im.width, im.height = header_size(fh.read(65536))
            elif im.source.startswith("coverartarchive") and mb_client is not None and hasattr(mb_client, "image_size"):
                im.width, im.height = mb_client.image_size(im.ref)
            elif im.identifier and ia_client is not None:
                name = unquote(im.ref.split(f"/download/{im.identifier}/", 1)[-1])
                im.width, im.height = header_size(ia_client.range_get(im.identifier, name, 0, 65536))
        except Exception:                           # unknown size just means "not preferred"
            im.width = im.height = 0

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, art.images))


def _quality(im: ArtImage, kind: str) -> float:
    """0..1 for resolution (300 dpi and up is full marks); an unmeasured image counts as middling."""
    dpi = print_dpi(im, kind)
    return min(dpi, GOOD_DPI) / GOOD_DPI if dpi else 0.45


def default_selection(art: ArtSet) -> dict:
    """Which image prints where: {"front", "back", "inside", "tray", "disc"} -> image id or None.
    The inside of the front insert is the first booklet page of a scan set (one pressing's scans, one archive.org
    item, one folder). The set that covers the most of the five parts, sharpest, leads; every part prefers it so the
    case matches, unless another scan is clearly sharper. Your own folder scans get a head start."""
    candidates = {kind: art.of(kind) for kind in ("front", "back", "tray", "disc")}
    firsts: dict = {}
    for im in art.of("booklet"):                     # each set's first page is its inside panel
        if im.group not in firsts or im.order < firsts[im.group].order:
            firsts[im.group] = im
    candidates["inside"] = list(firsts.values())
    kind_of = {"inside": "booklet"}

    def quality(im, part):
        return _quality(im, kind_of.get(part, part))

    groups = {im.group for ims in candidates.values() for im in ims}

    def coverage(group):
        return sum(max((quality(im, part) for im in ims if im.group == group), default=0.0)
                   for part, ims in candidates.items())

    main_group = max(sorted(groups), key=coverage, default="")

    def pick(part):
        ims = candidates[part]
        if not ims:
            return None

        def score(im):
            return (quality(im, part) + (0.3 if im.group == main_group else 0.0)
                    + (0.15 if im.source == "folder" else 0.0) - 0.02 * im.rank)

        return max(ims, key=score).id

    return {part: pick(part) for part in PARTS}


# ---- fetching ----------------------------------------------------------------------------------------------------

class ArtFetcher:
    """Full-size image bytes for an ArtImage, cached on disk."""

    def __init__(self, mb_client=None, ia_client=None, cache: Optional[Path] = None):
        self.mb, self.ia = mb_client, ia_client
        self.cache = cache or paths.cache_dir() / "art"

    def get(self, im: ArtImage, cancel: Optional[CancelCheck] = None) -> bytes:
        if im.source == "folder":
            return Path(im.ref).read_bytes()
        path = self.cache / im.id[:2] / im.id
        if path.exists():
            return path.read_bytes()
        check(cancel)
        if im.source.startswith("coverartarchive"):
            data = self.mb.image(im.ref)
        else:
            if self.ia is None:
                raise IAError("no archive.org client")
            name = unquote(im.ref.split(f"/download/{im.identifier}/", 1)[-1])
            resp = self.ia.open_stream(im.identifier, name)
            try:
                buf = io.BytesIO()
                for chunk in resp.iter_content(1 << 20):
                    check(cancel)
                    buf.write(chunk)
                data = buf.getvalue()
            finally:
                resp.close()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data

    def measure(self, im: ArtImage, cancel: Optional[CancelCheck] = None) -> ArtImage:
        from PIL import Image
        data = self.get(im, cancel)
        with Image.open(io.BytesIO(data)) as img:
            im.width, im.height = img.size
        im.size = im.size or len(data)
        return im


def save_to_folder(art: ArtSet, selection: dict, folder: Path, fetcher: ArtFetcher, subfolder: str = "Artwork") -> list:
    """Copy the chosen scans into <album>/Artwork/ as front.jpg, back.jpg, inside.jpg, tray.jpg, disc.jpg
    (cover.jpg beside the tracks is left alone, so players keep showing the same cover)."""
    out_dir = folder / subfolder
    out_dir.mkdir(exist_ok=True)
    written = []
    for name, iid in ((k, selection.get(k)) for k in PARTS):
        im = art.get(iid) if iid else None
        if im is None or (im.source == "folder" and Path(im.ref).parent == out_dir):
            continue
        data = fetcher.get(im)
        ext = ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
        target = out_dir / f"{name}{ext}"
        target.write_bytes(data)
        written.append(str(target))
    return written
