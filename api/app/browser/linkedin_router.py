"""GET and PUT /api/browser/linkedin: the person's LinkedIn switch (browser B10, SPEC-browser
decision 2 "Full use"; the rules are app/browser/linkedin_guard.py's).

GET answers the switch, whether the current sentence (`text`, `text_version`) is accepted, the
pause a checkpoint started, and whether the last LinkedIn sweep met the sign in wall. PUT turns
it on or off and sets the search words. Turning it on needs the acceptance of the current
version: `accept_version` is the version of the sentence the person read and accepted; it is
recorded with the date (`accepted_at`) in the person's `search` section. An older version is a
409 (`stale_text`, the page shows the new sentence), and on without any acceptance of the
current version is a 409 (`not_accepted`). Turning it off keeps the acceptance: on again needs
no new acceptance while the version is the same.

The user comes from the session (CSRF on the write); no id is in the path, so nobody reads or
changes another person's switch.
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.browser import linkedin_guard as guard
from app.browser.router import CurrentUser, no_store
from app.db import get_session
from app.identity import deps
from app.identity.schemas import ErrorOut
from app.profile import service
from app.profile.schemas import validate_section

router = APIRouter(prefix="/api/browser", tags=["browser"], dependencies=[Depends(no_store)])
Db = Annotated[Session, Depends(get_session)]

NOT_ACCEPTED = "Accept the sentence about LinkedIn's terms to turn LinkedIn on."
STALE_TEXT = "The sentence about LinkedIn's terms changed: read it again."
NO_QUERY = "Type the words to search on LinkedIn."
BUSY = "Your profile changed in another tab. Try again."
INVALID = "Your search settings do not validate. Fix them in your profile first."


class LinkedInOut(BaseModel):
    enabled: bool
    accepted: bool
    allowed: bool
    text: str
    text_version: int
    accepted_at: str | None
    accepted_version: int | None
    query: str | None
    location: str | None
    paused_until: datetime | None
    last_sweep_at: datetime | None
    needs_signin: bool


class LinkedInIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool
    interactive_only: bool = False
    base_revision: Annotated[int, Field(ge=0)] | None = None
    accept_version: Annotated[int, Field(ge=1, le=1000)] | None = None
    query: Annotated[str, Field(max_length=200)] | None = None
    location: Annotated[str, Field(max_length=200)] | None = None


def _out(state: guard.State) -> LinkedInOut:
    source = state.source
    return LinkedInOut(
        enabled=state.enabled,
        accepted=state.accepted,
        allowed=state.allowed,
        text=guard.TEXT,
        text_version=guard.TEXT_VERSION,
        accepted_at=source.accepted_at if source else None,
        accepted_version=source.text_version if source else None,
        query=source.query if source else None,
        location=source.location if source else None,
        paused_until=state.paused_until,
        last_sweep_at=state.last_sweep_at,
        needs_signin=state.needs_signin,
    )


@router.get("/linkedin", response_model=LinkedInOut)
def get_linkedin(request: Request, user: CurrentUser, db: Db) -> LinkedInOut:
    return _out(guard.state(db, user.id, deps.clock(request)))


@router.put(
    "/linkedin",
    response_model=LinkedInOut,
    responses={
        409: {
            "model": ErrorOut,
            "description": "not accepted, a stale sentence, or a profile edit",
        },
        422: {
            "model": ErrorOut,
            "description": "no search words, or a search that does not validate",
        },
    },
)
def put_linkedin(body: LinkedInIn, request: Request, user: CurrentUser, db: Db) -> LinkedInOut:
    now = deps.clock(request)
    profile = service.get_profile(db, user.id, now)
    revision = profile.revision if body.base_revision is None else body.base_revision
    if revision != profile.revision:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, BUSY)
    search: dict[str, Any] = dict((profile.sections or {}).get("search") or {})
    sources = [dict(s) for s in search.get("browser_sources") or []]
    index = next((i for i, s in enumerate(sources) if s.get("kind") == guard.KIND), None)
    entry: dict[str, Any] = dict(sources[index]) if index is not None else {"kind": guard.KIND}
    if body.accept_version is not None:
        if body.accept_version != guard.TEXT_VERSION:
            raise HTTPException(status.HTTP_409_CONFLICT, STALE_TEXT)
        entry["accepted_at"] = now.isoformat()
        entry["text_version"] = guard.TEXT_VERSION
    if body.query is not None:
        entry["query"] = " ".join(body.query.split())
    if body.location is not None:
        entry["location"] = " ".join(body.location.split()) or None
    if body.enabled:
        if entry.get("text_version") != guard.TEXT_VERSION or not entry.get("accepted_at"):
            raise HTTPException(status.HTTP_409_CONFLICT, NOT_ACCEPTED)
        if not entry.get("query"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, NO_QUERY)
    elif index is None and body.accept_version is None and not entry.get("query"):
        if body.base_revision is not None:
            current = service.get_profile(db, user.id, now, lock=True)
            if current.revision != revision:
                db.rollback()
                raise HTTPException(status.HTTP_409_CONFLICT, BUSY)
        db.rollback()
        return _out(guard.state(db, user.id, now))  # off, and nothing to keep
    if body.enabled and (
        "interactive_only" in body.model_fields_set or entry.get("enabled") is not True
    ):
        entry["interactive_only"] = body.interactive_only
    entry["enabled"] = body.enabled
    if not entry.get("query"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, NO_QUERY)
    if index is None:
        sources.append(entry)
    else:
        sources[index] = entry
    search["browser_sources"] = sources
    try:
        data = validate_section("search", search)
    except ValidationError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, INVALID) from None
    try:
        service.save_section(db, user.id, "search", data, revision, now, linkedin_consent=True)
    except service.StaleRevisionError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, BUSY) from None
    db.commit()
    return _out(guard.state(db, user.id, now))
