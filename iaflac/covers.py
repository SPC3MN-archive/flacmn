"""Front covers for an album from the Cover Art Archive, found through MusicBrainz, and only when you ask for them.

Which releases: the ones the album's tags lead to (its MusicBrainz release ID, the releases its tracks are on), then a
search by artist, album and song titles (health.candidate_releases, the same as the health check's comparison), then
the other pressings in the best one's release group (other editions often have other covers). For each, the Cover Art
Archive's front images; releases the album's own tags name come first.

Licensing, for a paid app: what is read from MusicBrainz here (titles, artists, dates, countries, labels, catalogue
numbers, IDs) is its CC0 core data. The Cover Art Archive makes no claim to the images: they belong to their owners.
So nothing is fetched until you ask for an album's covers, each one is shown with where it came from, and the one you
choose is saved only into your own album folder, where it labels the music you already have. Nothing is bundled,
uploaded or shared.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Callable, Optional

from . import health, musicbrainz as mb
from .events import CancelCheck, check

RELEASES = 6               # releases looked at per search: each is one MusicBrainz lookup, at its one request a second
SAVE_SIZE = "1200"         # the Cover Art Archive's 1200 px version is saved: sharp on any player, without a 20 MB scan
CREDIT = "Cover Art Archive (coverartarchive.org), found through MusicBrainz"


@dataclass
class CoverOffer:
    id: str                       # short, stable: a hash of the image's address
    release_id: str
    title: str
    artist: str
    date: str = ""
    country: str = ""
    label: str = ""
    catalog: str = ""
    formats: str = ""             # "CD", "12\" Vinyl", "Digital Media"
    exact: bool = False           # the album's own tags name this release
    image: str = ""               # the original, as uploaded
    large: str = ""               # the 1200 px version (what is saved), or the original when there is none
    thumb: str = ""               # 500 px, for the picker
    approved: bool = True         # checked by a MusicBrainz editor
    comment: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def offer_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def _offers(release: dict, images: list, exact: bool) -> list:
    label, catalog = mb._label(release)
    # one per release: its main front (the archive's "front" flag), else the first image typed Front
    fronts = [im for im in images if im.get("image") and im.get("front")] or \
             [im for im in images if im.get("image") and "Front" in (im.get("types") or [])]
    out = []
    for im in fronts[:1]:
        thumbs = im.get("thumbnails") or {}
        image = im["image"]
        out.append(CoverOffer(
            id=offer_id(image), release_id=release.get("id", ""), title=release.get("title", ""),
            artist=mb.credit_name(release.get("artist-credit") or []), date=release.get("date", ""),
            country=release.get("country", ""), label=label, catalog=catalog, formats=mb.format_summary(release),
            exact=exact, image=image, large=thumbs.get(SAVE_SIZE) or thumbs.get("large") or image,
            thumb=thumbs.get("500") or thumbs.get("small") or image, approved=bool(im.get("approved", True)),
            comment=im.get("comment", "")))
    return out


def find(mb_client, h: health.AlbumHealth, *, limit: int = RELEASES,
         progress: Optional[Callable[[dict], None]] = None, cancel: Optional[CancelCheck] = None) -> dict:
    """{"covers": [CoverOffer dicts], "releases": how many releases were looked at, "credit"}. The same image is
    offered once, however many releases share it."""
    emit = progress or (lambda e: None)
    emit({"stage": "covers", "step": "releases"})
    candidates = health.candidate_releases(mb_client, h)[:limit]
    offers, seen, looked, groups = [], set(), set(), []

    def add(new: list) -> None:
        for o in new:
            if o.id not in seen:
                seen.add(o.id)
                offers.append(o)

    for i, (rid, exact) in enumerate(candidates, 1):
        check(cancel)
        emit({"stage": "covers", "step": "release", "index": i, "total": len(candidates)})
        try:
            release = mb_client.release(rid)
        except mb.MBError:
            continue
        looked.add(rid)
        groups.append((release.get("release-group") or {}).get("id", ""))
        caa = release.get("cover-art-archive") or {}
        if caa and not caa.get("front"):                 # MusicBrainz already says it has no front cover
            continue
        try:
            images = mb_client.art_listing(rid)
        except mb.MBError:
            continue
        add(_offers(release, images, exact))
    # the other pressings of the album: one browse of its release group, which already says which have a front cover
    group = next((g for g in groups if g), "")
    if group and len(looked) < limit * 2:
        try:
            pressings = mb_client.group_releases(group)
        except mb.MBError:
            pressings = []
        for r in pressings:
            if len(looked) >= limit * 2:
                break
            if r.get("id") in looked or not (r.get("cover-art-archive") or {}).get("front"):
                continue
            check(cancel)
            looked.add(r["id"])
            emit({"stage": "covers", "step": "pressing", "index": len(looked)})
            try:
                images = mb_client.art_listing(r["id"])
            except mb.MBError:
                continue
            add(_offers(r, images, False))
    offers.sort(key=lambda o: (not o.exact, not o.approved))     # stable: the candidates' own order otherwise
    return {"covers": [o.to_dict() for o in offers], "releases": len(looked), "credit": CREDIT}
