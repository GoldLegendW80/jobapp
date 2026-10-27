"""/api/browser/sites: the job websites the person signed in to (browser B17).

- GET /api/browser/sites answers `{sites: [{host, state, signed_in_at}]}`, `state` being
  `signed_in` or `expired` (a search met the site's sign in page since).
- POST /api/browser/sites/done `{address}` is the Done of a website's Sign in: it ends the
  person's own sign in session (B7, `POST /api/browser/sessions {purpose: "signin", address}`
  started it), then asks browserd whether the profile now holds that website's cookies and
  records the login only when it does: 200 `{host, state}`, `state` `signed_in` or
  `not_signed_in` (also for a person without any browser profile: none is made). 422
  `bad_address` for an address the sign in rule refuses; 409 `busy` while another lease of the
  person's profile runs (Done again later); 503 when browserd or Redis cannot be reached.

Forgetting a login is B7's "Forget all logins" (DELETE /api/browser/profile): the rows go with
the profile row. The user comes from the session (CSRF on the write); no id is in any path.
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.browser import site_logins
from app.browser.address import ADDRESS_MAX
from app.browser.client import BrowserError
from app.browser.leases import StoreUnavailableError
from app.browser.router import BAD_ADDRESS, UNAVAILABLE, CurrentUser, Store, no_store
from app.identity.schemas import ErrorOut

router = APIRouter(prefix="/api/browser/sites", tags=["browser"], dependencies=[Depends(no_store)])

BUSY = "Your browser is busy with another task. Click Done again in a moment."
NOT_SIGNED_IN = "No login of this website was found in your browser. Sign in, then click Done."


class SiteOut(BaseModel):
    host: str
    state: Literal["signed_in", "expired"]
    signed_in_at: datetime


class SitesOut(BaseModel):
    sites: list[SiteOut]


class DoneIn(BaseModel):
    address: str = Field(max_length=ADDRESS_MAX)


class DoneOut(BaseModel):
    host: str
    state: Literal["signed_in", "not_signed_in"]
    detail: str | None = None


@router.get("", response_model=SitesOut)
async def get_sites(request: Request, user: CurrentUser) -> SitesOut:
    rows = await run_in_threadpool(site_logins.listing, request.app.state.sessionmaker, user.id)
    return SitesOut(
        sites=[SiteOut(host=r.host, state=r.state, signed_in_at=r.signed_in_at) for r in rows]
    )


@router.post(
    "/done",
    response_model=DoneOut,
    responses={
        409: {"model": ErrorOut, "description": BUSY},
        422: {"model": ErrorOut, "description": BAD_ADDRESS},
        503: {"model": ErrorOut, "description": UNAVAILABLE},
    },
)
async def done(
    body: DoneIn, request: Request, user: CurrentUser, store: Store
) -> DoneOut | JSONResponse:
    host = site_logins.host_of(body.address)
    if host is None:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": BAD_ADDRESS, "code": "bad_address"},
            headers={"Cache-Control": "no-store"},
        )
    sessions = request.app.state.sessionmaker
    try:
        # The person's own sign in session ends first: Chromium writes the profile and closes.
        await store.release(str(user.id), purposes=("signin",))
        await store.cancel(str(user.id), purposes=("signin",))
        profile = await run_in_threadpool(site_logins.profile_of, sessions, user.id)
        present = False if profile is None else await store.client.site_session(*profile, host)
    except (BrowserError, StoreUnavailableError):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE) from None
    if present is None:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": BUSY, "code": "busy"},
            headers={"Cache-Control": "no-store"},
        )
    if not present or not await run_in_threadpool(
        site_logins.record, sessions, user.id, host, datetime.now(UTC)
    ):
        return DoneOut(host=host, state="not_signed_in", detail=NOT_SIGNED_IN)
    return DoneOut(host=host, state="signed_in")
