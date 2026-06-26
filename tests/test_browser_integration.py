"""Live-browser coverage for the worker glue.

Skips automatically unless Playwright + a Chromium build are available
(`uv sync && uv run playwright install chromium`).
"""

from __future__ import annotations

import urllib.parse

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


def test_inspect_without_action_dumps_candidates(worker):
    url = data_url(
        "<html><body><p>Manage preferences</p>"
        "<button>Unsubscribe from all</button></body></html>"
    )
    state = worker.inspect(url)
    assert state["success"] is False
    names = [c["name"] for c in state["candidates"]]
    assert any("Unsubscribe from all" in n for n in names)
