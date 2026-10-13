#!/usr/bin/env bash
# Run flacmn on a Mac straight from a git checkout: no installer, no disk image.
#
#   bash packaging/macos/dev_setup.sh            venv + packages + flac/ffmpeg, run the tests, open the app
#   bash packaging/macos/dev_setup.sh --app      the same, then also build dist/flacmn.app with PyInstaller
#   bash packaging/macos/dev_setup.sh --no-run   set up and test only
#
# Needs Python 3.10+ (python.org or Homebrew). flac and ffmpeg come from Homebrew when it is there (fine for running
# from source); an .app needs the self-contained builds from packaging/tools/build_macos.sh instead (Xcode command
# line tools + cmake), because Homebrew's link to its own libraries and its ffmpeg is GPL.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
BUILD_APP=0
RUN=1
for arg in "$@"; do
  case "$arg" in
    --app) BUILD_APP=1 ;;
    --no-run) RUN=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

# ---- Python environment ------------------------------------------------------------------------------------------
PY="${PYTHON:-python3}"
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  echo "Python 3.10 or newer is needed (found: $("$PY" --version 2>&1 || echo none))." >&2
  echo "Install it from python.org or with: brew install python" >&2
  exit 1
fi
[ -d .venv ] || "$PY" -m venv .venv
VPY="$ROOT/.venv/bin/python"
"$VPY" -m pip install --quiet --upgrade pip
"$VPY" -m pip install --quiet -e ".[gui,dev]"
echo "python packages: ok ($("$VPY" --version))"

# ---- flac, metaflac, ffmpeg ---------------------------------------------------------------------------------------
have_bundled() { [ -x packaging/bin/flac ] && [ -x packaging/bin/metaflac ] && [ -x packaging/bin/ffmpeg ]; }
if [ "$BUILD_APP" = 1 ] && ! have_bundled; then
  echo "building self-contained flac and ffmpeg for the .app (takes a few minutes)"
  bash packaging/tools/build_macos.sh
elif ! have_bundled && ! { command -v flac >/dev/null && command -v metaflac >/dev/null && command -v ffmpeg >/dev/null; }; then
  if command -v brew >/dev/null; then
    echo "installing flac and ffmpeg with Homebrew"
    brew install flac ffmpeg
  else
    echo "flac/ffmpeg not found and no Homebrew: building them into packaging/bin"
    bash packaging/tools/build_macos.sh
  fi
fi
"$VPY" -c 'from iaflac import binaries; s = binaries.status(); print("tools:", s); raise SystemExit(0 if all(s.values()) else 1)'

# ---- tests --------------------------------------------------------------------------------------------------------
"$VPY" -m unittest discover -s tests -t . 2>&1 | tail -n 3

# ---- app ----------------------------------------------------------------------------------------------------------
if [ "$BUILD_APP" = 1 ]; then
  "$VPY" -m PyInstaller packaging/iaflac-gui.spec --noconfirm
  echo "built: $ROOT/dist/flacmn.app  (open it with: open dist/flacmn.app; first launch: right-click > Open)"
fi
if [ "$RUN" = 1 ]; then
  echo "starting flacmn (close the window to stop)"
  exec "$VPY" -m iaflac.gui.app
fi
