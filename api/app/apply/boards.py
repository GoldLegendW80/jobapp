"""Job board listings: the page a posting's address opens is often a board's listing (Arbeitnow,
Apec, Cadremploi, Glassdoor, Himalayas, ...), not the employer's application form. Its Apply
link is read without clicking it, validated, and followed the way a LinkedIn company-site link
is (docs/apply-linkedin.md): the lease ends, the application is queued again with the new form
address and canonical key, and the next fill checks the posting, the documents, duplicates and
the site guards again. At most `HOPS_MAX` such follows per application.

A listing whose Apply control has no address (a script-only button), several different ones,
or one that fails the checks is never guessed: the fill stops with `board_link_missing`, and
the person applies on the site and says "I applied"."""

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.apply.ats import base
from app.browser.sources import parse_snapshot as links_snapshot
from app.browser.sources.linkedin import public_host
from app.mail import hosts

# Follows of a board's Apply link (and of a LinkedIn company-site link) per application.
HOPS_MAX = 2
FOLLOW_KINDS = ("company_site", "board_link")

# `needs` of the stops: the Apply control has no usable address; the follows ran out.
NO_LINK = "board_link_missing"
HOPS = "board_hops"

# Hosts that list the postings of many companies, beside the mail module's list.
BOARDS = frozenset(
    {
        "adzuna.com",
        "adzuna.fr",
        "arbeitnow.com",
        "arbeitnow.fr",
        "cadremploi.fr",
        "himalayas.app",
        "joinhandshake.com",
        "jobijoba.com",
        "meteojob.com",
        "remoteok.com",
        "remotive.com",
        "weworkremotely.com",
    }
)

# The names of a listing's Apply control, folded (`base.fold`).
APPLY_NAMES = frozenset(
    {
        "apply",
        "apply now",
        "apply for this job",
        "apply for this position",
        "apply for this role",
        "apply on company site",
        "apply on company website",
        "apply on the company website",
        "apply on employer site",
        "apply on employer website",
        "apply externally",
        "easy apply",
        "postuler",
        "postuler maintenant",
        "postuler a cette offre",
        "postuler a l'offre",
        "postuler sur le site de l'entreprise",
        "postuler sur le site du recruteur",
        "postuler sur le site de l'employeur",
        "candidater",
        "je candidate",
        "je postule",
        "bewerben",
        "jetzt bewerben",
    }
)

# A sign in, sign up or marketing page is never the application form.
_NOT_A_FORM = re.compile(
    r"(?:^|[/._-])(?:log-?in|sign-?in|sign_in|sign-?up|sign_up|register|inscription|connexion|"
    r"auth|oauth|account|compte|pricing|premium|subscribe|newsletter|employers?|recruiters?|"
    r"post-a-job|alerts?)(?:$|[/._?-])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Hop:
    """What a listing's Apply control gives: the address to follow, or none (`NO_LINK`)."""

    url: str | None


def is_board(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return hosts.list_of(host) == hosts.JOB_BOARD or _under(host, BOARDS)


def _under(host: str, names: frozenset[str]) -> bool:
    host = host.lower().removesuffix(".")
    return any(host == name or host.endswith("." + name) for name in names)


def _same(one: str, two: str) -> bool:
    def bare(url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit(
            (parts.scheme, (parts.hostname or "").lower(), parts.path.rstrip("/"), parts.query, "")
        )

    return bare(one) == bare(two)


def valid(candidate: str, page_url: str, stub_base: str | None) -> bool:
    """An http(s) address of a public host on its default port, without credentials, spaces or
    control characters, other than the listing itself, and not a sign in or marketing page.
    Under the fake forms server (tests only, refused in production) the host checks give way
    to the fixture's own address."""
    if any(c.isspace() or ord(c) < 32 for c in candidate) or len(candidate) > 2000:
        return False
    parts = urlsplit(candidate)
    try:
        port_ok = parts.port in (None, 80 if parts.scheme == "http" else 443)
    except ValueError:
        return False
    fixture = bool(stub_base and candidate.startswith(stub_base.rstrip("/") + "/"))
    return (
        parts.scheme in ("http", "https")
        and not parts.username
        and not parts.password
        and (fixture or (port_ok and public_host(parts.hostname or "") is not None))
        and not _same(candidate, page_url)
        and not _NOT_A_FORM.search(parts.path)
        and not _NOT_A_FORM.search(parts.hostname or "")
    )


def _application_form(snapshot: str) -> bool:
    """A page with a file field (a CV upload) is an application form, not a listing."""
    found = base.discover_fields(base.parse_snapshot(snapshot), frozenset())
    return any(field.kind == "file" for field in found.fields)


def listing_hop(snapshot: str, page_url: str, stub_base: str | None) -> Hop | None:
    """The hop of a listing page, or None when the page is not a listing to leave: it shows an
    application form, or it has no Apply control at all (the fill then goes on as before).
    A page counts as a listing on a known board's host, or when it shows no application form."""
    if _application_form(snapshot) and not is_board(page_url):
        return None
    links: set[str] = set()
    controls = 0
    for node in links_snapshot(snapshot).walk():
        if node.role not in ("link", "button") or base.fold(node.name) not in APPLY_NAMES:
            continue
        controls += 1
        if node.role == "link" and node.url:
            candidate = urljoin(page_url, node.url.strip())
            parts = urlsplit(candidate)
            links.add(urlunsplit(parts._replace(fragment="")) if parts.fragment else candidate)
    if not controls:
        return None
    good = {link for link in links if valid(link, page_url, stub_base)}
    # One address, and every Apply link names it; else nothing is guessed.
    return Hop(next(iter(good)) if len(good) == 1 and good == links else None)
