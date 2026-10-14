"""The review of a run's proposals (G12, G13): what the page reads, the undo of an accept, and
the two bulk decisions.

- GET /api/ingestion/runs/{id}/proposals: the profile items the merge wrote for this run,
  grouped by section in the profile's order, each with its value, what the profile holds at its
  key now and what it would hold once accepted, its flags, its citations and the other values
  of a conflict. Nothing is stored about staleness: it is computed on every read against the
  profile as it is now (`item_proposals.review`), and for the derived years also against the
  employer items they were counted from.
- POST /api/ingestion/proposals/{id}/undo: takes back an accepted item, as one revision.
- POST /api/ingestion/runs/{id}/accept-section {section, proposal_ids[]}: "Accept all in this
  section" (`review.accept_section`). The ids that are pending items of this run and section
  and that a bulk accept may take are applied in one profile save, so one revision. It answers
  what it accepted, what it left out with the reason of each (`locked`, `flagged`, `removal`,
  `stale`, `not_pending`, `not_found`, `other_section`) and the count per reason. 409 when one
  item stops the section: nothing is applied and `proposal_id` names it (`reason` is `invalid`
  or `profile_conflict`), or when the profile is held by another request (`busy`).
- POST /api/ingestion/runs/{id}/reject-section {section, proposal_ids[]}: "Reject the rest of
  this section", with no exclusion. The profile is not touched.

Accept and reject of one item are the routes of `/api/proposals` (G5), unchanged. Every id of
another user answers 404 with the sentence of an unknown one, and in a bulk call an id of
another user or of another run is left out as `not_found`, like one that never existed. Every
answer is `no-store` (the middleware of `routers.py`).

What a document wrote reaches this answer as data only: a value, and an excerpt cut to
`EXCERPT_MAX` characters. The page renders both as text.
"""

import uuid
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal, Self

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy.orm import Session

from app.agents import proposals
from app.agents.models import Proposal, ProposalStatus
from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.ingestion import item_proposals, items, review
from app.ingestion.citations import FieldType
from app.ingestion.models import IngestionDocument
from app.ingestion.runs_models import IngestionRun, RunStatus
from app.profile import service as profile_service

router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])
Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]

RUN_NOT_FOUND = "Run not found."
PROPOSAL_NOT_FOUND = "Proposal not found."
EXCERPT_MAX = 400  # characters of an excerpt sent to the page
ITEMS_MAX = 2_500  # above the 2,000 items a run may store
NO_STORE = {"Cache-Control": "no-store"}
UNDO_REFUSED = {
    item_proposals.NOT_ACCEPTED: "Only an accepted proposal can be taken back.",
    item_proposals.CHANGED_SINCE: (
        "The profile changed here since this was accepted, so it was left as it is."
    ),
    item_proposals.NOT_UNDOABLE: (
        "This one cannot be taken back exactly. Edit the profile by hand instead."
    ),
}
SECTION_STOPPED = {
    item_proposals.INVALID: (
        "Nothing was accepted. One proposal no longer fits your profile and was set aside: "
        "{reason}. Accept the others again."
    ),
    item_proposals.PROFILE_CONFLICT: (
        "Nothing was accepted. Your profile cannot take one of these as it is: {reason}. "
        "Edit or reject that one, then accept the others again."
    ),
}
BUSY = "Your profile is being changed from somewhere else. Try again in a moment."
UNKNOWN_SECTION = "This is not a section of the profile."
STALE_PROFILE = "profile_changed"
STALE_DEPENDS = "depends_on_changed"

_401 = {401: {"model": ErrorOut, "description": "Not signed in"}}
_404 = {404: {"model": ErrorOut, "description": "No such run or proposal (or not yours)"}}


class DocumentRef(BaseModel):
    """A document of the run. `name` is null and `deleted` true once the document is gone."""

    id: str
    name: str | None
    deleted: bool


class CitationOut(BaseModel):
    """Where a value comes from. `document` is null when the document was deleted since: the
    excerpt is then all that is left of the source."""

    document_id: str
    document: DocumentRef | None
    excerpt: str
    excerpt_cut: bool
    start: int
    end: int
    transcribed: bool


class OtherValueOut(BaseModel):
    value: Any
    citations: list[CitationOut]


