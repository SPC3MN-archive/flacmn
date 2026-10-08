"""Match an album against MusicBrainz and turn the chosen release into tags; front covers from the Cover Art Archive.

A release is scored on what an upload can actually tell us: how many tracks it has (per disc), how long each one
runs (archive.org lists lengths, the FLAC headers give exact ones), and how close the artist and album names are.
Track lengths are the strong signal: two pressings of one album rarely share every length to within a few seconds,
while a different edition (bonus tracks, a remaster with longer fades) shows up immediately.

MusicBrainz asks for a meaningful User-Agent and at most one request per second; both are enforced here, and every
answer is cached on disk for a week.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import requests

from . import paths
from .ia_client import USER_AGENT
from .matching import similarity
from .tagging import AlbumMeta, TrackMeta

WS = "https://musicbrainz.org/ws/2"
CAA = "https://coverartarchive.org"
TTL = 7 * 24 * 3600
MIN_INTERVAL = 1.05
LENGTH_SCALE = 4.0           # seconds of mean per-track difference at which the length score falls to 1/e
RELEASE_INC = "recordings+artist-credits+labels+release-groups+genres+media"


class MBError(RuntimeError):
    pass


@dataclass
class ReleaseMatch:
    release_id: str
    title: str
    artist: str
    date: str = ""
    country: str = ""
    label: str = ""
    catalog: str = ""
    formats: str = ""            # "CD", "2×CD", "Digital Media", "12\" Vinyl"
    discs: list = field(default_factory=list)          # tracks per disc, e.g. [10] or [9, 8]
    score: float = 0.0           # 0..1, our own (not MusicBrainz's search score)
    length_delta: Optional[float] = None               # mean |difference| in seconds over the paired tracks
    reasons: list = field(default_factory=list)
    release_group_id: str = ""
    status: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class MBClient:
    def __init__(self, cache_dir: Optional[Path] = None, session=None, user_agent: str = USER_AGENT,
                 min_interval: float = MIN_INTERVAL):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.session.headers["Accept"] = "application/json"
        self.cache_dir = Path(cache_dir) if cache_dir else paths.cache_dir() / "musicbrainz"
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def _get(self, url: str, params: dict, ttl: float = TTL) -> dict:
        key = hashlib.sha1(json.dumps([url, params], sort_keys=True).encode()).hexdigest()
        path = self.cache_dir / key[:2] / f"{key}.json"
        try:
            if time.time() - path.stat().st_mtime <= ttl:
                return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        delay = 2.0
        for attempt in range(4):
            with self._lock:
                wait = self._last + self.min_interval - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
            try:
                resp = self.session.get(url, params={**params, "fmt": "json"}, timeout=(10, 30))
            except (requests.ConnectionError, requests.Timeout) as exc:
                err = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code == 200:
                    data = resp.json()
                    try:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
                        tmp.write_text(json.dumps(data), encoding="utf-8")
                        os.replace(tmp, path)
                    except OSError:
                        pass
                    return data
                if resp.status_code == 404:
                    raise MBError(f"not found: {url}")
                err = f"HTTP {resp.status_code}"
                if resp.status_code not in (429, 500, 502, 503, 504):
                    raise MBError(f"{err} for {url}")
            if attempt < 3:
                time.sleep(delay)
                delay *= 2
        raise MBError(f"MusicBrainz did not answer: {err}")

    def search_releases(self, artist: str, album: str, limit: int = 15) -> list:
        def q(s: str) -> str:
            return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
        parts = [f"release:{q(album)}"] if album else []
        if artist:
            parts.append(f"artist:{q(artist)}")
        if not parts:
            return []
        data = self._get(f"{WS}/release/", {"query": " AND ".join(parts), "limit": limit})
        return data.get("releases", [])

    def release(self, release_id: str) -> dict:
        return self._get(f"{WS}/release/{release_id}", {"inc": RELEASE_INC})

    def group_releases(self, release_group_id: str, limit: int = 100) -> list:
        """Every release (pressing) in a release group, with its media track counts and Cover Art Archive counts."""
        data = self._get(f"{WS}/release", {"release-group": release_group_id, "inc": "media", "limit": limit})
        return data.get("releases", [])

    def art_listing(self, release_id: str) -> list:
        """The Cover Art Archive's images for a release: [{"types": ["Back", "Spine"], "image": url, "thumbnails":
        {...}, "comment", "id"}], or [] when it has none. Cached like the MusicBrainz answers."""
        key = hashlib.sha1(f"caa\0{release_id}".encode()).hexdigest()
        path = self.cache_dir / "caa" / key[:2] / f"{key}.json"
        try:
            if time.time() - path.stat().st_mtime <= TTL:
                return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        try:
            resp = self.session.get(f"{CAA}/release/{release_id}", timeout=(10, 30), allow_redirects=True)
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise MBError(f"Cover Art Archive did not answer: {exc}") from exc
        if resp.status_code == 404:
            images = []
        elif resp.status_code == 200:
            images = resp.json().get("images", [])
        else:
            raise MBError(f"Cover Art Archive: HTTP {resp.status_code}")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(images), encoding="utf-8")
        except OSError:
            pass
        return images

    def image(self, url: str) -> bytes:
        """An image from the Cover Art Archive (or any https URL it redirects to), cached on disk for good."""
        key = hashlib.sha1(url.encode()).hexdigest()
        path = self.cache_dir / "images" / key[:2] / key
        if path.exists():
            return path.read_bytes()
        try:
            resp = self.session.get(url, timeout=(10, 120), allow_redirects=True)
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise MBError(f"could not fetch {url}: {exc}") from exc
        if resp.status_code != 200 or not resp.content:
            raise MBError(f"could not fetch {url}: HTTP {resp.status_code}")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(resp.content)
        os.replace(tmp, path)
        return resp.content

    def image_size(self, url: str) -> tuple:
        """(width, height) of a remote image from its first 64 KB (one ranged request), cached; (0, 0) if unknown."""
        key = hashlib.sha1(f"size\0{url}".encode()).hexdigest()
        path = self.cache_dir / "sizes" / key[:2] / f"{key}.json"
        try:
            return tuple(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        try:
            resp = self.session.get(url, headers={"Range": "bytes=0-65535"}, timeout=(10, 30), allow_redirects=True,
                                    stream=True)
        except (requests.ConnectionError, requests.Timeout):
            return (0, 0)
        try:
            if resp.status_code not in (200, 206):
                return (0, 0)
            head = b""
            for chunk in resp.iter_content(16384):
                head += chunk
                if len(head) >= 65536:
                    break
        finally:
            resp.close()
        size = header_size(head)
        if size != (0, 0):
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(size), encoding="utf-8")
            except OSError:
                pass
        return size

    def cover(self, release_id: str, release_group_id: str = "", size: str = "1200") -> Optional[bytes]:
        """Front cover bytes from the Cover Art Archive (the release's, else its release group's), or None."""
        for base in ([f"{CAA}/release/{release_id}"] + ([f"{CAA}/release-group/{release_group_id}"] if release_group_id else [])):
            try:
                resp = self.session.get(f"{base}/front-{size}", timeout=(10, 60), allow_redirects=True)
            except (requests.ConnectionError, requests.Timeout):
                continue
            if resp.status_code == 200 and resp.content:
                return resp.content
        return None


def header_size(head: bytes) -> tuple:
    """(width, height) from the first bytes of a JPEG/PNG/GIF/WebP, or (0, 0) when they don't say yet."""
    from PIL import ImageFile
    parser = ImageFile.Parser()
    try:
        parser.feed(head)
    except Exception:
        return (0, 0)
    img = parser.image
    return tuple(img.size) if img is not None else (0, 0)


