from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from unsubscribe_emails.models import UnsubscribeRecord
from unsubscribe_emails.report import write_report
from unsubscribe_emails.store import (
    DONE_PATH,
    IGNORED_PATH,
    REVIEW_PATH,
    TODO_PATH,
    ignore_sender,
    load_state,
    mark_done,
    mark_retry,
    next_records,
    save_state,
)


def make_record(record_id: str, sender: str = "news@example.com", subject: str = "Subject"):
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return UnsubscribeRecord(
        id=record_id,
        status="todo",
        attempts=0,
        lastModified=now,
        gmailMessageId=record_id,
        gmailThreadId=f"thread-{record_id}",
        gmailUrl=f"https://mail.google.com/mail/u/0/#all/thread-{record_id}",
        senderName="News",
        senderEmail=sender,
        toEmails=["me@example.com"],
        recipientEmail="me@example.com",
        subject=subject,
        unsubscribeUrl="https://example.com/unsubscribe",
        unsubscribeSource="body",
        createdAt=now,
    )


def test_mark_retry_moves_to_review_on_third_attempt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = load_state()
    state.todo = [make_record("m1")]
    save_state(state)

    first = mark_retry("m1", "first miss")
    second = mark_retry("m1", "second miss")
    third = mark_retry("m1", "third miss")
    state = load_state()

    assert first.status == "retry"
    assert second.attempts == 2
    assert third.status == "review"
    assert state.todo == []
    assert [record.id for record in state.review] == ["m1"]


def test_mark_done_moves_from_todo_to_done(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = load_state()
    state.todo = [make_record("m1")]
    save_state(state)

    record = mark_done("m1")
    state = load_state()

    assert record.status == "done"
    assert state.todo == []
    assert [done.id for done in state.done] == ["m1"]
    assert state.done[0].completedAt


def test_next_records_prints_compact_browser_payload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = load_state()
    state.todo = [make_record("m1")]
    save_state(state)

    result = next_records(1)

    assert result == [
        {
            "id": "m1",
            "senderName": "News",
            "senderEmail": "news@example.com",
            "subject": "Subject",
            "recipientEmail": "me@example.com",
            "gmailUrl": "https://mail.google.com/mail/u/0/#all/thread-m1",
            "unsubscribeUrl": "https://example.com/unsubscribe",
            "unsubscribeUrlFallback": None,
            "attempts": 0,
        }
    ]


def test_next_records_deduplicates_senders_and_keeps_newest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    older = make_record("older", sender="news@example.com", subject="Older")
    older.createdAt = (now - timedelta(days=2)).isoformat()
    older.lastModified = older.createdAt
    newer = make_record("newer", sender="NEWS@example.com", subject="Newer")
    newer.createdAt = (now - timedelta(days=1)).isoformat()
    newer.lastModified = newer.createdAt
    other = make_record("other", sender="other@example.com", subject="Other")
    other.createdAt = now.isoformat()
    other.lastModified = other.createdAt
    state = load_state()
    state.todo = [older, newer, other]
    save_state(state)

    result = next_records(10)

    assert [record["id"] for record in result] == ["newer", "other"]
    assert {record["senderEmail"].lower() for record in result} == {
        "news@example.com",
        "other@example.com",
    }


def test_ignore_sender_is_case_insensitive_and_removes_open_records(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = load_state()
    state.todo = [make_record("m1", sender="News@Example.com")]
    state.review = [make_record("m2", sender="news@example.com")]
    save_state(state)

    result = ignore_sender("NEWS@example.com")
    state = load_state()

    assert result["removedTodo"] == 1
    assert result["removedReview"] == 1
    assert state.ignored_senders == ["news@example.com"]
    assert state.todo == []
    assert state.review == []


def test_report_filters_recent_done_and_includes_review_links(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    recent = make_record("recent", subject="Recent Done")
    recent.status = "done"
    recent.completedAt = now.isoformat()
    old = make_record("old", subject="Old Done")
    old.status = "done"
    old.completedAt = (now - timedelta(days=8)).isoformat()
    review = make_record("review", subject="Needs Help")
    review.status = "review"
    review.attempts = 3
    review.reviewedAt = now.isoformat()
    state = load_state()
    state.done = [old, recent]
    state.review = [review]
    state.ignored_senders = ["z@example.com", "a@example.com"]
    save_state(state)

    report_path = write_report()
    html = report_path.read_text(encoding="utf-8")

    recent_section = html.split("Needs Manual Review")[0]
    assert "Recent Done" in recent_section
    assert "Old Done" not in recent_section
    assert "Needs Help" in html
    assert "/ignore?sender=news%40example.com" in html
    assert html.index("a@example.com") < html.index("z@example.com")


def test_markdown_files_contain_machine_json_block(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = load_state()
    state.todo = [make_record("m1")]
    save_state(state)

    text = TODO_PATH.read_text(encoding="utf-8")
    payload = text.split("<!-- AUTO-UNSUBSCRIBE:JSON", 1)[1].split("AUTO-UNSUBSCRIBE:END -->", 1)[0]

    assert json.loads(payload)[0]["id"] == "m1"
    assert DONE_PATH.exists()
    assert REVIEW_PATH.exists()
    assert IGNORED_PATH.exists()
