"""Where the external tools (flac, metaflac, ffmpeg) live.

A packaged app ships its own copies in a `bin/` folder next to the code (PyInstaller unpacks it under
`sys._MEIPASS`); a source checkout uses whatever is installed. Order: $IAFLAC_BIN, the bundled `bin/`, then PATH.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

TOOLS = ("flac", "metaflac", "ffmpeg", "ffprobe")


def bundled_dirs() -> list:
    dirs = []
    if os.environ.get("IAFLAC_BIN"):
        dirs.append(Path(os.environ["IAFLAC_BIN"]).expanduser())
    base = getattr(sys, "_MEIPASS", None)
    if base:
        dirs.append(Path(base) / "bin")
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).resolve().parent / "bin")
    return dirs


def find(name: str) -> Optional[str]:
    """Full path to a tool, or None when it is not available."""
    exe = f"{name}.exe" if os.name == "nt" else name
    for d in bundled_dirs():
        candidate = d / exe
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def flac() -> Optional[str]:
    return find("flac")


def ffmpeg() -> Optional[str]:
    return find("ffmpeg")


def status() -> dict:
    """{tool: path or None}, for a settings or diagnostics screen."""
    return {t: find(t) for t in TOOLS}
