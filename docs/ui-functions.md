# 1. Album Search

## Search

- **Search box**: "Artist – Album" (any dash with spaces round it), or just an artist to list their albums.
- **Search button**: searches archive.org for every format at once (FLAC, WAV, AIFF, M4A, Monkey's Audio, WavPack, MP3),
  then quick-tests the top few results automatically. An M4A is read to tell ALAC (lossless) from AAC (lossy):
  archive.org labels both "Apple Lossless Audio", so an unread M4A shows Not checked yet.
- **Format filters (FLAC / WAV / M4A / MP3 / Other)**: show or hide each format in the results without searching again;
  Other is AIFF, Monkey's Audio (APE) and WavPack.
- **Hide albums I have**: hides results that match an album in your library.
- **Sort**: by quality (default), by how well it matches your search, or by popularity on archive.org. The order holds
  while the automatic checks finish (rows update in place); **Re-sort** appears when the checks have changed the ranking.
- **Search notes**: other uploads by the artist that didn't match, albums that exist only in Shorten (SHN), which isn't searched, and how many results were left out and why (live recordings, singles).

## Results (the list block on the left, one row per upload, best quality first)

- **Name**: the album's name (the upload's title when the name can't be read), with **Best match** on the top row once it is checked.
- **Details line**: format (e.g. FLAC 16/44.1), source (CD rip, vinyl rip, "with log" when a rip log is there), tracks,
  year, and "In your library" for albums you already have.
- **Verdict**: Genuine lossless / Lossless, likely / Lossless / Hi-res (the same, never ranked above) / Lossy / Failed /
  Not checked yet, in plain words (it sums up the evidence the old Tier and Tested columns showed).
- **Size**: the download size.
- **Row click**: shows the upload on the right.

## The chosen upload (right)

- **Header**: the upload's cover (from archive.org), name, artist and year, label, tracks and length; its verdict;
  **Add to library** with the size; a one-line note when you already have the album, in a higher, the same or a lower quality.
- **Tags** (beside the header): album artist, album, date, genre, label and catalog number, edited in place (saved as
  you leave a field), each with where it came from (MB, ia, file, you); the MusicBrainz match with **Change**; **Track
  tags** opens the per-track tags in their own window.
- **Listen and check**: every track on the left (click one to play it and see its spectrogram; a dot shows whether its
  spectrum has a hard cutoff); on the right the player and a flat spectrogram, 0 to 25 kHz up the side, with a dashed
  line where the sound stops: at the file's own limit (22 kHz for a CD) for genuine lossless audio, at a sharp line
  lower down for a lossy source; a gentle fade is not called a cutoff. **3D view** shows the waterfall instead.
  **N checks passed** folds out the evidence (decode test, checksums, rip log, spectrum).

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
- **Lossless becomes FLAC**: WAV, AIFF, ALAC, APE and WavPack are converted to FLAC as they are saved, and checked
  sample for sample (`flac --verify` and the length); MP3 and AAC are kept as they are, with their tags.
- **Size and warnings**: download size, lossy notice for MP3 and AAC, "folder already exists", "no cover art".
- **Add to queue button**: adds the album to the Download Queue.

## Download Queue

- **Queue row**: one row per album: state (waiting, downloading, tagging, paused, done, failed), progress bar with MB and file n/m, problems found after saving.
- **Pause / resume**: stops between chunks and resumes from where it stopped; unfinished downloads also resume after a restart.
- **Cancel / retry / remove**: stop a download, try a failed one again, or take it off the list.
- **Clear finished**: removes completed downloads from the list.

# 2. Case Art (CD jewel case and cassette J-card): the parts on the left, the preview filling the right, one print bar under it

## Album

- **Format**: CD jewel case or cassette J-card; each has its own parts, print sheet and 3D view.
- **Album picker**: choose an album from your music folder (read in the background, with progress), or **Open album folder…** for any album folder on disk, remembered between launches.
- **Find art button**: searches the album folder, the archive.org upload it came from, the Cover Art Archive (including other pressings with the same tracklist) and other archive.org uploads.
- **Notes**: the matched MusicBrainz release, which pressings were searched, what wasn't found.

## Found Scans (one row per printed part)

- **Parts**: front cover · back + spines · inside of front insert · under-tray image · disc label.
- **Chosen scan**: thumbnail, where it came from (e.g. "Cover Art Archive · 1997 XE CD") and its print resolution in dpi.
- **Alternatives**: other scans for that part; click one to use it instead. By default all parts come from one pressing at the best resolution.
- **Your own image**: upload an image for any part.
- **Generated parts**: a part with no scan is drawn: pick its design (title, tracklist layouts, blank), its colour (the album's or the spcmn palette) and the size of the spcmn mark it carries.
- **Kept between launches**: found art, your choices, designs and uploads (in flacmn's own data folder, never the music folder).

## Case Preview

- **3D**: the case built in 3D with the parts exactly as they print: orbit it, open the lid, take the disc out (CD); the cassette's clear case with the J-card folded inside, and a tape that pops out.
- **Spine**: both tray-card spines, enlarged, as they print.
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

# 3. Library (the home screen)

## Albums (the list block on the left)

- **Album rows**: cover, album, artist and year, quality (Lossless / Hi-res / Lossy) and, when something needs fixing,
  a few words on what (missing songs, low quality, no cover, duplicates, tag issues).
- **Filter box**: narrows the list by artist or album.
- **Show**: All · Needs attention · Lossy · No cover, each with its count.
- **Check library / Check again**: the library check (missing songs, low-quality files, no cover art, albums you have
  twice); its results feed Needs attention. **Rescan**: re-reads the library folder (only changed albums are re-read).

## One album (right)

- **Header**: cover, name, artist and year, tracks and length, quality and format; **Play album**, **Edit tags**,
  **Find a lossless copy** (or **Find the complete album**, opening Album Search), **Case art**; and its facts: format,
  size, tracks, cover file, folder, tag issues.
- **Tracks**: click one to play the album from there; problems are marked on the track (no track number, no title, unreadable).
- **Needs attention**: each problem in plain words with its fix: find the missing songs or a better copy on the
  Internet Archive (then add the songs, or replace the album), find covers, edit tags, review and merge duplicates.
  Before the library check has run, it offers **Check library**. Replacements ask twice and send the old files to the Trash.
- **Cover**: the cover in use and the others found for it (Cover Art Archive, archive.org), with their sizes; **Use
  this** replaces it (asking first); **Find covers** searches for them.

## Now playing (opens from the top bar's playing display)

- **Art tab**: the cover, with the track, album and artist beside it (wrapped and shrunk to fit, never cut off), a progress bar you can click to seek, and previous / play-pause / next.
- **Scope and XY tabs**: just the live oscilloscope (waveform or stereo XY) in the album's colour; its gain backs off on loud passages so the trace never clips.
- **Keys**: Space plays and pauses, ←/→ seek 10 s, +/- volume.
- **Idle**: left alone while music plays, the card turns to the Scope tab (Settings → Now playing).

# App-wide

## Top bar (always visible)

- **Places**: Library · Album Search · Case Art.
- **Playing**: what is playing, from the library or an Album Search preview: cover, track, artist and album, progress
  (click to seek) and play/pause. Click the cover or names for the Now playing card (art, scope, XY).
- **Downloads**: the queue and running searches, tests and art lookups, each with progress and a cancel button.
- **Settings**: the sliders button; Escape goes back to the screen you came from.

## Settings

- **Library folder**: where albums are saved and scanned (local folder, USB drive or Volumio network share).
- **Search options**: include live recordings; free licences only; only uploads with a rip log or cue sheet; all formats (off = FLAC first, the other formats only if there's no FLAC).
- **Cover art**: save the front cover only, every image in the upload, or none.
- **Rip files**: skip or keep logs and cue sheets (skip by default so Volumio doesn't list albums twice).
- **Art options**: embed the cover in the files, write cover.jpg, use the Cover Art Archive when the upload has no cover.
- **Auto MusicBrainz**: look the album up automatically when it is opened for saving.
- **Audio cache size**: space used for streamed tracks.
- **Now playing**: how long without input, while music plays, before the card shows the oscilloscope (or never).
- **Simultaneous downloads**: how many albums download at the same time.
- **CD print**: paper (Letter/A4), bleed, disc hole size, output folder.
- **Tool status**: confirms flac and ffmpeg were found.
