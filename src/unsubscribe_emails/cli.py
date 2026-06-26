from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_config
from .gmail_client import audit_labeled_messages, ingest_from_gmail
from .report import write_report
from .server import run_review_server
from .store import ignore_sender, mark_done, mark_retry, next_records, save_state, load_state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="unsubscribe")
    parser.add_argument("--config", default="config.toml", help="Path to optional TOML config")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="Fetch new Gmail candidates into unsubscribe-todo.md")
    ingest.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on eligible unlabeled messages to ingest. Defaults to no cap.",
    )
    ingest.add_argument("--source-label")
    ingest.add_argument("--processed-label")

    next_parser = subparsers.add_parser("next", help="Print compact JSON entries for agentic browser work")
    next_parser.add_argument("--limit", type=int, default=10)

    done = subparsers.add_parser("mark-done", help="Move an entry to unsubscribe-done.md")
    done.add_argument("entry_id")

    retry = subparsers.add_parser("mark-retry", help="Increment attempts and maybe move to review")
    retry.add_argument("entry_id")
    retry.add_argument("--reason", required=True)

    subparsers.add_parser("report", help="Regenerate unsubscribe-report.html")

    server = subparsers.add_parser("review-server", help="Serve the report with working ignore links")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)

    ignore = subparsers.add_parser("ignore-sender", help="Add a sender to ignored-senders.md")
    ignore.add_argument("sender_email")

    subparsers.add_parser("init-files", help="Create empty managed Markdown files")

    audit = subparsers.add_parser(
        "audit", help="Find #auto-unsubscribe emails not accounted for in state files"
    )
    audit.add_argument("--processed-label")
    audit.add_argument(
        "--unlabel",
        action="store_true",
        help="Remove the processed label from all unaccounted messages",
    )

    args = parser.parse_args(argv)
    config = load_config(Path(args.config))

    if args.command == "ingest":
        result = ingest_from_gmail(
            config=config,
            limit=args.limit,
            source_label=args.source_label,
            processed_label=args.processed_label,
        )
        write_report()
        _print_json(result)
        return 0

    if args.command == "next":
        _print_json(next_records(args.limit))
        return 0

    if args.command == "mark-done":
        record = mark_done(args.entry_id)
        write_report()
        _print_json(record.to_dict())
        return 0

    if args.command == "mark-retry":
        record = mark_retry(args.entry_id, args.reason)
        write_report()
        _print_json(record.to_dict())
        return 0

    if args.command == "report":
        path = write_report()
        print(path)
        return 0

    if args.command == "review-server":
        run_review_server(host=args.host, port=args.port)
        return 0

    if args.command == "ignore-sender":
        result = ignore_sender(args.sender_email)
        write_report()
        _print_json(result)
        return 0

    if args.command == "init-files":
        save_state(load_state())
        write_report()
        print("Initialized unsubscribe markdown files and report.")
        return 0

    if args.command == "audit":
        unaccounted = audit_labeled_messages(
            config=config,
            processed_label=args.processed_label,
            unlabel=args.unlabel,
        )
        if not unaccounted:
            print("All #auto-unsubscribe emails are accounted for.")
        else:
            verb = "Unlabeled" if args.unlabel else "Found"
            print(f"{verb} {len(unaccounted)} unaccounted message(s):")
            _print_json(unaccounted)
        return 0

    parser.error(f"Unknown command {args.command!r}")
    return 2


def _print_json(value) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))
