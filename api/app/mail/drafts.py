"""Canonical draft validation and hashing shared by review and the gate."""

import hashlib
import json
import re

from app.mail import domains
from app.mail.draft_models import MailDraft

_LOCAL = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}")


def address(value: str) -> str:
    if not isinstance(value, str) or len(value) > 254 or value.count("@") != 1:
        raise ValueError("Use one complete email address.")
    local, host = value.rsplit("@", 1)
    normal = domains.normalise(host)
    if (
        not _LOCAL.fullmatch(local)
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or normal is None
    ):
        raise ValueError("Use one complete email address without a display name.")
    return f"{local}@{normal}"


def validate(draft: MailDraft) -> None:
    if not 1 <= len(draft.to_addrs) + len(draft.cc_addrs) <= 20:
        raise ValueError("Choose between one and twenty recipients.")
    for item in [*draft.to_addrs, *draft.cc_addrs]:
        if address(item) != item:
            raise ValueError("Check the recipient address.")
    if not draft.subject or len(draft.subject) > 998 or any(c in draft.subject for c in "\r\n"):
        raise ValueError("Use a subject of at most 998 characters on one line.")
    if len(draft.body) > 100_000:
        raise ValueError("Use a message of at most 100000 characters.")


def content_hash(draft: MailDraft) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "to": draft.to_addrs,
                "cc": draft.cc_addrs,
                "subject": draft.subject,
                "body": draft.body,
                "documents": draft.document_versions,
                "kind": draft.kind,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
