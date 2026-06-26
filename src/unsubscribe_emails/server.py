from __future__ import annotations

import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .report import REPORT_PATH, write_report
from .store import ignore_sender


def run_review_server(
    host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True
) -> None:
    write_report(REPORT_PATH)

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
                    self.send_response(HTTPStatus.BAD_REQUEST)
                    self.send_header("content-type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(str(exc).encode("utf-8"))
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

    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    print(f"Review server running at {url}")
    if open_browser:
        # Pop open a visible browser window once the server is actually serving.
        # A short delay avoids racing serve_forever() startup.
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    server.serve_forever()
