"""Apply to the selected postings (owner, 2026-11-06: "in the job list and map, i want being able
with the multi select to apply at all selected"; SPEC-apply, "API").

- POST /api/apply/bulk/preview `{posting_ids}` (1 to 500, the Jobs list's bulk cap, each once):
  what one click on "Apply to N" would do, read only. In the order given, until `LIMIT` postings
  are to apply: each posting the click would start (`items`: its company, title, the documents
  it lacks, which the click writes first, with their estimate, whether it is an e-mail
  application, `seen`, what the confirmation shows of it, and its `notices`), each one it would not
  (`left_out`, with the code and the sentence of the refusal its own Apply click meets), and the
  rest (`beyond`, not read: "select the others after"); the person's review box in force, the
  total estimate of the writing and the note on its cost.
- POST /api/apply/bulk `{review_before_send, items: [{posting_id, seen, documents}]}` (1 to
  `LIMIT`, each once): the person's approval of exactly the postings the confirmation named and
  nothing else (CLAUDE.md rule 4). Each goes through the path of its own Apply click
  (`likes.on_apply`: the documents it lacks asked, `service.start`'s refusals, its events, then
  the fill, the apply agent or, with the review box ticked, Review first's apply agent of A20,
  each `ready` in turn with its list and one Confirm), one transaction per posting, in the
  order given, once their own addresses were read side by side (jobs/liveness_check.py: a page
  that says it is closed closes its posting, whose Apply then refuses `posting_closed`);
  the answer holds a result per posting. A posting that changed since (`changed`: its company,
  title, form address or mode, or documents to write that the confirmation did not show), that
  is gone (`not_found`) or that now meets a refusal is left out with its sentence; the others go
  on. A review box that changed since answers 409 `settings_changed` and starts nothing: what
  the click does is the sentence the confirmation showed.

Two refusals of the bulk's own, read again at the click, each toward applying less: a posting on
LinkedIn while LinkedIn work is not allowed (`linkedin_off`, `linkedin_paused`: its own Apply is
not offered then, and its run would end `blocked` before any lease), and another posting of the
same role at the same company with an application under way (`same_role_live`, the Like's rule:
one selection holding the same job found on two sites applies once). An application by e-mail is
Review first, as from its own Apply (CLAUDE.md rule 3).

The bound (`LIMIT`, 25 a click): the apply queue fills one application at a time (one browser,
`worker.apply_queue`), and one run of the apply agent takes minutes (3 to 8 seconds between two
of its acts), so 25 already hold the browser for hours; 25 also stays under the safety ceiling of
30 submissions a day the owner set for automatic sending (2026-10-14), so one click never sends
more than a day of it; and the documents written first are at most 25 pairs, said before.

The user always comes from the session (default deny, CSRF on every write); another person's
posting reads as one that is gone. Every answer is `Cache-Control: no-store`. Logs carry ids and
codes only.
"""

import hashlib
import json
import logging
import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.agents import credentials
from app.agents.models import CredentialKind
from app.apply import caps, gate, guardrails, likes, service
from app.apply.models import Mode
from app.apply.router import BUSY, NO_STORE, RefusalOut
from app.browser import linkedin_guard
from app.db import get_session
from app.documents import estimate, store
from app.documents import freeze as documents
from app.documents.base_router import EstimateOut, _estimate_out
from app.documents.models import Kind
from app.identity import deps
from app.identity.models import User
from app.ingestion import pricing
from app.jobs import liveness_check, notices
from app.jobs import service as jobs_service
from app.jobs.models import Job
from app.jobs.schemas import BULK_MAX, NoticeOut

logger = logging.getLogger(__name__)

LIMIT = 25
SENTENCES = {
    "not_found": "This posting is no longer in your list.",
    "changed": (
        "This posting or its documents changed since you opened this. Select it again to apply."
    ),
    "linkedin_off": gate.SENTENCES["linkedin_off"],
    "linkedin_paused": "LinkedIn is paused for a week after a security check.",
    "busy": BUSY,
    "failed": "This one could not be started. Try again from its page.",
}
SETTINGS_CHANGED = (
    "Your review setting changed since you opened this. Open Apply again to see what it does now."
)


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE)


router = APIRouter(tags=["apply"], dependencies=[Depends(_no_store)])

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]


def _once_each[T](values: list[T]) -> list[T]:
    if len(set(values)) != len(values):
        raise ValueError("Name each posting once.")
    return values


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    posting_ids: list[uuid.UUID] = Field(min_length=1, max_length=BULK_MAX)

    @field_validator("posting_ids")
    @classmethod
    def _distinct(cls, ids: list[uuid.UUID]) -> list[uuid.UUID]:
        return _once_each(ids)


class PostingOut(BaseModel):
    posting_id: uuid.UUID
    company: str | None  # None for a posting that is gone
    title: str | None


