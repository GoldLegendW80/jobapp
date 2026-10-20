"""DELETE /api/browser/profile: "Forget all logins" (browser B7).

It ends the person's lease, whatever its purpose, then deletes the person's profile row: B3's
trigger queues the directory's removal, the directory is removed at once when browserd can
(app/browser/removals.py, `drain_one`) and by the worker's cron otherwise, and the next lease
makes a new row, so it starts on a new empty directory under a new uid whatever is left of the
old one. The answer is 204, also when there was nothing to forget (a second click). The person's
places in the queue are kept: what waits for the browser then starts signed out.

A person may forget FORGETS_PER_DAY times a UTC day (app/browser/leases.py counts them): each
Forget followed by a lease spends one of browserd's 40,000 uids for good (a uid never serves a
second profile): at 3 a day, ten people would need more than three years to spend them. Past it
the answer is 429 `forget_limit` and nothing is ended or deleted; a Forget with nothing to forget
spends none.

The user comes from the session (CSRF on the write); no id is in the path, so nobody can forget
another person's logins. 503 when Redis cannot be reached: nothing is deleted then.
"""

import contextlib

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.browser import profiles, removals
from app.browser.client import BrowserError
from app.browser.leases import StoreUnavailableError
from app.browser.router import UNAVAILABLE, CurrentUser, Store, no_store
from app.identity.schemas import ErrorOut

router = APIRouter(prefix="/api/browser", tags=["browser"], dependencies=[Depends(no_store)])

FORGETS_PER_DAY = 3
FORGET_LIMIT = f"You can forget your logins {FORGETS_PER_DAY} times a day. Try again tomorrow."


@router.delete(
    "/profile",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        429: {"model": ErrorOut, "description": FORGET_LIMIT},
        503: {"model": ErrorOut, "description": "Redis cannot be reached"},
    },
)
async def forget_logins(request: Request, user: CurrentUser, store: Store) -> Response:
    try:
        spent = await store.take_forget(str(user.id), FORGETS_PER_DAY)
        if spent is None:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": FORGET_LIMIT, "code": "forget_limit"},
                headers={"Cache-Control": "no-store"},
            )
        await store.release(str(user.id), reason="forget")
    except StoreUnavailableError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE) from None
    sessions = request.app.state.sessionmaker
    gone = await run_in_threadpool(profiles.remove, sessions, user.id)
    if gone is None:
        await store.give_back_forget(spent)
    if gone is not None and store.client.configured:
        # When browserd does not answer, the worker's cron removes it later.
        with contextlib.suppress(BrowserError):
            await removals.drain_one(sessions, store.client, gone)
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})
