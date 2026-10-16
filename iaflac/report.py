"""Render results: report.md, report.csv, candidates.json, plus terminal summaries."""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Optional

from . import preview
from .pipeline import Candidate, WantResult


def fmt_minutes(seconds: Optional[float]) -> str:
    return f"{seconds / 60:.1f} min" if seconds else "? min"


def fmt_mb(nbytes: int) -> str:
    return f"{nbytes / 1e6:,.0f} MB"


MEDIA_WORD = {"flac": "FLAC", "wav": "WAV", "aiff": "AIFF", "m4a": "M4A", "ape": "APE", "wv": "WavPack", "mp3": "VBR MP3"}


def facts(c: Candidate) -> str:
    st = c.alt_stats if c.item.media != "flac" else c.stats
    return (f"{len(c.item.flacs)} {MEDIA_WORD.get(c.item.media, 'FLAC')} · {st.format_label() if st else '?'} · "
            f"{fmt_minutes(c.item.runtime_seconds)} · {fmt_mb(c.item.flac_bytes)}")


def verification(c: Candidate) -> str:
    """One line per level reached: what was actually checked, and how it came out."""
    st, parts = c.stats, ["V0 ✓ metadata"]
    if st and st.n:
        if st.bad:
            kinds = ", ".join(sorted({k for _, k, _ in st.bad}))
            parts.append(f"V1 ✗ {len(st.bad)}/{st.n} files are not FLAC ({kinds})")
        else:
            parts.append(f"V1 ✓ {st.ok}/{st.n} headers are real FLAC")
    if c.alt_stats and c.alt_stats.n:
        a, word = c.alt_stats, MEDIA_WORD.get(c.item.media, "")
        if a.bad:
            parts.append(f"V1 ✗ {len(a.bad)}/{a.n} files are not real {word} ({', '.join(sorted({k for _, k, _ in a.bad}))})")
        else:
            parts.append(f"V1 ✓ {a.ok}/{a.n} headers are real {word} ({a.format_label()})")
    if c.integrity:
        ok = sum(r.ok for _, r in c.integrity)
        bits = "; ".join(sorted({r.message for _, r in c.integrity if r.message}))[:120]
        parts.append(f"V2 {'✓' if ok == len(c.integrity) else '✗'} {ok}/{len(c.integrity)} files decode "
                     f"cleanly{' (' + bits + ')' if bits and ok != len(c.integrity) else ''}")
    if c.spectral:
        parts.append("V3 " + c.spectral.summary())
    return " · ".join(parts)


def short_evidence(c: Candidate, n: int = 3) -> str:
    if not c.provenance:
        return ""
    picks = [r[2:] for r in c.provenance.reasons if r.startswith(("✓", "✗"))]
    return "; ".join(picks[:n])


def candidate_dict(c: Candidate) -> dict:
    it, st, pv = c.item, c.stats, c.provenance
    return {
        "identifier": it.identifier, "url": it.url, "title": it.title, "creator": it.creator, "year": it.year,
        "collections": it.collections, "licenseurl": it.licenseurl, "is_free": it.is_free, "is_live": it.is_live,
        "downloads": it.downloads, "flac_files": len(it.flacs), "flac_bytes": it.flac_bytes,
        "runtime_seconds": it.runtime_seconds, "format": st.format_label() if st else "",
        "aligned": f"{st.aligned}/{st.ok}" if st else "", "tier": pv.tier if pv else "?",
        "score": pv.score if pv else None, "label": pv.label if pv else "", "reasons": pv.reasons if pv else [],
        "hard_fail": pv.hard_fail if pv else [], "levels": c.levels, "verification": verification(c),
        "match": {"score": c.match.score, "artist": c.match.artist_score, "album": c.match.album_score} if c.match else None,
        "via": c.via, "logs": [lg.summary() for lg in c.logs], "aucdtect": c.aucdtect,
        "sidecars": {k: len(it.files_of(k)) for k in ("log", "cue", "accurip", "sfv", "md5", "aucdtect") if it.files_of(k)},
        "integrity": [{"file": n, **r.to_dict()} for n, r in c.integrity],
        "spectral": c.spectral.to_dict() if c.spectral else None,
        "issues": c.issues, "notes": c.notes, "alternates": c.alternates,
    }


