"""/api/browser: the person's browser and their own sessions in it (browser B3).

- GET /api/browser answers `{available, profile, lease, holder, queue}`: whether browserd answers,
  the person's profile (size and last use; never a file of it), their own current lease (with,
  during a sign in hand off, `waiting_person`, the site and the minutes left: B12a; on apply's
  submit lease, the id of the application it sends), who
  holds the browser as seen by this person (`you`, `other`, or null: never who, nor why), and
  their places in the queue with the estimated wait.
- POST /api/browser/sessions `{purpose: "look" | "signin", address?}` starts a session of the
  person in the live view (`look` is browserd's purpose `takeover`, the person at the keyboard):
  201 with the lease when the browser was free or already theirs, 202 with the place in the
  queue otherwise. It is idempotent: a waiting page asks again to keep its place and to take the
  turn. Both run Chromium with no debugging port and the person holding the input (B7); a
  `signin` may open at the address the person typed, an http or https address with a host of at
  most ADDRESS_MAX characters (app/browser/address.py), else 422 `bad_address` and nothing
  starts, a value that is not a string included.
- DELETE /api/browser/sessions/current ends the person's own session, or takes them out of the
  queue: 204, or 404 when there was neither.

The user always comes from the session (default deny, CSRF on every write); there is no id in
any path, so nobody can name another person's lease. When browserd or Redis cannot be reached a
write answers 503 with UNAVAILABLE, and GET says `available: false`. Every answer these routes
build is `Cache-Control: no-store` (the 401, 403 and 422 built before them hold no data).
"""

import math
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidatorFunctionWrapHandler,
    field_validator,
)

from app.browser import profiles
from app.browser.address import ADDRESS_MAX, check_address
from app.browser.client import BrowserdClient, BrowserError
from app.browser.leases import (
    DbProfiles,
    Lease,
    Leases,
    StoreUnavailableError,
    Waiting,
    lease_max_table,
)
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut

UNAVAILABLE = "The browser is not available right now."
NO_SESSION = "You have no browser session."
BAD_ADDRESS = "Type the address of the site, starting with https:// or http://."

# The purposes a person starts from the page, and browserd's name of each.
PERSON_PURPOSES = {"look": "takeover", "signin": "signin"}
SHOWN = {value: key for key, value in PERSON_PURPOSES.items()}


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api/browser", tags=["browser"], dependencies=[Depends(no_store)])

CurrentUser = Annotated[User, Depends(deps.current_user)]

_503 = {503: {"model": ErrorOut, "description": "browserd or Redis cannot be reached"}}


def get_leases(request: Request) -> Leases:
    """The app's lease store, made on first use from the settings."""
    state = request.app.state
    found: Leases | None = getattr(state, "browser_leases", None)
    if found is None:
        settings = state.settings
        secret = settings.browser_control_secret
        found = Leases(
            state.queue.pool,
            BrowserdClient(
                settings.browser_url, secret.get_secret_value() if secret is not None else None
            ),
            DbProfiles(state.sessionmaker),
            idle_s=settings.browser_idle_s,
            session_max_s=settings.browser_session_max_s,
            max_table=lease_max_table(settings.browser_lease_max_s),
        )
        state.browser_leases = found
    return found


Store = Annotated[Leases, Depends(get_leases)]


class ProfileOut(BaseModel):
    exists: bool
    size_bytes: int
    last_used_at: datetime | None


class LeaseOut(BaseModel):
    id: str
    purpose: str
    # False: a person's own session, where the person holds the input from start to end (B7).
    automation: bool
    state: Literal["starting", "active", "waiting_person"]
    wait_reason: str | None
    # B12b: the host of the address browserd noted for the sign in, never the model's words.
    wait_host: str | None
    wait_deadline: datetime | None
    # B12a: the whole minutes a sign in hand off still waits for the person, rounded up.
    wait_minutes_left: int | None
    started_at: datetime
    active_s: int
    max_s: int
    # The application a submit lease sends: its job page shows that application's lease only.
    application_id: str | None


class QueueOut(BaseModel):
    purpose: str
    position: int
    estimated_wait_s: int


class BrowserOut(BaseModel):
    available: bool
    profile: ProfileOut
    lease: LeaseOut | None
    holder: Literal["you", "other"] | None
    queue: list[QueueOut]


class SessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Literal["look", "signin"]
    address: str | None = Field(default=None, max_length=ADDRESS_MAX)

    @field_validator("address", mode="wrap")
    @classmethod
    def _any_bad_address(cls, value: Any, handler: ValidatorFunctionWrapHandler) -> str | None:
        # Not a string, or too long: "" here, so start_session answers 422 `bad_address`.
        try:
            return handler(value)
        except ValidationError:
            return ""


class SessionOut(BaseModel):
    lease: LeaseOut | None
    queue: QueueOut | None


def _when(at: float | None) -> datetime | None:
    return None if at is None else datetime.fromtimestamp(at, UTC)


def _lease_out(lease: Lease, now: float) -> LeaseOut:
    return LeaseOut(
        id=lease.lease_id,
        purpose=SHOWN.get(lease.purpose, lease.purpose),
        automation=lease.automation,
        state=lease.state,  # type: ignore[arg-type]
        wait_reason=lease.wait_reason,
        wait_host=lease.wait_host,
        wait_deadline=_when(lease.wait_deadline),
        wait_minutes_left=_minutes_left(lease, now),
        started_at=datetime.fromtimestamp(lease.started_at, UTC),
        active_s=int(lease.active_s(now)),
        max_s=lease.max_s,
        application_id=lease.application_id,
    )


def _minutes_left(lease: Lease, now: float) -> int | None:
    if lease.state != "waiting_person" or lease.wait_deadline is None:
        return None
    return max(0, math.ceil((lease.wait_deadline - now) / 60))


def _queue_out(place: Waiting) -> QueueOut:
    return QueueOut(
        purpose=SHOWN.get(place.purpose, place.purpose),
        position=place.position,
        estimated_wait_s=place.estimated_wait_s,
    )


def _unavailable() -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)


@router.get("", response_model=BrowserOut)
async def get_browser(request: Request, user: CurrentUser, store: Store) -> BrowserOut:
    try:
        view = await store.current(str(user.id))
    except StoreUnavailableError:
        view = None
    # Read after `current`, which may have just ended a lease and written its size (B7).
    found = await run_in_threadpool(profiles.view, request.app.state.sessionmaker, user.id)
    profile = ProfileOut(
        exists=found is not None,
        size_bytes=found.size_bytes if found else 0,
        last_used_at=found.last_used_at if found else None,
    )
    if view is None:
        return BrowserOut(available=False, profile=profile, lease=None, holder=None, queue=[])
    now = store.clock()
    holder: Literal["you", "other"] | None = (
        "you" if view.lease is not None else "other" if view.held_by_other else None
    )
    return BrowserOut(
        available=store.client.configured and await _answers(store),
        profile=profile,
        lease=_lease_out(view.lease, now) if view.lease is not None else None,
        holder=holder,
        queue=[_queue_out(place) for place in view.waiting],
    )


async def _answers(store: Leases) -> bool:
    try:
        await store.client.current()
    except BrowserError:
        return False
    return True


@router.post(
    "/sessions",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        202: {"model": SessionOut, "description": "Waiting in the queue"},
        422: {"model": ErrorOut, "description": BAD_ADDRESS},
        **_503,
    },
)
async def start_session(
    body: SessionIn, response: Response, user: CurrentUser, store: Store
) -> SessionOut | JSONResponse:
    address = None
    if body.address is not None:
        address = check_address(body.address) if body.purpose == "signin" else None
        if address is None:
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                content={"detail": BAD_ADDRESS, "code": "bad_address"},
                headers={"Cache-Control": "no-store"},
            )
    try:
        got = await store.acquire(
            str(user.id), PERSON_PURPOSES[body.purpose], automation=False, address=address
        )
    except (BrowserError, StoreUnavailableError):
        raise _unavailable() from None
    if got.lease is not None:
        return SessionOut(lease=_lease_out(got.lease, store.clock()), queue=None)
    response.status_code = status.HTTP_202_ACCEPTED
    return SessionOut(lease=None, queue=_queue_out(got.waiting) if got.waiting else None)


@router.delete(
    "/sessions/current",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorOut, "description": NO_SESSION}, **_503},
)
async def end_session(user: CurrentUser, store: Store) -> Response:
    person = tuple(PERSON_PURPOSES.values())
    try:
        ended = await store.release(str(user.id), purposes=person)
        left = await store.cancel(str(user.id), purposes=person)
    except StoreUnavailableError:
        raise _unavailable() from None
    if not ended and not left:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SESSION)
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})
