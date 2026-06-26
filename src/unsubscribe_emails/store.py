from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .extract import normalize_email
from .models import UnsubscribeRecord, UnsubscribeState
from .timeutil import now_iso, parse_iso

TODO_PATH = Path("unsubscribe-todo.md")
DONE_PATH = Path("unsubscribe-done.md")
REVIEW_PATH = Path("unsubscribe-review.md")
IGNORED_PATH = Path("ignored-senders.md")

JSON_START = "<!-- AUTO-UNSUBSCRIBE:JSON"
JSON_END = "AUTO-UNSUBSCRIBE:END -->"


def load_state() -> UnsubscribeState:
    return UnsubscribeState(
        todo=read_records(TODO_PATH),
        done=read_records(DONE_PATH),
        review=read_records(REVIEW_PATH),
        ignored_senders=read_ignored_senders(IGNORED_PATH),
    )


def save_state(state: UnsubscribeState) -> None:
    write_records(TODO_PATH, "Unsubscribe Todo", state.todo)
    write_records(DONE_PATH, "Unsubscribe Done", state.done)
    write_records(REVIEW_PATH, "Unsubscribe Review", state.review)
    write_ignored_senders(IGNORED_PATH, state.ignored_senders)


def read_records(path: Path) -> list[UnsubscribeRecord]:
    data = _read_json_block(path, default=[])
    return [UnsubscribeRecord.from_dict(item) for item in data]


def write_records(path: Path, title: str, records: Iterable[UnsubscribeRecord]) -> None:
    record_list = list(records)
    payload = [record.to_dict() for record in record_list]
    lines = [
        f"# {title}",
        "",
        JSON_START,
        json.dumps(payload, indent=2, sort_keys=True),
        JSON_END,
        "",
        _records_table(record_list),
        "",
    ]
    _atomic_write(path, "\n".join(lines))


def read_ignored_senders(path: Path = IGNORED_PATH) -> list[str]:
    data = _read_json_block(path, default=[])
    return sorted({normalize_email(item) for item in data if normalize_email(item)})


def write_ignored_senders(path: Path, senders: Iterable[str]) -> None:
    sender_list = sorted({normalize_email(sender) for sender in senders if normalize_email(sender)})
    rows = "\n".join(f"| `{sender}` |" for sender in sender_list) or "| _None_ |"
    content = "\n".join(
        [
            "# Ignored Senders",
            "",
            JSON_START,
            json.dumps(sender_list, indent=2, sort_keys=True),
            JSON_END,
            "",
            "| Sender |",
            "| --- |",
            rows,
            "",
        ]
    )
    _atomic_write(path, content)


def upsert_todo_record(record: UnsubscribeRecord) -> str:
    state = load_state()
    if _find_by_message_id(state.done, record.gmailMessageId) or _find_by_message_id(
        state.review, record.gmailMessageId
    ):
        save_state(state)
        return "skipped-existing-terminal"

    existing = _find_by_message_id(state.todo, record.gmailMessageId)
    if existing:
        existing.senderName = record.senderName
        existing.senderEmail = record.senderEmail
        existing.toEmails = record.toEmails
        existing.recipientEmail = record.recipientEmail
        existing.subject = record.subject
        existing.unsubscribeUrl = record.unsubscribeUrl
        existing.unsubscribeMailto = record.unsubscribeMailto
        existing.unsubscribeSource = record.unsubscribeSource
        existing.gmailThreadId = record.gmailThreadId
        existing.gmailUrl = record.gmailUrl
        existing.lastModified = now_iso()
        result = "updated"
    elif not record.unsubscribeUrl:
        timestamp = now_iso()
        record.status = "review"
        record.reviewedAt = timestamp
        record.lastModified = timestamp
        record.lastError = "no unsubscribe URL found"
        state.review = _replace_or_append(state.review, record)
        result = "created"
    else:
        state.todo.append(record)
        result = "created"
    save_state(state)
    return result


