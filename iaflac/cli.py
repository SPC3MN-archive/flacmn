"""Command line: find | genre | verify | get | inventory."""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from . import download, genre as genre_mod, inventory as inv, names as names_mod, queries, report
from .genre import GenreSpec
from .ia_client import IAClient, IAError
from .pipeline import Candidate, Options, Pipeline

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "run"


def run_dir_for(out: str, name: str) -> Path:
    return Path(out).expanduser() / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug(name)}"


def print_candidate(cand: Candidate) -> None:
    item, st, pv = cand.item, cand.stats, cand.provenance
    print(f"\n{item.identifier}  ->  {item.url}")
    print(f"  {item.title}  |  creator: {item.creator or '(none)'}  |  year: {item.year or '?'}")
    print(f"  collections: {', '.join(item.collections[:4]) or '-'}  |  licence: {item.licenseurl or 'none stated'}")
    runtime = item.runtime_seconds
    print(f"  {len(item.flacs)} .flac files, {item.flac_bytes / 1e6:.0f} MB"
          + (f", {runtime / 60:.1f} min" if runtime else "")
          + f"  |  logs: {len(item.files_of('log'))}  cues: {len(item.files_of('cue'))}")
    print(f"\n  {'file':44} {'kind':8} {'format':18} {'duration':>9} {'IA len':>8} {'ratio':>6} align  md5")
    for f in item.flacs:
        p = cand.probes.get(f.name)
        if p is None:
            continue
        si = p.streaminfo
        name = f.name.rsplit("/", 1)[-1]
        name = name if len(name) <= 44 else name[:41] + "..."
        if not p.ok or not si:
            print(f"  {name:44} {p.kind:8} NOT FLAC: {p.detail}")
            continue
        ratio = p.size_ratio(f.size)
        print(f"  {name:44} {p.kind:8} {p.describe():18} {si.duration:8.1f}s {f.length or 0:7.1f}s "
              f"{ratio if ratio is not None else 0:6.2f} {'yes' if si.sector_aligned else ' no'}    "
              f"{'set' if si.md5_set else 'UNSET'}")
    print(f"\n  Verified: {report.verification(cand)}")
    for issue in cand.issues:
        print(f"  ! {issue}")
    print(f"\n  TIER {pv.tier}: {pv.label}   (score {pv.score})")
    for reason in pv.reasons:
        print(f"    {reason}")


def cmd_verify(args, client: IAClient) -> int:
    pipe = Pipeline(client, log=say)
    try:
        cand = pipe.inspect(pipe.load(args.identifier))
        if args.deep or args.spectral:
            opts = Options(max_deep_bytes=args.max_deep_mb * 1024 * 1024, spectrogram_dir=Path(args.png_dir).expanduser())
            pipe.deep_verify(cand, opts, integrity_check=args.deep, spectral_check=args.spectral)
    except IAError as exc:
        say(f"error: {exc}")
        return 2
    print_candidate(cand)
    if cand.spectral and cand.spectral.png:
        print(f"\n  Spectrogram: {cand.spectral.png}")
    say(f"\n[{client.stats()}]")
    return 0


def options_from(args, run_dir: Path) -> Options:
    return Options(top=args.top, include_live=args.include_live, free_only=args.free_only,
                   rip_only=getattr(args, "rip_only", False), deep=args.deep, spectral=not args.no_spectral,
                   max_candidates=getattr(args, "max_candidates", 10), max_deep_bytes=args.max_deep_mb * 1024 * 1024,
                   spectrogram_dir=run_dir / "spectrograms")


def cmd_find(args, client: IAClient) -> int:
    wants = list(queries.read_wanted(args.file)) if args.file else []
    for raw in args.items:
        wants += queries.parse_wanted(raw)
    if not wants:
        say("error: give at least one request, e.g.  find \"Miles Davis - Kind of Blue\"  or  find -f wanted.txt")
        return 2
    name = Path(args.file).stem if args.file else wants[0].label
    run_dir = run_dir_for(args.out, name)
    pipe = Pipeline(client, log=say)
    say(f"{len(wants)} request(s); report will be written to {run_dir}")
    try:
        results = pipe.find(wants, options_from(args, run_dir))
    except IAError as exc:
        say(f"error: {exc}")
        return 2
    paths = report.write_run(run_dir, f"find: {name}", results,
                             {"stats": client.stats(), "command": "find " + " ".join(args.items or [args.file])})
    print(report.terminal_summary(results))
    say(f"\n[{client.stats()}]\nReport: {paths['markdown']}")
    return 0


