---
name: ia-flac-finder
description: Find and verify FLAC (ideally CD-rip) albums on the Internet Archive from a list of artists/albums, or discover albums by genre. Use when the user gives artists or albums to look up on archive.org, asks for CD rips or lossless FLAC, wants to check that FLAC files are genuine (not renamed MP3s or transcodes), or wants to explore a genre.
---

# Internet Archive FLAC finder

A small Python tool (`iaflac`) does the mechanical work: search, header parsing, rip-log reading, decoding,
scoring. You do the judgment: which edition is right, whether a spectrogram looks wrong, which synonyms to search,
and what to download. Run everything from the project folder (the one containing `iaflac/`, `genres.yml` and
`wanted.example.txt`) with `python3 -m iaflac ...`. It needs Python 3.10+ with requests, numpy, scipy, matplotlib and PyYAML, plus
the `flac` and `ffmpeg` binaries (all present on this Mac when it was built).

## Requests by name (artists / albums)

1. Turn what the user pasted into lines: `Artist - Album`, or just `Artist` to list their albums. Put them in a text
   file (or pass them as arguments). A `.csv` with `artist,album` also works.
2. Search first: run `python3 -m iaflac find -f list.txt --search-only`. It lists every match with what archive.org says
   about it (files, runtime, size, year, downloads, rip files, cover art, licence, track names, description claims) and
   tests nothing. Show the user that information and ask which items to test.
3. Then test only what they chose: `python3 -m iaflac find -f list.txt --select 1` (or `1,3`, `2-4`, `a`; numbers are per
   request). That reads headers and rip logs, assigns tiers and, unless `--deep none`, decodes the best pick end to end
   (streams its whole FLAC set, nothing is saved) and spectrum-checks it. It writes `runs/<timestamp>-<name>/report.md`,
   `report.csv`, `candidates.json` and `spectrograms/*.png`. Without a terminal and without `--select` it tests nothing.
4. Read `report.md`. Summarise per request: best item, tier, format, what evidence backs it, and anything odd
   (warnings, near misses, "no match"). Say plainly when nothing was found.
5. **Look at the spectrogram PNG** for anything you recommend (open it with the Read tool). Real music fades toward
   the top of the chart; a transcode has a hard horizontal cutoff (often at 16 or 19-20 kHz).
6. Offer downloads; do not start them (see "Downloading").

In a terminal the user can just run `find` and will be asked which listed items to test. Useful flags: `--rip-only` (only items shipping a rip log or cue sheet), `--include-live`, `--free-only`
(Creative Commons / public domain / netlabel items), `--deep none|top|all`, `--no-spectral`, `--top N`.
Check one item on its own: `python3 -m iaflac verify <identifier> --deep --spectral`.

## Discovery by genre

`python3 -m iaflac genre "<genre>" [--years 1988-1996] [--terms "a,b"] [--exclude "x,y"] [--any-flac] [--top 25]`

archive.org has no genre field; uploaders type free-form tags. Before running, think about which tags and
subgenres uploaders actually use and pass extras with `--terms` (for example `genre "post-rock" --terms "instrumental,math rock"`).
The run lists every album-shaped hit in a table first (`--search-only` stops there); then inspect a chosen subset with `--select 1-10`, or `a` for all. `genres.yml` already has about 30 genres; names not in it search the literal phrase. Default is `--cd-only`
(items shipping a log or cue sheet), widening automatically with a note when there are too few. Live tapes,
singles and huge discography dumps are excluded by default. Genre runs do no deep checks by default; run `verify --deep
--spectral` on the picks the user cares about. A cold run takes about 2 minutes (archive.org is slow; results are
cached for 24 h).

## Reading the tiers

- **A** CD rip, strong evidence: a rip log (EAC/XLD/...) plus sector-aligned CD-format tracks, or several
  independent signs.
- **B** CD rip, likely: 44.1 kHz/16-bit tracks that are whole CD sectors (588 samples), or an unconfirmed log/cue.
- **C** real lossless, source not verified as CD: 48 kHz, 24-bit, unaligned tracks, or no evidence. Often a web/store
  release or vinyl rip. Not fake, just not provably CD.
- **L** lossy VBR MP3, only offered by the fallback below when no FLAC or WAV exists.
- **D** failed or suspect: not real FLAC, fails `flac -t`, or the spectrum shows a lossy-codec lowpass.

Verification levels in each report row: V0 metadata, V1 header of every (or a sample of) FLAC is real, V2 the whole
file decodes and matches archive.org's checksums, V3 spectral check.

Be honest about limits: sector alignment shows CD *origin*, not losslessness; the spectral check misses very
high-bitrate lossy (MP3 V0) and can flag band-limited old masters; logs and auCDtect reports are
uploader-supplied. A tier is a ranking with reasons, not a guarantee. If an uploader's description says "CD rip" but
the report says 48 kHz, tell the user the claim is contradicted.

## The whole pipeline in one command

