from __future__ import annotations

from datetime import timedelta, timezone, datetime
from html import escape
from pathlib import Path
from urllib.parse import quote

from .models import UnsubscribeRecord
from .store import load_state
from .timeutil import parse_iso

REPORT_PATH = Path("unsubscribe-report.html")


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return ""
    return parse_iso(iso).strftime("%m-%d-%Y")


def write_report(path: Path = REPORT_PATH) -> Path:
    state = load_state()
    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(days=7)
    recent_done = [
        record
        for record in state.done
        if parse_iso(record.completedAt or record.lastModified) >= recent_cutoff
    ]
    recent_done.sort(key=lambda record: parse_iso(record.completedAt or record.lastModified), reverse=True)
    review = sorted(state.review, key=lambda record: parse_iso(record.reviewedAt or record.lastModified), reverse=True)
    recent_done_ids = {r.id for r in recent_done}
    archive_done = [r for r in state.done if r.id not in recent_done_ids]
    done = sorted(archive_done, key=lambda record: parse_iso(record.completedAt or record.lastModified), reverse=True)
    ignored = sorted(state.ignored_senders)

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Auto-Unsubscribe Report</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f7f7f4;
      --text: #202124;
      --muted: #686b70;
      --line: #dad7ce;
      --accent: #0b6b5d;
      --panel: #ffffff;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #171817;
        --text: #f2f0ea;
        --muted: #b8b5aa;
        --line: #3a3a35;
        --accent: #70d7c5;
        --panel: #20211f;
      }}
    }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 15px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      max-width: 1120px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 32px;
      letter-spacing: 0;
    }}
    .updated {{
      color: var(--muted);
      margin-bottom: 28px;
    }}
    section {{
      margin-top: 34px;
    }}
    h2 {{
      margin: 0 0 14px;
      font-size: 22px;
      letter-spacing: 0;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .04em;
    }}
    a {{
      color: var(--accent);
      text-decoration-thickness: 1px;
    }}
    .empty {{
      color: var(--muted);
      background: var(--panel);
      border: 1px solid var(--line);
      padding: 14px 16px;
    }}
    .subject {{
      max-width: 520px;
    }}
  </style>
</head>
<body>
  <main>
    <h1>Auto-Unsubscribe Report</h1>
    <div class="updated">Updated {escape(now.isoformat(timespec="seconds"))}</div>
    <section>
      <h2>Unsubscribed 😎</h2>
      {_recent_done_table(recent_done)}
    </section>
    <section>
      <h2>Needs Manual Review 🤖</h2>
      {_review_table(review)}
    </section>
    <section>
      <h2>Unsubscription Archive 💀</h2>
      {_archive_table(done)}
    </section>
    <section>
      <h2>Ignored</h2>
      {_ignored_table(ignored)}
    </section>
  </main>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")
    return path


def _recent_done_table(records: list[UnsubscribeRecord]) -> str:
    if not records:
        return '<div class="empty">No successful unsubscriptions in the last 7 days.</div>'
    rows = ["<table><thead><tr><th>Completed</th><th>Sender</th><th>Subject</th></tr></thead><tbody>"]
    for record in records:
        rows.append(
            "<tr>"
            f"<td>{escape(_fmt_date(record.completedAt or record.lastModified))}</td>"
            f"<td>{escape(record.senderName or record.senderEmail)}</td>"
            f'<td class="subject">{escape(record.subject)}</td>'
            "</tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)


def _review_table(records: list[UnsubscribeRecord]) -> str:
    if not records:
        return '<div class="empty">No entries need manual review.</div>'
    rows = [
        "<table><thead><tr><th>Last Tried</th><th>Sender</th><th>Subject</th><th>Email</th><th>Unsubscribe</th><th>Ignore</th></tr></thead><tbody>"
    ]
    for record in records:
        unsubscribe = (
            f'<a href="{escape(record.unsubscribeUrl, quote=True)}" target="_blank" rel="noreferrer">unsubscribe</a>'
            if record.unsubscribeUrl
            else "no link"
        )
        sender = record.senderEmail
        ignore_href = f"/ignore?sender={quote(sender)}"
        rows.append(
            "<tr>"
            f"<td>{escape(_fmt_date(record.reviewedAt or record.lastModified))}</td>"
            f"<td>{escape(record.senderName or sender)}</td>"
            f'<td class="subject">{escape(record.subject)}</td>'
            f'<td><a href="{escape(record.gmailUrl, quote=True)}" target="_blank" rel="noreferrer">view email</a></td>'
            f"<td>{unsubscribe}</td>"
            f'<td><a href="{ignore_href}">ignore</a></td>'
            "</tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)


def _archive_table(records: list[UnsubscribeRecord]) -> str:
    if not records:
        return '<div class="empty">No completed unsubscriptions yet.</div>'
    rows = ["<table><thead><tr><th>Completed</th><th>Sender</th><th>Subject</th></tr></thead><tbody>"]
    for record in records:
        rows.append(
            "<tr>"
            f"<td>{escape(_fmt_date(record.completedAt or record.lastModified))}</td>"
            f"<td>{escape(record.senderName or record.senderEmail)}</td>"
            f'<td class="subject">{escape(record.subject)}</td>'
            "</tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)


def _ignored_table(senders: list[str]) -> str:
    if not senders:
        return '<div class="empty">No ignored senders.</div>'
    rows = ["<table><thead><tr><th>Sender</th></tr></thead><tbody>"]
    for sender in senders:
        rows.append(f"<tr><td>{escape(sender)}</td></tr>")
    rows.append("</tbody></table>")
    return "\n".join(rows)
