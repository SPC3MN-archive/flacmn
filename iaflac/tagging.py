"""Album metadata: propose it, compare it, write it, check it, and lay the files out for a player such as Volumio.

Volumio (MPD underneath) groups an album by its ALBUM and ALBUMARTIST tags, orders it by DISCNUMBER and TRACKNUMBER,
and shows embedded art or a cover.jpg/folder.jpg beside the files. So a saved album gets:

    <library>/<Album Artist>/<Album> (<Year>)/01 - Title.flac           single disc
    <library>/<Album Artist>/<Album> (<Year>)/1-01 - Title.flac         multi-disc, still one folder
                                             /cover.jpg

and every track carries ARTIST, ALBUMARTIST, ALBUM, TITLE, TRACKNUMBER/TRACKTOTAL, DISCNUMBER/DISCTOTAL, DATE, GENRE
(plus MusicBrainz IDs when matched) and an embedded front cover. Rip logs and cue sheets are left out by default: MPD
reads a .cue as a playlist, which makes the album show up twice.

Only metadata blocks are rewritten. A FLAC's audio frames are untouched, which `flac -t` confirms afterwards against
the audio MD5 in STREAMINFO.
"""

from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

from .paths import is_hidden
from .names import clean_album_title, derive_names, folder_component

EMBED_MAX_PX = 1000                 # embedded art: big enough for any player, small enough not to bloat every track
COVER_NAMES = ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "albumart.jpg")
DISC_DIR = re.compile(r"(?:^|[\s_\-(\[])(?:cd|disc|disk|dvd)\s*[-_.]?\s*(\d{1,2})\b", re.I)
DISC_TRACK = re.compile(r"^\s*(\d{1,2})[-.](\d{1,3})(?=[\s._\-)])[\s._\-)]*")
PLAIN_TRACK = re.compile(r"^\s*(\d{1,3})(?=[\s._\-)])[\s._\-)]*")
NUMBER = re.compile(r"^\s*(\d+)\s*(?:/\s*(\d+))?")
AUDIO_EXTS = (".flac", ".mp3")


@dataclass
class TrackMeta:
    source: str                         # the archive.org file name this track comes from (the key while saving)
    title: str = ""
    track: int = 0
    disc: int = 1
    artist: str = ""                    # track artist; empty means the album artist
    length: Optional[float] = None
    mb_track_id: str = ""
    mb_recording_id: str = ""


@dataclass
class AlbumMeta:
    albumartist: str = ""
    album: str = ""
    date: str = ""                      # "1959" or "1959-08-17"
    genre: str = ""
    label: str = ""
    catalog: str = ""
    mb_release_id: str = ""
    mb_releasegroup_id: str = ""
    mb_albumartist_id: str = ""
    source_url: str = ""                # the archive.org item it was saved from (IAFLAC_SOURCE), for finding its scans later
    tracks: list = field(default_factory=list)          # [TrackMeta]
    sources: dict = field(default_factory=dict)         # field -> where the value came from ("tags", "archive.org", "musicbrainz", "you")

    @property
    def year(self) -> str:
        m = re.match(r"\d{4}", self.date or "")
        return m.group(0) if m else ""

    @property
    def disctotal(self) -> int:
        return max((t.disc for t in self.tracks), default=1)

    def tracktotal(self, disc: int) -> int:
        return sum(1 for t in self.tracks if t.disc == disc)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AlbumMeta":
        data = dict(data)
        tracks = [TrackMeta(**t) for t in data.pop("tracks", [])]
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known, tracks=tracks)


# ---- proposing tags from what archive.org and the files say ---------------------------------------------------

def parse_number(value) -> tuple:
    """'3/12' -> (3, 12), '07' -> (7, 0), anything else -> (0, 0)."""
    m = NUMBER.match(str(value or ""))
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)


def number_from_name(name: str) -> tuple:
    """(disc, track) from an archive file path: 'CD2/03 Song.flac' -> (2, 3), '1-05 Song.flac' -> (1, 5)."""
    path = PurePosixPath(name)
    disc = 0
    for part in path.parts[:-1]:
        m = DISC_DIR.search(part)
        if m:
            disc = int(m.group(1))
    m = DISC_TRACK.match(path.stem)
    if m:
        return disc or int(m.group(1)), int(m.group(2))
    m = PLAIN_TRACK.match(path.stem)
    return disc, int(m.group(1)) if m else 0


