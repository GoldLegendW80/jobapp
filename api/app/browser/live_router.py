"""The live view's api (browser B4): its one use ticket, the check Caddy asks for, and who holds
the keyboard and the mouse.

- POST /api/browser/live/ticket `{viewer}` answers a ticket for the person's own current lease:
  one use, TICKET_TTL_S to use, bound to the person, their session, the lease and the viewer (a
  random id the page makes once per live view). It is kept in Redis under the SHA-256 of its
  value, never the value itself. 403 when the person holds no lease that has started.
- GET /api/browser/live/auth is for Caddy's `forward_auth` only (caddy/Caddyfile answers 404 to
  anyone who asks for it from outside). Caddy sends the request of the viewer's websocket
  (`/vnc/websockify?ticket=...`) with its cookie and its Origin. The answer is 204 with the
  header X-Live-Grant, which Caddy copies to browserd, when: the session is valid, the Origin is
  the app's own (the Host or X-Forwarded-Host Caddy saw), the ticket exists (it is deleted by
  this read, whatever follows), has not expired, and was made by this person in this session,
  and the lease is still theirs and current. Otherwise 403, and 401 without a session.
  The grant names the lease and the viewer, lives GRANT_TTL_S, carries a nonce browserd accepts
  once, and is signed with BROWSER_CONTROL_SECRET as browser/browserd/live.py checks it.
- POST /api/browser/takeover `{viewer}` gives that viewer the input of the person's current lease
  (browserd forwards no key or click from any other viewer); POST /api/browser/giveback
  `{viewer}` gives it back to the agent, when that viewer is the one that holds it (a tab that
  unmounts after another took over does not take the input from it). The agent's next tool call
  waits meanwhile (browserd's `Live.agent_turn`). The input also goes back to the agent when the
  holder's last view has been closed for a few seconds (a closed tab runs no cleanup). On the
  person's own session (B7) both answer 409: the person holds the input from start to end.

Nobody but the holder of the lease gets a ticket, a grant or the input: there is no lease id in
any path or body, the user comes from the session. No ticket, grant or query string is logged.
"""

import hashlib
import hmac
import json
import secrets
from typing import Annotated, Literal
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict

from app.browser.client import BrowserError
from app.browser.leases import PREFIX, Lease, Leases, StoreUnavailableError
from app.browser.router import UNAVAILABLE, Store, no_store
from app.identity import deps, service
from app.identity.models import User
from app.identity.schemas import ErrorOut

TICKET_TTL_S = 30
GRANT_TTL_S = 10  # browserd's live.GRANT_TTL_S
TICKET_KEY = PREFIX + "live:"
LIVE_PATH = "/vnc/websockify"
GRANT_HEADER = "X-Live-Grant"
NOT_HOLDER = "You are not using the browser right now."
OWN_SESSION = "This is your own session: you already have the keyboard and the mouse."

router = APIRouter(prefix="/api/browser", tags=["browser"], dependencies=[Depends(no_store)])

CurrentUser = Annotated[User, Depends(deps.current_user)]
CurrentSession = Annotated[service.ActiveSession, Depends(deps.current_session)]

_403 = {403: {"model": ErrorOut, "description": NOT_HOLDER}}
_503 = {503: {"model": ErrorOut, "description": "browserd or Redis cannot be reached"}}


class TicketIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    viewer: UUID


class TicketOut(BaseModel):
    ticket: str
    url: str
    expires_in_s: int


class InputIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    viewer: UUID


class InputOut(BaseModel):
    input: Literal["you", "assistant"]


def _key(ticket: str) -> str:
    return TICKET_KEY + hashlib.sha256(ticket.encode()).hexdigest()


def grant_mac(secret: bytes, lease_id: str, viewer: str, exp: int, nonce: str) -> str:
    message = f"jobapp-live-grant|v1|{lease_id}|{viewer}|{exp}|{nonce}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def sign_grant(secret: bytes, lease_id: str, viewer: str, exp: int) -> str:
    """The grant browserd's live view admits once (browser/browserd/live.py, `read_grant`)."""
    nonce = secrets.token_hex(16)
    return f"v1.{lease_id}.{viewer}.{exp}.{nonce}.{grant_mac(secret, lease_id, viewer, exp, nonce)}"


def _forbidden() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, NOT_HOLDER)


def _unavailable() -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)


def _secret(request: Request) -> bytes:
    secret = request.app.state.settings.browser_control_secret
    value = secret.get_secret_value() if secret is not None else ""
    if not value:
        raise _unavailable()
    return value.encode()


