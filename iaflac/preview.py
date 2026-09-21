"""What the search found, before anything is tested.

Everything here comes from archive.org's search results and item records: no file is opened, so there are no
tiers or verdicts and no audio facts (sample rate, bit depth). Those need the tests, which only run for the
candidates the user picks (see `parse_selection`).
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Optional

from . import matching, provenance as prov
from .download import pick_cover
from .inventory import LEADING_NUMBER, clean_song

HEADER = ("Search results. Nothing has been tested yet: this is what archive.org's search and item records say, "
          "and no file has been opened.")
TEST_NOTE = ("Testing reads the files themselves: small header reads and the rip log first; then, unless you pass "
             "--deep none, a full decode test and spectrum check of the best pick, which streams that album's whole "
             "FLAC set (its size is listed above).")

FORMAT_NAMES = {"Flac": "FLAC", "24bit Flac": "24-bit FLAC"}


def term_width() -> int:
    return max(80, min(200, shutil.get_terminal_size((100, 24)).columns))


def cut(text: str, width: int) -> str:
    """Truncate to `width` characters (unlike textwrap.shorten this works on titles with no spaces)."""
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: max(1, width - 1)] + "…"


def short_licence(url: str) -> str:
    m = re.search(r"creativecommons\.org/licenses/([a-z\-]+)/([\d.]+)", url or "", re.I)
    if m:
        return f"CC {m.group(1).upper()} {m.group(2)}"
    if "publicdomain" in (url or "").lower():
        return "public domain"
    return url or ""


def rip_files(item) -> list:
    """Rip evidence the item ships, by name only (the files are not read here)."""
    out = []
    for kind, label in (("log", "log"), ("cue", "cue"), ("accurip", ".accurip"), ("aucdtect", "auCDtect")):
        n = len(item.files_of(kind))
        if n:
            out.append(label + (f" x{n}" if n > 1 else ""))
    return out


def format_label(item) -> str:
    """archive.org's own format label per file: 'Flac', or '24bit Flac' for hi-res. Not verified."""
    if getattr(item, "media", "flac") == "wav":
        return "WAV, converted to FLAC when saved"
    if getattr(item, "media", "flac") == "mp3":
        return "VBR MP3, lossy"
    seen = []
    for f in item.flacs:
        name = FORMAT_NAMES.get(f.format, f.format or "FLAC")
        if name not in seen:
            seen.append(name)
    return " + ".join(seen) or "FLAC"


def track_preview(item, artist: str) -> str:
    names = [clean_song(f.name, artist) for f in item.flacs]
    if len(names) >= 3:                       # "Solange Plexus - Drum Charged - 01 Chance Of ..." -> "Chance Of ..."
        prefix = os.path.commonprefix(names)
        if " - " in prefix:
            prefix = prefix[: prefix.rfind(" - ") + 3]
            stripped = [LEADING_NUMBER.sub("", n[len(prefix):]).strip() for n in names]
            if all(stripped):
                names = stripped
    names = [cut(n, 44) for n in names]
    if len(names) > 6:                       # first three and the last, so a bonus or alternate take still shows
        names = names[:3] + ["…", names[-1]]
    return " · ".join(names)


def description_hints(item) -> str:
    """What the uploader's text claims (a claim, not a check)."""
    claims = prov.scan_claims(item)
    parts = []
    if claims["cd"]:
        parts.append("mentions " + ", ".join(claims["cd"][:3]))
    if claims["other"]:
        parts.append("points to " + ", ".join(claims["other"]))
    if claims["fake"]:
        parts.append("WARNING: mentions " + ", ".join(claims["fake"]))
    return "; ".join(parts)


def source_label(item) -> str:
    """What kind of upload this looks like, from the rip files it ships and what its uploader says (claims, not checks)."""
    kind = {"wav": "WAV", "mp3": "VBR MP3"}.get(getattr(item, "media", "flac"), "FLAC")
    if item.files_of("log") or item.files_of("cue"):
        return "CD rip (ships a rip log or cue)"
    claims = prov.scan_claims(item)
    if claims["cd"]:
        return "CD rip (claimed by the uploader)"
    if "vinyl" in claims["other"]:
        return "vinyl rip (claimed by the uploader)"
    if claims["other"]:
        return "/".join(claims["other"]) + " source (claimed by the uploader)"
    return f"plain {kind} (source not stated)"


