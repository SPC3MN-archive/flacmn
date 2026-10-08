"""Background work for the GUI: one-off jobs (search, test, MusicBrainz lookup, library scan) and the download queue.

Every change is broadcast as an event dict, which the server forwards to the UI over a WebSocket:
    {"type": "job", "job": {...}}                          a job was queued, started, finished, failed or cancelled
    {"type": "progress", "job_id": ..., "stage": ...}      engine progress (events.py), throttled
    {"type": "queue", "item": {...}}                        a download queue entry changed
"""

from __future__ import annotations

import itertools
import json
import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..events import Cancelled

Broadcast = Callable[[dict], None]
PROGRESS_INTERVAL = 0.15            # seconds between progress events of one job (the last one always goes out)


@dataclass
class Job:
    id: str
    kind: str                        # search | test | deep | musicbrainz | library | genre | analyze
    label: str
    state: str = "queued"            # queued | running | done | failed | cancelled
    progress: dict = field(default_factory=dict)
    result: object = None
    error: str = ""
    created: float = field(default_factory=time.time)
    finished: float = 0.0
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def summary(self, with_result: bool = False) -> dict:
        d = {"id": self.id, "kind": self.kind, "label": self.label, "state": self.state, "progress": self.progress,
             "error": self.error, "created": self.created, "finished": self.finished}
        if with_result:
            d["result"] = self.result
        return d

    def cancelled(self) -> bool:
        return self._cancel.is_set()


class JobManager:
    def __init__(self, broadcast: Broadcast, workers: int = 4):
        self.broadcast = broadcast
        self.jobs: dict = {}
        self.pool = ThreadPoolExecutor(workers, thread_name_prefix="iaflac-job")
        self._last_progress: dict = {}

    def _send(self, job: Job) -> None:
        self.broadcast({"type": "job", "job": job.summary(with_result=job.state == "done")})

    def progress(self, job: Job) -> Callable[[dict], None]:
        def emit(event: dict) -> None:
            job.progress = event
            now = time.monotonic()
            final = event.get("state") in ("verified", "have") or event.get("stage") in ("found", "inspected", "decoded", "deep_done", "saved")
            if final or now - self._last_progress.get(job.id, 0) >= PROGRESS_INTERVAL:
                self._last_progress[job.id] = now
                self.broadcast({"type": "progress", "job_id": job.id, **event})
        return emit

    def submit(self, kind: str, label: str, fn: Callable[[Job], object]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label)
        self.jobs[job.id] = job
        self._send(job)

        def run():
            if job.cancelled():
                job.state = "cancelled"
            else:
                job.state = "running"
                self._send(job)
                try:
                    job.result = fn(job)
                    job.state = "cancelled" if job.cancelled() else "done"
                except Cancelled:
                    job.state = "cancelled"
                except Exception as exc:          # a job's failure is reported, never kills the server
                    job.state, job.error = "failed", f"{type(exc).__name__}: {exc}"
                    traceback.print_exc()
            job.finished = time.time()
            self._send(job)
            self._prune()

        self.pool.submit(run)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None or job.state in ("done", "failed", "cancelled"):
            return False
        job._cancel.set()
        return True

    def _prune(self, keep: int = 300) -> None:
        done = [j for j in self.jobs.values() if j.state in ("done", "failed", "cancelled")]
        for j in sorted(done, key=lambda j: j.finished)[:-keep] if len(done) > keep else []:
            self.jobs.pop(j.id, None)
            self._last_progress.pop(j.id, None)


# ---- the download queue ------------------------------------------------------------------------------------------

