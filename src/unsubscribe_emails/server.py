from __future__ import annotations

import errno
import socket
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .report import REPORT_PATH, write_report
from .store import ignore_sender, mark_done

DEFAULT_LIFETIME_HOURS = 8.0


def _port_in_use(host: str, port: int) -> bool:
    """True if something is already listening on ``host:port``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((host, port)) == 0


def run_review_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    lifetime_hours: float = DEFAULT_LIFETIME_HOURS,
) -> None:
    write_report(REPORT_PATH)
    url = f"http://{host}:{port}"

    # If a previous run left a review server on this port, don't crash with
    # "address already in use" — the report is already being served there, so
    # just point the browser at it and return.
    if _port_in_use(host, port):
        print(f"Review server already running at {url}; reusing it.")
        if open_browser:
            webbrowser.open(url)
        return

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/ignore":
                params = parse_qs(parsed.query)
                sender = params.get("sender", [""])[0]
                try:
                    ignore_sender(sender)
                    write_report(REPORT_PATH)
                except Exception as exc:
                    self._send_error(exc)
                    return
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("location", "/")
                self.end_headers()
                return

            if parsed.path == "/done":
                # Let the user mark an entry done after unsubscribing by hand.
                params = parse_qs(parsed.query)
                record_id = params.get("id", [""])[0]
                try:
                    mark_done(record_id)
                    write_report(REPORT_PATH)
                except Exception as exc:
                    self._send_error(exc)
                    return
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("location", "/")
                self.end_headers()
                return

            if parsed.path in {"/", "/unsubscribe-report.html"}:
                self._send_file(REPORT_PATH)
                return

            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()

        def _send_error(self, exc: Exception) -> None:
            self.send_response(HTTPStatus.BAD_REQUEST)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(str(exc).encode("utf-8"))

        def log_message(self, format, *args):  # noqa: A002
            return

        def _send_file(self, path: Path) -> None:
            if not path.exists():
                write_report(path)
            content = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        # Lost a race for the port between the check above and binding here.
        if exc.errno in (errno.EADDRINUSE, errno.EADDRNOTAVAIL):
            print(f"Review server already running at {url}; reusing it.")
            if open_browser:
                webbrowser.open(url)
            return
        raise

    lifetime = "no limit" if lifetime_hours <= 0 else f"{lifetime_hours:g}h"
    print(f"Review server running at {url} (auto-shutdown after {lifetime})")
    if open_browser:
        # Pop open a visible browser window once the server is actually serving.
        # A short delay avoids racing serve_forever() startup.
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    if lifetime_hours and lifetime_hours > 0:
        # Auto-shut down so an unattended server doesn't linger indefinitely.
        shutdown = threading.Timer(lifetime_hours * 3600, server.shutdown)
        shutdown.daemon = True
        shutdown.start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
