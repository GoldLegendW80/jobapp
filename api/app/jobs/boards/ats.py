"""The ATSs whose public job board APIs the company boards read: where a board's postings are,
how a pasted board address names its ATS and id, and how one answer becomes postings.

Endpoints (official, public, no key, no sign in; checked 2026-10-30, one request per board):
- ashby: GET https://api.ashbyhq.com/posting-api/job-board/{org} -> {"jobs": [...]}; pay is
  not read, so `includeCompensation` is not asked (smaller answers); a posting with
  `isListed: false` is left out. Board pages: jobs.ashbyhq.com/{org}.
- greenhouse: GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
  -> {"jobs": [...]}, `content` HTML escaped twice. Board pages: boards.greenhouse.io/{token},
  job-boards.greenhouse.io/{token}, boards.greenhouse.io/embed/job_board?for={token}.
- lever: GET https://api.lever.co/v0/postings/{company}?mode=json -> [...] ({"ok": false} for an
  unknown company). Board pages: jobs.lever.co/{company}.
- smartrecruiters: GET https://api.smartrecruiters.com/v1/companies/{id}/postings?limit=100&offset=N
  -> {"totalFound", "content": [...]}, newest first, no description in the list (one more
  request per posting would be needed: never made, so these postings are stored without text and
  get no fit). Board pages: jobs.smartrecruiters.com/{id}, careers.smartrecruiters.com/{id}.
- workable: GET https://apply.workable.com/api/v1/widget/accounts/{account}?details=true
  -> {"jobs": [...]}. Board pages: apply.workable.com/{account}, {account}.workable.com.
- recruitee: GET https://{subdomain}.recruitee.com/api/offers/ -> {"offers": [...]}. Board pages:
  {subdomain}.recruitee.com.
Not covered: Teamtailor (its API needs the employer's key; its per site RSS feed is not a
documented API), the EU hosts of Greenhouse and Lever (a pasted EU address is refused with a
clear message).

Parsing never raises for one odd posting: a posting that is not an object or lacks a title or an
address is counted (`Parsed.invalid`) and skipped. A body of the wrong shape raises
BoardError("invalid_response"). Text is made storable (NUL characters and lone surrogates
removed). A posting's location is every place the ATS names, then "Remote" when it says the
posting is remote, joined with ", ". Its contract is what the ATS states, mapped to the app's
contract types when the words say it plainly (`contract_of`), else None; the contract a posting
is filtered and stored with is filters.contract_of's (the title's words first).
"""

import html
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from app.jobs.models import ContractType
from app.profile.schemas import BOARD_ATS, BOARD_PATTERN, RECRUITEE_PATTERN

ATS_NAMES = {
    "ashby": "Ashby",
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "smartrecruiters": "SmartRecruiters",
    "workable": "Workable",
    "recruitee": "Recruitee",
}
# The host each ATS's API answers on; Recruitee's is the board's own subdomain.
API_HOSTS = {
    "ashby": "api.ashbyhq.com",
    "greenhouse": "boards-api.greenhouse.io",
    "lever": "api.lever.co",
    "smartrecruiters": "api.smartrecruiters.com",
    "workable": "apply.workable.com",
}
PAGE_SIZE = 100  # SmartRecruiters' largest page
PAGES_MAX = 2  # at most two requests for one board (SmartRecruiters' paging)

_BOARD = re.compile(BOARD_PATTERN)
_CONTROL = re.compile(r"[\x00\ud800-\udfff]")
_TAG = re.compile(r"<[^>]+>")


class BoardError(Exception):
    """A board could not be read; `code` is a short lower case word (client.CODES)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class BoardPosting:
    title: str
    location: str
    url: str
    published: str  # YYYY-MM-DD, "" when the ATS states no date
    text: str
    contract: ContractType | None = None


@dataclass
class Parsed:
    postings: list[BoardPosting] = field(default_factory=list)
    invalid: int = 0
    total: int | None = None  # what the ATS says it lists, when it pages (SmartRecruiters)


def valid_board(ats: str, board: str) -> bool:
    """The shape the profile accepts (schemas.CompanyBoard), checked again before a request."""
    if ats not in BOARD_ATS or not _BOARD.fullmatch(board) or ".." in board:
        return False
    return ats != "recruitee" or RECRUITEE_PATTERN.fullmatch(board) is not None


def api_host(ats: str, board: str) -> str:
    return f"{board}.recruitee.com" if ats == "recruitee" else API_HOSTS[ats]


def pace_key(ats: str) -> str:
    """What one pause and one 429 stop are shared by: the ATS (Recruitee's subdomains are one
    service)."""
    return ats


def api_url(ats: str, board: str, page: int = 0) -> str:
    """The address of a board's posting API; `board` is checked, then one path segment."""
    if not valid_board(ats, board):
        raise ValueError("invalid board id")
    segment = quote(board, safe="")
    host = api_host(ats, board)
    paths = {
        "ashby": f"/posting-api/job-board/{segment}",
        "greenhouse": f"/v1/boards/{segment}/jobs?content=true",
        "lever": f"/v0/postings/{segment}?mode=json",
        "smartrecruiters": f"/v1/companies/{segment}/postings?limit={PAGE_SIZE}"
        f"&offset={page * PAGE_SIZE}",
        "workable": f"/api/v1/widget/accounts/{segment}?details=true",
        "recruitee": "/api/offers/",
    }
    return f"https://{host}{paths[ats]}"


