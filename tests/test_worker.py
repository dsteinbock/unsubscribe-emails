from __future__ import annotations

from datetime import datetime, timezone

from unsubscribe_emails import browser
from unsubscribe_emails.browser import (
    OUTCOME_DONE,
    OUTCOME_NEEDS_AGENT,
    WorkerResult,
    decide_action,
    run_queue,
)
from unsubscribe_emails.models import UnsubscribeRecord
from unsubscribe_emails.oneclick import ONE_CLICK_BODY, OneClickResult, one_click_unsubscribe
from unsubscribe_emails.patterns import (
    detect_blocker,
    detect_error,
    detect_success,
    detect_unsafe,
)
from unsubscribe_emails.store import load_state, save_state


def candidate(role, name, index=0, checked=False, value=""):
    return {"id": f"{role}:{index}", "role": role, "name": name, "checked": checked, "value": value}


# --- patterns ---------------------------------------------------------------


def test_detect_success_matches_confirmation_language():
    assert detect_success("You have been unsubscribed from this list.")
    assert detect_success("Your email preferences have been updated.")
    assert not detect_success("Click unsubscribe below to stop receiving emails.")


def test_detect_unsafe_flags_account_actions_but_not_newsletter_cancel():
    assert detect_unsafe("Do you really want to delete your account?")
    assert detect_unsafe("Enter your password to continue")
    # "cancel subscription" is the unsubscribe itself for newsletters -> not unsafe
    assert detect_unsafe("Cancel your subscription to this newsletter") is None


def test_detect_blocker_flags_captcha_and_login():
    assert detect_blocker("Please verify you are human")
    assert detect_blocker("Sign in to your account to manage preferences")
    assert detect_blocker("You have been unsubscribed") is None


def test_detect_success_handles_email_in_place_of_you():
    # Real pages from the Haiku run that the old patterns missed.
    assert detect_success(
        "We're sorry to see you go We have removed danielsteinbock2@gmail.com "
        "from all Monte Rio Entertainment mailing lists."
    )
    assert detect_success("dan.steinbock@gmail.com will no longer receive emails from us.")


def test_detect_error_flags_dead_and_405_pages():
    assert detect_error("An error has occurred and has been logged by our system. Thank you.")
    assert detect_error(
        "Whitelabel Error Page This application has no explicit mapping for /error"
    )
    assert detect_error("405. That's an error. The server cannot process the request")
    assert detect_error("There was an unexpected error (type=Method Not Allowed, status=405).")
    assert detect_error("You have been unsubscribed") is None


# --- decide_action ----------------------------------------------------------


def test_decide_action_clicks_confirm_unsubscribe_button():
    candidates = [
        candidate("button", "Update preferences", 0),
        candidate("button", "Confirm unsubscribe", 1),
    ]
    action = decide_action(candidates, "Confirm your unsubscribe", None, email_filled=False)
    assert action["type"] == "click"
    assert action["candidate"]["id"] == "button:1"


def test_decide_action_fills_email_before_clicking():
    candidates = [
        candidate("textbox", "Email address", 0),
        candidate("button", "Unsubscribe", 0),
    ]
    action = decide_action(candidates, "Unsubscribe page", "me@example.com", email_filled=False)
    assert action == {
        "type": "fill",
        "candidate": candidates[0],
        "label": "email",
    }


def test_decide_action_skips_email_fill_when_already_filled():
    candidates = [
        candidate("textbox", "Email address", 0, value="me@example.com"),
        candidate("button", "Unsubscribe", 0),
    ]
    action = decide_action(candidates, "Unsubscribe page", "me@example.com", email_filled=True)
    assert action["type"] == "click"
    assert action["candidate"]["id"] == "button:0"


def test_decide_action_checks_unsubscribe_all_checkbox():
    candidates = [candidate("checkbox", "Unsubscribe from all emails", 0, checked=False)]
    action = decide_action(candidates, "Manage your subscription preferences", None, False)
    assert action["type"] == "check"


def test_decide_action_does_not_press_bare_submit_without_context():
    candidates = [candidate("button", "Submit", 0)]
    # No unsubscribe context in the body -> guarded submit must not fire.
    assert decide_action(candidates, "Contact us form", None, False) is None


def test_decide_action_presses_submit_with_unsub_context():
    candidates = [candidate("button", "Save", 0)]
    action = decide_action(candidates, "Update your email preferences", None, False)
    assert action["type"] == "click"
    assert action["label"] in ("save_preferences", "submit")


