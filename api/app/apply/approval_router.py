"""The person's decisions on an application (apply A6; SPEC-apply, "API").

- GET /api/applications/{id}/approval: what the approval covers: the fields of the filled form
  (label, kind, value, source, required; a field left is `you_answer`), the documents filled
  (version, fit, pages, how many corrections), the screenshots, and `answers_hash`, the hex
  SHA-256 the approve call sends back.
- GET /api/applications/{id}/screenshots/{shot_id}: one JPEG, owner only.
- POST /api/applications/{id}/approve `{answers_hash}`: the person's approval, then the gate's
  decision (`gate.authorise_approval`, `gate.decide`), committed before the submit lease is
  queued. 200 with the application `approved`; 409 `{detail, code}` for a refusal (the gate's
  codes), and for a stale hash `{detail, code: "stale", answers_hash, answers}` with the current
  fields.
- PUT /api/applications/{id}/answers/{field_key} `{value}`: the person's own answer to one field
  of the form (one left for them, `you_answer`, or one they override): source `person`, a new
  hash, any approval void (`approved` goes back to `ready`); the submit lease refills and reads it
  back, and a refill by the run keeps it. 200 with the approval view; 422 `{detail, code}` for an
  unknown field (`unknown_field`: none, a file, a skipped widget, a password's box) or a value that
  does not fit it (`wrong_type`: a text for a text, select or combobox, at most 5000 characters,
  or 10000 for an adapter-owned document-letter box; true or false for a checkbox or a yes or no
  question); 409 `not_editable` once the app clicked or after an end, `signin_first` while it
  waits for a sign in.
- POST /api/applications/{id}/reject, /cancel: `cancelled` (reject: the form as filled, before
  an approval; cancel: anything before the click).
- POST /api/applications/{id}/submitted: "It was submitted" on `submit_unknown`.
- POST /api/applications/{id}/retry: "Try again" after `submit_unknown` or `refused_by_site`:
  back to `ready`, where a new approval is needed.

The user always comes from the session (default deny, CSRF on every write); another person's
application answers 404, as a missing one. Every answer is `Cache-Control: no-store`. A lock
held past its timeout answers 409 `busy`.
"""

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

import anyio
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Session

from app import queue as queues
from app.apply import (
    admin,
    answers,
    bank,
    gate,
    model_check,
    queue_router,
    remember,
    service,
    shots,
)
from app.apply.models import (
    AnswerBankEntry,
    AnswerSource,
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    State,
)
from app.apply.router import NO_APPLICATION, NO_STORE, ApplicationOut
from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs import service as jobs_service
from app.jobs.models import Job
from app.profile import service as profile_service
from app.profile.models import Profile

logger = logging.getLogger(__name__)

SUBMIT_FUNCTION = "apply_submit"


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE)


router = APIRouter(tags=["apply"], dependencies=[Depends(_no_store)])

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]


class ConfirmIn(BaseModel):
    """A20: Confirm and send, with the hash of the list of filled fields the page showed."""

    model_config = ConfigDict(extra="forbid")

    fields_hash: str = Field(min_length=gate.HASH_HEX, max_length=gate.HASH_HEX)


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers_hash: str = Field(min_length=gate.HASH_HEX, max_length=gate.HASH_HEX)
    confirmed_email_recipient: str | None = Field(default=None, max_length=254)
    # IDs contain a fact-check rule code and two offsets into the 100000-character body.
    acknowledged_email_issues: list[Annotated[str, Field(max_length=64)]] = Field(
        default_factory=list, max_length=1000
    )


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Annotated[str, Field(min_length=1, max_length=gate.LETTER_MAX)] | StrictBool


class AnswerOut(BaseModel):
    field_key: str
    label: str
    kind: str
    options: list[str] | None = None  # the choices of a select, combobox or single, when read
    value: Any
    source: str
    required: bool
    approved: bool
    you_answer: bool  # left by the app: the person answers it on the site, or leaves it
    max_chars: int
    left_reason: str | None = None
    # For a field left for the person: "Remember this answer for next forms" offered ticked
    # ("on"), offered unticked because the question names the company ("off"), or not offered.
    remember: Literal["on", "off"] | None = None


class RememberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_key: str = Field(min_length=1, max_length=500)
    value: Annotated[str, Field(min_length=1, max_length=bank.VALUE_MAX)] | StrictBool


class RememberOut(BaseModel):
    """`saved`: the answer is now in Saved answers. `kept`: Saved answers already held an answer
    to this question, and it was kept as it is."""

    outcome: Literal["saved", "kept"]
    question: str


class ShotOut(BaseModel):
    id: uuid.UUID
    kind: str
    created_at: datetime


class ClaimOut(BaseModel):
    field: str | None  # the answer's field_key; None for an answer the checker was not given
    code: str
    start: int | None  # where the flagged words stand in the answer's value, when found
    end: int | None


class CheckOut(BaseModel):
    """The last check by model of this item's free text (A13c, `model_check.view`)."""

    verdict: str
    current: bool  # it covers the answers shown now
    claims: list[ClaimOut]


class ApprovalView(BaseModel):
    email: dict[str, Any] | None = None
    state: str
    dry_run: bool
    answers_hash: str
    answers: list[AnswerOut]
    documents: dict[str, dict[str, Any]]
    screenshots: list[ShotOut]
    check: CheckOut | None = None


class GateRefusalOut(BaseModel):
    detail: str
    code: str
    state: str | None = None
    answers_hash: str | None = None
    answers: list[AnswerOut] | None = None


_404 = {404: {"model": ErrorOut, "description": "No such application (or not yours)"}}
_409 = {409: {"model": GateRefusalOut, "description": "Refused by the gate"}}


def _uuid_or_404(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_APPLICATION) from None


def _application(db: Session, user: User, application_id: str) -> Application:
    found = service.get_application(db, user.id, _uuid_or_404(application_id))
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_APPLICATION)
    return found


def _context(db: Session, application: Application) -> tuple[answers.Context, str | None]:
    """What the resolver reads for this application's form, without the bank, and the company:
    for the "Remember this answer" box (`remember.py`). Reads the profile, never creates it."""
    profile = db.scalar(sa.select(Profile).where(Profile.user_id == application.user_id))
    sections = (
        {k: v for k, v in profile_service.sections_view(profile).items() if v} if profile else {}
    )
    posting = db.get(Job, application.posting_id)
    return (
        answers.Context(
            application.user_id,
            sections,
            answers.Posting.at(posting.location, posting.title) if posting else None,
        ),
        posting.company if posting else None,
    )


def _field(row: ApplicationAnswer) -> answers.FormField:
    return answers.FormField(
        row.label,
        row.kind,  # type: ignore[arg-type]
        tuple(row.options or ()),
        row.required,
    )


def _rememberable(row: ApplicationAnswer, email: bool) -> bool:
    return not (
        email or row.document_letter or row.field_key.startswith(("skipped:", "designated:"))
    )


def _answers(db: Session, application: Application) -> list[AnswerOut]:
    email = application.ats == "email"
    rows = gate.rows_of(db, application)
    offer: dict[str, Literal["on", "off"] | None] = {}
    left = [r for r in rows if r.source is AnswerSource.left and _rememberable(r, email)]
    if left:
        context, company = _context(db, application)
        offer = {r.field_key: remember.offered(_field(r), context, company) for r in left}
    return [
        AnswerOut(
            field_key=row.field_key,
            label=row.label,
            kind=row.kind,
            options=row.options or None,
            value=row.value,
            source=row.source.value,
            required=row.required,
            approved=row.approved,
            you_answer=row.source is AnswerSource.left,
            max_chars=gate.answer_max(row, email=application.ats == "email"),
            left_reason=row.left_reason if row.source is AnswerSource.left else None,
            remember=offer.get(row.field_key),
        )
        for row in rows
        if not row.field_key.startswith("designated:shape:")
        and row.field_key not in {"designated:incomplete", "designated:strategy"}
    ]


def _documents(application: Application) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for kind, record in (application.document_versions or {}).items():
        if isinstance(record, dict):
            out[kind] = {
                "version_id": record.get("version_id"),
                "number": record.get("number"),
                "fit": record.get("fit"),
                "pages": record.get("pages"),
                "corrections": len(record.get("corrections") or []),
            }
    return out


