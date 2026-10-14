"""One message of Gmail's into a `ParsedMessage` (mail M3): pure functions, the standard
library only, run by the syncing worker.

    parse(gmail_message_json, own_address) -> ParsedMessage

`gmail_message_json` is the answer of `messages.get` with `format=full`. Everything in it but
its ids, its labels and its date was written by a stranger, so:

- **the tree is walked under caps**, without recursion: at most `MAX_DEPTH` levels and
  `MAX_PARTS` parts. Of those, the first `text/plain` and the first `text/html` that are not
  attachments are read, each decoded from base64url and from its declared charset (`CHARSETS`,
  a fixed list; anything else is read as UTF-8 with replacement) and cut at `MAX_PART_BYTES`.
  Every part with a file name is listed as an attachment and never descended into: its bytes
  are not fetched. A cut of any kind sets `truncated`;
- **the HTML is only carried** (`raw_html`): no library of this process parses it. The parser
  service does (`parser_client.sanitise_mail_html`, called by M7);
- **headers are read strictly.** Addresses go through `email.headerregistry` and then through a
  check of their own; what fails it is dropped. A subject or a name keeps its accents and its
  emoji and loses every control character, line break and direction override;
- **`suspicious`** is set by two `From` headers (or none), a `From` with several addresses or a
  group, a `From` that cannot be read, a display name that contains an address of another
  domain, and more headers than are read. A suspicious message is never trusted (mail/auth.py);
- **direction**: `outbound` when Gmail labelled the message `SENT` and its sender is the
  connected address. A stranger's mail that shows the user's own address is `inbound`.

Nothing is logged here and no exception carries a value: `NotAMessageError` names a field of
Gmail's answer, never its content.
"""

from __future__ import annotations

import base64
import binascii
import html
import re
import unicodedata
from collections.abc import Mapping
from datetime import UTC, datetime
from email import errors as email_errors
from email import policy
from typing import Any

from app.mail import auth, domains
from app.mail.types import Attachment, Direction, ParsedMessage

MAX_DEPTH = 20
MAX_PARTS = 200
MAX_PART_BYTES = 500_000
MAX_HEADERS = 1000
MAX_HEADER_VALUE = auth.MAX_VALUE
MAX_ADDRESSES = 100
MAX_SUBJECT = 1000
MAX_DISPLAY_NAME = 300
MAX_SNIPPET = 1000
MAX_REFERENCES = 50
MAX_LABELS = 100
MAX_FILE_NAME = 255
MAX_SIZE = 2**40

# Declared charset -> the codec that reads it. ISO-8859-1 is read as Windows-1252, as every
# browser does: senders that declare the first write the second.
CHARSETS = {
    "utf-8": "utf-8",
    "utf8": "utf-8",
    "us-ascii": "utf-8",
    "ascii": "utf-8",
    "iso-8859-1": "cp1252",
    "latin1": "cp1252",
    "windows-1252": "cp1252",
    "cp1252": "cp1252",
    "iso-8859-15": "iso-8859-15",
    "utf-16": "utf-16",
    "utf-16le": "utf-16-le",
    "utf-16be": "utf-16-be",
}
_SINGLE_BYTE = frozenset({"cp1252", "iso-8859-15"})

_CHARSET = re.compile(r"""charset\s*=\s*["']?([A-Za-z0-9._:-]{1,40})""", re.IGNORECASE)
_LOCAL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}")
_MESSAGE_ID = re.compile(r"<[!-;=?-~]{1,300}@[!-;=?-~]{1,300}>")
_INSIDE_NAME = re.compile(r"""[^\s<>()\[\]"',;:@]+@([^\s<>()\[\]"',;:@]+)""")
_MIME = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]{0,60}/[a-z0-9][a-z0-9!#$&^_.+-]{0,60}")
_PART_ID = re.compile(r"[0-9]{1,4}(?:\.[0-9]{1,4}){0,20}")
_ID = re.compile(r"[0-9A-Za-z_-]{1,64}")
_LABEL = re.compile(r"[0-9A-Za-z_-]{1,100}")
# Marks that reorder what is shown: "gpj.exe" after one of them reads "exe.jpg".
_DIRECTION = re.compile("[؜‎‏‪-‮⁦-⁩]")
_FORBIDDEN_IN_NAME = re.compile(r'[:*?"<>|]')
_BASE64URL = re.compile(r"[A-Za-z0-9_-]*")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class NotAMessageError(ValueError):
    """Gmail's answer is not a message: `field` names what is missing or of the wrong type.
    Never a value."""

    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.field = field


# --- text -----------------------------------------------------------------------------------------


def _line(text: str, limit: int) -> str:
    """`text` as one line that is safe to store and to show: no control character, no line
    break, no direction override, no lone surrogate, spaces collapsed, at most `limit`
    characters. Accents, emoji and the joiners inside an emoji are kept."""
    kept = [
        " " if char in "\r\n\t\x0b\x0c" else char
        for char in _DIRECTION.sub("", text)
        if char in "\r\n\t\x0b\x0c" or unicodedata.category(char) not in ("Cc", "Cs", "Co", "Cn")
    ]
    return " ".join("".join(kept).split())[:limit].rstrip()


def _body(text: str) -> str:
    """The text of a part as it is stored: Unix line ends, and no control character other than
    the line break and the tab (Postgres refuses U+0000)."""
    text = text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        char for char in text if char in "\n\t" or unicodedata.category(char) not in ("Cc", "Cs")
    )