def title_from_name(name: str, artist: str = "") -> str:
    stem = PurePosixPath(name).stem
    m = DISC_TRACK.match(stem) or PLAIN_TRACK.match(stem)
    rest = stem[m.end():] if m else stem
    rest = re.sub(r"^[\s.\-_)]+", "", rest)
    if artist and rest.casefold().startswith(f"{artist} - ".casefold()):
        rest = rest[len(artist) + 3:]
    return re.sub(r"[_]+", " ", rest).strip() or stem


def _first(*values) -> str:
    return next((str(v).strip() for v in values if v not in (None, "") and str(v).strip()), "")


def from_candidate(cand) -> AlbumMeta:
    """The best guess at an album's tags before anything is downloaded: the FLAC tags the header probes read, then
    archive.org's per-file fields, then the file names; album-level names as `names.derive_names` files them."""
    item = cand.item
    st = cand.stats
    nm = derive_names(item.title, item.creator, st.tag_artist if st else "", st.tag_album if st else "")
    meta = AlbumMeta(albumartist=nm.artist, album=nm.album, date=str(item.year or ""), source_url=item.url)
    meta.sources = {"albumartist": nm.source, "album": nm.source, "date": "archive.org" if item.year else ""}
    probes = getattr(cand, "probes", {}) or {}
    genres, dates = [], []
    files = item.flacs
    for f in files:
        tags = getattr(probes.get(f.name), "tags", {}) or {}
        disc_n, track_n = number_from_name(f.name)
        t_track, _ = parse_number(tags.get("TRACKNUMBER") or f.tags.get("track"))
        t_disc, _ = parse_number(tags.get("DISCNUMBER") or f.tags.get("disc"))
        title = _first(tags.get("TITLE"), f.tags.get("title")) or title_from_name(f.name, meta.albumartist)
        artist = _first(tags.get("ARTIST"), f.tags.get("artist"), f.tags.get("creator"))
        meta.tracks.append(TrackMeta(source=f.name, title=title, track=t_track or track_n, disc=t_disc or disc_n or 1,
                                     artist="" if artist.casefold() == meta.albumartist.casefold() else artist,
                                     length=f.length))
        if tags.get("GENRE") or f.tags.get("genre"):
            genres.append(_first(tags.get("GENRE"), f.tags.get("genre")))
        if tags.get("DATE"):
            dates.append(tags["DATE"])
    if dates and not meta.date:
        meta.date, meta.sources["date"] = max(set(dates), key=dates.count), "tags"
    if genres:
        meta.genre, meta.sources["genre"] = max(set(genres), key=genres.count), "tags"
    renumber(meta)
    return meta


def renumber(meta: AlbumMeta) -> None:
    """Fill in missing or clashing track numbers from the file order, disc by disc; drop an empty disc 0."""
    for t in meta.tracks:
        t.disc = t.disc or 1
    for disc in sorted({t.disc for t in meta.tracks}):
        on_disc = [t for t in meta.tracks if t.disc == disc]
        numbers = [t.track for t in on_disc]
        if 0 in numbers or len(set(numbers)) != len(numbers):
            for i, t in enumerate(on_disc, 1):
                t.track = i
    discs = sorted({t.disc for t in meta.tracks})
    if discs and discs != list(range(1, len(discs) + 1)):              # e.g. only "CD2" was named: renumber 1..n
        remap = {d: i for i, d in enumerate(discs, 1)}
        for t in meta.tracks:
            t.disc = remap[t.disc]
    meta.tracks.sort(key=lambda t: (t.disc, t.track))


# ---- layout ------------------------------------------------------------------------------------------------------

def album_folder_name(meta: AlbumMeta) -> str:
    album = clean_album_title(meta.album) or meta.album
    name = f"{album} ({meta.year})" if meta.year and not album.endswith(f"({meta.year})") else album
    return folder_component(name)


def album_folder(library: Path, meta: AlbumMeta) -> Path:
    artist, album = folder_component(meta.albumartist), album_folder_name(meta)
    if not artist or not album:
        raise ValueError(f"cannot make folder names from {meta.albumartist!r} / {meta.album!r}")
    return library / artist / album


