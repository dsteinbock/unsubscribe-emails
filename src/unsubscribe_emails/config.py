from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class GmailConfig:
    source_label: str = "@SaneBlackHole"
    processed_label: str = "#auto-unsubscribe"


@dataclass(frozen=True)
class Config:
    known_recipient_emails: list[str] = field(default_factory=list)
    gmail: GmailConfig = field(default_factory=GmailConfig)


def load_config(path: Path = Path("config.toml")) -> Config:
    if not path.exists():
        return Config()

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    gmail_data = data.get("gmail", {})
    return Config(
        known_recipient_emails=[
            str(email).strip().lower()
            for email in data.get("known_recipient_emails", [])
            if str(email).strip()
        ],
        gmail=GmailConfig(
            source_label=str(gmail_data.get("source_label", "@SaneBlackHole")),
            processed_label=str(gmail_data.get("processed_label", "#auto-unsubscribe")),
        ),
    )
