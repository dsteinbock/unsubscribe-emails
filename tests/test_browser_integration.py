"""Live-browser coverage for the worker glue.

Skips automatically unless Playwright + a Chromium build are available
(`uv sync && uv run playwright install chromium`).
"""

from __future__ import annotations

import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("playwright")

from unsubscribe_emails.browser import (  # noqa: E402
    OUTCOME_DONE,
    OUTCOME_NEEDS_AGENT,
    OUTCOME_RETRY,
    BrowserWorker,
)


def data_url(html: str) -> str:
    return "data:text/html," + urllib.parse.quote(html)


@pytest.fixture(scope="module")
def worker():
    try:
        with BrowserWorker(nav_timeout=8000, settle_timeout=2000) as w:
            yield w
    except Exception as exc:  # noqa: BLE001 - chromium not installed, etc.
        pytest.skip(f"Chromium unavailable: {exc}")


@pytest.fixture(scope="module")
def nav_site():
    """A two-page site whose Unsubscribe button navigates to a confirmation.

    Mirrors real ESP pages (e.g. Klaviyo) where the unsubscribe control is a
    form submit that loads a separate confirmation URL. Chromium blocks
    script navigation to data: URLs, so a real same-origin HTTP hop is needed
    to exercise the navigation-wait path.
    """
    form = (
        "<html><body><p>Please confirm your unsubscribe request.</p>"
        "<button onclick=\"window.location.href='/done'\">Confirm unsubscribe</button>"
        "</body></html>"
    )
    done = "<html><body><h1>You have been unsubscribed</h1></body></html>"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = done if self.path.startswith("/done") else form
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, *args):  # silence test server logging
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()


def test_success_on_open_marks_done(worker):
    url = data_url("<html><body><h1>You have been unsubscribed.</h1></body></html>")
    result = worker.process_record(url, None)
    assert result.outcome == OUTCOME_DONE
    assert result.tier == "open"


def test_confirm_click_reaches_success(worker):
    url = data_url(
        "<html><body>"
        "<p>Please confirm you want to unsubscribe.</p>"
        "<button onclick=\"document.body.innerHTML='You have been unsubscribed'\">"
        "Confirm unsubscribe</button>"
        "</body></html>"
    )
    result = worker.process_record(url, None)
    assert result.outcome == OUTCOME_DONE
    assert result.tier == "action"


def test_ambiguous_preference_center_needs_agent(worker):
    url = data_url(
        "<html><body>"
        "<h1>Choose which topics you receive</h1>"
        "<label><input type='checkbox' checked> Weekly digest</label>"
        "<label><input type='checkbox' checked> Product news</label>"
        "<a href='#'>Manage preferences</a>"
        "</body></html>"
    )
    result = worker.process_record(url, None)
    assert result.outcome == OUTCOME_NEEDS_AGENT
    assert result.candidates  # the enumerated controls are handed off


def test_error_page_retries(worker):
    url = data_url(
        "<html><body>An error has occurred and has been logged by our system. Thank you."
        "</body></html>"
    )
    result = worker.process_record(url, None)
    assert result.outcome == OUTCOME_RETRY
    assert result.tier == "error_page"


def test_inspect_clicks_named_control_and_reports_success(worker):
    url = data_url(
        "<html><body>"
        "<p>Manage your email preferences.</p>"
        "<button onclick=\"document.body.innerHTML='You have been unsubscribed'\">"
        "Unsubscribe</button>"
        "</body></html>"
    )
    state = worker.inspect(url, clicks=["Unsubscribe"])
    assert state["success"] is True
    assert "unsubscribed" in state["snippet"].lower()


def test_inspect_click_that_navigates_reports_success(worker, nav_site):
    # The re-read must wait for the navigation, not the stale form page.
    state = worker.inspect(nav_site, clicks=["Confirm unsubscribe"])
    assert state["success"] is True
    assert "unsubscribed" in state["snippet"].lower()


def test_worker_confirms_unsubscribe_across_navigation(worker, nav_site):
    # Same navigation pattern through the deterministic Tier-2 action loop.
    result = worker.process_record(nav_site, None)
    assert result.outcome == OUTCOME_DONE
    assert result.tier == "action"


def test_inspect_captures_associated_label_as_candidate_name(worker):
    # A checkbox's text lives in its <label>, not the input node. Without
    # label resolution the candidate name is empty and nothing can match it.
    url = data_url(
        "<html><body>"
        "<input type='checkbox' id='all'>"
        "<label for='all'>Unsubscribe from all emails</label>"
        "</body></html>"
    )
    state = worker.inspect(url)
    names = [c["name"] for c in state["candidates"]]
    assert any("Unsubscribe from all emails" in n for n in names)


def test_worker_completes_labelled_checkbox_and_submit_form(worker):
    # Mirrors OhmConnect/SAS: tick "unsubscribe from all" (label-only text),
    # then click the save/confirm button -> confirmation.
    url = data_url(
        "<html><body><h1>Manage your email preferences</h1>"
        "<input type='checkbox' id='all'>"
        "<label for='all'>Unsubscribe me from all mailing lists</label>"
        "<button type='button' onclick=\"document.body.innerHTML="
        "'Your email preferences have been updated'\">Update email preferences</button>"
        "</body></html>"
    )
    result = worker.process_record(url, None)
    assert result.outcome == OUTCOME_DONE
    assert result.tier == "action"


def test_inspect_without_action_dumps_candidates(worker):
    url = data_url(
        "<html><body><p>Manage preferences</p>"
        "<button>Unsubscribe from all</button></body></html>"
    )
    state = worker.inspect(url)
    assert state["success"] is False
    names = [c["name"] for c in state["candidates"]]
    assert any("Unsubscribe from all" in n for n in names)