class ItemOut(BaseModel):
    """One proposal. `before` and `after` are what the profile holds at the key now and what it
    would hold once accepted, for a pending item. `stale`: the profile changed at the key since
    the proposal (`profile_changed`), or an item the derived years were counted from was
    rejected or edited (`depends_on_changed`). `bulk`: why "accept all" leaves it out, or null."""

    id: str
    status: ProposalStatus
    section: str
    key: str
    slot: str
    action: str
    value: Any
    before: Any
    after: Any
    noop: bool
    stale: bool
    stale_reason: Literal["profile_changed", "depends_on_changed"] | None
    locked: bool
    bulk: str | None
    problem: str | None
    flags: list[str]
    free_text: bool
    citations: list[CitationOut]
    conflict: list[OtherValueOut]
    depends_on: list[str]
    base_revision: int | None
    applied_revision: int | None


class CountsOut(BaseModel):
    total: int = 0
    pending: int = 0
    accepted: int = 0
    rejected: int = 0
    superseded: int = 0
    invalid: int = 0
    undone: int = 0


class SectionOut(BaseModel):
    section: str
    counts: CountsOut
    items: list[ItemOut]


class ReviewOut(BaseModel):
    run_id: str
    status: RunStatus
    partial: bool
    profile_revision: int
    counts: CountsOut
    documents: list[DocumentRef]
    sections: list[SectionOut]


class UndoOut(BaseModel):
    id: str
    status: ProposalStatus
    profile_revision: int


class UndoConflictOut(BaseModel):
    detail: str
    reason: Literal["not_accepted", "changed_since", "not_undoable"]
    status: ProposalStatus


# The canonical text of a uuid, as the list gives it: a bound the body's schema can state.
ProposalId = Annotated[
    str,
    StringConstraints(
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    ),
]


class SectionIn(BaseModel):
    """The body of the two bulk routes: the section, and the ids the user was looking at (the
    page sends the pending ones it shows, so nothing is decided that was not on the screen)."""

    model_config = ConfigDict(extra="forbid")

    section: Annotated[str, Field(max_length=32)]
    proposal_ids: Annotated[list[ProposalId], Field(max_length=review.IDS_MAX)]

    @field_validator("section")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in items.SLOTS:
            raise ValueError(UNKNOWN_SECTION)
        return value

    @property
    def ids(self) -> list[uuid.UUID]:
        return [uuid.UUID(one) for one in dict.fromkeys(self.proposal_ids)]


class SkippedOut(BaseModel):
    id: str
    reason: str


class SectionDecidedOut(BaseModel):
    """What a bulk call did. `decided`: the ids accepted (or rejected). `skipped`: the ids left
    out, each with its reason, and `left_out` their count per reason. `revision`: the one
    profile revision an accept made, null when it changed nothing and for a reject."""

    run_id: str
    section: str
    decided: list[str]
    skipped: list[SkippedOut]
    left_out: dict[str, int]
    revision: int | None
    profile_revision: int

    @classmethod
    def of(cls, run: IngestionRun, section: str, done: review.Decided, revision: int) -> Self:
        return cls(
            run_id=str(run.id),
            section=section,
            decided=[str(one) for one in done.done],
            skipped=[
                SkippedOut(id=str(one), reason=reason) for one, reason in done.skipped.items()
            ],
            left_out=done.left_out,
            revision=done.revision,
            profile_revision=revision,
        )


class SectionConflictOut(BaseModel):
    """A bulk accept that applied nothing. `proposal_id` is the item that stopped it (null when
    `reason` is `busy`)."""

    detail: str
    reason: Literal["invalid", "profile_conflict", "busy"]
    proposal_id: str | None = None


def _id(value: str, sentence: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=sentence) from None


