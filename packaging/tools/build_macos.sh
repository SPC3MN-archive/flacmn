#!/usr/bin/env bash
# Build static flac/metaflac and a small LGPL-only ffmpeg into packaging/bin for the macOS bundle.
#   packaging/tools/build_macos.sh            (on the Mac whose architecture you are building for)
# Homebrew's builds can't be bundled as they are: they link shared libraries from /opt/homebrew, and its ffmpeg is
# GPL. This ffmpeg has only what iaflac uses: audio decoders and demuxers, the spectrum picture filter and PNG output.
set -euo pipefail

FLAC_VERSION=1.5.0
FFMPEG_VERSION=8.0
export MACOSX_DEPLOYMENT_TARGET=11.0

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BIN="$ROOT/packaging/bin"
WORK="$(mktemp -d)"
JOBS="$(sysctl -n hw.ncpu)"
mkdir -p "$BIN"
trap 'rm -rf "$WORK"' EXIT

fetch() {  # url out
  echo "fetching $1"
  curl -fsSL "$1" -o "$2"
  echo "  sha256 $(shasum -a 256 "$2" | cut -d' ' -f1)"
  printf '%s\n  sha256 %s\n' "$1" "$(shasum -a 256 "$2" | cut -d' ' -f1)" >> "$BIN/SOURCES.txt"
}

printf 'Third-party tools bundled with iaflac (built from source on %s)\n\n' "$(uname -m)" > "$BIN/SOURCES.txt"

# ---- flac --------------------------------------------------------------------------------------------------------
fetch "https://github.com/xiph/flac/releases/download/$FLAC_VERSION/flac-$FLAC_VERSION.tar.xz" "$WORK/flac.tar.xz"
tar -xf "$WORK/flac.tar.xz" -C "$WORK"
cmake -S "$WORK/flac-$FLAC_VERSION" -B "$WORK/flac-build" -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
  -DWITH_OGG=OFF -DBUILD_DOCS=OFF -DBUILD_EXAMPLES=OFF -DBUILD_TESTING=OFF -DINSTALL_MANPAGES=OFF -DBUILD_CXXLIBS=OFF \
  -DCMAKE_OSX_DEPLOYMENT_TARGET="$MACOSX_DEPLOYMENT_TARGET" \
  -DCMAKE_IGNORE_PREFIX_PATH="/opt/homebrew;/usr/local" -DCMAKE_DISABLE_FIND_PACKAGE_Intl=ON >/dev/null   # no Homebrew libintl
cmake --build "$WORK/flac-build" -j "$JOBS" --target flacapp metaflac >/dev/null
cp "$(find "$WORK/flac-build" -type f -name flac -perm -u+x | head -1)" "$BIN/flac"
cp "$(find "$WORK/flac-build" -type f -name metaflac -perm -u+x | head -1)" "$BIN/metaflac"
cp "$WORK/flac-$FLAC_VERSION/COPYING.Xiph" "$BIN/FLAC-LICENSE.txt"

# ---- ffmpeg (LGPL, audio only) -----------------------------------------------------------------------------------
fetch "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz" "$WORK/ffmpeg.tar.xz"
tar -xf "$WORK/ffmpeg.tar.xz" -C "$WORK"
cd "$WORK/ffmpeg-$FFMPEG_VERSION"
./configure --prefix="$WORK/ffmpeg-out" --enable-static --disable-shared --disable-autodetect --disable-doc \
  --disable-network --disable-ffplay --disable-ffprobe --disable-everything --disable-x86asm \
  --enable-protocol=file,pipe \
  --enable-demuxer=flac,mp3,wav,ogg,mov,aiff,ape,wv,aac,image2 \
  --enable-decoder=flac,mp3,mp3float,mp2,pcm_s16le,pcm_s24le,pcm_s32le,pcm_f32le,pcm_s16be,pcm_s24be,pcm_u8,aac,alac,vorbis,opus,ape,wavpack \
  --enable-parser=flac,mpegaudio,aac,vorbis,opus \
  --enable-encoder=pcm_f32le,pcm_s16le,pcm_s24le,png \
  --enable-muxer=f32le,wav,image2,null \
  --enable-filter=showspectrumpic,aresample,aformat,anull,format,scale,null,anullsink,nullsink \
  --enable-swresample --enable-swscale --enable-zlib >/dev/null
make -j "$JOBS" >/dev/null
cp ffmpeg "$BIN/"
cp COPYING.LGPLv2.1 "$BIN/FFMPEG-LICENSE.txt"

strip "$BIN/flac" "$BIN/metaflac" "$BIN/ffmpeg"
"$BIN/flac" --version
"$BIN/ffmpeg" -hide_banner -version | head -1
if "$BIN/ffmpeg" -hide_banner -buildconf | grep -q -- "--enable-gpl"; then echo "GPL build: refusing" >&2; exit 1; fi
for tool in flac metaflac ffmpeg; do       # nothing may link outside the system
  if otool -L "$BIN/$tool" | tail -n +2 | grep -vqE '/usr/lib/|/System/Library/'; then
    echo "$tool links non-system libraries:" >&2; otool -L "$BIN/$tool" >&2; exit 1
  fi
done
echo "tools ready in $BIN"
