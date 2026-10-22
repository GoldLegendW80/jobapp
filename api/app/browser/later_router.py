"""A later sign in is bound to authenticated ownership, a registered module and one lease."""

import asyncio
import json
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError

from app import queue
from app.browser import later_handoff as later
from app.browser.address import check_address
from app.browser.client import BrowserError
from app.browser.leases import StoreUnavailableError
from app.browser.router import CurrentUser, SessionOut, Store, _lease_out, _queue_out, no_store

router = APIRouter(prefix="/api/browser/handoff/later", tags=["browser"])


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    module: str = Field(min_length=1, max_length=40, pattern=r"^[a-z_]{1,40}$")
    ref: uuid.UUID


def handler(body: Reference) -> later.Handler:
    found = later.HANDLERS.get(body.module)
    if found is None:
        raise HTTPException(404, "No sign in is waiting for you.")
    return found


async def pool(store: Store):
    try:
        return await store._pool()
    except Exception:
        raise HTTPException(503, "The browser is not available right now.") from None


def _address(request: Request, user_id: uuid.UUID, body: Reference) -> str:
    with request.app.state.sessionmaker() as db:
        address = handler(body).address(db, user_id, body.ref)
    if address is None:
        raise HTTPException(404, "No sign in is waiting for you.")
    checked = check_address(address)
    if checked is None:
        raise HTTPException(422, "The sign in address is no longer valid.")
    return checked


def _signed_in(request: Request, user_id: uuid.UUID, body: Reference) -> bool:
    with request.app.state.sessionmaker() as db:
        changed = handler(body).signed_in(db, user_id, body.ref, request.app.state.clock())
        db.commit()
        return changed


async def _binding(store: Store, user: str, body: Reference) -> dict:
    redis = await pool(store)
    try:
        raw = await redis.get(later.PREFIX + user)
    except RedisError:
        raise HTTPException(503, "The browser is not available right now.") from None
    try:
        binding = json.loads(raw) if raw else None
    except (ValueError, TypeError):
        binding = None
    if (
        not isinstance(binding, dict)
        or binding.get("module") != body.module
        or binding.get("ref") != str(body.ref)
    ):
        raise HTTPException(404, "No sign in is waiting for you.")
    return binding


@router.post("", response_model=SessionOut)
async def start(
    body: Reference, request: Request, response: Response, user: CurrentUser, store: Store
) -> SessionOut:
    no_store(response)
    address = await asyncio.to_thread(_address, request, user.id, body)
    redis = await pool(store)
    try:
        view = await store.current(str(user.id))
        if view.lease is not None:
            binding = await _binding(store, str(user.id), body)
            if binding.get("lease") != view.lease.lease_id or binding.get("done"):
                raise HTTPException(409, "Finish your current browser session first.")
            return SessionOut(lease=_lease_out(view.lease, store.clock()), queue=None)
        got = await store.acquire(str(user.id), "signin", False, address=address)
        if got.lease is None:
            response.status_code = 202
            return SessionOut(lease=None, queue=_queue_out(got.waiting) if got.waiting else None)
        await redis.set(
            later.PREFIX + str(user.id),
            json.dumps(
                {
                    "module": body.module,
                    "ref": str(body.ref),
                    "lease": got.lease.lease_id,
                    "kind": "session",
                }
            ),
            ex=store.session_max_s + 60,
        )
        response.status_code = 201
        return SessionOut(lease=_lease_out(got.lease, store.clock()), queue=None)
    except (BrowserError, StoreUnavailableError, RedisError):
        raise HTTPException(503, "The browser is not available right now.") from None


async def _end(
    action: Literal["done", "cancel"],
    body: Reference,
    request: Request,
    user: CurrentUser,
    store: Store,
) -> Response:
    handler(body)
    user_id = str(user.id)
    binding = await _binding(store, user_id, body)
    if binding.get("done"):
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    # Validate the owning module again before ending the person's browser or queuing any work.
    await asyncio.to_thread(_address, request, user.id, body)
    redis = await pool(store)
    try:
        view = await store.current(user_id)
        lease = view.lease
        if lease is None or lease.lease_id != binding["lease"]:
            raise HTTPException(409, "The sign in session ended. Open it again to continue.")
        if binding.get("kind") == "handoff":
            await store.client.handoff(lease.lease_id, action)
        await store.release(
            user_id, lease.lease_id, reason="signed_in" if action == "done" else "signin_cancelled"
        )
        if action == "cancel":
            await redis.delete(later.PREFIX + user_id)
        else:
            changed = await asyncio.to_thread(_signed_in, request, user.id, body)
            if changed:
                owner = handler(body)
                await request.app.state.queue.enqueue(
                    owner.job_name, str(body.ref), queue=queue.APPLY, job_id=owner.job_id(body.ref)
                )
            await redis.set(
                later.PREFIX + user_id,
                json.dumps({**binding, "done": True}),
                ex=store.session_max_s,
            )
    except (BrowserError, StoreUnavailableError, RedisError, queue.QueueUnavailableError):
        raise HTTPException(503, "The browser is not available right now.") from None
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/done", status_code=204)
async def done(body: Reference, request: Request, user: CurrentUser, store: Store) -> Response:
    return await _end("done", body, request, user, store)


@router.post("/cancel", status_code=204)
async def cancel(body: Reference, request: Request, user: CurrentUser, store: Store) -> Response:
    return await _end("cancel", body, request, user, store)