def artist_label(artist: str, item) -> str:
    """How an item relates to an artist-only request. Finding the name inside a title is weak evidence, so say so."""
    if item.creator and matching.similarity(artist, item.creator) >= 0.9 and matching.normalize(artist) == matching.normalize(item.creator):
        return "creator is this artist"
    if item.creator and matching.containment(artist, item.creator) == 1.0:
        return "name is part of the creator"
    if matching.containment(artist, item.title) == 1.0:
        return "name only appears in the title"
    return "weak match"


def match_label(want, cand) -> Optional[str]:
    if cand.match is None:
        return None
    if want.album:
        return "exact match" if cand.match.bucket == 0 else "close match"
    return artist_label(want.artist, cand.item)


def facts(cand, artist: str = "") -> dict:
    """Everything the preview knows about a candidate, as plain values (also used in the JSON report)."""
    it = cand.item
    runtime = it.runtime_seconds
    if not artist:
        artist = matching.guess_artist_album(it.title, it.creator)[0]
    return {
        "identifier": it.identifier, "url": it.url, "title": it.title, "creator": it.creator, "year": it.year,
        "flac_files": len(it.flacs), "format": format_label(it),
        "runtime_min": round(runtime / 60, 1) if runtime else None, "flac_mb": round(it.flac_bytes / 1e6),
        "downloads": it.downloads, "rip_files": rip_files(it), "cover": pick_cover(it) is not None,
        "licence": short_licence(it.licenseurl), "free": it.is_free, "live": it.is_live,
        "collections": it.collections[:3], "other_lossless": it.other_lossless,
        "tracks": track_preview(it, artist), "description": description_hints(it), "source": source_label(it),
    }


def candidate_lines(index: int, cand, artist: str = "", label: Optional[str] = None, same_as: int = 0, width: Optional[int] = None) -> list:
    f, width = facts(cand, artist), width or term_width()
    lines = [f"{index:>3}. {cut(f['title'] or f['identifier'], max(30, width - 8))}"]
    bits = []
    if label:
        bits.append(label)
    bits.append(f["source"])
    if same_as:
        bits.append(f"another upload or edition of #{same_as}")
    creator = f["creator"]
    if creator and creator.casefold() not in (f["title"] or "").casefold():
        bits.append("by " + cut(creator, 40))
    word = {"wav": "WAV", "mp3": "MP3"}.get(cand.item.media, "FLAC")
    bits += [f"{f['flac_files']} {word} ({f['format']})",
             f"{f['runtime_min']:.1f} min" if f["runtime_min"] else "runtime not listed", f"{f['flac_mb']:,} MB"]
    if f["year"]:
        bits.append(str(f["year"]))
    bits.append(f"{f['downloads']:,} downloads")
    lines.append("     " + " · ".join(bits))
    bits = ["rip files: " + (", ".join(f["rip_files"]) or "none"), "cover art: " + ("yes" if f["cover"] else "no")]
    if f["licence"]:
        bits.append("licence: " + f["licence"] + (" (free to share)" if f["free"] else ""))
    if f["live"]:
        bits.append("LIVE")
    if f["other_lossless"]:
        bits.append("also has " + "/".join(f["other_lossless"]))
    if f["collections"]:
        bits.append("in " + ", ".join(f["collections"]))
    lines.append("     " + " · ".join(bits))
    lines.append("     tracks: " + f["tracks"])
    if f["description"]:
        lines.append("     description " + f["description"])
    lines.append("     " + f["url"])
    return lines


def format_find_preview(res, position: int, total: int) -> str:
    want, found = res.want, res.found
    lines = ["", f"[{position}/{total}] {want.label}   " + (f"({len(found)} found)" if found else "(no FLAC match)")]
    if res.rungs:
        lines.append("  searched: " + " → ".join(f"{label} ({n})" for label, n in res.rungs))
    for reason, n in res.excluded.items():
        lines.append(f"  hidden: {n} x {reason}")
    for note in res.notes:
        lines.append(f"  note: {note}")
    keys: dict = {}
    for i, cand in enumerate(found, 1):
        key = matching.cluster_key(*matching.guess_artist_album(cand.item.title, cand.item.creator))
        first = keys.setdefault(key, i)
        lines.append("")
        lines += candidate_lines(i, cand, want.artist, match_label(want, cand), first if first != i else 0)
    if not found:
        if res.near_misses:
            lines += ["", "  FLAC items that did not match well enough:"]
            lines += [f"    - {n['identifier']}  {n['title'][:70]}" for n in res.near_misses[:5]]
        if res.lossless_other:
            lines += ["", "  Lossless in other formats (not FLAC; convertible):"]
            lines += [f"    - {n['identifier']}  {n['title'][:70]}" for n in res.lossless_other]
    return "\n".join(lines)