async def _held(store: Leases, user_id: str) -> Lease:
    """The person's own lease once browserd started it, else 403."""
    try:
        view = await store.current(user_id)
    except StoreUnavailableError:
        raise _unavailable() from None
    lease = view.lease
    if lease is None or lease.state == "starting":
        raise _forbidden()
    return lease


def same_origin(request: Request) -> bool:
    """The page that opens the websocket is the app's own: its Origin names the host Caddy
    received the request on. A missing or opaque Origin is refused."""
    origin = urlsplit(request.headers.get("origin", ""))
    if origin.scheme not in ("http", "https") or not origin.netloc:
        return False
    hosts = {
        value.strip().lower()
        for value in (request.headers.get("host"), request.headers.get("x-forwarded-host"))
        if value
    }
    return origin.netloc.lower() in hosts


def ticket_of(forwarded_uri: str) -> str | None:
    """The ticket of the viewer's websocket address, only on LIVE_PATH and given once."""
    parts = urlsplit(forwarded_uri)
    if parts.path != LIVE_PATH:
        return None
    values = parse_qs(parts.query).get("ticket", [])
    if len(values) != 1 or not 20 <= len(values[0]) <= 100:
        return None
    return values[0]


@router.post(
    "/live/ticket",
    response_model=TicketOut,
    status_code=status.HTTP_201_CREATED,
    responses={**_403, **_503},
)
async def live_ticket(
    body: TicketIn, request: Request, active: CurrentSession, store: Store
) -> TicketOut:
    _secret(request)
    user_id = str(active.user.id)
    lease = await _held(store, user_id)
    ticket = secrets.token_urlsafe(32)
    record = {
        "user": user_id,
        "session": str(active.row.id),
        "lease": lease.lease_id,
        "viewer": str(body.viewer),
        "exp": store.clock() + TICKET_TTL_S,
    }
    try:
        redis = await request.app.state.queue.pool()
        await redis.set(_key(ticket), json.dumps(record), ex=TICKET_TTL_S + 5, nx=True)
    except Exception:
        raise _unavailable() from None
    return TicketOut(ticket=ticket, url=f"{LIVE_PATH}?ticket={ticket}", expires_in_s=TICKET_TTL_S)


@router.get(
    "/live/auth",
    status_code=status.HTTP_204_NO_CONTENT,
    include_in_schema=False,
)
async def live_auth(request: Request, active: CurrentSession, store: Store) -> Response:
    ticket = ticket_of(request.headers.get("x-forwarded-uri", ""))
    if ticket is None or not same_origin(request):
        raise _forbidden()
    try:
        redis = await request.app.state.queue.pool()
        raw = await redis.getdel(_key(ticket))
    except Exception:
        raise _unavailable() from None
    if raw is None:
        raise _forbidden()
    record = json.loads(raw)
    user_id = str(active.user.id)
    if (
        record.get("user") != user_id
        or record.get("session") != str(active.row.id)
        or not store.clock() < float(record.get("exp", 0))
    ):
        raise _forbidden()
    lease = await _held(store, user_id)
    if lease.lease_id != record.get("lease"):
        raise _forbidden()
    grant = sign_grant(
        _secret(request), lease.lease_id, record["viewer"], int(store.clock()) + GRANT_TTL_S
    )
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={GRANT_HEADER: grant, "Cache-Control": "no-store"},
    )


async def _set_input(
    store: Leases, user_id: str, viewer: str | None, giver: str | None = None
) -> InputOut:
    lease = await _held(store, user_id)
    if not lease.automation:  # B7: the person's own session, their input from start to end
        raise HTTPException(status.HTTP_409_CONFLICT, OWN_SESSION)
    try:
        holder = await store.client.live_input(lease.lease_id, viewer, giver)
        # Mirror browserd's paused/resumed active clock, including an ignored foreign tab's
        # Give back. The native owner remains browserd's decision.
        await store.current(user_id)
    except BrowserError as exc:
        if exc.code == "refused":  # the lease ended in browserd meanwhile
            raise _forbidden() from None
        raise _unavailable() from None
    return InputOut(input="you" if holder == "person" else "assistant")


_409 = {409: {"model": ErrorOut, "description": OWN_SESSION}}


@router.post("/takeover", response_model=InputOut, responses={**_403, **_409, **_503})
async def take_over(body: InputIn, user: CurrentUser, store: Store) -> InputOut:
    return await _set_input(store, str(user.id), str(body.viewer))


@router.post("/giveback", response_model=InputOut, responses={**_403, **_409, **_503})
async def give_back(body: InputIn, user: CurrentUser, store: Store) -> InputOut:
    return await _set_input(store, str(user.id), None, str(body.viewer))
