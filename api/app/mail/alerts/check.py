"""Check a real alert e-mail against its parser, locally (SPEC-mail.md, "Job alert e-mails").

    uv run python -m app.mail.alerts.check indeed ~/Downloads/alert.eml

The file is Gmail's "Show original", then "Download original", of one alert: the message with
the headers Gmail wrote. This prints whether the sender is allowed and authenticated, and the
jobs the parser reads from it. It reads the file only: no network call, nothing stored. The file
is the person's own mail: never commit it (the repository is public).
"""

from __future__ import annotations

import base64
import email
import sys
from email import policy
from email.message import Message
from pathlib import Path
from typing import Any

from app.mail import parse
from app.mail.alerts.providers import PROVIDERS


def _part(message: Message, part_id: str) -> dict[str, Any]:
    made: dict[str, Any] = {
        "partId": part_id,
        "mimeType": message.get_content_type(),
        "filename": message.get_filename() or "",
        "headers": [{"name": name, "value": str(value)} for name, value in message.items()],
        "body": {"size": 0},
    }
    if message.is_multipart():
        children = message.get_payload()
        made["parts"] = [
            _part(child, f"{part_id}.{index}" if part_id else str(index))
            for index, child in enumerate(children if isinstance(children, list) else [])
        ]
    else:
        raw = message.get_payload(decode=True)
        raw = raw if isinstance(raw, bytes) else b""
        data = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        made["body"] = {"size": len(raw), "data": data}
    return made


def gmail_answer(raw: bytes, *, message_id: str = "local0000000001", internal_ms: int = 0) -> dict:
    """An RFC 5322 message in the shape of Gmail's `messages.get` with `format=full`: the
    transfer encodings undone, each part as base64url, as Gmail answers."""
    message = email.message_from_bytes(raw, policy=policy.compat32)
    return {
        "id": message_id,
        "threadId": message_id,
        "labelIds": ["INBOX"],
        "snippet": "",
        "internalDate": str(internal_ms),
        "payload": _part(message, ""),
    }


def main(argv: list[str]) -> int:
    from app.mail.alerts import source

    if len(argv) != 2 or argv[0] not in PROVIDERS:
        print(f"usage: python -m app.mail.alerts.check {{{','.join(PROVIDERS)}}} FILE.eml")
        return 2
    provider = PROVIDERS[argv[0]]
    answer = gmail_answer(Path(argv[1]).expanduser().read_bytes())
    message = parse.parse(answer, "")
    print(f"refused: {source.refused(message, provider, answer) or 'no'}")
    parsed = provider.parse_text(message.body_text) if message.body_text else None
    if parsed is None or not parsed.jobs:
        print("text part: no job (the HTML fallback needs the parser service)")
    else:
        print(f"jobs: {len(parsed.jobs)}, dropped blocks: {parsed.dropped}")
        for job in parsed.jobs:
            print(f"* {job.title} | {job.company} | {job.location} | {job.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