def cmd_genre(args, client: IAClient) -> int:
    try:
        years = queries.parse_years(args.years) if args.years else None
    except ValueError as exc:
        say(f"error: {exc}")
        return 2
    terms, exclude = genre_mod.resolve(args.genre, genre_mod.split_csv(args.terms), genre_mod.split_csv(args.exclude))
    spec = GenreSpec(genre=args.genre, terms=terms, exclude=exclude, years=years, cd_only=not args.any_flac,
                     min_tracks=args.min_tracks, per_artist=args.per_artist, top=args.top, probe_n=args.probe,
                     collections=tuple(genre_mod.split_csv(args.collections)), include_live=args.include_live,
                     free_only=args.free_only)
    run_dir = run_dir_for(args.out, f"genre-{args.genre}")
    say(f"genre '{args.genre}' -> terms: {', '.join(terms)}" + (f"  (excluding: {', '.join(exclude)})" if exclude else ""))
    say(f"report will be written to {run_dir}")
    try:
        res = Pipeline(client, log=say).genre(spec, options_from(args, run_dir))
    except IAError as exc:
        say(f"error: {exc}")
        return 2
    notes = list(res.notes) + [f"Excluded {n}: {reason}" for reason, n in res.excluded.items()]
    paths = report.write_run(run_dir, f"genre: {args.genre}", res.candidates,
                             {"stats": client.stats(), "notes": notes, "searched": res.searched,
                              "command": f"genre {args.genre}"})
    print(report.terminal_summary(res.candidates))
    for n in notes:
        say(f"  note: {n}")
    say(f"\n[{client.stats()}]\nReport: {paths['markdown']}")
    return 0


def _get_archive_layout(args, client: IAClient) -> int:
    dest = Path(args.dest).expanduser()
    pipe, exit_code = Pipeline(client, log=say), 0
    for identifier in args.identifiers:
        try:
            item = pipe.load(identifier).item
        except IAError as exc:
            say(f"error: {identifier}: {exc}")
            exit_code = 2
            continue
        res = download.get_item(client, item, dest, yes=args.yes, art=not args.no_art, log=say)
        flacs = [pf for pf in res.planned if pf.info.name.lower().endswith(".flac")]
        print(f"\n{identifier}: {len(res.planned)} file(s), {res.total_bytes / 1e6:,.0f} MB  ({len(flacs)} FLAC)")
        print(f"  destination: {res.folder}")
        if not args.yes:
            for pf in res.planned[:40]:
                print(f"    {pf.info.size / 1e6:8.1f} MB  {pf.info.name}")
            if len(res.planned) > 40:
                print(f"    ... and {len(res.planned) - 40} more")
        for note in res.skipped:
            print(f"  skipped {note}")
        if not args.yes:
            print("  Nothing was downloaded. Re-run with --yes to download these files.")
            continue
        print(f"  downloaded {res.downloaded}, already had {res.already_had}, failed {len(res.failures)}")
        for w in res.warnings:
            print(f"  ! {w}")
        for fail in res.failures:
            print(f"  FAILED {fail}")
        exit_code = exit_code or (0 if res.ok else 1)
    say(f"\n[{client.stats()}]")
    return exit_code


def mb(n: int) -> str:
    return f"{n / 1e6:,.0f} MB"


def print_plan(plan: "inv.Plan", indent: str = "  ") -> None:
    for pl in plan.placements:
        a = pl.album
        if pl.added:
            print(f"{indent}inventory: {pl.status}: {a.artist} / {a.album}: {len(a.rows)} row(s) -> Library rows "
                  f"{pl.library_row}-{pl.library_row + len(a.rows) - 1}, Summary row {pl.summary_row}")
        else:
            print(f"{indent}inventory: {a.artist} / {a.album} is ALREADY in the inventory, so nothing will be added for it")
    if plan.changes:
        print(f"{indent}totals: {plan.totals_before[0]} albums / {plan.totals_before[1]} songs -> "
              f"{plan.totals_after[0]} albums / {plan.totals_after[1]} songs")


