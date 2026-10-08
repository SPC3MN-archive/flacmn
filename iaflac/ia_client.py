"""Polite archive.org client: search, item metadata and ranged file reads, with a disk cache.

Everything goes through this one class so the User-Agent, concurrency cap, backoff and
caching rules apply uniformly. It only ever issues GET requests against public endpoints.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Iterator, Optional
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter

from . import __version__, paths

BASE = "https://archive.org"
USER_AGENT = f"iaflac/{__version__} (alecsang@yorku.ca)"
RETRY_STATUS = {429, 500, 502, 503, 504}
HEADER_CACHE_MAX = 512 * 1024  # ranged reads up to this size are cached (headers, rip logs); spectral slices are not
RANGE_TTL = 7 * 24 * 3600

SEARCH_FIELDS = [
    "identifier", "title", "creator", "year", "date", "downloads", "item_size",
    "files_count", "collection", "subject", "licenseurl", "venue", "taper",
]


class IAError(RuntimeError):
    pass


def default_cache_dir() -> Path:
    return paths.cache_dir()


class IAClient:
    def __init__(self, cache_dir=None, max_concurrency: int = 4, min_interval: float = 0.05,
                 user_agent: str = USER_AGENT, ttl_hours: float = 24.0, session=None):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent
        # every item sits on its own data-node host; the default pool remembers only 10 of them
        adapter = HTTPAdapter(pool_connections=64, pool_maxsize=max(8, max_concurrency * 3))
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.max_concurrency = max_concurrency
        self.min_interval = min_interval
        self.ttl = ttl_hours * 3600
        self._slots = threading.BoundedSemaphore(max_concurrency)
        self._rate_lock = threading.Lock()
        self._last_start = 0.0
        self.requests_made = 0
        self.cache_hits = 0
        self.bytes_read = 0
        self.timings: dict = {}      # kind -> [seconds]; where the waiting went
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.cache_dir = Path(tempfile.mkdtemp(prefix="iaflac-cache-"))

    # ---- plumbing -------------------------------------------------------------------

    def _throttle(self) -> None:
        with self._rate_lock:
            wait = self._last_start + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_start = time.monotonic()

    def _request(self, url: str, *, params=None, headers=None, stream: bool = False,
                 ok=(200,), retries: int = 5, timeout=(10, 60), kind: str = "other") -> requests.Response:
        delay, last_err = 1.0, "no attempt made"
        for attempt in range(retries + 1):
            self._throttle()
            started = time.monotonic()
            try:
                with self._slots:
                    resp = self.session.get(url, params=params, headers=headers,
                                            stream=stream, timeout=timeout)
                self.timings.setdefault(kind, []).append(time.monotonic() - started)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_err = f"{type(exc).__name__}: {exc}"
            else:
                self.requests_made += 1
                if resp.status_code in ok:
                    return resp
                status, retry_after = resp.status_code, resp.headers.get("Retry-After", "")
                resp.close()
                if status not in RETRY_STATUS:
                    raise IAError(f"HTTP {status} for {url}")
                last_err = f"HTTP {status} for {url}"
                if retry_after.isdigit():
                    delay = max(delay, float(retry_after))
            if attempt < retries:
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
        raise IAError(f"giving up after {retries + 1} attempts: {last_err}")

    def _cache_path(self, kind: str, key: str, ext: str) -> Path:
        digest = hashlib.sha1(f"{kind}\0{key}".encode()).hexdigest()
        return self.cache_dir / kind / digest[:2] / f"{digest}.{ext}"

    def _cached(self, kind: str, key: str, ttl: Optional[float], produce: Callable[[], bytes],
                ext: str = "json", store: bool = True) -> bytes:
        path = self._cache_path(kind, key, ext)
        try:
            if ttl is None or time.time() - path.stat().st_mtime <= ttl:
                data = path.read_bytes()
                self.cache_hits += 1
                return data
        except OSError:
            pass
        data = produce()
        if store:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
                tmp.write_bytes(data)
                os.replace(tmp, path)
            except OSError:
                pass
        return data

    # ---- search ---------------------------------------------------------------------

    def search(self, q: str, fields=None, rows: int = 100, page: int = 1,
               sort: Optional[str] = "downloads desc") -> dict:
        """One page of advancedsearch results: {"numFound", "start", "docs"}."""
        params = {"q": q, "fl[]": list(fields or SEARCH_FIELDS), "rows": rows, "page": page,
                  "output": "json"}
        if sort:
            params["sort[]"] = sort

        def produce() -> bytes:
            resp = self._request(f"{BASE}/advancedsearch.php", params=params, kind="search")
            try:
                data = resp.json()
            except ValueError as exc:
                raise IAError(f"search returned non-JSON for {q!r}") from exc
            if "response" not in data:
                raise IAError(f"search error for {q!r}: {data.get('error', data)}")
            return resp.content

        raw = self._cached("search", json.dumps(params, sort_keys=True), self.ttl, produce)
        return json.loads(raw)["response"]

    def search_iter(self, q: str, *, fields=None, sort: Optional[str] = "downloads desc",
                    max_hits: int = 100, page_size: int = 100) -> Iterator[dict]:
        page, seen = 1, 0
        while seen < max_hits:
            resp = self.search(q, fields, rows=page_size, page=page, sort=sort)
            docs = resp.get("docs", [])
            for doc in docs:
                yield doc
                seen += 1
                if seen >= max_hits:
                    return
            if not docs or page * page_size >= resp.get("numFound", 0):
                return
            page += 1

    def count(self, q: str) -> int:
        return int(self.search(q, fields=["identifier"], rows=0, sort=None).get("numFound", 0))

    # ---- items and files ------------------------------------------------------------

    def metadata(self, identifier: str) -> dict:
        def produce() -> bytes:
            resp = self._request(f"{BASE}/metadata/{quote(identifier, safe='')}", kind="metadata")
            data = resp.json()
            if not isinstance(data, dict) or "metadata" not in data:
                raise IAError(f"no metadata for {identifier!r} (missing, dark or empty item)")
            return resp.content

        return json.loads(self._cached("meta", identifier, self.ttl, produce))

    @staticmethod
    def file_url(identifier: str, name: str) -> str:
        return f"{BASE}/download/{quote(identifier, safe='')}/{quote(name, safe='/')}"

    def range_get(self, identifier: str, name: str, start: int, length: int) -> bytes:
        """Read `length` bytes at `start` using an HTTP Range request (b"" past end of file)."""
        if length <= 0:
            return b""
        url = self.file_url(identifier, name)

        def produce() -> bytes:
            resp = self._request(
                url, headers={"Range": f"bytes={start}-{start + length - 1}",
                              "Accept-Encoding": "identity"},
                stream=True, ok=(200, 206, 416), kind="range")
            try:
                if resp.status_code == 416:
                    return b""
                if resp.status_code == 200 and start > 0:
                    raise IAError(f"server ignored the Range header for {url}")
                bounded = resp.status_code == 206     # the server ends the body at `length`
                buf = bytearray()
                for chunk in resp.iter_content(64 * 1024):
                    buf += chunk
                    if len(buf) >= length and not bounded:
                        break                          # a 200 would keep sending the whole file
                return bytes(buf[:length])
            finally:
                resp.close()                            # after a fully read 206 this returns the connection to the pool

        data = self._cached("range", f"{url}|{start}|{length}", RANGE_TTL, produce, ext="bin",
                            store=length <= HEADER_CACHE_MAX)
        self.bytes_read += len(data)
        return data

    def get_bytes(self, identifier: str, name: str, max_bytes: int = 1 << 20) -> bytes:
        """Small whole files (rip logs, cue sheets); refuses anything bigger than max_bytes."""
        return self.range_get(identifier, name, 0, max_bytes)

    def open_stream(self, identifier: str, name: str, start: int = 0) -> requests.Response:
        """Streaming GET of a whole file (or from `start`). Caller must close the response."""
        headers = {"Accept-Encoding": "identity"}
        if start:
            headers["Range"] = f"bytes={start}-"
        return self._request(self.file_url(identifier, name), headers=headers, stream=True,
                             ok=(200, 206), timeout=(10, 120), kind="stream")

    def stats(self) -> str:
        waits = ", ".join(f"{kind} {len(v)} x {sum(v) / len(v):.2f}s" for kind, v in sorted(self.timings.items()) if v)
        return (f"{self.requests_made} requests ({waits}), {self.cache_hits} cache hits, "
                f"{self.bytes_read / 1e6:.1f} MB read")
