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
from .. import cdart, jewelcase, library_index
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
        self.arts: dict = {}                  # library folder -> cdart.ArtSet
        self.art_selections: dict = {}        # library folder -> {front, back, disc, tray, booklet: [...]}
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
        files = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in library_index.AUDIO and not paths.is_hidden(p, path))
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

    # ---- CD artwork: find the scans, choose, print, keep -------------------------------------------------------
    def _album_dir(self, folder: str) -> Path:
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
                                 search_archive=self.settings.art_search_archive, library_root=self._library(),
                                 progress=self.jobs.progress(job), cancel=job.cancelled)
            self.arts[folder] = art
            self.art_selections[folder] = cdart.default_selection(art)
            out = self.art_state(folder)
            self.broadcast({"type": "art", "folder": folder, "completeness": out["completeness"]})
            return out

        return {"job_id": self.jobs.submit("art", f"artwork: {folder}", run).id}

    def art_state(self, folder: str) -> dict:
        art = self._art(folder)
        return {**art.to_dict(), "selection": self.art_selections.get(folder) or cdart.default_selection(art),
                "pdf": str(self._pdf_path(art)) if self._pdf_path(art).exists() else None}

    def art_select(self, folder: str, selection: dict) -> dict:
        art = self._art(folder)
        clean = {}
        for part in cdart.PARTS:
            iid = selection.get(part)
            if iid is not None and art.get(iid) is None:
                raise ValueError(f"{part}: unknown image {iid}")
            clean[part] = iid
        self.art_selections[folder] = clean
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
                except (mb.MBError, IAError, OSError) as exc:
                    raise audio.AudioError(f"could not fetch the image: {exc}") from exc
            mime = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
            return data, mime
        raise NotFound(f"no image {image_id}")

    def _pdf_path(self, art: cdart.ArtSet) -> Path:
        name = folder_component(f"{art.artist} - {art.album}" + (f" ({art.year})" if art.year else "")) + " - CD case.pdf"
        return self.settings.print_path / name

    def art_print(self, folder: str, discs: int = 0) -> dict:
        art = self._art(folder)
        sel = dict(self.art_selections.get(folder) or cdart.default_selection(art))
        s = self.settings
        opts = jewelcase.PrintOptions(paper=s.paper, bleed=s.bleed_mm, disc_inner=s.disc_hole_mm,
                                      discs=discs or max([t["disc"] for t in art.tracks] or [1]))

        def run(job: Job):
            fetcher = cdart.ArtFetcher(self.mb, self.client)
            emit = self.jobs.progress(job)
            wanted = [k for k in cdart.PARTS if sel.get(k)]

            def load(part):
                im = art.get(sel[part])
                emit({"stage": "art", "step": "fetching", "part": part, "label": im.label,
                      "index": wanted.index(part) + 1, "total": len(wanted)})
                return jewelcase.Art(fetcher.get(im, cancel=job.cancelled), im.label)

            parts = {k: load(k) if sel.get(k) else None for k in cdart.PARTS}
            out = self._pdf_path(art)
            out.parent.mkdir(parents=True, exist_ok=True)
            emit({"stage": "art", "step": "rendering"})
            report = jewelcase.render(out, artist=art.artist, album=art.album, year=art.year, tracks=art.tracks,
                                      opts=opts, **parts)
            result = {"pdf": str(out), **report.to_dict()}
            self.broadcast({"type": "art_printed", "folder": folder, "pdf": str(out)})
            return result

        return {"job_id": self.jobs.submit("print", f"print sheet: {art.artist} / {art.album}", run).id}

    def art_pdf(self, folder: str) -> Path:
        path = self._pdf_path(self._art(folder))
        if not path.exists():
            raise NotFound("no print sheet yet: make one first")
        return path

    def art_open(self, folder: str) -> dict:
        """Open the print PDF in the system's PDF viewer (to print it from there)."""
        path = self.art_pdf(folder)
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