def _decoded(name: str, value: str) -> Any:
    """`value` read by `email.headerregistry` as the header `name` (RFC 2047 words decoded), or
    None when the library gives up on it."""
    one_line = auth.unfold(value)
    if one_line is None:
        return None
    try:
        return policy.default.header_factory(name, one_line)
    except Exception:  # the library's own failure on a hostile value: not read
        return None


def _unstructured(value: str, limit: int) -> str:
    """A subject: its encoded words decoded, as one clean line."""
    value = _CONTROL.sub(" ", value[:MAX_HEADER_VALUE])
    header = _decoded("Subject", value)
    return _line(str(header) if header is not None else value, limit)


# --- addresses -----------------------------------------------------------------------------------


def _strict(address: Any) -> str | None:
    """`local@domain` in lower case when both halves are plain, else None. The domain may be
    written in another script; it is stored in its ASCII form."""
    local, domain = getattr(address, "username", None), getattr(address, "domain", None)
    if not isinstance(local, str) or not isinstance(domain, str):
        return None
    name = domains.normalise(domain)
    if name is None or not _LOCAL.fullmatch(local) or ".." in local or local[0] == ".":
        return None
    return f"{local.lower()}@{name}"


def _invalid(header: Any) -> bool:
    return any(isinstance(defect, email_errors.InvalidHeaderDefect) for defect in header.defects)


def _address_list(values: list[str]) -> tuple[str, ...]:
    """Every plain address of the headers `values`, each once, in order."""
    found: dict[str, None] = {}
    for value in values:
        header = _decoded("To", value[:MAX_HEADER_VALUE])
        for address in getattr(header, "addresses", ()):
            strict = _strict(address)
            if strict is not None and len(found) < MAX_ADDRESSES:
                found[strict] = None
    return tuple(found)


def _names_another_domain(display_name: str, address: str) -> bool:
    """Whether a display name shows an address of another organisation than the real one:
    `"hr@company.example" <x@evil.example>`. Read after compatibility folding, so that a full
    width `@` is an `@`."""
    mine = domains.registrable_domain(address.rsplit("@", 1)[-1])
    shown = unicodedata.normalize("NFKC", display_name)
    return any(
        domains.registrable_domain(found[1].rstrip(".")) != mine or mine is None
        for found in _INSIDE_NAME.finditer(shown)
    )


def _sender(values: list[str]) -> tuple[str | None, str, bool]:
    """(address, display name, suspicious) of the `From` headers of a message."""
    if len(values) != 1 or len(values[0]) > MAX_HEADER_VALUE:
        return None, "", True
    header = _decoded("From", values[0])
    if header is None or _invalid(header):
        return None, "", True
    groups = header.groups
    # One address, alone: a group has a name of its own, and holds any number of addresses.
    if len(groups) != 1 or groups[0].display_name is not None or len(groups[0].addresses) != 1:
        return None, "", True
    address = groups[0].addresses[0]
    strict = _strict(address)
    if strict is None:
        return None, "", True
    name = address.display_name or ""
    return strict, _line(name, MAX_DISPLAY_NAME), _names_another_domain(name, strict)


# --- the tree ------------------------------------------------------------------------------------


