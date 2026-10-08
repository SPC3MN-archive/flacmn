# iaflac UI mockups

SVG mockups (1440 × 900 per screen) for three layout options of the iaflac desktop app, made for editing in Inkscape.

## Files

| File | What it is |
|---|---|
| `iaflac-mockups.svg` | **All screens in one Inkscape multi-page document** (9 pages, 3 × 3 grid, 1540 px apart horizontally, 1000 px vertically) |
| `00-styleguide.svg` | Cover page: palette with hex labels, type scale, components (buttons, quality badges, tier, verdict and tested-level chips, tag-source badges, inputs, toggle, checkbox, filter chips, segmented control, progress bars, result row) and a short description of each option |
| `A1-search.svg` | Option A · Workspace · Album Search (results, album detail, Quick test, Deep test, Metadata, Save, collapsed queue drawer) |
| `A2-cd-art.svg` | Option A · Workspace · CD Art (album picker + Find art, library art checklist, found scans, case preview page 1, print) |
| `A3-library.svg` | Option A · Workspace · Library (album table, album view with tag issues, expanded download queue drawer) |
| `A4-settings.svg` | Option A · Workspace · Settings (one compact screen) |
| `B1-check.svg` | Option B · Guided steps · Album Search, step 2 Check |
| `B2-choose-scans.svg` | Option B · Guided steps · CD Art, step 2 Choose scans (preview on page 2, Inside) |
| `C1-home-search.svg` | Option C · Library first · home grid with the archive.org results slide-over open |
| `C2-album-cd-art.svg` | Option C · Library first · album side sheet on the CD Art tab (preview on page 3, Disc) |
| `build_mockups.py` | Generator script that writes all of the above |
| `png/` | PNG renders of each screen (headless Edge), used for checking the layouts |

## Opening in Inkscape (1.2 or newer)

- **All screens:** *File › Open* `iaflac-mockups.svg`. Use the **Pages** tool (Shift+F7) or the page selector in the bottom bar to go from screen to screen. *File › Export* can write each page to PNG or PDF.
- **One screen:** open any single `A1-…`, `B1-…`, etc. file.
- Open *Layer › Layers and Objects* (Ctrl+Shift+L). Each screen is split into layers (Background, Title bar, Navigation, Search, Results, Album detail, Quick test, Deep test, Metadata, Save, Queue, Activity strip, …). In the combined file each screen is a top-level layer with these as sublayers. Every component is a named group such as `Button · Search`, `Quality badge · Lossless · CD rip` or `Result · miles-davis-kind-of-blue-1959`.
- All text is editable `<text>`. Colours, fonts and sizes are attributes on each element (no CSS classes), so the Fill & Stroke and Text dialogs work directly.
- Font stack: Inter, then Segoe UI, Helvetica Neue, Arial. Install Inter for the intended look. Without it the screens use Segoe UI, which is how the PNGs were rendered.

## Regenerating

```
python design/build_mockups.py
```

Standard library only. Colours are defined once at the top of the script as role constants (`APP_BG`, `PRIMARY`, `GOOD`, `WARN`, `BAD`, `TEXT`, …). Components are reusable functions (`button`, `chip`, `quality_badge`, `tier_chip`, `verdict_chip`, `field`, `toggle`, `result_row`, `waveform`, `spectrogram`, `cover`, `scan`, …), and each screen is one `screen_XX` function. **Regenerating overwrites the SVGs**, so either edit in Inkscape or edit the script, not both.

## Palette

`#DFD9E2` app background, secondary surfaces and borders · `#5C53A2` primary (nav, buttons, selection, focus) · `#20A76B` lossless / PASS / done · `#F4B73E` warnings, hi-res, in progress · `#BC3843` lossy, failed, destructive. Plus near-black text, white cards, and tints and shades of the five hues (mixed with white or with a very dark indigo).

## The three options

### A · Workspace
A dark sidebar (Album Search, CD Art, Library, Settings) and a three-pane work area. In Album Search you search and pick a result on the left. The centre shows the album detail with its quality verdict, track list, a Quick test card (waveform) and a Deep test card (spectrogram). Metadata and Save sit on the right. The download queue is a bottom drawer: collapsed in A1 and A2, expanded in A3. An activity strip along the bottom lists running jobs.
- **Pros:** everything is visible at once, so it is fast to compare uploads and fix tags. It suits power users and big screens.
- **Cons:** dense. The visuals (waveform, spectrogram) are small, and it needs a wide window.

### B · Guided steps
A top nav plus a stepper. Album Search runs 1 Find → 2 Check → 3 Tag → 4 Save. CD Art runs 1 Pick album → 2 Choose scans → 3 Print. Each step is one focused card with Back and Next, so the Check step has room for a big waveform and spectrogram.
- **Pros:** calm and hard to get wrong. Good for first-time or occasional use, with large previews and test results.
- **Cons:** more clicks. Comparing several uploads means going back to step 1, and it is slower for batch work.

### C · Library first
The home screen is the library cover grid, with quality badges, art-completeness dots and a "Needs attention" list. A command bar ("Search archive.org…", Ctrl K) opens the results in a slide-over. Clicking an album opens a side sheet with the tabs Tracks · Upgrade (find a better copy) · CD Art.
- **Pros:** feels like a music app. Library upkeep (upgrades, missing art, tag issues) is easy to find, and CD Art lives next to the album it belongs to.
- **Cons:** searching for new albums is a secondary flow. The side sheet has less room than a full screen, and Quick test, Deep test, Tag and Save would still need a detail view (not drawn).