def test_decide_action_matches_save_preferences_phrase():
    candidates = [candidate("button", "Save preferences", 0)]
    action = decide_action(candidates, "Update your email preferences", None, False)
    assert action["label"] == "save_preferences"


def test_decide_action_returns_none_for_ambiguous_preference_center():
    candidates = [
        candidate("checkbox", "Weekly digest", 0, checked=True),
        candidate("checkbox", "Product news", 1, checked=True),
        candidate("link", "Manage preferences", 0),
    ]
    assert decide_action(candidates, "Choose which topics you receive", None, False) is None


# --- one-click --------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_one_click_posts_rfc8058_body_and_succeeds_on_2xx():
    captured = {}

    def opener(request, timeout):
        captured["data"] = request.data
        captured["method"] = request.get_method()
        captured["url"] = request.full_url
        return _FakeResponse(200)

    result = one_click_unsubscribe("https://example.com/u", opener=opener)

    assert captured["data"] == ONE_CLICK_BODY
    assert captured["method"] == "POST"
    assert result.ok is True
    assert result.status == 200


def test_one_click_reports_failure_on_error():
    def opener(request, timeout):
        raise OSError("dns failure")

    result = one_click_unsubscribe("https://example.com/u", opener=opener)
    assert result.ok is False
    assert "dns failure" in (result.error or "")


# --- run_queue cap behavior --------------------------------------------------


def _todo_record(record_id, *, sender, one_click=False, fallback=None):
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return UnsubscribeRecord(
        id=record_id,
        status="todo",
        attempts=0,
        lastModified=now,
        gmailMessageId=record_id,
        gmailThreadId=f"t-{record_id}",
        gmailUrl=f"https://mail.google.com/mail/u/0/#all/t-{record_id}",
        senderName=sender,
        senderEmail=f"{sender}@example.com",
        toEmails=["me@example.com"],
        recipientEmail="me@example.com",
        subject="Subject",
        unsubscribeUrl=f"https://example.com/u/{record_id}",
        unsubscribeUrlFallback=fallback,
        oneClick=one_click,
        createdAt=now,
    )


class _AlwaysAmbiguousWorker:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def process_record(self, url, recipient_email):
        return WorkerResult(OUTCOME_NEEDS_AGENT, "ambiguous", "needs a human", url=url)


def test_run_queue_caps_agent_handoff_and_leaves_rest_in_todo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(browser, "BrowserWorker", _AlwaysAmbiguousWorker)
    state = load_state()
    state.todo = [_todo_record(f"m{i}", sender=f"s{i}") for i in range(5)]
    save_state(state)

    summary = run_queue(agent_limit=2)

    assert summary["needsAgent"] == 2
    assert summary["stoppedEarly"] is True
    assert len(summary["needsAgentEntries"]) == 2
    # The 3 unprocessed records remain in todo for the next run.
    assert len(load_state().todo) == 5  # nothing was marked done/retried


class _FallbackOnlyWorker:
    """Fails every primary URL but succeeds when the body-fallback URL is used."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def process_record(self, url, recipient_email):
        if "/fallback/" in url:
            return WorkerResult(OUTCOME_DONE, "open", "confirmed via body link", url=url)
        return WorkerResult(OUTCOME_NEEDS_AGENT, "ambiguous", "header link failed", url=url)


def test_run_queue_uses_body_fallback_when_primary_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(browser, "BrowserWorker", _FallbackOnlyWorker)
    state = load_state()
    state.todo = [_todo_record("m1", sender="s1", fallback="https://example.com/fallback/m1")]
    save_state(state)

    summary = run_queue()

    assert summary["autoDone"] == 1
    assert summary["needsAgent"] == 0
    assert {r.id for r in load_state().done} == {"m1"}


def test_run_queue_resolves_one_click_without_browser(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def boom(*args, **kwargs):  # browser must not be launched for pure one-click
        raise AssertionError("BrowserWorker should not be constructed")

    monkeypatch.setattr(browser, "BrowserWorker", boom)
    monkeypatch.setattr(
        browser, "one_click_unsubscribe", lambda url, **kw: OneClickResult(ok=True, status=200)
    )
    state = load_state()
    state.todo = [_todo_record(f"m{i}", sender=f"s{i}", one_click=True) for i in range(3)]
    save_state(state)

    summary = run_queue(agent_limit=2)

    assert summary["oneClick"] == 3
    assert summary["autoDone"] == 3
    assert summary["needsAgent"] == 0
    assert load_state().todo == []
    assert {r.id for r in load_state().done} == {"m0", "m1", "m2"}
