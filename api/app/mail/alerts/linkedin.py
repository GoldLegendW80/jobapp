"""LinkedIn's job alert e-mails (mail/alerts). Pure: the standard library and alerts/text.py.

**The text part** (`parse_text`), as public parsers and recorded alerts read it
(rwuniard/Gmail_LinkedIn_job_alerts, LaurenceSmithPersonal/linkedin-job-email-summariser):
a header ("Your job alert for <search>"), then one block per job, a line of dashes between two
blocks, and a footer ("See all jobs on LinkedIn: <link>"):

    Data Engineer
    Exemple Données
    Paris, Île-de-France, France
    This company is actively hiring
    View job: https://www.linkedin.com/comm/jobs/view/4000000001/?trackingId=…
    ---------------------------------------------------------

Within a block, the job is the paragraph that ends with its link line ("View job: <link>", any
label, or the link alone), or the paragraph just above that line when the link stands alone:
its first three lines are the title, the company and the location, the rest (badges such as
"Actively recruiting") are the snippet. A block whose link names no job (`job_id`), or whose
paragraph has fewer than three lines, is not a job: the header and the footer never are. A
title, company or location line that holds a link or an e-mail address drops the job, and such
a line ("Sent to <address>", a tracking link) is left out of the snippet (text.py, fail closed).

**The link** is `https://www.linkedin.com/comm/jobs/view/<id>/?trackingId=…` (or `/jobs/view/`)
on `linkedin.com` or one of its country hosts; the canonical address is
`https://www.linkedin.com/jobs/view/<id>/`, the tracking dropped, never fetched.

The HTML part is not read: LinkedIn alerts carry a text part, and its HTML layout was not
established from a public source (SPEC-mail.md, the providers' table).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.mail.alerts import text as t

_HOST = re.compile(r"(?:www|[a-z]{2})\.linkedin\.com|linkedin\.com")
_PATH = re.compile(r"/(?:comm/)?jobs/view/(?P<id>[0-9]{6,20})/?")
_DASHES = re.compile(r"-{20,}")
# The header lines of an alert, when a copy of the mail lost the blank line under them: never a
# job's title. English and French wordings.
_HEADER = re.compile(
    r"(?:your job alert for|new jobs? match(?:es)? your preferences|votre alerte"
    r"|nouvelles? offres?|nouveaux emplois)\b.*",
    re.IGNORECASE,
)


def job_id(url: str) -> str | None:
    """The job's number of a LinkedIn job link, None for any other address."""
    host = t.host_of(url)
    if host is None or not _HOST.fullmatch(host):
        return None
    try:
        path = urlsplit(url).path
    except ValueError:
        return None
    found = _PATH.fullmatch(path)
    return found["id"] if found is not None else None


def canonical(url: str) -> str | None:
    key = job_id(url)
    return f"https://www.linkedin.com/jobs/view/{key}/" if key else None


def _block_job(block: list[str]) -> tuple[t.AlertJob | None, bool]:
    """(the job of one block, whether the block held a job link at all)."""
    for at, line in enumerate(block):
        url = t.ending_link(line) if line else None
        if url is None or job_id(url) is None:
            continue
        # The paragraph that ends with this line, or the one above when the line stands alone.
        start = at
        while start > 0 and block[start - 1]:
            start -= 1
        paragraph = [line for line in block[start:at] if line]
        if not paragraph:
            end = start - 1
            while end > 0 and not block[end - 1]:
                end -= 1
            first = end
            while first > 0 and block[first - 1]:
                first -= 1
            paragraph = block[first:end]
        while paragraph and _HEADER.fullmatch(paragraph[0]):
            paragraph = paragraph[1:]
        if len(paragraph) < 3:
            return None, True
        if not t.fields_clear(*paragraph[:3]):
            return None, True  # a field line holds a link or an address: fail closed
        title = t.clean(paragraph[0], t.MAX_TITLE)
        company = t.clean(paragraph[1], t.MAX_COMPANY)
        address = canonical(url)
        if not title or not company or address is None:
            return None, True
        return (
            t.AlertJob(
                title=title,
                company=company,
                location=t.clean(paragraph[2], t.MAX_LOCATION),
                snippet=t.snippet_of(paragraph[3:]),
                url=address,
                job_id=job_id(url) or "",
            ),
            True,
        )
    return None, False


def parse_text(body: str) -> t.Parsed:
    """The jobs of the text part of a LinkedIn job alert (module docstring)."""
    jobs: dict[str, t.AlertJob] = {}
    dropped = 0
    block: list[str] = []
    blocks: list[list[str]] = []
    for line in t.lines(body):
        if _DASHES.fullmatch(line):
            blocks.append(block)
            block = []
        else:
            block.append(line)
    blocks.append(block)
    for block in blocks:
        job, linked = _block_job(block)
        if job is not None:
            jobs.setdefault(job.url, job)
        elif linked:
            dropped += 1
    kept = list(jobs.values())
    return t.Parsed(tuple(kept[: t.MAX_JOBS]), dropped + max(0, len(kept) - t.MAX_JOBS))
