"""The GUI's application core: everything the API can do, without HTTP. server.py is a thin layer over this.

Search results and loaded candidates live in memory for the session (keyed by search id and identifier); the
download queue and settings are kept on disk.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .. import __version__, binaries, musicbrainz as mb, paths, quality, queries, save, tagging
from .. import cassette, cdart, designs, health, jewelcase, library_index
from ..ia_client import IAClient, IAError
from ..names import folder_component
from ..model import track_files
from ..pipeline import Candidate, Options, Pipeline
from ..queries import Want
from ..tagging import AlbumMeta
from . import audio, serialize
from .jobs import DownloadQueue, Job, JobManager, QueueItem
from .settings import Settings


PLAYABLE = {".flac", ".mp3", ".wav", ".ogg", ".m4a", ".opus"}     # any of the item's audio, originals or IA's derivatives
def image_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:3] == b"GIF":
        return "image/gif"
    if data[:2] in (b"II", b"MM"):
        return "image/tiff"
    if data[:2] == b"BM":
        return "image/bmp"
    return "image/jpeg"


class NotFound(LookupError):
    pass


@dataclass
class SearchSession:
    id: str
    want: Optional[Want] = None
    result: object = None             # WantResult
    identifiers: list = field(default_factory=list)
    created: float = field(default_factory=time.time)
    done: bool = False


# what can be printed: the parts each format uses (cdart.PARTS names), its file name and its drawing module
FORMATS = {
    "cd": {"parts": cdart.PARTS, "suffix": "CD case", "module": jewelcase},
    "cassette": {"parts": ("front", "back", "inside"), "suffix": "cassette J-card", "module": cassette},
}


def check_format(fmt: str) -> str:
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}: one of {', '.join(FORMATS)}")
    return fmt


class FetchError(audio.AudioError):
    """An image could not be fetched (the Cover Art Archive did not answer, a file went missing)."""


def open_with_system(path: Path) -> None:
    import os
    import subprocess
    import sys
    if sys.platform == "win32":
        os.startfile(str(path))                      # noqa: S606 (a file the app itself wrote)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def parse_query(text: str = "", artist: str = "", album: str = "") -> Want:
    """'Artist - Album', 'Artist', or explicit artist/album fields."""
    if artist or album:
        artist, album = artist.strip(), album.strip()
        if not artist:                              # album only: archive.org needs some name to search by
            return Want(raw=album, artist=album, album="")
        return Want(raw=f"{artist} - {album}" if album else artist, artist=artist, album=album)
    wants = queries.parse_wanted(text or "")
    if not wants:
        raise ValueError("type an artist, or Artist - Album")
    return wants[0]


class App:
    def __init__(self, settings: Optional[Settings] = None, client: Optional[IAClient] = None,
                 mb_client: Optional[mb.MBClient] = None, broadcast: Optional[Callable[[dict], None]] = None,
                 queue_store: Optional[Path] = None):
        self.settings = settings or Settings.load()
        self.client = client or IAClient()
        self.mb = mb_client or mb.MBClient()
        self._listeners: list = []
        self._lock = threading.RLock()
        if broadcast is not None:
            self._listeners.append(broadcast)
        self.jobs = JobManager(self.broadcast)
        self.audio = audio.AudioCache(self.client, max_bytes=self.settings.audio_cache_mb * 2**20)
        self.searches: dict = {}
        self.cands: dict = {}                 # identifier -> Candidate
        self.wants: dict = {}                 # identifier -> Want it was found for
        self.metas: dict = {}                 # identifier -> AlbumMeta being edited
        self.matches: dict = {}               # identifier -> [ReleaseMatch]
        self.library: Optional[library_index.LibraryIndex] = None
        self._scan_job: Optional[str] = None          # the music-folder read in progress, if any
        self._opened: dict = {}                       # album folder opened on its own -> its AlbumEntry
        self.arts: dict = {}                  # album folder -> cdart.ArtSet
        self.art_selections: dict = {}        # album folder -> {front, back, inside, tray, disc}: image id, "colour:#rrggbb" or None
        self.art_palettes: dict = {}          # album folder -> {"from": image id, "colours": ["#rrggbb", ...]}
        self.art_textures: dict = {}          # (album folder, format) -> {part: PNG bytes} for the previews
        self.uploads: dict = {}               # album folder -> [ArtImage] you uploaded (kept across Find again)
        self.art_styles: dict = {}            # album folder -> {part: design} for the parts flacmn generates
        self._texture_version = 0
        self.health_index: Optional[health.HealthReport] = None
        self._health_job: Optional[str] = None
        self.health_found: dict = {}          # album folder -> its last Internet Archive search (health.search_archive)
        self.health_art: dict = {}            # image id -> cdart.ArtImage offered as a cover by those searches
        store = queue_store if queue_store is not None else paths.data_dir() / ".gui" / "queue.json"
        self.queue = DownloadQueue(self._run_save, self.broadcast, store=store,
                                   concurrency=self.settings.download_concurrency)
        self._load_albums()

    # ---- what you did with each album, kept between launches ----------------------------------------------------
    # The art found for each album, your choice for each part, the designs of the generated parts and your uploads are
    # kept in flacmn's own data folder (never the music folder) and read back on the next start, so nothing has to be
    # found or chosen again. A file that cannot be read is set aside, not trusted.
    def _albums_file(self) -> Path:
        return paths.data_dir() / ".gui" / "albums.json"

    def _load_albums(self) -> None:
        import json
        path = self._albums_file()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            try:
                path.replace(path.with_suffix(".unreadable.json"))
            except OSError:
                pass
            return
        for folder, saved in (data.get("albums") or {}).items() if isinstance(data, dict) else ():
            try:
                art = cdart.ArtSet.from_dict(saved["art"])
            except (KeyError, TypeError, ValueError):
                continue
            # an upload whose cached file has gone is dropped, with any choice of it
            art.images = [im for im in art.images if im.source != "upload" or Path(im.ref).is_file()]
            known = {im.id for im in art.images}
            self.arts[folder] = art
            sel = saved.get("selection") or {}
            self.art_selections[folder] = {k: (v if v is None or cdart.is_colour(v) or v in known else None)
                                           for k, v in sel.items() if k in cdart.PARTS}
            self.art_styles[folder] = {k: v for k, v in (saved.get("styles") or {}).items() if isinstance(v, str)}
            self.uploads[folder] = [im for im in art.images if im.source == "upload" and im.id in set(saved.get("uploads") or [])]

    def _save_albums(self) -> None:
        import json
        import os
        albums = {folder: {"art": {k: v for k, v in art.to_dict().items() if k not in ("completeness", "selection")},
                           "selection": self.art_selections.get(folder) or {},
                           "styles": self.art_styles.get(folder) or {},
                           "uploads": [im.id for im in self.uploads.get(folder, [])]}
                  for folder, art in self.arts.items()}
        path = self._albums_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps({"version": 1, "albums": albums}), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass                                       # keeping them is a convenience; the session carries on

    # ---- events ------------------------------------------------------------------------------------------------
    def listen(self, fn: Callable[[dict], None]) -> Callable[[], None]:
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    def broadcast(self, event: dict) -> None:
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception:
                pass

    def shutdown(self) -> None:
        self.queue.stop()

    # ---- status and settings -----------------------------------------------------------------------------------
    def health(self) -> dict:
        return {"version": __version__, "tools": binaries.status(), "library": self.settings.library,
                "library_ok": bool(self.settings.library_path and self.settings.library_path.is_dir()),
                "cache": str(paths.cache_dir()), "config": str(paths.config_file())}

    def update_settings(self, values: dict) -> dict:
        rejected = self.settings.update(values)
        self.settings.save()
        if "audio_cache_mb" in values:
            self.audio.max_bytes = self.settings.audio_cache_mb * 2**20
        if "library" in values:
            self.library = None
        return {"settings": self.settings.to_dict(), "rejected": rejected}

    def options(self, **over) -> Options:
        s = self.settings
        o = Options(top=50, include_live=s.include_live, free_only=s.free_only, rip_only=s.rip_only, deep="none",
                    max_candidates=s.max_candidates,
                    spectrogram_dir=paths.cache_dir() / "spectrograms", fallback=("wav", "vbr"),
                    all_formats=s.all_formats)
        for k, v in over.items():
            setattr(o, k, v)
        return o

    def pipeline(self, job: Optional[Job] = None) -> Pipeline:
        if job is None:
            return Pipeline(self.client)
        return Pipeline(self.client, progress=self.jobs.progress(job), cancel=job.cancelled)

    # ---- candidates --------------------------------------------------------------------------------------------
    def cand(self, identifier: str, media: Optional[str] = None) -> Candidate:
        with self._lock:
            c = self.cands.get(identifier)
        if c is not None and (media is None or c.item.media == media):
            return c
        try:
            c = self.pipeline().load(identifier, media=media or "flac")
        except IAError as exc:
            raise NotFound(str(exc)) from exc
        if not c.item.flacs and media is None:      # opened directly by identifier: pick the media it has
            for m in ("wav", "mp3"):
                if track_files(c.item.files, m):
                    c.item.media = m
                    break
        with self._lock:
            self.cands[identifier] = c
        return c

    def _remember(self, cands: list, want: Optional[Want]) -> None:
        with self._lock:
            for c in cands:
                self.cands[c.identifier] = c
                if want is not None:
                    self.wants[c.identifier] = want

    def summary(self, identifier: str) -> dict:
        c = self.cand(identifier)
        return serialize.candidate_summary(c, self.wants.get(identifier), self.library)

    def detail(self, identifier: str) -> dict:
        c = self.cand(identifier)
        return serialize.candidate_detail(c, self.wants.get(identifier), self.library)

    def _ranked(self, sess: SearchSession) -> list:
        with self._lock:
            cands = [self.cands[i] for i in sess.identifiers if i in self.cands]
        return [serialize.candidate_summary(c, sess.want, self.library) for c in quality.rank(cands)]

    # ---- search ------------------------------------------------------------------------------------------------
    def search(self, text: str = "", artist: str = "", album: str = "") -> dict:
        want = parse_query(text, artist, album)
        sess = SearchSession(id=uuid.uuid4().hex[:12], want=want)
        self.searches[sess.id] = sess
        self._ensure_library()

        def run(job: Job):
            pipe = self.pipeline(job)
            res = pipe.search_one(want, self.options())
            sess.result = res
            sess.identifiers = [c.identifier for c in res.found]
            self._remember(res.found, want)
            sess.done = True
            self.broadcast({"type": "results", "search_id": sess.id, "results": self._ranked(sess),
                            "notes": res.notes, "near_misses": res.near_misses, "lossless_other": res.lossless_other,
                            "excluded": res.excluded})
            top = quality.rank(res.found)[: self.settings.auto_test_top]
            if top:
                self._quick_test(top, want, sess)
            return {"search_id": sess.id, "count": len(res.found)}

        job = self.jobs.submit("search", want.label, run)
        return {"search_id": sess.id, "job_id": job.id, "want": {"artist": want.artist, "album": want.album}}

    def _quick_test(self, cands: list, want, sess: Optional[SearchSession]) -> Job:
        def run(job: Job):
            pipe = self.pipeline(job)
            for c in cands:
                if job.cancelled():
                    break
                if c.tier == "?":
                    pipe.inspect(c, want)
                self.broadcast({"type": "candidate", "candidate": serialize.candidate_summary(c, want, self.library)})
            if sess is not None:
                self.broadcast({"type": "results", "search_id": sess.id, "results": self._ranked(sess)})
            return {"tested": [c.identifier for c in cands]}

        return self.jobs.submit("test", f"quick test of {len(cands)} result(s)", run)

    def results(self, search_id: str) -> dict:
        sess = self.searches.get(search_id)
        if sess is None:
            raise NotFound(f"no search {search_id}")
        res = sess.result
        return {"search_id": sess.id, "done": sess.done,
                "want": {"artist": sess.want.artist, "album": sess.want.album} if sess.want else None,
                "results": self._ranked(sess),
                "notes": list(getattr(res, "notes", []) or []),
                "near_misses": list(getattr(res, "near_misses", []) or []),
                "lossless_other": list(getattr(res, "lossless_other", []) or []),
                "excluded": dict(getattr(res, "excluded", {}) or {})}

    def test(self, identifier: str, level: str = "quick") -> dict:
        """The quick test (headers + rip logs, V1). There is no deep test in the app: a track's spectrogram is drawn
        on request instead (/api/track/spectrum3d)."""
        if level != "quick":
            raise ValueError("level must be quick")
        c = self.cand(identifier)
        return {"job_id": self._quick_test([c], self.wants.get(identifier), None).id}

    # ---- metadata ----------------------------------------------------------------------------------------------
    def meta(self, identifier: str, fresh: bool = False) -> dict:
        c = self.cand(identifier)
        if fresh or identifier not in self.metas:
            if not c.probes and not c.alt_stats:
                self.pipeline().probe(c)
            self.metas[identifier] = tagging.from_candidate(c)
        m = self.metas[identifier]
        return {"meta": m.to_dict(), "folder": self._folder_preview(m), "issues": self._meta_issues(c, m)}

    def set_meta(self, identifier: str, data: dict) -> dict:
        c = self.cand(identifier)
        m = AlbumMeta.from_dict(data)
        for f in ("albumartist", "album", "date", "genre", "label", "catalog"):
            old = self.metas.get(identifier)
            if old is None or getattr(old, f) != getattr(m, f):
                m.sources[f] = "you"
        self.metas[identifier] = m
        return {"meta": m.to_dict(), "folder": self._folder_preview(m), "issues": self._meta_issues(c, m)}

    def _folder_preview(self, m: AlbumMeta) -> Optional[str]:
        lib = self.settings.library_path
        try:
            return str(tagging.album_folder(lib, m)) if lib else None
        except ValueError:
            return None

    def _meta_issues(self, c: Candidate, m: AlbumMeta) -> list:
        return save._check_meta(m, c.item)

    def musicbrainz(self, identifier: str, artist: str = "", album: str = "") -> dict:
        self.meta(identifier)
        m = self.metas[identifier]

        def run(job: Job):
            found = mb.find_matches(self.mb, m, artist, album)
            self.matches[identifier] = found
            return {"identifier": identifier, "matches": [x.to_dict() for x in found]}

        return {"job_id": self.jobs.submit("musicbrainz", f"MusicBrainz: {m.albumartist} / {m.album}", run).id}

    def apply_release(self, identifier: str, release_id: str) -> dict:
        self.meta(identifier)
        old = self.metas[identifier]
        new, warnings = mb.apply_release(old, self.mb.release(release_id))
        self.metas[identifier] = new
        c = self.cand(identifier)
        return {"meta": new.to_dict(), "diff": tagging.diff(old, new), "warnings": warnings,
                "folder": self._folder_preview(new), "issues": self._meta_issues(c, new)}

    # ---- saving ------------------------------------------------------------------------------------------------
    def save_options(self) -> save.SaveOptions:
        s = self.settings
        return save.SaveOptions(art=s.art, rip=s.rip_files, embed_art=s.embed_art, cover_file=s.cover_file,
                                online_cover=s.online_cover)

    def _library(self) -> Path:
        lib = self.settings.library_path
        if lib is None:
            raise ValueError("choose a library folder in Settings first")
        if not lib.is_dir():
            raise ValueError(f"the library folder {lib} does not exist (is the drive or network share connected?)")
        return lib.resolve()          # resolved like every path compared with it (macOS /var is /private/var, symlinked folders)

    def plan(self, identifier: str) -> dict:
        c = self.cand(identifier)
        self.meta(identifier)
        p = save.plan_save(c, self._library(), self.metas[identifier], self.save_options())
        out = p.to_dict()
        owned = self.library.find(p.meta.albumartist, p.meta.album) if self.library else None
        out["in_library"] = owned.to_dict() if owned else None
        return out

    def enqueue(self, identifier: str) -> dict:
        c = self.cand(identifier)
        if c.tier == "D":
            raise ValueError("this upload failed its tests (tier D) and is not saved")
        self.meta(identifier)
        m = self.metas[identifier]
        p = save.plan_save(c, self._library(), m, self.save_options())
        item = self.queue.add(identifier=identifier, media=c.item.media, artist=m.albumartist, album=m.album,
                              folder=str(p.folder), meta=m.to_dict(), options=self.save_options().__dict__,
                              total_bytes=p.total_bytes)
        return item.to_dict()

    def _run_save(self, item: QueueItem, progress, cancelled) -> dict:
        c = self.cand(item.identifier, media=item.media)
        if not c.probes and not c.alt_stats:
            self.pipeline().probe(c)
        meta = AlbumMeta.from_dict(item.meta)
        opts = save.SaveOptions(**item.options)
        plan = save.plan_save(c, self._library(), meta, opts)
        result = save.apply_save(self.client, c, plan, opts, mb_client=self.mb, progress=progress, cancel=cancelled)
        if result.ok and self.library is not None:
            self.library = None                         # rescanned on next use
            self._ensure_library()
        return result.to_dict()

    # ---- library -----------------------------------------------------------------------------------------------
    def _ensure_library(self) -> None:
        if self.library is None and self.settings.library_path and self.settings.library_path.is_dir():
            try:
                self.library = library_index.scan(self.settings.library_path)
            except OSError:
                self.library = None

    def library_scan(self) -> dict:
        """Read the music folder again in the background (one read at a time); progress and a "library" event follow."""
        root = self._library()
        with self._lock:
            if self._scan_job:
                return {"job_id": self._scan_job}

            def run(job: Job):
                try:
                    self.library = library_index.scan(root, progress=self.jobs.progress(job), cancel=job.cancelled)
                    return {"albums": len(self.library.albums)}
                finally:
                    with self._lock:
                        self._scan_job = None
                    self.broadcast({"type": "library", "count": len(self.library.albums) if self.library else 0})

            self._scan_job = self.jobs.submit("library", f"read {root}", run).id
            return {"job_id": self._scan_job}

    def library_albums(self) -> dict:
        """The albums to choose from: any opened on their own first, then the music folder's. Never waits for a read
        of the music folder: the first call starts one, and a "library" event says when it is done."""
        lib = self.settings.library_path
        if self.library is None and lib and lib.is_dir() and not self._scan_job:
            self.library_scan()
        out = {"root": self.settings.library, "albums": [], "scanned": self.library is not None,
               "scanning": self._scan_job, "library_ok": bool(lib and lib.is_dir())}
        if self.library is not None:
            out.update(self.library.to_dict())
        out["albums"] = [{**e.to_dict(), "opened": True} for e in self._opened_albums()] + out["albums"]
        return out

    # ---- albums opened on their own (any folder, in the music folder or not) -----------------------------------
    def _opened_albums(self) -> list:
        out = []
        for path in self.settings.opened:
            folder = Path(path)
            if not folder.is_dir():
                continue
            entry = self._opened.get(path)
            if entry is None:
                files = library_index._audio_files(folder) or [p for p in folder.rglob("*") if p.is_file()
                                                                 and p.suffix.lower() in library_index.AUDIO
                                                                 and not paths.is_hidden(p, folder)]
                entry = library_index.read_album(folder.parent, folder, files)
                entry.folder = str(folder)
                self._opened[path] = entry
            out.append(entry)
        return out

    def open_album(self, path: str) -> dict:
        """Add one album folder to the list (remembered between launches), for an album outside the music folder or
        to start before the music folder has been read."""
        folder = Path(path).expanduser()
        if not folder.is_dir():
            raise ValueError(f"{path} is not a folder")
        folder = folder.resolve()
        if not any(p.suffix.lower() in library_index.AUDIO for p in folder.rglob("*") if p.is_file()):
            raise ValueError(f"{folder.name} has no audio files: choose the album's own folder")
        key = str(folder)
        self.settings.opened = [key] + [p for p in self.settings.opened if p != key][:19]
        self.settings.save()
        self._opened.pop(key, None)
        entry = next(e for e in self._opened_albums() if e.folder == key)
        return {**entry.to_dict(), "opened": True}

    def _is_opened(self, folder: str) -> bool:
        return folder in self.settings.opened

    def _root_for(self, folder: str) -> Optional[Path]:
        """The music folder an album's paths are relative to, or None for an album opened on its own."""
        return None if self._is_opened(folder) else self._library()

    def library_album(self, folder: str) -> dict:
        root = self._library()
        path = (root / folder).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_dir():
            raise NotFound(folder)
        files = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in library_index.AUDIO and not paths.is_hidden(p, path))
        tracks = []
        for p in files:
            t = tagging.read_track(p) if p.suffix.lower() in (".flac", ".mp3") else {"format": p.suffix[1:].upper()}
            t["path"] = p.relative_to(root).as_posix()
            tracks.append(t)
        return {"folder": folder, "tracks": tracks, "issues": tagging.validate(path),
                "cover": next((p.relative_to(root).as_posix() for p in path.iterdir() if p.name.lower() in tagging.COVER_NAMES), None)}

    def library_file(self, rel: str) -> Path:
        """A file of an album (its cover, for the picker): relative to the music folder, or inside an opened album."""
        opened = [Path(p) for p in self.settings.opened]
        if Path(rel).is_absolute() and audio.inside(Path(rel), opened):
            return Path(rel).resolve()
        root = self._library()
        path = root / rel
        if not audio.inside(path, [root]):
            raise NotFound(rel)
        return path.resolve()

    # ---- CD artwork: find the scans, choose, print, keep -------------------------------------------------------
    def _album_dir(self, folder: str) -> Path:
        if self._is_opened(folder):
            if not Path(folder).is_dir():
                raise NotFound(f"the album folder {folder} is gone (is the drive connected?)")
            return Path(folder).resolve()
        root = self._library()
        path = (root / folder).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_dir():
            raise NotFound(f"no album folder {folder!r} in the library")
        return path

    def _art(self, folder: str) -> cdart.ArtSet:
        art = self.arts.get(folder)
        if art is None:
            raise NotFound(f"no artwork search for {folder!r} yet: run find first")
        return art

    def art_find(self, folder: str, release_id: str = "") -> dict:
        path = self._album_dir(folder)

        def run(job: Job):
            art = cdart.find_art(path, mb_client=self.mb, ia_client=self.client, release_id=release_id,
                                 search_archive=self.settings.art_search_archive, library_root=self._root_for(folder),
                                 progress=self.jobs.progress(job), cancel=job.cancelled)
            art.images += [im for im in self.uploads.get(folder, []) if art.get(im.id) is None]
            self.arts[folder] = art
            sel = cdart.default_selection(art)
            self.art_selections[folder] = cdart.fill_gaps(sel, self._palette(folder, art, sel, cancel=job.cancelled))
            self._save_albums()
            out = self.art_state(folder)
            self.broadcast({"type": "art", "folder": folder, "completeness": out["completeness"]})
            return out

        return {"job_id": self.jobs.submit("art", f"artwork: {folder}", run).id}

    def art_state(self, folder: str) -> dict:
        art = self._art(folder)
        sel = self.art_selections.get(folder) or cdart.default_selection(art)
        pdfs = {fmt: str(self._pdf_path(art, fmt)) if self._pdf_path(art, fmt).exists() else None for fmt in FORMATS}
        return {**art.to_dict(), "selection": sel, "palette": self._palette(folder, art, sel), "pdf": pdfs["cd"], "pdfs": pdfs,
                "styles": dict(self.art_styles.get(folder, {}))}

    def _palette(self, folder: str, art: cdart.ArtSet, selection: dict, cancel=None) -> list:
        """The album's colours, from the chosen front cover (else any scan); worked out once per source image."""
        src = cdart.palette_source(art, selection)
        if src is None:
            self.art_palettes.pop(folder, None)
            return []
        known = self.art_palettes.get(folder)
        if known and known["from"] == src.id:
            return known["colours"]
        try:
            colours = cdart.palette(cdart.ArtFetcher(self.mb, self.client).get(src, cancel=cancel))
        except (mb.MBError, IAError, OSError, ValueError):     # no colours is fine: the parts stay generated on white
            colours = []
        self.art_palettes[folder] = {"from": src.id, "colours": colours}
        return colours

    def art_select(self, folder: str, selection: dict) -> dict:
        art = self._art(folder)
        clean = {}
        for part in cdart.PARTS:
            iid = selection.get(part)
            if iid is not None and not cdart.is_colour(iid) and art.get(iid) is None:
                raise ValueError(f"{part}: unknown image {iid}")
            clean[part] = iid
        self.art_selections[folder] = clean
        self._save_albums()
        return self.art_state(folder)

    def _folder_of(self, art: cdart.ArtSet) -> Optional[str]:
        return next((f for f, a in self.arts.items() if a is art), None)

    # the layouts each part offers (designs.LAYOUTS); every layout offers the spcmn mark (designs.logos_for) and its ink
    PART_LAYOUTS = {"front": ("title", "blank"), "back": designs.TRACKLISTS + ("blank",),
                    "inside": designs.TRACKLISTS + ("blank",), "tray": ("blank",), "disc": ("title",)}

    def art_style(self, folder: str, part: str, layout: Optional[str] = None, logo: Optional[str] = None,
                  ink: Optional[str] = None) -> dict:
        """Choose how a part flacmn generates (one not printed from a scan or upload) looks: its layout, the spcmn
        mark's size on it, and the mark's colour. Anything left out stays as it was. See designs.py."""
        self._art(folder)
        if part not in self.PART_LAYOUTS:
            raise ValueError(f"unknown part {part!r}")
        styles = self.art_styles.setdefault(folder, {})
        layouts = self.PART_LAYOUTS[part]
        if layout is not None:
            if layout not in layouts:
                raise ValueError(f"{layout!r} is not a layout for the {part}: one of {', '.join(layouts)}")
            styles[part] = layout
        current = styles.get(part, layouts[0])
        if logo is not None:
            if logo not in designs.logos_for(current):
                raise ValueError(f"{logo!r} is not a logo size for a {current} {part}: one of {', '.join(designs.logos_for(current))}")
            styles[f"{part}:logo"] = logo
        elif styles.get(f"{part}:logo") not in designs.logos_for(current) + (None,):
            styles[f"{part}:logo"] = "medium"          # a large mark does not fit beside a tracklist
        if ink is not None:
            if ink not in designs.INKS:
                raise ValueError(f"{ink!r} is not a logo colour: one of {', '.join(designs.INKS)}")
            styles[f"{part}:ink"] = ink
        self._save_albums()
        return self.art_state(folder)

    def art_upload(self, folder: str, part: str, data: bytes, name: str = "") -> dict:
        """Use an image of your own for one part (front, back, inside, tray or disc) and choose it there."""
        art = self._art(folder)
        if part not in cdart.PARTS:
            raise ValueError(f"unknown part {part!r}: one of {', '.join(cdart.PARTS)}")
        im = cdart.store_upload(data, name, cdart.PART_KIND[part])
        if art.get(im.id) is None:
            art.images.append(im)
        mine = self.uploads.setdefault(folder, [])
        if not any(u.id == im.id for u in mine):
            mine.append(im)
        sel = dict(self.art_selections.get(folder) or cdart.default_selection(art))
        sel[part] = im.id
        self.art_selections[folder] = sel
        self._save_albums()
        return self.art_state(folder)

    def art_image(self, image_id: str, preview: bool = True) -> tuple:
        """(bytes, mime) of one found image; the Cover Art Archive's 500 px thumbnail when a preview is enough."""
        for art in self.arts.values():
            im = art.get(image_id)
            if im is None:
                continue
            fetcher = cdart.ArtFetcher(self.mb, self.client)
            data = None
            if preview and im.source.startswith("coverartarchive") and im.preview and im.preview != im.ref:
                try:
                    data = self.mb.image(im.preview)
                except mb.MBError:              # the archive's thumbnails fail now and then; the full image may not
                    data = None
            if data is None:
                try:
                    data = fetcher.get(im)
                except (mb.MBError, IAError, OSError, ValueError) as exc:
                    raise FetchError(f"could not fetch the image: {exc}") from exc
            return data, image_mime(data)
        raise NotFound(f"no image {image_id}")

    def _pdf_path(self, art: cdart.ArtSet, fmt: str = "cd") -> Path:
        name = (folder_component(f"{art.artist} - {art.album}" + (f" ({art.year})" if art.year else ""))
                + f" - {FORMATS[fmt]['suffix']}.pdf")
        return self.settings.print_path / name

    def _print_options(self, art: cdart.ArtSet, discs: int = 0, **over) -> jewelcase.PrintOptions:
        s = self.settings
        opts = jewelcase.PrintOptions(paper=s.paper, bleed=s.bleed_mm, disc_inner=s.disc_hole_mm,
                                      discs=discs or max([t["disc"] for t in art.tracks] or [1]))
        for k, v in over.items():
            setattr(opts, k, v)
        return opts

    def _render(self, art: cdart.ArtSet, sel: dict, out: Path, opts: jewelcase.PrintOptions, job: Job,
                fmt: str = "cd") -> jewelcase.PrintReport:
        """Fetch the chosen scans the format uses and draw its print PDF to `out`."""
        fetcher = cdart.ArtFetcher(self.mb, self.client)
        emit = self.jobs.progress(job)
        used = FORMATS[fmt]["parts"]
        wanted = [k for k in used if sel.get(k) and not cdart.is_colour(sel[k])]

        def load(part):
            if cdart.is_colour(sel[part]):
                return jewelcase.Colour(sel[part].split(":", 1)[1])
            im = art.get(sel[part])
            emit({"stage": "art", "step": "fetching", "part": part, "label": im.label,
                  "index": wanted.index(part) + 1, "total": len(wanted)})
            return jewelcase.Art(fetcher.get(im, cancel=job.cancelled), im.label)

        parts = {k: load(k) if sel.get(k) else None for k in used}
        out.parent.mkdir(parents=True, exist_ok=True)
        emit({"stage": "art", "step": "rendering"})
        styles = dict(self.art_styles.get(art_key, {})) if (art_key := self._folder_of(art)) else {}
        return FORMATS[fmt]["module"].render(out, artist=art.artist, album=art.album, year=art.year, tracks=art.tracks,
                                             opts=opts, styles=styles, **parts)

    def art_print(self, folder: str, discs: int = 0, fmt: str = "cd") -> dict:
        art = self._art(folder)
        check_format(fmt)
        sel = dict(self.art_selections.get(folder) or cdart.default_selection(art))
        opts = self._print_options(art, discs)

        def run(job: Job):
            out = self._pdf_path(art, fmt)
            report = self._render(art, sel, out, opts, job, fmt)
            result = {"pdf": str(out), "format": fmt, **report.to_dict()}
            self.broadcast({"type": "art_printed", "folder": folder, "pdf": str(out), "format": fmt})
            return result

        return {"job_id": self.jobs.submit("print", f"{FORMATS[fmt]['suffix']}: {art.artist} / {art.album}", run).id}

    def art_preview3d(self, folder: str, fmt: str = "cd") -> dict:
        """Draw the format exactly as it prints (one disc, no crop marks) and cut out its parts as pictures, for the
        3D view and the exact template previews. CD: front, back (tray card with spines), inside, tray, disc;
        cassette: outside, inside, spine, front."""
        art = self._art(folder)
        check_format(fmt)
        sel = dict(self.art_selections.get(folder) or cdart.default_selection(art))
        opts = self._print_options(art, 1, crop_marks=False)

        def run(job: Job):
            out = paths.cache_dir() / "preview3d" / f"{fmt}.pdf"
            self._render(art, sel, out, opts, job, fmt)
            textures = FORMATS[fmt]["module"].part_images(out, opts)
            with self._lock:
                self.art_textures[(folder, fmt)] = textures
                self._texture_version += 1
                version = self._texture_version
            return {"folder": folder, "format": fmt, "version": version, "parts": sorted(textures)}

        return {"job_id": self.jobs.submit("preview3d", f"preview: {art.artist} / {art.album}", run).id}

    def art_texture(self, folder: str, part: str, fmt: str = "cd") -> bytes:
        textures = self.art_textures.get((folder, fmt)) or {}
        if part not in textures:
            raise NotFound(f"no 3D preview of {part!r} for {folder!r} yet")
        return textures[part]

    def art_pdf(self, folder: str, fmt: str = "cd") -> Path:
        path = self._pdf_path(self._art(folder), check_format(fmt))
        if not path.exists():
            raise NotFound("no print sheet yet: make one first")
        return path

    def art_open(self, folder: str, fmt: str = "cd") -> dict:
        """Open the print PDF in the system's PDF viewer (to print it from there)."""
        path = self.art_pdf(folder, fmt)
        open_with_system(path)
        return {"opened": str(path)}

    def art_save(self, folder: str) -> dict:
        art, path = self._art(folder), self._album_dir(folder)
        sel = self.art_selections.get(folder) or cdart.default_selection(art)

        def run(job: Job):
            written = cdart.save_to_folder(art, sel, path, cdart.ArtFetcher(self.mb, self.client))
            self.library = None
            return {"written": written}

        return {"job_id": self.jobs.submit("art-save", f"save scans: {folder}", run).id}

    def art_scan_all(self) -> dict:
        """Find artwork for every album in the library: a checklist of what each CD can be rebuilt with."""
        root = self._library()

        def run(job: Job):
            index = library_index.scan(root)
            self.library = index
            emit = self.jobs.progress(job)
            rows = []
            for n, entry in enumerate(index.albums, 1):
                if job.cancelled():
                    break
                emit({"stage": "art-all", "index": n, "total": len(index.albums), "folder": entry.folder})
                try:
                    art = cdart.find_art(root / entry.folder, mb_client=self.mb, ia_client=self.client,
                                         search_archive=self.settings.art_search_archive, library_root=root,
                                         cancel=job.cancelled)
                except Exception as exc:                  # one album's trouble never stops the sweep
                    rows.append({"folder": entry.folder, "error": f"{type(exc).__name__}: {exc}"})
                    continue
                self.arts[entry.folder] = art
                self.art_selections[entry.folder] = cdart.default_selection(art)
                rows.append(self._art_row(art))
                self.broadcast({"type": "art", "folder": entry.folder, "completeness": art.completeness()})
            self._save_albums()
            return {"albums": rows}

        return {"job_id": self.jobs.submit("art-all", f"artwork for the whole library ({root})", run).id}

    @staticmethod
    def _art_row(art: cdart.ArtSet) -> dict:
        c = art.completeness()
        return {"folder": art.folder, "artist": art.artist, "album": art.album, "year": art.year,
                "completeness": c, "complete": all(c[k] for k in ("front", "back", "booklet", "tray", "disc")),
                "missing": [{"booklet": "inside", "tray": "under-tray"}.get(k, k)
                            for k in ("front", "back", "booklet", "tray", "disc") if not c[k]], "notes": art.notes}

    def art_summary(self) -> dict:
        return {"albums": [self._art_row(a) for a in sorted(self.arts.values(), key=lambda a: a.folder.casefold())]}

    # ---- Library Health Check -----------------------------------------------------------------------------------
    # Missing songs, low quality, missing art and duplicates across the music folder (health.py); per album, a search
    # of the Internet Archive for what is missing, and the fixes: fetch tracks, save a cover, merge duplicates.
    def health_report(self) -> dict:
        lib = self.settings.library_path
        out = {"root": self.settings.library, "checked": self.health_index is not None, "running": self._health_job,
               "library_ok": bool(lib and lib.is_dir()), "labels": health.LABEL}
        if self.health_index is not None:
            out.update(self.health_index.to_dict())
            for a in out["albums"]:
                a["searched"] = a["folder"] in self.health_found
        return out

    def health_scan(self, musicbrainz: bool = False) -> dict:
        """Check every album in the background (one check at a time); progress and a "health" event follow."""
        root = self._library()
        with self._lock:
            if self._health_job:
                return {"job_id": self._health_job}

            def run(job: Job):
                try:
                    self.health_index = health.scan(root, mb_client=self.mb if musicbrainz else None,
                                              progress=self.jobs.progress(job), cancel=job.cancelled)
                    return self.health_index.counts()
                finally:
                    with self._lock:
                        self._health_job = None
                    self.broadcast({"type": "health"})

            label = "health check" + (" with MusicBrainz" if musicbrainz else "") + f": {root}"
            self._health_job = self.jobs.submit("health", label, run).id
            return {"job_id": self._health_job}

    def _health_entry(self, folder: str) -> health.AlbumHealth:
        entry = self.health_index.get(folder) if self.health_index is not None else None
        if entry is None:
            raise NotFound(f"no health check of {folder!r}: run the check first")
        return entry

    def _health_refresh(self, folder: str, gone: tuple = ()) -> None:
        """Check one album again after a fix (and forget any albums merged away), keeping the rest of the report."""
        if self.health_index is None:
            return
        root = self._library()
        self.health_index.albums = [a for a in self.health_index.albums if a.folder not in gone]
        try:
            fresh = health.check_one(root, folder, self.mb if self.health_index.musicbrainz else None)
        except health.HealthError:
            self.health_index.albums = [a for a in self.health_index.albums if a.folder != folder]
        else:
            self.health_index.albums = [fresh if a.folder == folder else a for a in self.health_index.albums]
        health.mark_duplicates(self.health_index)
        for f in (folder, *gone):
            self.health_found.pop(f, None)
        self.library = None                              # the music folder changed: read again on next use
        self.broadcast({"type": "health"})

    def health_album(self, folder: str) -> dict:
        entry = self._health_entry(folder)
        out = entry.to_dict(tracks=True)
        out["tracks"] = health.sorted_tracks(entry)
        out["cover"] = next((f"{folder}/{p.name}" for p in health.cover_files(self._library() / folder)), None)
        out["search"] = self.health_found.get(folder)
        return out

    def health_search(self, folder: str) -> dict:
        """Search the Internet Archive for what this album is missing (a job; its result is the search)."""
        entry = self._health_entry(folder)

        def run(job: Job):
            found = health.search_archive(self.client, entry, mb_client=self.mb, progress=self.jobs.progress(job),
                                          cancel=job.cancelled)
            for im in found["art"]:
                self.health_art[im["id"]] = cdart.ArtImage(**im)
            self.health_found[folder] = found
            return found

        return {"job_id": self.jobs.submit("health-search", f"archive.org for {entry.artist} / {entry.album}", run).id}

    def health_fetch(self, folder: str, identifier: str, files: list) -> dict:
        """Download chosen tracks of an upload into the album folder (checked, tagged, nothing overwritten)."""
        entry = self._health_entry(folder)
        found = self.health_found.get(folder) or {}
        media = next((s["media"] for s in found.get("suggestions", []) if s["identifier"] == identifier), None)
        c = self.cand(identifier, media=media)
        root = self._library()
        health.plan_fetch(root, entry, c.item, files)        # refuse at once, not in the background

        def run(job: Job):
            try:
                return health.fetch_tracks(self.client, root, entry, c.item, files, progress=self.jobs.progress(job),
                                           cancel=job.cancelled)
            finally:
                self._health_refresh(folder)

        return {"job_id": self.jobs.submit("health-fetch", f"add {len(files)} track(s) to {folder}", run).id}

    def health_image(self, image_id: str) -> tuple:
        im = self.health_art.get(image_id)
        if im is None:
            raise NotFound(f"no image {image_id}")
        try:
            data = cdart.ArtFetcher(self.mb, self.client).get(im)
        except (mb.MBError, IAError, OSError, ValueError) as exc:
            raise FetchError(f"could not fetch the image: {exc}") from exc
        return data, image_mime(data)

    def health_cover(self, folder: str, image_id: str, replace: bool = False) -> dict:
        """Save a found cover; with `replace`, in place of the album's own (which goes to the Trash)."""
        entry = self._health_entry(folder)
        data, _ = self.health_image(image_id)
        out = health.save_cover(self._library(), entry, data, replace=replace)
        self._health_refresh(folder)
        return out

    def health_replace(self, folder: str, identifier: str) -> dict:
        """Replace the album with a lossless upload found by the search (a job: download into staging, verify, swap)."""
        entry = self._health_entry(folder)
        found = self.health_found.get(folder) or {}
        media = next((s["media"] for s in found.get("suggestions", []) if s["identifier"] == identifier), None)
        if media not in ("flac", "wav"):
            raise ValueError("only a lossless upload found by this album's search can replace it")
        root = self._library()

        def run(job: Job):
            c = self.cand(identifier, media=media)
            if not c.probes and not c.alt_stats:
                self.pipeline(job).probe(c)
            try:
                return health.replace_album(self.client, root, entry, c, opts=self.save_options(), mb_client=self.mb,
                                            progress=self.jobs.progress(job), cancel=job.cancelled)
            finally:
                self._health_refresh(folder)

        return {"job_id": self.jobs.submit("health-replace", f"replace {folder} with {identifier}", run).id}

    def health_merge_plan(self, folders: list) -> dict:
        return health.plan_merge(self._library(), folders)

    def health_merge(self, folders: list, token: str) -> dict:
        root = self._library()

        def run(job: Job):
            result = health.apply_merge(root, folders, token, progress=self.jobs.progress(job))
            self._health_refresh(result["target"], gone=tuple(result["trashed_folders"]))
            return result

        return {"job_id": self.jobs.submit("health-merge", f"merge {len(folders)} folder(s)", run).id}

    # ---- audio -------------------------------------------------------------------------------------------------
    def remote_file(self, identifier: str, name: str):
        c = self.cand(identifier)
        f = next((f for f in c.item.files if f.name == name), None)
        if f is None or Path(f.name).suffix.lower() not in PLAYABLE:
            raise NotFound(f"{identifier} has no playable file {name!r}")
        return c, f

    def cache_track(self, identifier: str, name: str) -> dict:
        """Fetch a track into the audio cache in the background (the player calls this when a track starts)."""
        _, f = self.remote_file(identifier, name)
        hit = self.audio.cached(identifier, name)
        if hit:
            return {"cached": True}

        def run(job: Job):
            emit = self.jobs.progress(job)
            self.audio.ensure(identifier, f, progress=lambda got, size: emit({"stage": "cache", "bytes": got, "size": size,
                                                                              "identifier": identifier, "file": name}),
                              cancel=job.cancelled)
            self.broadcast({"type": "cached", "identifier": identifier, "file": name})
            return {"identifier": identifier, "file": name}

        return {"cached": False, "job_id": self.jobs.submit("cache", f"fetch {name}", run).id}

    def local_track(self, identifier: str, name: str) -> Path:
        """The cached copy of a remote track, fetching it now (blocking) if needed."""
        _, f = self.remote_file(identifier, name)
        return self.audio.ensure(identifier, f)
