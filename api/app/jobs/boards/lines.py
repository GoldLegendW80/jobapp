"""Jobs J42 (issue #888; SPEC-jobs.md, Company job boards): a board's posting stored on one line
gets its lines when its board lists it again, with nobody's click.

Before J42 the readers of Greenhouse, Workable and Recruitee turned a posting's HTML into one line
(tags became spaces); they now read it as the job feeds do (ats.py). The store never replaces the
text of a posting found again, so a posting stored before keeps its one line until this pass:
after a board's postings are stored (run.py), every posting the board lists now, whether its
filters kept it or not (one found weeks ago is often older than the search's days now), is looked
for among the person's postings by its address, and `relined` decides:

- the stored text is one line: no line break, but for the posting's title and the blank line the
  store puts first when the text does not name the title (sweep._read_of);
- the stored characters other than spaces and line breaks are, in order and next to each other,
  those of a run of the board's text now: the whole text, or the run the posting reader cut from
  it; the new text is that run, so only the spaces and line breaks between the characters change,
  and it holds a line break;
- the run never glues: wherever the stored text held two letters, numbers or marks apart (by
  their Unicode category, any script), the run holds a space or a line break between them too.
  A word the old reading split at an inline tag ("Soft ware" from `<b>Soft</b>ware`) looks
  exactly like two words a converter glued, so it keeps its one line.

Any other text stays as stored: a word changed, added, removed or glued, other punctuation, a
text with lines already.

A posting kept from a card is left to its page's read (J31). Each posting is written in its own
transaction, locked with SKIP LOCKED and read again under the lock; one another transaction holds,
or one a posting reader's or a rating's run is bound to, is left for the next search; nothing is
written once the run let go of its row. The statement keeps `updated_at` and writes what a store
derives from the text again: the signals, the fingerprint and the closing date (`signals.stored`),
the fit with its competencies and gaps (a fit set by hand kept), and the description when it was
the head of the stored text. No History event; the status, the reader's `read_at` and attempts and
every other column stay, so the posting reader does not read it again (its quotes are found with
spacing aside, and its cut is the run kept). The career and culture reading is tied to the text's
digest, so it is made again. Logs carry ids and counts, never a text.
"""

import bisect
import itertools
import logging
import re
import unicodedata
import uuid
from collections.abc import Callable, Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.jobs import scoring, signals
from app.jobs.models import Job
from app.jobs.service import DESCRIPTION_CHARS
from app.jobs.visible_text import visible, visible_line
from app.profile.schemas import SearchSection, SkillsSection

logger = logging.getLogger("app.jobs.boards.lines")

LOGGED_IDS = 50
_TOKEN = re.compile(r"\S+")
_SPACES = re.compile(r"\s+")


def _head(text: str, title: str) -> str:
    """The title line and blank line the store put first, when `text` starts with them."""
    head = f"{title}\n\n"
    return head if title and text.startswith(head) else ""


def one_line(text: str, title: str) -> bool:
    """Whether `text` holds no line break but after the title the store put first."""
    body = text[len(_head(text, title)) :]
    return "\n" not in body and "\r" not in body


def _word(character: str) -> bool:
    """A letter, a number or a mark, in any script (Unicode categories L, N and M)."""
    return unicodedata.category(character)[0] in "LNM"


def _apart(body: str) -> list[int]:
    """Where `body` without its white space held two word characters (`_word`) apart: the index
    in it of the second one."""
    places, removed = [], 0
    for match in _SPACES.finditer(body):
        start, end = match.span()
        if start > 0 and end < len(body) and _word(body[start - 1]) and _word(body[end]):
            places.append(start - removed)
        removed += end - start
    return places


