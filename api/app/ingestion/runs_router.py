"""The routes of an ingestion run (ingestion G10): start, read, cancel, resume, continue.

    POST /api/ingestion/runs {document_ids, reread_document_ids?, quote_id}
    GET  /api/ingestion/runs                 the caller's latest runs, newest first
    GET  /api/ingestion/runs/{id}
    POST /api/ingestion/runs/{id}/cancel
    POST /api/ingestion/runs/{id}/resume     a run paused for anything but its ceiling
    POST /api/ingestion/runs/{id}/continue {adds}   a run paused on its token ceiling

The start checks, in this order and in one transaction: the consent (409 `consent_required`),
the selection through `estimate_router.quote` (404, 422), the echo of the estimate the dialog
showed (409 `estimate_changed` with the estimate of now), what blocks the start (409
`no_credential`, 422 `unreadable_documents` naming them, 422 `nothing_to_read`), the one active
run of a user (409 `active_run` with its id, from the partial unique index when two starts
race). Then it creates the run and its steps, commits, and moves the chain once.

Every route reads and writes the caller's runs only: another user's run id answers exactly like
an unknown one (404). No answer holds a document's text or an item's value: names, ids, counts,
statuses and the runner's own messages.

Importing this module is what makes the api know the extraction agent (`agents`), the provider
of finished steps for the estimate (`reuse`) and the delete guard (`delete_guard`).
"""

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from functools import partial
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy.orm import Session, sessionmaker

from app.agents.models import AgentRun, CredentialKind
from app.agents.models import RunStatus as AgentStatus
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.ingestion import (
    delete_guard,
    engine,
    estimate_router,
    merge,
    plan,
    reuse,
)
from app.ingestion.models import IngestionDocument
from app.ingestion.runs_models import (
    IngestionItem,
    IngestionRun,
    IngestionRunStep,
    RunStatus,
    StepStatus,
    of_stored_text,
)

logger = logging.getLogger("app.ingestion")

router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])
CurrentUser = Annotated[User, Depends(deps.current_user)]

NOT_FOUND = "Run not found."
ENDED = "This run has ended."
NOT_RESUMABLE = "Only a paused run can be resumed. A run paused on its token ceiling is continued."
NOT_CONTINUABLE = "Only a run paused on its token ceiling can be continued."
CONTINUE_CHANGED = (
    "What Continue adds has changed since this page was loaded. Nothing was added. Read the "
    "new figure, then continue again."
)
ADDS_MAX = 2**53 - 1  # the largest whole number every JSON client holds exactly
CONSENT_REQUIRED = "Read and accept the consent text before a run sends documents to Anthropic."
ACTIVE_RUN = "A run is already in progress. Wait for it, or cancel it."
OVERSHOOT = (
    "The run pauses at its ceiling between two requests: it can pass it by the request in "
    f"flight, and by at most {engine.OVERSHOOT_REQUESTS} requests of the step being read."
)
LIST_MAX = 20
_401 = {401: {"model": ErrorOut, "description": "Not signed in"}}
_404 = {404: {"model": ErrorOut, "description": "No such run (or not yours)"}}
# What the guard and the reuse query register at import (kept so the imports are not dropped).
REGISTERED = (delete_guard.read_by_a_run, reuse.finished)


class RunIn(estimate_router.EstimateIn):
    # The echo of the estimate the dialog showed (`estimate_router.quote_id_of`): 64
    # hexadecimal characters, so anything else, a lone surrogate included, is a 422 here and
    # never reaches the comparison.
    quote_id: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class StepOut(BaseModel):
    id: uuid.UUID
    position: int
    kind: plan.StepKind
    document_id: uuid.UUID
    document_name: str | None  # None once the document is deleted
    part: int | None
    status: StepStatus
    attempt: int
    items: int
    refused: int
    two_digit_year: int
    agent_run_id: uuid.UUID | None
    tokens: int | None = Field(
        description="Input and output tokens of the step's last agent run only. A step that ran "
        "twice counts both attempts in the run's `tokens.spent`, so the steps do not add up to it."
    )
    cost_usd: str | None = Field(
        description="Only for a step that ran on an Anthropic API key, when the cost is known."
    )
    error: str | None = Field(description="The runner's sentence for a step that failed.")


