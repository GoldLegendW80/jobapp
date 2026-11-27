"""The one rule of the same posting (browser B31, owner 2026-11-26: "Je veux vraiment qu'on remonte
un max de job"): two addresses name one posting when they give one posting id of a board that has
ids (`BOARDS`, on any host of the board's family and any path), else when they are one address
once the parameters that only track are left out (`TRACKING` and the prefix, and the board's own
on its family), one trailing slash and the order of the parameters aside. A parameter not listed
identifies. Accept and refuse tables: api/tests/browser/test_b31_same_posting.py.

Two readers, so they cannot disagree (`test_the_store_and_the_session_read_one_rule`): the website
search's session (app/browser/finder.py, what it saw and what a report names) and the store's
duplicate key (app/jobs/slug.py, `PostingKey`, `url_key` of this rule's key). Pure: no state.
"""

import re
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

# Owner, 2026-10-29 (B19, "Let it use the search boxes"): each website's own hosts are allowed.
# A platform's hosts are a family, as browserd's discovery.FAMILIES: its domain, the domain's
# `www.` host and, with `countries`, every host of two lowercase ASCII letters under it
# (`fr.indeed.com`), on https and its default port; each has a `.test` stand in on http and port
# 8080 for the tests (`.test` is reserved, RFC 2606), as platform_policy.test_start maps a
# platform there. The lease opens the family (`finder.lease_hosts`); since B20 a page may be on
# any public host.
COUNTRY_LABEL = re.compile(r"[a-z]{2}")
PLATFORM_FAMILIES = (
    ("indeed.com", True),
    ("linkedin.com", True),
    ("welcometothejungle.com", False),
)
FAMILIES = tuple(
    family
    for domain, countries in PLATFORM_FAMILIES
    for family in (
        (domain, countries, "https", 443),
        (domain.rsplit(".", 1)[0] + ".test", countries, "http", 8080),
    )
)


def family_of(url: str) -> tuple[str, bool, str, int] | None:
    """The family a canonical address is on: its scheme and port, its host the domain, the
    domain's `www.` host or, with countries, two lowercase letters under the domain."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.username or parts.password:
        return None
    host = parts.hostname or ""
    port = {"http": 80, "https": 443}.get(parts.scheme) if port is None else port
    for family in FAMILIES:
        domain, countries, scheme, family_port = family
        if (parts.scheme, port) != (scheme, family_port):
            continue
        label, dot, rest = host.partition(".")
        if host in (domain, "www." + domain) or (
            countries and dot and rest == domain and COUNTRY_LABEL.fullmatch(label)
        ):
            return family
    return None


@dataclass(frozen=True)
class Board:
    """A job board whose postings carry an id of their own: the query parameters that hold it on
    any path, a path that holds it (group 1), its shape, a value naming it (group 1), the names a
    script's item gives it (case, `_` and `-` aside), the board's own address of a posting, the
    paths of the board's page of one posting (whatever its query holds: never a search, a list
    or a company page holding the id), and the parameters that only track on the board's hosts."""

    params: frozenset[str]
    path: re.Pattern[str] | None
    shape: re.Pattern[str]
    urn: re.Pattern[str] | None
    fields: frozenset[str]
    address: str
    page: re.Pattern[str]
    tracking: frozenset[str]


_LINKEDIN_VIEW = re.compile(r"/jobs/view/(?:[^/]*-)?([0-9]+)/?")
BOARDS = {
    "indeed": Board(
        params=frozenset({"jk"}),
        path=None,
        shape=re.compile(r"[0-9a-f]{16}"),
        urn=None,
        fields=frozenset({"jk", "jobkey"}),
        address="/viewjob?jk={id}",
        page=re.compile(r"(?:/m(?:/basecamp)?)?/viewjob/?|/(?:rc|pagead)/clk/?"),
        tracking=frozenset({"tk", "from", "vjs", "bb", "xkcb", "advn", "adid", "sjdu"}),
    ),
    "linkedin": Board(
        params=frozenset({"currentJobId"}),
        path=_LINKEDIN_VIEW,
        shape=re.compile(r"[0-9]{1,19}"),
        urn=re.compile(r"urn:li:(?:fs_|fsd_)?jobPosting:([0-9]{1,19})"),
        fields=frozenset({"jobid", "jobpostingid", "currentjobid", "entityurn"}),
        address="/jobs/view/{id}/",
        page=_LINKEDIN_VIEW,
        tracking=frozenset({"trk", "trkInfo", "refId", "trackingId", "eBP", "lipi"}),
    ),
}
TRACKING = frozenset(
    {"gclid", "gbraid", "wbraid", "dclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "_hsenc",
     "_hsmi", "igshid"}
)  # fmt: skip
TRACKING_PREFIX = "utm_"


