"""The allow list of job alert senders (SPEC-mail.md, "Job alert e-mails: a narrow exception").

One row per provider the app reads: its key (the one Find jobs setup turns on, `search.
alert_emails.<key>`), the exact sender addresses of its alert e-mails, and its parsers. A mail
is read for a provider only when its one `From` address is EXACTLY one of that provider's
`senders` (lower case, no alias, no other address of the same domain) and Gmail authenticated
it (`mail/auth.py`). A provider whose format could not be established from a public source has
no row: its TODO is in the spec, never a guessed parser.

`verified` says, per address, where it was seen: in the code or the recorded mails of a public
parser (sources in the spec). The owner confirms each one on a real alert of his mailbox before
relying on it; until then a wrong address fails closed (its mail is not read).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType

from app.mail.alerts import indeed, linkedin
from app.mail.alerts.text import Parsed


@dataclass(frozen=True)
class Provider:
    key: str
    label: str  # as the setup and the stats name it
    senders: tuple[str, ...]  # exact, lower case
    parse_text: Callable[[str], Parsed]  # the text part
    # The parser service's text version of the HTML part, for a mail without a usable text part;
    # None when the HTML layout is not established.
    parse_html_text: Callable[[str], Parsed] | None


PROVIDERS: MappingProxyType[str, Provider] = MappingProxyType(
    {
        "indeed": Provider(
            key="indeed",
            label="Indeed",
            # alert@indeed.com: job-joy's recorded fr.indeed alerts (March 2026), Emmanuelpean/jam.
            # donotreply@jobalert.indeed.com: 0xCAF3D0OD/job-bot (real alerts, October 2026),
            # MarvinLeRouge/JobFlow. Not donotreply@match.indeed.com: single "match" mails whose
            # links carry no job key.
            senders=("alert@indeed.com", "donotreply@jobalert.indeed.com"),
            parse_text=indeed.parse_text,
            parse_html_text=indeed.parse_html_text,
        ),
        "linkedin": Provider(
            key="linkedin",
            label="LinkedIn",
            # jobalerts-noreply@linkedin.com: the alerts of the person's saved searches, in every
            # public LinkedIn alert parser. Not jobs-listings@ or jobs-noreply@: LinkedIn's own
            # recommendations, which the person did not ask for.
            senders=("jobalerts-noreply@linkedin.com",),
            parse_text=linkedin.parse_text,
            parse_html_text=None,
        ),
    }
)
KEYS = tuple(PROVIDERS)


def source_host(key: str) -> str:
    """The `host` of a provider's stats entry: "mail:indeed"."""
    return f"mail:{key}"
