"""Start the GUI: the API server on a free 127.0.0.1 port, then a native window (pywebview) showing it.

    iaflac-gui                 window
    iaflac-gui --browser       open in the default browser instead (handy while developing the UI)
    iaflac-gui --server-only   just the server; prints the URL
"""

from __future__ import annotations

import argparse
import secrets
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from .server import create_app


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port: int, token: str) -> uvicorn.Server:
    config = uvicorn.Config(create_app(token=token), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="iaflac-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise SystemExit("the iaflac server did not start")
    return server


class Bridge:
    """Called from the page as window.pywebview.api.* (only inside the app window, not in a browser)."""

    def __init__(self):
        self._window = None

    def choose_folder(self, start: str = ""):
        import webview
        picked = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory=start or "")
        return picked[0] if picked else None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="iaflac-gui", description="iaflac desktop app")
    p.add_argument("--browser", action="store_true", help="open in the default web browser instead of a window")
    p.add_argument("--server-only", action="store_true", help="run the server and print its URL")
    p.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free port)")
    p.add_argument("--debug", action="store_true", help="enable the web inspector in the window")
    p.add_argument("--url-file", help="also write the URL (with its token) to this file; for scripted checks of a build")
    args = p.parse_args(argv)
    port, token = args.port or free_port(), secrets.token_urlsafe(24)
    server = start_server(port, token)
    url = f"http://127.0.0.1:{port}/?token={token}"
    if args.url_file:
        Path(args.url_file).write_text(url + "\n", encoding="utf-8")
    try:
        if args.server_only:
            print(url, flush=True)
            while True:
                time.sleep(3600)
        elif args.browser:
            print(url, flush=True)
            webbrowser.open(url)
            while True:
                time.sleep(3600)
        else:
            import webview
            bridge = Bridge()
            bridge._window = webview.create_window("flacmn", url, width=1440, height=900, min_size=(1000, 680), js_api=bridge,
                                                  background_color="#2E2E33")   # the launch screen's charcoal, no flash of white
            # WebKit will not let the page start the launch video itself; a call from here counts as the user's, so it plays
            bridge._window.events.loaded += lambda: bridge._window.evaluate_js("window.flacmnSplash && window.flacmnSplash.play()")
            webview.start(debug=args.debug)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
    return 0


if __name__ == "__main__":
    sys.exit(main())