class CountsOut(BaseModel):
    steps: int
    planned: int
    running: int
    done: int
    reused: int
    failed: int
    cancelled: int
    items: int = Field(description="Stored items of this run's documents, as stored now.")
    refused: int
    two_digit_year: int
    proposals: int


class CeilingEntryOut(BaseModel):
    at: datetime
    spent: int
    from_: int = Field(alias="from", serialization_alias="from")
    to: int


class ContinueIn(BaseModel):
    # No other field: the body is a few bytes, so its schema is bounded and stays inside the
    # default body class (body_limit.py; the guard in tests/identity/test_body_limit_api.py).
    model_config = ConfigDict(extra="forbid")

    # The echo of `tokens.continue_adds` as the page showed it: the click agrees to that
    # figure and to no other. A strict whole number, so a string, a float or a boolean is a
    # 422 here and never reaches the comparison.
    adds: StrictInt = Field(ge=1, le=ADDS_MAX)


class TokensOut(BaseModel):
    spent: int = Field(description="As recorded, the running step included.")
    ceiling: int
    low: int
    high: int
    history: list[CeilingEntryOut]
    continue_adds: int | None = Field(
        description="For a run paused on `token_ceiling`: what one Continue adds to the ceiling "
        "now. One `high`, or more when the step that runs next needs it: a step the ceiling "
        "interrupted starts again from its first request, on at least twice the budget that was "
        "not enough. The click sends this figure back as `adds`. Zero when no step would run "
        "(what was left to read was deleted, or the run is out of time): no raise is asked, "
        "the run closes by itself within a minute, and a Continue sent meanwhile adds nothing, "
        "closes it at once and is answered 409 `continue_changed` with `continue_adds` 0."
    )
    overshoot_requests: int
    overshoot: str


class RunOut(BaseModel):
    id: uuid.UUID
    status: RunStatus
    partial: bool
    paused_reason: str | None = Field(
        description="`token_ceiling`, or the sentence of the failure that paused the run."
    )
    closed_reason: str | None = Field(
        description="The sentence of a run the engine closed `failed` because it could not move "
        "it. Null for every other end."
    )
    waiting: str | None = Field(description="`slots` while the user has four agent runs active.")
    cancel_requested: bool
    document_ids: list[uuid.UUID]
    unread_document_ids: list[uuid.UUID]
    current_agent_run_id: uuid.UUID | None
    tokens: TokensOut
    counts: CountsOut
    steps: list[StepOut]
    created_at: datetime
    deadline_at: datetime
    finished_at: datetime | None


class RunLine(BaseModel):
    id: uuid.UUID
    status: RunStatus
    partial: bool
    created_at: datetime
    finished_at: datetime | None


class RunList(BaseModel):
    runs: list[RunLine]
    active_run_id: uuid.UUID | None


class ContinueChangedOut(BaseModel):
    """409 of a Continue whose `adds` is not what one Continue adds now: nothing was written."""

    detail: str
    code: str = "continue_changed"
    continue_adds: int = Field(description="What one Continue adds now: show it, ask again.")
    run: RunOut


class _RefusedError(Exception):
    """The start is refused: raised out of the transaction, which rolls back."""

    def __init__(self, code: int, body: dict[str, Any]) -> None:
        super().__init__(body.get("code", ""))
        self.status = code
        self.body = body


def sessions_of(request: Request) -> sessionmaker[Session]:
    found: sessionmaker[Session] = request.app.state.sessionmaker
    return found


def _id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from None


# --- read ---------------------------------------------------------------------------------------


def _cost(agent: Any) -> str | None:
    if agent is None or agent.cost_usd is None:
        return None
    if agent.credential_kind is not CredentialKind.anthropic_api_key:
        return None
    return str(Decimal(agent.cost_usd).quantize(Decimal("0.0001")))


