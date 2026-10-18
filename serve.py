"""Serve the session tracker on this machine only.

The process binds to 127.0.0.1. Tailscale Serve can publish that port
on the tailnet. It is not on the public internet. A background read
refreshes the board. Nothing on the page starts an agent or sends a continue.
"""

from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from classify import classify
from collect import collect, read_snapshot, write_snapshot
from render import render_board, render_page

REFRESH_S = 15


def live_collect() -> dict:
    now_ms = int(time.time() * 1000)
    snapshot = collect(read_snapshot(), now_ms=now_ms)
    try:
        write_snapshot(snapshot)
    except OSError:
        pass
    return snapshot


def make_server(collect_fn, port: int = 8765, *, refresh_s: int | None = None) -> ThreadingHTTPServer:
    state = {"view": None}
    lock = threading.Lock()
    stop = threading.Event()

    def pull() -> None:
        try:
            snapshot = collect_fn()
            view = classify(snapshot, now_ms=int(time.time() * 1000))
        except Exception as exc:
            with lock:
                previous = state["view"]
            if previous and refresh_s:
                view = dict(previous)
                view["warning"] = "The latest read failed. This is the last good list."
                view["stale"] = True
            else:
                view = _failure_view(exc)
        with lock:
            state["view"] = view

    def loop() -> None:
        while not stop.is_set():
            pull()
            stop.wait(refresh_s)

    if refresh_s:
        threading.Thread(target=loop, name="central-command-refresh", daemon=True).start()

    def current_view() -> dict:
        if not refresh_s:
            try:
                snapshot = collect_fn()
                return classify(snapshot, now_ms=int(time.time() * 1000))
            except Exception as exc:
                return _failure_view(exc)
        deadline = time.time() + 90
        while time.time() < deadline:
            with lock:
                if state["view"] is not None:
                    return state["view"]
            time.sleep(0.2)
        return _failure_view(TimeoutError("The first read did not finish."))

    server = ThreadingHTTPServer(("127.0.0.1", port), handler_factory(current_view))
    server.refresh_stop = stop
    return server


def handler_factory(view_fn):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/health":
                self._send(200, "text/plain; charset=utf-8", b"ok\n")
                return
            if path == "/partial":
                body = render_board(view_fn()).encode("utf-8")
                self._send(200, "text/html; charset=utf-8", body)
                return
            if path not in ("/", "/index.html"):
                self._send(404, "text/plain; charset=utf-8", b"not found\n")
                return
            self._send(200, "text/html; charset=utf-8", render_page(view_fn()).encode("utf-8"))

        def do_POST(self):
            self._send(405, "text/plain; charset=utf-8", b"read only\n")

        def log_message(self, fmt, *args):
            return

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _failure_view(exc: Exception) -> dict:
    return {
        "stale": True,
        "read_at_label": "failed",
        "overview": "The read failed before a session list could be built.",
        "warning": str(exc),
        "sessions": [
            {
                "state": "attention",
                "title": "The read failed",
                "line": "The next read replaces this note.",
                "where": "This box",
                "age": "unread",
                "stands": [str(exc)],
            }
        ],
    }


def main() -> None:
    port = int(os.environ.get("CENTRAL_COMMAND_PORT", "8765"))
    server = make_server(live_collect, port, refresh_s=REFRESH_S)
    print(f"Central Command at http://127.0.0.1:{server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.refresh_stop.set()


if __name__ == "__main__":
    main()
