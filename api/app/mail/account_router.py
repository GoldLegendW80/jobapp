"""/api/mail: connecting, the account, the consent, disconnecting (mail M4).

- GET /oauth/start sends the browser to Google's consent screen (offline access, consent asked,
  the four scopes, PKCE S256, a signed state bound to this session). When this user cannot
  start, or the server is not set up for Gmail, it sends the browser back to
  `/account?gmail=error` instead.
- GET /oauth/callback is where Google sends the browser back. It always answers with a
  redirect to `/account?gmail=<connected | denied | taken | scope | error>`: a fixed list, never
  a value of the request. Nothing of the query is echoed, logged or stored. It is a public
  route (identity.deps.PUBLIC_ROUTES) that reads the session itself: with none it redirects to
  `error` too, so the `code` and the `state` do not stay in the address bar (the sign in then
  leads to /account?gmail=error), and the state is left unread, so it stays good for its owner's
  session until it expires.
- GET /account answers the caller's own connection: `{available, status, email, can_send,
  scopes, connected_at, last_sync_at, consent}`. `status` is `not_connected`, `ok`,
  `needs_reconnect`, `error` or `disconnecting`.
- PUT /account/consent `{granted, version}` records or withdraws the consent of decision 13:
  404 without an account, 409 when `version` is not the current text's.
- DELETE /account disconnects: `{disconnected, revoked}`. `revoked` false means Google did not
  confirm, and the page tells the user to remove the access there. 200 also when nothing was
  connected.

The user always comes from the session (default deny, CSRF on every write). Every route acts
on the caller's own row only: there is no id in any path. A lock wait over 5 s is the 409 of
app.main. Every answer is `Cache-Control: no-store` (routers.no_store).
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.agents.crypto import Keyring
from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.identity.service import ActiveSession
from app.jobs.service import is_lock_timeout
from app.mail import accounts, locks, log, oauth
from app.mail.google import SCOPE_READONLY, SCOPE_SEND, GoogleClient
from app.mail.models import MailAccount

router = APIRouter(prefix="/api/mail", tags=["mail"])

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]
CurrentSession = Annotated[ActiveSession, Depends(deps.current_session)]

BACK = "/account?gmail="
NOT_CONNECTED = "Gmail is not connected."
CONSENT_CHANGED = "The consent text has changed. Reload the page and read it again."

_404 = {404: {"model": ErrorOut, "description": "No Gmail account is connected"}}
_409 = {409: {"model": ErrorOut, "description": "Busy, or the consent text changed"}}


class ConsentOut(BaseModel):
    granted: bool
    at: datetime | None
    version: int | None
    # The version of the text the page must show and send back.
    current_version: int


class AccountOut(BaseModel):
    # False when this server has no Google client, no encryption key or no redirect address.
    available: bool
    status: Literal["not_connected", "ok", "needs_reconnect", "error", "disconnecting"]
    email: str | None
    can_read: bool
    can_send: bool
    connected_at: datetime | None
    last_sync_at: datetime | None
    consent: ConsentOut


class ConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    granted: bool
    version: int


class DisconnectOut(BaseModel):
    disconnected: bool
    revoked: bool


def _google(request: Request) -> GoogleClient | None:
    found: GoogleClient | None = request.app.state.mail_google
    return found


def _keyring(request: Request) -> Keyring | None:
    found: Keyring | None = request.app.state.credentials_keyring
    return found


def _sessions(request: Request) -> sessionmaker[Session]:
    found: sessionmaker[Session] = request.app.state.sessionmaker
    return found


def _available(request: Request) -> bool:
    return (
        _google(request) is not None
        and _keyring(request) is not None
        and request.app.state.mail_state_key is not None
        and request.app.state.settings.google_oauth_redirect_uri is not None
    )


def _busy(failed: DBAPIError) -> HTTPException:
    """A lock wait that ran out is 409 with the module's sentence; anything else goes on."""
    if not is_lock_timeout(failed):
        raise failed
    return HTTPException(
        status.HTTP_409_CONFLICT, detail=locks.BUSY, headers={"Cache-Control": "no-store"}
    )


