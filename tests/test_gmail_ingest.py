from __future__ import annotations

import base64
from email.message import EmailMessage

from unsubscribe_emails.config import Config
from unsubscribe_emails.gmail_client import ingest_from_gmail
from unsubscribe_emails.store import load_state, write_ignored_senders


class Request:
    def __init__(self, payload, on_execute=None):
        self.payload = payload
        self.on_execute = on_execute

    def execute(self):
        if self.on_execute:
            self.on_execute()
        return self.payload


class FakeLabels:
    def __init__(self):
        self.labels = [
            {"name": "@SaneBlackHole", "id": "source"},
            {"name": "#auto-unsubscribe", "id": "processed"},
        ]

    def list(self, userId):  # noqa: N803
        return Request({"labels": self.labels})

    def create(self, userId, body):  # noqa: N803
        created = {"name": body["name"], "id": f"label-{body['name']}"}
        self.labels.append(created)
        return Request(created)


class FakeMessages:
    def __init__(self, raw_messages):
        self.raw_messages = raw_messages
        self.modified_ids = []
        self.require_todo_before_label = True

    def list(self, userId, labelIds, maxResults, pageToken=None):  # noqa: N803
        message_ids = list(self.raw_messages)
        start = int(pageToken or 0)
        end = start + maxResults
        payload = {"messages": [{"id": message_id} for message_id in message_ids[start:end]]}
        if end < len(message_ids):
            payload["nextPageToken"] = str(end)
        return Request(payload)

    def get(self, userId, id, format):  # noqa: A002, N803
        return Request(self.raw_messages[id])

    def modify(self, userId, id, body):  # noqa: A002, N803
        def assert_local_write_happened_before_label():
            state = load_state()
            if self.require_todo_before_label and id == "m1":
                assert [record.id for record in state.todo] == ["m1"]
            self.modified_ids.append((id, body))

        return Request({}, assert_local_write_happened_before_label)


class FakeUsers:
    def __init__(self, raw_messages):
        self._labels = FakeLabels()
        self._messages = FakeMessages(raw_messages)

    def labels(self):
        return self._labels

    def messages(self):
        return self._messages


class FakeService:
    def __init__(self, raw_messages):
        self.users_resource = FakeUsers(raw_messages)

    def users(self):
        return self.users_resource


def raw_message(message: EmailMessage, message_id="m1", thread_id="t1", labels=None):
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
    return {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": labels or ["source"],
        "raw": raw,
    }


def make_email(sender="News <news@example.com>"):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "me@example.com"
    message["Subject"] = "Newsletter"
    message["List-Unsubscribe"] = "<https://example.com/unsub>"
    message.set_content("Hello")
    return message


def test_ingest_writes_todo_before_labeling_message(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake = FakeService({"m1": raw_message(make_email())})
    monkeypatch.setattr("unsubscribe_emails.gmail_client._build_service", lambda: fake)

    result = ingest_from_gmail(Config(), limit=1)
    state = load_state()

    assert result["created"] == 1
    assert result["labeled"] == 1
    assert state.todo[0].senderEmail == "news@example.com"
    assert fake.users_resource._messages.modified_ids == [("m1", {"addLabelIds": ["processed"]})]


def test_ingest_ignored_sender_labels_without_creating_todo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_ignored_senders(tmp_path / "ignored-senders.md", ["news@example.com"])
    fake = FakeService({"m1": raw_message(make_email())})
    fake.users_resource._messages.require_todo_before_label = False
    monkeypatch.setattr("unsubscribe_emails.gmail_client._build_service", lambda: fake)

    result = ingest_from_gmail(Config(), limit=1)
    state = load_state()

    assert result["ignored"] == 1
    assert state.todo == []
    assert fake.users_resource._messages.modified_ids == [("m1", {"addLabelIds": ["processed"]})]


def test_ingest_skips_already_processed_message(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake = FakeService({"m1": raw_message(make_email(), labels=["source", "processed"])})
    monkeypatch.setattr("unsubscribe_emails.gmail_client._build_service", lambda: fake)

    result = ingest_from_gmail(Config(), limit=1)

    assert result["skippedProcessed"] == 1
    assert fake.users_resource._messages.modified_ids == []


def test_ingest_limit_counts_eligible_messages_not_processed_messages(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake = FakeService(
        {
            "processed-1": raw_message(make_email(sender="Old <old@example.com>"), message_id="processed-1", labels=["source", "processed"]),
            "processed-2": raw_message(make_email(sender="Old <old2@example.com>"), message_id="processed-2", labels=["source", "processed"]),
            "eligible": raw_message(make_email(sender="News <news@example.com>"), message_id="eligible"),
        }
    )
    monkeypatch.setattr("unsubscribe_emails.gmail_client._build_service", lambda: fake)

    result = ingest_from_gmail(Config(), limit=1)
    state = load_state()

    assert result["seen"] == 3
    assert result["eligible"] == 1
    assert result["skippedProcessed"] == 2
    assert result["created"] == 1
    assert [record.id for record in state.todo] == ["eligible"]
    assert fake.users_resource._messages.modified_ids == [("eligible", {"addLabelIds": ["processed"]})]


def test_ingest_deduplicates_by_sender_with_newest_first(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake = FakeService(
        {
            "newest": raw_message(make_email(sender="News <news@example.com>"), message_id="newest"),
            "older": raw_message(make_email(sender="News <news@example.com>"), message_id="older"),
        }
    )
    fake.users_resource._messages.require_todo_before_label = False
    monkeypatch.setattr("unsubscribe_emails.gmail_client._build_service", lambda: fake)

    result = ingest_from_gmail(Config(), limit=10)
    state = load_state()

    assert result["created"] == 1
    assert result["skippedDuplicate"] == 1
    assert [record.id for record in state.todo] == ["newest"]
    assert fake.users_resource._messages.modified_ids == [
        ("newest", {"addLabelIds": ["processed"]}),
        ("older", {"addLabelIds": ["processed"]}),
    ]
