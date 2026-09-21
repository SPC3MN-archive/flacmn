# iaflac

Find FLAC albums on the [Internet Archive](https://archive.org), prefer CD rips, and check that the files
really are FLAC. Give it a list of artists/albums, or a genre.

```bash
python3 -m iaflac find -f wanted.example.txt          # look up a list (one "Artist - Album" or "Artist" per line)
python3 -m iaflac find "Miles Davis - Kind of Blue"   # or pass requests directly
python3 -m iaflac genre shoegaze --years 1988-1996    # discover by genre
python3 -m iaflac verify <identifier> --deep --spectral   # check one item as hard as possible
python3 -m iaflac get <identifier> --dest ~/Music/IA      # lists files and sizes; add --yes to download
```

Needs Python 3.10+ with `requests numpy scipy matplotlib pyyaml`, plus the `flac` and `ffmpeg` binaries.
Run it from this folder. Reports land in `runs/<timestamp>-<name>/` (`report.md`, `report.csv`,
`candidates.json`, `spectrograms/`). `SKILL.md` teaches Claude to drive it.

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

`get --library` files an album at `<library>/<Artist>/<Album>/` with a `cover.jpg` and, by default, the rip
`.log/.cue/.accurip`. With `--inventory` it then adds the album to a workbook shaped like the one this was built
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