def _query(query: str) -> list[tuple[str, str]]:
    """A query's parameters as a server reads them: split on `&`, each name and value percent
    decoded (a `+` kept as written), empty pieces aside."""
    pairs: list[tuple[str, str]] = []
    for piece in query.split("&"):
        if piece:
            name, _, value = piece.partition("=")
            pairs.append((unquote(name), unquote(value)))
    return pairs


def board_of(url: str) -> tuple[str, Board] | None:
    """The board (BOARDS) a canonical address is on, with its family's domain (`family_of`)."""
    family = family_of(url)
    board = BOARDS.get(family[0].rsplit(".", 1)[0]) if family else None
    return (family[0], board) if family and board else None


def posting_id(url: str) -> tuple[str, str] | None:
    """The board's domain and the posting id a canonical address gives: its id parameters and its
    id path, when they hold one id of the board's shape. None for an address of no board, with no
    id, with two different ids, or with an id of another shape."""
    found = board_of(url)
    if found is None:
        return None
    domain, board = found
    parts = urlsplit(url)
    ids = {value for name, value in _query(parts.query) if name in board.params}
    if board.path is not None and (match := board.path.fullmatch(parts.path)):
        ids.add(match.group(1))
    if len(ids) != 1:
        return None
    one = ids.pop()
    return (domain, one) if board.shape.fullmatch(one) else None


def posting_key(url: str) -> str:
    """What names the posting a canonical address shows: its board and id (`posting_id`), else
    the address once the parameters that only track are left out, one trailing slash and the
    order of the parameters aside. "" for an address it cannot read. Two addresses of one key
    name the same posting."""
    found = posting_id(url)
    if found is not None:
        return f"{found[0]} {found[1]}"  # a space is in no canonical address
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    board = board_of(url)
    tracking = TRACKING | board[1].tracking if board else TRACKING
    kept = sorted(
        (
            (name, value)
            for name, value in _query(parts.query)
            if name not in tracking and not name.startswith(TRACKING_PREFIX)
        ),
        key=lambda pair: pair[0],
    )
    path = parts.path[:-1] if parts.path.endswith("/") else parts.path
    query = "&".join(f"{quote(name, safe='')}={quote(value, safe='')}" for name, value in kept)
    return f"{parts.scheme}://{parts.netloc}{path}?{query}"


def posting_of_key(key: str) -> tuple[str, str] | None:
    """The board's domain and the posting id a key of `posting_key` names; None for the key of an
    address (it holds a `/` before any space, or no space)."""
    domain, space, posting = key.partition(" ")
    return (domain, posting) if space and posting and "/" not in domain else None


def board_address(url: str, posting: str) -> str:
    """The board's own address of a posting id, on the scheme and host of `url`, a board's
    canonical address (so the address is canonical too); "" for an address of no board or an
    id of another shape."""
    found = board_of(url)
    if found is None or not found[1].shape.fullmatch(posting):
        return ""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{found[1].address.format(id=posting)}"


def posting_address(url: str) -> str:
    """The board's own address of the posting a board's address names (`posting_id`), on its
    host; the address itself for any other."""
    found = posting_id(url)
    return board_address(url, found[1]) if found else url


def posting_page(url: str) -> bool:
    """Whether a board's canonical address that gives a posting id is the board's page of that
    posting (`Board.page`, its path whatever its query holds), not a search, a list or a company
    page that holds the id (LinkedIn's `/jobs/search/?currentJobId=<id>`, Indeed's
    `/cmp/<company>/jobs?jk=<id>`). False for an address of no board."""
    found = board_of(url)
    return found is not None and found[1].page.fullmatch(urlsplit(url).path) is not None
