"""The hosts that are nobody's own company (mail M3; read by M5, M7 and M10, edited by none).

B of the binding rule (tasks/mail/todo.md) binds a mail to a posting through "the host of the
posting's address, when that host is the company's own, that is, in none of the lists of
`mail/hosts.py`". These are the lists, and the one table:

- `ATS_SYSTEMS`, **the one constant about applicant tracking systems**: one row per system with
  the hosts its postings live on and the domains its mail is sent from. A mail of one system
  binds a posting only when the posting is hosted on that SAME system (`ats_posting`), which is
  why hosts and sending domains sit in one row and are never two lists that could drift.
- `JOB_BOARDS`: sites that list the postings of many companies.
- `SHARED_HOSTING`: suffixes under which anybody publishes a page (`notion.site`, `github.io`).
- `PUBLIC_MAILBOXES`: domains where anybody has an address (`gmail.com`).

An entry stands for itself and for every name under it: `greenhouse.io` covers
`job-boards.eu.greenhouse.io`. No name is in two lists, and no entry of one list sits under an
entry of another (tests/mail/test_hosts.py).

**Assumption A12.** Every `sending_domains` value below is what the author believes these
systems write in `From`, taken from no mailbox: the tests use hand made mails, and checkpoint
stage B corrects the table on the owner's real messages. A wrong row fails closed: a system mail
from a domain that is not listed binds nothing and becomes a proposal.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from app.mail import domains


@dataclass(frozen=True)
class AtsSystem:
    name: str
    posting_hosts: tuple[str, ...]
    sending_domains: tuple[str, ...]


ATS_SYSTEMS: tuple[AtsSystem, ...] = (
    AtsSystem("greenhouse", ("greenhouse.io",), ("greenhouse.io", "greenhouse-mail.io")),
    AtsSystem("lever", ("lever.co",), ("lever.co",)),
    AtsSystem("ashby", ("ashbyhq.com",), ("ashbyhq.com",)),
    AtsSystem("workable", ("workable.com",), ("workable.com", "workablemail.com")),
    AtsSystem("smartrecruiters", ("smartrecruiters.com",), ("smartrecruiters.com",)),
    AtsSystem(
        "workday",
        ("myworkdayjobs.com", "myworkdaysite.com"),
        ("myworkday.com", "workday.com"),
    ),
    AtsSystem("teamtailor", ("teamtailor.com",), ("teamtailor.com", "teamtailor-mail.com")),
    AtsSystem("recruitee", ("recruitee.com",), ("recruitee.com",)),
    AtsSystem("personio", ("personio.de", "personio.com"), ("personio.de", "personio.com")),
    AtsSystem("bamboohr", ("bamboohr.com",), ("bamboohr.com",)),
    AtsSystem("icims", ("icims.com",), ("icims.com",)),
    AtsSystem("jobvite", ("jobvite.com",), ("jobvite.com",)),
    AtsSystem("taleo", ("taleo.net",), ("taleo.net",)),
    AtsSystem(
        "successfactors",
        ("successfactors.com", "successfactors.eu"),
        ("successfactors.com", "successfactors.eu"),
    ),
)

JOB_BOARDS = frozenset(
    {
        "apec.fr",
        "businessfrance.fr",
        "francetravail.fr",
        "glassdoor.com",
        "glassdoor.fr",
        "hellowork.com",
        "indeed.com",
        "jobteaser.com",
        "linkedin.com",
        "monster.com",
        "monster.fr",
        "otta.com",
        "welcometothejungle.com",
        "wellfound.com",
        "workatastartup.com",
    }
)

SHARED_HOSTING = frozenset(
    {
        "airtable.com",
        "blogspot.com",
        "carrd.co",
        "docs.google.com",
        "firebaseapp.com",
        "forms.gle",
        "github.io",
        "herokuapp.com",
        "netlify.app",
        "notion.site",
        "notion.so",
        "pages.dev",
        "sites.google.com",
        "tally.so",
        "typeform.com",
        "vercel.app",
        "web.app",
        "webflow.io",
        "wixsite.com",
        "wordpress.com",
    }
)

PUBLIC_MAILBOXES = frozenset(
    {
        "aol.com",
        "bbox.fr",
        "fastmail.com",
        "free.fr",
        "gmail.com",
        "gmx.com",
        "gmx.de",
        "gmx.fr",
        "googlemail.com",
        "hey.com",
        "hotmail.com",
        "hotmail.fr",
        "icloud.com",
        "laposte.net",
        "live.com",
        "live.fr",
        "mac.com",
        "mail.com",
        "me.com",
        "msn.com",
        "neuf.fr",
        "orange.fr",
        "outlook.com",
        "outlook.fr",
        "pm.me",
        "proton.me",
        "protonmail.com",
        "sfr.fr",
        "tuta.io",
        "tutanota.com",
        "wanadoo.fr",
        "yahoo.com",
        "yahoo.fr",
        "yandex.com",
        "ymail.com",
        "zoho.com",
    }
)

# The names of the four lists, as `list_of` answers them.
ATS, JOB_BOARD, SHARED, PUBLIC_MAILBOX = "ats", "job_board", "shared_hosting", "public_mailbox"

_POSTING = MappingProxyType(
    {host: system for system in ATS_SYSTEMS for host in system.posting_hosts}
)
_SENDING = MappingProxyType(
    {domain: system for system in ATS_SYSTEMS for domain in system.sending_domains}
)
_LISTS = MappingProxyType(
    {
        **dict.fromkeys(_POSTING, ATS),
        **dict.fromkeys(_SENDING, ATS),
        **dict.fromkeys(JOB_BOARDS, JOB_BOARD),
        **dict.fromkeys(SHARED_HOSTING, SHARED),
        **dict.fromkeys(PUBLIC_MAILBOXES, PUBLIC_MAILBOX),
    }
)


def _under[Found](host: object, entries: MappingProxyType[str, Found]) -> Found | None:
    """The value of the entry `host` is, or sits under: the name itself, then each parent."""
    name = domains.normalise(host)
    if name is None:
        return None
    labels = name.split(".")
    for start in range(len(labels) - 1):
        found = entries.get(".".join(labels[start:]))
        if found is not None:
            return found
    return None


def list_of(host: object) -> str | None:
    """The list `host` is in (`ATS`, `JOB_BOARD`, `SHARED`, `PUBLIC_MAILBOX`), or None."""
    return _under(host, _LISTS)


def is_company_own(host: object) -> bool:
    """Whether `host` may be a company's own: a host name that is in none of the lists. What
    is not a host name at all is nobody's."""
    return domains.normalise(host) is not None and list_of(host) is None


def is_public_mailbox(domain: object) -> bool:
    return _under(domain, _LISTS) == PUBLIC_MAILBOX


def ats_of_posting_host(host: object) -> AtsSystem | None:
    """The system a posting at `host` is hosted on, or None."""
    return _under(host, _POSTING)


def ats_of_sending_domain(domain: object) -> AtsSystem | None:
    """The system that sends mail from `domain`, or None."""
    return _under(domain, _SENDING)