def track_filename(meta: AlbumMeta, t: TrackMeta, ext: str) -> str:
    number = f"{t.disc}-{t.track:02d}" if meta.disctotal > 1 else f"{t.track:02d}"
    title = folder_component(t.title) or "Track"
    return f"{number} - {title}{ext.lower()}"


def planned_names(meta: AlbumMeta, ext_of: dict) -> dict:
    """{source: final file name}; names are made unique so two tracks can never collide."""
    out, seen = {}, set()
    for t in meta.tracks:
        name = track_filename(meta, t, ext_of.get(t.source, PurePosixPath(t.source).suffix or ".flac"))
        base, n = name, 2
        while name.casefold() in seen:
            stem, dot, ext = base.rpartition(".")
            name = f"{stem} ({n}).{ext}"
            n += 1
        seen.add(name.casefold())
        out[t.source] = name
    return out


# ---- writing ----------------------------------------------------------------------------------------------------

def vorbis_tags(meta: AlbumMeta, t: TrackMeta) -> dict:
    tags = {
        "TITLE": t.title, "ARTIST": t.artist or meta.albumartist, "ALBUMARTIST": meta.albumartist, "ALBUM": meta.album,
        "TRACKNUMBER": str(t.track), "TRACKTOTAL": str(meta.tracktotal(t.disc)),
        "DISCNUMBER": str(t.disc), "DISCTOTAL": str(meta.disctotal),
        "DATE": meta.date, "GENRE": meta.genre, "LABEL": meta.label, "CATALOGNUMBER": meta.catalog,
        "MUSICBRAINZ_ALBUMID": meta.mb_release_id, "MUSICBRAINZ_RELEASEGROUPID": meta.mb_releasegroup_id,
        "MUSICBRAINZ_ALBUMARTISTID": meta.mb_albumartist_id, "MUSICBRAINZ_RELEASETRACKID": t.mb_track_id,
        "MUSICBRAINZ_TRACKID": t.mb_recording_id, "IAFLAC_SOURCE": meta.source_url,
    }
    return {k: v for k, v in tags.items() if v}


MANAGED = ("TITLE", "ARTIST", "ALBUMARTIST", "ALBUM ARTIST", "ALBUM", "TRACKNUMBER", "TRACKTOTAL", "TOTALTRACKS",
           "DISCNUMBER", "DISCTOTAL", "TOTALDISCS", "DATE", "YEAR", "GENRE", "LABEL", "CATALOGNUMBER",
           "MUSICBRAINZ_ALBUMID", "MUSICBRAINZ_RELEASEGROUPID", "MUSICBRAINZ_ALBUMARTISTID",
           "MUSICBRAINZ_RELEASETRACKID", "MUSICBRAINZ_TRACKID", "IAFLAC_SOURCE")


def prepare_cover(data: bytes, max_px: int = EMBED_MAX_PX) -> tuple:
    """(jpeg or png bytes, mime, width, height) scaled down to max_px on the long side for embedding."""
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    img.load()
    if max(img.size) > max_px:
        img.thumbnail((max_px, max_px))
    else:
        mime = Image.MIME.get(img.format, "")
        if mime in ("image/jpeg", "image/png"):
            return data, mime, img.width, img.height
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=90)
    return out.getvalue(), "image/jpeg", img.width, img.height


def write_flac(path: Path, tags: dict, cover: Optional[tuple] = None) -> None:
    from mutagen.flac import FLAC, Picture
    audio = FLAC(str(path))                         # only metadata blocks change; STREAMINFO and audio frames are kept
    if audio.tags is None:
        audio.add_tags()
    for key in {k for k in audio.tags.keys() if k.upper() in MANAGED}:
        del audio[key]
    for k, v in tags.items():
        audio[k] = v
    if cover is not None:
        data, mime, width, height = cover
        audio.clear_pictures()
        pic = Picture()
        pic.type, pic.mime, pic.desc = 3, mime, "Front cover"
        pic.width, pic.height, pic.depth = width, height, 24
        pic.data = data
        audio.add_picture(pic)
    audio.save()


