#!/usr/bin/env bash
# Wrap dist/iaflac.app in a drag-to-Applications disk image.
#   packaging/macos/make_dmg.sh 0.1.0
set -euo pipefail
VERSION="${1:-0.1.0}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ARCH="$(uname -m)"
APP="$ROOT/dist/iaflac.app"
OUT="$ROOT/dist/iaflac-$VERSION-macos-$ARCH.dmg"
[ -d "$APP" ] || { echo "build the app first: pyinstaller packaging/iaflac-gui.spec" >&2; exit 1; }

# unsigned build: an ad-hoc signature keeps Apple Silicon from refusing to run it at all
codesign --force --deep --sign - "$APP"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cat > "$STAGE/First launch - read me.txt" <<'EOF'
iaflac is not signed by Apple yet, so the first time:
  1. Drag iaflac to Applications.
  2. In Applications, right-click (or Control-click) iaflac and choose Open, then Open again.
After that it opens normally. If macOS still refuses: System Settings > Privacy & Security > "Open Anyway".
EOF
rm -f "$OUT"
hdiutil create -volname "iaflac $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$OUT" >/dev/null
echo "$OUT"