def _documents(db: Session, user_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> dict[str, DocumentRef]:
    """The user's documents among `ids`, by id as text. A document of another user is not found
    here, so it reads as deleted and nothing of it is shown."""
    document = IngestionDocument
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    rows = db.execute(
        sa.select(document.id, document.name).where(
            document.user_id == user_id, document.id.in_(wanted)
        )
    ).all()
    return {str(one): DocumentRef(id=str(one), name=name, deleted=False) for one, name in rows}


def _cited(raw: Any) -> list[Mapping[str, Any]]:
    return [one for one in raw or () if isinstance(one, Mapping)] if isinstance(raw, list) else []


def _document_ids(payload: Mapping[str, Any]) -> list[uuid.UUID]:
    found: list[uuid.UUID] = []
    groups = [payload.get("citations")]
    groups += [other.get("citations") for other in _cited(payload.get("conflict"))]
    for group in groups:
        for citation in _cited(group):
            try:
                found.append(uuid.UUID(str(citation.get("document_id"))))
            except ValueError:
                continue
    return found


def _citations(raw: Any, known: Mapping[str, DocumentRef]) -> list[CitationOut]:
    out: list[CitationOut] = []
    for citation in _cited(raw):
        document_id = str(citation.get("document_id"))
        excerpt = citation.get("excerpt")
        excerpt = excerpt if isinstance(excerpt, str) else ""
        start, end = citation.get("start"), citation.get("end")
        out.append(
            CitationOut(
                document_id=document_id,
                document=known.get(document_id),
                excerpt=excerpt[:EXCERPT_MAX],
                excerpt_cut=len(excerpt) > EXCERPT_MAX,
                start=start if type(start) is int else 0,
                end=end if type(end) is int else 0,
                transcribed=citation.get("transcribed") is True,
            )
        )
    return out


def _free_text(payload: Mapping[str, Any]) -> bool:
    """Whether the value holds free text, whose words no quote can vouch for one by one."""
    try:
        types = items.from_payload(payload).field_types
    except items.ItemError:
        return False
    return FieldType.TEXT in types.values()


def _changed(found: Sequence[Proposal]) -> set[str]:
    """The employer keys whose item was rejected, taken back, or accepted with the user's own
    value: the derived years that counted one of them are to be checked again."""
    changed: set[str] = set()
    for proposal in found:
        if proposal.payload.get("section") != "experience":
            continue
        moved = proposal.status in (ProposalStatus.rejected, ProposalStatus.undone) or (
            proposal.status is ProposalStatus.accepted and proposal.edited_value is not None
        )
        if moved:
            changed.add(str(proposal.payload.get("key")))
    return changed


def _set_aside(proposal: Proposal) -> str | None:
    """Why the system set this item aside (G13 greys it with the reason): the sentence written
    when it was marked invalid, which names a rule and no value."""
    return proposal.reason if proposal.status is ProposalStatus.invalid else None


def _item(
    proposal: Proposal,
    stored: Mapping[str, Any] | None,
    known: Mapping[str, DocumentRef],
    changed: set[str],
) -> ItemOut:
    payload = proposal.payload
    depends = [name for name in payload.get("depends_on") or () if isinstance(name, str)]
    pending = proposal.status is ProposalStatus.pending
    seen = item_proposals.review(payload, stored) if pending else None
    reason = None
    if seen is not None and seen.stale:
        reason = STALE_PROFILE
    elif pending and changed.intersection(depends):
        reason = STALE_DEPENDS
    applied = payload.get("applied_revision")
    base = payload.get("base_revision")
    key = str(payload.get("key"))
    return ItemOut(
        id=str(proposal.id),
        status=proposal.status,
        section=str(payload.get("section")),
        key=key,
        slot=key.partition(":")[0],
        action=str(payload.get("action")),
        value=proposal.edited_value if proposal.edited_value is not None else payload.get("value"),
        before=seen.before if seen else None,
        after=seen.after if seen else None,
        noop=bool(seen and seen.noop),
        stale=reason is not None,
        stale_reason=reason,  # type: ignore[arg-type]
        locked=bool(seen.locked) if seen else item_proposals.bulk_exclusion(payload) == "locked",
        bulk=(seen.bulk or (item_proposals.STALE if reason else None)) if seen else None,
        problem=seen.problem if seen else _set_aside(proposal),
        flags=[flag for flag in payload.get("flags") or () if flag in items.FLAGS],
        free_text=_free_text(payload),
        citations=_citations(payload.get("citations"), known),
        conflict=[
            OtherValueOut(
                value=other.get("value"), citations=_citations(other.get("citations"), known)
            )
            for other in _cited(payload.get("conflict"))
        ],
        depends_on=depends,
        base_revision=base if type(base) is int else None,
        applied_revision=applied if type(applied) is int else None,
    )


def _counts(found: Sequence[ItemOut]) -> CountsOut:
    counts = CountsOut(total=len(found))
    for one in found:
        setattr(counts, one.status.value, getattr(counts, one.status.value) + 1)
    return counts


@router.get("/runs/{run_id}/proposals", response_model=ReviewOut, responses={**_401, **_404})
def list_proposals(run_id: str, user: CurrentUser, request: Request, db: Db) -> ReviewOut:
    run = db.scalar(
        sa.select(IngestionRun).where(
            IngestionRun.id == _id(run_id, RUN_NOT_FOUND), IngestionRun.user_id == user.id
        )
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=RUN_NOT_FOUND)
    found = item_proposals.list_items(
        db, user.id, ingestion_run_id=run.id, statuses=tuple(ProposalStatus), limit=ITEMS_MAX
    )
    profile = profile_service.get_profile(db, user.id, deps.clock(request))
    selected = list(run.document_ids or ())
    cited = [one for proposal in found for one in _document_ids(proposal.payload)]
    known = _documents(db, user.id, [*selected, *cited])
    changed = _changed(found)
    by_section: dict[str, list[ItemOut]] = {}
    for proposal in found:
        section = str(proposal.payload.get("section"))
        stored = (profile.sections or {}).get(section)
        by_section.setdefault(section, []).append(_item(proposal, stored, known, changed))
    order = {name: index for index, name in enumerate(items.SLOTS)}
    sections = [
        SectionOut(
            section=name,
            counts=_counts(listed),
            items=sorted(listed, key=lambda one: (one.key, one.id)),
        )
        for name, listed in sorted(
            by_section.items(), key=lambda pair: (order.get(pair[0], len(order)), pair[0])
        )
    ]
    out = ReviewOut(
        run_id=str(run.id),
        status=run.status,
        partial=run.partial,
        profile_revision=profile.revision,
        counts=_counts([one for section in sections for one in section.items]),
        documents=[
            known.get(str(one)) or DocumentRef(id=str(one), name=None, deleted=True)
            for one in selected
        ],
        sections=sections,
    )
    db.commit()  # `get_profile` creates the profile row of a user who has none yet
    return out


@router.post(
    "/proposals/{proposal_id}/undo",
    response_model=UndoOut,
    responses={
        **_401,
        **_404,
        409: {"model": UndoConflictOut, "description": "Not accepted, or it cannot be undone"},
    },
)
def undo(proposal_id: str, user: CurrentUser, request: Request, db: Db) -> Any:
    target = _id(proposal_id, PROPOSAL_NOT_FOUND)
    try:
        done = proposals.undo(db, user.id, target, deps.clock(request))
    except proposals.InvalidValueError:
        db.rollback()  # one of the user's job proposals: no profile item has this id
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=PROPOSAL_NOT_FOUND
        ) from None
    except item_proposals.UndoError as exc:
        current, reason = exc.proposal.status, exc.reason
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=UndoConflictOut(
                detail=UNDO_REFUSED[reason],
                reason=reason,  # type: ignore[arg-type]
                status=current,
            ).model_dump(mode="json"),
            headers=NO_STORE,
        )
    if done is None:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PROPOSAL_NOT_FOUND)
    proposal, profile = done
    out = UndoOut(id=str(proposal.id), status=proposal.status, profile_revision=profile.revision)
    db.commit()
    return out


