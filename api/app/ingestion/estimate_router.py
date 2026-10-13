"""What a run would read and what it may cost, and the consent to sending it (decisions 5, 6
and 10 of tasks/ingestion/todo.md). Three routes, no run is started here:

- `POST /api/ingestion/runs/estimate {document_ids, reread_document_ids?}`: what the start
  dialog shows. The documents that would be read with their characters and images, the ones
  that cannot be read and why, the steps and how many are reused, the model of the caller's
  kind of credential, the token range `L` to `H`, the ceiling `C` where the run pauses, and for
  an API key the three costs in US dollars. Without a credential it still answers, and says
  what is missing.
- `GET /api/ingestion/consent`: the current text, its version, and whether the caller accepted it.
- `POST /api/ingestion/consent {version}`: records that the caller accepted the current text.

**Consent.** One row of `ingestion_consents` per user and version of the text. The current
consent of a user is the row of `CONSENT_VERSION`: a user without it has not consented to the
current text, whatever was accepted before. A new text is a new `CONSENT_VERSION`, and everyone
is asked again. Recording is one `INSERT ... ON CONFLICT DO NOTHING`, so two posts at once
leave one row with the first date. A version that is not the current one is refused (422): the
caller accepted a text this version no longer shows.

**For the tasks that call this module** (the contract is in the plan, block G16): `quote` is the
one function that turns a selection into a plan and an estimate, for the dialog here and for
the start of a run in G10, so the ceiling the user saw is the ceiling the run gets.
`has_consented` is what the start checks. `register_finished_provider` is where G10 registers
the query that finds the finished step identities: until then nothing is reused.

Every route takes the user from the session, never from the request. Another user's document
answers the same 404 as one that does not exist, and an id that is not a UUID too. Nothing of a
document is logged or put in an error but its id: no name, no text. A document's text is read
only when it is longer than one part, to cut it where the run will, and never leaves `quote`.
"""

import logging
import uuid
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Session

from app.agents import credentials
from app.agents.models import CredentialKind
from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.ingestion import documents, estimate, plan, pricing
from app.ingestion.models import IngestionConsent, IngestionDocument, IngestionImage, TextStatus

logger = logging.getLogger("app.ingestion")

# The text a user accepts, and its version. Change both together, never the text alone: the
# version is what a consent row records, and a new one asks every user again.
CONSENT_VERSION = 1
CONSENT_TEXT = (
    "When you start a run, the app sends the text and the page images of the documents you "
    "selected, and the current sections of your profile without your contact details, to "
    "Anthropic. They are sent through your own Claude subscription or Anthropic API key. "
    "Deleting a document here removes it from this app. It does not delete what Anthropic "
    "has already received."
)
CONSENT_REMINDER = "The selected documents are sent to Anthropic through your own credential."
CONSENT_OUTDATED = "The consent text has changed. Read the current text and accept it."

NOT_FOUND = "Document not found."
REREAD_NOT_SELECTED = "Only a selected document can be read again."
ID_MAX_CHARS = 64  # a UUID in any spelling is shorter
INT4_MAX = 2**31 - 1

REASONS: dict[str, str] = {
    "not_readable_yet": (
        "This version cannot read this kind of file yet. It is kept, and it will be read as "
        "soon as it can."
    ),
    "pending": "This document is still being read. Try again in a moment.",
    "failed": "This document could not be read.",
    "secret_unconfirmed": (
        "This document may hold a password or a key. Confirm it in the list of documents "
        "before a run reads it."
    ),
    plan.Refusal.awaiting_transcription.value: (
        "This document is made of page images, which this version cannot read yet."
    ),
    plan.Refusal.empty.value: "There is no text in this document.",
    plan.Refusal.over_steps.value: (
        f"A run has at most {plan.RUN_MAX_STEPS} steps and this document does not fit. "
        "Start another run for it."
    ),
}
# A document left out by the 60 step rule does not stop the others: the run ends partial.
NOT_BLOCKING = frozenset({plan.Refusal.over_steps.value})
BLOCKERS: dict[str, str] = {
    "no_credential": (
        "Add a Claude subscription or an Anthropic API key on the Account page to start a run."
    ),
    "nothing_to_read": "None of the selected documents can be read now.",
    "unreadable_documents": "Remove the documents that cannot be read from the selection.",
}
MODEL_REASONS: dict[CredentialKind, str] = {
    CredentialKind.claude_subscription: "Your credential is a Claude subscription, so the run uses",
    CredentialKind.anthropic_api_key: "Your credential is an Anthropic API key, so the run uses",
}

router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])
Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]

_401 = {401: {"model": ErrorOut, "description": "Not signed in"}}

# --- the finished steps of earlier runs (G10 registers the query)

FinishedProvider = Callable[
    [Session, uuid.UUID, Sequence[plan.Document], str], Collection[plan.Identity]
]