def board_page(ats: str, board: str) -> str:
    """The public board a person opens, for links in the setup."""
    pages = {
        "ashby": f"https://jobs.ashbyhq.com/{board}",
        "greenhouse": f"https://job-boards.greenhouse.io/{board}",
        "lever": f"https://jobs.lever.co/{board}",
        "smartrecruiters": f"https://careers.smartrecruiters.com/{board}",
        "workable": f"https://apply.workable.com/{board}/",
        "recruitee": f"https://{board}.recruitee.com/",
    }
    return pages[ats]


# --- a pasted board address ---------------------------------------------------------------------

# Hosts whose first path segment is the board id.
_PATH_HOSTS = {
    "jobs.ashbyhq.com": "ashby",
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.smartrecruiters.com": "smartrecruiters",
    "careers.smartrecruiters.com": "smartrecruiters",
    "apply.workable.com": "workable",
}
_API_PATHS = {
    "api.ashbyhq.com": ("ashby", re.compile(r"^/posting-api/job-board/([^/]+)")),
    "boards-api.greenhouse.io": ("greenhouse", re.compile(r"^/v1/boards/([^/]+)")),
    "api.lever.co": ("lever", re.compile(r"^/v0/postings/([^/]+)")),
    "api.smartrecruiters.com": ("smartrecruiters", re.compile(r"^/v1/companies/([^/]+)")),
}
# EU hosts with their own API, not read yet.
EU_HOSTS = frozenset({"job-boards.eu.greenhouse.io", "jobs.eu.lever.co", "api.eu.lever.co"})
_NOT_A_BOARD = frozenset({"embed", "j", "api", "v1", "jobs", "careers", "www"})


@dataclass(frozen=True)
class Detected:
    ats: str
    board: str

    @property
    def company(self) -> str:
        """A readable company name from the id: "mistral.ai" -> "Mistral", "scaleai" ->
        "Scaleai" (the person may rename it)."""
        stem = re.split(r"[._-]", self.board)
        words = [w for w in stem if w and w.lower() not in {"ai", "io", "com", "inc", "hq"}]
        return " ".join(w[:1].upper() + w[1:] for w in words or stem) or self.board


def detect(address: str) -> Detected | None:
    """The ATS and board id a board address names (jobs.ashbyhq.com/<org>, a posting address
    under it, an API address), None when it names none. Only https and http, no credentials."""
    text = address.strip()
    if "://" not in text:
        text = f"https://{text}"
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or parts.username or parts.password or port:
        return None
    segments = [s for s in parts.path.split("/") if s]
    found: tuple[str, str] | None = None
    if host in _PATH_HOSTS:
        ats = _PATH_HOSTS[host]
        if ats == "greenhouse" and segments[:2] == ["embed", "job_board"]:
            token = parse_qs(parts.query).get("for", [""])[0]
            found = (ats, token) if token else None
        elif segments and segments[0].lower() not in _NOT_A_BOARD:
            found = (ats, segments[0])
    elif host in _API_PATHS:
        ats, pattern = _API_PATHS[host]
        match = pattern.match(parts.path)
        found = (ats, match.group(1)) if match else None
    elif host.endswith(".workable.com") and host.count(".") == 2:
        found = ("workable", host.split(".")[0])
    elif host.endswith(".recruitee.com") and host.count(".") == 2:
        found = ("recruitee", host.split(".")[0])
    if found is None:
        return None
    ats, board = found
    if ats in ("ashby", "greenhouse", "lever", "recruitee", "workable"):
        board = board.lower()
    if not valid_board(ats, board) or board in _NOT_A_BOARD:
        return None
    return Detected(ats, board)


# --- one answer -----------------------------------------------------------------------------------


def storable(text: str) -> str:
    return _CONTROL.sub("", text)


