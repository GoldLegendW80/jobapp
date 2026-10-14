"""What a mail is once it has been read, and what binds it to a posting (mail M3).

Two frozen types and nothing that computes:

- `ParsedMessage` is what `mail/parse.py` makes of one message as Gmail returns it: every column
  of `mail_messages` that comes from the mail itself. Two columns do not come from here:
  `body_html_sanitized` and `has_remote_images` are the parser service's answer
  (`parser_client.sanitise_mail_html`, called by M7), and `before_connection` is the sync's.
- `Binding` is the result of the binding rule (tasks/mail/todo.md, "The binding rule"). The type
  lives here so that every reader imports it from one place, but **only `mail/binding.py` (M5)
  builds one**: a test of M13 walks `api/app` and fails on any other constructor call.

Every string here was written by a stranger. Nothing of it is ever logged or put in an error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Direction = Literal["inbound", "outbound"]

# The five bases of B of the binding rule, in the order the rule states them.
BASES = ("posting_host", "user_wrote", "user_confirmed", "ats_reference", "ats_posting")


@dataclass(frozen=True)
class Attachment:
    """A part with a file name, listed and never fetched: `mail_messages.attachments` holds one
    object per attachment with these four keys."""

    part_id: str  # Gmail's `partId` ("1", "0.2"): what the download asks Gmail for later
    name: str  # safe to show and to offer as a file name: no path, no control character
    mime_type: str  # what the sender declared, never trusted: the download sniffs the bytes
    size: int  # bytes, as Gmail counts them

    def as_json(self) -> dict[str, str | int]:
        return {
            "part_id": self.part_id,
            "name": self.name,
            "type": self.mime_type,
            "size": self.size,
        }


@dataclass(frozen=True)
class ParsedMessage:
    """One message of Gmail's, read. Field names are the columns of `mail_messages`."""

    gmail_message_id: str
    gmail_thread_id: str
    message_id_header: str | None  # "<id@host>", or None when absent or not of that shape
    references_header: str  # the ids of `References`, one space between them, "" when none
    in_reply_to: str | None  # the first id of `In-Reply-To` (no column: M5 reads it with the above)
    from_addr: str | None  # the one address of the one `From`, lower case; None otherwise
    from_name: str  # its display name, decoded, "" when none
    reply_to: str | None
    to_addrs: tuple[str, ...]
    cc_addrs: tuple[str, ...]
    received_at: datetime  # Gmail's `internalDate`, UTC
    subject: str
    snippet: str
    # The first `text/plain` part, "" when the message has none. M7 then takes the text of the
    # parser's answer, or the snippet when the parser failed.
    body_text: str
    # The first `text/html` part, decoded and capped, **as the stranger wrote it**: hostile.
    # Its one use is to be sent to the parser service. It is never stored, shown or logged.
    raw_html: str | None
    # A part was cut at the cap, the tree was deeper or wider than is walked, or a list of
    # recipients was longer than is read (or stood in a second header).
    truncated: bool
    # A of the binding rule (mail/auth.py). `from_domain` is the registrable domain of the
    # visible `From`; it is a claim of the sender unless `trusted` is true.
    trusted: bool
    from_domain: str | None
    suspicious: bool
    direction: Direction
    labels: tuple[str, ...]
    attachments: tuple[Attachment, ...]


@dataclass(frozen=True)
class Binding:
    """Whether a mail is bound to a posting, and on which basis. Built only by
    `mail/binding.py` (M5). A bound one names its basis and the domain that binds; one that is
    not bound names why as a code (`not_trusted`, `company_ambiguous`, ...; M5 owns the list)."""

    bound: bool
    basis: str | None = None
    domain: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.bound:
            if self.basis not in BASES or not self.domain or self.reason is not None:
                raise ValueError("a bound Binding has one of BASES, a domain and no reason")
        elif self.basis is not None or not self.reason:
            raise ValueError("a Binding that is not bound has a reason and no basis")