def format_genre_preview(res) -> str:
    spec, found, width = res.spec, res.found, term_width()
    lines = ["", f"Genre '{spec.genre}': matching {', '.join(spec.terms)}"
             + (f"  (excluding {', '.join(spec.exclude)})" if spec.exclude else "")]
    for label, n, _query in res.searched:
        lines.append(f"  searched: {label}: {n} hit(s)")
    for reason, n in res.excluded.items():
        lines.append(f"  hidden: {n} x {reason}")
    for note in res.notes:
        lines.append(f"  note: {note}")
    if not found:
        return "\n".join(lines + ["", "  Nothing album-shaped was found."])
    lines += ["", f"  {'#':>3}  {'genre match':<11} {'files':>5}  {'min':>5}  {'MB':>6}  {'year':>4}  {'downloads':>9}  "
                  f"{'rip files':<15} title  [identifier]"]
    for i, cand in enumerate(found, 1):
        it = cand.item
        runtime = it.runtime_seconds
        rip = "+".join(k for k in ("log", "cue", "accurip", "aucdtect") if it.files_of(k)) or "-"
        title = cut(it.title or it.identifier, max(14, width - 78 - len(it.identifier)))
        lines.append(f"  {i:>3}  {cand.genre_conf or '':<11} {len(it.flacs):>5}  "
                     f"{(f'{runtime / 60:.0f}' if runtime else '?'):>5}  {round(it.flac_bytes / 1e6):>6,}  "
                     f"{(it.year or ''):>4}  {it.downloads:>9,}  {rip:<15} {title}  [{it.identifier}]")
    lines += ["", "  Details for any row: https://archive.org/details/<identifier>"]
    return "\n".join(lines)


# ---- choosing what to test ---------------------------------------------------------------------

class SelectionError(ValueError):
    pass


@dataclass
class Selection:
    action: str                                   # "test" | "skip" | "quit"
    indexes: list = field(default_factory=list)   # 0-based, sorted, unique
    default_for_rest: bool = False                # 'd': use the default for every remaining request
    dropped: list = field(default_factory=list)   # numbers that were out of range (only when strict=False)


def parse_selection(text: str, n: int, default: str = "1", strict: bool = True) -> Selection:
    """Parse an answer such as '', '1', '1,3', '2-4', '1 3-5', 'a', 's', 'd' or 'q' against n listed candidates.
    Enter (empty) means `default`. strict=False (for --select) drops out-of-range numbers instead of failing."""
    raw = (text or "").strip().lower() or default.strip().lower()
    if raw in ("q", "quit", "exit"):
        return Selection("quit")
    if raw in ("s", "skip", "n", "no", "none"):
        return Selection("skip")
    if raw == "d":
        chosen = parse_selection("", n, default, strict)
        chosen.default_for_rest = True
        return chosen
    if raw in ("a", "all"):
        return Selection("test", list(range(n)))
    picked, dropped = set(), []
    for token in filter(None, re.split(r"[,\s]+", raw)):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if not m:
            raise SelectionError(f"'{token}' is not a number or a range")
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        if hi < lo:
            raise SelectionError(f"'{token}' is a backwards range")
        for k in range(lo, hi + 1):
            (picked.add(k - 1) if 1 <= k <= n else dropped.append(k))
    if dropped and strict:
        raise SelectionError(f"{sorted(set(dropped))} is outside 1-{n}")
    if not picked:
        if strict:
            raise SelectionError("nothing selected")
        return Selection("skip", dropped=sorted(set(dropped)))
    return Selection("test", sorted(picked), dropped=sorted(set(dropped)))


def find_prompt() -> str:
    return ("Test which?   Enter = 1 (top listed) · numbers like 1,3 or 2-4 · a = all · s = skip · "
            "d = Enter for all remaining requests · q = quit\n> ")


def genre_prompt(default_n: int) -> str:
    return (f"Inspect which?   Enter = 1-{default_n} · numbers like 1,3 or 2-9 · a = all · q = quit\n> ")