def write_mp3(path: Path, tags: dict, cover: Optional[tuple] = None) -> None:
    from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TPUB, TRCK, TXXX
    try:
        id3 = ID3(str(path))
    except ID3NoHeaderError:
        id3 = ID3()
    for frame in ("TIT2", "TPE1", "TPE2", "TALB", "TRCK", "TPOS", "TDRC", "TCON", "TPUB"):
        id3.delall(frame)
    id3.delall("TXXX:MusicBrainz Album Id")
    id3.delall("TXXX:MusicBrainz Release Group Id")
    id3.delall("TXXX:MusicBrainz Album Artist Id")
    id3.delall("TXXX:IAFLAC_SOURCE")
    simple = {"TITLE": TIT2, "ARTIST": TPE1, "ALBUMARTIST": TPE2, "ALBUM": TALB, "DATE": TDRC, "GENRE": TCON, "LABEL": TPUB}
    for key, frame in simple.items():
        if tags.get(key):
            id3.add(frame(encoding=3, text=tags[key]))
    if tags.get("TRACKNUMBER"):
        id3.add(TRCK(encoding=3, text=f"{tags['TRACKNUMBER']}/{tags.get('TRACKTOTAL', '')}".rstrip("/")))
    if tags.get("DISCNUMBER"):
        id3.add(TPOS(encoding=3, text=f"{tags['DISCNUMBER']}/{tags.get('DISCTOTAL', '')}".rstrip("/")))
    for key, desc in (("MUSICBRAINZ_ALBUMID", "MusicBrainz Album Id"), ("MUSICBRAINZ_RELEASEGROUPID", "MusicBrainz Release Group Id"),
                      ("MUSICBRAINZ_ALBUMARTISTID", "MusicBrainz Album Artist Id"), ("IAFLAC_SOURCE", "IAFLAC_SOURCE")):
        if tags.get(key):
            id3.add(TXXX(encoding=3, desc=desc, text=tags[key]))
    if cover is not None:
        id3.delall("APIC")
        id3.add(APIC(encoding=3, mime=cover[1], type=3, desc="Front cover", data=cover[0]))
    id3.save(str(path), v2_version=4)


def write_track(path: Path, meta: AlbumMeta, t: TrackMeta, cover: Optional[tuple] = None) -> None:
    tags = vorbis_tags(meta, t)
    if path.suffix.lower() == ".flac":
        write_flac(path, tags, cover)
    elif path.suffix.lower() == ".mp3":
        write_mp3(path, tags, cover)
    else:
        raise ValueError(f"cannot tag {path.name}: only FLAC and MP3")


@dataclass
class ApplyResult:
    files: list = field(default_factory=list)       # final paths, in track order
    renamed: dict = field(default_factory=dict)     # old name -> new name
    cover: Optional[Path] = None
    problems: list = field(default_factory=list)


