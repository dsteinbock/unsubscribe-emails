from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Config
from .extract import build_record_from_message, decode_gmail_raw, normalize_email, parse_sender
from .store import is_ignored_sender, load_state, upsert_todo_record

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
CLIENT_SECRET_PATH = Path("secrets/client_secret.json")
TOKEN_PATH = Path("secrets/token.json")


def ingest_from_gmail(
    config: Config,
    limit: int | None = None,
    source_label: str | None = None,
    processed_label: str | None = None,
) -> dict[str, Any]:
    source_label = source_label or config.gmail.source_label
    processed_label = processed_label or config.gmail.processed_label
    service = _build_service()
    labels = _label_map(service)
    source_label_id = _require_label(labels, source_label)
    processed_label_id = _ensure_label(service, labels, processed_label)

    summary = {
        "seen": 0,
        "eligible": 0,
        "created": 0,
        "updated": 0,
        "ignored": 0,
        "skippedProcessed": 0,
        "skippedExistingTerminal": 0,
        "skippedDuplicate": 0,
        "labeled": 0,
    }
    if limit is not None and limit <= 0:
        return summary

    seen_sender_emails: set[str] = set()
    eligible_count = 0

    for message_stub in _iter_message_stubs(service, source_label_id):
        summary["seen"] += 1
        message_id = message_stub["id"]
        raw_message = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="raw")
            .execute()
        )
        label_ids = set(raw_message.get("labelIds", []))
        if processed_label_id in label_ids:
            summary["skippedProcessed"] += 1
            continue

        eligible_count += 1
        summary["eligible"] = eligible_count

        parsed_message = decode_gmail_raw(raw_message["raw"])
        record = build_record_from_message(
            gmail_message_id=raw_message["id"],
            gmail_thread_id=raw_message["threadId"],
            message=parsed_message,
            known_recipient_emails=config.known_recipient_emails,
        )

        if is_ignored_sender(record.senderEmail):
            _apply_label(service, raw_message["id"], processed_label_id)
            summary["ignored"] += 1
            summary["labeled"] += 1
            continue

        if record.senderEmail in seen_sender_emails:
            _apply_label(service, raw_message["id"], processed_label_id)
            summary["skippedDuplicate"] += 1
            summary["labeled"] += 1
            continue
        seen_sender_emails.add(record.senderEmail)

        result = upsert_todo_record(record)
        if result == "created":
            summary["created"] += 1
        elif result == "updated":
            summary["updated"] += 1
        else:
            summary["skippedExistingTerminal"] += 1

        _apply_label(service, raw_message["id"], processed_label_id)
        summary["labeled"] += 1

        if limit is not None and eligible_count >= limit:
            break

    return summary


def _build_service():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Gmail dependencies are not installed. Run `uv sync` or `uv run unsubscribe ingest` from this project."
        ) from exc

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CLIENT_SECRET_PATH.exists():
                raise FileNotFoundError(
                    f"Missing OAuth client file at {CLIENT_SECRET_PATH}. See README setup instructions."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds)


def _label_map(service) -> dict[str, str]:
    response = service.users().labels().list(userId="me").execute()
    return {label["name"]: label["id"] for label in response.get("labels", [])}


def _require_label(labels: dict[str, str], name: str) -> str:
    if name not in labels:
        raise KeyError(f"Gmail label {name!r} was not found")
    return labels[name]


def _ensure_label(service, labels: dict[str, str], name: str) -> str:
    if name in labels:
        return labels[name]
    created = (
        service.users()
        .labels()
        .create(
            userId="me",
            body={
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        .execute()
    )
    labels[name] = created["id"]
    return created["id"]


def _iter_message_stubs(service, source_label_id: str):
    page_token = None
    while True:
        request = (
            service.users()
            .messages()
            .list(userId="me", labelIds=[source_label_id], maxResults=100, pageToken=page_token)
        )
        response = request.execute()
        messages = response.get("messages", [])
        if not messages:
            return
        for message in messages:
            yield message
        page_token = response.get("nextPageToken")
        if not page_token:
            return


def audit_labeled_messages(
    config: Config,
    processed_label: str | None = None,
    unlabel: bool = False,
) -> list[dict[str, Any]]:
    """Return messages labeled #auto-unsubscribe that aren't accounted for in state files."""
    processed_label = processed_label or config.gmail.processed_label
    service = _build_service()
    labels = _label_map(service)
    processed_label_id = _require_label(labels, processed_label)

    state = load_state()
    all_records = state.todo + state.done + state.review
    known_message_ids: set[str] = {r.gmailMessageId for r in all_records}
    known_sender_emails: set[str] = {normalize_email(r.senderEmail) for r in all_records}
    ignored_senders: set[str] = set(state.ignored_senders)

    unaccounted: list[dict[str, Any]] = []

    for message_stub in _iter_message_stubs(service, processed_label_id):
        message_id = message_stub["id"]
        if message_id in known_message_ids:
            continue

        raw_message = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="raw")
            .execute()
        )
        parsed = decode_gmail_raw(raw_message["raw"])
        sender_name, sender_email = parse_sender(parsed.get("From"))

        normalized_sender = normalize_email(sender_email)
        if normalized_sender in ignored_senders:
            continue
        if normalized_sender in known_sender_emails:
            continue

        thread_id = raw_message.get("threadId", "")
        unaccounted.append({
            "gmailMessageId": message_id,
            "gmailThreadId": thread_id,
            "gmailUrl": f"https://mail.google.com/mail/u/0/#all/{thread_id}",
            "senderName": sender_name,
            "senderEmail": sender_email,
            "subject": str(parsed.get("Subject") or ""),
        })

    if unlabel:
        for entry in unaccounted:
            (
                service.users()
                .messages()
                .modify(
                    userId="me",
                    id=entry["gmailMessageId"],
                    body={"removeLabelIds": [processed_label_id]},
                )
                .execute()
            )

    return unaccounted


def _apply_label(service, message_id: str, label_id: str) -> None:
    (
        service.users()
        .messages()
        .modify(userId="me", id=message_id, body={"addLabelIds": [label_id]})
        .execute()
    )
