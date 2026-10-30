"""Boards to suggest: the company boards the person's own postings point to and their list does
not hold yet (owner, 2026-10-30: discover more companies on Ashby and the other ATSs).

A posting found on a job website, imported, or read from an alert often links its employer's
board: its address (`jobs.ashbyhq.com/<org>/<id>`) or an apply link in its text. Reading those
costs no request: nothing is fetched, nothing is added by itself; Find jobs lists the
suggestions and the person adds the ones they want. A board already in the list, ticked or not,
is not suggested. The company name is the one the postings state most often (never "Employer
not stated"), else one made from the board id.

Why not a search engine: Google's Custom Search JSON API, the one official way to query Google
for `site:jobs.ashbyhq.com "<title>"`, is closed to new customers and ends for existing ones on
2027-01-01 (developers.google.com/custom-search/v1/overview, read 2026-10-30); Bing's Search API
was retired in 2025; Brave's API needs a payment card and bills by the request (CLAUDE.md rule
11: everything stays free); scraping result pages, or a service reselling them (SerpApi and the
like), is never done. No ATS publishes a directory of its boards.
"""

import re
import uuid
from collections import Counter
from collections.abc import Iterable

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.jobs.boards.ats import Detected, detect
from app.jobs.models import Job
from app.profile.schemas import CompanyBoard

UNKNOWN_EMPLOYER = "Employer not stated"  # sweep.UNKNOWN_EMPLOYER
SUGGESTIONS_MAX = 20
JOBS_READ = 2000  # the person's newest postings read
TEXT_READ = 20_000  # characters of a posting's text searched for board links
_LINK = re.compile(
    r"https?://(?:[a-z0-9-]+\.)*(?:ashbyhq\.com|greenhouse\.io|lever\.co|smartrecruiters\.com|"
    r"workable\.com|recruitee\.com)/[^\s\"'<>)\]]*",
    re.IGNORECASE,
)


def boards_in(url: str | None, text: str | None) -> set[Detected]:
    found = {d for d in [detect(url)] if d is not None} if url else set()
    for link in _LINK.findall((text or "")[:TEXT_READ]):
        detected = detect(link)
        if detected is not None:
            found.add(detected)
    return found


def suggestions(
    rows: Iterable[tuple[str | None, str | None, str | None]],
    listed: Iterable[CompanyBoard],
    limit: int = SUGGESTIONS_MAX,
) -> list[tuple[Detected, str, int]]:
    """(board, a company name, how many postings point to it), most pointed to first, from
    (url, text, company) rows; boards in `listed` left out."""
    have = {board.key for board in listed}
    counts: Counter[Detected] = Counter()
    names: dict[Detected, Counter[str]] = {}
    for url, text, company in rows:
        for board in boards_in(url, text):
            if (board.ats, board.board.casefold()) in have:
                continue
            counts[board] += 1
            if company and company.strip() and company.strip() != UNKNOWN_EMPLOYER:
                names.setdefault(board, Counter())[company.strip()[:200]] += 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0].ats, item[0].board))
    return [
        (board, names[board].most_common(1)[0][0] if board in names else board.company, count)
        for board, count in ranked[:limit]
    ]


def of_user(
    db: Session, user_id: uuid.UUID, listed: Iterable[CompanyBoard]
) -> list[tuple[Detected, str, int]]:
    rows = db.execute(
        sa.select(Job.url, sa.func.left(Job.posting_text, TEXT_READ), Job.company)
        .where(Job.user_id == user_id)
        .order_by(Job.created_at.desc())
        .limit(JOBS_READ)
    )
    return suggestions(((url, text, company) for url, text, company in rows), listed)