def relined(stored: str | None, text: str, title: str) -> str | None:
    """`stored` with the lines of the board's `text` (module docstring), or None."""
    if not stored or "\n" not in text or not one_line(stored, title):
        return None
    head = _head(stored, title)
    wanted = _SPACES.sub("", stored[len(head) :])
    tokens = [match.span() for match in _TOKEN.finditer(text)]
    found = "".join(text[start:end] for start, end in tokens).find(wanted) if wanted else -1
    if found < 0:
        return None
    ends = list(itertools.accumulate(end - start for start, end in tokens))
    # Never glue: two word characters the stored text held apart start a new token in the run.
    starts = set(ends)
    if any(found + at not in starts for at in _apart(stored[len(head) :])):
        return None

    def position(at: int) -> int:
        token = bisect.bisect_right(ends, at)
        start, end = tokens[token]
        return start + at - (ends[token] - (end - start))

    run = text[position(found) : position(found + len(wanted) - 1) + 1]
    new = head + run
    return new if "\n" in run and new != stored else None


def _line_breaks(column: Any) -> Any:
    return sa.func.length(column) - sa.func.length(sa.func.replace(column, "\n", ""))


def _held(job: Job) -> bool:
    """A posting reader's or a rating's run holds it until its end is settled."""
    return job.read_run_id is not None or (isinstance(job.match, dict) and "run" in job.match)


def _derived(
    job: Job, text: str, skills: SkillsSection | None, search: SearchSection | None
) -> dict[str, Any]:
    values: dict[str, Any] = {"posting_text": text, **signals.stored(text)}
    stored = job.posting_text or ""
    if job.description == (stored[:DESCRIPTION_CHARS] or None):
        values["description"] = text[:DESCRIPTION_CHARS] or None
    if skills is not None:
        engine = scoring.engine_text(text, (job.legacy or {}).get("posting_header"))
        result = scoring.score(engine, skills, title=job.title, search=search)
        values["competencies"], values["gaps"] = list(result.competencies), list(result.gaps)
        if not job.fit_overridden:
            values["fit"] = result.fit
    return values


def _give(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    board: tuple[str, str],
    skills: SkillsSection | None,
    search: SearchSection | None,
) -> bool:
    text, title = board
    with sessions() as db:
        job = db.execute(
            sa.select(Job)
            .where(Job.id == job_id, Job.user_id == user_id)
            .options(undefer(Job.posting_text))
            .with_for_update(key_share=True, skip_locked=True)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if job is None or job.list_url is not None or _held(job):
            return False
        new = relined(job.posting_text, text, title)
        if new is None:
            return False
        db.execute(
            sa.update(Job)
            .where(Job.id == job_id)
            .values(**_derived(job, new, skills, search), updated_at=Job.updated_at)
            .execution_options(synchronize_session=False)
        )
        db.commit()
        return True


def give_lines(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    postings: Sequence[Any],
    skills: SkillsSection | None,
    search: SearchSection | None,
    stopped: Callable[[], bool] = lambda: False,
) -> int:
    """Give their lines to the person's postings stored on one line that `postings`, what one
    board lists now (each with `url`, `title` and `text`), hold (module docstring). A posting
    whose write raises is logged and left as stored, the others go on; nothing is written once
    `stopped()`. Returns how many were written."""
    boards = {p.url: (visible(p.text), visible_line(p.title)) for p in postings if p.text}
    if not boards or stopped():
        return 0
    with sessions() as db:
        rows = db.execute(
            sa.select(Job.id, Job.url, Job.posting_text).where(
                Job.user_id == user_id,
                Job.url.in_(list(boards)),
                Job.list_url.is_(None),
                Job.posting_text.is_not(None),
                _line_breaks(Job.posting_text) <= 2,
            )
        ).all()
    ids = [row.id for row in rows if relined(row.posting_text, *boards[row.url]) is not None]
    urls = {row.id: row.url for row in rows}
    written: list[uuid.UUID] = []
    for job_id in ids:
        if stopped():
            break
        try:
            if _give(sessions, user_id, job_id, boards[urls[job_id]], skills, search):
                written.append(job_id)
        except Exception:
            logger.exception("jobs.boards.lines.posting_failed", extra={"job_id": str(job_id)})
    if written:
        logger.info(
            "jobs.boards.lines",
            extra={"written": len(written), "job_ids": [str(i) for i in written[:LOGGED_IDS]]},
        )
    return len(written)