def print_rows(rows: list, indent: str = "    ", head: int = 3) -> None:
    for r in rows[:head]:
        print(f"{indent}{r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]}")
    if len(rows) > head + 1:
        print(f"{indent}... {len(rows) - head - 1} more ...")
    if len(rows) > head:
        r = rows[-1]
        print(f"{indent}{r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]}")


def _get_library(args, client: IAClient) -> int:
    library = Path(args.library).expanduser()
    overrides = {}
    for spec in args.as_names or []:
        try:
            ident, artist, album = names_mod.parse_override(spec)
        except ValueError as exc:
            say(f"error: {exc}")
            return 2
        overrides[ident] = (artist, album)
    book = None
    if args.inventory:
        try:
            book = inv.Inventory(args.inventory)
        except inv.InventoryError as exc:
            say(f"error: {exc}")
            return 2
        for problem in book.consistency_problems()[:5]:
            say(f"warning: the inventory's Summary disagrees with its Library ({problem}); "
                "only the rows for the albums being added will be changed")
    known = list(book.artists()) if book else []
    if library.exists():
        known += [d.name for d in library.iterdir() if d.is_dir()]
    pipe, exit_code, to_add, previews = Pipeline(client, log=say), 0, [], []
    for identifier in args.identifiers:
        try:
            cand = pipe.load(identifier)
            pipe.probe(cand)
        except IAError as exc:
            say(f"error: {identifier}: {exc}")
            exit_code = 2
            continue
        if identifier in overrides:
            artist, album = overrides[identifier]
            source = "given with --as"
        else:
            st = cand.stats
            nm = names_mod.derive_names(cand.item.title, cand.item.creator, st.tag_artist if st else "", st.tag_album if st else "")
            artist, album, source = nm.artist, nm.album, f"GUESSED from the {nm.source}; check it, or pass --as"
        artist = names_mod.canonical_artist(artist, known)
        try:
            res = download.library_get(client, cand.item, library, artist, album, yes=args.yes, art=args.art,
                                       rip=args.rip_files, log=say)
        except download.DownloadError as exc:
            say(f"error: {identifier}: {exc}")
            exit_code = 2
            continue
        flacs = [pf for pf in res.planned if pf.info.name.lower().endswith(".flac")]
        rip_files = sum(1 for pf in res.planned if download.file_kind(pf.info.name) in download.LIB_RIP_ROLES)
        print(f"\n{identifier}\n  {artist}  /  {album}   ({source})")
        print(f"  destination: {res.folder}")
        print(f"  {len(res.planned)} file(s), {mb(res.total_bytes)}: {len(flacs)} FLAC"
              + (f", cover: {res.cover.name}" if res.cover else ", no cover art")
              + (f", rip evidence: {rip_files} file(s)" if args.rip_files == "keep" else ""))
        for note in res.skipped:
            print(f"  skipped {note}")
        preview = None
        if book:
            try:
                preview = inv.album_from_item(cand.item, cand.probes, artist, album)
                status = book.plan([preview]).placements[0].status
                if status == "already in inventory":
                    print("  inventory: this album is ALREADY in the inventory, so nothing will be added for it")
                else:
                    previews.append(preview)
                    print(f"  inventory: {len(preview.rows)} row(s) to add ({status})")
                    print_rows(book.plan([preview]).placements[0].album.rows)
                    if cand.stats and cand.stats.n < len(cand.item.flacs):
                        print(f"    (file types assumed from {cand.stats.n} of {len(cand.item.flacs)} probed tracks; "
                              "the real rows are rebuilt from the downloaded files)")
            except inv.InventoryError as exc:
                say(f"  cannot preview the inventory rows: {exc}")
        if not args.yes:
            print("  Nothing was downloaded and nothing was changed. Re-run with --yes to do it.")
            continue
        print(f"  downloaded {res.downloaded}, already had {res.already_had}, failed {len(res.failures)}")
        for w in res.warnings:
            print(f"  ! {w}")
        for fail in res.failures:
            print(f"  FAILED {fail}")
        if not res.ok or res.warnings:
            exit_code = exit_code or 1
            if book:
                print("  NOT adding this album to the inventory until every file has verified cleanly")
            continue
        if book:
            try:
                actual = inv.album_from_folder(res.folder, artist, album)
            except inv.InventoryError as exc:
                say(f"  cannot read the downloaded folder for the inventory: {exc}")
                exit_code = exit_code or 1
                continue
            if preview is not None and [r[2:] for r in preview.rows] != [r[2:] for r in actual.rows]:
                print("  note: the rows read from the downloaded files differ from the preview (the real files win)")
            to_add.append(actual)
    if book and not args.yes and previews:
        print("\nInventory, if all of the above are added together:")
        print_plan(book.plan(previews))
    if book and args.yes and to_add:
        try:
            fresh = inv.Inventory(args.inventory)              # re-read right before writing, in case it changed
            plan = fresh.plan(to_add)
            print("\nUpdating the inventory:")
            print_plan(plan)
            result = fresh.apply(plan)
            if result["written"]:
                print(f"  wrote {args.inventory}\n  backup of the previous version: {result['backup']}")
            else:
                print("  nothing to write")
        except inv.InventoryError as exc:
            say(f"error: the albums are downloaded but the inventory was NOT updated: {exc}\n"
                "       fix that (for example close the workbook in Excel) and run:  inventory add <album folder> --xlsx ... --yes")
            exit_code = exit_code or 1
    say(f"\n[{client.stats()}]")
    return exit_code


