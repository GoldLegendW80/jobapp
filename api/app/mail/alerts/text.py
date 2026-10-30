"""Line and link helpers shared by the alert parsers (mail/alerts).

Everything here reads a stranger's text, so it is pure, linear in the size of its input and
bounded: a line longer than `MAX_LINE` is not read as a field, a field is cut at its own cap.

**Fail closed on addresses.** An alert names its recipient ("Sent to <address>") and carries
tracking links (an unsubscribe token, `midToken`, `email=`), and a parser that reads a layout
it was not written for may take such a line for a field. So a line that holds a link or an
e-mail address (`holds_address`) is never a field: it is left out of the snippet
(`snippet_of`), and a job whose title, company or location line holds one is dropped and
counted (`fields_clear`), never stored.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

MAX_LINE = 2000  # characters of one line a parser reads; a longer line is no field
MAX_JOBS = 100  # postings one mail may give; the rest is dropped and counted
MAX_TITLE = 300
MAX_COMPANY = 200
MAX_LOCATION = 200
MAX_SNIPPET = 2000

# Direction overrides and isolates: they make a line read as something else.
_DIRECTION = re.compile("[؜‎‏‪-‮⁦-⁩]")
# An address as it stands in a text part: up to the first space or angle bracket.
URL = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
_ENDING = re.compile(r"https?://[^\s<>\"]+$", re.IGNORECASE)
# A link without its scheme still names a host: "www.", or any "scheme://".
_BARE_LINK = re.compile(r"://|\bwww\.", re.IGNORECASE)
# An "@" written as a word ("[at]", "(at)"), with the spaces around it.
_AT_WORD = re.compile(r"\s*[\[(]\s*at\s*[\])]\s*", re.IGNORECASE)
# An "@" between spaces.
_SPACED_AT = re.compile(r"[ \t]*@[ \t]*")


@dataclass(frozen=True)
class AlertJob:
    """One posting an alert lists, as its parser read it. `url` is the canonical address built
    from the job's id, never the tracked link of the mail; `snippet` is what the mail says of
    the job, never its full description."""

    title: str
    company: str
    location: str
    snippet: str
    url: str
    job_id: str


@dataclass(frozen=True)
class Parsed:
    """What one mail gave: its jobs, and the blocks that looked like a job and were dropped
    (`dropped`: a link without a job id, a block without a title, more than MAX_JOBS)."""

    jobs: tuple[AlertJob, ...]
    dropped: int


def clean(text: str, limit: int) -> str:
    """`text` as one line safe to store and to show: no control character, no direction
    override, NFC, spaces collapsed, at most `limit` characters."""
    kept = [
        " " if unicodedata.category(char) in ("Cc", "Zl", "Zp") else char
        for char in _DIRECTION.sub("", text)
        if unicodedata.category(char) not in ("Cs", "Co", "Cn", "Cf") or char == "‍"
    ]
    return " ".join(unicodedata.normalize("NFC", "".join(kept)).split())[:limit].rstrip()


def _has_mail_address(line: str) -> bool:
    """Whether `line` holds `\\S+@\\S+\\.\\S+`, in linear time: a word with an "@" after its
    first character and a "." at least two characters further, before its last character."""
    for word in line.split():
        at = word.find("@", 1)
        if at != -1 and word.rfind(".", 0, len(word) - 1) >= at + 2:
            return True
    return False


def _undisguised(line: str) -> str:
    """`line` with the usual disguises of an "@" written as one: "%40", "[at]", "(at)", and
    an "@" between spaces ("name @ example.org")."""
    return _SPACED_AT.sub("@", _AT_WORD.sub("@", line.replace("%40", "@")))


def holds_address(line: str) -> bool:
    """Whether a line holds a link or an e-mail address, as it stands, once `clean` dropped
    the characters that could hide one (a direction override, a zero width space), in its NFKC
    form (a fullwidth or small "@", a fullwidth "www."), and with a disguised "@" undone."""
    cleaned = clean(line, MAX_LINE)
    folded = unicodedata.normalize("NFKC", cleaned)
    for form in (line, cleaned, folded, _undisguised(folded)):
        if URL.search(form) or _BARE_LINK.search(form) or _has_mail_address(form):
            return True
    return False


def fields_clear(*fields: str) -> bool:
    """Whether none of a job's field lines (title, company, location) holds an address."""
    return not any(holds_address(field) for field in fields)


def snippet_of(found: list[str]) -> str:
    """The snippet of a job from its lines: every line holding an address left out."""
    return clean(" ".join(line for line in found if not holds_address(line)), MAX_SNIPPET)


def lines(text: str) -> list[str]:
    """The lines of a text part, each stripped; a line over MAX_LINE reads as empty. Unix, Mac
    and Windows line ends are all line ends."""
    return [
        line.strip() if len(line) <= MAX_LINE else ""
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]


def paragraphs(text: str) -> list[list[str]]:
    """The runs of non empty lines, a blank line between two runs."""
    found: list[list[str]] = []
    current: list[str] = []
    for line in lines(text):
        if line:
            current.append(line)
        elif current:
            found.append(current)
            current = []
    if current:
        found.append(current)
    return found


def ending_link(line: str) -> str | None:
    """The address a line ends with ("View job: <address>", or the address alone), if any."""
    found = _ENDING.search(line)
    return found[0] if found is not None else None


def host_of(url: str) -> str | None:
    """The lower case host of an http or https address, None for anything else (a user part,
    a port, a backslash or a space make it anything else)."""
    if "\\" in url or any(char.isspace() for char in url):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or port is not None:
        return None
    if parts.username is not None or parts.password is not None or not parts.hostname:
        return None
    return parts.hostname.lower()


def query_values(url: str, name: str) -> list[str]:
    """Every value of the query parameter `name` (exact, case sensitive) of `url`."""
    try:
        query = urlsplit(url).query
    except ValueError:
        return []
    return [value for key, value in parse_qsl(query, keep_blank_values=True) if key == name]


def split_company(line: str) -> tuple[str, str]:
    """(company, location) of a "Company - Location" line: the LAST " - " separates them, as a
    company name may hold one ("Example - Studio - Paris" is "Example - Studio" in "Paris").
    Without it, the line is the company and the location is unknown."""
    company, separator, location = line.rpartition(" - ")
    if not separator or not company.strip() or not location.strip():
        return line, ""
    return company, location
