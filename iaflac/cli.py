"""Command line: find | genre | verify | get | inventory | replace | health."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import download, genre as genre_mod, inventory as inv, matching, names as names_mod, paths, preview, queries, replace as replace_mod, report
from .genre import GenreSpec
from .ia_client import IAClient, IAError
from .pipeline import Candidate, Options, Pipeline

PROJECT_ROOT = paths.data_dir()


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


FALLBACKS = {"both": ("wav", "vbr"), "wav": ("wav",), "vbr": ("vbr",), "none": ()}
MEDIA_WORD = {"flac": "FLAC", "wav": "WAV", "mp3": "MP3"}


def options_from(args, run_dir: Path) -> Options:
    return Options(top=args.top, include_live=args.include_live, free_only=args.free_only,
                   rip_only=getattr(args, "rip_only", False), deep=args.deep, spectral=not args.no_spectral,
                   max_candidates=getattr(args, "max_candidates", 10), max_deep_bytes=args.max_deep_mb * 1024 * 1024,
                   spectrogram_dir=run_dir / "spectrograms", fallback=FALLBACKS.get(getattr(args, "fallback", "none"), ()))


SEARCH_ONLY_HINT = """Nothing was tested. To test some of these, either:
  - run the same command in a terminal without --search-only, and you will be asked which to test, or
  - add --select 1 (the top listed item of every request), --select 1,3, --select 2-4 or --select a (all listed), or
  - check one item as thoroughly as possible:  python3 -m iaflac verify <identifier> --deep --spectral"""


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def ask(prompt: str) -> str:
    return input(prompt)


def choose(prompt: str, n: int, default: str) -> "preview.Selection":
    """Ask which listed candidates to test, re-asking until the answer makes sense. EOF or Ctrl-C means quit."""
    while True:
        try:
            answer = ask(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            return preview.Selection("quit")
        try:
            return preview.parse_selection(answer, n, default)
        except preview.SelectionError as exc:
            print(f"  {exc}. Try again.")


def run_mode(args) -> str:
    """search-only: list and stop. select: --select decides. prompt: ask in the terminal.
    Without a terminal and without --select nothing is tested: the tests never start unasked."""
    if args.search_only or (not args.select and not is_interactive()):
        return "search-only"
    return "select" if args.select else "prompt"


def check_select(args) -> bool:
    if args.search_only and args.select:
        say("error: --search-only and --select contradict each other")
        return False
    if args.select:
        try:
            preview.parse_selection(args.select, 1, strict=False)
        except preview.SelectionError as exc:
            say(f"error: --select {args.select!r}: {exc}")
            return False
    return True


def search_only_footer(args) -> None:
    print("\n" + SEARCH_ONLY_HINT)
    if not args.search_only:
        print("(This is not an interactive terminal, so I did not ask which to test.)")


def cmd_find(args, client: IAClient) -> int:
    wants = list(queries.read_wanted(args.file)) if args.file else []
    for raw in args.items:
        wants += queries.parse_wanted(raw)
    if not wants:
        say("error: give at least one request, e.g.  find \"Miles Davis - Kind of Blue\"  or  find -f wanted.txt")
        return 2
    if not check_select(args):
        return 2
    name = Path(args.file).stem if args.file else wants[0].label
    run_dir = run_dir_for(args.out, name)
    opts, mode, pipe = options_from(args, run_dir), run_mode(args), Pipeline(client, log=say)
    print(preview.HEADER)
    if mode == "prompt":
        print(preview.TEST_NOTE)
    results, chosen, default_for_rest = [], [], False
    try:
        for i, want in enumerate(wants, 1):
            res = pipe.search_one(want, opts)
            results.append(res)
            print(preview.format_find_preview(res, i, len(wants)))
            picks = []
            if mode != "search-only" and res.found:
                n = len(res.found)
                if mode == "select":
                    sel = preview.parse_selection(args.select, n, strict=False)
                    if sel.dropped:
                        say(f"  --select: {sel.dropped} is outside 1-{n} for this request; ignored")
                elif default_for_rest:
                    sel = preview.parse_selection("", n)
                else:
                    print()
                    sel = choose(preview.find_prompt(), n, "1")
                    default_for_rest = default_for_rest or sel.default_for_rest
                if sel.action == "quit":
                    print("Stopped. Nothing was tested.")
                    return 0
                if sel.action == "test":
                    picks = sel.indexes
            chosen.append(picks)
    except IAError as exc:
        say(f"error: {exc}")
        return 2
    if mode == "search-only":
        search_only_footer(args)
        say(f"\n[{client.stats()}]")
        return 0
    total = sum(len(p) for p in chosen)
    if total == 0:
        print("\nNothing was selected, so nothing was tested.")
        return 0
    biggest = max(res.found[k].item.flac_bytes for res, picks in zip(results, chosen) for k in picks) // 1_000_000
    say(f"\nTesting {total} candidate(s): header reads and rip logs"
        + ("" if opts.deep == "none" else f", then a full decode test and spectrum check of the best pick (streams up to {biggest:,} MB)")
        + ". Ctrl-C stops it.")
    try:
        for res, picks in zip(results, chosen):
            pipe.verify_selected(res, [res.found[k] for k in picks], opts)
    except IAError as exc:
        say(f"error: {exc}")
        return 2
    except KeyboardInterrupt:
        say("\nInterrupted; no report was written.")
        return 130
    paths = report.write_run(run_dir, f"find: {name}", results,
                             {"stats": client.stats(), "command": "find " + " ".join(args.items or [args.file])})
    print(report.terminal_summary(results))
    say(f"\nReport: {paths['markdown']}")
    try:
        code = save_phase(args, client, results)
    except IAError as exc:
        say(f"error: {exc}")
        code = 2
    say(f"\n[{client.stats()}]")
    return code


def cmd_genre(args, client: IAClient) -> int:
    try:
        years = queries.parse_years(args.years) if args.years else None
    except ValueError as exc:
        say(f"error: {exc}")
        return 2
    if not check_select(args):
        return 2
    terms, exclude = genre_mod.resolve(args.genre, genre_mod.split_csv(args.terms), genre_mod.split_csv(args.exclude))
    spec = GenreSpec(genre=args.genre, terms=terms, exclude=exclude, years=years, cd_only=getattr(args, "rip_only", False),
                     min_tracks=args.min_tracks, per_artist=args.per_artist, top=args.top, probe_n=args.probe,
                     collections=tuple(genre_mod.split_csv(args.collections)), include_live=args.include_live,
                     free_only=args.free_only)
    run_dir = run_dir_for(args.out, f"genre-{args.genre}")
    opts, mode, pipe = options_from(args, run_dir), run_mode(args), Pipeline(client, log=say)
    print(preview.HEADER)
    try:
        res = pipe.genre_search(spec, opts)
    except IAError as exc:
        say(f"error: {exc}")
        return 2
    print(preview.format_genre_preview(res))
    found = len(res.found)
    if mode == "search-only":
        search_only_footer(args)
        say(f"\n[{client.stats()}]")
        return 0
    if found == 0:
        return 0
    default_n = min(spec.probe_n, found)
    if mode == "select":
        sel = preview.parse_selection(args.select, found, default=f"1-{default_n}", strict=False)
        if sel.dropped:
            say(f"  --select: {sel.dropped} is outside 1-{found}; ignored")
    else:
        print()
        sel = choose(preview.genre_prompt(default_n), found, f"1-{default_n}")
    if sel.action != "test":
        print("Nothing was tested.")
        return 0
    selected = [res.found[k] for k in sel.indexes]
    say(f"\nInspecting {len(selected)} candidate(s): header reads and rip logs, about ten small reads each. Ctrl-C stops it.")
    try:
        pipe.genre_inspect(res, selected, opts)
    except IAError as exc:
        say(f"error: {exc}")
        return 2
    except KeyboardInterrupt:
        say("\nInterrupted; no report was written.")
        return 130
    notes = list(res.notes) + [f"Excluded {n}: {reason}" for reason, n in res.excluded.items()]
    paths = report.write_run(run_dir, f"genre: {args.genre}", res.candidates,
                             {"stats": client.stats(), "notes": notes, "searched": res.searched,
                              "command": f"genre {args.genre}"})
    print(report.terminal_summary(res.candidates))
    for note in notes:
        say(f"  note: {note}")
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
        if pl.added and pl.removed:
            print(f"{indent}inventory: replaces {len(pl.removed)} existing row(s) of {a.artist} / {a.album} "
                  f"({pl.removed[0][4]}) with {len(a.rows)} row(s) ({a.rows[0][4]}), Library rows "
                  f"{pl.library_row}-{pl.library_row + len(a.rows) - 1}")
        elif pl.added:
            print(f"{indent}inventory: {pl.status}: {a.artist} / {a.album}: {len(a.rows)} row(s) -> Library rows "
                  f"{pl.library_row}-{pl.library_row + len(a.rows) - 1}, Summary row {pl.summary_row}")
        else:
            print(f"{indent}inventory: {a.artist} / {a.album} is ALREADY in the inventory, so no rows are added for it")
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


def open_inventory(path: str):
    """The inventory workbook, or None with the reason printed."""
    try:
        book = inv.Inventory(path)
    except inv.InventoryError as exc:
        say(f"error: {exc}")
        return None
    for problem in book.consistency_problems()[:5]:
        say(f"warning: the inventory's Summary disagrees with its Library ({problem}); "
            "only the rows for the albums being added will be changed")
    return book


@dataclass
class Job:
    identifier: str
    names: Optional[tuple] = None          # (artist, album) given by the user
    cand: object = None                    # an already loaded and probed Candidate
    replace: Optional[Path] = None         # an existing album folder this download takes the place of


def inventory_album_for(book, artist: str, folder_name: str) -> Optional[str]:
    """The album name the inventory uses for an existing folder (they are not always spelled alike), or None."""
    albums = list(dict.fromkeys(r[1] for r in book.library_rows if r[0] == artist))
    plain = replace_mod.album_name_from_folder(folder_name, artist)
    scored = [(max(matching.similarity(folder_name, a), matching.similarity(plain, a)), a) for a in albums]
    best = max(scored, default=(0.0, None))
    return best[1] if best[0] >= 0.85 else None


def listed_album(book, artist: str, album: str) -> Optional[str]:
    """The inventory's spelling of this artist's album when the guessed name differs from it only in case, spacing or
    punctuation ("Mm.. Food" / "MM...FOOD"), or None. Deliberately strict: "Madvillainy" is not "Madvillainy 2" and
    "Album" is not "Album (Deluxe)"."""
    def bare(text) -> str:
        return re.sub(r"[\W_]+", "", str(text).casefold())
    return next((str(r[1]) for r in book.library_rows if r[0] == artist and bare(r[1]) == bare(album)), None)


def in_inventory(book, artist: str, album: str) -> bool:
    return bool(book) and any(r[0] == artist and str(r[1]).casefold() == album.casefold() for r in book.library_rows)


def guess_names(cand, known: list, book=None) -> tuple:
    """(artist, album, source) for filing a candidate: FLAC tags, then the item's creator, then its title. With the
    inventory, an album it already lists keeps the inventory's name, so a re-download lands under the same name."""
    st = cand.stats
    nm = names_mod.derive_names(cand.item.title, cand.item.creator, st.tag_artist if st else "", st.tag_album if st else "")
    artist, album = names_mod.canonical_artist(nm.artist, known), nm.album
    source = f"GUESSED from the {nm.source}; check it, or pass --as"
    listed = listed_album(book, artist, album) if book else None
    if listed and listed != album:
        album, source = listed, f"GUESSED from the {nm.source}, spelled as in your inventory; check it, or pass --as"
    return artist, album, source