# ---- reading a release -------------------------------------------------------------------------------------------

def credit_name(credits: list) -> str:
    """'Miles Davis', or 'Simon & Garfunkel' from an artist-credit list with join phrases."""
    return "".join((c.get("name") or (c.get("artist") or {}).get("name", "")) + c.get("joinphrase", "")
                   for c in credits or [])


def release_tracks(release: dict) -> list:
    """[[(title, seconds or None, track id, recording id, track artist), ...] per disc] for audio media only."""
    discs = []
    for medium in release.get("media", []):
        if (medium.get("format") or "").lower() in ("dvd-video", "blu-ray", "dvd"):
            continue
        tracks = []
        for t in medium.get("tracks", []):
            ms = t.get("length") or (t.get("recording") or {}).get("length")
            tracks.append((t.get("title") or (t.get("recording") or {}).get("title", ""),
                           ms / 1000 if ms else None, t.get("id", ""), (t.get("recording") or {}).get("id", ""),
                           credit_name(t.get("artist-credit") or [])))
        if tracks:
            discs.append(tracks)
    return discs


def format_summary(release: dict) -> str:
    fmts = [m.get("format") or "?" for m in release.get("media", [])]
    if not fmts:
        return ""
    if len(set(fmts)) == 1 and len(fmts) > 1:
        return f"{len(fmts)}×{fmts[0]}"
    return " + ".join(fmts)


def _label(release: dict) -> tuple:
    info = (release.get("label-info") or [{}])[0] or {}
    return (info.get("label") or {}).get("name", ""), info.get("catalog-number") or ""


def top_genre(release: dict) -> str:
    genres = (release.get("genres") or []) + ((release.get("release-group") or {}).get("genres") or [])
    if not genres:
        return ""
    best = max(genres, key=lambda g: g.get("count", 0))
    return best.get("name", "").title()


# ---- scoring -----------------------------------------------------------------------------------------------------

def _discs_of(meta: AlbumMeta) -> list:
    out = []
    for disc in sorted({t.disc for t in meta.tracks}):
        out.append([t for t in meta.tracks if t.disc == disc])
    return out