def _refusal_out(code: str, **rest: Any) -> GateRefusalOut:
    return GateRefusalOut(detail=gate.SENTENCES.get(code, code), code=code, **rest)


def _status_refusal(status_code: int, code: str) -> JSONResponse:
    out = _refusal_out(code)
    return JSONResponse(
        status_code=status_code, content=out.model_dump(mode="json"), headers=NO_STORE
    )


def _refusal(code: str, **rest: Any) -> JSONResponse:
    out = _refusal_out(code, **rest)
    return JSONResponse(status_code=409, content=out.model_dump(mode="json"), headers=NO_STORE)


async def _paused(request: Request) -> bool:
    try:
        pool = await request.app.state.queue.pool()
    except Exception:  # Redis down: fail closed
        return True
    return await admin.paused(pool)


def _enqueue_fill(request: Request, application_id: uuid.UUID) -> None:
    """A partial wizard resumes from the original job; the tick retries an unavailable queue."""

    async def enqueue() -> bool:
        return await request.app.state.queue.enqueue(
            "apply_fill",
            str(application_id),
            queue=queues.APPLY,
            job_id=queues.apply_fill_job_id(application_id),
        )

    try:
        anyio.from_thread.run(enqueue)
    except queues.QueueUnavailableError:
        logger.warning("apply.fill_enqueue_failed", extra={"application_id": str(application_id)})


def _enqueue_submit(request: Request, application_id: uuid.UUID) -> None:
    """After the commit. Redis down leaves the application `approved`: the tick asks again."""

    async def enqueue() -> bool:
        found: bool = await request.app.state.queue.enqueue(
            SUBMIT_FUNCTION,
            str(application_id),
            queue=queues.APPLY,
            job_id=queues.apply_submit_job_id(application_id),
        )
        return found

    try:
        anyio.from_thread.run(enqueue)
    except queues.QueueUnavailableError:
        logger.warning("apply.submit_enqueue_failed", extra={"application_id": str(application_id)})


@router.get(
    "/api/applications/{application_id}/approval", response_model=ApprovalView, responses=_404
)
def read_approval(application_id: str, request: Request, user: CurrentUser, db: Db) -> ApprovalView:
    """What an approval of this application covers, and the hash to send back."""
    application = _application(db, user, application_id)
    email_check = None
    if application.ats == "email":
        from app.apply import email as apply_email
        from app.mail import gate as mail_gate
        from app.mail.draft_models import MailDraft

        if apply_email.recover(
            db,
            application,
            deps.clock(request),
            configured=bool(request.app.state.settings.mail_send_key),
        ):
            db.commit()
        values = {row.field_key: row for row in gate.rows_of(db, application)}
        body = values.get("email_body")
        recipient = values.get("email_to")
        preview = MailDraft(
            user_id=user.id,
            application_id=application.id,
            kind="application",
            to_addrs=[recipient.value] if recipient and recipient.value else [],
            cc_addrs=[],
            body=body.value if body else "",
            subject="",
        )
        email_check = {
            "issues": mail_gate.issues(db, preview, application.updated_at),
            "written_by": "user" if body and body.source is AnswerSource.person else "agent",
        }
    return ApprovalView(
        email=email_check,
        state=application.state.value,
        dry_run=application.dry_run,
        answers_hash=gate.current_hash(db, application).hex(),
        answers=_answers(db, application),
        documents=_documents(application),
        screenshots=[
            ShotOut(id=s.id, kind=s.kind.value, created_at=s.created_at)
            for s in shots.of_application(db, user.id, application.id)
        ],
        check=model_check.view(db, application),
    )


@router.get(
    "/api/applications/{application_id}/screenshots/{shot_id}",
    response_class=Response,
    responses={**_404, 200: {"content": {"image/jpeg": {}}}},
)
def read_screenshot(application_id: str, shot_id: str, user: CurrentUser, db: Db) -> Response:
    """One screenshot of one of the caller's applications."""
    application = _application(db, user, application_id)
    owned = {s.id for s in shots.of_application(db, user.id, application.id)}
    wanted = _uuid_or_404(shot_id)
    data = shots.get(db, user.id, wanted) if wanted in owned else None
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_APPLICATION)
    return Response(content=data, media_type="image/jpeg", headers=NO_STORE)


