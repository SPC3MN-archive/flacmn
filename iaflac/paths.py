"""Per-user folders for config, cache and run reports, on macOS, Windows and Linux.

A source checkout keeps its old behaviour (cache and runs inside the project folder, config in ~/.config/iaflac) so
existing setups don't move. An installed app uses the platform's own places via platformdirs:
  macOS    ~/Library/Application Support/iaflac, ~/Library/Caches/iaflac
  Windows  %APPDATA%\\iaflac, %LOCALAPPDATA%\\iaflac\\Cache
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from platformdirs import user_cache_dir, user_config_dir, user_data_dir

APP = "iaflac"
PROJECT = Path(__file__).resolve().parents[1]
LEGACY_CONFIG = Path("~/.config/iaflac/config.json")


def is_hidden(path: Path, base: Optional[Path] = None) -> bool:
    """A dot-file or anything inside a dot-folder (below `base`, when given): never part of an album. This covers the
    `._<name>` files macOS writes beside every file on FAT32/exFAT drives, which keep the audio file's extension
    but hold only Finder metadata, as well as .DS_Store and iaflac's own markers and staging folders."""
    parts = Path(path).relative_to(base).parts if base is not None else (Path(path).name,)
    return any(part.startswith(".") for part in parts)


def from_source() -> bool:
    """Running from a checkout (pyproject.toml beside the package), not a frozen or pip-installed app."""
    return not getattr(sys, "frozen", False) and (PROJECT / "pyproject.toml").is_file()


def config_file() -> Path:
    if os.environ.get("IAFLAC_CONFIG"):
        return Path(os.environ["IAFLAC_CONFIG"]).expanduser()
    legacy = LEGACY_CONFIG.expanduser()
    if from_source() or legacy.exists():
        return legacy
    return Path(user_config_dir(APP, appauthor=False, roaming=True)) / "config.json"


def cache_dir() -> Path:
    if os.environ.get("IAFLAC_CACHE"):
        return Path(os.environ["IAFLAC_CACHE"]).expanduser()
    if from_source():
        return PROJECT / ".cache"
    return Path(user_cache_dir(APP, appauthor=False))


def data_dir() -> Path:
    """Run reports, spectrograms and the GUI's own state (queue, library index)."""
    if os.environ.get("IAFLAC_DATA"):
        return Path(os.environ["IAFLAC_DATA"]).expanduser()
    if from_source():
        return PROJECT
    return Path(user_data_dir(APP, appauthor=False))