def _candidate_md(c: Candidate, heading: str, rel_png_dir: str = "spectrograms") -> list:
    it, pv = c.item, c.provenance
    lines = [f"{heading} Tier {pv.tier}: {pv.label} (score {pv.score})", "",
             f"[`{it.identifier}`]({it.url}) — {it.title}", "",
             f"- **{facts(c)}** · {it.downloads:,} downloads",
             f"- Collections: {', '.join(it.collections[:5]) or '—'} · Licence: {it.licenseurl or 'none stated'}"
             + (" (free to share)" if it.is_free else ""),
             f"- Verified: {verification(c)}"]
    for issue in c.issues[:5]:
        lines.append(f"- ⚠ {issue}")
    for note in c.notes:
        lines.append(f"- Note: {note}")
    lines.append("- Evidence:")
    lines += [f"  - {r}" for r in pv.reasons]
    if c.spectral and getattr(c.spectral, "png", None):
        lines += ["", f"![spectrogram of {it.identifier}]({rel_png_dir}/{Path(c.spectral.png).name})"]
    if c.alternates:
        alts = ", ".join(f"`{a['identifier']}` (tier {a['tier']}, {a['flac_files']} files, {a['mb']} MB)" for a in c.alternates[:4])
        lines.append(f"- Other uploads / editions of this album: {alts}")
    lines += [f"- Get: `python3 -m iaflac get {it.identifier} --dest ~/Music/IA` (lists files and sizes; nothing downloads until you add `--yes`)", ""]
    return lines


def render_markdown(title: str, results: list, meta: dict) -> str:
    out = [f"# iaflac report — {title}", "",
           f"Generated {meta.get('generated', '')} · {meta.get('stats', '')}", "",
           "Tiers say how sure we are the audio is genuinely lossless, from any source (CD, download, vinyl, tape): "
           "**A** strong evidence · **B** likely · **C** little evidence either way "
           "· **L** lossy MP3, only when no FLAC or WAV exists · **D** failed or suspect. Evidence is uploader-supplied or heuristic, so a tier is a ranking, not a guarantee.", ""]
    for note in meta.get("notes", []):
        out.append(f"- {note}")
    if meta.get("searched"):
        out += ["", "How this was searched:"]
        out += [f"- {label}: {n} hit(s) \u2014 `{query}`" for label, n, query in meta["searched"]]
    if meta.get("notes") or meta.get("searched"):
        out.append("")
    if all(isinstance(r, WantResult) for r in results):
        out += ["| # | Request | Best match | Tier | Format | Verified |", "|---|---|---|---|---|---|"]
        for i, r in enumerate(results, 1):
            if r.candidates:
                c = r.candidates[0]
                out.append(f"| {i} | {r.want.label} | [{c.identifier}]({c.item.url}) | {c.tier} | "
                           f"{c.stats.format_label() if c.stats else ''} | {' '.join(c.levels)} |")
            elif r.found and not r.tested:
                out.append(f"| {i} | {r.want.label} | *not tested* ({len(r.found)} found) | — | — | — |")
            else:
                out.append(f"| {i} | {r.want.label} | *no FLAC match* | — | — | — |")
        out.append("")
        for i, r in enumerate(results, 1):
            out += [f"## {i}. {r.want.label}", "",
                    "Searched: " + " → ".join(f"{label} ({n})" for label, n in r.rungs), ""]
            for reason, n in r.excluded.items():
                out.append(f"- Excluded {n}: {reason}")
            for note in r.notes:
                out.append(f"- {note}")
            if r.found and not r.tested:
                out.append("- Nothing was tested for this request (skipped).")
            if r.candidates:
                out += [""] + _candidate_md(r.candidates[0], "###")
                if len(r.candidates) > 1:
                    out += ["### Other candidates", "", "| Tier | Item | Files / format | Evidence |", "|---|---|---|---|"]
                    for c in r.candidates[1:]:
                        out.append(f"| {c.tier} | [{c.identifier}]({c.item.url}) {c.item.title[:60]} | {facts(c)} | {short_evidence(c)} |")
                    out.append("")
            if r.untested:
                out += ["### Found but not tested", "", "| Item | Files / format | Rip files | Downloads |", "|---|---|---|---|"]
                for c in r.untested:
                    f = preview.facts(c, r.want.artist)
                    out.append(f"| [{c.identifier}]({c.item.url}) {c.item.title[:60]} | {f['flac_files']} FLAC ({f['format']}) \u00b7 "
                               f"{f['runtime_min'] or '?'} min \u00b7 {f['flac_mb']:,} MB | {', '.join(f['rip_files']) or 'none'} | {f['downloads']:,} |")
                out.append("")
            if r.near_misses:
                out += ["### Near misses (FLAC items that did not match well enough)", ""]
                out += [f"- [`{n['identifier']}`](https://archive.org/details/{n['identifier']}) — {n['title']}" for n in r.near_misses[:6]]
                out.append("")
            if r.lossless_other:
                out += ["### Lossless in other formats (not FLAC; convertible)", ""]
                out += [f"- [`{n['identifier']}`](https://archive.org/details/{n['identifier']}) — {n['title']}" for n in r.lossless_other]
                out.append("")
    else:  # a flat list of candidates (genre search)
        out += ["| # | Tier | Item | Files / format | Evidence |", "|---|---|---|---|---|"]
        for i, c in enumerate(results, 1):
            out.append(f"| {i} | {c.tier} | [{c.identifier}]({c.item.url}) {c.item.title[:60]} | {facts(c)} | {short_evidence(c)} |")
        out.append("")
        for i, c in enumerate(results, 1):
            out += _candidate_md(c, f"### {i}.")
    return "\n".join(out) + "\n"


