"""Line and link helpers shared by the alert parsers (mail/alerts).

Everything here reads a stranger's text, so it is pure, linear in the size of its input and
bounded: a line longer than `MAX_LINE` is not read as a field, a field is cut at its own cap.
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