def _headers(part: Mapping[str, Any]) -> tuple[list[tuple[str, str]], bool]:
    """The (name, value) pairs of a part in Gmail's order, and whether some were left unread."""
    found = part.get("headers")
    if not isinstance(found, list):
        return [], False
    pairs = [
        (item["name"], item["value"])
        for item in found[:MAX_HEADERS]
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("value"), str)
    ]
    return pairs, len(found) > MAX_HEADERS


def _named(headers: list[tuple[str, str]], name: str) -> list[str]:
    return [value for found, value in headers if found.strip().lower() == name]


def _decode(part: Mapping[str, Any]) -> tuple[str, bool] | None:
    """(text, cut) of a text part, or None when it holds no readable data."""
    body = part.get("body")
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, str):
        return None
    # The cap is applied to the base64 before anything is decoded: 4 characters are 3 bytes.
    limit = (MAX_PART_BYTES // 3 + 1) * 4
    cut = len(data) > limit
    chunk = data[:limit].rstrip("=")
    if not _BASE64URL.fullmatch(chunk):
        return None
    if len(chunk) % 4 == 1:  # no base64 has this length, unless the cut made it
        if not cut:
            return None
        chunk = chunk[:-1]
    try:
        raw = base64.urlsafe_b64decode(chunk + "=" * (-len(chunk) % 4))
    except (binascii.Error, ValueError):
        return None
    if len(raw) > MAX_PART_BYTES:
        raw, cut = raw[:MAX_PART_BYTES], True
    headers, _ = _headers(part)
    declared = _CHARSET.search(" ".join(_named(headers, "content-type"))[:1000])
    codec = CHARSETS.get(declared[1].lower(), "utf-8") if declared else "utf-8"
    if codec in _SINGLE_BYTE and not raw.isascii():
        # A wrong declaration, the common one: UTF-8 sent as Latin-1. Bytes that are valid
        # UTF-8 with accents in them are UTF-8; Latin-1 text almost never is.
        try:
            return _strip_cut(raw.decode("utf-8"), cut), cut
        except UnicodeDecodeError:
            pass
    return _strip_cut(raw.decode(codec, "replace"), cut), cut


def _strip_cut(text: str, cut: bool) -> str:
    """A cut can fall inside a character: the half that is left is not text."""
    return text.rstrip("�") if cut else text


def safe_file_name(name: str) -> str:
    """A file name that can be shown and offered for download: its last path component, without
    control or format characters, without the characters a file system refuses, at most
    `MAX_FILE_NAME` characters with its extension kept. Every extension stays visible."""
    name = unicodedata.normalize("NFC", name[: MAX_FILE_NAME * 8])
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(char for char in name if unicodedata.category(char)[0] != "C")
    name = " ".join(_FORBIDDEN_IN_NAME.sub("_", name).split()).strip(". ")
    if len(name) > MAX_FILE_NAME:
        stem, dot, extension = name.rpartition(".")
        if dot and 0 < len(extension) <= 16:
            name = stem[: MAX_FILE_NAME - len(extension) - 1] + "." + extension
        else:
            name = name[:MAX_FILE_NAME]
    return name or "attachment"


def _attachment(part: Mapping[str, Any], name: str) -> Attachment:
    body = part.get("body")
    size = body.get("size") if isinstance(body, dict) else None
    part_id, mime = part.get("partId"), part.get("mimeType")
    mime = mime.strip().lower() if isinstance(mime, str) else ""
    return Attachment(
        part_id=part_id if isinstance(part_id, str) and _PART_ID.fullmatch(part_id) else "",
        name=safe_file_name(name),
        mime_type=mime if _MIME.fullmatch(mime) else "application/octet-stream",
        size=size if type(size) is int and 0 <= size <= MAX_SIZE else 0,
    )


def _walk(payload: Mapping[str, Any]) -> tuple[str | None, str | None, list[Attachment], bool]:
    """(plain text, HTML, attachments, cut) of a message's tree, in the order of the message."""
    text: str | None = None
    markup: str | None = None
    attachments: list[Attachment] = []
    cut = False
    seen = 0
    stack: list[tuple[Any, int]] = [(payload, 1)]
    while stack:
        part, depth = stack.pop()
        if not isinstance(part, dict):
            continue
        seen += 1
        if seen > MAX_PARTS:
            cut = True
            break
        name = part.get("filename")
        if isinstance(name, str) and name:
            attachments.append(_attachment(part, name))
            continue
        children = part.get("parts")
        if isinstance(children, list) and children:
            if depth >= MAX_DEPTH:
                cut = True
            else:
                # Only as many as can still be read, pushed so that the first is read first.
                stack.extend((child, depth + 1) for child in reversed(children[:MAX_PARTS]))
                cut = cut or len(children) > MAX_PARTS
            continue
        mime = part.get("mimeType")
        mime = mime.strip().lower() if isinstance(mime, str) else ""
        wanted = (mime == "text/plain" and text is None) or (mime == "text/html" and markup is None)
        headers, _ = _headers(part)
        disposition = " ".join(_named(headers, "content-disposition")).lstrip().lower()
        if not wanted or disposition.startswith("attachment"):
            continue
        decoded = _decode(part)
        if decoded is None:
            continue
        cut = cut or decoded[1]
        if mime == "text/plain":
            text = _body(decoded[0])
        else:
            markup = decoded[0].replace("\x00", "")
    return text, markup, attachments, cut


# --- the message ---------------------------------------------------------------------------------


def _message_ids(values: list[str]) -> list[str]:
    return [found for value in values for found in _MESSAGE_ID.findall(value[:MAX_HEADER_VALUE])]


def parse(gmail_message_json: Mapping[str, Any], own_address: str) -> ParsedMessage:
    """The message as it is stored. Raises NotAMessageError when Gmail's answer has no id, no
    thread, no date or no payload: that is Google's answer being wrong, not the mail."""
    message = gmail_message_json
    if not isinstance(message, Mapping):
        raise NotAMessageError("message")
    message_id, thread_id = message.get("id"), message.get("threadId")
    if not isinstance(message_id, str) or not _ID.fullmatch(message_id):
        raise NotAMessageError("id")
    if not isinstance(thread_id, str) or not _ID.fullmatch(thread_id):
        raise NotAMessageError("threadId")
    stamp = message.get("internalDate")
    if not isinstance(stamp, str) or not stamp.isascii() or not stamp.isdigit() or len(stamp) > 14:
        raise NotAMessageError("internalDate")
    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise NotAMessageError("payload")
    found_labels = message.get("labelIds")
    labels = tuple(
        label
        for label in (found_labels if isinstance(found_labels, list) else [])[:MAX_LABELS]
        if isinstance(label, str) and _LABEL.fullmatch(label)
    )

    headers, unread = _headers(payload)
    from_addr, from_name, odd_sender = _sender(_named(headers, "from"))
    suspicious = odd_sender or unread
    verdict = auth.authenticate(headers, from_addr, suspicious=suspicious)
    own = own_address.strip().lower() if isinstance(own_address, str) else ""
    direction: Direction = (
        "outbound" if "SENT" in labels and from_addr is not None and from_addr == own else "inbound"
    )

    references = _message_ids(_named(headers, "references"))
    if len(references) > MAX_REFERENCES:  # the root of the thread, then its nearest ancestors
        references = [references[0], *references[-(MAX_REFERENCES - 1) :]]
    message_ids = _message_ids(_named(headers, "message-id")[:1])
    in_reply_to = _message_ids(_named(headers, "in-reply-to")[:1])
    reply_to = _address_list(_named(headers, "reply-to")[:1])
    subjects = _named(headers, "subject")
    snippet = message.get("snippet")
    text, markup, attachments, cut = _walk(payload)

    return ParsedMessage(
        gmail_message_id=message_id,
        gmail_thread_id=thread_id,
        message_id_header=message_ids[0] if message_ids else None,
        references_header=" ".join(references),
        in_reply_to=in_reply_to[0] if in_reply_to else None,
        from_addr=from_addr,
        from_name=from_name,
        reply_to=reply_to[0] if reply_to else None,
        to_addrs=_address_list(_named(headers, "to")),
        cc_addrs=_address_list(_named(headers, "cc")),
        received_at=datetime.fromtimestamp(int(stamp) / 1000, UTC),
        subject=_unstructured(subjects[0], MAX_SUBJECT) if subjects else "",
        snippet=_line(html.unescape(snippet[: MAX_SNIPPET * 8]), MAX_SNIPPET)
        if isinstance(snippet, str)
        else "",
        body_text=text or "",
        raw_html=markup,
        truncated=cut,
        trusted=verdict.trusted,
        from_domain=verdict.from_domain,
        suspicious=suspicious,
        direction=direction,
        labels=labels,
        attachments=tuple(attachments),
    )
