# Complete Workflow For A Fresh Agent

Use this repo from its project directory:

```bash
cd /Users/daniel/Dropbox/Code/unsubscribe-emails
export UV_CACHE_DIR=/Users/daniel/Dropbox/Code/unsubscribe-emails/.uv-cache
```

The script handles Gmail discovery, local state files, Gmail labeling, retries, review movement, ignored senders, and report generation. It does not initiate the agentic browser work by itself. An agent should run the scripted steps, then use the browser for each unsubscribe URL returned by `next`.

**Start immediately with step 1 below. Do not re-read this file or pre-initialize the browser before your first tool call — bootstrap the browser runtime just before you navigate to the first unsubscribe URL.**

---

1. Ingest all currently eligible Gmail messages:

```bash
uv run unsubscribe ingest
```

This finds all Gmail messages labeled `@SaneBlackHole` that are not already labeled `#auto-unsubscribe`, writes entries to `unsubscribe-todo.md`, then applies `#auto-unsubscribe` after the local write succeeds. Only the most recent email per sender is processed; older duplicates from the same sender are labeled and skipped. Do not add an ingest `--limit` during the daily automation unless the user explicitly asks for a diagnostic/safety cap.

**`ingest` requires outbound network access to `gmail.googleapis.com`.** If it exits with a DNS or network error (`ServerNotFoundError`, `Unable to find the server`), re-run the command with elevated sandbox/network permissions — this is the only command that requires them. All other commands (`next`, `mark-done`, `mark-retry`, `report`, `review-server`) are local filesystem operations and do not need elevated network permissions.

The ingest output fields:
- `created` — new todo entries written to `unsubscribe-todo.md`
- `updated` — existing entries refreshed
- `skippedProcessed` — emails already labeled `#auto-unsubscribe`, skipped
- `skippedDuplicate` — duplicate sender emails within this run, skipped (only the newest is kept)
- `ignored` — emails from senders in `ignored-senders.md`, labeled and skipped
- `labeled` — total emails that had `#auto-unsubscribe` applied this run

If `created` is 0, the todo queue was not changed by this run (all emails were already processed).

---

2. Get the browser work queue:

```bash
uv run unsubscribe next --limit 10
```

This is the real daily workload throttle. It returns at most 10 entries for the agentic browser step, and the returned entries are deduplicated by sender email so one browser batch does not process multiple messages from the same sender.

For each JSON entry:

- If `unsubscribeUrl` is missing, run `mark-retry` with a clear reason.
- Otherwise open `unsubscribeUrl` in the agentic browser.
- If the page says the user is already unsubscribed, run `mark-done`.
- If the page needs confirmation, click the confirmation control. If it needs the recipient email, use `recipientEmail`.
- If the page confirms success, run `mark-done`.
- If the page cannot be completed, run `mark-retry` with the observed error.

Examples:

```bash
uv run unsubscribe mark-done <entry-id>
uv run unsubscribe mark-retry <entry-id> --reason "Unsubscribe page returned an error"
```

`mark-retry` increments attempts. After 3 attempts, the entry moves from `unsubscribe-todo.md` to `unsubscribe-review.md`.

### Browser interaction patterns

**Preference centers:** If the URL loads a subscription preference page (multiple checkboxes or topic categories rather than a single unsubscribe button), look for a link or button labeled "Unsubscribe from all" or similar and click it. If no such option exists, deselect all subscriptions using the checkboxes, then submit the form.

**`subscription_center.aspx?jwt=` URLs (ExactTarget / Salesforce Marketing Cloud):** These URLs contain short-lived JWT tokens. If the page immediately shows "An error has occurred and has been logged by our system," the token has expired and the page cannot be completed. Run `mark-retry` with reason `"subscription_center.aspx JWT error – token likely expired"`. Check whether the entry also has a non-null `unsubscribeMailto` field and include `"(mailto fallback available)"` in the reason so a reviewer knows a recoverable path exists.

**Aweber preference pages (aweber.com):** The unsubscribe radio button may already be selected when the page loads. Submit the form regardless. If the result page says "None of your subscriptions have been changed," this means the address was already in the unsubscribed state — treat it as confirmed and run `mark-done`.

**Verifying success after form submission:** After clicking a submit/confirm button that triggers a page navigation, verify success by taking a snapshot of the resulting page rather than asserting the button is still present (it won't be after navigation). Look for confirmation language ("You have been unsubscribed", "Successfully unsubscribed", etc.).

**Multiple entries from the same sender:** `next` deduplicates by sender email and should return at most one entry per sender. If older duplicate todo records exist from a previous version of the workflow, leave them in todo; future runs will continue choosing the newest record for that sender.

**REPL variable scope:** If you issue multiple JavaScript calls to the browser in the same session, variable names persist across calls. Use `var` (not `const`/`let`) for locators, or use entry-specific variable names, to avoid `"Identifier 'X' has already been declared"` errors.

---

3. Regenerate the report:

```bash
uv run unsubscribe report
```

---

4. Serve the report when the user wants working `ignore` links:

```bash
uv run unsubscribe review-server --port 8765
```

Start the server with `tty: true` and allow a few seconds for it to bind before navigating to it. Do not use `nohup` or shell backgrounding (`&`) — these may silently fail to bind in sandboxed environments. The `file://` URL fallback is blocked by browser security policy and should not be attempted.

Then show the report in the browser:

```text
http://127.0.0.1:8765
```

If a static report is enough and ignore links do not need to modify files, open:

```text
file:///Users/daniel/Dropbox/Code/unsubscribe-emails/unsubscribe-report.html
```

When using the browser manually, the preferred final state is that `unsubscribe-todo.md` contains no entries returned by `next`, successful entries have moved to `unsubscribe-done.md`, failed 3-attempt entries have moved to `unsubscribe-review.md`, and the browser is showing the latest report.
