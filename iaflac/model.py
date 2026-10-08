"""Item and file records parsed from archive.org metadata."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Optional

DETAILS = "https://archive.org/details/"

# IA item-level format labels for lossless audio other than FLAC (reported, never silently used)
OTHER_LOSSLESS = {"Apple Lossless Audio": "alac", "WAVE": "wav", "Shorten": "shn",
                  "WavPack": "wavpack", "Monkey's Audio": "ape", "AIFF": "aiff"}

LIVE_META_KEYS = ("venue", "taper", "lineage", "transferer", "coverage")
LIVE_TITLE = re.compile(
    r"\b(19|20)\d\d[-./](\d\d|xx)[-./](\d\d|xx)\b|\blive\b|\b(sbd|soundboard)\b|"
    r"\b(radio|peel|bbc|kexp|in) sessions?\b", re.I)
LIVE_COLLECTIONS = {"etree", "gratefuldead", "livemusicarchive"}


def parse_length(value) -> Optional[float]:
    """IA gives `length` as seconds ("170.25") or clock time ("3:12", "1:02:03")."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        if ":" in text:
            seconds = 0.0
            for part in text.split(":"):
                seconds = seconds * 60 + float(part)
            return seconds
        return float(text)
    except ValueError:
        return None


def as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v not in (None, "")]
    return [str(value)]


def strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def natural_key(name: str):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


def file_kind(name: str) -> str:
    """Coarse role of a file in a rip: flac, log, cue, accurip, aucdtect, ffp, sfv, md5, ..."""
    base = name.lower().rsplit("/", 1)[-1]
    if base.endswith(".flac"):
        return "flac"
    if "aucdtect" in base:
        return "aucdtect"
    for ext, kind in ((".log", "log"), (".cue", "cue"), (".accurip", "accurip"), (".ffp", "ffp"),
                      (".sfv", "sfv"), (".md5", "md5"), (".st5", "md5"), (".m3u", "playlist"),
                      (".m3u8", "playlist"), (".txt", "text"), (".pdf", "art"), (".jpg", "art"),
                      (".jpeg", "art"), (".png", "art")):
        if base.endswith(ext):
            return kind
    return "other"


VBR_FORMAT = "VBR MP3"


def is_track(f: "FileInfo", media: str = "flac") -> bool:
    """Is this file one of the album's tracks in the given media? Original WAVs, MP3s (the uploader's own, or IA's
    'VBR MP3' derivatives), or FLAC. See track_files for which MP3s win when an item has both."""
    if media == "wav":
        return f.name.lower().endswith((".wav", ".wave")) and f.source != "derivative"
    if media == "mp3":
        return f.format == VBR_FORMAT or (f.name.lower().endswith(".mp3") and f.source != "derivative")
    return file_kind(f.name) == "flac"


def track_files(files: list, media: str = "flac") -> list:
    """An item's tracks in `media`, in track order. MP3: the uploader's original MP3s when there are any (IA's VBR
    derivatives of them would only be a second, worse copy), otherwise IA's 'VBR MP3' files."""
    tracks = [f for f in files if is_track(f, media)]
    if media == "mp3":
        originals = [f for f in tracks if f.source != "derivative"]
        tracks = originals or tracks
    return sorted(tracks, key=lambda f: natural_key(f.name))


@dataclass
class FileInfo:
    name: str
    format: str = ""
    source: str = ""
    size: int = 0
    length: Optional[float] = None
    md5: str = ""
    sha1: str = ""

    @classmethod
    def from_ia(cls, raw: dict) -> "FileInfo":
        try:
            size = int(raw.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        return cls(name=raw.get("name", ""), format=raw.get("format", ""),
                   source=raw.get("source", ""), size=size, length=parse_length(raw.get("length")),
                   md5=(raw.get("md5") or "").lower(), sha1=(raw.get("sha1") or "").lower())


@dataclass
class ItemInfo:
    identifier: str
    title: str = ""
    creator: str = ""
    year: Optional[int] = None
    date: str = ""
    description: str = ""
    licenseurl: str = ""
    collections: list = field(default_factory=list)
    subjects: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)      # venue / taper / lineage / source / notes ...
    downloads: int = 0
    item_size: int = 0
    files: list = field(default_factory=list)      # list[FileInfo]
    media: str = "flac"                            # what `flacs` (the tracks) means: "flac", or for the fallback "wav" / "mp3" (VBR)

    @property
    def url(self) -> str:
        return DETAILS + self.identifier

    @property
    def flacs(self) -> list:
        """The item's tracks in the media being looked at: FLAC files, or (when no FLAC exists and the fallback search
        found this item by its WAV or VBR MP3 files) those files. The name is kept so everything downstream works alike."""
        return track_files(self.files, self.media)

    def files_of(self, kind: str) -> list:
        return sorted((f for f in self.files if file_kind(f.name) == kind and f.source != "derivative"),
                      key=lambda f: natural_key(f.name))

    @property
    def other_lossless(self) -> list:
        found = {OTHER_LOSSLESS[f.format] for f in self.files if f.format in OTHER_LOSSLESS}
        return sorted(found)

    @property
    def runtime_seconds(self) -> Optional[float]:
        flacs = self.flacs
        if flacs and all(f.length for f in flacs):
            return sum(f.length for f in flacs)
        return None

    @property
    def flac_bytes(self) -> int:
        return sum(f.size for f in self.flacs)

    @property
    def is_live(self) -> bool:
        if any(self.extra.get(k) for k in LIVE_META_KEYS):
            return True
        if {c.lower() for c in self.collections} & LIVE_COLLECTIONS:
            return True
        # a date next to a barcode/catalogue number is a release date, not a concert date
        title = re.sub(r"[(\[][^)\]]*\d{8,}[^)\]]*[)\]]", " ", self.title)
        return bool(LIVE_TITLE.search(title))

    @property
    def is_free(self) -> bool:
        """Carries a Creative Commons / public-domain style licence, or sits in a netlabel collection."""
        lic = self.licenseurl.lower()
        return ("creativecommons.org" in lic or "publicdomain" in lic
                or "netlabels" in {c.lower() for c in self.collections})

    @classmethod
    def from_metadata(cls, data: dict) -> "ItemInfo":
        meta = data.get("metadata", {})
        date = as_list(meta.get("date"))[:1]
        year_src = as_list(meta.get("year"))[:1] or date
        match = re.search(r"\b(1[89]|20)\d\d\b", year_src[0]) if year_src else None
        try:
            downloads = int(meta.get("downloads") or 0)
        except (TypeError, ValueError):
            downloads = 0
        return cls(
            identifier=meta.get("identifier", ""),
            title=" / ".join(as_list(meta.get("title"))),
            creator="; ".join(as_list(meta.get("creator"))),
            year=int(match.group(0)) if match else None,
            date=date[0] if date else "",
            description=strip_html(" ".join(as_list(meta.get("description")))),
            licenseurl=(as_list(meta.get("licenseurl")) or [""])[0],
            collections=as_list(meta.get("collection")),
            subjects=[s.strip() for v in as_list(meta.get("subject")) for s in re.split(r"[;,]", v) if s.strip()],
            extra={k: " ".join(as_list(meta.get(k))) for k in
                   (*LIVE_META_KEYS, "source", "notes") if meta.get(k)},
            downloads=downloads,
            item_size=int(data.get("item_size") or 0),
            files=[FileInfo.from_ia(f) for f in data.get("files", []) if f.get("name")],
        )
