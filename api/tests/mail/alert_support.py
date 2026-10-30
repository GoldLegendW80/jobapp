"""Synthetic job alert e-mails in Gmail's shape, and a fake mailbox (mail/alerts tests).

`fixtures/alerts/*.eml` are alert e-mails written by hand after the public formats of
SPEC-mail.md ("Job alert e-mails"): invented jobs and companies, example.org recipients, the
providers' real sender addresses (the allow list is what is tested). `gmail_json` turns one into
the answer of `messages.get` with `format=full` the way Gmail does: the transfer encoding
(quoted-printable, base64) undone, each part's bytes as base64url, and, unless told otherwise,
the lines Gmail writes on top of a mail it took by SMTP (assumption A4 of mail/auth.py) with
the authentication result asked for.
"""

from __future__ import annotations

import email
import email.utils
import itertools
from email import policy
from pathlib import Path
from typing import Any

from app.mail import domains
from app.mail.alerts.check import gmail_answer

HERE = Path(__file__).resolve().parent / "fixtures" / "alerts"
ME = "e2e-alerts@example.org"
_IDS = itertools.count(1)


def eml(name: str) -> bytes:
    return (HERE / name).read_bytes()


def gmail_lines(domain: str, auth: str) -> list[dict[str, str]]:
    """Gmail's own lines above the sender's: `auth` is "pass" (DKIM and DMARC aligned with
    `domain`), "spf-only" (no DKIM, no DMARC: never a pass), "dmarc-fail" (a DKIM pass vetoed
    by DMARC), "other-domain" (a DKIM pass of another organisation) or "none" (no line)."""
    lines = [
        ("Delivered-To", ME),
        ("Received", "by 2002:a05:6402:fake with SMTP id fake1; Fri, 30 Oct 2026 07:00:00 +0000"),
        (
            "Received",
            f"from mail.{domain} (mail.{domain}. [203.0.113.9]) by mx.google.com with ESMTPS "
            f"id fake-2 for <{ME}>; Fri, 30 Oct 2026 07:00:00 +0000",
        ),
    ]
    results = {
        "pass": f"dkim=pass header.i=@{domain} header.s=s1; spf=pass smtp.mailfrom={domain}; "
        f"dmarc=pass (p=REJECT) header.from={domain}",
        "spf-only": f"spf=pass smtp.mailfrom={domain}",
        "dmarc-fail": f"dkim=pass header.i=@{domain}; dmarc=fail (p=REJECT) header.from={domain}",
        "other-domain": "dkim=pass header.i=@evil.example; spf=pass smtp.mailfrom=evil.example",
    }
    if auth != "none":
        lines.append(("Authentication-Results", f"mx.google.com; {results[auth]}"))
    return [{"name": name, "value": value} for name, value in lines]


def gmail_json(
    raw: bytes,
    *,
    auth: str = "pass",
    domain: str | None = None,
    internal_ms: int = 1_793_343_600_000,
    message_id: str | None = None,
    labels: tuple[str, ...] = ("INBOX",),
    from_header: str | None = None,
) -> dict[str, Any]:
    message = email.message_from_bytes(raw, policy=policy.compat32)
    if from_header is not None:
        del message["From"]
        message["From"] = from_header
    sender = email.utils.parseaddr(message.get("From", ""))[1]
    domain = domain or domains.registrable_domain(sender.rsplit("@", 1)[-1]) or "example.org"
    number = next(_IDS)
    answer = gmail_answer(
        message.as_bytes(), message_id=message_id or f"a{number:015x}", internal_ms=internal_ms
    )
    answer["payload"]["headers"] = gmail_lines(domain, auth) + answer["payload"]["headers"]
    answer.update(threadId=f"t{number:015x}", labelIds=list(labels))
    return answer


class FakeMailbox:
    """`mcp_client.GmailMcp` as alerts/source.py calls it: a list of Gmail answers, newest
    first, as `messages.list` orders them. It records every call; it answers `messages.list`
    with every message whatever the query (Gmail's `from:` is only a pre-filter: the code
    decides on the parsed message), paged by `page`."""

    def __init__(self, messages: list[dict[str, Any]], page: int = 100) -> None:
        self.messages = sorted(messages, key=lambda m: -int(m["internalDate"]))
        self.page = page
        self.calls: list[tuple[str, Any]] = []
        self.error: dict[str, Any] | None = None
        self.fail_after: int | None = None

    async def list_messages(
        self, account: Any, *, q: str = "", page_token: str | None = None, max_results: int = 100
    ) -> dict[str, Any]:
        self.calls.append(("list", q))
        if self.error is not None:
            return self.error
        start = int(page_token or 0)
        chunk = self.messages[start : start + self.page]
        answer: dict[str, Any] = {"messages": [{"id": m["id"]} for m in chunk]}
        if start + self.page < len(self.messages):
            answer["nextPageToken"] = str(start + self.page)
        return answer

    async def get_message(
        self, account: Any, id: str, *, format: str = "full", metadata_headers: Any = None
    ) -> dict[str, Any]:
        self.calls.append(("get", id))
        if self.fail_after is not None and len([c for c in self.calls if c[0] == "get"]) > (
            self.fail_after
        ):
            return {"error": "gmail_5xx"}
        return next(m for m in self.messages if m["id"] == id)