def _run(db: Session, user: User, run_id: str) -> IngestionRun:
    run = review.run_of(db, user.id, _id(run_id, RUN_NOT_FOUND))
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=RUN_NOT_FOUND)
    return run


def _refused(out: SectionConflictOut) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=out.model_dump(mode="json"),
        headers=NO_STORE,
    )


_409_BULK = {
    409: {"model": SectionConflictOut, "description": "One item stopped the section, or busy"}
}
_422 = {422: {"model": ErrorOut, "description": "Unknown section or malformed id"}}


@router.post(
    "/runs/{run_id}/accept-section",
    response_model=SectionDecidedOut,
    responses={**_401, **_404, **_409_BULK, **_422},
)
def accept_section(
    run_id: str, body: SectionIn, user: CurrentUser, request: Request, db: Db
) -> Any:
    run = _run(db, user, run_id)
    now = deps.clock(request)
    try:
        done = review.accept_section(db, user.id, run.id, body.section, body.ids, now)
    except review.SectionRefusedError as exc:
        failure = exc.failure
        if failure.reason == item_proposals.INVALID:
            db.commit()  # keeps the one proposal marked invalid, and nothing else
        else:
            db.rollback()
        return _refused(
            SectionConflictOut(
                detail=SECTION_STOPPED[failure.reason].format(reason=failure.message.rstrip(".")),
                reason=failure.reason,  # type: ignore[arg-type]
                proposal_id=str(failure.proposal_id),
            )
        )
    except review.ReviewBusyError:
        db.rollback()
        return _refused(SectionConflictOut(detail=BUSY, reason="busy"))
    revision = profile_service.get_profile(db, user.id, now).revision
    out = SectionDecidedOut.of(run, body.section, done, revision)
    db.commit()
    return out


@router.post(
    "/runs/{run_id}/reject-section",
    response_model=SectionDecidedOut,
    responses={**_401, **_404, **_409_BULK, **_422},
)
def reject_section(
    run_id: str, body: SectionIn, user: CurrentUser, request: Request, db: Db
) -> Any:
    run = _run(db, user, run_id)
    now = deps.clock(request)
    try:
        done = review.reject_section(db, user.id, run.id, body.section, body.ids, now)
    except review.ReviewBusyError:
        db.rollback()
        return _refused(SectionConflictOut(detail=BUSY, reason="busy"))
    revision = profile_service.get_profile(db, user.id, now).revision
    out = SectionDecidedOut.of(run, body.section, done, revision)
    db.commit()
    return out