class ItemOut(PostingOut):
    # The kinds the click writes first, in order: [], ["letter"] or ["cv", "letter"].
    documents: list[str]
    estimate: EstimateOut | None  # of that writing, None when nothing is written
    email: bool  # an application by e-mail: Review first, the person sends it
    seen: str
    notices: list[NoticeOut] = []  # what to know before the click (app/jobs/notices.py)


class LeftOut(PostingOut):
    code: str
    detail: str


class PreviewOut(BaseModel):
    review_before_send: bool
    limit: int
    items: list[ItemOut]
    left_out: list[LeftOut]
    beyond: list[PostingOut]
    estimate: EstimateOut | None  # of all the writing, None when nothing is written
    cost_note: str


class StartItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    posting_id: uuid.UUID
    seen: str = Field(min_length=64, max_length=64)
    documents: list[Kind] = Field(max_length=2)


class StartIn(BaseModel):
    """The postings the confirmation named, each as it showed it, and the review box it said."""

    model_config = ConfigDict(extra="forbid")

    review_before_send: StrictBool
    items: list[StartItemIn] = Field(min_length=1, max_length=LIMIT)

    @field_validator("items")
    @classmethod
    def _distinct(cls, items: list[StartItemIn]) -> list[StartItemIn]:
        _once_each([one.posting_id for one in items])
        return items


class ResultOut(BaseModel):
    posting_id: uuid.UUID
    started: bool
    application_id: uuid.UUID | None = None
    mode: str | None = None
    state: str | None = None
    code: str | None = None  # why it was left out
    detail: str | None = None


class StartOut(BaseModel):
    results: list[ResultOut]
    started: int
    left_out: int


class _LeftOutError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _sentence(code: str) -> str:
    return SENTENCES.get(code) or likes.sentence(code)


def _mode(review_before_send: bool) -> Mode:
    """What the Apply button starts (`router._mode_of`), never a rehearsal."""
    return Mode.review if review_before_send else Mode.direct


def _seen(posting: Job, planned: service.Plan) -> str:
    """What the confirmation shows of a posting and what its click starts, as a hex SHA-256."""
    shown = [
        str(posting.id),
        posting.company,
        posting.title,
        planned.mode.value,
        planned.ats,
        planned.form_url,
    ]
    return hashlib.sha256(json.dumps(shown).encode()).hexdigest()


def _check(
    db: Session,
    user_id: uuid.UUID,
    posting: Job,
    mode: Mode,
    request: Request,
) -> tuple[service.Plan, tuple[Kind, ...]]:
    """The refusals of the posting's own Apply click, then the bulk's two (module docstring),
    and the documents the click would write. Raises `service.RefusedError` or `_LeftOutError`;
    writes nothing."""
    now = deps.clock(request)
    planned = service.plan(
        db,
        user_id,
        posting,
        mode,
        now=now,
        stub_base=request.app.state.settings.apply_stub_base_url,
    )
    if caps.host_of(planned.ats, planned.form_url) == "linkedin":
        code = linkedin_guard.refusal(db, user_id, now)
        if code is not None:
            raise _LeftOutError(code)
    if (other := likes._same_role_live(db, user_id, posting)) is not None:
        raise service.RefusedError(guardrails.Refusal("same_role_live", other))
    return planned, likes.kinds_to_ask(documents.current(db, user_id, posting.id))


def _posting(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID) -> Job | None:
    return db.execute(
        sa.select(Job).where(Job.user_id == user_id, Job.id == posting_id)
    ).scalar_one_or_none()