def plain(text: str) -> str:
    """Tags become spaces, entities are decoded, whitespace collapses."""
    return " ".join(html.unescape(_TAG.sub(" ", text)).split())


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _required(job: Any, name: str) -> str | None:
    value = job.get(name) if isinstance(job, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _where(*places: Any, remote: bool = False) -> str:
    names = [p.strip() for p in places if isinstance(p, str) and p.strip()]
    if remote and not any("remote" in n.lower() for n in names):
        names.append("Remote")
    return storable(", ".join(dict.fromkeys(names)))


def _day(value: Any) -> str:
    """The date part of an ISO date or time; "" when it is not one."""
    text = _text(value)[:10]
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else ""


# The contract words of the ATSs (Ashby's employmentType, Lever's commitment, Workable's
# employment_type, SmartRecruiters' typeOfEmployment, Recruitee's employment_type_code), in
# order: the first that matches wins. "Contract" alone stays unknown (fixed term or freelance),
# and so does "Full time" or "Part time": a schedule, not a contract (an internship is often
# listed FullTime). filters.contract_of decides the posting's contract with the title's words.
_CONTRACTS = (
    (ContractType.Alternance, re.compile(r"apprentice|alternance|work.?study")),
    (ContractType.Stage, re.compile(r"\bintern(?:ship)?s?\b|trainee|\bstage\b")),
    (ContractType.VIE, re.compile(r"\bvie\b")),
    (ContractType.Freelance, re.compile(r"freelance|contractor")),
    (ContractType.CDD, re.compile(r"temporary|fixed.?term|\bcdd\b")),
    (ContractType.CDI, re.compile(r"permanent|\bcdi\b")),
)


def contract_of(value: Any) -> ContractType | None:
    words = _text(value).lower()
    if not words:
        return None
    return next((contract for contract, pattern in _CONTRACTS if pattern.search(words)), None)


def _jobs(payload: Any, key: str = "jobs") -> list[Any]:
    jobs = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        raise BoardError("invalid_response")
    return jobs


def _ashby(payload: Any, board: str) -> Parsed:
    del board
    parsed = Parsed()
    for job in _jobs(payload):
        if isinstance(job, dict) and job.get("isListed") is False:
            continue
        title, url = _required(job, "title"), _required(job, "jobUrl")
        if title is None or url is None:
            parsed.invalid += 1
            continue
        secondary = job.get("secondaryLocations")
        others = [s.get("location") for s in secondary if isinstance(s, dict)] if isinstance(
            secondary, list
        ) else []  # fmt: skip
        remote = job.get("isRemote") is True or _text(job.get("workplaceType")) == "Remote"
        parsed.postings.append(
            BoardPosting(
                title=storable(title),
                location=_where(job.get("location"), *others, remote=remote),
                url=storable(url),
                published=_day(job.get("publishedAt")),
                text=storable(_text(job.get("descriptionPlain"))),
                contract=contract_of(job.get("employmentType")),
            )
        )
    return parsed


def _greenhouse(payload: Any, board: str) -> Parsed:
    del board
    parsed = Parsed()
    for job in _jobs(payload):
        title, url = _required(job, "title"), _required(job, "absolute_url")
        if title is None or url is None:
            parsed.invalid += 1
            continue
        where = job.get("location")
        location = _text(where.get("name")) if isinstance(where, dict) else ""
        parsed.postings.append(
            BoardPosting(
                title=storable(title),
                location=_where(location),
                url=storable(url),
                published=_day(job.get("first_published")) or _day(job.get("updated_at")),
                text=storable(plain(html.unescape(_text(job.get("content"))))),
            )
        )
    return parsed


def _lever(payload: Any, board: str) -> Parsed:
    del board
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise BoardError("not_found")
    if not isinstance(payload, list):
        raise BoardError("invalid_response")
    parsed = Parsed()
    for job in payload:
        title, url = _required(job, "text"), _required(job, "hostedUrl")
        if title is None or url is None:
            parsed.invalid += 1
            continue
        categories = job.get("categories") if isinstance(job.get("categories"), dict) else {}
        places = categories.get("allLocations")
        places = places if isinstance(places, list) else []
        created = job.get("createdAt")
        published = ""
        if isinstance(created, int | float) and not isinstance(created, bool):
            try:
                published = datetime.fromtimestamp(created / 1000, tz=UTC).date().isoformat()
            except (OverflowError, OSError, ValueError):
                published = ""
        parsed.postings.append(
            BoardPosting(
                title=storable(title),
                location=_where(
                    categories.get("location"),
                    *places,
                    remote=_text(job.get("workplaceType")).lower() == "remote",
                ),
                url=storable(url),
                published=published,
                text=storable(_text(job.get("descriptionPlain"))),
                contract=contract_of(categories.get("commitment")),
            )
        )
    return parsed


def _smartrecruiters(payload: Any, board: str) -> Parsed:
    parsed = Parsed()
    total = payload.get("totalFound") if isinstance(payload, dict) else None
    parsed.total = total if isinstance(total, int) and not isinstance(total, bool) else None
    for job in _jobs(payload, "content"):
        title = _required(job, "name")
        ident = job.get("id") if isinstance(job, dict) else None
        if title is None or not isinstance(ident, str | int) or isinstance(ident, bool):
            parsed.invalid += 1
            continue
        where = job.get("location") if isinstance(job.get("location"), dict) else {}
        kind = job.get("typeOfEmployment") if isinstance(job.get("typeOfEmployment"), dict) else {}
        country = _text(where.get("country")).upper()
        parsed.postings.append(
            BoardPosting(
                title=storable(title),
                location=_where(where.get("city"), country, remote=where.get("remote") is True),
                url=f"https://jobs.smartrecruiters.com/{quote(board, safe='')}/"
                f"{quote(str(ident), safe='')}",
                published=_day(job.get("releasedDate")),
                text="",
                contract=contract_of(kind.get("id")) or contract_of(kind.get("label")),
            )
        )
    return parsed


def _workable(payload: Any, board: str) -> Parsed:
    del board
    parsed = Parsed()
    for job in _jobs(payload):
        title = _required(job, "title")
        url = _required(job, "url") or _required(job, "shortlink")
        if title is None or url is None:
            parsed.invalid += 1
            continue
        places: list[Any] = []
        listed = job.get("locations")
        for place in listed if isinstance(listed, list) else []:
            if isinstance(place, dict) and place.get("hidden") is not True:
                places += [place.get("city"), place.get("country")]
        if not places:
            places = [job.get("city"), job.get("country")]
        parsed.postings.append(
            BoardPosting(
                title=storable(title),
                location=_where(*places, remote=job.get("telecommuting") is True),
                url=storable(url),
                published=_day(job.get("published_on")) or _day(job.get("created_at")),
                text=storable(plain(_text(job.get("description")))),
                contract=contract_of(job.get("employment_type")),
            )
        )
    return parsed


def _recruitee(payload: Any, board: str) -> Parsed:
    del board
    parsed = Parsed()
    for job in _jobs(payload, "offers"):
        title = _required(job, "title")
        url = _required(job, "careers_url")
        if title is None or url is None:
            parsed.invalid += 1
            continue
        text = f"{_text(job.get('description'))} {_text(job.get('requirements'))}"
        parsed.postings.append(
            BoardPosting(
                title=storable(title),
                location=_where(
                    job.get("location") or job.get("city"),
                    job.get("country"),
                    remote=job.get("remote") is True,
                ),
                url=storable(url),
                published=_day(job.get("published_at")) or _day(job.get("created_at")),
                text=storable(plain(text)),
                contract=contract_of(job.get("employment_type_code")),
            )
        )
    return parsed


PARSERS: dict[str, Callable[[Any, str], Parsed]] = {
    "ashby": _ashby,
    "greenhouse": _greenhouse,
    "lever": _lever,
    "smartrecruiters": _smartrecruiters,
    "workable": _workable,
    "recruitee": _recruitee,
}


TITLE_MAX = 300  # finder.TITLE_MAX
LOCATION_MAX = 300  # finder.LOCATION_MAX
URL_MAX = 2000
TEXT_MAX = 200_000  # sweep.POSTING_TEXT_MAX


def _address(url: str) -> bool:
    """An absolute http or https address with a host and no credentials, as a link is opened."""
    if len(url) > URL_MAX or any(c.isspace() or ord(c) < 32 for c in url):
        return False
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018, raises on a port that is not a number
    except ValueError:
        return False
    return (
        parts.scheme in ("http", "https")
        and bool(parts.hostname)
        and not parts.username
        and not parts.password
    )


def parse(ats: str, board: str, body: bytes | bytearray) -> Parsed:
    """The postings of one raw answer. Raises BoardError for a body that is not the JSON the
    ATS documents. A posting whose address is not an http or https link is invalid; a long
    title, location or text is cut to what a job row takes."""
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        raise BoardError("invalid_response") from None
    parsed = PARSERS[ats](payload, board)
    kept = [p for p in parsed.postings if _address(p.url)]
    parsed.invalid += len(parsed.postings) - len(kept)
    parsed.postings = [
        BoardPosting(
            title=p.title[:TITLE_MAX],
            location=p.location[:LOCATION_MAX],
            url=p.url,
            published=p.published,
            text=p.text[:TEXT_MAX],
            contract=p.contract,
        )
        for p in kept
    ]
    return parsed
