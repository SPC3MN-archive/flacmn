# iaflac UI functions

Move, merge, rename or delete lines freely; this list decides how the UI is grouped.

# 1. Album Search

## Search
- Artist box
- Album box
- Search button
- Album / Genre mode switch
- Format filters (FLAC, WAV, MP3)
- Lossless-only toggle
- Hide albums already in library
- Sort (quality, match, popularity)
- Search notes (near misses, other lossless formats, excluded counts)

## Genre Search
- Genre picker
- Year range
- Extra terms
- Exclude terms
- CD rips only toggle

## Results
- Results table (best quality first)
- Quality badge
- Format
- Tier (A–D)
- Match strength
- Tracks, length, size, year
- Rip files (log/cue)
- Source (CD rip, vinyl, plain)
- Test status (V0–V3)
- "In library" badge

## Album Detail
- Cover, title, year, archive.org link, licence
- Uploader claims
- Track list (title, length, size, rate/bits, decode ✓/✗)
- Quality verdict (tier, evidence, rip log summary)
- Other uploads of the same album

## Quick Test
- Quick test button (headers and rip logs)
- Track picker
- Play / pause / seek
- Waveform
- Track readout (sample rate, bit depth)

## Deep Test
- Deep test button (with download size)
- Track picker
- Play / pause / seek
- Spectrogram (playhead, cutoff line)
- Verdict (PASS / SUSPECT / LIKELY LOSSY / UPSAMPLED, cutoff kHz)
- Album spectrum result

## Metadata
- Album fields (album artist, album, date, genre, label, catalog #)
- Track tag table (disc, number, title, track artist)
- Field source badges (file, archive.org, MusicBrainz, you)
- MusicBrainz matches (score, date, country, format, length diff, apply)
- Tag diff (before → after)
- Cover picker (upload / Cover Art Archive)

## Save
- Destination folder preview
- Final file names
- Size and warnings (lossy, folder exists, no cover)
- Add to queue button

## Download Queue
- Album rows with state and progress (MB, file n/m)
- Pause / resume / cancel / retry / remove
- Problems found after saving
- Clear finished

# 2. CD Art

## Album
- Library album picker
- Find art button (with progress)
- Matched release, pressings searched, notes

## Found Scans
- One row per part: front, back + spines, inside of insert, under-tray, disc
- Source, pressing and dpi per scan
- Click to swap the chosen scan

## Case Preview
- Outside (page 1)
- Inside (page 2)
- Disc (page 3)
- Soft-print warnings (under 200 dpi)

## Print
- Make PDF
- Open PDF
- Save scans to album folder

## Library Art Checklist
- Found / missing per album
- Find art for whole library

# 3. Library

## Albums
- Album table (artist, album, year, format, tracks, cover, tag issues)
- Filter box
- Rescan button

## Album View
- Tracks with their tags
- Tag issues

# App-wide (not a section)

## Always Visible
- Section navigation (Album Search · CD Art · Library · Settings)
- Activity strip (running jobs, progress, cancel)

## Settings
- Library folder
- Quick-test count
- Search options (live recordings, free licences only, CD rips only, all formats)
- Cover art option (cover, all, none)
- Rip files (skip, keep)
- Embed art / write cover.jpg / online cover
- Auto MusicBrainz lookup
- Audio cache size
- Simultaneous downloads
- CD print: paper (Letter/A4), bleed, disc hole size, output folder
- Tool status (flac, ffmpeg)
