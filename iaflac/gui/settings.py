"""The GUI's settings, kept in the same config file as the CLI's (under a "gui" key; the library folder is shared)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .. import paths


@dataclass
class Settings:
    library: str = ""                      # where albums are saved (a local folder, a USB drive or a mounted Volumio share)
    auto_test_top: int = 4                 # quick header test (V1) of this many top results after every search
    max_candidates: int = 10               # search hits read per format
    include_live: bool = False
    free_only: bool = False
    rip_only: bool = False
    all_formats: bool = True               # list WAV and MP3 next to FLAC
    art: str = "cover"                     # cover | all | none
    rip_files: str = "skip"                # skip | keep (.log/.cue/.accurip; a .cue makes Volumio list the album twice)
    embed_art: bool = True
    cover_file: bool = True
    online_cover: bool = True
    auto_musicbrainz: bool = True          # look the album up on MusicBrainz when it is opened for saving
    audio_cache_mb: int = 2048
    idle_scope: int = 30                   # seconds without input, while playing, before Now playing shows the scope (0: never)
    download_concurrency: int = 1          # albums downloading at once (archive.org is happiest with 1-2)
    # CD artwork printing
    paper: str = "letter"                  # letter | a4
    bleed_mm: float = 1.5
    disc_hole_mm: float = 41.0             # 41 for most sticker labels; ~23 or less for printable discs
    print_folder: str = ""                 # where print PDFs go (default: Documents/iaflac CD prints)
    art_search_archive: bool = True        # also look through other archive.org uploads of the album for scans

    @classmethod
    def load(cls) -> "Settings":
        data = _read()
        gui = data.get("gui", {}) if isinstance(data.get("gui"), dict) else {}
        known = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in gui.items() if k in known})
        if not s.library:
            s.library = os.environ.get("IAFLAC_LIBRARY") or data.get("library") or ""
        return s

    def save(self) -> None:
        data = _read()
        data["gui"] = asdict(self)
        if self.library:
            data["library"] = self.library             # the CLI picks the same library up
        path = paths.config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def update(self, values: dict) -> list:
        """Apply the known keys of `values` (type-checked against the defaults); returns the rejected keys."""
        rejected = []
        types = {f.name: type(f.default) for f in fields(self)}
        for k, v in values.items():
            t = types.get(k)
            if t is float and isinstance(v, int) and not isinstance(v, bool):
                v = float(v)
            if t is None or (t is int and isinstance(v, bool)) or not isinstance(v, t):
                rejected.append(k)
                continue
            setattr(self, k, v)
        if self.art not in ("cover", "all", "none"):
            self.art, rejected = "cover", rejected + ["art"]
        if self.rip_files not in ("skip", "keep"):
            self.rip_files, rejected = "skip", rejected + ["rip_files"]
        if self.paper not in ("letter", "a4"):
            self.paper, rejected = "letter", rejected + ["paper"]
        if not 0 <= self.bleed_mm <= 5:
            self.bleed_mm, rejected = 1.5, rejected + ["bleed_mm"]
        if not 5 <= self.disc_hole_mm <= 50:
            self.disc_hole_mm, rejected = 41.0, rejected + ["disc_hole_mm"]
        return rejected

    @property
    def print_path(self) -> Path:
        if self.print_folder:
            return Path(self.print_folder).expanduser()
        from platformdirs import user_documents_dir
        return Path(user_documents_dir()) / "iaflac CD prints"

    @property
    def library_path(self):
        return Path(self.library).expanduser() if self.library else None

    def to_dict(self) -> dict:
        return asdict(self)


def _read() -> dict:
    try:
        data = json.loads(paths.config_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