def library_flow(client: IAClient, library: Path, inventory: Optional[str], jobs: list, *, yes: bool, art: str,
                 rip: str, track_names: str, show_plan: bool = True, named_by: str = "given with --as") -> int:
    """Plan (yes=False) or carry out (yes=True) filing albums into the library and adding them to the inventory.
    jobs: [Job]. Names not given are guessed and shown. A job with `replace` takes the place of that existing album
    folder: the new files are downloaded and verified beside it first, and only then is the old folder moved to the
    Trash and the inventory's rows for it replaced. Returns 0, 1 (something failed) or 2 (could not start)."""
    book = None
    if inventory:
        book = open_inventory(inventory)
        if book is None:
            return 2
    known = list(book.artists()) if book else []
    if library.exists():
        known += [d.name for d in library.iterdir() if d.is_dir()]
    pipe, exit_code, to_add, previews = Pipeline(client, log=say), 0, [], []
    for job in jobs:
        identifier, override, cand = job.identifier, job.names, job.cand
        try:
            cand = cand or pipe.load(identifier)
            if not cand.probes and not cand.alt_stats:
                pipe.probe(cand)
        except IAError as exc:
            say(f"error: {identifier}: {exc}")
            exit_code = 2
            continue
        old = old_album = staging = None
        media = cand.item.media
        if job.replace and media == "mp3":
            say(f"error: {identifier}: this upload only has lossy MP3 files, so it cannot replace your album with FLAC")
            exit_code = 2
            continue
        if job.replace:
            old = replace_mod.survey(job.replace)
            if not old.replaceable:
                say(f"error: {identifier}: {job.replace} {'already has FLAC files' if old.has_flac else 'has no audio files'}; "
                    "not replacing it")
                exit_code = 2
                continue
            artist = job.replace.parent.name
            old_album = inventory_album_for(book, artist, job.replace.name) if book else None
            album = (override[1] if override else None) or old_album or replace_mod.album_name_from_folder(job.replace.name, artist)
            source = "kept from the album you already have"
            staging = replace_mod.staging_folder(job.replace, identifier)
        elif override:
            artist, album = override
            source = named_by
        else:
            artist, album, source = guess_names(cand, known, book)
        artist = artist if job.replace else names_mod.canonical_artist(artist, known)
        try:
            res = download.library_get(client, cand.item, library, artist, album, yes=yes, art=art,
                                       rip=rip, track_names=track_names, folder=staging, media=media, log=say)
        except download.DownloadError as exc:
            say(f"error: {identifier}: {exc}")
            exit_code = 2
            continue
        flacs = [pf for pf in res.planned if download.is_track(pf.info, media)]
        rip_files = sum(1 for pf in res.planned if download.file_kind(pf.info.name) in download.LIB_RIP_ROLES)
        word = MEDIA_WORD[media]
        if show_plan:
            print(f"\n{identifier}\n  {artist}  /  {album}   ({source})")
            if job.replace:
                print(f"  REPLACES: {job.replace}\n            now {old.describe()}  ->  {len(flacs)} {'FLAC' if media != 'mp3' else word}")
                if old.tracks != len(flacs):
                    print(f"  ! the old album has {old.tracks} track(s) and the new one {len(flacs)}: check the names above before saying yes")
                print("  The old folder goes to the Trash (you can put it back); anything else in it, such as booklets, is kept.")
            else:
                print(f"  destination: {res.folder}")
            if media == "wav":
                print("  These are WAV files. Each is downloaded, checked against archive.org's checksum, encoded to FLAC "
                      "(lossless, verified as it is encoded) and the WAV removed.")
            elif media == "mp3":
                print("  ** LOSSY: no FLAC or WAV exists for this album. These are VBR MP3 files and will be saved as MP3. **")
            print(f"  {len(res.planned)} file(s), {mb(res.total_bytes)}: {len(flacs)} {word}"
                  + (f", cover: {res.cover.name}" if res.cover else ", no cover art")
                  + (f", rip evidence: {rip_files} file(s)" if rip == "keep" else ""))
            for pf in flacs[:3]:
                print(f"    {(pf.final or pf.path).relative_to(res.folder.resolve())}")
            if len(flacs) > 3:
                print(f"    ... {len(flacs) - 3} more")
            for note in res.skipped:
                print(f"  skipped {note}")
        preview = None
        if book:
            try:
                preview = (inv.album_from_alt(cand.item, cand.alt_stats, artist, album) if media != "flac"
                           else inv.album_from_item(cand.item, cand.probes, artist, album))
                preview.replaces = old_album or ""
                status = book.plan([preview]).placements[0].status
                if status == "already in inventory":
                    if show_plan:
                        print("  inventory: this album is ALREADY in the inventory: the files are downloaded, and no rows are added")
                else:
                    previews.append(preview)
                    if show_plan:
                        print(f"  inventory: {len(preview.rows)} row(s) to add ({status})")
                        print_rows(book.plan([preview]).placements[0].album.rows)
                        if cand.stats and cand.stats.n < len(cand.item.flacs):
                            print(f"    (file types assumed from {cand.stats.n} of {len(cand.item.flacs)} probed tracks; "
                                  "the real rows are rebuilt from the downloaded files)")
            except inv.InventoryError as exc:
                say(f"  cannot preview the inventory rows: {exc}")
        if not yes:
            continue
        print(f"\n{artist}  /  {album}: downloaded {res.downloaded}, already had {res.already_had}, failed {len(res.failures)}"
              f"\n  {job.replace or res.folder}")
        for w in res.warnings:
            print(f"  ! {w}")
        for fail in res.failures:
            print(f"  FAILED {fail}")
        if not res.ok or res.warnings:
            exit_code = exit_code or 1
            if job.replace:
                print(f"  NOT replacing anything: your original album is untouched. The partial download is in {res.folder.name} "
                      "and will be resumed next time.")
            elif book:
                print("  NOT adding this album to the inventory until every file has verified cleanly")
            continue
        final = res.folder
        if job.replace:
            try:
                moved_to, carried = replace_mod.swap_in(job.replace, staging, library)
            except (replace_mod.ReplaceError, OSError) as exc:
                say(f"  could not replace the album: {exc}\n  The verified FLACs are in {staging}")
                exit_code = exit_code or 1
                continue
            final = job.replace
            print(f"  replaced {job.replace.name}: the old files are in {moved_to}"
                  + (f"\n  kept from the old folder: {', '.join(carried[:6])}" if carried else ""))
        if book:
            try:
                actual = inv.album_from_folder(final, artist, album, allow_mp3=media == "mp3")
                actual.replaces = old_album or ""
            except inv.InventoryError as exc:
                say(f"  cannot read the downloaded folder for the inventory: {exc}")
                exit_code = exit_code or 1
                continue
            if preview is not None and [r[2:] for r in preview.rows] != [r[2:] for r in actual.rows]:
                print("  note: the rows read from the downloaded files differ from the preview (the real files win)")
            to_add.append(actual)
    if book and not yes and previews and show_plan:
        print("\nInventory, if all of the above are added together:")
        print_plan(book.plan(previews))
    if book and yes and to_add:
        try:
            fresh = inv.Inventory(inventory)              # re-read right before writing, in case it changed
            plan = fresh.plan(to_add)
            print("\nUpdating the inventory:")
            print_plan(plan)
            result = fresh.apply(plan)
            if result["written"]:
                print(f"  wrote {inventory}\n  backup of the previous version: {result['backup']}")
            else:
                print("  nothing to write")
        except inv.InventoryError as exc:
            say(f"error: the albums are downloaded but the inventory was NOT updated: {exc}\n"
                "       fix that (for example close the workbook in Excel) and run:  inventory add <album folder> --xlsx ... --yes")
            exit_code = exit_code or 1
    return exit_code


