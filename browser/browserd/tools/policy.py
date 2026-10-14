"""What the tool layer refuses before it touches the page (browser B5): the host allow list a lease
is given at its creation, the schemes, the documents placed for upload, the keys, the size of a
snapshot. Pure functions over strings and bytes, unit tested as tables (test_b5_unit.py).

Hosts. An entry is a host name (`boards.greenhouse.io`, the test stack's single label `pages`) or
`*.` and a domain of at least two labels (`*.greenhouse.io`: any name under it, never the domain
itself). A URL is let through when its scheme is http or https, it has no user part, and its host
(lower case, a final dot dropped) is an entry or under a wildcard one. Fail closed: a URL with a
character outside printable ASCII (an IDN is given in its `xn--` form), a backslash (which a
browser reads as a slash, so `https://evil.example\\@pages/` goes to evil.example) or that does
not parse is refused as `bad_url`.
"""

import re
from typing import Any
from urllib.parse import urlsplit

HOSTS_MAX = 20
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
HOST_ENTRY = re.compile(rf"^(?:\*\.{_LABEL}(?:\.{_LABEL})+|{_LABEL}(?:\.{_LABEL})*)$")
ALLOWED_SCHEMES = ("http", "https")
URL_MAX = 2048

SNAPSHOT_MAX = 40_000
TEXT_MAX = 10_000  # one browser_type
SELECTORS_MAX, SELECTOR_MAX = 20, 200

FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,78}\.pdf$")
FILE_MAX = 20 * 1024 * 1024
FILES_MAX = 3
PDF_MAGIC = b"%PDF-"

# browser_press_key: what filling a form needs, nothing that opens, prints or saves.
KEYS = frozenset(
    {"Tab", "Shift+Tab", "Enter", "Escape", "Backspace", "Delete", "Space", "ArrowUp",
     "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"}
)  # fmt: skip
WAIT_MAX_S = 10.0


def parse_hosts(raw: Any) -> tuple[str, ...]:
    """The allow list of a lease's request; ValueError when an entry is not a host name."""
    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > HOSTS_MAX:
        raise ValueError("hosts")
    hosts = []
    for entry in raw:
        if not isinstance(entry, str):
            raise ValueError("hosts")  # noqa: TRY004 - one error kind: the request's 400
        entry = entry.strip().lower().rstrip(".")
        if not HOST_ENTRY.fullmatch(entry) or entry.rsplit(".", 1)[-1].isdigit():
            raise ValueError("hosts")
        hosts.append(entry)
    return tuple(dict.fromkeys(hosts))


def parse_selectors(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > SELECTORS_MAX:
        raise ValueError("submit_selectors")
    if not all(isinstance(s, str) and 0 < len(s.strip()) <= SELECTOR_MAX for s in raw):
        raise ValueError("submit_selectors")
    return tuple(s.strip() for s in raw)


def host_allowed(host: str, hosts: tuple[str, ...]) -> bool:
    host = host.lower().rstrip(".")
    if not host:
        return False
    for entry in hosts:
        if entry.startswith("*."):
            if host.endswith(entry[1:]) and len(host) > len(entry) - 1:
                return True
        elif host == entry:
            return True
    return False


def url_verdict(url: Any, hosts: tuple[str, ...]) -> str | None:
    """None when the lease may navigate to `url`, else the refusal's code: `bad_url`,
    `refused_scheme` or `host_not_allowed`."""
    if not isinstance(url, str) or not 0 < len(url) <= URL_MAX:
        return "bad_url"
    if any(not 0x21 <= ord(ch) <= 0x7E for ch in url) or "\\" in url:
        return "bad_url"
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        _ = parts.port
    except ValueError:
        return "bad_url"
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        return "refused_scheme"
    if "@" in parts.netloc or not host:
        return "bad_url"
    return None if host_allowed(host, hosts) else "host_not_allowed"


def cut_snapshot(text: str, limit: int = SNAPSHOT_MAX) -> tuple[str, bool]:
    """The snapshot within `limit` characters, cut at a line end, with a marker saying so."""
    if len(text) <= limit:
        return text, False
    marker = (
        f"\n[snapshot cut at {limit} characters: {len(text) - limit} more not shown]"
    )
    head = text[: limit - len(marker)]
    if "\n" in head:
        head = head[: head.rindex("\n")]
    return head + marker, True


def file_verdict(name: str, data: bytes, held: dict[str, bytes]) -> str | None:
    """None when `data` may be placed on the lease under `name`, else the code."""
    if not FILE_NAME.fullmatch(name):
        return "bad_file_name"
    if len(data) > FILE_MAX:
        return "file_too_large"
    if not data.startswith(PDF_MAGIC):
        return "not_a_pdf"
    if name not in held and len(held) >= FILES_MAX:
        return "too_many_files"
    return None


REF = re.compile(r"^(?:f\d{1,4})?e\d{1,7}$")


def ref_of(args: dict[str, Any]) -> str:
    ref = args.get("ref")
    if not isinstance(ref, str) or not REF.fullmatch(ref):
        raise ValueError("ref")
    return ref
