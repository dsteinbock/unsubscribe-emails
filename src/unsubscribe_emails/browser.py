"""Tiers 1-3: deterministic Playwright unsubscribe worker.

The worker drives unsubscribe pages with *no LLM*:

* Tier 1 -- open the URL and detect an already-confirmed success page.
* Tier 2 -- enumerate interactive candidates and act on allowlisted ones
  (click unsubscribe/confirm, fill the recipient email, tick "unsubscribe from
  all"), re-reading the page and verifying success after each step.
* Tier 4 -- escalate (retry) when the page is unsafe (account/billing/password)
  or blocked (captcha/login). Checked before any click.
* Tier 3 -- when nothing deterministic resolves it, emit an agent-agnostic
  ``needs_agent`` handoff: url, title, body snippet and the enumerated
  candidate list, for whatever orchestrator agent is driving the run.

``decide_action`` is a pure function over candidate dicts + body text so the
decision policy is unit-testable without launching a browser. Playwright is
imported lazily so the rest of the package works without it installed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .oneclick import one_click_unsubscribe
from .patterns import (
    CLICK_PRIORITIES,
    CLICKABLE_ROLES,
    EMAIL_FIELD_RE,
    detect_blocker,
    detect_error,
    detect_success,
    detect_unsafe,
    has_unsub_context,
)

OUTCOME_DONE = "done"
OUTCOME_RETRY = "retry"
OUTCOME_NEEDS_AGENT = "needs_agent"

# Roles we enumerate as candidates and the max we keep per role.
ENUMERATED_ROLES = ("button", "link", "checkbox", "radio", "textbox")
MAX_PER_ROLE = 40


@dataclass
class WorkerResult:
    outcome: str  # done | retry | needs_agent
    tier: str
    reason: str
    url: str | None = None
    title: str | None = None
    snippet: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide_action(
    candidates: list[dict[str, Any]],
    body_text: str,
    recipient_email: str | None,
    email_filled: bool,
) -> dict[str, Any] | None:
    """Choose the single next action, or None when nothing deterministic fits.

    Returns a dict ``{"type": "fill"|"check"|"click", "candidate": {...},
    "label": <priority name>}``. Pure: no Playwright, no I/O.
    """
    # Priority 0: an empty email field that the page is asking us to fill.
    if recipient_email and not email_filled:
        for candidate in candidates:
            if (
                candidate.get("role") == "textbox"
                and not (candidate.get("value") or "").strip()
                and EMAIL_FIELD_RE.search(candidate.get("name") or "")
            ):
                return {"type": "fill", "candidate": candidate, "label": "email"}

    has_context = has_unsub_context(body_text)
    for label, pattern, guarded in CLICK_PRIORITIES:
        if guarded and not has_context:
            continue
        for candidate in candidates:
            role = candidate.get("role")
            if role not in CLICKABLE_ROLES:
                continue
            if not pattern.search(candidate.get("name") or ""):
                continue
            if role in ("checkbox", "radio"):
                if candidate.get("checked"):
                    continue  # already selected; never toggle it back off
                return {"type": "check", "candidate": candidate, "label": label}
            return {"type": "click", "candidate": candidate, "label": label}
    return None


class BrowserWorker:
    """Context-managed Playwright Chromium session running Tiers 1-3."""

    def __init__(
        self,
        headed: bool = False,
        nav_timeout: float = 20000,
        settle_timeout: float = 8000,
        max_steps: int = 4,
    ) -> None:
        self.headed = headed
        self.nav_timeout = nav_timeout
        self.settle_timeout = settle_timeout
        self.max_steps = max_steps
        self._pw = None
        self._browser = None
        self.context = None

    def __enter__(self) -> "BrowserWorker":
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=not self.headed)
        self.context = self._browser.new_context()
        return self

    def __exit__(self, *exc: object) -> None:
        for closer in (
            getattr(self.context, "close", None),
            getattr(self._browser, "close", None),
            getattr(self._pw, "stop", None),
        ):
            if closer is None:
                continue
            try:
                closer()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass

    def process_record(self, url: str, recipient_email: str | None) -> WorkerResult:
        page = self.context.new_page()
        try:
            return self._run_tiers(page, url, recipient_email)
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass

    def inspect(
        self,
        url: str,
        recipient_email: str | None = None,
        fill_email: bool = False,
        checks: list[str] | None = None,
        clicks: list[str] | None = None,
    ) -> dict[str, Any]:
        """Open ``url``, optionally perform ordered actions, then dump page state.

        Backs the ``unsubscribe browse`` CLI: a single stateless call does
        navigate -> (fill email / check / click by accessible name) -> re-read.
        Returns ``{url, title, snippet, candidates, success}`` as plain JSON so
        any agent can drive a real browser from the shell without an MCP tool.
        """
        page = self.context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout)
            self._settle(page)

            if fill_email and recipient_email:
                self._act_on_name(page, ("textbox",), None, "fill", recipient_email)
            for name in checks or []:
                self._act_on_name(page, ("checkbox", "radio"), name, "check", "")
            for name in clicks or []:
                self._act_on_name(page, ("button", "link"), name, "click", "")

            if checks or clicks or fill_email:
                self._settle(page)
            body = self._body_text(page)
            return {
                "url": url,
                "title": self._title(page),
                "snippet": " ".join((body or "").split())[:500],
                "candidates": self._enumerate(page),
                "success": detect_success(body),
            }
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass

    def _act_on_name(self, page, roles, name, action_type, fill_value) -> None:
        """Find the first visible candidate in ``roles`` matching ``name`` and act.

        ``name=None`` matches the first candidate of that role (used for the
        email field). Matching is case-insensitive substring on accessible name.
        """
        needle = (name or "").strip().lower()
        for candidate in self._enumerate(page):
            if candidate["role"] not in roles:
                continue
            if needle and needle not in (candidate.get("name") or "").lower():
                continue
            self._apply(page, {"type": action_type, "candidate": candidate}, fill_value)
            return

    def _run_tiers(self, page, url: str, recipient_email: str | None) -> WorkerResult:
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout)
        except Exception as exc:  # noqa: BLE001
            return WorkerResult(OUTCOME_RETRY, "navigate", f"navigation failed: {exc}", url=url)
        self._settle(page)

        body = self._body_text(page)

        # Tier 1: already confirmed on open.
        if detect_success(body):
            return self._result(OUTCOME_DONE, "open", "page already confirms unsubscribe", page, url, body)

        # HTTP error status (e.g. 405 on a POST-only endpoint) -> deterministic retry.
        status = getattr(response, "status", None)
        if isinstance(status, int) and status >= 400:
            return self._result(
                OUTCOME_RETRY, "http_error", f"page returned HTTP {status}", page, url, body
            )

        escalation = self._escalation(body, page, url)
        if escalation is not None:
            return escalation

        # A page that rendered essentially nothing and exposes no controls cannot
        # be driven deterministically -> retry instead of a useless agent handoff.
        if len("".join(body.split())) < 15 and not self._enumerate(page):
            return self._result(OUTCOME_RETRY, "empty", "page rendered no content", page, url, body)

        # Tier 2: deterministic action loop.
        email_filled = False
        for _ in range(self.max_steps):
            candidates = self._enumerate(page)
            action = decide_action(candidates, body, recipient_email, email_filled)
            if action is None:
                return self._needs_agent(
                    "no deterministic action matched", page, url, body, candidates
                )
            self._apply(page, action, recipient_email or "")
            if action["type"] == "fill":
                email_filled = True
            self._settle(page)
            body = self._body_text(page)
            if detect_success(body):
                return self._result(
                    OUTCOME_DONE, "action", f"unsubscribed via {action['label']}", page, url, body
                )
            escalation = self._escalation(body, page, url)
            if escalation is not None:
                return escalation

        return self._needs_agent(
            "did not confirm success after deterministic actions", page, url, body, self._enumerate(page)
        )

    # --- helpers -------------------------------------------------------------

    def _escalation(self, body: str, page, url: str) -> WorkerResult | None:
        unsafe = detect_unsafe(body)
        if unsafe:
            return self._result(OUTCOME_RETRY, "unsafe", f"unsafe action detected: {unsafe}", page, url, body)
        blocker = detect_blocker(body)
        if blocker:
            return self._result(OUTCOME_RETRY, "blocked", f"blocked: {blocker}", page, url, body)
        error = detect_error(body)
        if error:
            return self._result(OUTCOME_RETRY, "error_page", f"error page: {error}", page, url, body)
        return None

    def _needs_agent(self, reason, page, url, body, candidates) -> WorkerResult:
        result = self._result(OUTCOME_NEEDS_AGENT, "ambiguous", reason, page, url, body)
        result.candidates = candidates
        return result

    def _result(self, outcome, tier, reason, page, url, body) -> WorkerResult:
        return WorkerResult(
            outcome=outcome,
            tier=tier,
            reason=reason,
            url=url,
            title=self._title(page),
            snippet=" ".join((body or "").split())[:500],
        )

    def _settle(self, page) -> None:
        for state in ("domcontentloaded", "networkidle"):
            try:
                page.wait_for_load_state(state, timeout=self.settle_timeout)
            except Exception:  # noqa: BLE001 - settling is best-effort
                pass

    def _body_text(self, page) -> str:
        try:
            return page.locator("body").inner_text(timeout=self.settle_timeout)
        except Exception:  # noqa: BLE001
            return ""

    def _title(self, page) -> str | None:
        try:
            return page.title()
        except Exception:  # noqa: BLE001
            return None

    def _enumerate(self, page) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for role in ENUMERATED_ROLES:
            loc = page.get_by_role(role)
            try:
                count = min(loc.count(), MAX_PER_ROLE)
            except Exception:  # noqa: BLE001
                continue
            for index in range(count):
                item = loc.nth(index)
                try:
                    if not item.is_visible():
                        continue
                except Exception:  # noqa: BLE001
                    continue
                value, checked = self._candidate_state(item, role)
                candidates.append(
                    {
                        "id": f"{role}:{index}",
                        "role": role,
                        "name": self._accessible_name(item),
                        "value": value,
                        "checked": checked,
                    }
                )
        return candidates

    def _candidate_state(self, item, role) -> tuple[str, bool]:
        value = ""
        checked = False
        if role == "textbox":
            try:
                value = item.input_value(timeout=1000) or ""
            except Exception:  # noqa: BLE001
                value = ""
        elif role in ("checkbox", "radio"):
            try:
                checked = item.is_checked(timeout=1000)
            except Exception:  # noqa: BLE001
                checked = False
        return value, checked

    def _accessible_name(self, item) -> str:
        getters = (
            lambda: item.get_attribute("aria-label"),
            lambda: item.inner_text(timeout=1000),
            lambda: item.get_attribute("placeholder"),
            lambda: item.get_attribute("value"),
        )
        for getter in getters:
            try:
                value = getter()
            except Exception:  # noqa: BLE001
                value = None
            if value and value.strip():
                return " ".join(value.split())
        return ""

    def _apply(self, page, action: dict[str, Any], fill_value: str) -> None:
        candidate = action["candidate"]
        role, index = candidate["id"].split(":")
        locator = page.get_by_role(role).nth(int(index))
        if action["type"] == "fill":
            locator.fill(fill_value, timeout=self.settle_timeout)
        elif action["type"] == "check":
            locator.check(timeout=self.settle_timeout)
        else:
            locator.click(timeout=self.settle_timeout)


def run_queue(
    agent_limit: int | None = 10,
    headed: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Process the dedup'd todo queue through Tiers 0-3 and update state.

    Deterministic resolution (one-click + heuristic browser actions) runs over
    the whole queue unbounded. Only the ambiguous ``needs-agent`` handoff is
    capped at ``agent_limit`` to keep an orchestrator's workload bounded; once
    that many ambiguous pages are collected the run stops and leaves the rest in
    todo for the next run. ``limit`` is an optional diagnostic cap on the total
    number of records pulled.

    Returns a summary plus the agent-agnostic ``needsAgentEntries`` handoff.
    """
    from .store import mark_done, mark_retry, next_full_records

    records = next_full_records(limit)
    summary: dict[str, Any] = {
        "processed": 0,
        "oneClick": 0,
        "autoDone": 0,
        "autoRetry": 0,
        "needsAgent": 0,
        "stoppedEarly": False,
        "needsAgentEntries": [],
    }
    if not records:
        return summary

    pending = []
    for record in records:
        if not record.unsubscribeUrl:
            mark_retry(record.id, "no unsubscribe URL found")
            summary["autoRetry"] += 1
            summary["processed"] += 1
            continue
        # Tier 0: one-click POST, no browser. Always done for the whole queue.
        if record.oneClick:
            result = one_click_unsubscribe(record.unsubscribeUrl)
            if result.ok:
                mark_done(record.id)
                summary["autoDone"] += 1
                summary["oneClick"] += 1
                summary["processed"] += 1
                continue
        pending.append(record)

    if pending:
        with BrowserWorker(headed=headed) as worker:
            for record in pending:
                if agent_limit is not None and summary["needsAgent"] >= agent_limit:
                    # Hit the ambiguous-handoff cap; leave the rest in todo.
                    summary["stoppedEarly"] = True
                    break
                summary["processed"] += 1
                result = worker.process_record(record.unsubscribeUrl, record.recipientEmail)
                # Fallback: if the header link did not succeed, try the unsubscribe
                # link found in the email body (a different URL) before giving up.
                if result.outcome != OUTCOME_DONE and record.unsubscribeUrlFallback:
                    fallback = worker.process_record(
                        record.unsubscribeUrlFallback, record.recipientEmail
                    )
                    if fallback.outcome == OUTCOME_DONE:
                        result = fallback
                if result.outcome == OUTCOME_DONE:
                    mark_done(record.id)
                    summary["autoDone"] += 1
                elif result.outcome == OUTCOME_RETRY:
                    mark_retry(record.id, result.reason)
                    summary["autoRetry"] += 1
                else:
                    summary["needsAgent"] += 1
                    summary["needsAgentEntries"].append(
                        {
                            **record.compact_for_browser(),
                            "reason": result.reason,
                            "pageTitle": result.title,
                            "pageSnippet": result.snippet,
                            "candidates": result.candidates,
                        }
                    )
    return summary