def next_records(limit: int) -> list[dict]:
    state = load_state()
    newest_by_sender: dict[str, UnsubscribeRecord] = {}
    for record in state.todo:
        sender = normalize_email(record.senderEmail)
        key = sender or record.id
        existing = newest_by_sender.get(key)
        if existing is None or parse_iso(record.createdAt or record.lastModified) > parse_iso(
            existing.createdAt or existing.lastModified
        ):
            newest_by_sender[key] = record
    records = sorted(
        newest_by_sender.values(),
        key=lambda record: parse_iso(record.createdAt or record.lastModified),
    )
    return [record.compact_for_browser() for record in records[:limit]]


def mark_done(record_id: str) -> UnsubscribeRecord:
    state = load_state()
    record = _pop_by_id(state.todo, record_id)
    if record is None:
        record = _pop_by_id(state.review, record_id)
    if record is None:
        raise KeyError(f"No todo/review entry found for id {record_id!r}")

    timestamp = now_iso()
    record.status = "done"
    record.completedAt = timestamp
    record.lastModified = timestamp
    record.lastError = None
    state.done = _replace_or_append(state.done, record)
    save_state(state)
    return record


def mark_retry(record_id: str, reason: str) -> UnsubscribeRecord:
    state = load_state()
    record = _pop_by_id(state.todo, record_id)
    if record is None:
        raise KeyError(f"No todo entry found for id {record_id!r}")

    timestamp = now_iso()
    record.attempts += 1
    record.lastModified = timestamp
    record.lastError = reason
    if record.attempts >= 3:
        record.status = "review"
        record.reviewedAt = timestamp
        state.review = _replace_or_append(state.review, record)
    else:
        record.status = "retry"
        state.todo = _replace_or_append(state.todo, record)
    save_state(state)
    return record


def ignore_sender(sender_email: str) -> dict[str, int | str]:
    normalized = normalize_email(sender_email)
    if not normalized:
        raise ValueError("sender email is required")

    state = load_state()
    state.ignored_senders = sorted(set(state.ignored_senders) | {normalized})
    before_todo = len(state.todo)
    before_review = len(state.review)
    state.todo = [record for record in state.todo if normalize_email(record.senderEmail) != normalized]
    state.review = [record for record in state.review if normalize_email(record.senderEmail) != normalized]
    save_state(state)
    return {
        "sender": normalized,
        "removedTodo": before_todo - len(state.todo),
        "removedReview": before_review - len(state.review),
    }


def is_ignored_sender(sender_email: str) -> bool:
    return normalize_email(sender_email) in set(load_state().ignored_senders)


def _read_json_block(path: Path, default):
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8")
    start = text.find(JSON_START)
    end = text.find(JSON_END)
    if start == -1 or end == -1 or end < start:
        return default
    payload_start = start + len(JSON_START)
    payload = text[payload_start:end].strip()
    if not payload:
        return default
    return json.loads(payload)


def _records_table(records: list[UnsubscribeRecord]) -> str:
    if not records:
        return "| Status | Attempts | Sender | Subject | Recipient | Last Modified |\n| --- | ---: | --- | --- | --- | --- |\n| _None_ |  |  |  |  |  |"
    rows = [
        "| Status | Attempts | Sender | Subject | Recipient | Last Modified |",
        "| --- | ---: | --- | --- | --- | --- |",
    ]
    for record in sorted(records, key=lambda item: parse_iso(item.lastModified), reverse=True):
        sender = _md_escape(record.senderName or record.senderEmail)
        subject = _md_escape(record.subject)
        recipient = _md_escape(record.recipientEmail)
        rows.append(
            f"| {record.status} | {record.attempts} | {sender} | {subject} | {recipient} | {record.lastModified} |"
        )
    return "\n".join(rows)


def _atomic_write(path: Path, content: str) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def _find_by_message_id(records: list[UnsubscribeRecord], message_id: str) -> UnsubscribeRecord | None:
    for record in records:
        if record.gmailMessageId == message_id:
            return record
    return None


def _pop_by_id(records: list[UnsubscribeRecord], record_id: str) -> UnsubscribeRecord | None:
    for index, record in enumerate(records):
        if record.id == record_id:
            return records.pop(index)
    return None


def _replace_or_append(records: list[UnsubscribeRecord], record: UnsubscribeRecord) -> list[UnsubscribeRecord]:
    kept = [item for item in records if item.id != record.id]
    kept.append(record)
    return kept


def _md_escape(value: str | None) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ")