def run_out(db: Session, run: IngestionRun, now: datetime) -> RunOut:
    step = IngestionRunStep
    rows = db.execute(
        sa.select(
            step,
            IngestionDocument.name,
            AgentRun.status,
            AgentRun.error,
            AgentRun.input_tokens,
            AgentRun.output_tokens,
            AgentRun.cost_usd,
            AgentRun.credential_kind,
        )
        .outerjoin(
            IngestionDocument,
            sa.and_(
                IngestionDocument.id == step.document_id, IngestionDocument.user_id == run.user_id
            ),
        )
        .outerjoin(AgentRun, AgentRun.id == step.agent_run_id)
        .where(step.ingestion_run_id == run.id)
        .order_by(step.position)
    ).all()
    steps: list[StepOut] = []
    spent = run.tokens_spent
    current: uuid.UUID | None = None
    by_status = dict.fromkeys((one.value for one in StepStatus), 0)
    read: set[uuid.UUID] = set()
    extracted: set[uuid.UUID] = set()
    cut_short: set[uuid.UUID] = set()  # an extraction that failed or was cancelled
    for row in rows:
        one = row[0]
        has_agent = row.status is not None
        tokens = int(row.input_tokens or 0) + int(row.output_tokens or 0) if has_agent else None
        by_status[one.status.value] += 1
        if one.status in (StepStatus.done, StepStatus.reused):
            read.add(one.document_id)
            if one.kind is plan.StepKind.extract:
                extracted.add(one.document_id)
        elif one.kind is plan.StepKind.extract and one.status in (
            StepStatus.failed,
            StepStatus.cancelled,
        ):
            cut_short.add(one.document_id)
        if one.status is StepStatus.running:
            current = one.agent_run_id
            spent += tokens or 0  # not yet added to the run: its end is not recorded
        steps.append(
            StepOut(
                id=one.id,
                position=one.position,
                kind=one.kind,
                document_id=one.document_id,
                document_name=row.name,
                part=one.part,
                status=one.status,
                attempt=one.attempt,
                items=one.items_count,
                refused=one.refused_count,
                two_digit_year=one.two_digit_year_count,
                agent_run_id=one.agent_run_id,
                tokens=tokens,
                cost_usd=_cost(row) if has_agent else None,
                error=row.error if has_agent and row.status is AgentStatus.failed else None,
            )
        )
    # A transcribed document is not read until its text is: its transcription done is not enough.
    read -= cut_short - extracted
    sources = reuse.source_steps(db, run.id)
    items = 0
    if sources:
        items = int(
            db.scalar(
                sa.select(sa.func.count())
                .select_from(IngestionItem)
                .where(IngestionItem.step_id.in_(sources), of_stored_text())
            )
            or 0
        )
    return RunOut(
        id=run.id,
        status=run.status,
        partial=run.partial,
        paused_reason=run.paused_reason if run.status is RunStatus.paused else None,
        closed_reason=run.closed_reason,
        waiting=run.waiting,
        cancel_requested=run.cancel_requested_at is not None,
        document_ids=list(run.document_ids),
        unread_document_ids=[one for one in run.document_ids if one not in read],
        current_agent_run_id=current,
        tokens=TokensOut(
            spent=spent,
            ceiling=run.token_ceiling,
            low=run.estimate_low,
            high=run.estimate_high,
            history=[CeilingEntryOut.model_validate(entry) for entry in run.ceiling_history],
            continue_adds=engine.continue_adds(db, run, [row[0] for row in rows], now)
            if run.status is RunStatus.paused and run.paused_reason == engine.TOKEN_CEILING
            else None,
            overshoot_requests=engine.OVERSHOOT_REQUESTS,
            overshoot=OVERSHOOT,
        ),
        counts=CountsOut(
            steps=len(steps),
            items=items,
            refused=sum(one.refused for one in steps),
            two_digit_year=sum(one.two_digit_year for one in steps),
            proposals=merge.count_of_run(db, run.user_id, run.id),
            **by_status,
        ),
        steps=steps,
        created_at=run.created_at,
        deadline_at=run.deadline_at,
        finished_at=run.finished_at,
    )


def _read(
    sessions: sessionmaker[Session], user_id: uuid.UUID, run_id: uuid.UUID, now: datetime
) -> RunOut | None:
    with sessions() as db:
        run = db.execute(
            sa.select(IngestionRun).where(
                IngestionRun.id == run_id, IngestionRun.user_id == user_id
            )
        ).scalar_one_or_none()
        return run_out(db, run, now) if run is not None else None


async def _answer(request: Request, user_id: uuid.UUID, run_id: uuid.UUID) -> RunOut:
    found = await run_in_threadpool(
        _read, sessions_of(request), user_id, run_id, deps.clock(request)
    )
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return found