def score_release(meta: AlbumMeta, release: dict, want_artist: str = "", want_album: str = "") -> ReleaseMatch:
    discs = release_tracks(release)
    label, catno = _label(release)
    m = ReleaseMatch(release_id=release.get("id", ""), title=release.get("title", ""),
                     artist=credit_name(release.get("artist-credit") or []), date=release.get("date", ""),
                     country=release.get("country", ""), label=label, catalog=catno, formats=format_summary(release),
                     discs=[len(d) for d in discs], release_group_id=(release.get("release-group") or {}).get("id", ""),
                     status=release.get("status", ""))
    ours = _discs_of(meta)
    ours_n, theirs_n = [len(d) for d in ours], m.discs
    total_ours, total_theirs = sum(ours_n), sum(theirs_n)
    if ours_n == theirs_n:
        count = 1.0
        m.reasons.append(f"same track count ({total_ours}" + (f" on {len(ours_n)} discs)" if len(ours_n) > 1 else ")"))
    elif total_ours == total_theirs:
        count = 0.85
        m.reasons.append(f"same {total_ours} tracks, split into discs differently ({'/'.join(map(str, theirs_n))})")
    else:
        count = max(0.0, 1 - abs(total_ours - total_theirs) / max(total_ours, total_theirs, 1)) * 0.6
        m.reasons.append(f"{total_theirs} tracks on MusicBrainz vs {total_ours} here")
    flat_ours = [t for d in ours for t in d]
    flat_theirs = [t for d in discs for t in d]
    pairs = [(a.length, b[1]) for a, b in zip(flat_ours, flat_theirs) if a.length and b[1]]
    if pairs and total_ours == total_theirs:
        m.length_delta = sum(abs(a - b) for a, b in pairs) / len(pairs)
        length = math.exp(-m.length_delta / LENGTH_SCALE)
        m.reasons.append(f"track lengths differ by {m.length_delta:.1f} s on average ({len(pairs)} compared)")
    else:
        length = 0.3 if total_ours == total_theirs else 0.0
        if total_ours == total_theirs:
            m.reasons.append("no track lengths to compare")
    names = 0.5 * (similarity(want_artist or meta.albumartist, m.artist) if (want_artist or meta.albumartist) else 1.0) \
        + 0.5 * (similarity(want_album or meta.album, m.title) if (want_album or meta.album) else 1.0)
    m.score = round(0.3 * count + 0.45 * length + 0.25 * names, 3)
    if m.status and m.status.lower() != "official":
        m.score = round(m.score * 0.95, 3)
        m.reasons.append(f"status: {m.status}")
    return m


def find_matches(client: MBClient, meta: AlbumMeta, artist: str = "", album: str = "", limit: int = 8,
                 lookups: int = 6) -> list:
    """Search, then look up the most promising releases (search results carry no track lengths) and score them.
    Returns ReleaseMatch list, best first."""
    artist, album = artist or meta.albumartist, album or meta.album
    hits = client.search_releases(artist, album, limit=25)
    total = len(meta.tracks)

    def pre(r):
        n = r.get("track-count") or sum(mm.get("track-count", 0) for mm in r.get("media", []))
        return (abs(n - total), -int(r.get("score", 0)))

    out = []
    for r in sorted(hits, key=pre)[:lookups]:
        try:
            full = client.release(r["id"])
        except MBError:
            continue
        out.append(score_release(meta, full, artist, album))
    out.sort(key=lambda m: -m.score)
    return out[:limit]


def apply_release(meta: AlbumMeta, release: dict) -> tuple:
    """(new AlbumMeta, warnings). Album fields always come from the release; track titles, numbers and IDs only when
    the track counts agree, paired in order."""
    warnings = []
    new = AlbumMeta.from_dict(meta.to_dict())
    label, catno = _label(release)
    new.albumartist = credit_name(release.get("artist-credit") or []) or new.albumartist
    new.album = release.get("title") or new.album
    new.date = release.get("date") or new.date
    new.label, new.catalog = label or new.label, catno or new.catalog
    new.genre = top_genre(release) or new.genre
    new.mb_release_id = release.get("id", "")
    new.mb_releasegroup_id = (release.get("release-group") or {}).get("id", "")
    credits = release.get("artist-credit") or []
    new.mb_albumartist_id = (credits[0].get("artist") or {}).get("id", "") if credits else ""
    for f in ("albumartist", "album", "date", "label", "catalog", "genre"):
        new.sources[f] = "musicbrainz"
    discs = release_tracks(release)
    flat = [(d, i, t) for d, tracks in enumerate(discs, 1) for i, t in enumerate(tracks, 1)]
    if len(flat) != len(new.tracks):
        warnings.append(f"MusicBrainz has {len(flat)} tracks and this upload {len(new.tracks)}: only the album fields "
                        "were taken; check the track titles yourself")
        return new, warnings
    ordered = sorted(new.tracks, key=lambda t: (t.disc, t.track))
    for t, (disc, pos, (title, _secs, track_id, rec_id, track_artist)) in zip(ordered, flat):
        t.title, t.disc, t.track = title or t.title, disc, pos
        t.mb_track_id, t.mb_recording_id = track_id, rec_id
        t.artist = "" if not track_artist or track_artist == new.albumartist else track_artist
    new.tracks = ordered
    new.sources["tracks"] = "musicbrainz"
    return new, warnings
