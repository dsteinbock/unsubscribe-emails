from __future__ import annotations

import base64
import re
from email import message_from_bytes
from email.message import EmailMessage, Message
from email.policy import default
from email.utils import getaddresses
from html import unescape
from urllib.parse import urlparse

from .models import UnsubscribeRecord
from .timeutil import now_iso

URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
ANGLE_VALUE_RE = re.compile(r"<([^>]+)>")

LINK_TERMS = (
    ("unsubscribe", 100),
    ("opt-out", 90),
    ("opt out", 90),
    ("email preferences", 80),
    ("manage preferences", 80),
    ("subscription preferences", 80),
    ("preferences", 30),
)


def decode_gmail_raw(raw: str) -> EmailMessage:
    padding = "=" * (-len(raw) % 4)
    payload = base64.urlsafe_b64decode(raw + padding)
    return message_from_bytes(payload, policy=default)


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def parse_sender(value: str | None) -> tuple[str, str]:
    parsed = getaddresses([str(value or "")])
    if not parsed:
        return "", ""
    name, email = parsed[0]
    return str(name or "").strip(), normalize_email(email)


def parse_addresses(value: str | None) -> list[str]:
    addresses = []
    for _, email in getaddresses([str(value or "")]):
        normalized = normalize_email(email)
        if normalized and normalized not in addresses:
            addresses.append(normalized)
    return addresses


def choose_recipient(to_emails: list[str], known_recipient_emails: list[str]) -> str:
    known = [normalize_email(email) for email in known_recipient_emails]
    for email in to_emails:
        if email in known:
            return email
    return to_emails[0] if to_emails else ""


def parse_list_unsubscribe(header_value: str | None) -> tuple[str | None, str | None]:
    if not header_value:
        return None, None
    candidates = ANGLE_VALUE_RE.findall(header_value)
    if not candidates:
        candidates = [part.strip() for part in header_value.split(",")]

    urls: list[str] = []
    mailtos: list[str] = []
    for candidate in candidates:
        value = candidate.strip().strip("<>").strip()
        if value.lower().startswith(("http://", "https://")):
            urls.append(value)
        elif value.lower().startswith("mailto:"):
            mailtos.append(value)
    return (urls[0] if urls else None, mailtos[0] if mailtos else None)


def supports_one_click(header_value: str | None) -> bool:
    """True when the RFC 8058 ``List-Unsubscribe-Post`` header opts into one-click."""
    return "one-click" in str(header_value or "").lower()


def extract_body_unsubscribe_link(message: Message) -> str | None:
    candidates: list[tuple[int, str]] = []
    for part in _iter_text_parts(message):
        content_type = part.get_content_type()
        try:
            content = part.get_content()
        except Exception:
            continue
        if not isinstance(content, str):
            continue

        if content_type == "text/html":
            candidates.extend(_html_link_candidates(content))
        else:
            candidates.extend(_plain_link_candidates(content))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def build_record_from_message(
    gmail_message_id: str,
    gmail_thread_id: str,
    message: Message,
    known_recipient_emails: list[str] | None = None,
) -> UnsubscribeRecord:
    known_recipient_emails = known_recipient_emails or []
    sender_name, sender_email = parse_sender(message.get("From"))
    to_emails = parse_addresses(message.get("To"))
    recipient_email = choose_recipient(to_emails, known_recipient_emails)
    subject = str(message.get("Subject") or "")
    header_url, mailto = parse_list_unsubscribe(message.get("List-Unsubscribe"))
    body_url = extract_body_unsubscribe_link(message)
    unsubscribe_url = header_url or body_url
    source = "list-unsubscribe" if header_url else ("body" if body_url else None)
    # When a header link exists, keep a distinct body link as a fallback to try
    # if the header link errors out (expired token, POST-only endpoint, etc.).
    fallback_url = body_url if (header_url and body_url and body_url != header_url) else None
    one_click = bool(header_url) and supports_one_click(message.get("List-Unsubscribe-Post"))
    timestamp = now_iso()

    return UnsubscribeRecord(
        id=gmail_message_id,
        status="todo",
        attempts=0,
        lastModified=timestamp,
        gmailMessageId=gmail_message_id,
        gmailThreadId=gmail_thread_id,
        gmailUrl=f"https://mail.google.com/mail/u/0/#all/{gmail_thread_id}",
        senderName=sender_name,
        senderEmail=sender_email,
        toEmails=to_emails,
        recipientEmail=recipient_email,
        subject=subject,
        unsubscribeUrl=unsubscribe_url,
        unsubscribeUrlFallback=fallback_url,
        unsubscribeMailto=mailto,
        unsubscribeSource=source,
        oneClick=one_click,
        createdAt=timestamp,
    )


def _iter_text_parts(message: Message):
    if message.is_multipart():
        for part in message.walk():
            if part.is_multipart():
                continue
            if part.get_content_type() in {"text/html", "text/plain"}:
                yield part
    elif message.get_content_type() in {"text/html", "text/plain"}:
        yield message


def _html_link_candidates(content: str) -> list[tuple[int, str]]:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return _plain_link_candidates(unescape(re.sub(r"<[^>]+>", " ", content)))

    candidates: list[tuple[int, str]] = []
    soup = BeautifulSoup(content, "html.parser")
    for anchor in soup.find_all("a"):
        href = str(anchor.get("href") or "").strip()
        if not _is_http_url(href):
            continue
        text = " ".join(anchor.get_text(" ", strip=True).split())
        score = _score_link(text, href)
        if score > 0:
            candidates.append((score, href))
    return candidates


def _plain_link_candidates(content: str) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = []
    for match in URL_RE.finditer(content):
        url = match.group(0).rstrip(").,;]")
        if not _is_http_url(url):
            continue
        start = max(0, match.start() - 80)
        end = min(len(content), match.end() + 80)
        context = content[start:end]
        score = _score_link(context, url)
        if score > 0:
            candidates.append((score, url))
    return candidates


def _score_link(text: str, href: str) -> int:
    haystack = f"{text} {href}".lower().replace("%20", " ")
    score = 0
    for term, weight in LINK_TERMS:
        if term in haystack:
            score = max(score, weight)
    parsed = urlparse(href)
    if "unsubscribe" in parsed.path.lower():
        score += 20
    return score


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
