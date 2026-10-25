"""The person's dedicated platform consent routes."""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.browser.router import CurrentUser, no_store
from app.db import get_session
from app.identity import deps
from app.opportunities.platforms import guard
from app.profile import service
from app.profile.schemas import PLATFORM_CONSENT_KINDS, BrowserSource, validate_section

router = APIRouter(
    prefix="/api/opportunities/platforms", tags=["opportunities"], dependencies=[Depends(no_store)]
)
Db = Annotated[Session, Depends(get_session)]


class PlatformOut(BaseModel):
    kind: str
    name: str
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
    terms_url: str | None
    checked_on: str | None


class PlatformIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool
    interactive_only: bool = False
    base_revision: Annotated[int, Field(ge=0)] | None = None
    accept_version: Annotated[int, Field(ge=1, le=1000)] | None = None
    query: Annotated[str, Field(max_length=200)] | None = None
    location: Annotated[str, Field(max_length=200)] | None = None


def _out(db: Session, user_id: Any, kind: str, now: datetime) -> PlatformOut:
    if kind not in guard.KINDS:
        raise HTTPException(404, "Platform not available.")
    item = guard.metadata(kind)
    state = guard.state(db, user_id, kind, now)
    source = state.source
    return PlatformOut(
        kind=kind,
        name=item.name,
        enabled=state.enabled,
        accepted=state.accepted,
        allowed=state.allowed,
        text=item.text,
        text_version=item.text_version,
        accepted_at=source.accepted_at if source else None,
        accepted_version=source.text_version if source else None,
        query=source.query if isinstance(source, BrowserSource) else None,
        location=source.location if isinstance(source, BrowserSource) else None,
        paused_until=state.paused_until,
        last_sweep_at=state.last_sweep_at,
        needs_signin=state.needs_signin,
        terms_url=item.terms_url,
        checked_on=item.checked_on,
    )


@router.get("/{kind}", response_model=PlatformOut)
def get_platform(kind: str, request: Request, user: CurrentUser, db: Db) -> PlatformOut:
    return _out(db, user.id, kind, deps.clock(request))


def _put_additional(
    db: Session, user_id: Any, kind: str, body: PlatformIn, now: datetime
) -> PlatformOut:
    if body.query is not None or body.location is not None:
        raise HTTPException(422, "This platform consent does not accept query or location.")
    if "interactive_only" in body.model_fields_set:
        raise HTTPException(422, "This platform consent does not accept reader intent.")
    profile = service.get_profile(db, user_id, now)
    revision = profile.revision if body.base_revision is None else body.base_revision
    if revision != profile.revision:
        db.rollback()
        raise HTTPException(409, "Your profile changed in another tab. Try again.")
    search = dict((profile.sections or {}).get("search") or {})
    consents = dict(search.get("platform_consents") or {})
    entry = dict(consents.get(kind) or {})
    version = guard.metadata(kind).text_version
    if body.accept_version is not None:
        if body.accept_version != version:
            raise HTTPException(409, "The risk sentence changed: read it again.")
        entry.update(accepted_at=now.isoformat(), text_version=version)
    if body.enabled and (entry.get("text_version") != version or not entry.get("accepted_at")):
        raise HTTPException(409, "Accept the risk sentence to turn it on.")
    if not body.enabled and not entry:
        if body.base_revision is not None:
            current = service.get_profile(db, user_id, now, lock=True)
            if current.revision != revision:
                db.rollback()
                raise HTTPException(409, "Your profile changed in another tab. Try again.")
        db.rollback()
        return _out(db, user_id, kind, now)
    consents[kind] = {**entry, "enabled": body.enabled}
    search["platform_consents"] = consents
    try:
        data = validate_section("search", search)
    except ValidationError:
        raise HTTPException(
            422, "Your search settings do not validate. Fix them in your profile first."
        ) from None
    try:
        service.save_section(db, user_id, "search", data, revision, now, platform_consent=kind)
    except service.StaleRevisionError:
        db.rollback()
        raise HTTPException(409, "Your profile changed in another tab. Try again.") from None
    db.commit()
    return _out(db, user_id, kind, now)


@router.put("/{kind}", response_model=PlatformOut)
def put_platform(
    kind: str, body: PlatformIn, request: Request, user: CurrentUser, db: Db
) -> PlatformOut:
    if kind not in guard.KINDS:
        raise HTTPException(404, "Platform not available.")
    now = deps.clock(request)
    if kind in PLATFORM_CONSENT_KINDS:
        return _put_additional(db, user.id, kind, body, now)
    profile = service.get_profile(db, user.id, now)
    revision = profile.revision if body.base_revision is None else body.base_revision
    if revision != profile.revision:
        db.rollback()
        raise HTTPException(409, "Your profile changed in another tab. Try again.")
    search: dict[str, Any] = dict((profile.sections or {}).get("search") or {})
    sources = [dict(s) for s in search.get("browser_sources") or []]
    index = next((i for i, source in enumerate(sources) if source.get("kind") == kind), None)
    entry = dict(sources[index]) if index is not None else {"kind": kind}
    if body.accept_version is not None:
        if body.accept_version != guard.TEXT_VERSION:
            raise HTTPException(409, "The sentence about this site's terms changed: read it again.")
        entry.update(accepted_at=now.isoformat(), text_version=guard.TEXT_VERSION)
    if body.query is not None:
        entry["query"] = " ".join(body.query.split())
    if body.location is not None:
        entry["location"] = " ".join(body.location.split()) or None
    if body.enabled and (
        entry.get("text_version") != guard.TEXT_VERSION or not entry.get("accepted_at")
    ):
        raise HTTPException(409, "Accept the sentence about this site's terms to turn it on.")
    if (
        not body.enabled
        and index is None
        and body.accept_version is None
        and not entry.get("query")
    ):
        if body.base_revision is not None:
            current = service.get_profile(db, user.id, now, lock=True)
            if current.revision != revision:
                db.rollback()
                raise HTTPException(409, "Your profile changed in another tab. Try again.")
        db.rollback()
        return _out(db, user.id, kind, now)
    if not entry.get("query"):
        raise HTTPException(422, "Type the words to search on Free-Work.")
    if body.enabled and (
        "interactive_only" in body.model_fields_set or entry.get("enabled") is not True
    ):
        entry["interactive_only"] = body.interactive_only
    entry["enabled"] = body.enabled
    if index is None:
        sources.append(entry)
    else:
        sources[index] = entry
    search["browser_sources"] = sources
    try:
        data = validate_section("search", search)
    except ValidationError:
        raise HTTPException(
            422, "Your search settings do not validate. Fix them in your profile first."
        ) from None
    try:
        service.save_section(db, user.id, "search", data, revision, now, platform_consent=kind)
    except service.StaleRevisionError:
        db.rollback()
        raise HTTPException(409, "Your profile changed in another tab. Try again.") from None
    db.commit()
    return _out(db, user.id, kind, now)
