# Auto-Unsubscribe Runbook (execute this top to bottom)

You are an automation agent. Execute the steps below **in order**. Run each command exactly as written. Do not summarize, plan, or ask questions first — start at Step 1 and work down. The only part that needs judgment is Step 3 (a handful of leftover web pages); everything else is copy-paste.

**Setup — run this first, once:**

```bash
cd /Users/daniel/Dropbox/Code/unsubscribe-emails
export UV_CACHE_DIR=/Users/daniel/Dropbox/Code/unsubscribe-emails/.uv-cache
```

A Python script does all the deterministic work (Gmail ingest, parsing, labeling, retries, report, and most unsubscribes). You only touch a browser for the few ambiguous pages the script explicitly hands back in Step 3.

---

## Step 1 — Ingest new emails

```bash
uv run unsubscribe ingest
```

This labels eligible Gmail messages and writes them to `unsubscribe-todo.md`. Do **not** add `--limit`.

- This is the **only** command that needs network access. If it fails with a DNS/network error (`ServerNotFoundError`, `Unable to find the server`), re-run it with elevated sandbox/network permissions, then continue.
- The output is a JSON summary. If `created` is `0`, no new emails were added — that's fine, keep going.

---

## Step 2 — Run the deterministic worker

```bash
uv run unsubscribe run
```

Run this in the **foreground** and wait for it to finish — it may take a minute or two while the browser works through the queue. **Do not run it in the background** (no trailing `&`, no background-task option); just let it complete and read the JSON it prints.

This unsubscribes the easy majority automatically (no browser, no tokens) and prints a JSON summary like:

```json
{ "processed": 53, "oneClick": 31, "autoDone": 47, "autoRetry": 4,
  "needsAgent": 2, "stoppedEarly": false, "needsAgentEntries": [ ... ] }
```

Decide what to do next from this summary:

- **`needsAgentEntries` is empty (`needsAgent: 0`)** → skip Step 3, go to Step 4.
- **`needsAgentEntries` has entries** → do Step 3 for each one.
- After finishing Step 3, **if `stoppedEarly` was `true`**, run `uv run unsubscribe run` again and repeat Step 3. Keep looping until a run returns `needsAgent: 0` **or** `stoppedEarly: false`. (Stop after at most 5 loops and go to Step 4 regardless.)

---

## Step 3 — Finish the leftover pages (the only browser work)

Do this once per item in `needsAgentEntries`. Each item looks like:

```json
{ "id": "<entry-id>", "senderName": "...", "subject": "...",
  "recipientEmail": "you@example.com", "unsubscribeUrl": "https://...",
  "reason": "no deterministic action matched",
  "pageTitle": "...", "pageSnippet": "first 500 chars of visible text",
  "candidates": [ {"id": "button:0", "role": "button", "name": "Update Preferences", "checked": false, "value": ""} ] }
```

**Drive the browser with the `browse` command** — `uv run unsubscribe browse` opens the page headlessly with Playwright (already installed) and prints just the page state as JSON: `{ "title", "snippet", "candidates", "success" }`. This is the most token-cheap and reliable option — use it instead of any MCP/extension browser tool. Each call is one shot: it navigates, performs the actions you pass, then re-reads the page.

- **Inspect a page:** `uv run unsubscribe browse <entry-id>`
- **Act on it (actions run in order: fill, then checks, then clicks):**
  `uv run unsubscribe browse <entry-id> --fill-email --check "<name>" --click "<name>"`
  - `--fill-email` types the entry's recipient into the email field.
  - `--check "Unsubscribe from all"` ticks a checkbox/radio by name.
  - `--click "Unsubscribe"` clicks a button/link by name (names match the `candidates[].name` values).
- The printed `"success": true` means the page now shows unsubscribe-confirmation text.

**For each entry, follow this procedure exactly:**

1. Run `browse <entry-id>` to read `title`, `snippet`, and `candidates`.
2. Decide using the **first** rule that matches:
   - **Already done** — `"success": true`, or the snippet says you're unsubscribed → run `mark-done`. Done with this entry.
   - **Stop — do not touch** — snippet mentions delete/close account, billing, payment, password, two-factor/2FA, a CAPTCHA, or asks you to log in → run `mark-retry` with a reason describing the blocker. Done.
   - **Act** — there is a clear unsubscribe control in `candidates` (named like "Unsubscribe", "Confirm unsubscribe", "Unsubscribe from all", "Opt out", "Remove me") → re-run `browse` with the action(s): add `--fill-email` if there is an email textbox, `--check`/`--click` as needed.
   - **Unsure** — none of the above is clearly true → run `mark-retry` with reason `"needs manual review: <one-line description>"`. Done.
3. After an action call, read the returned `success`/`snippet` (it already re-read the page). If `success` is true → `mark-done`. If not, try **one** more obvious unsubscribe/confirm control; if it still doesn't confirm → `mark-retry` with the observed state.
4. **Never run more than 3 action calls on a single page.** If it isn't confirmed by then, `mark-retry` and move on.

**Recording the result (always do exactly one of these per entry):**

```bash
uv run unsubscribe mark-done <entry-id>
uv run unsubscribe mark-retry <entry-id> --reason "Unsubscribe page returned an error"
```

`mark-retry` increments attempts; after 3 attempts the entry moves to `unsubscribe-review.md` for a human. When in doubt, prefer `mark-retry` over guessing — never click anything risky.

> Note: the deterministic worker in Step 2 already auto-handles expired-token/error pages, HTTP errors, and already-confirmed pages, and it retries a body-link fallback — so the entries that reach you here are genuinely ones needing a judgment call (e.g. a preference center with no clear "unsubscribe from all", or a form that didn't confirm). For a preference center with no single unsubscribe control, click an "Unsubscribe from all" candidate if present; otherwise treat it as **Unsure** → `mark-retry "needs manual review: preference center, no unsubscribe-all option"`.

---

## Step 4 — Regenerate the report

```bash
uv run unsubscribe report
```

---

## Step 5 — Open the report for review

This is the final step. Once Steps 1–4 are done (`unsubscribe run` reports `needsAgent: 0`, or you've looped Step 2/3 up to 5 times), start the review server so the user can review the report. It **automatically opens a visible browser window** at the report.

The review server is a **long-running process that blocks**, so start it in the **background** (do not wait for it to exit):

```bash
uv run unsubscribe review-server --port 8765 &
```

(If your tooling has a dedicated "run in background" option, use that instead of the trailing `&`.) A browser window opens on its own at `http://127.0.0.1:8765`; the page has working "ignore sender" links. If for some reason no window appears, tell the user to open `http://127.0.0.1:8765` manually.

Then print a one-line summary of the final `run` JSON (processed / autoDone / autoRetry / needsAgent), note that the report is open in the browser, and stop. Leave the server running.
