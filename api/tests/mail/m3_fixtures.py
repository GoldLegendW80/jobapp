"""The hand made mails of mail M3, in Gmail's shape.

`fixtures/m3/messages.json` holds messages written by hand for these tests: invented people at
`example.org`, `example.com`, `example.net` and `evil.example`, no real mail, no real company.
Each is in the shape of `messages.get` with `format=full`, except that a part states its body
as `text` (with an optional `charset`) where Gmail sends base64url in `body.data`; `message()`
makes that conversion. `gmail: true` puts the lines Gmail writes on top of a mail it took by
SMTP (assumption A4) above the sender's headers. `fixtures/m3/html/` holds the bodies the
sanitiser is tested on, one trick per file.
"""

import base64
import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent / "fixtures" / "m3"
ME = "e2e-reader@example.org"
_SOURCE = json.loads((HERE / "messages.json").read_text(encoding="utf-8"))
NAMES = tuple(_SOURCE["messages"])


def encoded(raw: bytes) -> str:
    """Bytes as Gmail sends them: base64url without padding."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def part(source: dict[str, Any]) -> dict[str, Any]:
    """A part of Gmail's from its readable form."""
    body: dict[str, Any] = {"size": 0}
    if "raw" in source:
        body = {"size": len(source["raw"]), "data": encoded(source["raw"])}
    elif "text" in source:
        raw = source["text"].encode(source.get("charset", "utf-8"))
        body = {"size": len(raw), "data": encoded(raw)}
    if "attachmentId" in source:
        body["attachmentId"] = source["attachmentId"]
    if "size" in source:
        body["size"] = source["size"]
    made: dict[str, Any] = {
        "partId": source.get("partId", ""),
        "mimeType": source.get("mimeType", "text/plain"),
        "filename": source.get("filename", ""),
        "headers": [{"name": name, "value": value} for name, value in source.get("headers", [])],
        "body": body,
    }
    if "parts" in source:
        made["parts"] = [part(child) for child in source["parts"]]
    return made


def gmail_lines() -> list[list[str]]:
    return [list(pair) for pair in _SOURCE["gmail_lines"]]


def build(source: dict[str, Any]) -> dict[str, Any]:
    """A whole answer of `messages.get` from the readable form of a message."""
    headers = (gmail_lines() if source.get("gmail") else []) + [
        *source.get("headers", []),
        *source.get("headers_part", []),
    ]
    return {
        "id": source.get("id", "m3b0000000000001"),
        "threadId": source.get("threadId", "t3b0000000000001"),
        "labelIds": list(source.get("labelIds", ["INBOX"])),
        "snippet": source.get("snippet", ""),
        "historyId": "1",
        "internalDate": source.get("internalDate", "1791795600000"),
        "sizeEstimate": 1,
        "payload": part({**source, "headers": headers}),
    }


def message(name: str) -> dict[str, Any]:
    return build(_SOURCE["messages"][name])


def simple(headers: list[list[str]], *, gmail: bool = True, **more: Any) -> dict[str, Any]:
    """A one part message with these headers of the sender."""
    return build(
        {"headers": headers, "gmail": gmail, "mimeType": "text/plain", "text": "hello", **more}
    )


def page(name: str) -> str:
    return (HERE / "html" / f"{name}.html").read_text(encoding="utf-8")
