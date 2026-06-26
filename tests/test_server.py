from __future__ import annotations

import socket
import threading
import time
import urllib.request
from datetime import datetime, timezone

from unsubscribe_emails.models import UnsubscribeRecord
from unsubscribe_emails.server import _port_in_use, run_review_server
from unsubscribe_emails.store import load_state, save_state


def _listening_socket() -> tuple[socket.socket, str, int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    host, port = sock.getsockname()
    return sock, host, port


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_port_in_use_detects_listener():
    sock, host, port = _listening_socket()
    try:
        assert _port_in_use(host, port) is True
    finally:
        sock.close()


def test_run_review_server_reuses_busy_port_without_raising(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sock, host, port = _listening_socket()
    try:
        # A previous server already holds the port: this must return promptly
        # (reuse) rather than crash with "address already in use" or block.
        start = time.time()
        run_review_server(host=host, port=port, open_browser=False, lifetime_hours=0)
        assert time.time() - start < 5
    finally:
        sock.close()


def _review_record(record_id: str) -> UnsubscribeRecord:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return UnsubscribeRecord(
        id=record_id,
        status="review",
        attempts=3,
        lastModified=now,
        gmailMessageId=record_id,
        gmailThreadId=f"t-{record_id}",
        gmailUrl=f"https://mail.google.com/mail/u/0/#all/t-{record_id}",
        senderName="News",
        senderEmail="news@example.com",
        toEmails=["me@example.com"],
        recipientEmail="me@example.com",
        subject="Subject",
        unsubscribeUrl="https://example.com/unsub",
        reviewedAt=now,
    )


def test_done_endpoint_marks_review_record_done(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = load_state()
    state.review = [_review_record("rev1")]
    save_state(state)

    port = _free_port()
    thread = threading.Thread(
        target=run_review_server,
        kwargs=dict(host="127.0.0.1", port=port, open_browser=False, lifetime_hours=0.01),
        daemon=True,
    )
    thread.start()
    deadline = time.time() + 5
    while not _port_in_use("127.0.0.1", port) and time.time() < deadline:
        time.sleep(0.05)

    with urllib.request.urlopen(f"http://127.0.0.1:{port}/done?id=rev1", timeout=5) as resp:
        assert resp.status == 200  # redirected to the report

    state = load_state()
    assert [r.id for r in state.done] == ["rev1"]
    assert state.review == []


def test_run_review_server_auto_shuts_down_after_lifetime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    port = _free_port()
    start = time.time()
    # ~0.7s lifetime: serve_forever should be released by the shutdown timer.
    run_review_server(
        host="127.0.0.1", port=port, open_browser=False, lifetime_hours=0.0002
    )
    elapsed = time.time() - start
    assert 0.1 < elapsed < 10