def _back(outcome: accounts.Outcome) -> RedirectResponse:
    return RedirectResponse(
        f"{BACK}{outcome.value}",
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


def _out(request: Request, account: MailAccount | None) -> AccountOut:
    consent = ConsentOut(
        granted=(
            account is not None
            and account.assistant_consent_at is not None
            and account.assistant_consent_version == accounts.CONSENT_VERSION
        ),
        at=account.assistant_consent_at if account else None,
        version=account.assistant_consent_version if account else None,
        current_version=accounts.CONSENT_VERSION,
    )
    if account is None:
        return AccountOut(
            available=_available(request),
            status="not_connected",
            email=None,
            can_read=False,
            can_send=False,
            connected_at=None,
            last_sync_at=None,
            consent=consent,
        )
    return AccountOut(
        available=_available(request),
        status="disconnecting" if account.disconnecting_at else account.status.value,
        email=account.email,
        can_read=SCOPE_READONLY in account.scopes,
        can_send=SCOPE_SEND in account.scopes,
        connected_at=account.connected_at,
        last_sync_at=account.last_sync_at,
        consent=consent,
    )


@router.get("/oauth/start", status_code=status.HTTP_303_SEE_OTHER, response_class=RedirectResponse)
async def start(request: Request, session: CurrentSession) -> RedirectResponse:
    google = _google(request)
    if google is None or not _available(request):
        return _back(accounts.Outcome.error)
    try:
        address = await accounts.begin(
            sessions=_sessions(request),
            google=google,
            verifiers=request.app.state.mail_verifiers,
            key=request.app.state.mail_state_key,
            redirect_uri=request.app.state.settings.google_oauth_redirect_uri,
            user_id=session.user.id,
            session_id=session.row.id,
            now=deps.clock(request),
        )
    except oauth.StoreUnavailableError:
        log.event("mail.oauth.refused", user_id=str(session.user.id), reason="store")
        return _back(accounts.Outcome.error)
    if address is None:
        return _back(accounts.Outcome.error)
    return RedirectResponse(
        address,
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get(
    "/oauth/callback", status_code=status.HTTP_303_SEE_OTHER, response_class=RedirectResponse
)
async def callback(request: Request) -> RedirectResponse:
    session = await deps.resolve(request)
    if session is None:
        return _back(accounts.Outcome.error)
    google, keyring = _google(request), _keyring(request)
    if google is None or keyring is None or not _available(request):
        return _back(accounts.Outcome.error)
    outcome = await accounts.complete(
        sessions=_sessions(request),
        google=google,
        keyring=keyring,
        verifiers=request.app.state.mail_verifiers,
        key=request.app.state.mail_state_key,
        redirect_uri=request.app.state.settings.google_oauth_redirect_uri,
        user_id=session.user.id,
        session_id=session.row.id,
        # The last value of a repeated name, as a plain mapping: nothing else is read.
        query=dict(request.query_params),
        now=deps.clock(request),
    )
    return _back(outcome)


@router.get("/account", response_model=AccountOut)
def read_account(request: Request, db: Db, user: CurrentUser) -> AccountOut:
    return _out(request, accounts.get(db, user.id))


@router.put("/account/consent", response_model=AccountOut, responses={**_404, **_409})
def put_consent(request: Request, body: ConsentIn, db: Db, user: CurrentUser) -> AccountOut:
    try:
        account = accounts.set_consent(
            db, user.id, granted=body.granted, version=body.version, now=deps.clock(request)
        )
    except accounts.ConsentError as refused:
        db.rollback()
        if refused.reason == "version":
            raise HTTPException(status.HTTP_409_CONFLICT, detail=CONSENT_CHANGED) from None
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_CONNECTED) from None
    except DBAPIError as failed:
        db.rollback()
        raise _busy(failed) from None
    db.commit()
    return _out(request, account)


@router.delete("/account", response_model=DisconnectOut, responses=_409)
async def delete_account(request: Request, user: CurrentUser) -> DisconnectOut:
    try:
        done = await accounts.disconnect(
            sessions=_sessions(request),
            google=_google(request),
            keyring=_keyring(request),
            user_id=user.id,
            now=deps.clock(request),
        )
    except DBAPIError as failed:
        # After the flag: the tick finishes the disconnect, or the user asks again.
        raise _busy(failed) from None
    return DisconnectOut(disconnected=True, revoked=done.revoked)