def cmd_get(args, client: IAClient) -> int:
    if bool(args.dest) == bool(args.library):
        say("error: give exactly one of --dest (archive-style folders) or --library (Artist/Album folders)")
        return 2
    if args.inventory and not args.library:
        say("error: --inventory needs --library")
        return 2
    return _get_library(args, client) if args.library else _get_archive_layout(args, client)


def cmd_inventory(args, client: IAClient) -> int:
    try:
        book = inv.Inventory(args.xlsx)
    except inv.InventoryError as exc:
        say(f"error: {exc}")
        return 2
    if args.action == "check":
        problems = book.consistency_problems()
        totals = inv.summarise(book.library_rows)
        print(f"{args.xlsx}: {len(book.library_rows)} songs, {sum(v[0] for v in totals.values())} albums, {len(totals)} artists")
        print("Summary agrees with Library" if not problems else "Summary and Library DISAGREE:")
        for prob in problems[:20]:
            print(f"  {prob}")
        return 1 if problems else 0
    folders = [Path(f).expanduser() for f in args.folders]
    if not folders:
        say("error: 'inventory add' needs at least one album folder")
        return 2
    if (args.artist or args.album) and len(folders) != 1:
        say("error: --artist/--album can only be used with a single folder")
        return 2
    try:
        albums = [inv.album_from_folder(f, args.artist or f.parent.name, args.album or f.name) for f in folders]
        plan = book.plan(albums)
        print_plan(plan, indent="")
        for pl in plan.placements:
            if pl.added:
                print_rows(pl.album.rows)
        if not args.yes:
            print("Dry run: nothing was changed. Re-run with --yes to write it (a backup is made first).")
            return 0
        result = book.apply(plan)
        print(f"wrote {args.xlsx}" + (f"\nbackup of the previous version: {result['backup']}" if result["written"] else " (nothing to add)"))
    except inv.InventoryError as exc:
        say(f"error: {exc}")
        return 1
    return 0


