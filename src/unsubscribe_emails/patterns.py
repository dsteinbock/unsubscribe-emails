"""Shared, deterministic text patterns for the browser worker.

These constants are the safety and decision policy for the unsubscribe worker.
They are intentionally pure (regex over strings) so they can be unit-tested
without a live browser and reused by both the heuristic tiers and the
needs-agent handoff.
"""

from __future__ import annotations

import re

# A page that matches one of these has already confirmed the unsubscribe.
# Phrased as confirmations ("you have been ...") to avoid firing on pre-action
# descriptions like "unsubscribe to stop receiving these emails".
SUCCESS_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"you('ve| have) been (successfully )?unsubscribed",
        r"successfully unsubscribed",
        r"unsubscribe (was )?successful",
        r"you are (now )?unsubscribed",
        r"you('ve| have) been removed",
        r"has been (removed|unsubscribed)",
        r"we('ve| have) removed\b",  # "We have removed <email> from all ... lists"
        r"removed from (our|the|this) (mailing )?list",
        r"removed .{0,60}from .{0,40}(mailing )?lists?",  # email/name between removed & list
        r"no longer (be )?subscribed",
        # Confirmation that future mail stops; the subject is often an email
        # address rather than "you", so don't require "you" before it.
        r"(you('ll| will)|will) no longer receive",
        r"no longer receive (any )?(emails?|messages|mail)",
        r"your (email |subscription )?preferences have been (saved|updated)",
        r"preferences (have been |were )?(saved|updated)",
        r"none of your subscriptions have been changed",  # Aweber already-off case
    )
)

# Pages that errored out (expired token, wrong HTTP method, server fault). These
# cannot be completed deterministically, so the worker retries rather than
# handing a dead page to an agent.
ERROR_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"an error has occurred",
        r"has been logged",
        r"whitelabel error page",
        r"this application has no explicit mapping",
        r"that'?s an error",
        r"\b405\b",
        r"method '?\w+'? (is )?not (supported|allowed)",
        r"\b(unexpected|internal server) error\b",
    )
)

# Pages that demand a higher-risk action than unsubscribing from a newsletter.
# Deliberately excludes "cancel subscription" / "cancel": for newsletters that
# phrasing is usually the unsubscribe itself, so escalating on it would create
# false positives. Only genuinely account-/money-/credential-level actions here.
UNSAFE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"delete (your )?account",
        r"close (your )?account",
        r"deactivate (your )?account",
        r"enter your password",
        r"two[- ]factor",
        r"\b2fa\b",
        r"\bbilling\b",
        r"payment method",
        r"credit card",
    )
)

# Pages we cannot complete deterministically and should not guess at.
BLOCKER_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"are you a robot",
        r"i'?m not a robot",
        r"recaptcha",
        r"verify you are (a )?human",
        r"complete the captcha",
        r"please (sign|log) ?in",
        r"(sign|log) ?in to (your account|continue)",
    )
)

# Used to gate generic "submit"/"save" buttons: only press them when the page is
# clearly an unsubscribe / preferences context.
UNSUB_CONTEXT_RE = re.compile(
    r"unsubscrib|opt[\s-]?out|preferenc|subscription|mailing list|no longer",
    re.IGNORECASE,
)

# Matches the accessible name of an email input field.
EMAIL_FIELD_RE = re.compile(r"e-?mail", re.IGNORECASE)

# Ordered click priorities. The worker takes the first matching candidate.
# `guarded` priorities only fire when the page has unsubscribe context, so a
# bare "Submit" on an unrelated form is never pressed blindly.
CLICK_PRIORITIES: tuple[tuple[str, re.Pattern[str], bool], ...] = (
    (
        "confirm_unsubscribe",
        re.compile(
            r"confirm.*unsubscrib|unsubscrib.*confirm|yes,?\s*unsubscrib|confirm.*opt[\s-]?out",
            re.IGNORECASE,
        ),
        False,
    ),
    (
        "unsubscribe_all",
        re.compile(
            r"unsubscribe from all|unsubscribe all|opt out of all|stop all email"
            r"|remove me from all|(from|of) all (mailing )?(lists?|emails?)",
            re.IGNORECASE,
        ),
        False,
    ),
    (
        "unsubscribe",
        re.compile(
            r"unsubscrib|opt[\s-]?out|remove me|take me off|stop (all )?emails?"
            r"|no longer (wish to )?receive",
            re.IGNORECASE,
        ),
        False,
    ),
    (
        "save_preferences",
        re.compile(
            r"save (my )?preferences|update (my )?(email )?preferences|save changes|update subscription",
            re.IGNORECASE,
        ),
        True,
    ),
    (
        "submit",
        re.compile(r"\b(submit|save|continue|update|confirm)\b", re.IGNORECASE),
        True,
    ),
)

CLICKABLE_ROLES = frozenset({"button", "link", "checkbox", "radio"})


def _matches_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> str | None:
    for pattern in patterns:
        if pattern.search(text):
            return pattern.pattern
    return None


def detect_success(text: str) -> bool:
    """True if the page text confirms the unsubscribe completed."""
    return _matches_any(text, SUCCESS_PATTERNS) is not None


def detect_unsafe(text: str) -> str | None:
    """Return the matched unsafe phrase, or None when the page is safe to act on."""
    return _matches_any(text, UNSAFE_PATTERNS)


def detect_blocker(text: str) -> str | None:
    """Return the matched blocker phrase (captcha/login), or None."""
    return _matches_any(text, BLOCKER_PATTERNS)


def detect_error(text: str) -> str | None:
    """Return the matched error phrase (expired token, 405, server fault), or None."""
    return _matches_any(text, ERROR_PATTERNS)


def has_unsub_context(text: str) -> bool:
    return bool(UNSUB_CONTEXT_RE.search(text))