def _nothing_finished(
    db: Session, user_id: uuid.UUID, selected: Sequence[plan.Document], model: str
) -> Collection[plan.Identity]:
    return frozenset()


_finished_provider: FinishedProvider = _nothing_finished


def register_finished_provider(provider: FinishedProvider) -> None:
    """G10 gives the query that returns, for a user, the selected documents and a model, the
    identities of the steps that finished in earlier runs. Registered once, at import."""
    global _finished_provider
    _finished_provider = provider


# --- consent


def consent_of(db: Session, user_id: uuid.UUID) -> IngestionConsent | None:
    """The caller's consent to the current text, or None."""
    return db.get(IngestionConsent, (user_id, CONSENT_VERSION), populate_existing=True)


def has_consented(db: Session, user_id: uuid.UUID) -> bool:
    """What the start of a run checks: 409 `consent_required` while this is False (G10)."""
    return consent_of(db, user_id) is not None


def record_consent(db: Session, user_id: uuid.UUID) -> IngestionConsent:
    """Record the consent to the current text. Twice, or twice at once, is one row: the first
    date stays."""
    db.execute(
        pg.insert(IngestionConsent)
        .values(user_id=user_id, text_version=CONSENT_VERSION)
        .on_conflict_do_nothing(index_elements=["user_id", "text_version"])
    )
    db.flush()
    consent = consent_of(db, user_id)
    if consent is None:  # pragma: no cover - the row was inserted or already there
        raise RuntimeError("the consent was not recorded")
    return consent


# --- the quote of a selection


@dataclass(frozen=True)
class Unreadable:
    id: uuid.UUID
    name: str
    code: str


@dataclass(frozen=True)
class Read:
    id: uuid.UUID
    name: str
    characters: int
    images: int
    steps: int
    reused_steps: int


@dataclass(frozen=True)
class Quote:
    """A selection as a run would take it now. `plan` and `estimate` are what G10 starts from:
    `estimate.ceiling` is the run's ceiling, `plan.steps` its steps."""

    documents: tuple[Read, ...]
    unreadable: tuple[Unreadable, ...]
    credential_kind: CredentialKind | None
    model: str | None
    plan: plan.Plan
    estimate: estimate.RunEstimate
    costs: pricing.Costs | None

    @property
    def blockers(self) -> tuple[str, ...]:
        found = []
        if self.credential_kind is None:
            found.append("no_credential")
        if not self.plan.steps:
            found.append("nothing_to_read")
        if any(one.code not in NOT_BLOCKING for one in self.unreadable):
            found.append("unreadable_documents")
        return tuple(found)

    @property
    def can_start(self) -> bool:
        return not self.blockers


class NotFoundError(Exception):
    """An id of the selection is not a document of this user."""


