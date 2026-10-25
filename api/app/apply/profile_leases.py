"""Bind an owned profile run to its lease without retaining tokens or capabilities."""

import json
import uuid

from app.browser import later_handoff

PREFIX = "jobapp:site-profile:lease:"


def key(user_id: uuid.UUID, run_id: uuid.UUID) -> str:
    return f"{PREFIX}{user_id}:{run_id}"


async def bind(store, user_id: uuid.UUID, run_id: uuid.UUID, lease_id: str) -> None:
    redis = await store._pool()
    await redis.set(key(user_id, run_id), lease_id, ex=store.session_max_s + 1000)


async def clear(store, user_id: uuid.UUID, run_id: uuid.UUID) -> None:
    redis = await store._pool()
    await redis.delete(key(user_id, run_id))


async def cancel_bound(store, user_id: uuid.UUID, run_id: uuid.UUID) -> bool:
    redis = await store._pool()
    raw = await redis.get(key(user_id, run_id))
    lease_id = raw.decode() if isinstance(raw, bytes) else raw
    later_raw = await redis.get(later_handoff.PREFIX + str(user_id))
    binding = json.loads(later_raw) if later_raw else {}
    later_owned = binding.get("module") == "site_profile" and binding.get("ref") == str(run_id)
    if lease_id is None and later_owned:
        lease_id = binding.get("lease")
    view = await store.current(str(user_id))
    lease = view.lease
    if lease is None or lease_id is None or lease.lease_id != lease_id:
        return False
    if lease.state == "waiting_person":
        await store.client.handoff(lease.lease_id, "cancel")
    await store.release(str(user_id), lease.lease_id, reason="profile_cancelled")
    await redis.delete(key(user_id, run_id))
    if later_owned:
        await redis.delete(later_handoff.PREFIX + str(user_id))
    return True