@dataclass
class QueueItem:
    id: str
    identifier: str
    media: str
    artist: str
    album: str
    folder: str
    meta: dict                       # tagging.AlbumMeta.to_dict()
    options: dict                    # save.SaveOptions fields
    total_bytes: int = 0
    state: str = "queued"            # queued | downloading | tagging | paused | failed | done | cancelled
    done_bytes: int = 0
    file: str = ""
    index: int = 0
    total: int = 0
    error: str = ""
    result: dict = field(default_factory=dict)
    added: float = field(default_factory=time.time)
    order: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class DownloadQueue:
    """Albums waiting to be saved, saved one (or `concurrency`) at a time. Pausing or cancelling stops the running
    download between chunks and keeps its .part files; resuming or retrying picks up where it stopped. The queue is
    kept on disk, so albums still waiting when the app closes start again next time."""

    def __init__(self, runner: Callable[[QueueItem, Callable[[dict], None], Callable[[], bool]], dict],
                 broadcast: Broadcast, store: Optional[Path] = None, concurrency: int = 1):
        self.runner = runner
        self.broadcast = broadcast
        self.store = store
        self.concurrency = max(1, concurrency)
        self.items: dict = {}
        self._flags: dict = {}       # id -> "pause" | "cancel"
        self._cond = threading.Condition()
        self._running: set = set()
        self._order = itertools.count(1)
        self._stop = False
        self._load()
        self._threads = [threading.Thread(target=self._worker, name=f"iaflac-download-{i}", daemon=True)
                         for i in range(self.concurrency)]
        for t in self._threads:
            t.start()

    # persistence
    def _load(self) -> None:
        if not self.store:
            return
        try:
            data = json.loads(self.store.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        fields_ = set(QueueItem.__dataclass_fields__)
        for raw in data.get("items", []):
            item = QueueItem(**{k: v for k, v in raw.items() if k in fields_})
            if item.state in ("downloading", "tagging"):
                item.state = "queued"                  # the app closed mid-download: resume it
            self.items[item.id] = item
        top = max((i.order for i in self.items.values()), default=0)
        self._order = itertools.count(top + 1)

    def _save(self) -> None:
        if not self.store:
            return
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.store.with_name(self.store.name + ".tmp")
            tmp.write_text(json.dumps({"items": [i.to_dict() for i in self.list()]}, indent=1), encoding="utf-8")
            os.replace(tmp, self.store)
        except OSError:
            pass

    def _changed(self, item: QueueItem, persist: bool = True) -> None:
        if persist:
            self._save()
        self.broadcast({"type": "queue", "item": item.to_dict()})

    # API
    def list(self) -> list:
        return sorted(self.items.values(), key=lambda i: i.order)

    def add(self, **values) -> QueueItem:
        with self._cond:
            same = next((i for i in self.items.values() if i.folder == values.get("folder")
                         and i.state in ("queued", "downloading", "tagging", "paused")), None)
            if same is not None:
                raise ValueError(f"already in the queue: {same.artist} / {same.album}")
            item = QueueItem(id=uuid.uuid4().hex[:12], order=next(self._order), **values)
            self.items[item.id] = item
            self._changed(item)
            self._cond.notify_all()
            return item

    def _set_flag(self, item_id: str, flag: str, idle_state: str) -> QueueItem:
        with self._cond:
            item = self.items[item_id]
            if item_id in self._running:
                self._flags[item_id] = flag                # the worker stops at the next chunk and sets the state
            elif item.state in ("queued", "paused", "failed"):
                item.state = idle_state
                self._changed(item)
            return item

    def pause(self, item_id: str) -> QueueItem:
        return self._set_flag(item_id, "pause", "paused")

    def cancel(self, item_id: str) -> QueueItem:
        return self._set_flag(item_id, "cancel", "cancelled")

    def resume(self, item_id: str) -> QueueItem:
        with self._cond:
            item = self.items[item_id]
            if item.state in ("paused", "failed", "cancelled"):
                item.state, item.error = "queued", ""
                self._changed(item)
                self._cond.notify_all()
            return item

    retry = resume

    def remove(self, item_id: str) -> None:
        with self._cond:
            if item_id in self._running:
                raise ValueError("pause or cancel it first")
            item = self.items.pop(item_id)
            self._save()
            self.broadcast({"type": "queue", "item": {**item.to_dict(), "state": "removed"}})

    def clear_finished(self) -> int:
        with self._cond:
            gone = [i for i in self.items.values() if i.state in ("done", "cancelled")]
            for i in gone:
                self.items.pop(i.id)
            self._save()
            for i in gone:
                self.broadcast({"type": "queue", "item": {**i.to_dict(), "state": "removed"}})
            return len(gone)

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            for i in self._running:
                self._flags[i] = "shutdown"           # stopped by closing the app: starts again next time
            self._cond.notify_all()

    # worker
    def _next(self) -> Optional[QueueItem]:
        waiting = [i for i in self.list() if i.state == "queued" and i.id not in self._running]
        return waiting[0] if waiting else None

    def _worker(self) -> None:
        while True:
            with self._cond:
                while not self._stop and self._next() is None:
                    self._cond.wait(timeout=1.0)
                if self._stop:
                    return
                item = self._next()
                self._running.add(item.id)
                item.state, item.error = "downloading", ""
                self._changed(item)
            last = [0.0]

            def progress(event: dict, item=item) -> None:
                if event.get("stage") == "download":
                    item.done_bytes = event.get("done_bytes", item.done_bytes)
                    item.total_bytes = event.get("total_bytes", item.total_bytes)
                    item.file, item.index, item.total = event.get("file", ""), event.get("index", 0), event.get("total", 0)
                elif event.get("stage") in ("tagging", "cover"):
                    item.state = "tagging"
                now = time.monotonic()
                if now - last[0] >= PROGRESS_INTERVAL or event.get("state") == "verified" or event.get("stage") != "download":
                    last[0] = now
                    self._changed(item, persist=False)

            def cancelled(item=item) -> bool:
                return item.id in self._flags

            try:
                item.result = self.runner(item, progress, cancelled) or {}
                item.state = "done" if item.result.get("ok") else "failed"
                if not item.result.get("ok"):
                    item.error = "; ".join(item.result.get("failures", [])[:3]) or "the download did not complete"
            except Cancelled:
                item.state = {"cancel": "cancelled", "shutdown": "queued"}.get(self._flags.get(item.id), "paused")
            except Exception as exc:
                item.state, item.error = "failed", f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
            with self._cond:
                self._running.discard(item.id)
                self._flags.pop(item.id, None)
                self._changed(item)
                self._cond.notify_all()