class SelectionError(Exception):
    """The selection is refused as a whole, with a sentence for the user."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _ids(values: Sequence[str]) -> list[uuid.UUID]:
    """The ids as given, each once, in order. One that is not a UUID is not a document."""
    seen: dict[uuid.UUID, None] = {}
    for value in values:
        try:
            seen.setdefault(uuid.UUID(value), None)
        except ValueError:
            raise NotFoundError from None
    return list(seen)


def _why_not(document: IngestionDocument, images: int, transcribing: bool) -> str | None:
    """Why a run cannot read this document now, from its row alone, or None."""
    readable = documents.kind_of(document.mime) in documents.READABLE_KINDS
    status_ = document.text_status
    if status_ is TextStatus.failed:
        return "failed"
    if status_ is TextStatus.pending:
        return "pending" if readable else "not_readable_yet"
    if document.secret_warning and document.secret_confirmed_at is None:
        return "secret_unconfirmed"
    if status_ is TextStatus.awaiting_transcription and not (transcribing and images):
        return plan.Refusal.awaiting_transcription.value
    return None


def quote(
    db: Session,
    user_id: uuid.UUID,
    document_ids: Sequence[str],
    reread_document_ids: Sequence[str] = (),
) -> Quote:
    """The plan and the estimate of a selection of the user's documents (module docstring).
    Raises `NotFoundError` for an id that is not a document of this user and `SelectionError`
    for a selection over a limit of a run, with no document, or with a document to read again
    that is not selected."""
    ids = _ids(document_ids)
    reread = set(_ids(reread_document_ids))
    try:
        plan.check_count(len(ids))
    except plan.LimitError as exc:
        raise SelectionError(exc.code, exc.message) from None
    if not reread <= set(ids):
        raise SelectionError("reread", REREAD_NOT_SELECTED)

    doc = IngestionDocument
    characters = sa.func.coalesce(sa.func.char_length(doc.text), 0)
    found = {
        row[0].id: row
        for row in db.execute(
            sa.select(doc, characters).where(doc.user_id == user_id, doc.id.in_(ids))
        )
    }
    if len(found) != len(ids):
        raise NotFoundError
    positions: dict[uuid.UUID, list[int]] = {}
    for document_id, position in db.execute(
        sa.select(IngestionImage.document_id, IngestionImage.position).where(
            IngestionImage.user_id == user_id,
            IngestionImage.document_id.in_(ids),
            IngestionImage.text.is_(None),
        )
    ):
        positions.setdefault(document_id, []).append(position)

    transcribing = plan.TRANSCRIPTION
    names: dict[uuid.UUID, str] = {}
    unreadable: list[Unreadable] = []
    selected: list[plan.Document] = []
    for document_id in ids:
        row, size = found[document_id]
        names[document_id] = row.name
        images = tuple(sorted(positions.get(document_id, ())))
        code = _why_not(row, len(images), transcribing)
        if code is not None:
            unreadable.append(Unreadable(document_id, row.name, code))
            continue
        selected.append(plan.Document(document_id, row.sha256.hex(), int(size), images))

    credential = credentials.get(db, user_id)
    kind = credential.kind if credential is not None else None
    model = plan.model_for(kind)
    planned = plan.Plan(steps=(), refused=())
    if selected:
        try:
            plan.check_limits(selected)
        except plan.LimitError as exc:
            raise SelectionError(exc.code, exc.message) from None
        selected = _with_cuts(db, user_id, selected)
        finished = _finished_provider(db, user_id, selected, model)
        planned = plan.build(selected, model=model, finished=finished, reread=reread)
    unreadable.extend(
        Unreadable(document_id, names[document_id], refusal.value)
        for document_id, refusal in planned.refused
    )
    order = {document_id: at for at, document_id in enumerate(ids)}
    unreadable.sort(key=lambda one: order[one.id])

    by_id = {document.id: document for document in selected}
    read = []
    for document_id in planned.planned_documents:
        steps = [step for step in planned.steps if step.document_id == document_id]
        read.append(
            Read(
                id=document_id,
                name=names[document_id],
                characters=by_id[document_id].characters,
                images=len(by_id[document_id].images),
                steps=len(steps),
                reused_steps=sum(step.reused for step in steps),
            )
        )
    figures = estimate.run(planned.steps)
    return Quote(
        documents=tuple(read),
        unreadable=tuple(unreadable),
        credential_kind=kind,
        model=model if kind is not None else None,
        plan=planned,
        estimate=figures,
        costs=pricing.costs(figures, model) if pricing.priced(kind) else None,
    )


def _with_cuts(
    db: Session, user_id: uuid.UUID, selected: list[plan.Document]
) -> list[plan.Document]:
    """The documents with the parts of their text, cut where the run will cut it. Only a text
    longer than one part is loaded, one document at a time, and only its offsets are kept."""
    cut: list[plan.Document] = []
    for document in selected:
        if document.images:
            parts = None  # its text is not all there yet: counted in advance, in equal parts
        elif document.characters <= plan.PART_MAX_CHARS:
            parts = ((0, document.characters),) if document.characters else ()
        else:
            text = db.execute(
                sa.select(IngestionDocument.text).where(
                    IngestionDocument.id == document.id, IngestionDocument.user_id == user_id
                )
            ).scalar_one()
            parts = plan.cut(text or "")
        cut.append(
            plan.Document(document.id, document.sha256, document.characters, document.images, parts)
        )
    return cut


# --- the routes


class EstimateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Longer than a run takes, so that 31 documents get the sentence about the limit of a run;
    # never longer than a user has documents.
    document_ids: list[Annotated[str, Field(max_length=ID_MAX_CHARS)]] = Field(
        max_length=documents.USER_MAX_DOCUMENTS
    )
    reread_document_ids: list[Annotated[str, Field(max_length=ID_MAX_CHARS)]] = Field(
        default_factory=list, max_length=documents.USER_MAX_DOCUMENTS
    )


class ReadOut(BaseModel):
    id: uuid.UUID
    name: str
    characters: int
    images: int
    steps: int
    reused_steps: int


class UnreadableOut(BaseModel):
    id: uuid.UUID
    name: str
    code: str
    reason: str


class TokensOut(BaseModel):
    """Estimates in tokens: `low` to `high` is the expected range, `ceiling` is where the run
    pauses and asks before going on (twice `high`)."""

    low: int
    high: int
    ceiling: int


class CostOut(BaseModel):
    """Estimates in a currency, as text with two decimals, for an API key only."""

    currency: str
    low: str
    high: str
    ceiling: str
    prices_as_of: str
    note: str


class LimitsOut(BaseModel):
    documents: int
    characters: int
    images: int
    steps: int
    step_minutes: int
    run_minutes: int


class BlockerOut(BaseModel):
    code: str
    reason: str


class ConsentStateOut(BaseModel):
    version: int
    accepted: bool


class EstimateOut(BaseModel):
    documents: list[ReadOut]
    unreadable: list[UnreadableOut]
    characters: int
    images: int
    steps: int
    reused_steps: int
    credential_kind: CredentialKind | None
    model: str | None
    model_reason: str
    tokens: TokensOut
    cost: CostOut | None
    cost_note: str | None
    limits: LimitsOut
    consent: ConsentStateOut
    can_start: bool
    blockers: list[BlockerOut]


class ConsentOut(BaseModel):
    version: int
    text: str
    reminder: str
    accepted: bool
    accepted_at: datetime | None


class ConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1, le=INT4_MAX)


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _model_reason(found: Quote) -> str:
    if found.credential_kind is None or found.model is None:
        return BLOCKERS["no_credential"]
    return f"{MODEL_REASONS[found.credential_kind]} {found.model}."


def _cost(found: Quote) -> CostOut | None:
    if found.costs is None:
        return None
    return CostOut(
        currency=pricing.CURRENCY,
        low=str(found.costs.low),
        high=str(found.costs.high),
        ceiling=str(found.costs.ceiling),
        prices_as_of=pricing.PRICES_AS_OF.isoformat(),
        note=pricing.ESTIMATE_NOTE,
    )


@router.post(
    "/runs/estimate",
    response_model=EstimateOut,
    responses={
        **_401,
        404: {"model": ErrorOut, "description": "No such document (or not yours)"},
        422: {"model": ErrorOut, "description": "No document, or a selection over a limit"},
    },
)
def estimate_run(body: EstimateIn, user: CurrentUser, db: Db) -> EstimateOut:
    try:
        found = quote(db, user.id, body.document_ids, body.reread_document_ids)
    except NotFoundError:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
    except SelectionError as exc:
        logger.info("ingestion_estimate_refused", extra={"user_id": str(user.id), "rule": exc.code})
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.message) from None
    figures = found.estimate
    logger.info(
        "ingestion_estimate",
        extra={
            "user_id": str(user.id),
            "documents": len(found.documents),
            "unreadable": len(found.unreadable),
            "steps": figures.steps,
            "reused_steps": figures.reused,
            "tokens_low": figures.low,
            "tokens_high": figures.high,
            "tokens_ceiling": figures.ceiling,
            "credential_kind": found.credential_kind.value if found.credential_kind else None,
        },
    )
    subscription = found.credential_kind is CredentialKind.claude_subscription
    return EstimateOut(
        documents=[ReadOut(**vars(one)) for one in found.documents],
        unreadable=[
            UnreadableOut(id=one.id, name=one.name, code=one.code, reason=REASONS[one.code])
            for one in found.unreadable
        ],
        characters=sum(one.characters for one in found.documents),
        images=sum(one.images for one in found.documents),
        steps=figures.steps,
        reused_steps=figures.reused,
        credential_kind=found.credential_kind,
        model=found.model,
        model_reason=_model_reason(found),
        tokens=TokensOut(low=figures.low, high=figures.high, ceiling=figures.ceiling),
        cost=_cost(found),
        cost_note=pricing.QUOTA_NOTE if subscription else None,
        limits=LimitsOut(
            documents=plan.RUN_MAX_DOCUMENTS,
            characters=plan.RUN_MAX_CHARS,
            images=plan.RUN_MAX_IMAGES,
            steps=plan.RUN_MAX_STEPS,
            step_minutes=plan.STEP_MINUTES,
            run_minutes=plan.RUN_MINUTES,
        ),
        consent=ConsentStateOut(version=CONSENT_VERSION, accepted=has_consented(db, user.id)),
        can_start=found.can_start,
        blockers=[BlockerOut(code=code, reason=BLOCKERS[code]) for code in found.blockers],
    )


def _consent_out(consent: IngestionConsent | None) -> ConsentOut:
    return ConsentOut(
        version=CONSENT_VERSION,
        text=CONSENT_TEXT,
        reminder=CONSENT_REMINDER,
        accepted=consent is not None,
        accepted_at=consent.accepted_at if consent is not None else None,
    )


@router.get("/consent", response_model=ConsentOut, responses=_401)
def read_consent(user: CurrentUser, db: Db) -> ConsentOut:
    return _consent_out(consent_of(db, user.id))


@router.post(
    "/consent",
    response_model=ConsentOut,
    responses={**_401, 422: {"model": ErrorOut, "description": "Not the current version"}},
)
def give_consent(body: ConsentIn, user: CurrentUser, db: Db) -> ConsentOut:
    if body.version != CONSENT_VERSION:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, CONSENT_OUTDATED)
    consent = record_consent(db, user.id)
    db.commit()
    logger.info(
        "ingestion_consent", extra={"user_id": str(user.id), "text_version": CONSENT_VERSION}
    )
    return _consent_out(consent)
