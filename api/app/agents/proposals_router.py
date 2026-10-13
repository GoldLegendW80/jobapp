"""/api/proposals: what agents suggest, and the user's decision (SPEC-agents.md, API).

- GET /api/proposals?status=&kind=&target=&target_id=&run_id=&limit=: the caller's proposals,
  newest first; status and kind repeat for several values (`?status=pending&kind=job_note`).
- POST /api/proposals/{id}/accept {edited_value?, updated_at?}: apply it (app/agents/proposals.py)
  and answer the accepted proposal. 422 when edited_value does not fit the kind; 409 when it is
  no longer pending (reason not_pending, with its status), when its job is gone (target_gone: it
  is now invalid), when an unedited status proposal was made from another status than the job
  has now (status_changed: now invalid) or when updated_at is not the job's current version
  (stale, with that version).
  For a profile item (`kind=profile_item`, target "profile") the body is {edited_value?,
  profile_revision?}: 422 when edited_value does not fit its section model (the location
  names the field) or when the item holds several values and none was chosen; 409 invalid
  (the stored value no longer fits: now invalid), profile_conflict (the section cannot take
  it: still pending) or stale (the profile changed at that key since: still pending, with
  the current `profile_revision` to send back).
- POST /api/proposals/{id}/reject: mark it rejected; 409 not_pending when already decided.

Another user's proposal answers 404 like one that does not exist. The user always comes from the
session (default deny, CSRF on every write).
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.agents import proposals
from app.agents.models import Proposal, ProposalStatus
from app.agents.schemas import (
    AcceptIn,
    ItemConflictOut,
    ProposalConflictOut,
    ProposalList,
    ProposalOut,
)
from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs import service as jobs_service

router = APIRouter(prefix="/api/proposals", tags=["proposals"])
Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]

LIST_DEFAULT = 50
LIST_MAX = 200
NO_STORE = {"Cache-Control": "no-store"}
NOT_FOUND = "Proposal not found."
DECIDED = "This proposal was already decided: it is {status}."
GONE = "The job of this proposal no longer exists. The proposal is now invalid."
MOVED = (
    "The job's status changed since this was proposed. The proposal is now invalid: "
    "assess the job again."
)
STALE = "This job changed since you loaded it. Reload it and try again."
ITEM_CONFLICT = "The profile cannot take this as it is: {reason}."
ITEM_STALE = (
    "The profile changed here since this was proposed. Compare the two values and accept again."
)

_401 = {401: {"model": ErrorOut, "description": "Not signed in"}}
_404 = {404: {"model": ErrorOut, "description": "No such proposal (or not yours)"}}
_409 = {409: {"model": ProposalConflictOut, "description": "Decided already, job gone or stale"}}
_409_ACCEPT = {
    409: {
        "model": ProposalConflictOut | ItemConflictOut,
        "description": "Decided already, job gone or stale; for a profile item: invalid, "
        "profile conflict or stale",
    }
}


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE)


def _id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from None


def _conflict(out: ProposalConflictOut | ItemConflictOut) -> JSONResponse:
    return JSONResponse(status_code=409, content=out.model_dump(mode="json"), headers=NO_STORE)


def _not_pending(proposal: Proposal) -> JSONResponse:
    return _conflict(
        ProposalConflictOut(
            detail=DECIDED.format(status=proposal.status.value),
            reason="not_pending",
            status=proposal.status,
        )
    )


@router.get("", response_model=ProposalList, responses=_401, dependencies=[Depends(_no_store)])
def list_proposals(
    user: CurrentUser,
    db: Db,
    status_: Annotated[list[ProposalStatus] | None, Query(alias="status")] = None,
    kind: Annotated[list[str] | None, Query(max_length=20)] = None,
    target: Annotated[str | None, Query(max_length=32)] = None,
    target_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=LIST_MAX)] = LIST_DEFAULT,
) -> ProposalList:
    filters = proposals.Filters(
        statuses=status_ or (),
        kinds=kind or (),
        target=target,
        target_id=target_id,
        run_id=run_id,
    )
    rows = proposals.list_proposals(db, user.id, filters, limit)
    return ProposalList(items=[ProposalOut.model_validate(row) for row in rows])


@router.post(
    "/{proposal_id}/accept",
    response_model=ProposalOut,
    responses={
        **_401,
        **_404,
        **_409_ACCEPT,
        422: {"model": ErrorOut, "description": "Invalid value"},
    },
    dependencies=[Depends(_no_store)],
)
def accept(proposal_id: str, body: AcceptIn, user: CurrentUser, request: Request, db: Db) -> Any:
    target = _id(proposal_id)
    kwargs: dict[str, Any] = {"expected_updated_at": body.updated_at}
    if body.profile_revision is not None:
        kwargs["profile_revision"] = body.profile_revision
    if "edited_value" in body.model_fields_set:  # sent, even as null (then refused as invalid)
        kwargs["edited_value"] = body.edited_value
    try:
        done = proposals.accept(db, user.id, target, deps.clock(request), **kwargs)
    except proposals.NotPendingError as exc:
        db.rollback()
        return _not_pending(exc.proposal)
    except proposals.EditedValueError as exc:
        db.rollback()
        raise RequestValidationError(
            [{"type": "value_error", "loc": ("body", "edited_value", *exc.loc), "msg": str(exc)}]
        ) from None
    except proposals.InvalidValueError as exc:
        db.rollback()
        raise RequestValidationError(
            [{"type": "value_error", "loc": ("body", "edited_value"), "msg": str(exc)}]
        ) from None
    except proposals.StatusChangedError:
        db.commit()  # keeps the proposal marked invalid
        return _conflict(
            ProposalConflictOut(
                detail=MOVED, reason="status_changed", status=ProposalStatus.invalid
            )
        )
    except proposals.TargetGoneError:
        db.commit()  # keeps the proposal marked invalid
        return _conflict(
            ProposalConflictOut(detail=GONE, reason="target_gone", status=ProposalStatus.invalid)
        )
    except proposals.ItemInvalidError as exc:
        why = exc.proposal.reason or ""
        db.commit()  # keeps the proposal marked invalid
        return _conflict(
            ItemConflictOut(detail=why, reason="invalid", status=ProposalStatus.invalid)
        )
    except proposals.ProfileConflictError as exc:
        db.rollback()
        return _conflict(
            ItemConflictOut(
                detail=ITEM_CONFLICT.format(reason=exc.message.rstrip(".")),
                reason="profile_conflict",
            )
        )
    except proposals.StaleItemError as exc:
        revision = exc.revision
        db.rollback()
        return _conflict(
            ItemConflictOut(detail=ITEM_STALE, reason="stale", profile_revision=revision)
        )
    except jobs_service.StaleJobError as exc:
        current = exc.job.updated_at
        db.rollback()
        return _conflict(ProposalConflictOut(detail=STALE, reason="stale", updated_at=current))
    if done is None:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    out = ProposalOut.model_validate(done[0])
    db.commit()
    return out


@router.post(
    "/{proposal_id}/reject",
    response_model=ProposalOut,
    responses={**_401, **_404, **_409},
    dependencies=[Depends(_no_store)],
)
def reject(proposal_id: str, user: CurrentUser, request: Request, db: Db) -> Any:
    target = _id(proposal_id)
    try:
        found = proposals.reject(db, user.id, target, deps.clock(request))
    except proposals.NotPendingError as exc:
        db.rollback()
        return _not_pending(exc.proposal)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    out = ProposalOut.model_validate(found)
    db.commit()
    return out
