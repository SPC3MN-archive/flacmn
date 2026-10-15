# Building the desktop app

The app is the `iaflac.gui` package (a local FastAPI server plus a pywebview window), frozen with PyInstaller and
named flacmn (flacmn.app, flacmn.exe, the flacmn installers); the Python package and the command line tool stay `iaflac`.
External tools (`flac`, `metaflac`, `ffmpeg`) ship inside the bundle in `bin/`, where `iaflac/binaries.py` looks first.

## Run from source

```bash
python -m pip install -e ".[gui,dev]"
python -m iaflac.gui              # window
python -m iaflac.gui --browser    # in your browser instead (easier for UI work: dev tools, reload)
```

`flac` and `ffmpeg` must be on PATH, or in `packaging/bin` (where the fetch/build scripts put them), or `$IAFLAC_BIN`.

**macOS, from a checkout, no installer**

```bash
git clone https://github.com/SPC3MN/iaflac && cd iaflac && git checkout gui
bash packaging/macos/dev_setup.sh            # .venv, packages, flac + ffmpeg (Homebrew), tests, then opens the app
bash packaging/macos/dev_setup.sh --app      # also builds dist/flacmn.app (self-contained tools via build_macos.sh)
bash packaging/macos/dev_setup.sh --no-run   # set up and test only
```

Afterwards `.venv/bin/python -m iaflac.gui.app` starts it again (`--browser` opens it in a browser instead, handy
with the web inspector). The UI is plain HTML/CSS/JS in `iaflac/gui/static/` (no build step); the design it follows
is `design/iaflac-mockups-v3.svg`. A checkout keeps its settings in `~/.config/iaflac/config.json` (shared with the
CLI) and its download queue and caches next to the code (`.gui/`, `.library-index/`, `.cache/`; all git-ignored).

## Build an installer locally

**Windows**

```powershell
powershell -ExecutionPolicy Bypass -File packaging\tools\fetch_windows.ps1   # flac + LGPL ffmpeg -> packaging\bin
pyinstaller packaging\iaflac-gui.spec --noconfirm                              # -> dist\flacmn\flacmn.exe
& "C:\Program Files (x86)\Inno Setup 6\iscc.exe" packaging\windows\iaflac.iss  # -> dist\flacmn-0.1.0-windows-x64-setup.exe
```

**macOS** (needs Xcode command line tools and cmake; build on each architecture you ship)

```bash
bash packaging/tools/build_macos.sh          # static flac + minimal LGPL ffmpeg -> packaging/bin
pyinstaller packaging/iaflac-gui.spec --noconfirm
bash packaging/macos/make_dmg.sh 0.1.0       # -> dist/flacmn-0.1.0-macos-arm64.dmg
```

## Releases

`.github/workflows/release.yml` does all of the above on GitHub's runners when a `v*` tag is pushed and attaches a
Windows installer plus Apple Silicon and Intel disk images to a draft release. The builds are unsigned for now:
Windows SmartScreen and macOS Gatekeeper ask once on first launch (the release notes say how). Signing needs an
Apple Developer ID (notarization) and a Windows code-signing certificate; both slot into the workflow later.

## Licences of the bundled tools

- FLAC: Xiph.Org BSD-style licence (`FLAC-LICENSE.txt`).
- FFmpeg: built LGPL-only (no `--enable-gpl`; both build scripts refuse a GPL build). On Windows it is BtbN's static
  LGPL build; on macOS a minimal one built from the official source. `SOURCES.txt` records the exact downloads and
  their SHA-256 sums.
- Font: TeX Gyre Heros (free Helvetica clone, unmodified), GUST Font License / LPPL 1.3c. It lives in
  `iaflac/gui/static/fonts/` with its licence and is packaged with the rest of the UI. Helvetica Neue itself is not
  bundled: its licence does not allow redistribution in an app.

## Known follow-ups

- The Windows ffmpeg is BtbN's general-purpose build (~130 MB). A minimal build like the macOS one would cut the
  installer by ~100 MB.
- App icons go in `packaging/icons/iaflac.ico` and `iaflac.icns`; the spec and installer pick them up when present.
