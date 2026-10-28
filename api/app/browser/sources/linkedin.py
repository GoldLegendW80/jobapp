"""LinkedIn's addresses and walls, shared by the job website search (app/browser/platform_policy.py,
browser B14), Apply (app/apply/ats/linkedin.py, app/apply/site_profile.py) and the LinkedIn guard
(app/browser/linkedin_guard.py). LinkedIn's own fixed reader (browser B10) was deleted with the
company boards and the freelance feeds (owner, 2026-10-28: "Delete it"): LinkedIn is searched
only as a ticked job website, at the pace of SPEC-browser decision 2 (PAUSE_S between two pages
of the host on the shared per host pacer).

Walls: a page under `/checkpoint/` (LinkedIn's security check), or a page that is not what was
asked for and whose heading speaks of a security check or unusual activity, is
`linkedin_checkpoint`: the guard pauses every LinkedIn path for 7 days. A page under `/login`,
`/authwall`, `/uas/` or `/signup`, or a sign in heading on a page that is not what was asked, is
`linkedin_signin`.

The company's own Apply address behind LinkedIn's redirect (`external_url`) must name a public
host (`public_host`).
"""

import re
import unicodedata
from urllib.parse import SplitResult, parse_qs, urljoin, urlsplit, urlunsplit

from app.browser.sources import Node

HOST = "www.linkedin.com"
PAUSE_S = (10.0, 30.0)  # SPEC-browser decision 2: 10 to 30 seconds per page
URL_MAX = 2048

REDIRECTS = frozenset({("safety", "go"), ("jobs", "view", "externalApply")})
CHECKPOINT_WORDS = (
    "security check",
    "security verification",
    "unusual activity",
    "vérification de sécurité",
    "activité inhabituelle",
)
SIGNIN_HEADINGS = frozenset(
    {
        "sign in",
        "sign in to linkedin",
        "join linkedin",
        "welcome back",
        "s'identifier",
        "connexion",
        "rejoindre linkedin",
        "bon retour parmi nous",
    }
)
SIGNIN_SEGMENTS = frozenset({"login", "authwall", "uas", "signup"})

APOSTROPHES = {0x2019: "'", 0x02BC: "'"}  # typographic apostrophes


def norm(text: str) -> str:
    """NFKC, casefolded, typographic apostrophes as plain ones, whitespace collapsed."""
    text = unicodedata.normalize("NFKC", text).translate(APOSTROPHES)
    return " ".join(text.casefold().split())


def host(base_url: str | None) -> str:
    """The host whose pages LinkedIn's pace counts: www.linkedin.com, or the fixture server's."""
    return (urlsplit(base_url).hostname or "") if base_url else HOST


def same_site(parts: SplitResult, page_url: str) -> bool:
    """Whether a link (split) is on the page's own site: same scheme, host and port, no user."""
    page = urlsplit(page_url)
    try:
        ports = parts.port, page.port
    except ValueError:
        return False
    return (
        parts.username is None
        and parts.password is None
        and bool(parts.hostname)
        and (parts.scheme, parts.hostname, ports[0]) == (page.scheme, page.hostname, ports[1])
    )


def _linkedin(host: str | None) -> bool:
    return bool(host) and (host == "linkedin.com" or str(host).endswith(".linkedin.com"))


# Host suffixes that never name a company's public site: names of a home or office network, of
# this stack's own containers and tailnet, and the reserved names (RFC 6761, RFC 8375). A label
# equal to one, or a host ending with "." and one, is refused.
INTERNAL_SUFFIXES = (
    "localhost",
    "localdomain",
    "local",
    "internal",
    "intranet",
    "lan",
    "home",
    "corp",
    "private",
    "arpa",  # home.arpa, in-addr.arpa, ip6.arpa
    "ts.net",
    "test",
    "invalid",
    "onion",
    "nip.io",  # wildcard DNS that answers the address written in the name
    "sslip.io",
    "localtest.me",
)
DEFAULT_PORTS = {"http": 80, "https": 443}
# Dots a browser reads as the label separator (UTS 46): the ideographic and the full width ones.
DOTS = {0x3002: ".", 0xFF0E: ".", 0xFF61: "."}
# A public host, as an allow list: dot separated labels of letters, digits and hyphens, each 1 to
# 63 characters and neither starting nor ending with a hyphen. The last label starts with a letter,
# so no IPv4 literal in any form (dotted, decimal, hex, octal, short) can match.
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_TLD = re.compile(r"[a-z][a-z0-9-]*")
HOST_MAX = 253


