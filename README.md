# iaflac

Find FLAC albums on the [Internet Archive](https://archive.org), prefer CD rips, and check that the files
really are FLAC. Give it a list of artists/albums, or a genre.

```bash
python3 -m iaflac find -f wanted.example.txt          # look up a list (one "Artist - Album" or "Artist" per line)
python3 -m iaflac find "Miles Davis - Kind of Blue"   # or pass requests directly
python3 -m iaflac find "Miles Davis - Kind of Blue" --library ~/Music   # ...and choose what to save into your library afterwards
python3 -m iaflac genre shoegaze --years 1988-1996    # discover by genre
python3 -m iaflac find "Miles Davis - Kind of Blue" --search-only   # just list what was found: test nothing
python3 -m iaflac verify <identifier> --deep --spectral   # check one item as hard as possible
python3 -m iaflac get <identifier> --dest ~/Music/IA      # lists files and sizes; add --yes to download
```

Needs Python 3.10+ with `requests numpy scipy matplotlib pyyaml`, plus the `flac` and `ffmpeg` binaries.
Run it from this folder. Reports land in `runs/<timestamp>-<name>/` (`report.md`, `report.csv`,
`candidates.json`, `spectrograms/`). `SKILL.md` teaches Claude to drive it.

## Walkthrough: from a wish list to a verified album in your library

This takes one album from a search to your library and inventory, in one command. Nothing is tested until you pick it,
and nothing is saved until you confirm it. Run everything from this folder.

**1. Check the setup (once).**

```bash
python3 --version                       # 3.10 or newer
python3 -c "import requests, numpy, scipy, matplotlib, yaml"   # no output means the packages are there
flac --version && ffmpeg -version | head -1
```

If an import fails, `pip install requests numpy scipy matplotlib pyyaml`. If `flac` or `ffmpeg` is missing on a Mac,
`brew install flac ffmpeg`.

**2. Say what you want.** Pass requests on the command line, or put one per line in a text file (`Artist - Album`, or
just `Artist`; lines starting with `#` are comments; `.csv` with `artist,album` also works). See `wanted.example.txt`.
An album request finds far better matches than an artist-only one, which returns whatever uploads mention the name.

**3. Search, and read the list. Nothing is tested yet.**

```bash
python3 -m iaflac find "Miles Davis - Kind of Blue"
```

You get a numbered list of everything archive.org has for the request. Each entry shows the FLAC count, runtime, size,
year, downloads, which rip files it ships (`log`, `cue`, `.accurip`), cover art, licence, track names, and what the
uploader's description claims. Read it for the signs of a real CD rip: a `log` and a `cue`, a track count and runtime
that match the album, and a name that matches ("exact match", not "weak match"). Entries marked "another upload or
edition of #1" are duplicates of the same album. To stop here, add `--search-only`.

**4. Choose what to test.** At the prompt, press Enter to test the top entry, type numbers such as `1,3` or `2-4`,
`a` for all, `s` to skip this request, or `q` to quit. The tests read the files, so pick the entries you would
actually keep. The best pick also gets a full decode test and a spectrum check, which streams that album's whole
FLAC set (the size is in the listing). Add `--deep none` to skip that and only read headers and the rip log.

**5. Read the report.** The run writes a folder under `runs/` and prints a summary. Open `report.md` in it. Each
tested item has a tier and the evidence behind it:

| Tier | Meaning |
|---|---|
| **A** | CD rip, strong evidence: rip log plus CD-format, sector-aligned tracks |
| **B** | CD rip, likely |
| **C** | Real lossless, but not shown to come from a CD (48 kHz, 24-bit, no rip log) |
| **D** | Failed or suspect: not FLAC, failed the decode test, or a lossy spectrum |

Tiers describe how sure the tool is that the source was a CD, not how good the audio is. A tier C item can be a
perfectly good lossless file. Also look for `V2 ✓ n/n files decode cleanly` (every file passed `flac -t` and matched archive.org's
checksum) and the spectrum verdict; open the PNG in `spectrograms/` if it says `SUSPECT`.

**6. Choose what to save.** With `--library` (see below), a **Save** section follows the tests. It lists the albums you
tested that can be saved (tiers A to C; tier D is never offered), each with the artist and album name it would be filed
under, size, which tests it has passed and the evidence. Only albums you tested are listed. Enter nothing to save
nothing, or type numbers such as `1,3`, `2-4` or `a`.

```bash
python3 -m iaflac find "Miles Davis - Kind of Blue" --library ~/Music
```

**7. Review and confirm.** Before anything is written you see, for each album you picked: the folder it will go to
(`~/Music/Miles Davis/Kind of Blue/`), the first track names, the file count and size, the cover art, and the exact rows
that would be added to your inventory. Then `y` saves, `e` lets you correct the artist and album names (archive.org
titles are messy, so check them), and `n` cancels. Nothing has been downloaded up to this point.

**8. It is saved in the format your library already uses.** The tool copies the library's own conventions:

- folders `<Artist>/<Album>/`, using the artist spelling already in your library (`Boards of Canada`, not `Boards Of Canada`)
- tracks named `01 Title.flac`, the most common style in the library (`--track-names keep` keeps archive.org's names instead)
- a `cover.jpg` (`--art none` skips it, `--art all` keeps every scan)
- audio and cover only, since the library has no rip logs (`--rip-files keep` adds the `.log/.cue/.accurip`)
- the album added to your inventory workbook, with a timestamped backup made first

Each file downloads to a `.part` name and is renamed only once it matches archive.org's checksum. An album is added to
the inventory only after every file verified, and the inventory rows are read from the downloaded files. It refuses to
merge into a folder holding other files and never overwrites a file that differs. Close the workbook in Excel first,
since an open file stops the write; the download still happens and the command to add it afterwards is printed.

**Setting your library once.** Instead of repeating `--library` and `--inventory`, set them in your shell profile:

```bash
export IAFLAC_LIBRARY=~/Music
export IAFLAC_INVENTORY=~/Music/Inventory.xlsx    # optional: if the library folder holds exactly one *Inventory*.xlsx it is used
```

`--no-inventory` saves without touching any workbook.

**9. Check the result (optional).**

```bash
python3 -m iaflac inventory check --xlsx ~/Music/Inventory.xlsx    # Summary matches Library
python3 -m iaflac verify <identifier> --deep --spectral            # re-test one item as hard as possible
python3 -m iaflac get <identifier> --library ~/Music --yes         # save one item by identifier, without searching (preview first without --yes)
```

**Discovering by genre instead.** When you don't have a specific album in mind:

```bash
python3 -m iaflac genre shoegaze --years 1988-1996
```

You get a table of album-shaped hits with rip files, size and popularity. Pick which to inspect at the prompt (Enter
inspects the first 30), read the report the same way as in step 5, then save an album you like with `get` (see step 9), or search
for it by name with `find` and use step 6. `--terms "dream pop"` adds search words and `--exclude` removes some.

**Running it unattended.** With no terminal (a script or a pipe) and no `--select`, `find` lists and tests nothing.
Use `--select 1` to answer the test prompt in advance. Saving needs two explicit flags when there is no terminal:
`--save 1` (numbers from the Save list) and `--yes`, for example
`python3 -m iaflac find "Miles Davis - Kind of Blue" --library ~/Music --select 1 --save 1 --yes`.
Without both, nothing is downloaded.

## Search first, then test what you choose

`find` and `genre` work in two phases, and the tests never start until you say so.

1. **Search results.** Each request is searched and every match is listed with what archive.org itself says about it:
   title, number of FLAC files, runtime, size, year, downloads, which rip files it ships (log, cue, `.accurip`),
   whether it has cover art, licence, collections, track names, and what the uploader's description claims. No file is
   opened and there are no verdicts yet.
2. **Tests, for what you pick.** In a terminal you are asked which listed items to test (`Enter` = the top one,
   `1,3`, `2-4`, `a` = all, `s` = skip this request, `d` = `Enter` for all remaining requests, `q` = quit). Only then are
   headers and rip logs read, tiers assigned and, unless `--deep none`, the best pick decoded end to end and
   spectrum-checked (which streams its whole FLAC set; the size is in the listing).

`--search-only` lists and stops. `--select 1` (or `1,3`, `2-4`, `a`) answers the prompt in advance, per request. Without
a terminal (a pipe, a script) and without `--select`, nothing is tested, so nothing expensive can start unasked. Found
items you didn't pick are recorded under "Found but not tested" in the report. `genre` works the same way: it lists
every album-shaped hit in a table and asks which to inspect (`Enter` = the first `--probe`, default 30).

## How a candidate is judged

Each item is verified in levels; a report row says which it reached.

| Level | What | Cost | Catches |
|---|---|---|---|
| V0 | archive.org metadata: FLAC list, sizes, checksums, rip sidecars | 1 request | missing tracks, mislabeled formats |
| V1 | first 32 KB of each FLAC (HTTP Range): `fLaC` magic (ID3 prefix skipped), STREAMINFO, tags. Non-FLAC is named by magic bytes. Duration and size cross-checked against the archive's numbers | ~32 KB per track | MP3/WAV/M4A renamed `.flac`, truncation, CD-format and sector-alignment evidence |
| V2 | every file streamed through `flac -t` (frame CRCs + audio MD5) and compared with archive.org's md5/sha1. Nothing is stored | the whole album | corruption, truncation |
| V3 | three 1.5 MB slices decoded and searched for a lossy codec's lowpass cliff; writes a spectrogram | 5-15 MB | MP3/AAC transcodes, upsampled hi-res |

`find` runs V0+V1 on every candidate and V2+V3 on the top pick per request (`--deep all` for everything).
`genre` runs V0+V1 only (`--deep top` to add the deep checks to the best few).

Tiers (ranking, best first): **A** CD rip with strong evidence (rip log + CD-format, sector-aligned tracks) ·
**B** CD rip, likely · **C** real lossless, source not verified as CD (48 kHz, 24-bit, no evidence) ·
**D** failed or suspect. The weights are constants in `iaflac/provenance.py`, pinned by tests against four real items.

CD-origin evidence: a CD track is a whole number of 588-sample sectors, so a real rip's tracks all have
`total_samples % 588 == 0`. In real items: an EAC rip 12/12 and 14/14, a 48 kHz "(FLAC)" upload 0/12. Only meaningful
in aggregate (three or more tracks, 90%+).

## Searching

- **By name:** query ladder per request: exact title restricted to items that ship a rip log or cue sheet
  (archive.org labels those as item formats `Log` and `Cue Sheet`), then any exact title, then relaxed words.
  Artist is matched in `creator` *or* `title` (many uploads have no creator). Matches are confirmed against the
  FLAC's own ARTIST/ALBUM tags; duplicate uploads are grouped with the best copy first; volume/part numbers must
  agree ("II" is not "IV"). If there is no FLAC match you get near misses and other lossless formats, never silence.
- **By genre:** archive.org has no genre field, so `subject:` tags, then title, then description are matched, ranked
  by tag strength, provenance tier, album shape and popularity, and capped per artist. `genres.yml` maps ~30 genres to
  tags and exclusions; anything else searches the literal phrase; `--terms`/`--exclude` extend it.

## Guardrails

Read-only against archive.org's public endpoints; identifies itself; at most 4 requests in flight; on-disk cache
(24 h; `.cache/`); backoff on 429/5xx. Skips access-restricted items. Every row shows licence and collections
(`--free-only` keeps Creative Commons / public-domain / netlabel items). `find`/`genre`/`verify` write only cache and
reports. `get` needs `--yes`, fetches only audio, rip logs and original cover art, refuses names that could escape the
destination, downloads to `.part` files that must match archive.org's checksums, and never executes anything.

## Known limits

- Sector alignment shows CD *origin*, not losslessness (a gapless MP3 decode of a CD rip is also aligned).
- The spectral check catches MP3 128-320 CBR and V2 (63-70 dB cliffs vs 5 dB for real audio) but **misses MP3 V0**
  and other very high bitrates, and can flag band-limited old masters (verdict `SUSPECT`: look at the spectrogram).
- Rip logs and auCDtect reports are uploader-supplied; tier A means strong evidence, not proof. Not built: recomputing
  AccurateRip checksums from the audio and comparing them with the item's `.accurip` (that would be proof), and a
  MusicBrainz track-count completeness check.
- archive.org metadata is free-form and many albums aren't there.
- Cold archive.org requests are slow (metadata ~3 s, ranged reads ~1.8 s), so a first genre run takes about 2 minutes.

## Your library and inventory

```bash
python3 -m iaflac get <id> --library ~/Music --inventory ~/Music/Inventory.xlsx --as "<id>=Artist | Album"   # preview
python3 -m iaflac get <id> --library ~/Music --inventory ~/Music/Inventory.xlsx --as "<id>=Artist | Album" --yes
python3 -m iaflac inventory check --xlsx ~/Music/Inventory.xlsx
python3 -m iaflac inventory add "~/Music/Artist/Album" --xlsx ~/Music/Inventory.xlsx --yes    # for albums you downloaded some other way
```

`get --library` (and the save step of `find`) files an album at `<library>/<Artist>/<Album>/` as `01 Title.flac` tracks
with a `cover.jpg`, and by default no rip `.log/.cue/.accurip` files (`--rip-files keep` adds them). With `--inventory` it then adds the album to a workbook shaped like the one this was built
against (a `Library` sheet with one row per track: Artist, Album, Song, File Type, Format, artists in case-sensitive
order and rows banded by parity; a `Summary` sheet with per-artist counts and a TOTAL row). Those conventions were
reverse-engineered from a real inventory and reproduced exactly: its Summary is recomputed from its Library with zero
differences, and 684 of 684 FLAC `File Type` strings were rebuilt from the file headers. A new artist block goes
where the sort order puts it, an existing artist keeps its spelling, banding is re-applied below the insertion, and
Summary/TOTAL/filter ranges are updated. Safety: a dry run by default; never merges into a folder with other files or
overwrites a differing file; an album is recorded only after every file verified; the workbook is written via a temp
file, checked (an independent OOXML structural check plus a cell-by-cell comparison), backed up with a timestamp, then
atomically replaced; Excel/LibreOffice lock files stop the write. Only FLAC albums are supported.

## Tests

```bash
python3 -m unittest discover -s tests -t .            # offline, ~15 s; builds real FLACs/MP3s with flac and ffmpeg
IAFLAC_LIVE=1 python3 -m unittest tests.test_live -v  # smoke tests against archive.org
python3 -m tests.record_fixtures                      # re-snapshot the four calibration items (network)
```