def _approve(
    request: Request, user: User, db: Session, wanted: uuid.UUID, body: ApproveIn
) -> tuple[int, ApplicationOut | GateRefusalOut]:
    """One approval, the same for the approve route and Approve all ready: the person's
    authorisation bound to the hash they saw, then the gate's decision, committed before the
    submit lease is queued. The status code and the body: 200 with the application, 404, 409 or
    422 with the refusal (nothing approved)."""
    sent = gate.parse_hash(body.answers_hash)
    if sent is None:
        return 409, _refusal_out("stale")
    settings = request.app.state.settings
    paused = anyio.from_thread.run(_paused, request)
    now = deps.clock(request)
    try:
        authorisation = gate.authorise_approval(
            db, user.id, wanted, sent, now, settings.apply_approval_ttl_s
        )
        approved_application = db.get(Application, wanted)
        if approved_application.ats == "email":
            recipient = db.get(ApplicationAnswer, (wanted, "email_to"))
            if recipient is None or body.confirmed_email_recipient != recipient.value:
                db.rollback()
                return 409, _refusal_out("unconfirmed_recipient")
            from app.apply.email import sync_draft
            from app.mail import drafts as mail_drafts
            from app.mail.draft_models import MailDraft

            draft = db.scalar(sa.select(MailDraft).where(MailDraft.application_id == wanted))
            if draft is not None:
                sync_draft(db, approved_application, draft, now)
                draft.fact_check = {
                    "hash": mail_drafts.content_hash(draft),
                    "acknowledged": body.acknowledged_email_issues,
                }
        decision = gate.decide(
            db,
            user.id,
            authorisation.id,
            now,
            switches=gate.Switches(enabled=settings.apply_submit_enabled, paused=paused),
        )
        db.commit()
    except gate.NotFoundError:
        db.rollback()
        return 404, GateRefusalOut(detail=NO_APPLICATION, code="not_found")
    except gate.StaleError as exc:
        db.rollback()
        found = service.get_application(db, user.id, wanted)
        if found is None:
            return 404, GateRefusalOut(detail=NO_APPLICATION, code="not_found")
        return 409, _refusal_out(
            "stale",
            state=found.state.value,
            answers_hash=exc.current.hex(),
            answers=_answers(db, found),
        )
    except gate.InvalidAnswerError as exc:
        db.rollback()
        return 422, GateRefusalOut(detail=gate.SENTENCES[exc.code], code=exc.code)
    except gate.RefusedError as exc:
        db.rollback()
        return 409, _refusal_out(exc.code)
    except jobs_service.JobBusyError:
        db.rollback()
        return 409, _refusal_out("busy")
    if not decision.passed:
        return 409, _refusal_out(decision.code or "refused", state=decision.state.value)
    approved = db.get(Application, wanted)
    if approved is not None and approved.ats == "email":
        from app.mail.dispatch import application as send_email_application

        db.commit()
        anyio.from_thread.run(send_email_application, request.app, wanted)
        db.expire_all()
    else:
        _enqueue_submit(request, wanted)
    return 200, ApplicationOut.model_validate(_application(db, user, str(wanted)))


@router.post(
    "/api/applications/{application_id}/approve",
    response_model=ApplicationOut,
    responses={**_404, **_409},
)
def approve(
    application_id: str, body: ApproveIn, request: Request, user: CurrentUser, db: Db
) -> ApplicationOut | JSONResponse:
    """Approve and submit: the form exactly as shown, then the gate's decision."""
    code, out = _approve(request, user, db, _uuid_or_404(application_id), body)
    if code == 404:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_APPLICATION)
    if isinstance(out, ApplicationOut):
        return out
    return JSONResponse(status_code=code, content=out.model_dump(mode="json"), headers=NO_STORE)


class ReadyItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: uuid.UUID
    answers_hash: str = Field(min_length=gate.HASH_HEX, max_length=gate.HASH_HEX)


class ApproveReadyIn(BaseModel):
    """The items the confirmation named, each with the hash of the form it showed."""

    model_config = ConfigDict(extra="forbid")

    items: list[ReadyItemIn] = Field(min_length=1, max_length=queue_router.QUEUE_MAX)

    @field_validator("items")
    @classmethod
    def _once_each(cls, items: list[ReadyItemIn]) -> list[ReadyItemIn]:
        if len({one.application_id for one in items}) != len(items):
            raise ValueError("Name each application once.")
        return items


