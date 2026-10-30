"""The job feeds' routes (SPEC-jobs.md, Job feeds): which feeds exist and whether the person
stored the key a feed needs; save or remove Adzuna's key. The caller's own row only, from the
session; the key is never returned."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.orm import Session

from app.agents.router import keyring_of
from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs.feeds import FEED_NAMES, keys

router = APIRouter(prefix="/api/jobs/feeds", tags=["jobs"])

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]


class FeedOut(BaseModel):
    name: str
    needs_key: bool
    key_stored: bool = False
    key_last4: str | None = None
    key_saved_at: datetime | None = None


class FeedsOut(BaseModel):
    feeds: list[FeedOut]
    # Whether this server can store a key at all (CREDENTIALS_KEY is set).
    keys_available: bool


class FeedKeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    app_id: SecretStr = Field(max_length=200)
    app_key: SecretStr = Field(max_length=400)


def _out(db: Session, user: User, name: str) -> FeedOut:
    if name not in keys.KEYED_FEEDS:
        return FeedOut(name=name, needs_key=False)
    row = keys.get(db, user.id, name)
    return FeedOut(
        name=name,
        needs_key=True,
        key_stored=row is not None,
        key_last4=row.last4 if row is not None else None,
        key_saved_at=row.updated_at if row is not None else None,
    )


@router.get("", response_model=FeedsOut)
def list_feeds(request: Request, user: CurrentUser, db: Db) -> FeedsOut:
    return FeedsOut(
        feeds=[_out(db, user, name) for name in FEED_NAMES],
        keys_available=request.app.state.credentials_keyring is not None,
    )


@router.put(
    "/{feed}/key",
    response_model=FeedOut,
    responses={422: {"model": ErrorOut}, 503: {"model": ErrorOut}},
)
def put_key(
    feed: Literal["adzuna"], body: FeedKeyIn, request: Request, user: CurrentUser, db: Db
) -> FeedOut:
    keyring = keyring_of(request)
    try:
        key = keys.clean(body.app_id.get_secret_value(), body.app_key.get_secret_value())
    except keys.FormatError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from None
    keys.save(db, keyring, user.id, feed, key, deps.clock(request))
    db.commit()
    return _out(db, user, feed)


@router.delete("/{feed}/key", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_key(feed: Literal["adzuna"], user: CurrentUser, db: Db) -> Response:
    keys.delete(db, user.id, feed)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
