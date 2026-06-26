# Auto-Unsubscribe System

This repo contains a mostly programmatic Gmail unsubscribe workflow. Scripts do deterministic work: Gmail ingestion, RFC header parsing, unsubscribe-link extraction, state movement, ignored senders, and report generation. An agentic browser is only needed for browser judgment and unsubscribe-page interaction.

## Setup

1. Create an OAuth desktop client in Google Cloud with Gmail API enabled.
2. Save the downloaded OAuth client file as `secrets/client_secret.json`.
3. Optionally copy `config.example.toml` to `config.toml` and add known recipient addresses.
4. Install/sync dependencies:

```bash
uv sync --extra dev
```

The first Gmail command opens the local OAuth flow and writes `secrets/token.json`.

## Commands

```bash
uv run unsubscribe ingest
uv run unsubscribe next --limit 10
uv run unsubscribe mark-done <entry-id>
uv run unsubscribe mark-retry <entry-id> --reason "Could not find confirmation button"
uv run unsubscribe report
uv run unsubscribe review-server --port 8765
```

`unsubscribe ingest` scans all eligible Gmail messages by default. `unsubscribe next --limit 10` is the browser workload throttle and prints compact JSON for agentic browser processing, deduplicated by sender email. After the agent completes or fails an unsubscribe page, use `mark-done` or `mark-retry`.

See [AGENT_WORKFLOW.md](AGENT_WORKFLOW.md) for the complete agent workflow.

## Files

- `unsubscribe-todo.md`: pending and retryable entries
- `unsubscribe-done.md`: successful unsubscriptions
- `unsubscribe-review.md`: entries that reached 3 attempts
- `ignored-senders.md`: normalized sender emails excluded from future processing
- `unsubscribe-report.html`: generated review/report page

The Markdown files contain a machine-managed JSON block plus a readable table. Edit with care; rerunning commands regenerates the readable tables from JSON.