class ReadyResultOut(BaseModel):
    application_id: uuid.UUID
    approved: bool
    state: str | None = None
    code: str | None = None  # why it was left out: the gate's code, `stale`, `not_found`
    detail: str | None = None


class ApproveReadyOut(BaseModel):
    results: list[ReadyResultOut]
    approved: int
    left_out: int


@router.post("/api/apply/queue/approve", response_model=ApproveReadyOut)
def approve_ready(
    body: ApproveReadyIn, request: Request, user: CurrentUser, db: Db
) -> ApproveReadyOut:
    """Approve all ready: the person's approval of exactly the items the confirmation named,
    each through the approve route's own path (`_approve`: its authorisation, the gate's
    decision and its events), one transaction per item, in the order given. An item whose form
    or documents changed since (`stale`), that is no longer waiting (`not_ready`) or that the
    gate refuses is left out with its sentence; the others go on."""
    results: list[ReadyResultOut] = []
    for item in body.items:
        _, out = _approve(
            request, user, db, item.application_id, ApproveIn(answers_hash=item.answers_hash)
        )
        if isinstance(out, ApplicationOut):
            results.append(ReadyResultOut(application_id=out.id, approved=True, state=out.state))
        else:
            results.append(
                ReadyResultOut(
                    application_id=item.application_id,
                    approved=False,
                    state=out.state,
                    code=out.code,
                    detail=out.detail,
                )
            )
    approved = sum(one.approved for one in results)
    return ApproveReadyOut(results=results, approved=approved, left_out=len(results) - approved)


@router.put(
    "/api/applications/{application_id}/answers/{field_key:path}",
    response_model=ApprovalView,
    responses={**_404, **_409, 422: {"model": GateRefusalOut, "description": "No such field"}},
)
def set_answer(
    application_id: str,
    field_key: str,
    body: AnswerIn,
    request: Request,
    user: CurrentUser,
    db: Db,
) -> ApprovalView | JSONResponse:
    """The person's own answer to one field; any approval of the form before it is void."""
    wanted = _uuid_or_404(application_id)
    try:
        application = gate.set_answer(
            db, user.id, wanted, field_key, body.value, deps.clock(request)
        )
        db.commit()
        if application.state is State.queued:
            _enqueue_fill(request, wanted)
    except gate.NotFoundError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_APPLICATION) from None
    except gate.InvalidAnswerError as exc:
        db.rollback()
        out = GateRefusalOut(detail=gate.SENTENCES[exc.code], code=exc.code)
        return JSONResponse(status_code=422, content=out.model_dump(mode="json"), headers=NO_STORE)
    except gate.RefusedError as exc:
        db.rollback()
        return _refusal(exc.code)
    except jobs_service.JobBusyError:
        db.rollback()
        return _refusal("busy")
    return read_approval(application_id, request, user, db)


@router.post(
    "/api/applications/{application_id}/confirm",
    response_model=ApplicationOut,
    responses={404: {"model": ErrorOut}, 409: {"model": GateRefusalOut}},
)
def confirm_and_send(
    application_id: str, body: ConfirmIn, request: Request, user: CurrentUser, db: Db
) -> ApplicationOut | JSONResponse:
    """Confirm and send (A20): the list of what the apply agent filled, exactly as shown; the
    held session's gate clicks the form's control that sends it."""
    from app.apply import confirm

    wanted = _uuid_or_404(application_id)
    sent = gate.parse_hash(body.fields_hash)
    if sent is None:
        return _refusal("stale")
    try:
        application = confirm.confirm(db, user.id, wanted, sent, deps.clock(request))
    except gate.NotFoundError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_APPLICATION) from None
    except gate.RefusedError as exc:
        if exc.code == "session_lost":
            db.commit()  # the application is `failed`: the page offers Try again
        else:
            db.rollback()
        return _refusal(exc.code)
    except jobs_service.JobBusyError:
        db.rollback()
        return _refusal("busy")
    db.commit()
    return ApplicationOut.model_validate(application)


