from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UnsubscribeRecord:
    id: str
    status: str
    attempts: int
    lastModified: str
    gmailMessageId: str
    gmailThreadId: str
    gmailUrl: str
    senderName: str
    senderEmail: str
    toEmails: list[str]
    recipientEmail: str
    subject: str
    unsubscribeUrl: str | None = None
    unsubscribeUrlFallback: str | None = None
    unsubscribeMailto: str | None = None
    unsubscribeSource: str | None = None
    oneClick: bool = False
    createdAt: str | None = None
    completedAt: str | None = None
    reviewedAt: str | None = None
    lastError: str | None = None
    notes: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UnsubscribeRecord":
        values = dict(data)
        values.setdefault("unsubscribeUrl", None)
        values.setdefault("unsubscribeUrlFallback", None)
        values.setdefault("unsubscribeMailto", None)
        values.setdefault("unsubscribeSource", None)
        values["oneClick"] = bool(values.get("oneClick", False))
        values.setdefault("createdAt", None)
        values.setdefault("completedAt", None)
        values.setdefault("reviewedAt", None)
        values.setdefault("lastError", None)
        values.setdefault("notes", None)
        values["attempts"] = int(values.get("attempts", 0))
        values["toEmails"] = list(values.get("toEmails", []))
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "attempts": self.attempts,
            "lastModified": self.lastModified,
            "gmailMessageId": self.gmailMessageId,
            "gmailThreadId": self.gmailThreadId,
            "gmailUrl": self.gmailUrl,
            "senderName": self.senderName,
            "senderEmail": self.senderEmail,
            "toEmails": self.toEmails,
            "recipientEmail": self.recipientEmail,
            "subject": self.subject,
            "unsubscribeUrl": self.unsubscribeUrl,
            "unsubscribeUrlFallback": self.unsubscribeUrlFallback,
            "unsubscribeMailto": self.unsubscribeMailto,
            "unsubscribeSource": self.unsubscribeSource,
            "oneClick": self.oneClick,
            "createdAt": self.createdAt,
            "completedAt": self.completedAt,
            "reviewedAt": self.reviewedAt,
            "lastError": self.lastError,
            "notes": self.notes,
        }

    def compact_for_browser(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "senderName": self.senderName,
            "senderEmail": self.senderEmail,
            "subject": self.subject,
            "recipientEmail": self.recipientEmail,
            "gmailUrl": self.gmailUrl,
            "unsubscribeUrl": self.unsubscribeUrl,
            "unsubscribeUrlFallback": self.unsubscribeUrlFallback,
            "attempts": self.attempts,
        }


@dataclass
class UnsubscribeState:
    todo: list[UnsubscribeRecord] = field(default_factory=list)
    done: list[UnsubscribeRecord] = field(default_factory=list)
    review: list[UnsubscribeRecord] = field(default_factory=list)
    ignored_senders: list[str] = field(default_factory=list)
