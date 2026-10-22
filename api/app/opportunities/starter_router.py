"""/api/opportunities/starter: the starter list of boards and sources (opportunities O5b),
starter.py.

- GET /api/opportunities/starter: the caller's list: `entries` (each `{key, kind: board|source,
  company, ats, org, source, query, location, why, added}`), `targets` (roles, industries or
  places were given), `unmatched` (role and industry words the catalog knows nothing of),
  `blocked` (how many entries a never apply rule left out), the catalog's `version` and
  `checked_on`. 409 `search_unreadable` when the stored search section no longer passes the
  checks.
- POST /api/opportunities/starter `{add: [key], remove: [key]}`: the person's choice. Each key
  added becomes an enabled board or browser source of the search section (the person ticked it;
  one revision of `search`), each key added or removed is never proposed again once gone from
  the search section (one revision of `preferences`). Keys no longer proposed are passed over.
  200 `{list, added}`; 422 for a key in no catalog entry; 409 `search_unreadable`; 409
  `too_many` when the search section would hold more boards or sources than it allows.

Every route needs a session and reads or writes the caller's profile only. Never cached.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.opportunities import starter
from app.profile.schemas import StarterKey

NO_STORE = {"Cache-Control": "no-store"}
UNREADABLE = "Open Search options and save them again, then come back to this list."
TOO_MANY = "Search options hold as many boards and sites as they can. Remove some first."
UNKNOWN = "That board or site is not on the starter list."

router = APIRouter(prefix="/api/opportunities/starter", tags=["opportunities"])

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE)


def catalog(request: Request) -> starter.Catalog:
    """The app's catalog (tests install their own on app.state)."""
    found = getattr(request.app.state, "starter_catalog", None)
    if isinstance(found, starter.Catalog):
        return found
    return starter.catalog_for(getattr(request.app.state, "settings", None))


class EntryOut(BaseModel):
    key: str
    kind: str
    company: str
    ats: str | None
    org: str | None
    source: str | None
    query: str | None
    location: str | None
    why: str
    added: bool


class ListOut(BaseModel):
    targets: bool
    entries: list[EntryOut]
    unmatched: list[str]
    blocked: int
    version: int
    checked_on: str


class ChoiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    add: Annotated[list[StarterKey], Field(max_length=50)] = Field(default_factory=list)
    remove: Annotated[list[StarterKey], Field(max_length=50)] = Field(default_factory=list)


class ChoiceOut(BaseModel):
    list: ListOut
    added: list[str]


def _out(found: starter.Proposal) -> ListOut:
    return ListOut(
        targets=found.targets,
        entries=[EntryOut(**entry.__dict__) for entry in found.entries],
        unmatched=found.unmatched,
        blocked=found.blocked,
        version=found.version,
        checked_on=found.checked_on,
    )


def _refused(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"detail": detail, "code": code}, headers=NO_STORE
    )


_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorOut, "description": "Not signed in"},
    409: {"model": ErrorOut, "description": "The search section cannot take the change"},
}


@router.get("", response_model=ListOut, responses=_RESPONSES, dependencies=[Depends(_no_store)])
def read_list(
    user: CurrentUser,
    request: Request,
    db: Db,
    found: Annotated[starter.Catalog, Depends(catalog)],
) -> Any:
    """The caller's starter list."""
    try:
        proposal = starter.current(db, user, found, deps.clock(request))
    except starter.SearchUnreadableError:
        db.rollback()
        return _refused(409, "search_unreadable", UNREADABLE)
    db.commit()
    return _out(proposal)


@router.post(
    "",
    response_model=ChoiceOut,
    responses={**_RESPONSES, 422: {"model": ErrorOut, "description": "Unknown key"}},
    dependencies=[Depends(_no_store)],
)
def choose(
    body: ChoiceIn,
    user: CurrentUser,
    request: Request,
    db: Db,
    found: Annotated[starter.Catalog, Depends(catalog)],
) -> Any:
    """Add or remove entries of the caller's starter list."""
    try:
        proposal, added = starter.choose(
            db, user, found, body.add, body.remove, deps.clock(request)
        )
    except starter.UnknownKeyError:
        db.rollback()
        return _refused(422, "unknown_key", UNKNOWN)
    except starter.SearchUnreadableError:
        db.rollback()
        return _refused(409, "search_unreadable", UNREADABLE)
    except starter.TooManyError:
        db.rollback()
        return _refused(409, "too_many", TOO_MANY)
    db.commit()
    return ChoiceOut(list=_out(proposal), added=added)
