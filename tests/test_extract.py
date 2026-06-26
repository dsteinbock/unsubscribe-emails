from __future__ import annotations

import base64
from email.message import EmailMessage

from unsubscribe_emails.extract import (
    build_record_from_message,
    decode_gmail_raw,
    extract_body_unsubscribe_link,
    parse_list_unsubscribe,
)


def test_parse_headers_and_prefer_known_recipient():
    message = EmailMessage()
    message["From"] = "=?utf-8?q?Jos=C3=A9_Sender?= <Sender@Example.COM>"
    message["To"] = "first@example.com, Alias <second@example.com>"
    message["Subject"] = "=?utf-8?q?A_special_offer?="
    message["List-Unsubscribe"] = "<mailto:leave@example.com>, <https://example.com/unsub?id=1>"
    message.set_content("Hello")

    record = build_record_from_message("m1", "t1", message, ["second@example.com"])

    assert record.senderName == "José Sender"
    assert record.senderEmail == "sender@example.com"
    assert record.toEmails == ["first@example.com", "second@example.com"]
    assert record.recipientEmail == "second@example.com"
    assert record.subject == "A special offer"
    assert record.unsubscribeUrl == "https://example.com/unsub?id=1"
    assert record.unsubscribeMailto == "mailto:leave@example.com"
    assert record.unsubscribeSource == "list-unsubscribe"


def test_one_click_flag_set_only_with_post_header_and_url():
    message = EmailMessage()
    message["From"] = "Sender <sender@example.com>"
    message["To"] = "me@example.com"
    message["List-Unsubscribe"] = "<https://example.com/unsub?id=1>"
    message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message.set_content("Hello")

    record = build_record_from_message("m1", "t1", message)
    assert record.oneClick is True


def test_one_click_flag_false_without_post_header():
    message = EmailMessage()
    message["From"] = "Sender <sender@example.com>"
    message["To"] = "me@example.com"
    message["List-Unsubscribe"] = "<https://example.com/unsub?id=1>"
    message.set_content("Hello")

    record = build_record_from_message("m1", "t1", message)
    assert record.oneClick is False


def test_body_fallback_set_when_body_link_differs_from_header():
    message = EmailMessage()
    message["From"] = "Sender <sender@example.com>"
    message["To"] = "me@example.com"
    message["List-Unsubscribe"] = "<https://esp.example.com/header-unsub?id=1>"
    message.set_content("plain text")
    message.add_alternative(
        '<html><body><a href="https://esp.example.com/body-unsub?id=1">Unsubscribe</a>'
        "</body></html>",
        subtype="html",
    )

    record = build_record_from_message("m1", "t1", message)

    assert record.unsubscribeUrl == "https://esp.example.com/header-unsub?id=1"
    assert record.unsubscribeUrlFallback == "https://esp.example.com/body-unsub?id=1"


def test_body_fallback_none_when_only_header_present():
    message = EmailMessage()
    message["From"] = "Sender <sender@example.com>"
    message["To"] = "me@example.com"
    message["List-Unsubscribe"] = "<https://esp.example.com/header-unsub?id=1>"
    message.set_content("No links in this body at all.")

    record = build_record_from_message("m1", "t1", message)

    assert record.unsubscribeUrl == "https://esp.example.com/header-unsub?id=1"
    assert record.unsubscribeUrlFallback is None


def test_parse_list_unsubscribe_keeps_mailto_when_no_url():
    url, mailto = parse_list_unsubscribe("<mailto:unsubscribe@example.com?subject=remove>")

    assert url is None
    assert mailto == "mailto:unsubscribe@example.com?subject=remove"


def test_extract_html_body_unsubscribe_link():
    message = EmailMessage()
    message.set_content("Plain fallback")
    message.add_alternative(
        """
        <html><body>
          <a href="https://example.com/view">View online</a>
          <a href="https://example.com/email-preferences">Manage preferences</a>
          <a href="https://example.com/unsubscribe">Unsubscribe</a>
        </body></html>
        """,
        subtype="html",
    )

    assert extract_body_unsubscribe_link(message) == "https://example.com/unsubscribe"


def test_extract_plain_text_unsubscribe_link():
    message = EmailMessage()
    message.set_content("To opt out, visit https://example.com/preferences/unsubscribe.")

    assert extract_body_unsubscribe_link(message) == "https://example.com/preferences/unsubscribe"


def test_decode_gmail_raw_round_trip():
    message = EmailMessage()
    message["From"] = "Sender <sender@example.com>"
    message.set_content("Hello")
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")

    parsed = decode_gmail_raw(raw)

    assert parsed.get("From") == "Sender <sender@example.com>"
