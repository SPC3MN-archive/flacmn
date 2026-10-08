# Tests call flac / metaflac / ffmpeg by name: let them use the copies the packaging scripts fetched, like the app does.
import os as _os

from iaflac import binaries as _binaries

if _binaries.SOURCE_BIN.is_dir():
    _os.environ["PATH"] = str(_binaries.SOURCE_BIN) + _os.pathsep + _os.environ.get("PATH", "")