SAVE_HINT = ("To save an album, add --library <your music folder> to the same command (or set IAFLAC_LIBRARY). "
             "Or use:  python3 -m iaflac get <identifier> --library <folder> --yes")


def config_path() -> Path:
    return paths.config_file()


def read_config() -> dict:
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def remember(**values) -> bool:
    """Keep the library (and inventory) between runs. Never fatal: a read-only home just means asking again."""
    try:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**read_config(), **values}, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def library_from(args) -> Optional[Path]:
    """--library, then $IAFLAC_LIBRARY, then the folder remembered from an earlier run."""
    value = getattr(args, "library", None) or os.environ.get("IAFLAC_LIBRARY") or read_config().get("library")
    return Path(value).expanduser() if value else None


def ask_library() -> Optional[Path]:
    """First save in a terminal: ask where the music library is, and remember the answer."""
    guess = next((d for d in (Path("~/Desktop/1Music").expanduser(), Path("~/Music").expanduser()) if d.is_dir()), None)
    while True:
        try:
            answer = ask("\nWhere is your music library? It will be remembered."
                         + (f"   Enter = {guess}" if guess else "") + "   s = don't save\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if answer.lower() in ("s", "skip", "q"):
            return None
        chosen = Path(answer).expanduser() if answer else guess
        if chosen is None:
            print("  Type the folder, or s to skip.")
        elif not chosen.is_dir():
            print(f"  {chosen} is not a folder. Try again.")
        else:
            where = config_path()
            print(f"  Using {chosen}" + (f" (remembered in {where})" if remember(library=str(chosen)) else " (could not remember it)"))
            return chosen


def inventory_from(args, library: Path) -> tuple:
    """(path or None, note). An explicit --inventory or $IAFLAC_INVENTORY wins; otherwise the one workbook in the
    library folder, if there is exactly one (backups and lock files don't count)."""
    if getattr(args, "no_inventory", False):
        return None, ""
    value = getattr(args, "inventory", None) or os.environ.get("IAFLAC_INVENTORY") or read_config().get("inventory")
    if value:
        return str(Path(value).expanduser()), ""
    if library.is_dir():
        hits = sorted(p for p in library.glob("*nventory*.xlsx") if ".backup-" not in p.name and not p.name.startswith(("~$", ".")))
        if len(hits) == 1:
            return str(hits[0]), "  (the only inventory workbook in the library folder; --no-inventory to skip)"
    return None, ""


def savable(results: list) -> tuple:
    """Tested candidates that may be saved (tier A-C, or L for lossy MP3 when no lossless copy exists), and the ones that may not (tier D or unknown), by identifier.
    Every tested match is offered, not only the few the report lists: the best copy of each album first, then the other
    uploads and editions. Each candidate remembers the request it came from (`cand.request`)."""
    ok, blocked, seen = [], [], set()
    for res in results:
        for cand in getattr(res, "candidates", []) + getattr(res, "matched", []):
            if cand.identifier in seen:
                continue
            seen.add(cand.identifier)
            cand.request = res.want.label if hasattr(res, "want") else ""
            (ok if cand.tier in ("A", "B", "C", "L") else blocked).append(cand)
    return ok, blocked


def existing_albums(args, library: Path, cands: list, known: list) -> dict:
    """{identifier: existing album folder} for candidates that would take the place of an album you already have
    with no FLAC in it. A folder named with `replace <folder>` is used as given; otherwise it is matched by name."""
    named = getattr(args, "replace_map", None) or {}
    out = {}
    for cand in cands:
        folder = named.get(getattr(cand, "request", ""))
        if folder is None:
            artist, album, _ = guess_names(cand, known)
            found = replace_mod.find_existing(library, artist, album)
            folder = found.path if found and found.replaceable else None
        if folder is not None:
            out[cand.identifier] = folder
    return out


def choose_save(n: int) -> "preview.Selection":
    prompt = "Save which to your library?   Enter = none · numbers like 1,3 or 2-4 · a = all\n> "
    while True:
        try:
            answer = ask(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            return preview.Selection("skip")
        try:
            return preview.parse_selection(answer, n, default="s")
        except preview.SelectionError as exc:
            print(f"  {exc}. Try again.")


def confirm_save(n: int, size_mb: int, replacing: int = 0) -> str:
    """'y' save, 'e' edit names, 'n' cancel."""
    what = f"Save {n} album(s)" if not replacing else (f"Replace {replacing} of your albums" + (f" and save {n - replacing} more" if n > replacing else ""))
    extra = "; the old files go to the Trash" if replacing else ""
    while True:
        try:
            answer = ask(f"\n{what}, about {size_mb:,} MB to download{extra}?   y = yes · e = edit names · n = cancel\n> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return "n"
        if answer in ("y", "yes", "e", "edit", "n", "no", ""):
            return {"yes": "y", "edit": "e", "no": "n", "": "n"}.get(answer, answer)
        print("  Answer y, e or n.")


def edit_names(picked: list, overrides: dict, known: list, replacing: Optional[dict] = None, book=None) -> None:
    for cand in picked:
        if replacing and cand.identifier in replacing:
            print(f"  {cand.identifier}: keeps the names of your existing album ({replacing[cand.identifier].name})")
            continue
        artist, album, _ = guess_names(cand, known, book)
        artist, album = overrides.get(cand.identifier, (artist, album))
        try:
            answer = ask(f"  {cand.identifier}\n  Artist | Album   Enter keeps [{artist} | {album}]\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not answer:
            continue
        try:
            _, a, b = names_mod.parse_override(f"{cand.identifier}={answer}")
        except ValueError:
            print("  Expected: Artist | Album. Kept the current names.")
            continue
        overrides[cand.identifier] = (a, b)


def save_phase(args, client: IAClient, results: list) -> int:
    """After the tests: choose which tested albums to save, see exactly what would be written, confirm, then download
    into the library in its own layout and add them to the inventory. Nothing is downloaded without a confirmation:
    a 'y' at the prompt, or --save SPEC together with --yes when there is no terminal."""
    cands, blocked = savable(results)
    if not cands and not blocked:
        return 0
    print("\n" + "=" * 78 + "\nSave")
    if blocked:
        print("  Not offered (failed or suspect, tier D): " + ", ".join(c.identifier for c in blocked))
    if not cands:
        print("  None of the tested albums can be saved.")
        return 0
    library = library_from(args)
    if library is None and is_interactive() and not getattr(args, "save", None):
        library = ask_library()
    if library is None:
        print(f"  {SAVE_HINT}")
        return 0
    inventory, note = inventory_from(args, library)
    print(f"  library:   {library}\n  inventory: {inventory or 'none (nothing will be added to a workbook)'}{note}")
    known = [d.name for d in library.iterdir() if d.is_dir()] if library.is_dir() else []
    try:
        book = inv.Inventory(inventory) if inventory else None
    except inv.InventoryError:
        book = None                                    # library_flow reports the problem when it opens it
    if book:
        known = list(book.artists()) + known
    replacing = existing_albums(args, library, cands, known)
    print("\n  Tested albums you can save:")
    clusters: dict = {}
    for i, cand in enumerate(cands, 1):
        artist, album, _ = guess_names(cand, known, book)
        spectral = f" · spectrum {cand.spectral.verdict}" if cand.spectral else ""
        first = clusters.setdefault(matching.cluster_key(*cand.artist_album), i)
        extra = (f"   (another upload of #{first})" if first != i else "") \
            + ("   (in your inventory: downloads again, no rows added)" if in_inventory(book, artist, album) else "")
        if cand.identifier in replacing:
            now = replace_mod.survey(replacing[cand.identifier])
            artist, album = replacing[cand.identifier].parent.name, replace_mod.album_name_from_folder(replacing[cand.identifier].name, replacing[cand.identifier].parent.name)
            print(f"  {i:>3}. {cand.tier}  {artist} / {album}   ** REPLACES your album ({now.describe()}) **\n       {len(cand.item.flacs)} {MEDIA_WORD[cand.item.media]} · "
                  f"{round(cand.item.flac_bytes / 1e6):,} MB · tests {' '.join(cand.levels)}{spectral} · {report.short_evidence(cand, 2) or 'no rip evidence'}\n"
                  f"       {cand.item.url}")
            continue
        print(f"  {i:>3}. {cand.tier}  {artist} / {album}"
              + ({"wav": "   (WAV, will be converted to FLAC)", "mp3": "   ** LOSSY: VBR MP3 **"}.get(cand.item.media, "")) + extra
              + f"\n       {len(cand.item.flacs)} {MEDIA_WORD[cand.item.media]} · "
              f"{round(cand.item.flac_bytes / 1e6):,} MB · tests {' '.join(cand.levels)}{spectral} · {report.short_evidence(cand, 2) or 'no rip evidence'}\n"
              f"       {cand.item.url}")
    interactive = is_interactive()
    if getattr(args, "save", None):
        sel = preview.parse_selection(args.save, len(cands), strict=False)
    elif interactive and len(cands) == 1:
        sel = preview.Selection("test", [0])        # one album: the plan and the y/n below are the only question
    elif interactive:
        print()
        sel = choose_save(len(cands))
    else:
        print(f"\n  Not saved: this is not an interactive terminal. Use --save 1 --yes (numbers from the list above).")
        return 0
    if sel.action != "test":
        print("  Nothing saved.")
        return 0
    picked = [cands[k] for k in sel.indexes]
    if not interactive and replacing and not (getattr(args, "replace", False) or getattr(args, "replace_map", None)):
        skipped = [c for c in picked if c.identifier in replacing]
        picked = [c for c in picked if c.identifier not in replacing]
        for c in skipped:
            print(f"  Not replacing {replacing[c.identifier]}: replacing an existing album without a terminal needs --replace.")
        if not picked:
            print("\nNothing saved.")
            return 0
    overrides: dict = {}
    while True:
        jobs = [Job(c.identifier, overrides.get(c.identifier), c, replacing.get(c.identifier)) for c in picked]
        print("\nWhat saving would do (nothing is written yet):")
        library_flow(client, library, inventory, jobs, yes=False, art=args.art, rip=args.rip_files, track_names=args.track_names, named_by="edited by you")
        if interactive:
            answer = confirm_save(len(picked), round(sum(c.item.flac_bytes for c in picked) / 1e6),
                                  sum(1 for c in picked if c.identifier in replacing))
        else:
            answer = "y" if args.yes else "n"
        if answer == "e":
            edit_names(picked, overrides, known, replacing, book)
            continue
        break
    if answer != "y":
        print("\nNothing saved." + ("" if interactive else "  Add --yes to save."))
        return 0
    print("\nSaving. Ctrl-C stops; run the same command again to resume.")
    try:
        return library_flow(client, library, inventory, jobs, yes=True, art=args.art, rip=args.rip_files,
                            track_names=args.track_names, show_plan=False)
    except KeyboardInterrupt:
        say("\nInterrupted. Finished files are kept and unfinished ones are marked; run again to resume.")
        return 130


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
    inventory = str(Path(args.inventory).expanduser()) if args.inventory else None
    jobs = [Job(i, overrides.get(i)) for i in args.identifiers]
    code = library_flow(client, library, inventory, jobs, yes=args.yes, art=args.art, rip=args.rip_files,
                        track_names=getattr(args, "track_names", "keep"))
    if not args.yes:
        print("\nNothing was downloaded and nothing was changed. Re-run with --yes to do it.")
    say(f"\n[{client.stats()}]")
    return code


def cmd_replace(args, client: IAClient) -> int:
    """Replace albums you already have (typically MP3) with FLAC. With --scan, list the candidates and stop."""
    library = library_from(args)
    if library is None and is_interactive():
        library = ask_library()
    if library is None or not library.is_dir():
        say("error: say where your library is with --library <folder> (or set IAFLAC_LIBRARY)")
        return 2
    if args.scan:
        found = replace_mod.scan(library)
        if not found:
            print(f"Every album in {library} that has audio already has FLAC.")
            return 0
        print(f"{len(found)} album(s) in {library} have audio but no FLAC:\n")
        for info in found:
            print(f"  {info.path.relative_to(library).as_posix()}   ({info.describe()})")
        print("\nReplace one with:  python3 -m iaflac replace \"<Artist>/<Album>\"     (or several at once)")
        return 0
    if not args.items and not args.file:
        say("error: name the album folder(s) to replace, e.g.  replace \"Solange/When I Get Home\"   (or --scan to list candidates)")
        return 2
    requests, replace_map = [], {}
    for spec in args.items:
        try:
            folder = replace_mod.resolve_folder(spec, library)
        except replace_mod.ReplaceError:
            requests.append(spec)               # not a folder: a plain "Artist - Album" request, matched to your album by name
            continue
        info = replace_mod.survey(folder)
        if not info.replaceable:
            say(f"skipping {folder.relative_to(library)}: " + ("it already has FLAC files" if info.has_flac else "it has no audio files"))
            continue
        artist = folder.parent.name
        text = f"{artist} - {replace_mod.album_name_from_folder(folder.name, artist)}"
        requests.append(text)
        replace_map[queries.parse_wanted(text)[0].label] = folder
    if not requests:
        return 2
    args.items, args.file, args.library, args.replace_map = requests, None, str(library), replace_map
    return cmd_find(args, client)


def cmd_health(args, client: IAClient) -> int:
    """Library Health Check: missing songs, low quality, missing art and duplicates; --archive looks each flagged
    album up on archive.org; --merge merges duplicate album folders (a plan unless --yes)."""
    from . import health
    library = library_from(args)
    if library is None and is_interactive():
        library = ask_library()
    if library is None or not library.is_dir():
        say("error: say where your library is with --library <folder> (or set IAFLAC_LIBRARY)")
        return 2
    mb_client = None
    if args.musicbrainz or args.archive:
        from .musicbrainz import MBClient
        mb_client = MBClient()
    if args.merge:
        try:
            plan = health.plan_merge(library, args.merge)
        except health.HealthError as exc:
            say(f"error: {exc}")
            return 2
        print(f"Merge into {plan['target']}/  ({plan['moves']} song(s) move in, {plan['drops']} worse cop(ies) to the Trash)")
        for s in plan["songs"]:
            keep = s["keep"]
            where = "" if keep["folder"] == plan["target"] else f"  <- from {keep['folder']}/"
            print(f"  keep  {keep['name']}  [{keep['format']}]{where}")
            for d in s["drop"]:
                print(f"  trash {d['folder']}/{d['name']}  [{d['format']}]")
        for folder in plan["trash_folders"]:
            print(f"  then {folder}/ goes to the Trash (anything in it that is not audio is copied across first)")
        if not args.yes:
            print("\nNothing was changed. Add --yes to merge.")
            return 0
        result = health.apply_merge(library, args.merge, plan["token"])
        print(f"\nMerged: {len(result['moved'])} moved in, {len(result['trashed_files'])} file(s) and "
              f"{len(result['trashed_folders'])} folder(s) to the Trash.")
        for note in result["notes"]:
            print(f"  {note}")
        return 0
    only = set(args.only.split(",")) if args.only else set(health.KINDS)
    unknown = only - set(health.KINDS)
    if unknown:
        say(f"error: --only takes {', '.join(health.KINDS)}")
        return 2
    say(f"Checking {library}" + (" (comparing tracklists with MusicBrainz: about 5 s per album the first time)"
                                 if args.musicbrainz else ""))
    report = health.scan(library, mb_client=mb_client if args.musicbrainz else None,
                         progress=lambda e: say(f"  {e['index']}/{e['total']}  {e['folder']}") if args.musicbrainz else None)
    flagged = [a for a in report.albums if a.kinds & only]
    if args.json:
        out = report.to_dict()
        out["albums"] = [a.to_dict() for a in flagged]
        print(json.dumps(out, indent=2))
        return 0
    c = report.counts()
    print(f"{c['albums']} albums, {c['healthy']} healthy · " + " · ".join(f"{c[k]} {health.LABEL[k].lower()}" for k in health.KINDS))
    for a in flagged:
        print(f"\n{a.artist} / {a.album}" + (f" ({a.year})" if a.year else "") + f"   [{a.folder}]")
        for f in a.findings:
            if f.kind in only:
                print(f"  {'!' if f.severity == 'bad' else '-'} {health.LABEL[f.kind]}: {f.text}")
        if a.musicbrainz:
            print(f"    MusicBrainz: {a.musicbrainz}")
        if args.archive and a.kinds & {health.MISSING, health.QUALITY, health.ART}:
            try:
                found = health.search_archive(client, a, mb_client=mb_client)
            except IAError as exc:
                print(f"    archive.org search failed: {exc}")
                continue
            useful = [s for s in found["suggestions"] if s["fixes"]][:3]
            if not useful:
                print("    archive.org: nothing found that fixes this")
            for s in useful:
                fixes = ", ".join(health.LABEL[k].lower() for k in s["fixes"])
                print(f"    archive.org {s['identifier']}  ({s['format']}, {s['tracks']} tracks) fixes: {fixes}")
                for p in s["provides"]:
                    print(f"      has {p['title']}  ({p['file']})")
    dupes = health.duplicate_albums(report.albums)
    if dupes and health.DUPES in only:
        print("\nMerge duplicate folders with:")
        for g in dupes:
            print("  python3 -m iaflac health --merge " + " ".join(f'"{a.folder}"' for a in g))
    return 0


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
    p.add_argument("--search-only", action="store_true",
                   help="list what was found and stop: nothing is tested (this is also what happens without a terminal)")
    p.add_argument("--select", metavar="SPEC",
                   help="skip the prompt and test these listed candidates: 1, 1,3, 2-4 or a (all); numbers are per request")
    p.add_argument("-o", "--out", default=str(PROJECT_ROOT / "runs"), help="where run folders are written")


def add_find_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--fallback", choices=list(FALLBACKS), default="both",
                   help="when no FLAC is found, look for WAV (lossless, converted to FLAC when saved) and/or VBR MP3 "
                        "(lossy, saved as MP3): both (default), wav, vbr or none")
    p.add_argument("--rip-only", action="store_true", help="only items that ship a rip log or cue sheet")
    p.add_argument("--top", type=int, default=3, help="candidates listed per request (default 3)")
    p.add_argument("--max-candidates", type=int, default=10, help="search hits to inspect per request (default 10)")
    add_common(p)
    add_save(p)


def add_save(p: argparse.ArgumentParser) -> None:
    """Options for saving into the library after testing (find)."""
    p.add_argument("--library", help="your music folder; enables the save step after testing (or set $IAFLAC_LIBRARY)")
    p.add_argument("--inventory", help="the inventory .xlsx to add saved albums to (default: the one workbook in the library folder)")
    p.add_argument("--no-inventory", action="store_true", help="save albums without touching any inventory workbook")
    p.add_argument("--save", metavar="SPEC", help="without a terminal: save these tested albums (numbers from the Save list); needs --yes")
    p.add_argument("--yes", action="store_true", help="with --save and no terminal: confirm the download")
    add_layout(p)


def add_layout(p: argparse.ArgumentParser) -> None:
    p.add_argument("--art", choices=["cover", "all", "none"], default="cover", help="cover.jpg only (default), all scans, or none")
    p.add_argument("--rip-files", choices=["keep", "skip"], default="skip",
                   help="keep the rip .log/.cue/.accurip next to the audio, or skip them (default: skip, as the rest of the library has none)")
    p.add_argument("--track-names", choices=["library", "keep"], default="library",
                   help="library: name tracks '01 Title.flac' like the rest of the library (default); keep: archive.org's names")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="iaflac", description="Find and verify high-quality lossless albums (FLAC, any source) on the Internet Archive.")
    ap.add_argument("--cache", help="cache directory (default: .cache next to the package, or $IAFLAC_CACHE)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("find", help="look up albums / artists, test them, and save what you confirm")
    f.add_argument("items", nargs="*", help='requests such as "Miles Davis - Kind of Blue"')
    f.add_argument("-f", "--file", help="text file (one request per line) or .csv (artist,album)")
    add_find_options(f)
    f.add_argument("--replace", action="store_true",
                   help="without a terminal, let --save --yes replace an album you already have (with no FLAC in it)")

    r = sub.add_parser("replace", help="replace albums you have (e.g. MP3) with FLAC; --scan lists candidates")
    r.add_argument("items", nargs="*", help='album folders ("Artist/Album" in the library, or a path) or "Artist - Album" requests')
    r.add_argument("--scan", action="store_true", help="list the albums in the library that have audio but no FLAC, and stop")
    add_find_options(r)
    r.set_defaults(fallback="wav")            # replacing an MP3 album with another MP3 is not the point

    g = sub.add_parser("genre", help="discover albums by genre instead of by name")
    g.add_argument("genre", help='e.g. shoegaze, "post-punk", ambient (see genres.yml; any phrase works)')
    g.add_argument("--terms", help="extra subject/title words, comma-separated (synonyms, subgenres)")
    g.add_argument("--exclude", help="words that disqualify an item, comma-separated")
    g.add_argument("--years", help="1988-1996, or a single year")
    g.add_argument("--rip-only", action="store_true", help="only items that ship a rip log or cue sheet (default: any lossless source)")
    g.add_argument("--any-flac", action="store_true", help=argparse.SUPPRESS)      # the default now; kept so old commands work
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
    add_layout(d)
    d.add_argument("--yes", action="store_true", help="actually download (without it, only the plan is shown)")
    d.add_argument("--no-art", action="store_true", help="archive-style layout: skip cover art and booklet scans")

    i = sub.add_parser("inventory", help="check or update the music inventory workbook")
    i.add_argument("action", choices=["check", "add"], help="check: is Summary consistent with Library. add: add album folders (dry run unless --yes)")
    i.add_argument("--xlsx", required=True, help="the inventory workbook")
    i.add_argument("folders", nargs="*", help="album folders (<library>/<Artist>/<Album>) for 'add'")
    i.add_argument("--artist", help="artist name if the folder's parent is not it")
    i.add_argument("--album", help="album name if the folder's own name is not it")
    i.add_argument("--yes", action="store_true", help="write the change (a backup is made first)")

    hc = sub.add_parser("health", help="Library Health Check: missing songs, low quality, missing art, duplicates")
    hc.add_argument("--library", help="your music folder (default: $IAFLAC_LIBRARY or the remembered one)")
    hc.add_argument("--musicbrainz", action="store_true", help="also compare each tracklist with MusicBrainz to name missing songs (slow)")
    hc.add_argument("--archive", action="store_true", help="search archive.org for each flagged album and say what each upload fixes")
    hc.add_argument("--only", help="comma-separated: " + "missing_tracks,low_quality,missing_art,duplicates")
    hc.add_argument("--json", action="store_true", help="print the report as JSON")
    hc.add_argument("--merge", nargs="+", metavar="FOLDER", help="merge these album folders (Artist/Album) into one, best copy of each song")
    hc.add_argument("--yes", action="store_true", help="with --merge: do it (without it, only the plan is shown)")

    args = ap.parse_args(argv)
    client = IAClient(cache_dir=args.cache)
    return {"find": cmd_find, "replace": cmd_replace, "genre": cmd_genre, "verify": cmd_verify, "get": cmd_get,
                                  "inventory": cmd_inventory, "health": cmd_health}[args.cmd](args, client)


if __name__ == "__main__":
    sys.exit(main())
