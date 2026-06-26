"""Tier 0: RFC 8058 one-click unsubscribe.

When a sender advertises ``List-Unsubscribe-Post: List-Unsubscribe=One-Click``,
the unsubscribe completes with a single HTTP POST to the ``List-Unsubscribe``
URL -- no browser, no DOM, no LLM. This handles a large share of modern senders
(Mailchimp, Substack, most ESPs) for effectively zero cost.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass

# RFC 8058 mandates exactly this body.
ONE_CLICK_BODY = b"List-Unsubscribe=One-Click"
USER_AGENT = "unsubscribe-emails/0.1 (+https://github.com/) one-click"


@dataclass
class OneClickResult:
    ok: bool
    status: int | None = None
    error: str | None = None


def one_click_unsubscribe(url: str, timeout: float = 20.0, opener=None) -> OneClickResult:
    """POST the RFC 8058 one-click body to ``url``.

    ``opener`` is injectable for testing; it defaults to ``urllib.request.urlopen``.
    Any 2xx response is treated as success.
    """
    request = urllib.request.Request(
        url,
        data=ONE_CLICK_BODY,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
    )
    opener = opener or urllib.request.urlopen
    try:
        with opener(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            ok = status is not None and 200 <= status < 300
            return OneClickResult(ok=ok, status=status)
    except urllib.error.HTTPError as exc:
        return OneClickResult(ok=False, status=exc.code, error=f"HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001 - network/DNS/TLS failures are all "retry"
        return OneClickResult(ok=False, status=None, error=str(exc))