async def _drive(request: Request, run_id: uuid.UUID) -> None:
    await engine.drive(
        sessions_of(request),
        engine.queue_enqueue(request.app.state.queue),
        run_id,
        lambda: deps.clock(request),
        request.app.state.settings,
    )


# --- start --------------------------------------------------------------------------------------


def _changed(db: Session, user_id: uuid.UUID, quote: estimate_router.Quote) -> _RefusedError:
    body = estimate_router.EstimateChangedOut(estimate=estimate_router.answer(db, user_id, quote))
    return _RefusedError(status.HTTP_409_CONFLICT, body.model_dump(mode="json"))


def _create(db: Session, user_id: uuid.UUID, body: RunIn, now: datetime) -> uuid.UUID:
    if not estimate_router.has_consented(db, user_id):
        raise _RefusedError(
            status.HTTP_409_CONFLICT,
            {
                "detail": CONSENT_REQUIRED,
                "code": "consent_required",
                "consent_version": estimate_router.CONSENT_VERSION,
            },
        )
    try:
        quote = estimate_router.quote(db, user_id, body.document_ids, body.reread_document_ids)
    except estimate_router.NotFoundError:
        raise _RefusedError(
            status.HTTP_404_NOT_FOUND, {"detail": estimate_router.NOT_FOUND}
        ) from None
    except estimate_router.SelectionError as exc:
        raise _RefusedError(
            status.HTTP_422_UNPROCESSABLE_CONTENT, {"detail": exc.message, "code": exc.code}
        ) from None
    try:
        estimate_router.check_echo(quote, body.quote_id)
    except estimate_router.EstimateChangedError as exc:
        raise _changed(db, user_id, exc.quote) from None
    blockers = quote.blockers
    if "no_credential" in blockers:
        raise _RefusedError(
            status.HTTP_409_CONFLICT,
            {"detail": estimate_router.BLOCKERS["no_credential"], "code": "no_credential"},
        )
    if "unreadable_documents" in blockers:
        raise _RefusedError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {
                "detail": estimate_router.BLOCKERS["unreadable_documents"],
                "code": "unreadable_documents",
                "documents": [
                    {
                        "id": str(one.id),
                        "name": one.name,
                        "code": one.code,
                        "reason": estimate_router.REASONS.get(one.code, ""),
                    }
                    for one in quote.unreadable
                    if one.code not in estimate_router.NOT_BLOCKING
                ],
            },
        )
    if blockers:
        raise _RefusedError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"detail": estimate_router.BLOCKERS[blockers[0]], "code": blockers[0]},
        )
    try:
        return engine.create(db, user_id, quote, now).id
    except engine.ActiveRunError as exc:
        raise _RefusedError(
            status.HTTP_409_CONFLICT,
            {
                "detail": ACTIVE_RUN,
                "code": "active_run",
                "run_id": str(exc.run_id) if exc.run_id is not None else None,
            },
        ) from None


def _start(
    sessions: sessionmaker[Session], user_id: uuid.UUID, body: RunIn, now: datetime
) -> uuid.UUID:
    try:
        with sessions() as db, db.begin():
            return _create(db, user_id, body, now)
    except engine.TextChangedError:
        # A text changed between the quote and the steps: nothing was kept, and the estimate
        # of now is the answer, as for any other change.
        with sessions() as db:
            fresh = estimate_router.quote(db, user_id, body.document_ids, body.reread_document_ids)
            raise _changed(db, user_id, fresh) from None


@router.post(
    "/runs",
    response_model=RunOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        **_401,
        404: {"model": ErrorOut, "description": "No such document (or not yours)"},
        409: {
            "description": (
                "`consent_required`, `estimate_changed` (with the estimate of now), "
                "`no_credential` or `active_run` (with the id of the run in progress)"
            )
        },
        422: {
            "description": "No `quote_id`, documents that cannot be read, a selection over a limit"
        },
    },
)
async def start_run(body: RunIn, user: CurrentUser, request: Request) -> Any:
    try:
        run_id = await run_in_threadpool(
            _start, sessions_of(request), user.id, body, deps.clock(request)
        )
    except _RefusedError as refused:
        logger.info(
            "ingestion_run_refused",
            extra={"user_id": str(user.id), "rule": refused.body.get("code", "not_found")},
        )
        return JSONResponse(status_code=refused.status, content=refused.body)
    await _drive(request, run_id)
    return await _answer(request, user.id, run_id)