def write_run(run_dir: Path, title: str, results: list, meta: dict) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {"generated": time.strftime("%Y-%m-%d %H:%M"), **meta}
    flat = []
    for r in results:
        if isinstance(r, WantResult):
            flat += [(r.want.label, i, c) for i, c in enumerate(r.candidates, 1)]
        else:
            flat.append(("", len(flat) + 1, r))

    (run_dir / "report.md").write_text(render_markdown(title, results, meta), encoding="utf-8")
    columns = ["request", "rank", "tier", "score", "identifier", "url", "title", "creator", "year", "flac_files",
               "runtime_min", "size_mb", "format", "aligned", "levels", "spectral", "licence", "free", "live",
               "downloads", "collections", "match"]
    with open(run_dir / "report.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(columns)
        for label, rank, c in flat:
            d = candidate_dict(c)
            w.writerow([label, rank, d["tier"], d["score"], d["identifier"], d["url"], d["title"], d["creator"],
                        d["year"], d["flac_files"], round((d["runtime_seconds"] or 0) / 60, 1),
                        round(d["flac_bytes"] / 1e6), d["format"], d["aligned"], " ".join(d["levels"]),
                        (d["spectral"] or {}).get("verdict", ""), d["licenseurl"], d["is_free"], d["is_live"],
                        d["downloads"], ";".join(d["collections"][:4]), (d["match"] or {}).get("score", "")])
    payload = {"meta": meta, "title": title, "results": [
        {"want": {"artist": r.want.artist, "album": r.want.album}, "rungs": r.rungs, "excluded": r.excluded,
         "notes": r.notes, "near_misses": r.near_misses, "lossless_other": r.lossless_other, "tested": r.tested,
         "candidates": [candidate_dict(c) for c in r.candidates],
         "untested": [preview.facts(c, r.want.artist) for c in r.untested]} if isinstance(r, WantResult)
        else candidate_dict(r) for r in results]}
    (run_dir / "candidates.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    return {"markdown": run_dir / "report.md", "csv": run_dir / "report.csv", "json": run_dir / "candidates.json"}


def terminal_summary(results: list) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        if isinstance(r, WantResult):
            lines.append(f"\n[{i}/{len(results)}] {r.want.label}")
            if not r.candidates and r.found and not r.tested:
                lines.append(f"    not tested (skipped); {len(r.found)} found")
            elif not r.candidates:
                lines.append("    no FLAC match" + (f"  (near misses: {', '.join(n['identifier'] for n in r.near_misses[:3])})" if r.near_misses else ""))
            for c in r.candidates:
                lines.append(f"    {c.tier}  {c.identifier[:46]:46}  {facts(c)}")
                lines.append(f"       {short_evidence(c)}  |  {' '.join(c.levels)}"
                             + (f"  |  spectral {c.spectral.verdict}" if c.spectral else ""))
        else:
            c = r
            lines.append(f"{i:>3}. {c.tier}  {c.identifier[:44]:44}  {facts(c)}")
            lines.append(f"       {c.item.title[:60]}  |  {short_evidence(c, 2) or 'no rip evidence'}  |  {' '.join(c.levels)}"
                         + (f"  |  spectral {c.spectral.verdict}" if c.spectral else ""))
    return "\n".join(lines)
