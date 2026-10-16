"""HTTP API and static files for the GUI, on 127.0.0.1 only.

Every /api route needs the per-launch token: the window opens `/?token=...`, which sets an HttpOnly cookie, so the
page's own fetches, <audio> elements and the event WebSocket carry it automatically. Requests whose Host header is
not this server (DNS rebinding) are refused. Interactive API docs: /docs.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import requests
from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..ia_client import IAClient
from . import audio
from ..health import HealthError
from ..retag import RetagError
from .service import App, NotFound

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "iaflac_token"
MIME = {".flac": "audio/flac", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".ogg": "audio/ogg",
        ".opus": "audio/ogg", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def create_app(core: Optional[App] = None, token: Optional[str] = None, allowed_hosts: tuple = ("127.0.0.1", "localhost")) -> FastAPI:
    core = core or App()
    token = token or secrets.token_urlsafe(24)
    @asynccontextmanager
    async def lifespan(_app):
        yield
        core.shutdown()

    api = FastAPI(title="iaflac", version=__version__, lifespan=lifespan)
    api.state.core, api.state.token = core, token

    # ---- guard -----------------------------------------------------------------------------------------------
    def authorised(request) -> bool:
        given = request.headers.get("x-iaflac-token") or request.cookies.get(COOKIE) or request.query_params.get("token")
        return bool(given) and secrets.compare_digest(given, token)

    def host_ok(request) -> bool:
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        return host in allowed_hosts or host == "testserver"

    @api.middleware("http")
    async def guard(request: Request, call_next):
        if not host_ok(request):
            return JSONResponse({"error": "bad host"}, status_code=403)
        if request.url.path.startswith("/api/") and not authorised(request):
            return JSONResponse({"error": "missing or wrong token"}, status_code=401)
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            # the app window keeps its cache between launches: revalidate so an update shows at once
            response.headers["Cache-Control"] = "no-cache"
        return response

    @api.exception_handler(NotFound)
    async def not_found(_request, exc):
        return JSONResponse({"error": str(exc)}, status_code=404)

    @api.exception_handler(ValueError)
    async def bad_value(_request, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @api.exception_handler(HealthError)
    @api.exception_handler(RetagError)
    async def health_error(_request, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @api.exception_handler(audio.AudioError)
    async def audio_error(_request, exc):
        return JSONResponse({"error": str(exc)}, status_code=502)

    # ---- page ------------------------------------------------------------------------------------------------
    @api.get("/", include_in_schema=False)
    def index(request: Request, token_q: str = Query("", alias="token")):
        page = STATIC / "index.html"
        resp = FileResponse(page) if page.exists() else HTMLResponse("<h1>iaflac</h1><p>UI not built yet.</p>")
        if token_q and secrets.compare_digest(token_q, token):
            resp.set_cookie(COOKIE, token, httponly=True, samesite="strict")
        return resp

    if STATIC.is_dir():
        api.mount("/static", StaticFiles(directory=STATIC), name="static")

    # ---- status and settings ---------------------------------------------------------------------------------
    @api.get("/api/health")
    def health():
        return core.health()

    @api.get("/api/settings")
    def get_settings():
        return core.settings.to_dict()

    @api.put("/api/settings")
    def put_settings(values: dict = Body(...)):
        return core.update_settings(values)

    # ---- jobs ------------------------------------------------------------------------------------------------
    @api.get("/api/jobs")
    def jobs():
        return [j.summary() for j in sorted(core.jobs.jobs.values(), key=lambda j: -j.created)[:100]]

    @api.get("/api/jobs/{job_id}")
    def job(job_id: str):
        j = core.jobs.get(job_id)
        if j is None:
            raise NotFound(job_id)
        return j.summary(with_result=True)

    @api.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        return {"cancelled": core.jobs.cancel(job_id)}

    # ---- search and results ----------------------------------------------------------------------------------
    @api.post("/api/search")
    def search(body: dict = Body(...)):
        return core.search(body.get("query", ""), body.get("artist", ""), body.get("album", ""))

    @api.get("/api/search/{search_id}")
    def results(search_id: str):
        return core.results(search_id)

    @api.post("/api/search/{search_id}/test")
    def test_results(search_id: str, body: dict = Body(default={})):
        return core.test_results(search_id, body.get("identifiers"))

    @api.get("/api/items/{identifier}")
    def item(identifier: str):
        return core.detail(identifier)

    @api.post("/api/items/{identifier}/test")
    def test(identifier: str, body: dict = Body(default={})):
        return core.test(identifier, body.get("level", "quick"))

    # ---- metadata and saving ---------------------------------------------------------------------------------
    @api.get("/api/items/{identifier}/meta")
    def get_meta(identifier: str, fresh: bool = False):
        return core.meta(identifier, fresh)

    @api.put("/api/items/{identifier}/meta")
    def put_meta(identifier: str, body: dict = Body(...)):
        return core.set_meta(identifier, body)

    @api.post("/api/items/{identifier}/musicbrainz")
    def musicbrainz(identifier: str, body: dict = Body(default={})):
        return core.musicbrainz(identifier, body.get("artist", ""), body.get("album", ""))

    @api.post("/api/items/{identifier}/musicbrainz/{release_id}")
    def apply_release(identifier: str, release_id: str):
        return core.apply_release(identifier, release_id)

    @api.get("/api/items/{identifier}/plan")
    def plan(identifier: str):
        return core.plan(identifier)

    @api.post("/api/queue")
    def enqueue(body: dict = Body(...)):
        return core.enqueue(body["identifier"])

    @api.get("/api/queue")
    def queue():
        return [i.to_dict() for i in core.queue.list()]

    @api.post("/api/queue/{item_id}/{action}")
    def queue_action(item_id: str, action: str):
        actions = {"pause": core.queue.pause, "resume": core.queue.resume, "retry": core.queue.retry,
                   "cancel": core.queue.cancel}
        if action not in actions:
            raise ValueError(f"unknown action {action}")
        if item_id not in core.queue.items:
            raise NotFound(item_id)
        return actions[action](item_id).to_dict()

    @api.delete("/api/queue/{item_id}")
    def queue_remove(item_id: str):
        if item_id not in core.queue.items:
            raise NotFound(item_id)
        core.queue.remove(item_id)
        return {"removed": item_id}

    @api.post("/api/queue/clear")
    def queue_clear():
        return {"removed": core.queue.clear_finished()}

    # ---- library ---------------------------------------------------------------------------------------------
    @api.get("/api/library")
    def library():
        return core.library_albums()

    @api.post("/api/library/scan")
    def library_scan():
        return core.library_scan()

    @api.get("/api/library/album")
    def library_album(folder: str):
        return core.library_album(folder)
    @api.get("/api/library/tags")
    def album_tags(folder: str):
        return core.album_tags(folder)

    @api.post("/api/library/tags/musicbrainz")
    def album_tags_musicbrainz(body: dict = Body(...)):
        return core.album_tags_musicbrainz(body["folder"], body.get("artist", ""), body.get("album", ""))

    @api.post("/api/library/tags/preview")
    def album_tags_preview(body: dict = Body(...)):
        return core.album_tags_preview(body["folder"], body["meta"], body.get("release_id", ""))

    @api.put("/api/library/tags")
    def album_tags_save(body: dict = Body(...)):
        return core.album_tags_save(body["folder"], body["meta"], bool(body.get("rename")))

    @api.post("/api/library/open")
    def library_open(body: dict = Body(...)):
        return core.open_album(body["path"])

    @api.get("/api/library/file")
    def library_file(path: str):
        p = core.library_file(path)
        # revalidated each time (a 304 when unchanged), so a cover replaced under the same name shows at once
        return FileResponse(p, media_type=MIME.get(p.suffix.lower(), "application/octet-stream"),
                            headers={"Cache-Control": "no-cache"})

    # ---- Library Health Check (/api/health is the app's own status) ------------------------------------------
    @api.get("/api/health-check")
    def health_report():
        return core.health_report()

    @api.post("/api/health-check/scan")
    def health_scan(body: dict = Body(default={})):
        return core.health_scan(bool(body.get("musicbrainz")))

    @api.get("/api/health-check/album")
    def health_album(folder: str):
        return core.health_album(folder)

    @api.post("/api/health-check/search")
    def health_search(body: dict = Body(...)):
        return core.health_search(body["folder"])

    @api.post("/api/health-check/fetch")
    def health_fetch(body: dict = Body(...)):
        return core.health_fetch(body["folder"], body["identifier"], list(body.get("files") or []))

    @api.get("/api/health-check/image/{image_id}")
    def health_image(image_id: str):
        data, mime = core.health_image(image_id)
        return Response(data, media_type=mime, headers={"Cache-Control": "max-age=86400"})

    @api.post("/api/health-check/cover")
    def health_cover(body: dict = Body(...)):
        return core.health_cover(body["folder"], body["image_id"], bool(body.get("replace")))

    @api.post("/api/health-check/replace")
    def health_replace(body: dict = Body(...)):
        return core.health_replace(body["folder"], body["identifier"])

    @api.post("/api/health-check/merge-plan")
    def health_merge_plan(body: dict = Body(...)):
        return core.health_merge_plan(list(body.get("folders") or []))

    @api.post("/api/health-check/merge")
    def health_merge(body: dict = Body(...)):
        return core.health_merge(list(body.get("folders") or []), body.get("token", ""))

    # ---- CD artwork --------------------------------------------------------------------------------------------
    @api.post("/api/art/find")
    def art_find(body: dict = Body(...)):
        return core.art_find(body["folder"], body.get("release_id", ""))

    @api.get("/api/art")
    def art_state(folder: str):
        return core.art_state(folder)

    @api.put("/api/art/selection")
    def art_select(body: dict = Body(...)):
        return core.art_select(body["folder"], body.get("selection") or {})

    @api.put("/api/art/style")
    def art_style(body: dict = Body(...)):
        return core.art_style(body["folder"], body["part"], body.get("layout"), body.get("logo"), body.get("ink"))

    @api.post("/api/art/upload")
    def art_upload(body: dict = Body(...)):
        """{folder, part, name, data}: data is the image file, base64-encoded."""
        import base64
        import binascii
        try:
            data = base64.b64decode(body.get("data") or "", validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("the upload is not valid base64") from None
        return core.art_upload(body["folder"], body["part"], data, body.get("name", ""))

    @api.get("/api/art/image/{image_id}")
    def art_image(image_id: str, full: bool = False):
        data, mime = core.art_image(image_id, preview=not full)
        return Response(data, media_type=mime, headers={"Cache-Control": "max-age=86400"})

    @api.post("/api/art/print")
    def art_print(body: dict = Body(...)):
        return core.art_print(body["folder"], int(body.get("discs", 0)), body.get("format", "cd"))

    @api.post("/api/art/preview3d")
    def art_preview3d(body: dict = Body(...)):
        return core.art_preview3d(body["folder"], body.get("format", "cd"))

    @api.get("/api/art/texture")
    def art_texture(folder: str, part: str, format: str = "cd"):
        return Response(core.art_texture(folder, part, format), media_type="image/png", headers={"Cache-Control": "no-cache"})

    @api.get("/api/art/pdf")
    def art_pdf(folder: str, format: str = "cd"):
        p = core.art_pdf(folder, format)
        return FileResponse(p, media_type="application/pdf", filename=p.name, content_disposition_type="inline")

    @api.post("/api/art/open")
    def art_open(body: dict = Body(...)):
        return core.art_open(body["folder"], body.get("format", "cd"))

    @api.post("/api/art/save")
    def art_save(body: dict = Body(...)):
        return core.art_save(body["folder"])

    @api.post("/api/art/scan-all")
    def art_scan_all():
        return core.art_scan_all()

    @api.get("/api/art/summary")
    def art_summary():
        return core.art_summary()

    # ---- audio: playback, waveform, spectrogram, analysis ---------------------------------------------------
    def local_path(identifier: str, file: str, path: str) -> Path:
        if path:
            return core.library_file(path)
        if identifier and file:
            return core.local_track(identifier, file)
        raise ValueError("give identifier and file, or a library path")

    @api.get("/api/audio")
    def play(request: Request, identifier: str = "", file: str = "", path: str = "", wav: bool = False):
        if path or (identifier and file and core.audio.cached(identifier, file)):
            p = core.library_file(path) if path else core.audio.cached(identifier, file)
            if wav:
                return StreamingResponse(audio.transcode_wav(p), media_type="audio/wav")
            return FileResponse(p, media_type=MIME.get(p.suffix.lower(), "application/octet-stream"))
        if not (identifier and file):
            raise ValueError("give identifier and file, or a library path")
        _, f = core.remote_file(identifier, file)
        if wav:
            return StreamingResponse(audio.transcode_wav(core.local_track(identifier, file)), media_type="audio/wav")
        return proxy(request, identifier, f)

    def proxy(request: Request, identifier: str, f):
        """Forward the player's Range request to archive.org and stream the answer back; start caching the track."""
        rng = request.headers.get("range", "")
        if not rng or rng.startswith("bytes=0-"):
            core.cache_track(identifier, f.name)
        headers = {"Accept-Encoding": "identity", "User-Agent": core.client.session.headers.get("User-Agent", "")}
        if rng:
            headers["Range"] = rng
        try:
            upstream = core.client.session.get(IAClient.file_url(identifier, f.name), headers=headers, stream=True,
                                               timeout=(10, 120))
        except requests.RequestException as exc:
            raise audio.AudioError(f"archive.org did not answer: {exc}") from exc
        if upstream.status_code not in (200, 206):
            upstream.close()
            raise audio.AudioError(f"archive.org answered HTTP {upstream.status_code}")
        out = {"Accept-Ranges": "bytes"}
        for h in ("Content-Length", "Content-Range"):
            if h in upstream.headers:
                out[h] = upstream.headers[h]

        def body():
            try:
                yield from upstream.iter_content(256 * 1024)
            finally:
                upstream.close()

        return StreamingResponse(body(), status_code=upstream.status_code, headers=out,
                                 media_type=MIME.get(Path(f.name).suffix.lower(), "application/octet-stream"))

    @api.post("/api/audio/cache")
    def cache(body: dict = Body(...)):
        return core.cache_track(body["identifier"], body["file"])

    @api.get("/api/track/peaks")
    def peaks(identifier: str = "", file: str = "", path: str = "", n: int = 1600):
        return audio.peaks(local_path(identifier, file, path), n)

    @api.get("/api/track/spectrogram.png")
    def spectrogram(identifier: str = "", file: str = "", path: str = "", w: int = 1200, h: int = 400, legend: bool = False):
        return Response(audio.spectrogram_png(local_path(identifier, file, path), w, h, legend), media_type="image/png")

    @api.get("/api/track/spectrum3d")
    def spectrum3d(identifier: str = "", file: str = "", path: str = "", slices: int = 40, bins: int = 128):
        return audio.spectrum3d(local_path(identifier, file, path), slices, bins)

    @api.get("/api/track/analysis")
    def analysis(identifier: str = "", file: str = "", path: str = ""):
        return audio.analyze(local_path(identifier, file, path))

    # ---- events ----------------------------------------------------------------------------------------------
    @api.websocket("/api/events")
    async def events(ws: WebSocket):
        given = ws.cookies.get(COOKIE) or ws.query_params.get("token") or ""
        if not given or not secrets.compare_digest(given, token):
            await ws.close(code=4401)
            return
        await ws.accept()
        loop = asyncio.get_running_loop()
        inbox: asyncio.Queue = asyncio.Queue(maxsize=2000)

        def push(event: dict) -> None:
            def put():
                if not inbox.full():
                    inbox.put_nowait(event)
            loop.call_soon_threadsafe(put)

        unlisten = core.listen(push)
        try:
            await ws.send_text(json.dumps({"type": "hello", "version": __version__}))
            while True:
                event = await inbox.get()
                await ws.send_text(json.dumps(event, default=str))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            unlisten()

    return api
