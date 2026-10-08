# 1. Album Search

## Search

- **Artist box**: artist name to search archive.org for (required: archive.org searches by name).
- **Album box**: album title; leave empty to list the artist's albums.
- **Search button**: searches archive.org for FLAC, WAV and MP3 uploads at once, then quick-tests the top few results automatically.
- **Format filters (FLAC / WAV / MP3)**: show or hide each format in the results without searching again.
- **Lossless only**: hides MP3 results.
- **Hide albums I already have**: hides results that match an album in your library.
- **Sort**: by quality (default), by how well it matches your search, or by popularity on archive.org.
- **Search notes**: other uploads by the artist that didn't match, albums that exist only in other lossless formats (ALAC, APE), and how many results were left out and why (live recordings, singles).

## Results (one row per upload, best quality first)

- **Quality class**: Lossless · CD rip / Lossless · hi-res / Lossless / Lossy · high bitrate / Lossy / Failed.
- **Format**: measured once tested, e.g. FLAC 24/96, MP3 VBR ~245 kbps.
- **Tier**: A strong CD-rip evidence, B likely CD rip, C real lossless with source unknown, L lossy, D failed or fake, ? untested.
- **Match**: exact or close match to what you typed.
- **Tracks, length, size, year**: track count, total length, download size and release year.
- **Rip files**: rip log / cue sheet / AccurateRip present (signs of a careful CD rip).
- **Source**: CD rip, vinyl rip, web/store, or plain (not stated).
- **Tested**: which checks have run (V0–V3).
- **In library**: marks albums you already have, with the format of your copy.
- **Row click**: opens the upload in Album Detail.

## Album Detail

- **Header**: cover, title, year, archive.org link, licence, and what the uploader claims (e.g. "EAC secure rip").
- **Track list**: title, length, size, sample rate / bit depth, and whether each file decodes cleanly (✓/✗ after a deep test).
- **Quality verdict**: the tier and the evidence for and against it; rip log summary (e.g. "EAC, all tracks accurately ripped").
- **Other uploads of this album**: duplicate copies with their tier and size, to switch to.

## Quick Test

- **Quick test button**: reads the start of each file only, to confirm the files are what they claim, read their format and the rip log. Runs automatically on the top results.
- **Track picker**: choose a track.
- **Play / pause / seek**: streams the track from archive.org without downloading the album.
- **Waveform**: the whole track's waveform, drawn once the track has cached; click to jump.
- **Readout**: sample rate and bit depth.

## Deep Test

- **Deep test button**: temporarily downloads every file, decodes it, checks it against archive.org's checksums, and checks the audio spectrum for signs of a lossy source. Shows the download size first.
- **Track picker, play / pause / seek**: as in Quick Test.
- **Spectrogram**: the whole track's frequency picture with the playhead and a line at any detected cutoff (an MP3 source shows a hard ceiling near 16–20 kHz).
- **Verdict**: PASS / SUSPECT / LIKELY LOSSY / UPSAMPLED, with the cutoff in kHz.
- **Album spectrum result**: the three-sample summary image for the whole album.

## Metadata (the tags written when saving)

- **Album fields**: album artist, album, date, genre, label, catalog number; all editable.
- **Track tag table**: disc, track number, title, track artist per file; editable.
- **Source badge on each field**: where the value came from (the file's tags, archive.org, MusicBrainz, or you).
- **MusicBrainz matches**: candidate releases with match score, date, country, format (CD, 2×CD, digital), track count and average track-length difference; Apply fills in all the tags from that release.
- **Tag changes**: before → after for every value Apply would change.
- **Cover picker**: the upload's own cover, or the Cover Art Archive's.

## Save

- **Destination**: the folder it will create, e.g. Music/Miles Davis/Kind of Blue (1959)/.
- **File names**: 01 - So What.flac (1-01 - … for multi-disc), plus cover.jpg.
- **Size and warnings**: download size, lossy notice for MP3, "folder already exists", "no cover art".
- **Add to queue button**: adds the album to the Download Queue.

## Download Queue

- **Queue row**: one row per album: state (waiting, downloading, tagging, paused, done, failed), progress bar with MB and file n/m, problems found after saving.
- **Pause / resume**: stops between chunks and resumes from where it stopped; unfinished downloads also resume after a restart.
- **Cancel / retry / remove**: stop a download, try a failed one again, or take it off the list.
- **Clear finished**: removes completed downloads from the list.

# 2. CD Art

## Album

- **Library album picker**: choose an album you already have.
- **Find art button**: searches the album folder, the archive.org upload it came from, the Cover Art Archive (including other pressings with the same tracklist) and other archive.org uploads.
- **Notes**: the matched MusicBrainz release, which pressings were searched, what wasn't found.

## Found Scans (one row per printed part)

- **Parts**: front cover · back + spines · inside of front insert · under-tray image · disc label.
- **Chosen scan**: thumbnail, where it came from (e.g. "Cover Art Archive · 1997 XE CD") and its print resolution in dpi.
- **Alternatives**: other scans for that part; click one to use it instead. By default all parts come from one pressing at the best resolution.

## Case Preview

- **Outside (page 1)**: front cover above the back tray card with its spines.
- **Inside (page 2)**: inside of the front insert and the under-tray image, laid out to print on the back of page 1.
- **Disc (page 3)**: the disc label, for label stock or a printable disc.
- **Warnings**: pieces under 200 dpi (print soft), and pieces generated because no scan was found (tracklist, spines, disc cut from the cover).

## Print

- **Make PDF**: true-size PDF with crop and fold marks.
- **Open PDF**: opens it in your PDF viewer to print.
- **Save scans to album folder**: copies the chosen scans into the album's Artwork/ subfolder (cover.jpg is left alone).

## Library Art Checklist

- **Checklist row**: one row per album: front / back / inside / under-tray / disc found (✓) or missing (–).
- **Find art for whole library**: runs the search for every album in the background (cancellable).

# 3. Library

## Albums

- **Album table**: artist, album, year, format (e.g. FLAC 16/44.1, MP3 320), track count, cover present, number of tag issues.
- **Filter box**: narrows the table by artist or album.
- **Rescan**: re-reads the library folder (only changed albums are re-read).

## Album View

- **Tracks**: title, artist, length, format per track.
- **Tag issues**: what a player like Volumio would trip over: missing titles or track numbers, gaps, mismatched album names, no cover, or a .cue file that can make it list the album twice.

# App-wide

## Top bar (always visible)

- **Navigation**: Album Search · CD Art · Library · Settings.
- **Activity strip**: running searches, tests, downloads and art lookups, each with progress and a cancel button.

## Settings

- **Library folder**: where albums are saved and scanned (local folder, USB drive or Volumio network share).
- **Search options**: include live recordings; free licences only; CD rips only; all formats (off = FLAC first, WAV/MP3 only if there's no FLAC).
- **Cover art**: save the front cover only, every image in the upload, or none.
- **Rip files**: skip or keep logs and cue sheets (skip by default so Volumio doesn't list albums twice).
- **Art options**: embed the cover in the files, write cover.jpg, use the Cover Art Archive when the upload has no cover.
- **Auto MusicBrainz**: look the album up automatically when it is opened for saving.
- **Audio cache size**: space used for streamed tracks.
- **Simultaneous downloads**: how many albums download at the same time.
- **CD print**: paper (Letter/A4), bleed, disc hole size, output folder.
- **Tool status**: confirms flac and ffmpeg were found.
