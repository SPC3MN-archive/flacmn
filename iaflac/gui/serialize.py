"""Engine objects as plain JSON for the UI. Field names here are the API contract the frontend is written against."""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from .. import preview, quality, report
from ..altmedia import CODEC_NAME
from ..download import pick_cover
from ..library_index import YEAR_SUFFIX
from ..matching import guess_artist_album
from ..preview import short_licence
from ..tagging import title_from_name


def _match(cand, want) -> Optional[dict]:
    m = cand.match
    if m is None:
        return None
    return {"score": m.score, "bucket": m.bucket, "artist_score": m.artist_score, "album_score": m.album_score,
            "label": preview.match_label(want, cand) if want is not None else ("exact match" if m.bucket == 0 else "close match")}


def _integrity(cand) -> Optional[dict]:
    if not cand.integrity:
        return None
    ok = sum(1 for _, r in cand.integrity if r.ok)
    return {"checked": len(cand.integrity), "ok": ok, "of": len(cand.item.flacs),
            "all_ok": ok == len(cand.item.flacs) and ok == len(cand.integrity),
            "failures": [{"file": n, "message": r.message} for n, r in cand.integrity if not r.ok]}


def _spectral(cand) -> Optional[dict]:
    s = cand.spectral
    if s is None:
        return None
    return {"verdict": s.verdict, "detail": s.detail, "sample_rate": s.sample_rate, "has_png": bool(s.png),
            "windows": [{"track": w.track, "cutoff_hz": round(w.cutoff_hz), "step_db": round(w.step_db, 1), "flag": w.flag}
                        for w in s.windows]}


def candidate_summary(cand, want=None, library=None) -> dict:
    """One row of the results list."""
    it = cand.item
    q = quality.assess(cand)
    artist, album = guess_artist_album(it.title, it.creator, cand.stats.tag_artist if cand.stats else "",
                                       cand.stats.tag_album if cand.stats else "")
    bare = YEAR_SUFFIX.sub("", album or "")          # "Kind of Blue (1959)" is the library's "Kind of Blue"
    owned = library.find(artist, bare or album) if library is not None and artist and album else None
    runtime = it.runtime_seconds
    front = pick_cover(it)
    return {
        "identifier": it.identifier, "url": it.url, "title": it.title, "creator": it.creator, "year": it.year,
        "artist": artist, "album": album, "media": it.media,
        "quality": q.to_dict(), "tier": cand.tier, "levels": list(cand.levels),
        "match": _match(cand, want),
        "tracks": len(it.flacs), "runtime_s": round(runtime) if runtime else None, "bytes": it.flac_bytes,
        "downloads": it.downloads, "rip_files": preview.rip_files(it), "cover": front is not None,
        "cover_url": f"https://archive.org/download/{it.identifier}/{quote(front.name)}" if front is not None else None,
        "licence": short_licence(it.licenseurl), "free": it.is_free, "live": it.is_live,
        "collections": it.collections[:4], "source": preview.source_label(it),
        "claims": preview.description_hints(it), "other_lossless": it.other_lossless,
        "evidence": report.short_evidence(cand, 3) if cand.provenance else "",
        "integrity": _integrity(cand), "spectral": _spectral(cand),
        "alternates": list(cand.alternates),
        "in_library": owned.to_dict() if owned else None,
        "tested": cand.tier != "?",
    }


def candidate_detail(cand, want=None, library=None) -> dict:
    """Everything about one result: the summary plus every track, the evidence and the notes."""
    it = cand.item
    out = candidate_summary(cand, want, library)
    artist = out["artist"]
    tracks = []
    # an .m4a is named by what it holds once read (archive.org calls ALAC and AAC alike "Apple Lossless Audio")
    alt = cand.alt_stats
    codec = CODEC_NAME.get(alt.codec, "") if alt is not None and alt.codec in ("alac", "aac") else ""
    for f in it.flacs:
        probe = cand.probes.get(f.name)
        si = probe.streaminfo if probe is not None and probe.ok else None
        tracks.append({
            "name": f.name, "title": f.tags.get("title") or (probe.tags.get("TITLE") if probe is not None else "")
            or title_from_name(f.name, artist), "size": f.size, "length": f.length, "format": codec or f.format,
            "md5": f.md5,
            "probe": None if probe is None else {
                "ok": probe.ok, "kind": probe.kind, "detail": probe.detail,
                "sample_rate": si.sample_rate if si else None, "bits": si.bits_per_sample if si else None,
                "channels": si.channels if si else None, "seconds": round(si.duration, 2) if si else None,
                "md5_set": si.md5_set if si else None, "sector_aligned": si.sector_aligned if si else None,
                "vendor": probe.vendor, "tags": dict(probe.tags)},
        })
    integrity = {n: {"ok": r.ok, "message": r.message, "md5_ok": r.md5_ok} for n, r in cand.integrity}
    for t in tracks:
        t["integrity"] = integrity.get(t["name"])
    other = [{"name": f.name, "size": f.size, "format": f.format, "source": f.source} for f in it.files
             if f not in it.flacs and f.source != "derivative"]
    out.update({
        "description": it.description[:4000], "date": it.date, "subjects": it.subjects[:20],
        "track_list": tracks, "other_files": other,
        "provenance": {"tier": cand.tier, "score": cand.provenance.score if cand.provenance else None,
                       "label": cand.provenance.label if cand.provenance else "",
                       "reasons": list(cand.provenance.reasons) if cand.provenance else [],
                       "hard_fails": list(cand.provenance.hard_fail) if cand.provenance else []},
        "logs": [{"name": lg.file, "summary": lg.summary(), "strength": lg.strength} for lg in cand.logs],
        "issues": list(cand.issues), "notes": list(cand.notes),
        "format_label": cand.stats.format_label() if cand.stats else (cand.alt_stats.format_label() if cand.alt_stats else ""),
    })
    return out