def apply(folder: Path, meta: AlbumMeta, paths: dict, cover: Optional[bytes] = None, *,
          write_cover_file: bool = True, embed: bool = True, verify=None) -> ApplyResult:
    """Tag every track and rename it into the layout, inside `folder`. paths: {source: current Path}.
    `verify(path) -> (ok, message)` re-checks a FLAC after tagging (download.flac_test); a failure is reported.
    Tracks move from any subfolder (Disc 1/, CD2/) into the album folder; emptied subfolders are removed."""
    res = ApplyResult()
    prepared = None
    if cover:
        try:
            prepared = prepare_cover(cover)
        except Exception as exc:                    # a broken image must not stop the album
            res.problems.append(f"cover art could not be read ({type(exc).__name__}); saved without it")
    ext_of = {t.source: paths[t.source].suffix for t in meta.tracks if t.source in paths}
    finals = planned_names(meta, ext_of)
    moves = []
    for t in meta.tracks:
        src = paths.get(t.source)
        if src is None or not src.exists():
            res.problems.append(f"{t.source}: file missing, not tagged")
            continue
        write_track(src, meta, t, prepared if embed else None)
        if verify is not None and src.suffix.lower() == ".flac":
            ok, msg = verify(src)
            if ok is False:
                res.problems.append(f"{src.name}: fails flac -t after tagging ({msg})")
        moves.append((src, folder / finals[t.source]))
    # two-step rename so a track can take a name another track still has
    staged = []
    for i, (src, dst) in enumerate(moves):
        tmp = src.with_name(f".iaflac-rename-{i}{src.suffix}")
        src.replace(tmp)
        staged.append((src, tmp, dst))
    for src, tmp, dst in staged:
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp.replace(dst)
        res.files.append(dst)
        if src.resolve() != dst.resolve():
            res.renamed[str(src.resolve().relative_to(folder.resolve())).replace("\\", "/")] = dst.name
    if cover and write_cover_file:
        target = folder / "cover.jpg"
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(cover))
            if img.format == "JPEG":
                target.write_bytes(cover)
            else:
                buf = io.BytesIO()
                img.convert("RGB").save(buf, "JPEG", quality=92)
                target.write_bytes(buf.getvalue())
            res.cover = target
        except Exception as exc:
            res.problems.append(f"cover.jpg not written ({type(exc).__name__})")
    for d in sorted((p for p in folder.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        try:
            d.rmdir()                               # only succeeds when empty
        except OSError:
            pass
    return res


# ---- reading and checking ----------------------------------------------------------------------------------------

def read_track(path: Path) -> dict:
    """Normalised tags of one file: {"title", "artist", "albumartist", "album", "track", "tracktotal", "disc",
    "disctotal", "date", "genre", "has_art", "format", "bits", "rate", "kbps", "length"}, plus the MusicBrainz IDs
    (release, release group, recording and release track) when the file carries them."""
    from mutagen import File, MutagenError
    out = {"path": str(path), "has_art": False, "format": path.suffix.lower().lstrip(".").upper()}
    try:
        audio = File(str(path))
    except MutagenError as exc:                     # e.g. FLACNoHeaderError: the name says .flac, the bytes don't
        out["error"] = f"not a readable {out['format']} file ({type(exc).__name__})"
        return out
    if audio is None:
        out["error"] = "not an audio file mutagen can read"
        return out
    info = audio.info
    out["length"] = round(getattr(info, "length", 0) or 0, 2)
    out["rate"] = getattr(info, "sample_rate", 0)
    out["bits"] = getattr(info, "bits_per_sample", 0)
    out["kbps"] = round((getattr(info, "bitrate", 0) or 0) / 1000)
    if path.suffix.lower() == ".flac":
        tags = {k.upper(): v[0] for k, v in (audio.tags or {}).items()} if audio.tags else {}
        out["has_art"] = bool(audio.pictures)
        get = tags.get
        track, ttotal = parse_number(get("TRACKNUMBER"))
        disc, dtotal = parse_number(get("DISCNUMBER"))
        out.update(title=get("TITLE", ""), artist=get("ARTIST", ""), albumartist=get("ALBUMARTIST") or get("ALBUM ARTIST", ""),
                   album=get("ALBUM", ""), track=track, tracktotal=int(get("TRACKTOTAL") or get("TOTALTRACKS") or ttotal or 0),
                   disc=disc, disctotal=int(get("DISCTOTAL") or get("TOTALDISCS") or dtotal or 0),
                   date=get("DATE") or get("YEAR", ""), genre=get("GENRE", ""), mb_release_id=get("MUSICBRAINZ_ALBUMID", ""),
                   mb_releasegroup_id=get("MUSICBRAINZ_RELEASEGROUPID", ""), source_url=get("IAFLAC_SOURCE", ""),
                   mb_recording_id=get("MUSICBRAINZ_TRACKID", ""), mb_track_id=get("MUSICBRAINZ_RELEASETRACKID", ""),
                   label=get("LABEL") or get("ORGANIZATION", ""), catalog=get("CATALOGNUMBER", ""),
                   mb_albumartist_id=get("MUSICBRAINZ_ALBUMARTISTID", ""))
    else:
        tags = audio.tags or {}

        def text(frame):
            f = tags.get(frame)
            return str(f.text[0]) if f is not None and getattr(f, "text", None) else ""
        track, ttotal = parse_number(text("TRCK"))
        disc, dtotal = parse_number(text("TPOS"))
        out["has_art"] = any(k.startswith("APIC") for k in tags.keys())
        out.update(title=text("TIT2"), artist=text("TPE1"), albumartist=text("TPE2"), album=text("TALB"), track=track,
                   tracktotal=ttotal, disc=disc, disctotal=dtotal, date=text("TDRC"), genre=text("TCON"),
                   mb_release_id=text("TXXX:MusicBrainz Album Id"),
                   mb_releasegroup_id=text("TXXX:MusicBrainz Release Group Id"), source_url=text("TXXX:IAFLAC_SOURCE"),
                   mb_recording_id=_ufid(tags) or text("TXXX:MusicBrainz Track Id"),
                   mb_track_id=text("TXXX:MusicBrainz Release Track Id"), label=text("TPUB"),
                   catalog=text("TXXX:CATALOGNUMBER"), mb_albumartist_id=text("TXXX:MusicBrainz Album Artist Id"))
    return out


def _ufid(tags) -> str:
    """The MusicBrainz recording ID Picard stores in an MP3 (UFID frame owned by musicbrainz.org)."""
    frame = tags.get("UFID:http://musicbrainz.org") if hasattr(tags, "get") else None
    data = getattr(frame, "data", b"") or b""
    return data.decode("ascii", "replace") if data else ""


def folder_cover(folder: Path) -> Optional[Path]:
    lower = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
    return next((lower[n] for n in COVER_NAMES if n in lower), None)


def validate(folder: Path) -> list:
    """What a player would trip over in an album folder, as plain sentences (empty list: all good)."""
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS and not is_hidden(p, folder))
    if not files:
        return ["no FLAC or MP3 files"]
    tracks = [read_track(p) for p in files]
    issues = []
    for t in tracks:
        if t.get("error"):
            issues.append(f"{Path(t['path']).name}: {t['error']}")
    tracks = [t for t in tracks if not t.get("error")]
    for key, label in (("title", "TITLE"), ("album", "ALBUM"), ("track", "TRACKNUMBER")):
        missing = [Path(t["path"]).name for t in tracks if not t.get(key)]
        if missing:
            issues.append(f"{len(missing)} track(s) have no {label} ({missing[0]}{', ...' if len(missing) > 1 else ''})")
    if not any(t.get("albumartist") for t in tracks) and len({t.get("artist") for t in tracks}) > 1:
        issues.append("no ALBUMARTIST and the track artists differ: a player may split the album up")
    for key, label in (("album", "ALBUM"), ("albumartist", "ALBUMARTIST"), ("date", "DATE")):
        values = {t.get(key) for t in tracks if t.get(key)}
        if len(values) > 1:
            issues.append(f"tracks disagree on {label}: " + " / ".join(sorted(values)[:3]))
    for disc in sorted({t.get("disc") or 1 for t in tracks}):
        numbers = sorted(t["track"] for t in tracks if (t.get("disc") or 1) == disc and t.get("track"))
        if len(numbers) != len(set(numbers)):
            issues.append(f"disc {disc}: track numbers repeat")
        elif numbers and numbers != list(range(1, numbers[-1] + 1)):
            gaps = sorted(set(range(1, numbers[-1] + 1)) - set(numbers))
            issues.append(f"disc {disc}: missing track number(s) {', '.join(map(str, gaps[:6]))}")
    if len({t.get("disc") or 1 for t in tracks}) > 1 and not all(t.get("disctotal") for t in tracks):
        issues.append("multi-disc album without DISCTOTAL on every track")
    if not any(t.get("has_art") for t in tracks) and folder_cover(folder) is None:
        issues.append("no cover art (embedded or cover.jpg)")
    if any(p.suffix.lower() == ".cue" and not is_hidden(p, folder) for p in folder.rglob("*")):
        issues.append("has a .cue sheet: Volumio/MPD may list the album twice")
    return issues


# ---- comparing ---------------------------------------------------------------------------------------------------

ALBUM_FIELDS = ("albumartist", "album", "date", "genre", "label", "catalog")
TRACK_FIELDS = ("title", "track", "disc", "artist")


def diff(old: AlbumMeta, new: AlbumMeta) -> list:
    """[{"scope": "album"|"track", "source", "field", "old", "new"}] for every value that changes. Tracks are paired by
    their archive file (`source`)."""
    out = []
    for f in ALBUM_FIELDS:
        a, b = getattr(old, f), getattr(new, f)
        if (a or "") != (b or ""):
            out.append({"scope": "album", "source": "", "field": f, "old": a, "new": b})
    olds = {t.source: t for t in old.tracks}
    for t in new.tracks:
        o = olds.get(t.source)
        for f in TRACK_FIELDS:
            a, b = (getattr(o, f) if o else ""), getattr(t, f)
            if (a or "") != (b or ""):
                out.append({"scope": "track", "source": t.source, "field": f, "old": a, "new": b})
    return out