def add_common(p: argparse.ArgumentParser, deep_default: str = "top") -> None:
    p.add_argument("--include-live", action="store_true", help="keep live recordings (excluded by default)")
    p.add_argument("--free-only", action="store_true", help="only Creative Commons / public-domain / netlabel items")
    p.add_argument("--deep", choices=["none", "top", "all"], default=deep_default,
                   help=f"full integrity + spectral checks: none, the top pick, or all candidates (default {deep_default})")
    p.add_argument("--no-spectral", action="store_true", help="skip the spectral (fake-lossless) check")
    p.add_argument("--max-deep-mb", type=int, default=1500, help="skip full integrity check above this size (default 1500)")
    p.add_argument("-o", "--out", default=str(PROJECT_ROOT / "runs"), help="where run folders are written")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="iaflac", description="Find and verify FLAC (ideally CD-rip) albums on the Internet Archive.")
    ap.add_argument("--cache", help="cache directory (default: .cache next to the package, or $IAFLAC_CACHE)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("find", help="look up albums / artists")
    f.add_argument("items", nargs="*", help='requests such as "Miles Davis - Kind of Blue"')
    f.add_argument("-f", "--file", help="text file (one request per line) or .csv (artist,album)")
    f.add_argument("--rip-only", action="store_true", help="only items that ship a rip log or cue sheet")
    f.add_argument("--top", type=int, default=3, help="candidates listed per request (default 3)")
    f.add_argument("--max-candidates", type=int, default=10, help="search hits to inspect per request (default 10)")
    add_common(f)

    g = sub.add_parser("genre", help="discover albums by genre instead of by name")
    g.add_argument("genre", help='e.g. shoegaze, "post-punk", ambient (see genres.yml; any phrase works)')
    g.add_argument("--terms", help="extra subject/title words, comma-separated (synonyms, subgenres)")
    g.add_argument("--exclude", help="words that disqualify an item, comma-separated")
    g.add_argument("--years", help="1988-1996, or a single year")
    g.add_argument("--any-flac", action="store_true", help="don't require a rip log or cue sheet (default requires one, widening if too few)")
    g.add_argument("--min-tracks", type=int, default=5, help="minimum FLAC files for an album-shaped item (default 5)")
    g.add_argument("--per-artist", type=int, default=2, help="max items per artist (default 2)")
    g.add_argument("--top", type=int, default=25, help="how many to list (default 25)")
    g.add_argument("--probe", type=int, default=30, help="how many candidates get header probes and log reads (default 30)")
    g.add_argument("--collections", help="limit to archive.org collections, comma-separated (e.g. netlabels,hifidelity)")
    add_common(g, deep_default="none")

    v = sub.add_parser("verify", help="check one archive.org item")
    v.add_argument("identifier")
    v.add_argument("--deep", action="store_true",
                   help="stream every FLAC through flac -t (downloads the whole album but stores nothing)")
    v.add_argument("--spectral", action="store_true", help="spectral fake-lossless check on 3 slices (~5-15 MB)")
    v.add_argument("--max-deep-mb", type=int, default=1500, help="skip the full decode test above this size")
    v.add_argument("--png-dir", default=str(PROJECT_ROOT / "runs" / "verify"), help="where spectrogram PNGs go")

    d = sub.add_parser("get", help="download an item's FLACs (lists files and sizes first; needs --yes)")
    d.add_argument("identifiers", nargs="+")
    d.add_argument("--dest", help="archive-style layout: a folder to put '<title> [identifier]' folders in")
    d.add_argument("--library", help="library layout: your music folder; files go to <library>/<Artist>/<Album>/ with cover.jpg")
    d.add_argument("--inventory", help="with --library: the inventory .xlsx to add each verified album to (a backup is made first)")
    d.add_argument("--as", dest="as_names", action="append", metavar='"ID=Artist | Album"',
                   help="name an item explicitly (repeatable); otherwise names are guessed and shown for checking")
    d.add_argument("--art", choices=["cover", "all", "none"], default="cover", help="library layout: cover.jpg only (default), all scans, or none")
    d.add_argument("--rip-files", choices=["keep", "skip"], default="keep", help="library layout: keep the rip .log/.cue/.accurip next to the audio (default) or skip them")
    d.add_argument("--yes", action="store_true", help="actually download (without it, only the plan is shown)")
    d.add_argument("--no-art", action="store_true", help="archive-style layout: skip cover art and booklet scans")

    i = sub.add_parser("inventory", help="check or update the music inventory workbook")
    i.add_argument("action", choices=["check", "add"], help="check: is Summary consistent with Library. add: add album folders (dry run unless --yes)")
    i.add_argument("--xlsx", required=True, help="the inventory workbook")
    i.add_argument("folders", nargs="*", help="album folders (<library>/<Artist>/<Album>) for 'add'")
    i.add_argument("--artist", help="artist name if the folder's parent is not it")
    i.add_argument("--album", help="album name if the folder's own name is not it")
    i.add_argument("--yes", action="store_true", help="write the change (a backup is made first)")

    args = ap.parse_args(argv)
    client = IAClient(cache_dir=args.cache)
    return {"find": cmd_find, "genre": cmd_genre, "verify": cmd_verify, "get": cmd_get, "inventory": cmd_inventory}[args.cmd](args, client)


if __name__ == "__main__":
    sys.exit(main())
