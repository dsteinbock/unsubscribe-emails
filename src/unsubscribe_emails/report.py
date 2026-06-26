from __future__ import annotations

from datetime import timezone, datetime
from html import escape
from pathlib import Path
from urllib.parse import quote

from .models import UnsubscribeRecord
from .store import load_state
from .timeutil import parse_iso

REPORT_PATH = Path("unsubscribe-report.html")


def _fmt_datetime(iso: str | None) -> str:
    if not iso:
        return ""
    dt = parse_iso(iso).astimezone()  # UTC -> local
    return dt.strftime("%m-%d-%Y %I:%M ") + dt.strftime("%p").lower()


def write_report(path: Path = REPORT_PATH) -> Path:
    state = load_state()
    now = datetime.now(timezone.utc)
    review = sorted(state.review, key=lambda record: parse_iso(record.reviewedAt or record.lastModified), reverse=True)
    done = sorted(
        state.done,
        key=lambda record: parse_iso(record.completedAt or record.lastModified),
        reverse=True,
    )
    # Pending: the whole todo queue — both entries that have already failed and
    # those not yet tried. Showing all of them keeps the report honest about
    # what is still outstanding.
    pending = sorted(
        state.todo,
        key=lambda record: parse_iso(record.lastModified),
        reverse=True,
    )
    ignored = sorted(state.ignored_senders)

    review_count = len(state.review)
    pending_count = len(state.todo)
    done_count = len(state.done)
    summary = (
        f"{review_count} need review &middot; {pending_count} pending "
        f"&middot; {done_count} done"
    )

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
    .summary {{
      margin: 4px 0 18px;
      padding: 10px 14px;
      background: var(--panel);
      border: 1px solid var(--line);
      font-size: 14px;
      color: var(--text);
    }}
    .why {{
      color: var(--muted);
      max-width: 360px;
      font-size: 13px;
    }}
    .see-all summary {{
      margin: 10px 0 0;
      color: var(--accent);
      cursor: pointer;
      font-size: 14px;
    }}
  </style>
</head>
<body>
  <main>
    <h1>Auto-Unsubscribe Report</h1>
    <div class="updated">Updated {escape(_fmt_datetime(now.isoformat()))}</div>
    <div class="summary">{summary}</div>
    <section>
      <h2>Needs Manual Review 🤖</h2>
      {_review_table(review)}
    </section>
    <section>
      <h2>Pending ⏳</h2>
      {_pending_table(pending)}
    </section>
    <section>
      <h2>Unsubscribed 😎</h2>
      {_done_table(done)}
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


def _done_row(record: UnsubscribeRecord) -> str:
    return (
        "<tr>"
        f"<td>{escape(_fmt_datetime(record.completedAt or record.lastModified))}</td>"
        f"<td>{escape(record.senderName or record.senderEmail)}</td>"
        f'<td class="subject">{escape(record.subject)}</td>'
        "</tr>"
    )


def _done_table(records: list[UnsubscribeRecord]) -> str:
    if not records:
        return '<div class="empty">No completed unsubscriptions yet.</div>'
    head = "<table><thead><tr><th>Completed</th><th>Sender</th><th>Subject</th></tr></thead><tbody>"
    first = "\n".join([head, *(_done_row(r) for r in records[:10]), "</tbody></table>"])
    if len(records) <= 10:
        return first
    rest = "\n".join([head, *(_done_row(r) for r in records[10:]), "</tbody></table>"])
    return (
        first
        + f'<details class="see-all"><summary>See all {len(records)} '
        f"unsubscribed</summary>{rest}</details>"
    )


def _link(url: str, text: str) -> str:
    return f'<a href="{escape(url, quote=True)}" target="_blank" rel="noreferrer">{escape(text)}</a>'


def _unsubscribe_cell(record: UnsubscribeRecord) -> str:
    """Link to the body fallback when present.

    The header (`List-Unsubscribe`) link is what failed and landed the record
    here; the body link is the one that actually loads a real unsubscribe form,
    so surface it as the primary link (with the header kept as a secondary).
    """
    body = record.unsubscribeUrlFallback
    header = record.unsubscribeUrl
    if body and body != header:
        secondary = f" &middot; {_link(header, 'header')}" if header else ""
        return _link(body, "unsubscribe (body)") + secondary
    if header:
        return _link(header, "unsubscribe")
    return "no link"


def _pending_table(records: list[UnsubscribeRecord]) -> str:
    if not records:
        return '<div class="empty">No entries are mid-retry.</div>'
    rows = [
        "<table><thead><tr><th>Last Tried</th><th>Sender</th><th>Subject</th>"
        "<th>Attempts</th><th>Why</th><th>Unsubscribe</th></tr></thead><tbody>"
    ]
    for record in records:
        why = (record.lastError or "").splitlines()[0] if record.lastError else ""
        rows.append(
            "<tr>"
            f"<td>{escape(_fmt_datetime(record.lastModified))}</td>"
            f"<td>{escape(record.senderName or record.senderEmail)}</td>"
            f'<td class="subject">{escape(record.subject)}</td>'
            f"<td>{record.attempts}</td>"
            f'<td class="why">{escape(why)}</td>'
            f"<td>{_unsubscribe_cell(record)}</td>"
            "</tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)


def _review_table(records: list[UnsubscribeRecord]) -> str:
    if not records:
        return '<div class="empty">No entries need manual review.</div>'
    rows = [
        "<table><thead><tr><th>Last Tried</th><th>Sender</th><th>Subject</th><th>Email</th>"
        "<th>Unsubscribe</th><th>Ignore</th><th>Done</th></tr></thead><tbody>"
    ]
    for record in records:
        sender = record.senderEmail
        ignore_href = f"/ignore?sender={quote(sender)}"
        done_href = f"/done?id={quote(record.id)}"
        rows.append(
            "<tr>"
            f"<td>{escape(_fmt_datetime(record.reviewedAt or record.lastModified))}</td>"
            f"<td>{escape(record.senderName or sender)}</td>"
            f'<td class="subject">{escape(record.subject)}</td>'
            f'<td><a href="{escape(record.gmailUrl, quote=True)}" target="_blank" rel="noreferrer">view email</a></td>'
            f"<td>{_unsubscribe_cell(record)}</td>"
            f'<td><a href="{ignore_href}">ignore</a></td>'
            f'<td><a href="{done_href}">done</a></td>'
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
