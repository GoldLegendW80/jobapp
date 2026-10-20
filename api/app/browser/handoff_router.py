"""The person's answers to a sign in hand off (browser B12a; browser/browserd/handoff.py):

- POST /api/browser/handoff/done, "I'm signed in": browserd closes the Chromium the person used
  (no debugging port) and starts it again with CDP on the same profile at the noted address,
  then the lease resumes here too (`Leases.resume`), with the active time it had left. 204. A
  second click restarts nothing and answers the same.
- POST /api/browser/handoff/cancel, "Cancel": the lease is released `signin_cancelled` and the
  work waits as `needs_person` in its module. 204.

Both answer 404 NO_HANDOFF when the person's own lease is not an agent's lease waiting for them
(no lease, a person's own session, a lease of someone else: there is no id in the path, the user
comes from the session), and 503 when browserd or Redis cannot be reached. The state itself
(`waiting_person: signin`, the site, the minutes left) is GET /api/browser's (app/browser/
router.py); the app shell's dot is apply A10a's.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.browser.client import BrowserError
from app.browser.leases import Lease, Leases, StoreUnavailableError
from app.browser.router import UNAVAILABLE, CurrentUser, Store, no_store
from app.identity.schemas import ErrorOut

NO_HANDOFF = "No sign in is waiting for you."

router = APIRouter(
    prefix="/api/browser/handoff", tags=["browser"], dependencies=[Depends(no_store)]
)

_errors = {
    404: {"model": ErrorOut, "description": NO_HANDOFF},
    503: {"model": ErrorOut, "description": UNAVAILABLE},
}


def _no_handoff() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, NO_HANDOFF)


def _unavailable() -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)


async def _agents_lease(store: Leases, user_id: str) -> Lease:
    """The person's own lease when an agent drives it (a hand off can only be on one)."""
    try:
        view = await store.current(user_id)
    except StoreUnavailableError:
        raise _unavailable() from None
    lease = view.lease
    if lease is None or not lease.automation or lease.state == "starting":
        raise _no_handoff()
    return lease


async def _tell(store: Leases, lease: Lease, action: str) -> None:
    try:
        await store.client.handoff(lease.lease_id, action)
    except BrowserError as exc:
        if exc.code in ("ended", "refused"):
            raise _no_handoff() from None
        raise _unavailable() from None


@router.post("/done", status_code=status.HTTP_204_NO_CONTENT, responses=_errors)
async def handoff_done(user: CurrentUser, store: Store) -> Response:
    user_id = str(user.id)
    lease = await _agents_lease(store, user_id)
    await _tell(store, lease, "done")
    try:
        await store.resume(user_id, lease.lease_id)
    except StoreUnavailableError:
        raise _unavailable() from None
    return _no_content()


@router.post("/cancel", status_code=status.HTTP_204_NO_CONTENT, responses=_errors)
async def handoff_cancel(user: CurrentUser, store: Store) -> Response:
    user_id = str(user.id)
    lease = await _agents_lease(store, user_id)
    await _tell(store, lease, "cancel")
    try:
        await store.release(user_id, lease.lease_id, reason="signin_cancelled")
    except StoreUnavailableError:
        raise _unavailable() from None
    return _no_content()


def _no_content() -> Response:
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})
