# Building the desktop app

The app is the `iaflac.gui` package (a local FastAPI server plus a pywebview window), frozen with PyInstaller.
External tools (`flac`, `metaflac`, `ffmpeg`) ship inside the bundle in `bin/`, where `iaflac/binaries.py` looks first.

## Run from source

```bash
python -m pip install -e ".[gui,dev]"
python -m iaflac.gui              # window
python -m iaflac.gui --browser    # in your browser instead (easier for UI work: dev tools, reload)
```

`flac` and `ffmpeg` must be on PATH (or in `packaging/bin`, or `$IAFLAC_BIN`).

## Build an installer locally

**Windows**

```powershell
powershell -ExecutionPolicy Bypass -File packaging\tools\fetch_windows.ps1   # flac + LGPL ffmpeg -> packaging\bin
pyinstaller packaging\iaflac-gui.spec --noconfirm                              # -> dist\iaflac\iaflac.exe
& "C:\Program Files (x86)\Inno Setup 6\iscc.exe" packaging\windows\iaflac.iss  # -> dist\iaflac-0.1.0-windows-x64-setup.exe
```

**macOS** (needs Xcode command line tools and cmake; build on each architecture you ship)

```bash
bash packaging/tools/build_macos.sh          # static flac + minimal LGPL ffmpeg -> packaging/bin
pyinstaller packaging/iaflac-gui.spec --noconfirm
bash packaging/macos/make_dmg.sh 0.1.0       # -> dist/iaflac-0.1.0-macos-arm64.dmg
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

## Known follow-ups

- The Windows ffmpeg is BtbN's general-purpose build (~130 MB). A minimal build like the macOS one would cut the
  installer by ~100 MB.
- App icons go in `packaging/icons/iaflac.ico` and `iaflac.icns`; the spec and installer pick them up when present.