def _list(sessions: sessionmaker[Session], user_id: uuid.UUID) -> RunList:
    with sessions() as db:
        rows = db.scalars(
            sa.select(IngestionRun)
            .where(IngestionRun.user_id == user_id)
            .order_by(IngestionRun.created_at.desc())
            .limit(LIST_MAX)
        ).all()
        return RunList(
            runs=[
                RunLine(
                    id=row.id,
                    status=row.status,
                    partial=row.partial,
                    created_at=row.created_at,
                    finished_at=row.finished_at,
                )
                for row in rows
            ],
            active_run_id=engine.active_run_id(db, user_id),
        )


@router.get("/runs", response_model=RunList, responses=_401)
async def list_runs(user: CurrentUser, request: Request) -> RunList:
    return await run_in_threadpool(_list, sessions_of(request), user.id)


@router.get("/runs/{run_id}", response_model=RunOut, responses={**_401, **_404})
async def read_run(run_id: str, user: CurrentUser, request: Request) -> RunOut:
    return await _answer(request, user.id, _id(run_id))


# --- cancel, resume, continue -------------------------------------------------------------------


def _act(
    sessions: sessionmaker[Session],
    action: Any,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    now: datetime,
) -> RunStatus | None:
    """One of the engine's actions in its own transaction. None for a run that is not the
    caller's; the status the action found otherwise."""
    with sessions() as db, db.begin():
        before = db.scalar(
            sa.select(IngestionRun.status).where(
                IngestionRun.id == run_id, IngestionRun.user_id == user_id
            )
        )
        if before is None or action(db, user_id, run_id, now) is None:
            return None
        return before


async def _action(request: Request, user: User, run_id: str, action: Any, refusal: str) -> RunOut:
    found = _id(run_id)
    try:
        before = await run_in_threadpool(
            _act, sessions_of(request), action, user.id, found, deps.clock(request)
        )
    except engine.WrongStateError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal) from None
    if before is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    if before not in engine.ACTIVE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ENDED)
    await _drive(request, found)
    return await _answer(request, user.id, found)


_ACTION = {**_401, **_404, 409: {"model": ErrorOut, "description": "Not in a state for this"}}


@router.post("/runs/{run_id}/cancel", response_model=RunOut, responses=_ACTION)
async def cancel_run(run_id: str, user: CurrentUser, request: Request) -> RunOut:
    return await _action(request, user, run_id, engine.request_cancel, ENDED)


@router.post("/runs/{run_id}/resume", response_model=RunOut, responses=_ACTION)
async def resume_run(run_id: str, user: CurrentUser, request: Request) -> RunOut:
    return await _action(request, user, run_id, engine.resume, NOT_RESUMABLE)


@router.post(
    "/runs/{run_id}/continue",
    response_model=RunOut,
    responses={
        **_ACTION,
        409: {
            "description": (
                "Not paused on its token ceiling, or `continue_changed`: `adds` is not what one "
                "Continue adds now (the answer holds the figure of now and the run; nothing "
                "was added)"
            )
        },
        422: {"description": "No `adds`, or not a whole number in range"},
    },
)
async def continue_run(run_id: str, body: ContinueIn, user: CurrentUser, request: Request) -> Any:
    action = partial(engine.continue_, adds=body.adds)
    try:
        return await _action(request, user, run_id, action, NOT_CONTINUABLE)
    except engine.ContinueChangedError as changed:
        # The transaction rolled back: the ceiling, its history and the run's state are as
        # they were. The page shows the new figure and asks again. A figure now zero means
        # nothing would run: the run closes here, as it would at the next minute.
        if changed.adds == 0:
            await _drive(request, _id(run_id))
        logger.info(
            "ingestion_continue_refused",
            extra={"user_id": str(user.id), "rule": "continue_changed"},
        )
        answer = ContinueChangedOut(
            detail=CONTINUE_CHANGED,
            continue_adds=changed.adds,
            run=await _answer(request, user.id, _id(run_id)),
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=answer.model_dump(mode="json", by_alias=True),
        )
