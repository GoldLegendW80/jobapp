"""Typed callbacks for work waiting for a person's later sign in, never arbitrary callables."""

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.browser.leases import StoreUnavailableError

PREFIX = "jobapp:browser:later:"


@dataclass(frozen=True)
class Handler:
    address: Callable[[Session, uuid.UUID, uuid.UUID], str | None]
    signed_in: Callable[[Session, uuid.UUID, uuid.UUID, datetime], bool]
    job_name: str
    job_id: Callable[[object], str]


HANDLERS: dict[str, Handler] = {}


async def bind_wait(store, user: str, module: str, ref: uuid.UUID, lease_id: str) -> None:
    """Retain a wait whose worker ended; a later route may finish only this exact work."""
    try:
        redis = await store._pool()
        await redis.set(
            PREFIX + user,
            json.dumps({"module": module, "ref": str(ref), "lease": lease_id, "kind": "handoff"}),
            ex=store.session_max_s + 60,
        )
    except RedisError as exc:
        raise StoreUnavailableError from exc


def register(
    module: str,
    address: Callable[[Session, uuid.UUID, uuid.UUID], str | None],
    signed_in: Callable[[Session, uuid.UUID, uuid.UUID, datetime], bool],
    *,
    job_name: str,
    job_id: Callable[[object], str],
) -> None:
    handler = Handler(address, signed_in, job_name, job_id)
    if module in HANDLERS and HANDLERS[module] != handler:
        raise ValueError("A later sign in module is already registered.")
    HANDLERS[module] = handler