@router.post("/api/apply/bulk/preview", response_model=PreviewOut)
def preview(body: PreviewIn, request: Request, user: CurrentUser, db: Db) -> PreviewOut:
    """What Apply to the selected postings would do, read only (module docstring)."""
    review = caps.settings_of(db, user.id).review_before_send
    mode = _mode(review)
    credential = credentials.get(db, user.id)
    kind = CredentialKind(credential.kind) if credential is not None else None
    model, priced = estimate.model_for(kind), pricing.priced(kind)
    items: list[ItemOut] = []
    left_out: list[LeftOut] = []
    beyond: list[PostingOut] = []
    writing: list[estimate.RunEstimate] = []
    # What the earlier postings of this click take, as their committed applications will.
    keys: set[str] = set()
    roles: set[tuple[str, str]] = set()
    shown: list[Job] = []
    for posting_id in body.posting_ids:
        posting = _posting(db, user.id, posting_id)
        if posting is None:
            left_out.append(
                LeftOut(
                    posting_id=posting_id,
                    company=None,
                    title=None,
                    code="not_found",
                    detail=_sentence("not_found"),
                )
            )
            continue
        names = {"posting_id": posting.id, "company": posting.company, "title": posting.title}
        if len(items) >= LIMIT:
            beyond.append(PostingOut(**names))
            continue
        role = (guardrails.company_key(posting.company), guardrails.title_key(posting.title))
        try:
            planned, kinds = _check(db, user.id, posting, mode, request)
            if planned.key in keys:
                raise service.RefusedError(guardrails.Refusal("same_form"))
            if role in roles:
                raise service.RefusedError(guardrails.Refusal("same_role_live"))
        except service.RefusedError as exc:
            code = exc.refusal.code
            left_out.append(LeftOut(**names, code=code, detail=_sentence(code)))
            continue
        except _LeftOutError as exc:
            left_out.append(LeftOut(**names, code=exc.code, detail=_sentence(exc.code)))
            continue
        keys.add(planned.key)
        roles.add(role)
        figures = likes.writing_estimate(db, user.id, posting.id, kinds) if kinds else None
        if figures is not None:
            writing.append(figures)
        items.append(
            ItemOut(
                **names,
                documents=[one.value for one in kinds],
                estimate=_estimate_out(figures, model, priced) if figures is not None else None,
                email=planned.ats == "email",
                seen=_seen(posting, planned),
            )
        )
        shown.append(posting)
    said = notices.notices_of(db, user.id, shown, notices.today_of(deps.clock(request)))
    for item in items:
        item.notices = [NoticeOut.model_validate(notice) for notice in said[item.posting_id]]
    db.rollback()
    return PreviewOut(
        review_before_send=review,
        limit=LIMIT,
        items=items,
        left_out=left_out,
        beyond=beyond,
        estimate=_estimate_out(estimate.combine(writing), model, priced) if writing else None,
        cost_note=pricing.ESTIMATE_NOTE if priced else pricing.QUOTA_NOTE,
    )


def _start_one(
    db: Session, user: User, item: StartItemIn, mode: Mode, request: Request
) -> ResultOut:
    """One posting of the click, in its own transaction: under the locks of its Apply click (the
    posting's row, the person's apply lock, its documents), checked again against what the
    confirmation showed, then `likes.on_apply`, which commits."""
    posting = jobs_service.lock_job(db, user.id, item.posting_id)
    if posting is None:
        raise _LeftOutError("not_found")
    service._lock_person(db, user.id)
    likes.lock_documents(db, user.id, posting.id)
    planned, kinds = _check(db, user.id, posting, mode, request)
    if _seen(posting, planned) != item.seen or not set(kinds) <= set(item.documents):
        raise _LeftOutError("changed")
    started = likes.on_apply(
        db,
        user.id,
        posting.id,
        mode,
        False,
        now=deps.clock(request),
        stub_base=request.app.state.settings.apply_stub_base_url,
        # A20: as from its own Apply: with the box ticked the apply agent fills each one, and
        # each waits for the person's Confirm in its held session, one at a time (one browser).
        agent_review=True,
    )
    application = started.application
    return ResultOut(
        posting_id=posting.id,
        started=True,
        application_id=application.id,
        mode=application.mode.value,
        state=application.state.value,
    )


@router.post("/api/apply/bulk", response_model=StartOut, responses={409: {"model": RefusalOut}})
def start(body: StartIn, request: Request, user: CurrentUser, db: Db) -> StartOut | JSONResponse:
    """Apply to the postings the confirmation named (module docstring)."""
    if caps.settings_of(db, user.id).review_before_send != body.review_before_send:
        db.rollback()
        content = {"detail": SETTINGS_CHANGED, "code": "settings_changed"}
        return JSONResponse(status_code=409, content=content, headers=NO_STORE)
    db.rollback()
    liveness_check.check_all(request, user.id, [item.posting_id for item in body.items])
    mode = _mode(body.review_before_send)
    results: list[ResultOut] = []
    for item in body.items:
        code: str | None = None
        try:
            results.append(_start_one(db, user, item, mode, request))
            continue
        except _LeftOutError as exc:
            code = exc.code
        except service.RefusedError as exc:
            code = exc.refusal.code
        except service.PostingNotFoundError:
            code = "not_found"
        except (jobs_service.JobBusyError, store.DocumentBusyError):
            code = "busy"
        except DBAPIError as exc:
            # One posting that fails alone: the others still go.
            code = "busy" if jobs_service.is_lock_timeout(exc) else "failed"
            if code == "failed":
                logger.exception("apply.bulk_failed", extra={"posting_id": str(item.posting_id)})
        db.rollback()
        results.append(
            ResultOut(posting_id=item.posting_id, started=False, code=code, detail=_sentence(code))
        )
    started = sum(one.started for one in results)
    logger.info(
        "apply.bulk",
        extra={"started": started, "left_out": [one.code for one in results if not one.started]},
    )
    return StartOut(results=results, started=started, left_out=len(results) - started)