def public_host(host: str) -> str | None:
    """`host` normalised (NFKC, browser dots, lowercase, IDNA checked) when it names a public
    site, None otherwise. Fails closed: the ASCII form of the name must be two or more labels of
    [a-z0-9-] with an alphabetic last label, so a percent sign, a bracket, an IPv6 or IPv4
    literal, a control character or an empty label is refused; so is an internal or reserved
    suffix."""
    if not host or "%" in host:
        return None
    name = unicodedata.normalize("NFKC", host).translate(DOTS).lower().removesuffix(".")
    if not name or "%" in name:
        return None
    try:
        ascii_name = name.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = ascii_name.split(".")
    if (
        len(ascii_name) > HOST_MAX
        or len(labels) < 2
        or not all(_LABEL.fullmatch(label) for label in labels)
        or not _TLD.fullmatch(labels[-1])
    ):
        return None
    if any(ascii_name == s or ascii_name.endswith("." + s) for s in INTERNAL_SUFFIXES):
        return None
    return name


def external_url(href: str, page_url: str) -> str | None:
    """The company's Apply address behind LinkedIn's redirect, None when it is not one: http or
    https, a public host (`public_host`), no user or password, no port other than the
    scheme's, no space or control character, at most URL_MAX characters. On the fixture server
    (tests), a target on the server's own site is kept too."""
    if len(href) > URL_MAX:
        return None
    parts = urlsplit(urljoin(page_url, href))
    if not (_linkedin(parts.hostname) and parts.scheme == "https") and not same_site(
        parts, page_url
    ):
        return None
    segments = tuple(s for s in parts.path.split("/") if s)
    if not any(segments[i : i + len(r)] == r for r in REDIRECTS for i in range(len(segments))):
        return None
    target = parse_qs(parts.query).get("url", [""])[0]
    if len(target) > URL_MAX or any(
        c.isspace() or unicodedata.category(c)[0] == "C" for c in target
    ):
        return None
    out = urlsplit(target)
    try:
        port = out.port
    except ValueError:
        return None  # a malformed port
    if (
        out.scheme not in ("http", "https")
        or not out.hostname
        or _linkedin(out.hostname)
        or out.username is not None
        or out.password is not None
    ):
        return None
    if not _linkedin(urlsplit(page_url).hostname) and same_site(out, page_url):
        host = out.hostname  # the fixture server's own site
    else:
        host = public_host(out.hostname)
        if host is None or port not in (None, DEFAULT_PORTS[out.scheme]):
            return None
    netloc = host
    if port is not None and port != DEFAULT_PORTS[out.scheme]:
        netloc = f"{netloc}:{port}"
    return urlunsplit((out.scheme, netloc, out.path or "/", out.query, ""))


def _segments(page_url: str, base_url: str | None) -> list[str]:
    path = urlsplit(page_url).path
    if base_url:
        prefix = urlsplit(base_url).path.rstrip("/") + "/linkedin"
        path = path[len(prefix) :] if path.startswith(prefix) else path
    return [s.casefold() for s in path.split("/") if s]


def wall_by_url(page_url: str, base_url: str | None) -> str | None:
    """The code of a wall page told by its address, None for any other page."""
    segments = _segments(page_url, base_url)
    if segments[:1] == ["checkpoint"]:
        return "linkedin_checkpoint"
    if segments[:1] and segments[0] in SIGNIN_SEGMENTS:
        return "linkedin_signin"
    return None


def wall_by_heading(root: Node) -> str | None:
    """The code of a wall page told by its headings (read only on a page that is not the one
    asked for), None when no heading is a wall's."""
    headings = [norm(n.label()) for n in root.walk() if n.role == "heading" and not n.hidden]
    if any(word in h for h in headings for word in CHECKPOINT_WORDS):
        return "linkedin_checkpoint"
    if any(h in SIGNIN_HEADINGS for h in headings):
        return "linkedin_signin"
    return None
