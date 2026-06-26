# Auto-Unsubscribe System

This repo contains a mostly programmatic Gmail unsubscribe workflow. Scripts do the deterministic work: Gmail ingestion, RFC header parsing, unsubscribe-link extraction, state movement, ignored senders, report generation, and — via `unsubscribe run` — the unsubscribe browser work itself (RFC 8058 one-click POST, open-and-confirm, and heuristic unsubscribe/confirm clicks with no LLM tokens). An orchestrator agent is only needed for the few ambiguous pages the worker hands back as a structured `needs-agent` list.

## Setup

1. Create an OAuth desktop client in Google Cloud with Gmail API enabled.
2. Save the downloaded OAuth client file as `secrets/client_secret.json`.
3. Optionally copy `config.example.toml` to `config.toml` and add known recipient addresses.
4. Install/sync dependencies and the Chromium browser used by `run`:

```bash
uv sync --extra dev
uv run playwright install chromium
```

The first Gmail command opens the local OAuth flow and writes `secrets/token.json`.

## Commands

```bash
uv run unsubscribe ingest
uv run unsubscribe run                   # deterministic worker (whole queue); prints a needs-agent handoff
uv run unsubscribe next --limit 10       # compact queue JSON (no processing)
uv run unsubscribe browse <entry-id> --fill-email --click "Unsubscribe"  # drive one page via Playwright, print JSON
uv run unsubscribe mark-done <entry-id>
uv run unsubscribe mark-retry <entry-id> --reason "Could not find confirmation button"
uv run unsubscribe report
uv run unsubscribe review-server --port 8765   # auto-opens the report in a browser; add --no-open to suppress
```

`unsubscribe ingest` scans all eligible Gmail messages by default. `unsubscribe run` deterministically completes the easy unsubscribes across the whole queue (calling `mark-done`/`mark-retry` itself) and prints a JSON summary whose `needsAgentEntries` lists the ambiguous pages an orchestrator agent should finish in a browser. The worker auto-handles already-confirmed pages, HTTP/error pages (expired tokens, 405s), and — when a header `List-Unsubscribe` link fails — retries a fallback unsubscribe link scraped from the email body. Deterministic work is unbounded; only the agent handoff is throttled, via `--agent-limit` (default 10). `unsubscribe next` just prints the compact queue without processing it. For the few leftover pages, `unsubscribe browse <entry-id>` opens the page with Playwright and prints `{title, snippet, candidates, success}` as JSON (add `--fill-email`, `--check NAME`, `--click NAME` to act) — a token-cheap browser primitive that needs no MCP/extension. After completing or failing a page, use `mark-done` or `mark-retry`.

See [AGENT_WORKFLOW.md](AGENT_WORKFLOW.md) for the complete agent workflow.

## Files

- `unsubscribe-todo.md`: pending and retryable entries
- `unsubscribe-done.md`: successful unsubscriptions
- `unsubscribe-review.md`: entries that reached 3 attempts
- `ignored-senders.md`: normalized sender emails excluded from future processing
- `unsubscribe-report.html`: generated review/report page

The Markdown files contain a machine-managed JSON block plus a readable table. Edit with care; rerunning commands regenerates the readable tables from JSON.
