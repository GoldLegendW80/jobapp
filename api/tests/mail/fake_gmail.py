"""A fake Google at the HTTP boundary, for every test of the mail module (mail M1).

One implementation, two ways to run it:

- in process, by pytest: `FakeGoogle().transport()` is an httpx transport, given to
  `app.mail.google.GoogleClient(settings, transport=...)`. No socket is opened.
- as the service `gmailstub` of tests/gmail-stub.compose.yml, for the Playwright journeys:

      python3 api/tests/mail/fake_gmail.py --port 8098 [--seed fixtures/mailboxes] [--hold 30]

  Standard library only, so the plain Python image runs it.

No test may ever reach a real Google account: this file is what they reach instead.

What it serves (paths as Google's, under one host):

    GET  /o/oauth2/v2/auth                       the consent page: an account field, one checkbox
                                                 per scope asked, Allow, Deny
    POST /o/oauth2/v2/auth/decision              the form of that page: 302 to the redirect address
    POST /token                                  code and refresh grants, PKCE (S256) verified
    POST /revoke
    GET  /v1/userinfo
    GET  /gmail/v1/users/me/profile              getProfile
    GET  /gmail/v1/users/me/messages             messages.list: q, pageToken, maxResults
    GET  /gmail/v1/users/me/messages/{id}        messages.get: format full, metadata, minimal
    GET  /gmail/v1/users/me/messages/{id}/attachments/{aid}
    GET  /gmail/v1/users/me/threads/{id}         threads.get
    GET  /gmail/v1/users/me/history              history.list: startHistoryId, historyTypes
    POST /gmail/v1/users/me/messages/send        stores the raw message, answers id and threadId
    GET  /healthz

The shapes are those of Google's reference, written here from the documentation as it was known
to the author on 2026-10-13 (the pages could not be opened from the build machine that day, so
they are ASSUMPTIONS until checkpoint stage A reads a real answer; see "Measurements", M1, of
tasks/mail/todo.md):

- token: `{"access_token", "expires_in", "refresh_token", "scope", "token_type": "Bearer",
  "id_token"}`; a refresh answers the same without `refresh_token`; errors are
  `{"error": "invalid_grant", "error_description": ...}` with 400, `invalid_client` with 401.
- Gmail errors: `{"error": {"code", "message", "errors": [{"message", "domain", "reason"}],
  "status"}}`. Reasons used: `rateLimitExceeded`, `userRateLimitExceeded`, `dailyLimitExceeded`,
  `insufficientPermissions`, `authError`, `notFound`, `backendError`, `invalidArgument`.
- messages.list: `{"messages": [{"id", "threadId"}], "nextPageToken", "resultSizeEstimate"}`,
  newest first; no `messages` key when nothing matches.
- messages.get: `{"id", "threadId", "labelIds", "snippet", "historyId", "internalDate" (ms, a
  string), "sizeEstimate", "payload": {"partId", "mimeType", "filename", "headers": [{"name",
  "value"}], "body": {"size", "data" | "attachmentId"}, "parts"}}`; bodies are base64url.
  `metadata` keeps `mimeType` and `headers` only.
- history.list: `{"history": [{"id", "messages", "messagesAdded": [{"message"}],
  "messagesDeleted", "labelsAdded": [{"message", "labelIds"}], "labelsRemoved"}],
  "nextPageToken", "historyId"}`; 404 `notFound` for a start id that is too old (A3).
- attachments.get: `{"size", "data"}`. getProfile: `{"emailAddress", "messagesTotal",
  "threadsTotal", "historyId"}`. messages.send: `{"id", "threadId", "labelIds": ["SENT"]}`.

Where it is NOT Gmail, on purpose:

- A search operator it does not implement answers 400 with the reason `fakeUnsupportedQuery`,
  where Gmail would ignore it: a test that leans on an operator the fake cannot judge fails
  loudly. Implemented: words and "phrases", `-`, `OR`, `( )`, `{ }`, `from:`, `to:`, `cc:`,
  `subject:`, `rfc822msgid:`, `after:`, `before:` (YYYY/MM/DD at midnight UTC, or epoch
  seconds), `newer_than:`, `older_than:` (d, m, y), `in:`, `label:`, `is:`, `has:attachment`,
  `filename:`.
- `format=raw` answers 400: the module never asks for it.
- The consent page refuses a request without PKCE S256, which Google accepts.
- Its clock is fixed (`NOW`) until the control API moves it, so nothing expires by itself.

The control API (`/__control/...`, JSON in and out), for tests and journey helpers:

    POST reset                                   forget everything (keeps what --seed loaded)
    POST mailboxes        a mailbox fixture      seed or replace one mailbox
    POST grant            {email, scopes?}       a grant without the browser: {refresh_token}
    POST revoke           {email}                the user took the access away at Google
    POST expire-access    {email?}               every access token dies now (the grant lives)
    POST fail             {endpoint, kind, times?, retry_after?}   the next calls of it fail
    POST switch           {name, value}          see SWITCHES
    POST clock            {now}                  ISO 8601
    POST history-floor    {email, historyId}     older start ids answer 404
    POST messages         {email, message}       a new message arrives (a history record)
    POST delete-message   {email, id}            deleted in Gmail (a history record)
    POST labels           {email, id, add?, remove?}
    GET  calls            ?endpoint=&email=      every call received, oldest first
    GET  sent             ?email=                what was sent, with the raw message

`fail` kinds: `429`, `403_rate`, `403_user_rate`, `403_daily`, `403_scope`, `401`, `404`,
`500`, `503`, `invalid_grant`, `timeout`, `truncated`, `html`. Endpoint names are the keys of
`ROUTES` plus `token`, `revoke`, `userinfo`.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import html
import json
import random
import re
import secrets
import sys
import threading
import time
from collections import deque
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import message_from_bytes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit

CLIENT_ID = "fake-client-id.apps.fake-google.test"
CLIENT_SECRET = "fake-client-secret-0123456789"
NOW = "2026-10-13T12:00:00Z"
TOKEN_TTL_S = 3599
CODE_TTL_S = 600
REDIRECT_PATH = "/api/mail/oauth/callback"
SCOPE_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_SEND = "https://www.googleapis.com/auth/gmail.send"
SCOPE_EMAIL = "https://www.googleapis.com/auth/userinfo.email"
ALL_SCOPES = ("openid", SCOPE_EMAIL, SCOPE_READONLY, SCOPE_SEND)
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mailboxes"
BLOCK = 48_000  # raw bytes per piece of a generated attachment: a multiple of 3
OCTETS = "application/octet-stream"

SWITCHES = {
    # What `messages.send` does with the Message-ID it is given (assumption A10).
    "send_message_id": ("keep", "replace"),
    # `drop`: the mail is stored and the answer never comes. `error`: stored, then a 503.
    "send_answer": ("normal", "drop", "error"),
    # `messages.list` hands back the same page token for ever.
    "repeat_page_token": ("off", "on"),
}

ROUTES = [
    ("getProfile", "GET", re.compile(r"^/gmail/v1/users/[^/]+/profile$")),
    ("messages.send", "POST", re.compile(r"^/gmail/v1/users/[^/]+/messages/send$")),
    ("messages.list", "GET", re.compile(r"^/gmail/v1/users/[^/]+/messages$")),
    (
        "attachments.get",
        "GET",
        re.compile(r"^/gmail/v1/users/[^/]+/messages/([^/]+)/attachments/([^/]+)$"),
    ),
    ("messages.get", "GET", re.compile(r"^/gmail/v1/users/[^/]+/messages/([^/]+)$")),
    ("threads.get", "GET", re.compile(r"^/gmail/v1/users/[^/]+/threads/([^/]+)$")),
    ("history.list", "GET", re.compile(r"^/gmail/v1/users/[^/]+/history$")),
]
GMAIL_SCOPE = {"messages.send": SCOPE_SEND}

_ERRORS = {
    "429": (429, "rateLimitExceeded", "usageLimits", "RESOURCE_EXHAUSTED", "Too many requests."),
    "403_rate": (
        403,
        "rateLimitExceeded",
        "usageLimits",
        "PERMISSION_DENIED",
        "Rate Limit Exceeded",
    ),
    "403_user_rate": (
        403,
        "userRateLimitExceeded",
        "usageLimits",
        "PERMISSION_DENIED",
        "User Rate Limit Exceeded",
    ),
    "403_daily": (
        403,
        "dailyLimitExceeded",
        "usageLimits",
        "PERMISSION_DENIED",
        "Daily Limit Exceeded",
    ),
    "403_scope": (
        403,
        "insufficientPermissions",
        "global",
        "PERMISSION_DENIED",
        "Request had insufficient authentication scopes.",
    ),
    "401": (
        401,
        "authError",
        "global",
        "UNAUTHENTICATED",
        "Request had invalid authentication credentials.",
    ),
    "404": (404, "notFound", "global", "NOT_FOUND", "Requested entity was not found."),
    "400": (400, "invalidArgument", "global", "INVALID_ARGUMENT", "Invalid argument."),
    "500": (500, "backendError", "global", "INTERNAL", "Internal error encountered."),
    "503": (503, "backendError", "global", "UNAVAILABLE", "The service is currently unavailable."),
}


@dataclass
class Reply:
    """An answer. `body` is bytes, or pieces for a large one. `fault` replaces the answer by a
    failure of the connection: `timeout` (nothing comes) or `drop` (closed without a word)."""

    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | Iterator[bytes] = b""
    fault: str | None = None


class UnsupportedQueryError(ValueError):
    """A search this fake cannot judge."""


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def parse_time(value: Any) -> int:
    """Milliseconds since the epoch of an ISO 8601 text or of a number of milliseconds."""
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)


def generated_block(seed: str) -> bytes:
    return (hashlib.sha256(seed.encode("utf-8")).digest() * (BLOCK // 32 + 1))[:BLOCK]


def generated_bytes(seed: str, size: int) -> Iterator[bytes]:
    """The content of a generated attachment, piece by piece: the same for a seed and a size
    every time, so a test compares what it downloaded without holding it."""
    block = generated_block(seed)
    for _ in range(size // BLOCK):
        yield block
    if size % BLOCK:
        yield block[: size % BLOCK]


def raw_size_for_answer(answer_bytes: int) -> int:
    """The attachment size whose `attachments.get` answer weighs about `answer_bytes`."""
    return answer_bytes * 3 // 4 - 64


def _json(status: int, found: Any, headers: dict[str, str] | None = None) -> Reply:
    body = json.dumps(found, ensure_ascii=False).encode("utf-8")
    return Reply(
        status, {"Content-Type": "application/json; charset=UTF-8", **(headers or {})}, body
    )


def gmail_error(kind: str, message: str | None = None, reason: str | None = None) -> Reply:
    status, default_reason, domain, word, text = _ERRORS[kind]
    text = message or text
    headers = {}
    if status == 401:
        headers["WWW-Authenticate"] = (
            'Bearer realm="https://accounts.google.com/", error="invalid_token"'
        )
    return _json(
        status,
        {
            "error": {
                "code": status,
                "message": text,
                "errors": [{"message": text, "domain": domain, "reason": reason or default_reason}],
                "status": word,
            }
        },
        headers,
    )


def oauth_error(status: int, error: str, description: str) -> Reply:
    return _json(status, {"error": error, "error_description": description})


def _page(status: int, title: str, body: str) -> Reply:
    text = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title></head><body>{body}</body></html>"
    )
    return Reply(status, {"Content-Type": "text/html; charset=utf-8"}, text.encode("utf-8"))


# --- a mailbox ------------------------------------------------------------------------------


def _gmail_headers(email: str, message: Mapping[str, Any], when_ms: int) -> list[list[str]]:
    """The headers Gmail itself writes above what the sender wrote, from the fixture's `gmail`
    block: `{"domain", "from_domain"?, "mailfrom"?, "dkim"?, "spf"?, "dmarc"?, "fetched"?}`
    (assumption A4)."""
    gmail = message.get("gmail")
    if not isinstance(gmail, dict):
        return []
    domain = gmail.get("domain", "example.org")
    mailfrom = gmail.get("mailfrom", f"bounce@{domain}")
    stamp = datetime.fromtimestamp(when_ms / 1000, UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")
    dkim, spf, dmarc = (gmail.get(name, "pass") for name in ("dkim", "spf", "dmarc"))
    results = [f"dkim={dkim} header.i=@{domain} header.s=s1 header.b=FaKe0000"]
    from_domain = gmail.get("from_domain", domain)
    results.append(
        f"spf={spf} (google.com: domain of {mailfrom} designates 203.0.113.7 as permitted "
        f"sender) smtp.mailfrom={mailfrom}"
    )
    results.append(f"dmarc={dmarc} (p=REJECT sp=REJECT dis=NONE) header.from={from_domain}")
    headers = [
        ["Delivered-To", email],
        ["Received", f"by 2002:a05:6402:fake with SMTP id fake{when_ms}; {stamp}"],
        ["Return-Path", f"<{mailfrom}>"],
        [
            "Received",
            f"from mail.{domain} (mail.{domain}. [203.0.113.7]) by mx.google.com with ESMTPS "
            f"id fake-{when_ms} for <{email}>; {stamp}",
        ],
        [
            "Received-SPF",
            f"{spf} (google.com: domain of {mailfrom} designates 203.0.113.7 as permitted "
            "sender) client-ip=203.0.113.7;",
        ],
        ["Authentication-Results", "mx.google.com; " + "; ".join(results)],
    ]
    if gmail.get("fetched"):
        headers.append(["X-Gmail-Fetch-Info", f"{gmail['fetched']} 1 pop.example.org 995 someone"])
    return headers


def _part(mime: str, data: bytes, charset: str | None, part_id: str) -> dict[str, Any]:
    content_type = f'{mime}; charset="{charset}"' if charset else mime
    return {
        "partId": part_id,
        "mimeType": mime,
        "filename": "",
        "headers": [{"name": "Content-Type", "value": content_type}],
        "body": {"size": len(data), "data": b64url(data)},
    }


class Mailbox:
    """One account of the fake. A fixture message is

    {"id", "threadId", "labelIds", "internalDate" (ISO or ms), "gmail": {...}?,
     "headers": [[name, value], ...],            what the sender wrote, in order
     "text"?, "html"?, "charset"? (utf-8), "bytes_base64"? (a body that is not valid text),
     "pad_bytes"? (the text body is filled up to that size when it is asked in full),
     "attachments"?: [{"filename", "mimeType", "base64" | "size" (generated content)}],
     "payload"? (a complete Gmail payload, used as it is)}
    """

    def __init__(self, fixture: Mapping[str, Any], now_ms: int) -> None:
        self.email: str = fixture["email"]
        self.history_id = int(fixture.get("historyId", 1000))
        self.history_floor = int(fixture.get("historyFloor", 1))
        self.messages: dict[str, dict[str, Any]] = {}
        self.attachments: dict[str, dict[str, Any]] = {}
        self.history: list[dict[str, Any]] = []
        for message in fixture.get("messages", []):
            self._store(message)
        for spec in fixture.get("generate", []):
            for message in generate_messages(spec, now_ms):
                self._store(message)

    def _store(self, source: Mapping[str, Any]) -> dict[str, Any]:
        source = copy.deepcopy(dict(source))
        self.history_id += 1
        when = parse_time(source.get("internalDate", NOW))
        message_id = source.get("id") or secrets.token_hex(8)
        source.update(
            id=message_id,
            threadId=source.get("threadId") or message_id,
            labelIds=list(source.get("labelIds", ["INBOX"])),
            internalDate=when,
            historyId=self.history_id,
        )
        source["headers"] = _gmail_headers(self.email, source, when) + [
            list(pair) for pair in source.get("headers", [])
        ]
        for index, attachment in enumerate(source.get("attachments", [])):
            attachment_id = attachment.get("attachmentId") or (
                "ANGjdJ_"
                + hashlib.sha256(f"{self.email}/{message_id}/{index}".encode()).hexdigest()
            )
            attachment["attachmentId"] = attachment_id
            if "base64" in attachment:
                attachment["size"] = len(base64.b64decode(attachment["base64"]))
            self.attachments[attachment_id] = {**attachment, "messageId": message_id}
        self.messages[message_id] = source
        return source

    # -- changes, each with a history record --

    def _record(self, message: Mapping[str, Any], **change: Any) -> None:
        ref = {"id": message["id"], "threadId": message["threadId"]}
        self.history.append({"id": str(self.history_id), "messages": [ref], **change})

    def add(self, source: Mapping[str, Any]) -> dict[str, Any]:
        message = self._store(source)
        ref = {
            "id": message["id"],
            "threadId": message["threadId"],
            "labelIds": message["labelIds"],
        }
        self._record(message, messagesAdded=[{"message": ref}])
        return message

    def delete(self, message_id: str) -> bool:
        message = self.messages.pop(message_id, None)
        if message is None:
            return False
        self.history_id += 1
        ref = {
            "id": message["id"],
            "threadId": message["threadId"],
            "labelIds": message["labelIds"],
        }
        self._record(message, messagesDeleted=[{"message": ref}])
        return True

    def relabel(self, message_id: str, add: list[str], remove: list[str]) -> bool:
        message = self.messages.get(message_id)
        if message is None:
            return False
        labels = [name for name in message["labelIds"] if name not in remove]
        labels += [name for name in add if name not in labels]
        message["labelIds"] = labels
        for names, key in ((add, "labelsAdded"), (remove, "labelsRemoved")):
            if not names:
                continue
            self.history_id += 1
            message["historyId"] = self.history_id
            ref = {"id": message["id"], "threadId": message["threadId"], "labelIds": labels}
            self._record(message, **{key: [{"message": ref, "labelIds": list(names)}]})
        return True

    # -- Gmail's view of a message --

    def header(self, message: Mapping[str, Any], name: str) -> str:
        """The LAST header of that name: the one the sender wrote, under Gmail's own."""
        found = [value for key, value in message["headers"] if key.lower() == name.lower()]
        return found[-1] if found else ""

    def _body(self, message: Mapping[str, Any], full: bool) -> bytes:
        if "bytes_base64" in message:
            return base64.b64decode(message["bytes_base64"])
        data = message.get("text", "").encode(message.get("charset", "utf-8"), "replace")
        pad = int(message.get("pad_bytes", 0))
        if full and pad > len(data):
            filler = b"Lorem ipsum dolor sit amet. "
            data += filler * ((pad - len(data)) // len(filler) + 1)
            data = data[:pad]
        return data

    def payload(self, message: Mapping[str, Any], full: bool = True) -> dict[str, Any]:
        if "payload" in message:
            return copy.deepcopy(message["payload"])
        charset = message.get("charset", "utf-8")
        parts: list[dict[str, Any]] = []
        if "text" in message or "bytes_base64" in message or "html" not in message:
            parts.append(_part("text/plain", self._body(message, full), charset, "0"))
        if "html" in message:
            parts.append(
                _part("text/html", message["html"].encode(charset, "replace"), charset, "1")
            )
        if len(parts) == 1:
            body = parts[0]
        else:
            body = {
                "partId": "",
                "mimeType": "multipart/alternative",
                "filename": "",
                "headers": [
                    {"name": "Content-Type", "value": 'multipart/alternative; boundary="alt"'}
                ],
                "body": {"size": 0},
                "parts": parts,
            }
        attachments = message.get("attachments", [])
        if attachments:
            files = [
                {
                    "partId": str(index + 1),
                    "mimeType": item.get("mimeType", "application/octet-stream"),
                    "filename": item["filename"],
                    "headers": [
                        {
                            "name": "Content-Type",
                            "value": f'{item.get("mimeType", OCTETS)}; name="{item["filename"]}"',
                        },
                        {
                            "name": "Content-Disposition",
                            "value": f'attachment; filename="{item["filename"]}"',
                        },
                    ],
                    "body": {"attachmentId": item["attachmentId"], "size": item["size"]},
                }
                for index, item in enumerate(attachments)
            ]
            body = {
                "partId": "",
                "mimeType": "multipart/mixed",
                "filename": "",
                "headers": [{"name": "Content-Type", "value": 'multipart/mixed; boundary="mix"'}],
                "body": {"size": 0},
                "parts": [body, *files],
            }
        own = body.pop("headers")
        body["partId"] = ""
        body["headers"] = [{"name": name, "value": value} for name, value in message["headers"]]
        if not any(name.lower() == "content-type" for name, _ in message["headers"]):
            body["headers"] += own
        return body

    def snippet(self, message: Mapping[str, Any]) -> str:
        if "snippet" in message:
            return message["snippet"]
        text = message.get("text") or re.sub(r"<[^>]+>", " ", message.get("html", ""))
        return " ".join(text.split())[:200]

    def resource(self, message: Mapping[str, Any], fmt: str, wanted: list[str]) -> dict[str, Any]:
        found: dict[str, Any] = {
            "id": message["id"],
            "threadId": message["threadId"],
            "labelIds": list(message["labelIds"]),
            "snippet": self.snippet(message),
            "historyId": str(message["historyId"]),
            "internalDate": str(message["internalDate"]),
            "sizeEstimate": max(
                int(message.get("pad_bytes", 0)),
                len(message.get("text", "")) + len(message.get("html", "")) + 2048,
            ),
        }
        if fmt == "minimal":
            return found
        payload = self.payload(message, full=fmt == "full")
        if fmt == "metadata":
            headers = payload["headers"]
            if wanted:
                lowered = {name.lower() for name in wanted}
                headers = [item for item in headers if item["name"].lower() in lowered]
            payload = {"mimeType": payload["mimeType"], "headers": headers}
        found["payload"] = payload
        return found

    # -- search --

    def document(self, message: Mapping[str, Any]) -> dict[str, Any]:
        body = message.get("text", "") + " " + re.sub(r"<[^>]+>", " ", message.get("html", ""))
        return {
            "from": self.header(message, "From").lower(),
            "to": self.header(message, "To").lower(),
            "cc": self.header(message, "Cc").lower(),
            "subject": self.header(message, "Subject").lower(),
            "text": body.lower(),
            "labels": {name.lower() for name in message["labelIds"]},
            "ms": message["internalDate"],
            "msgid": self.header(message, "Message-ID").strip().strip("<>").lower(),
            "files": [item["filename"].lower() for item in message.get("attachments", [])],
        }

    def search(self, q: str, now_ms: int, include_spam_trash: bool) -> list[dict[str, Any]]:
        node = parse_query(q)
        anywhere = include_spam_trash or bool(
            re.search(r"\b(in|label):(anywhere|spam|trash)\b", q, re.IGNORECASE)
        )
        found = []
        for message in self.messages.values():
            document = self.document(message)
            if not anywhere and document["labels"] & {"spam", "trash"}:
                continue
            if matches(node, document, now_ms):
                found.append(message)
        return sorted(found, key=lambda item: (-item["internalDate"], item["id"]))


def generate_messages(spec: Mapping[str, Any], now_ms: int) -> Iterator[dict[str, Any]]:
    """Messages made from a fixture's `generate` entry, the same every time:
    `{"count", "seed", "prefix", "days", "companies": [{"name", "domain"}], "people": [...],
    "templates": [{"subject", "text", "labelIds"?}], "threadId"?}`. `{company}`, `{person}` and
    `{n}` are replaced in the templates. With `threadId` they all join one conversation."""
    rng = random.Random(spec.get("seed", 1))  # noqa: S311, a fixture, not a secret
    count = int(spec["count"])
    span_ms = int(spec.get("days", 200)) * 86_400_000
    for n in range(count):
        company = rng.choice(spec["companies"])
        person = rng.choice(spec.get("people", ["The hiring team"]))
        template = rng.choice(spec["templates"])
        when = now_ms - (n * span_ms // max(1, count)) - rng.randrange(3_600_000)
        fill = {"company": company["name"], "person": person, "n": n}
        local = person.lower().replace(" ", ".")
        message_id = f"{spec.get('prefix', 'gen')}-{n:04d}"
        yield {
            "id": message_id,
            "threadId": spec.get("threadId") or f"t-{message_id}",
            "labelIds": template.get("labelIds", ["INBOX"]),
            "internalDate": when,
            "gmail": {"domain": company["domain"]},
            "headers": [
                ["From", f"{person} <{local}@{company['domain']}>"],
                ["To", spec.get("to", "e2e-bulk@example.org")],
                ["Subject", template["subject"].format(**fill)],
                [
                    "Date",
                    datetime.fromtimestamp(when / 1000, UTC).strftime(
                        "%a, %d %b %Y %H:%M:%S +0000"
                    ),
                ],
                ["Message-ID", f"<{message_id}@{company['domain']}>"],
            ],
            "text": template["text"].format(**fill),
        }


# --- Gmail's search, the part the module uses -----------------------------------------------

_TOKEN = re.compile(r'\s*(\(|\)|\{|\}|"[^"]*"|-(?=[^\s])|[^\s(){}"]+)')
_OPERATORS = frozenset(
    {
        "from", "to", "cc", "subject", "rfc822msgid", "after", "before", "newer_than",
        "older_than", "in", "label", "is", "has", "filename",
    }
)  # fmt: skip
_AGE = re.compile(r"^(\d+)([dmy])$")


def parse_query(q: str) -> Any:
    tokens = _TOKEN.findall(q or "")
    if "".join(tokens).replace(" ", "") != re.sub(r"\s+", "", q or ""):
        raise UnsupportedQueryError("unreadable query")
    position = 0

    def group(closer: str | None, any_of: bool) -> Any:
        nonlocal position
        alternatives: list[list[Any]] = [[]]
        while position < len(tokens):
            token = tokens[position]
            if token == closer:
                position += 1
                break
            if token in (")", "}"):
                raise UnsupportedQueryError("unbalanced")
            if token == "OR":
                position += 1
                alternatives.append([])
                continue
            alternatives[-1].append(term())
        else:
            if closer is not None:
                raise UnsupportedQueryError("unbalanced")
        if any_of:
            return ("or", [item for block in alternatives for item in block])
        return ("or", [("and", block) for block in alternatives])

    def term() -> Any:
        nonlocal position
        token = tokens[position]
        position += 1
        if token == "-":
            return ("not", term())
        if token == "(":
            return group(")", False)
        if token == "{":
            return group("}", True)
        if token.startswith('"'):
            return ("word", token.strip('"').lower())
        name, colon, value = token.partition(":")
        if not colon:
            return ("word", token.lower())
        if name.lower() not in _OPERATORS:
            raise UnsupportedQueryError(name)
        if value:
            return ("op", name.lower(), ("word", value.lower()))
        if position >= len(tokens):
            raise UnsupportedQueryError("operator without a value")
        return ("op", name.lower(), term())

    return group(None, False)


def _when(value: str) -> int:
    if value.isdigit():
        return int(value) * 1000
    try:
        return int(datetime.strptime(value, "%Y/%m/%d").replace(tzinfo=UTC).timestamp() * 1000)
    except ValueError:
        raise UnsupportedQueryError("date") from None


def _age_ms(value: str) -> int:
    found = _AGE.match(value)
    if found is None:
        raise UnsupportedQueryError("age")
    return int(found.group(1)) * {"d": 1, "m": 30, "y": 365}[found.group(2)] * 86_400_000


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text) is not None


def matches(node: Any, document: Mapping[str, Any], now_ms: int) -> bool:
    kind = node[0]
    if kind == "and":
        return all(matches(item, document, now_ms) for item in node[1])
    if kind == "or":
        return any(matches(item, document, now_ms) for item in node[1])
    if kind == "not":
        return not matches(node[1], document, now_ms)
    if kind == "word":
        return any(_has_word(document[name], node[1]) for name in ("subject", "text", "from", "to"))
    name, value = node[1], node[2]
    if value[0] != "word":  # from:(a OR b): the operator applies to each word of the group
        if value[0] == "not":
            return not matches(("op", name, value[1]), document, now_ms)
        test = all if value[0] == "and" else any
        return test(matches(("op", name, item), document, now_ms) for item in value[1])
    word = value[1]
    if name in ("from", "to", "cc"):
        return word in document[name]
    if name == "subject":
        return _has_word(document["subject"], word)
    if name == "rfc822msgid":
        return document["msgid"] == word.strip("<>")
    if name == "after":
        return document["ms"] >= _when(word)
    if name == "before":
        return document["ms"] < _when(word)
    if name == "newer_than":
        return document["ms"] >= now_ms - _age_ms(word)
    if name == "older_than":
        return document["ms"] < now_ms - _age_ms(word)
    if name in ("in", "label"):
        return word == "anywhere" or word in document["labels"]
    if name == "is":
        if word == "read":
            return "unread" not in document["labels"]
        return word in document["labels"]
    if name == "has":
        if word != "attachment":
            raise UnsupportedQueryError("has")
        return bool(document["files"])
    return any(word in filename for filename in document["files"])  # filename:


# --- the fake -------------------------------------------------------------------------------


class FakeGoogle:
    def __init__(
        self,
        *,
        client_id: str = CLIENT_ID,
        client_secret: str = CLIENT_SECRET,
        now: str = NOW,
        hold_s: float = 30.0,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.hold_s = hold_s
        self._lock = threading.RLock()
        self._seed: list[dict[str, Any]] = []
        self._start = parse_time(now)
        self.reset()

    # -- state --

    def reset(self) -> None:
        with self._lock:
            self.now_ms = self._start
            self.mailboxes: dict[str, Mailbox] = {}
            self.codes: dict[str, dict[str, Any]] = {}
            self.refresh_tokens: dict[str, dict[str, Any]] = {}
            self.access_tokens: dict[str, dict[str, Any]] = {}
            self.failures: dict[str, deque[dict[str, Any]]] = {}
            self.switches = {name: values[0] for name, values in SWITCHES.items()}
            self.calls: list[dict[str, Any]] = []
            self.sent: list[dict[str, Any]] = []
            for fixture in self._seed:
                self.seed(fixture)

    def seed(self, fixture: Mapping[str, Any] | str | Path) -> Mailbox:
        """Seed or replace one mailbox, from a fixture or from the name of one of
        fixtures/mailboxes (`alice`, `bob`, `bulk`)."""
        if isinstance(fixture, str | Path):
            path = Path(fixture)
            if not path.suffix:
                path = FIXTURES / f"{fixture}.json"
            fixture = json.loads(path.read_text(encoding="utf-8"))
        with self._lock:
            mailbox = Mailbox(fixture, self.now_ms)  # type: ignore[arg-type]
            self.mailboxes[mailbox.email] = mailbox
            return mailbox

    def keep_seed(self, fixture: Mapping[str, Any]) -> None:
        """Seed a mailbox that comes back after every reset (the service's --seed)."""
        self._seed.append(dict(fixture))
        self.seed(fixture)

    def grant(self, email: str, scopes: tuple[str, ...] | list[str] = ALL_SCOPES) -> str:
        """A grant made without the browser: the refresh token of `email`."""
        with self._lock:
            if email not in self.mailboxes:
                raise KeyError(email)
            token = "1//fake-" + secrets.token_urlsafe(48)
            self.refresh_tokens[token] = {"email": email, "scopes": sorted(scopes)}
            return token

    def revoke(self, email: str) -> None:
        with self._lock:
            for store in (self.refresh_tokens, self.access_tokens):
                for token in [key for key, held in store.items() if held["email"] == email]:
                    del store[token]

    def expire_access(self, email: str | None = None) -> None:
        with self._lock:
            for token in [
                key for key, held in self.access_tokens.items() if email in (None, held["email"])
            ]:
                del self.access_tokens[token]

    def fail(self, endpoint: str, kind: str, times: int = 1, **more: Any) -> None:
        with self._lock:
            queue = self.failures.setdefault(endpoint, deque())
            queue.extend({"kind": kind, **more} for _ in range(times))

    def switch(self, name: str, value: str) -> None:
        if value not in SWITCHES[name]:
            raise ValueError(f"{name} is one of {SWITCHES[name]}")
        self.switches[name] = value

    def calls_of(
        self, endpoint: str | None = None, email: str | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            return [
                call
                for call in self.calls
                if endpoint in (None, call["endpoint"]) and email in (None, call["email"])
            ]

    def _access(self, email: str, scopes: list[str]) -> str:
        token = "ya29.fake-" + secrets.token_urlsafe(64)
        self.access_tokens[token] = {
            "email": email,
            "scopes": scopes,
            "expires_ms": self.now_ms + TOKEN_TTL_S * 1000,
        }
        return token

    # -- one request --

    def handle(
        self, method: str, target: str, headers: Mapping[str, str], body: bytes = b""
    ) -> Reply:
        """Answer one request. `headers` has lower case names."""
        parts = urlsplit(target)
        query = parse_qs(parts.query, keep_blank_values=True)
        with self._lock:
            endpoint, reply, email = self._route(method.upper(), parts.path, query, headers, body)
            if not parts.path.startswith("/__control/") and parts.path != "/healthz":
                self.calls.append(
                    {
                        "n": len(self.calls) + 1,
                        "endpoint": endpoint,
                        "method": method.upper(),
                        "path": parts.path,
                        # Of a token call, the kind of grant only: never a code or a token.
                        "query": {
                            "grant_type": dict(parse_qsl(body.decode("utf-8", "replace"))).get(
                                "grant_type"
                            )
                        }
                        if endpoint == "token"
                        else {name: values[-1] for name, values in query.items()},
                        "email": email,
                        "status": reply.status if reply.fault is None else reply.fault,
                    }
                )
            return reply

    def _route(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        headers: Mapping[str, str],
        body: bytes,
    ) -> tuple[str, Reply, str | None]:
        if path == "/healthz":
            return "healthz", _json(200, {"ok": True}), None
        if path.startswith("/__control/"):
            return (
                "control",
                self._control(method, path.removeprefix("/__control/"), query, body),
                None,
            )
        if path == "/o/oauth2/v2/auth" and method == "GET":
            return "consent", self._consent(query), None
        if path == "/o/oauth2/v2/auth/decision" and method == "POST":
            return "decision", self._decision(parse_qs(body.decode("utf-8", "replace"))), None
        if path == "/token" and method == "POST":
            form = dict(parse_qsl(body.decode("utf-8", "replace")))
            return "token", self._failing("token") or self._token(form), None
        if path == "/revoke" and method == "POST":
            form = dict(parse_qsl(body.decode("utf-8", "replace")))
            return "revoke", self._failing("revoke") or self._revoke(form), None
        if path == "/v1/userinfo" and method == "GET":
            held = self._bearer(headers)
            if held is None:
                return "userinfo", gmail_error("401"), None
            failing = self._failing("userinfo")
            if failing is not None:
                return "userinfo", failing, held["email"]
            sub = str(int(hashlib.sha256(held["email"].encode()).hexdigest()[:15], 16))
            found = {"sub": sub, "email": held["email"], "email_verified": True}
            return "userinfo", _json(200, found), held["email"]
        for name, verb, pattern in ROUTES:
            match = pattern.match(path)
            if match is None or verb != method:
                continue
            held = self._bearer(headers)
            if held is None:
                return name, gmail_error("401"), None
            email = held["email"]
            failing = self._failing(name)
            if failing is not None:
                return name, failing, email
            if GMAIL_SCOPE.get(name, SCOPE_READONLY) not in held["scopes"]:
                return name, gmail_error("403_scope"), email
            mailbox = self.mailboxes[email]
            try:
                return name, self._gmail(name, mailbox, match, query, body), email
            except UnsupportedQueryError as error:
                reply = gmail_error(
                    "400", f"fake: unsupported search ({error})", "fakeUnsupportedQuery"
                )
                return name, reply, email
        return "unknown", gmail_error("404", "fake: no such path"), None

    def _bearer(self, headers: Mapping[str, str]) -> dict[str, Any] | None:
        scheme, _, token = headers.get("authorization", "").partition(" ")
        held = self.access_tokens.get(token.strip()) if scheme.lower() == "bearer" else None
        if held is None or held["expires_ms"] <= self.now_ms or held["email"] not in self.mailboxes:
            return None
        return held

    def _failing(self, endpoint: str) -> Reply | None:
        queue = self.failures.get(endpoint)
        if not queue:
            return None
        failure = queue.popleft()
        kind = failure["kind"]
        if kind == "timeout":
            return Reply(fault="timeout")
        if kind == "truncated":
            return Reply(
                200,
                {"Content-Type": "application/json; charset=UTF-8"},
                b'{"messages": [{"id": "18c',
            )
        if kind == "html":
            return _page(
                502,
                "Error 502 (Server Error)!!1",
                "<p>The server encountered a temporary error.</p>",
            )
        if kind == "invalid_grant":
            return oauth_error(400, "invalid_grant", "Token has been expired or revoked.")
        reply = gmail_error(kind)
        if failure.get("retry_after") is not None:
            reply.headers["Retry-After"] = str(failure["retry_after"])
        return reply

    # -- OAuth --

    def _redirect_ok(self, uri: str) -> bool:
        parts = urlsplit(uri)
        return parts.scheme in ("http", "https") and parts.path == REDIRECT_PATH and not parts.query

    def _consent(self, query: dict[str, list[str]]) -> Reply:
        ask = {name: values[-1] for name, values in query.items()}
        if ask.get("client_id") != self.client_id:
            return _page(401, "Error 401: invalid_client", "<h1>Error 401: invalid_client</h1>")
        if not self._redirect_ok(ask.get("redirect_uri", "")):
            return _page(
                400, "Error 400: redirect_uri_mismatch", "<h1>Error 400: redirect_uri_mismatch</h1>"
            )
        if ask.get("response_type") != "code" or not ask.get("scope"):
            return _page(400, "Error 400: invalid_request", "<h1>Error 400: invalid_request</h1>")
        if ask.get("code_challenge_method") != "S256" or not ask.get("code_challenge"):
            return _page(
                400,
                "Error 400: invalid_request",
                "<h1>Error 400: invalid_request</h1><p>fake: PKCE with S256 is required</p>",
            )
        hidden = "".join(
            f'<input type="hidden" name="{name}" '
            f'value="{html.escape(ask.get(name, ""), quote=True)}">'
            for name in ("client_id", "redirect_uri", "state", "code_challenge", "scope")
        )
        boxes = "".join(
            f'<label><input type="checkbox" name="granted" '
            f'value="{html.escape(scope, quote=True)}" checked> '
            f"{html.escape(scope)}</label><br>"
            for scope in ask["scope"].split()
            if scope != "openid"
        )
        account = html.escape(ask.get("login_hint", ""), quote=True)
        form = (
            "<h1>Sign in with Google (fake)</h1>"
            '<form method="post" action="/o/oauth2/v2/auth/decision">'
            f"{hidden}"
            f'<label>Account <input type="email" name="account" value="{account}" required></label>'
            f"<fieldset><legend>jobapp wants access to</legend>{boxes}</fieldset>"
            '<button type="submit" name="decision" value="allow">Allow</button> '
            '<button type="submit" name="decision" value="deny" formnovalidate>Deny</button>'
            "</form>"
        )
        return _page(200, "Sign in with Google (fake)", form)

    def _decision(self, form: dict[str, list[str]]) -> Reply:
        one = {name: values[-1] for name, values in form.items()}
        redirect_uri = one.get("redirect_uri", "")
        if one.get("client_id") != self.client_id or not self._redirect_ok(redirect_uri):
            return _page(400, "Error 400: invalid_request", "<h1>Error 400: invalid_request</h1>")
        back = {"state": one.get("state", "")}
        if one.get("decision") != "allow":
            back["error"] = "access_denied"
            return Reply(302, {"Location": f"{redirect_uri}?{urlencode(back)}"})
        email = one.get("account", "").strip().lower()
        if email not in self.mailboxes:
            return _page(
                400,
                "Couldn't find your Google Account",
                "<h1>Couldn't find your Google Account</h1><p>fake: seed this mailbox first</p>",
            )
        asked = one.get("scope", "").split()
        granted = sorted(
            {"openid", *(scope for scope in form.get("granted", []) if scope in asked)}
        )
        code = "4/0fake-" + secrets.token_urlsafe(40)
        self.codes[code] = {
            "email": email,
            "scopes": granted,
            "challenge": one.get("code_challenge", ""),
            "redirect_uri": redirect_uri,
            "expires_ms": self.now_ms + CODE_TTL_S * 1000,
        }
        back.update(code=code, scope=" ".join(granted), authuser="0", prompt="consent")
        return Reply(302, {"Location": f"{redirect_uri}?{urlencode(back)}"})

    def _token(self, form: dict[str, str]) -> Reply:
        if (
            form.get("client_id") != self.client_id
            or form.get("client_secret") != self.client_secret
        ):
            return oauth_error(401, "invalid_client", "Unauthorized")
        if form.get("grant_type") == "refresh_token":
            held = self.refresh_tokens.get(form.get("refresh_token", ""))
            if held is None:
                return oauth_error(400, "invalid_grant", "Token has been expired or revoked.")
            return _json(
                200,
                {
                    "access_token": self._access(held["email"], held["scopes"]),
                    "expires_in": TOKEN_TTL_S,
                    "scope": " ".join(held["scopes"]),
                    "token_type": "Bearer",
                },
            )
        if form.get("grant_type") != "authorization_code":
            return oauth_error(400, "unsupported_grant_type", "Invalid grant_type")
        held = self.codes.pop(form.get("code", ""), None)
        if held is None or held["expires_ms"] <= self.now_ms:
            return oauth_error(400, "invalid_grant", "Bad Request")
        if form.get("redirect_uri") != held["redirect_uri"]:
            return oauth_error(400, "redirect_uri_mismatch", "Bad Request")
        verifier = form.get("code_verifier", "")
        if not verifier:
            return oauth_error(400, "invalid_grant", "Missing code verifier.")
        if (
            b64url(hashlib.sha256(verifier.encode("ascii", "replace")).digest())
            != held["challenge"]
        ):
            return oauth_error(400, "invalid_grant", "Invalid code verifier.")
        refresh = "1//fake-" + secrets.token_urlsafe(48)
        self.refresh_tokens[refresh] = {"email": held["email"], "scopes": held["scopes"]}
        claims = {
            "iss": "https://accounts.google.com",
            "email": held["email"],
            "email_verified": True,
        }
        id_token = (
            ".".join(b64url(json.dumps(part).encode()) for part in ({"alg": "none"}, claims))
            + ".fake"
        )
        return _json(
            200,
            {
                "access_token": self._access(held["email"], held["scopes"]),
                "expires_in": TOKEN_TTL_S,
                "refresh_token": refresh,
                "scope": " ".join(held["scopes"]),
                "token_type": "Bearer",
                "id_token": id_token,
            },
        )

    def _revoke(self, form: dict[str, str]) -> Reply:
        token = form.get("token", "")
        held = self.refresh_tokens.get(token) or self.access_tokens.get(token)
        if held is None:
            return oauth_error(400, "invalid_token", "Token expired or revoked")
        self.revoke(held["email"])
        return Reply(200, {"Content-Type": "application/json; charset=UTF-8"}, b"{}")

    # -- Gmail --

    def _gmail(
        self,
        name: str,
        mailbox: Mailbox,
        match: re.Match[str],
        query: dict[str, list[str]],
        body: bytes,
    ) -> Reply:
        one = {key: values[-1] for key, values in query.items()}
        if name == "getProfile":
            threads = {message["threadId"] for message in mailbox.messages.values()}
            return _json(
                200,
                {
                    "emailAddress": mailbox.email,
                    "messagesTotal": len(mailbox.messages),
                    "threadsTotal": len(threads),
                    "historyId": str(mailbox.history_id),
                },
            )
        if name == "messages.list":
            return self._list(mailbox, one)
        if name == "messages.get":
            message = mailbox.messages.get(match.group(1))
            if message is None:
                return gmail_error("404")
            fmt = one.get("format", "full")
            if fmt not in ("full", "metadata", "minimal"):
                return gmail_error("400", "fake: format=raw is not served", "fakeUnsupportedFormat")
            return _json(200, mailbox.resource(message, fmt, query.get("metadataHeaders", [])))
        if name == "threads.get":
            messages = sorted(
                (item for item in mailbox.messages.values() if item["threadId"] == match.group(1)),
                key=lambda item: item["internalDate"],
            )
            if not messages:
                return gmail_error("404")
            fmt = one.get("format", "full")
            return _json(
                200,
                {
                    "id": match.group(1),
                    "historyId": str(max(item["historyId"] for item in messages)),
                    "messages": [
                        mailbox.resource(item, fmt, query.get("metadataHeaders", []))
                        for item in messages
                    ],
                },
            )
        if name == "attachments.get":
            held = mailbox.attachments.get(match.group(2))
            if (
                held is None
                or held["messageId"] != match.group(1)
                or match.group(1) not in mailbox.messages
            ):
                return gmail_error("404")
            return Reply(
                200, {"Content-Type": "application/json; charset=UTF-8"}, self._attachment(held)
            )
        if name == "history.list":
            return self._history(mailbox, one, query.get("historyTypes", []))
        return self._send(mailbox, body)

    def _list(self, mailbox: Mailbox, one: dict[str, str]) -> Reply:
        q = one.get("q", "")
        found = mailbox.search(q, self.now_ms, one.get("includeSpamTrash") == "true")
        size = max(1, min(500, int(one.get("maxResults", "100") or 100)))
        stamp = hashlib.sha256(q.encode("utf-8")).hexdigest()[:6]
        offset = 0
        token = one.get("pageToken")
        if token:
            try:
                marked, raw = token[:6], int(token[6:])
            except ValueError:
                marked, raw = "", -1
            if marked != stamp or raw < 0:
                return gmail_error("400", "Invalid pageToken")
            offset = raw
        if self.switches["repeat_page_token"] == "on":
            page, more, next_offset = found[:size], True, 0
        else:
            page = found[offset : offset + size]
            next_offset = offset + size
            more = next_offset < len(found)
        answer: dict[str, Any] = {"resultSizeEstimate": len(found)}
        if page:
            answer = {
                "messages": [{"id": item["id"], "threadId": item["threadId"]} for item in page],
                **answer,
            }
        if more:
            answer["nextPageToken"] = f"{stamp}{next_offset:014d}"
        return _json(200, answer)

    def _history(self, mailbox: Mailbox, one: dict[str, str], types: list[str]) -> Reply:
        start = one.get("startHistoryId", "")
        if not start.isdigit():
            return gmail_error("400", "Invalid startHistoryId")
        if int(start) < mailbox.history_floor:
            return gmail_error("404")
        keys = {
            "messageAdded": "messagesAdded",
            "messageDeleted": "messagesDeleted",
            "labelAdded": "labelsAdded",
            "labelRemoved": "labelsRemoved",
        }
        wanted = {keys[name] for name in types if name in keys} or set(keys.values())
        records = []
        for record in mailbox.history:
            if int(record["id"]) <= int(start):
                continue
            kept = {
                key: value
                for key, value in record.items()
                if key not in keys.values() or key in wanted
            }
            if any(key in kept for key in keys.values()):
                records.append(kept)
        size = max(1, min(500, int(one.get("maxResults", "100") or 100)))
        offset = int(one["pageToken"]) if one.get("pageToken", "").isdigit() else 0
        answer: dict[str, Any] = {"historyId": str(mailbox.history_id)}
        page = records[offset : offset + size]
        if page:
            answer = {"history": page, **answer}
        if offset + size < len(records):
            answer["nextPageToken"] = str(offset + size)
        return _json(200, answer)

    def _attachment(self, held: Mapping[str, Any]) -> Iterator[bytes]:
        size = int(held["size"])
        yield f'{{"size": {size}, "data": "'.encode("ascii")
        if "base64" in held:
            yield b64url(base64.b64decode(held["base64"])).encode("ascii")
        else:
            for piece in generated_bytes(held["attachmentId"], size):
                yield base64.urlsafe_b64encode(piece).rstrip(b"=")
        yield b'"}'

    def _send(self, mailbox: Mailbox, body: bytes) -> Reply:
        try:
            asked = json.loads(body.decode("utf-8"))
            raw = unb64url(asked["raw"])
        except (ValueError, KeyError, TypeError):
            return gmail_error(
                "400",
                "'raw' RFC822 payload message string or uploading message via /upload/* URL "
                "required",
            )
        thread_id = asked.get("threadId")
        if thread_id and not any(
            item["threadId"] == thread_id for item in mailbox.messages.values()
        ):
            return gmail_error("404")
        parsed = message_from_bytes(raw)
        headers = [[name, str(value)] for name, value in parsed.items()]
        given = parsed.get("Message-ID")
        kept = given
        if given is None or self.switches["send_message_id"] == "replace":
            kept = f"<CAfake{secrets.token_hex(12)}@mail.gmail.com>"
            headers = [pair for pair in headers if pair[0].lower() != "message-id"]
            headers.append(["Message-ID", kept])
        payload = parsed.get_payload(decode=True) if not parsed.is_multipart() else b""
        message = mailbox.add(
            {
                "id": secrets.token_hex(8),
                "threadId": thread_id,
                "labelIds": ["SENT"],
                "internalDate": self.now_ms,
                "headers": headers,
                "text": (payload or b"").decode(parsed.get_content_charset() or "utf-8", "replace"),
            }
        )
        self.sent.append(
            {
                "email": mailbox.email,
                "id": message["id"],
                "threadId": message["threadId"],
                "messageId": kept,
                "messageIdGiven": given,
                "headers": {name: value for name, value in headers},
                "raw": asked["raw"],
            }
        )
        if self.switches["send_answer"] == "drop":
            return Reply(fault="drop")
        if self.switches["send_answer"] == "error":
            return gmail_error("503")
        return _json(
            200, {"id": message["id"], "threadId": message["threadId"], "labelIds": ["SENT"]}
        )

    # -- the control API --

    def _control(self, method: str, name: str, query: dict[str, list[str]], body: bytes) -> Reply:
        one = {key: values[-1] for key, values in query.items()}
        if method == "GET" and name == "calls":
            return _json(200, {"calls": self.calls_of(one.get("endpoint"), one.get("email"))})
        if method == "GET" and name == "sent":
            sent = [item for item in self.sent if one.get("email") in (None, item["email"])]
            return _json(200, {"sent": sent})
        if method != "POST":
            return _json(404, {"error": "fake: no such control"})
        try:
            asked = json.loads(body.decode("utf-8")) if body.strip() else {}
            return _json(200, self._act(name, asked))
        except (KeyError, ValueError, TypeError) as error:
            return _json(400, {"error": f"fake: {type(error).__name__}: {error}"})

    def _act(self, name: str, asked: dict[str, Any]) -> dict[str, Any]:
        if name == "reset":
            self.reset()
        elif name == "mailboxes":
            mailbox = self.seed(asked.get("name") or asked)
            return {
                "email": mailbox.email,
                "messages": len(mailbox.messages),
                "historyId": str(mailbox.history_id),
            }
        elif name == "grant":
            return {"refresh_token": self.grant(asked["email"], asked.get("scopes", ALL_SCOPES))}
        elif name == "revoke":
            self.revoke(asked["email"])
        elif name == "expire-access":
            self.expire_access(asked.get("email"))
        elif name == "fail":
            more = {key: asked[key] for key in ("retry_after",) if key in asked}
            if asked["kind"] not in _ERRORS and asked["kind"] not in (
                "timeout",
                "truncated",
                "html",
                "invalid_grant",
            ):
                raise ValueError("kind")
            self.fail(asked["endpoint"], asked["kind"], int(asked.get("times", 1)), **more)
        elif name == "switch":
            self.switch(asked["name"], asked["value"])
        elif name == "clock":
            self.now_ms = parse_time(asked["now"])
        elif name == "history-floor":
            self.mailboxes[asked["email"]].history_floor = int(asked["historyId"])
        elif name == "messages":
            message = self.mailboxes[asked["email"]].add(asked["message"])
            return {
                "id": message["id"],
                "threadId": message["threadId"],
                "historyId": str(message["historyId"]),
            }
        elif name == "delete-message":
            return {"deleted": self.mailboxes[asked["email"]].delete(asked["id"])}
        elif name == "labels":
            done = self.mailboxes[asked["email"]].relabel(
                asked["id"], asked.get("add", []), asked.get("remove", [])
            )
            return {"changed": done}
        else:
            raise KeyError(name)
        return {"ok": True}

    # -- in process --

    def transport(self) -> Any:
        """An httpx transport that answers from this fake, without a socket."""
        import httpx

        def respond(request: httpx.Request) -> httpx.Response:
            headers = {name.lower(): value for name, value in request.headers.items()}
            target = request.url.raw_path.decode("ascii")
            reply = self.handle(request.method, target, headers, request.content)
            if reply.fault == "timeout":
                raise httpx.ReadTimeout("fake: no answer", request=request)
            if reply.fault is not None:
                raise httpx.RemoteProtocolError("fake: closed without an answer", request=request)
            if isinstance(reply.body, bytes):
                return httpx.Response(reply.status, headers=reply.headers, content=reply.body)
            pieces = reply.body

            async def stream() -> Any:
                for piece in pieces:
                    yield piece

            return httpx.Response(reply.status, headers=reply.headers, content=stream())

        return httpx.MockTransport(respond)


# --- as a service ---------------------------------------------------------------------------


def make_handler(fake: FakeGoogle) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            headers = {name.lower(): value for name, value in self.headers.items()}
            reply = fake.handle(self.command, self.path, headers, body)
            self.close_connection = True
            if reply.fault is not None:
                if reply.fault == "timeout":
                    time.sleep(fake.hold_s)  # the caller's timeout passes first
                return
            self.send_response(reply.status)
            for name, value in reply.headers.items():
                self.send_header(name, value)
            self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-store")
            if isinstance(reply.body, bytes):
                self.send_header("Content-Length", str(len(reply.body)))
                self.end_headers()
                self.wfile.write(reply.body)
                return
            self.end_headers()
            for piece in reply.body:
                self.wfile.write(piece)

        do_GET = _serve  # noqa: N815, the names http.server calls
        do_POST = _serve  # noqa: N815

        def log_message(self, format: str, *args: Any) -> None:
            return  # addresses carry search terms and codes: the fake logs nothing

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The fake Google of the mail module's tests.")
    parser.add_argument("--port", type=int, default=8098)
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104, a test service in a container
    parser.add_argument(
        "--seed", type=Path, help="a folder of mailbox fixtures, kept across resets"
    )
    parser.add_argument(
        "--hold", type=float, default=30.0, help="seconds a `timeout` failure holds"
    )
    parser.add_argument("--client-id", default=CLIENT_ID)
    parser.add_argument("--client-secret", default=CLIENT_SECRET)
    args = parser.parse_args(argv)
    fake = FakeGoogle(client_id=args.client_id, client_secret=args.client_secret, hold_s=args.hold)
    if args.seed is not None:
        for path in sorted(args.seed.glob("*.json")):
            fake.keep_seed(json.loads(path.read_text(encoding="utf-8")))
    server = ThreadingHTTPServer((args.host, args.port), make_handler(fake))
    print(f"fake google on {args.host}:{args.port}, {len(fake.mailboxes)} mailboxes", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
