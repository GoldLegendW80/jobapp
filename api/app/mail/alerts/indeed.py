"""Indeed's job alert e-mails (mail/alerts). Pure: the standard library and alerts/text.py.

**The text part** (`parse_text`), as public parsers read it (SpangleLabs/indeed_emails, and the
French layout quoted by 0xCAF3D0OD/job-bot; confirmed on a real alert by the owner before the
provider is trusted, SPEC-mail.md): a few header lines, then one paragraph per job, a blank line
between two:

    Ingénieur DevOps
    Exemple Conseil - Lyon (69)
    Participer à la gestion des environnements Cloud…
    il y a 1 jour
    https://fr.indeed.com/rc/clk/dl?jk=0123456789abcdef&from=ja&…

The first line is the title, the second "Company - Location" (the LAST " - " splits them), the
lines between it and the link are the snippet, and the paragraph ends with the job's link. A
paragraph is a job only when it ends with a job link of Indeed's (`job_id`) and has a title and
a company line above it; anything else (the header, the footer, a sponsored entry whose link
carries no `jk`) is not a job. Header lines glued to the first job ("Votre alerte pour …") are
skipped above its title (`_HEADER`, in French and in English: "Jobs for <search> in
<place>"); a title or company line that holds a link or an e-mail
address drops the job, and such a line is left out of the snippet (text.py, fail closed).

**The HTML part** (`parse_html_text`) is never parsed here: the parser service turns it into its
text version (one line per block, a tab between cells, a link written "label (address)"), and
this reads that: a line "Title (job link)" starts a job, the next line is the company and the
location (two cells, or "Company - Location"), the lines up to the next job are its snippet. That
layout is the parser service's, not established on a real Indeed alert: it is the fallback for
a mail without a text part.

**The link** is a tracked redirect (`/rc/clk`, `/pagead/clk`) or a `/viewjob` address on
`indeed.com` or a country site (`fr.indeed.com`); the job's key is its one `jk` parameter, 16
hexadecimal characters. The canonical address is `https://<country site>/viewjob?jk=<key>`: the
tracking is dropped and the link is never fetched.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.mail.alerts import text as t

# A country site of Indeed: two lower case letters under indeed.com.
_COUNTRY = re.compile(r"[a-z]{2}\.indeed\.com")
_JOB_PATHS = frozenset({"/rc/clk", "/rc/clk/dl", "/pagead/clk", "/pagead/clk/dl", "/viewjob"})
_JOB_KEY = re.compile(r"[0-9a-fA-F]{16}")
# A line of the parser service's text version that is a link: "label (address)".
_LABELLED = re.compile(r"(?P<label>.*\S) \((?P<url>https?://[^\s()<>\"]+)\)")
# The header lines of an alert ("3 nouveaux emplois correspondent à votre alerte …", "Votre
# alerte pour …", "Jobs for data engineer in Lyon"), when a copy of the mail lost the blank line
# under them: never a job's title. French and English wordings.
_HEADER = re.compile(
    r"(?:\d+\s+)?(?:nouvel(?:le)?s?\s+(?:emplois?|offres?)|nouveaux\s+emplois|votre\s+alerte"
    r"|emplois?\s+correspond|your\s+(?:job\s+)?alert|new\s+jobs?\b"
    r"|jobs?\s+for\s+\S.*?\s+in\s+(?=\S))\b.*",
    re.IGNORECASE,
)


def _site(url: str) -> str | None:
    """The Indeed site of an address: `www.indeed.com` for `indeed.com` itself, the country
    site as it is, None for any other host (a tracker such as `cts.indeed.com` included)."""
    host = t.host_of(url)
    if host in ("indeed.com", "www.indeed.com"):
        return "www.indeed.com"
    if host is not None and _COUNTRY.fullmatch(host):
        return host
    return None


def is_job_link(url: str) -> bool:
    """A link at a job path of an Indeed site, with or without a key."""
    if _site(url) is None:
        return False
    try:
        path = urlsplit(url).path.rstrip("/")
    except ValueError:
        return False
    return path in _JOB_PATHS


def job_id(url: str) -> str | None:
    """The job's key of a job link, lower case; None without exactly one well formed `jk`."""
    if not is_job_link(url):
        return None
    keys = t.query_values(url, "jk")
    if len(keys) != 1 or not _JOB_KEY.fullmatch(keys[0]):
        return None
    return keys[0].lower()


def canonical(url: str) -> str | None:
    """`https://<site>/viewjob?jk=<key>`, or None when the link names no job."""
    key = job_id(url)
    site = _site(url)
    return f"https://{site}/viewjob?jk={key}" if key and site else None


def _job(title: str, company_line: str, snippet: list[str], url: str) -> t.AlertJob | None:
    """The job of its lines, None when a field is missing or a field line holds an address
    (text.py: fail closed)."""
    if not t.fields_clear(title, company_line):
        return None
    key, address = job_id(url), canonical(url)
    if "\t" in company_line:
        company, location = (cell.strip() for cell in company_line.split("\t", 2)[:2])
    else:
        company, location = t.split_company(company_line)
    title = t.clean(title, t.MAX_TITLE)
    company = t.clean(company, t.MAX_COMPANY)
    if not key or not address or not title or not company:
        return None
    return t.AlertJob(
        title=title,
        company=company,
        location=t.clean(location, t.MAX_LOCATION),
        snippet=t.snippet_of(snippet),
        url=address,
        job_id=key,
    )


def _done(jobs: list[t.AlertJob], dropped: int) -> t.Parsed:
    kept = {job.url: job for job in reversed(jobs)}  # one entry per job, the first one kept
    unique = [job for job in jobs if kept.get(job.url) is job]
    return t.Parsed(tuple(unique[: t.MAX_JOBS]), dropped + max(0, len(unique) - t.MAX_JOBS))


def parse_text(body: str) -> t.Parsed:
    """The jobs of the text part of an Indeed alert (module docstring)."""
    jobs: list[t.AlertJob] = []
    dropped = 0
    for paragraph in t.paragraphs(body):
        url = t.ending_link(paragraph[-1])
        if url is None or not is_job_link(url):
            continue
        above = paragraph[:-1]
        while above and _HEADER.fullmatch(above[0]):
            above = above[1:]
        job = _job(above[0], above[1], above[2:], url) if len(above) >= 2 else None
        if job is None:
            dropped += 1
        else:
            jobs.append(job)
    return _done(jobs, dropped)


def parse_html_text(version: str) -> t.Parsed:
    """The jobs of the parser service's text version of an Indeed alert's HTML part."""
    jobs: list[t.AlertJob] = []
    dropped = 0
    current: tuple[str, str, list[str]] | None = None  # title, link, the lines below it

    def close() -> None:
        nonlocal dropped
        if current is None:
            return
        title, url, below = current
        job = _job(title, below[0], below[1:], url) if below else None
        if job is None:
            dropped += 1
        else:
            jobs.append(job)

    for line in t.lines(version):
        if not line:
            continue
        labelled = _LABELLED.fullmatch(line)
        if labelled is not None and is_job_link(labelled["url"]):
            close()
            current = (labelled["label"], labelled["url"], [])
        elif current is not None:
            if t.URL.search(line) is not None:
                # A line with an address ends the job: a button, the footer, an unsubscribe.
                close()
                current = None
            elif len(current[2]) < 6:
                current[2].append(line)
    close()
    return _done(jobs, dropped)