@router.post(
    "/api/applications/{application_id}/remember",
    response_model=RememberOut,
    responses={
        **_404,
        409: {"model": GateRefusalOut, "description": "Not your answer, or it changed"},
        422: {"model": GateRefusalOut, "description": "An answer that is not remembered"},
    },
)
def remember_answer(
    application_id: str, body: RememberIn, request: Request, user: CurrentUser, db: Db
) -> RememberOut | JSONResponse:
    """Remember this answer for next forms: the person's own answer to one field of this form,
    as saved here, goes to Saved answers under the field's question (owner, 2026-11-05). The
    application does not change. 409 `not_answered` when the field does not hold that answer of
    the person, 422 `not_rememberable` for a question the app never fills from Saved answers
    (a sensitive one among them). An answer already saved to the same question is kept."""
    application = _application(db, user, application_id)
    row = db.get(ApplicationAnswer, (application.id, body.field_key))
    if row is None or row.user_id != user.id or not _rememberable(row, application.ats == "email"):
        return _status_refusal(422, "not_rememberable")
    if row.source is not AnswerSource.person or row.value != body.value:
        return _status_refusal(409, "not_answered")
    context, _ = _context(db, application)
    text = remember.check(_field(row), context, row.value)
    if text is None:
        return _status_refusal(422, "not_rememberable")
    now = deps.clock(request)
    question = row.label.strip()
    inserted = db.execute(
        pg.insert(AnswerBankEntry)
        .values(
            user_id=user.id,
            question_key=bank.question_key(question),
            question=question,
            answer=text,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "question_key"])
        .returning(AnswerBankEntry.question_key)
    ).scalar_one_or_none()
    outcome: Literal["saved", "kept"] = "saved" if inserted is not None else "kept"
    db.add(
        ApplicationEvent(
            application_id=application.id,
            user_id=user.id,
            at=now,
            kind="answer_remembered",
            data={"field_key": row.field_key, "outcome": outcome},
        )
    )
    db.commit()
    return RememberOut(outcome=outcome, question=question)


def _person(
    request: Request, user: User, db: Session, application_id: str, act: Any
) -> ApplicationOut | JSONResponse:
    wanted = _uuid_or_404(application_id)
    try:
        application = act(db, user.id, wanted, deps.clock(request))
        db.commit()
    except gate.NotFoundError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_APPLICATION) from None
    except gate.RefusedError as exc:
        db.rollback()
        return _refusal(exc.code)
    except jobs_service.JobBusyError:
        db.rollback()
        return _refusal("busy")
    return ApplicationOut.model_validate(application)


@router.post(
    "/api/applications/{application_id}/reject",
    response_model=ApplicationOut,
    responses={**_404, **_409},
)
def reject(
    application_id: str, request: Request, user: CurrentUser, db: Db
) -> ApplicationOut | JSONResponse:
    """Reject the form as filled: nothing is sent."""
    return _person(
        request,
        user,
        db,
        application_id,
        lambda db, uid, aid, now: gate.cancel(db, uid, aid, now, reject=True),
    )


@router.post(
    "/api/applications/{application_id}/cancel",
    response_model=ApplicationOut,
    responses={**_404, **_409},
)
def cancel(
    application_id: str, request: Request, user: CurrentUser, db: Db
) -> ApplicationOut | JSONResponse:
    """Stop an application before the click."""
    return _person(
        request,
        user,
        db,
        application_id,
        lambda db, uid, aid, now: gate.cancel(db, uid, aid, now, reject=False),
    )


@router.post(
    "/api/applications/{application_id}/submitted",
    response_model=ApplicationOut,
    responses={**_404, **_409},
)
def submitted(
    application_id: str, request: Request, user: CurrentUser, db: Db
) -> ApplicationOut | JSONResponse:
    """It was submitted: the person confirms an application the app could not confirm."""
    return _person(request, user, db, application_id, gate.confirm_submitted)


@router.post(
    "/api/applications/{application_id}/retry",
    response_model=ApplicationOut,
    responses={**_404, **_409},
)
def retry(
    application_id: str, request: Request, user: CurrentUser, db: Db
) -> ApplicationOut | JSONResponse:
    """Try again: back to the approval; nothing is sent without a new one."""
    return _person(request, user, db, application_id, gate.retry)
