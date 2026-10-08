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

from .. import __version__, binaries, genre as genre_mod, musicbrainz as mb, paths, quality, queries, save, tagging
from .. import library_index
from ..ia_client import IAClient, IAError
from ..model import track_files
from ..pipeline import Candidate, Options, Pipeline
from ..queries import Want
from ..tagging import AlbumMeta
from . import audio, serialize
from .jobs import DownloadQueue, Job, JobManager, QueueItem
from .settings import Settings


PLAYABLE = {".flac", ".mp3", ".wav", ".ogg", ".m4a", ".opus"}     # any of the item's audio, originals or IA's derivatives


class NotFound(LookupError):
    pass


@dataclass
class SearchSession:
    id: str
    kind: str                         # "album" | "genre"
    want: Optional[Want] = None
    spec: object = None               # GenreSpec
    result: object = None             # WantResult | GenreResult
    identifiers: list = field(default_factory=list)
    created: float = field(default_factory=time.time)
    done: bool = False


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
        store = queue_store if queue_store is not None else paths.data_dir() / ".gui" / "queue.json"
        self.queue = DownloadQueue(self._run_save, self.broadcast, store=store,
                                   concurrency=self.settings.download_concurrency)

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
                    max_candidates=s.max_candidates, max_deep_bytes=s.max_deep_mb * 2**20,
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
        sess = SearchSession(id=uuid.uuid4().hex[:12], kind="album", want=want)
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
                ranked = self._genre_ranked(sess) if sess.kind == "genre" else self._ranked(sess)
                self.broadcast({"type": "results", "search_id": sess.id, "results": ranked})
            return {"tested": [c.identifier for c in cands]}

        return self.jobs.submit("test", f"quick test of {len(cands)} result(s)", run)

    def results(self, search_id: str) -> dict:
        sess = self.searches.get(search_id)
        if sess is None:
            raise NotFound(f"no search {search_id}")
        res = sess.result
        return {"search_id": sess.id, "kind": sess.kind, "done": sess.done,
                "want": {"artist": sess.want.artist, "album": sess.want.album} if sess.want else None,
                "results": self._ranked(sess) if sess.kind == "album" else self._genre_ranked(sess),
                "notes": list(getattr(res, "notes", []) or []),
                "near_misses": list(getattr(res, "near_misses", []) or []),
                "lossless_other": list(getattr(res, "lossless_other", []) or []),
                "excluded": dict(getattr(res, "excluded", {}) or {})}

    def test(self, identifier: str, level: str = "quick") -> dict:
        """level: quick (headers + rip logs, V1) | deep (full decode V2 + spectrum V3)."""
        c = self.cand(identifier)
        want = self.wants.get(identifier)
        if level == "quick":
            return {"job_id": self._quick_test([c], want, None).id}
        if level != "deep":
            raise ValueError("level must be quick or deep")

        def run(job: Job):
            pipe = self.pipeline(job)
            if c.tier == "?":
                pipe.inspect(c, want)
            pipe.deep_verify(c, self.options(), integrity_check=True, spectral_check=True)
            summary = serialize.candidate_summary(c, want, self.library)
            self.broadcast({"type": "candidate", "candidate": summary})
            return summary

        return {"job_id": self.jobs.submit("deep", f"deep test: {c.item.title or identifier}", run).id}

    def spectrogram_file(self, identifier: str) -> Path:
        c = self.cand(identifier)
        if not c.spectral or not c.spectral.png or not Path(c.spectral.png).exists():
            raise NotFound("no spectrogram yet: run the deep test")
        return Path(c.spectral.png)

    # ---- genre -------------------------------------------------------------------------------------------------
    def genres(self) -> list:
        return sorted(genre_mod.load_genres().keys())

    def genre_search(self, name: str, years: str = "", terms: str = "", exclude: str = "",
                     cd_only: bool = True, top: int = 25) -> dict:
        spec_kw = dict(extra_terms=genre_mod.split_csv(terms), extra_exclude=genre_mod.split_csv(exclude))
        terms_, excl = genre_mod.resolve(name, **spec_kw)
        spec = genre_mod.GenreSpec(genre=name, terms=terms_, exclude=excl,
                                   years=queries.parse_years(years) if years else None, cd_only=cd_only, top=top,
                                   include_live=self.settings.include_live, free_only=self.settings.free_only)
        sess = SearchSession(id=uuid.uuid4().hex[:12], kind="genre", spec=spec)
        self.searches[sess.id] = sess
        self._ensure_library()

        def run(job: Job):
            pipe = self.pipeline(job)
            res = pipe.genre_search(spec, self.options())
            sess.result, sess.identifiers, sess.done = res, [c.identifier for c in res.found], True
            self._remember(res.found, None)
            self.broadcast({"type": "results", "search_id": sess.id, "results": self._genre_ranked(sess),
                            "notes": res.notes, "excluded": res.excluded})
            top = res.found[: self.settings.auto_test_top]
            if top:
                self._quick_test(top, None, sess)
            return {"search_id": sess.id, "count": len(res.found)}

        job = self.jobs.submit("genre", f"genre: {name}", run)
        return {"search_id": sess.id, "job_id": job.id}

    def _genre_ranked(self, sess: SearchSession) -> list:
        with self._lock:
            cands = [self.cands[i] for i in sess.identifiers if i in self.cands]
        return [serialize.candidate_summary(c, None, self.library) for c in sorted(cands, key=Pipeline.genre_key)]

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
        for f in ("albumartist", "album", "date", "genre"):
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
        return lib

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
        root = self._library()

        def run(job: Job):
            self.library = library_index.scan(root, progress=self.jobs.progress(job), cancel=job.cancelled)
            self.broadcast({"type": "library", "count": len(self.library.albums)})
            return {"albums": len(self.library.albums)}

        return {"job_id": self.jobs.submit("library", f"scan {root}", run).id}

    def library_albums(self) -> dict:
        if self.library is None:
            self._ensure_library()
        if self.library is None:
            return {"root": self.settings.library, "albums": [], "scanned": False}
        return {**self.library.to_dict(), "scanned": True}

    def library_album(self, folder: str) -> dict:
        root = self._library()
        path = (root / folder).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_dir():
            raise NotFound(folder)
        files = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in library_index.AUDIO)
        tracks = []
        for p in files:
            t = tagging.read_track(p) if p.suffix.lower() in (".flac", ".mp3") else {"format": p.suffix[1:].upper()}
            t["path"] = p.relative_to(root).as_posix()
            tracks.append(t)
        return {"folder": folder, "tracks": tracks, "issues": tagging.validate(path),
                "cover": next((p.relative_to(root).as_posix() for p in path.iterdir() if p.name.lower() in tagging.COVER_NAMES), None)}

    def library_file(self, rel: str) -> Path:
        root = self._library()
        path = root / rel
        if not audio.inside(path, [root]):
            raise NotFound(rel)
        return path.resolve()

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