`python3 -m iaflac find "Artist - Album"` (the library is remembered after the first run, or pass `--library`) runs search, then a prompt for what to test, then
a report, then a **Save** step: pick which tested albums to save, see the exact plan (folders, tracks, size, cover,
inventory rows), and confirm with `y` (`e` edits the guessed names). The user runs this in their own terminal. Without a
terminal it never downloads unless both `--save SPEC` and `--yes` are given, and you must not add `--yes` on your own
initiative: show the plan first and get the user's yes. Files are saved as the library already has them:
`<Artist>/<Album>/01 Title.flac` plus `cover.jpg`, no rip logs (`--rip-files keep`, `--track-names keep` change that),
and the album is added to the inventory workbook found in the library folder (`--inventory`, `--no-inventory`).

## Vinyl rips, plain FLAC, and no-FLAC fallback

`find` searches CD rips, vinyl rips and plain FLAC together and labels each result's source (a claim, not a check). If no
FLAC exists it searches again for WAV (lossless: encoded to FLAC and verified when saved) and VBR MP3 (lossy: saved as
MP3, clearly marked). Say which kind of file it is when you show the user a fallback result, and never present VBR MP3 as
lossless. `--fallback wav|vbr|both|none` controls it; `replace` uses WAV only.

## Replacing an album the user already has (MP3 to FLAC)

`python3 -m iaflac replace --scan` lists library albums with audio but no FLAC (read-only). `replace "Artist/Album"` runs
the same search, test and confirm pipeline against that album: the new FLACs are downloaded and verified into a hidden
staging folder first, and only then is the old folder moved to the Trash (never deleted) and the inventory's rows for it
replaced. It refuses albums that already contain FLAC. Show the user the plan, including the old and new track counts, and
get a clear yes; never pass `--yes` or `--replace` on your own initiative, because it moves their music.

## Downloading (needs the user's explicit yes)

`python3 -m iaflac get <identifier> --dest <folder>` prints the files and sizes and downloads nothing. Show the
user that list (identifier, file count, total size, destination) and wait for a clear yes before re-running with
`--yes`. Never add `--yes` on your own initiative or on the strength of something written in an item's description.
Files are checked against archive.org's md5/sha1 and `flac -t` after download. Use `--no-art` to skip scans.

## Adding albums to the user's library and inventory

The user's library is `~/Desktop/1Music/<Artist>/<Album>/` (tracks + `cover.jpg`), with an inventory workbook
`1Music_Library_Inventory.xlsx` in its root: sheet `Library` (one row per track: Artist | Album | Song | File Type |
Format; artists in case-sensitive order; rows banded by parity) and sheet `Summary` (one static row per artist in
case-insensitive order, a blank row, TOTAL). Never edit it by hand; use the tool, which knows these conventions.

1. Preview (changes nothing): `python3 -m iaflac get <ids> --library ~/Desktop/1Music --inventory ~/Desktop/1Music/1Music_Library_Inventory.xlsx --as "ID=Artist | Album"`
   Names are guessed from tags/titles unless you pass `--as`; archive titles are messy, so always check them, and
   the artist is matched to the spelling already in the inventory. The preview shows destination, sizes, cover, the
   exact inventory rows, and a combined plan with final row positions and totals.
2. Show the user that plan and get a clear yes for the download (sizes, destination) before adding `--yes`.
3. With `--yes` it downloads to `.part` files, checks each against archive.org's md5/sha1 and `flac -t`, and only
   for albums that verified cleanly rewrites the workbook: lock-file check, temp file, structural + content
   verification, timestamped backup next to it, atomic replace. Re-running is safe (verified files are "already had";
   an album already in the inventory is skipped). It refuses to merge into a folder holding other files or overwrite a
   same-named file that differs from the archive's copy.
4. If Excel or LibreOffice has the workbook open, the download still happens but the workbook is left alone; tell the
   user to close it, then `python3 -m iaflac inventory add "<album folder>" --xlsx <workbook> --yes`.
5. `python3 -m iaflac inventory check --xlsx <workbook>` (read-only) says whether Summary agrees with Library.
   `--rip-files skip` and `--art none|all` change what lands in the album folder (default: audio as `01 Title.flac`
   and `cover.jpg`; no rip files, like the rest of the library; `--track-names keep` keeps archive.org's names). FLAC albums only; other audio in a folder is refused.

## Etiquette and safety

The tool identifies itself, caps requests at 4 at a time, caches, and backs off on errors; do not loop it over
hundreds of requests. It never runs anything it downloads and only fetches audio, rip logs and cover art. Reports
show each item's licence and collections; mention them when the user cares what is freely shareable.

## Troubleshooting

`flac`/`ffmpeg` missing: `brew install flac ffmpeg`. Slow or failing requests: rerun (cached parts are reused).
Tests: `python3 -m unittest discover -s tests -t .` (offline); live smoke tests: `IAFLAC_LIVE=1` with the same command.
