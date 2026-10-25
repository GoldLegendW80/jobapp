"""Cancel releases only the exact owned run's lease, including a waiting sign in."""

import asyncio
import json
import uuid
from types import SimpleNamespace

from app.apply import profile_leases
from app.browser import later_handoff
from app.browser.leases import Lease
from tests.apply.conftest import AT
from tests.apply.test_a10b_later import Store


def waiting(store, user, lease_id="profile-lease"):
    store.lease = Lease(
        lease_id=lease_id,
        user_id=str(user),
        purpose="profile_edit",
        automation=True,
        profile_id="fixture",
        max_s=900,
        idle_s=300,
        started_at=AT.timestamp(),
        active_since=None,
        last_input=AT.timestamp(),
        state="waiting_person",
    )
    calls = []

    async def handoff(lease, action):
        calls.append((lease, action))

    store.client = SimpleNamespace(handoff=handoff)
    return calls


def test_cancel_wakes_only_bound_profile_signin():
    store = Store()
    user, run = uuid.uuid4(), uuid.uuid4()
    calls = waiting(store, user)
    asyncio.run(profile_leases.bind(store, user, run, "profile-lease"))
    assert asyncio.run(profile_leases.cancel_bound(store, user, run)) is True
    assert calls == [("profile-lease", "cancel")]
    assert store.released == [(str(user), "profile-lease", "profile_cancelled")]


def test_cancel_does_not_release_unrelated_live_session():
    store = Store()
    user, run = uuid.uuid4(), uuid.uuid4()
    calls = waiting(store, user, "unrelated-session")
    asyncio.run(profile_leases.bind(store, user, run, "old-profile-lease"))
    assert asyncio.run(profile_leases.cancel_bound(store, user, run)) is False
    assert calls == []
    assert store.released == []


def test_cancel_releases_owned_later_signin_even_after_db_state_changes():
    store = Store()
    user, run = uuid.uuid4(), uuid.uuid4()
    calls = waiting(store, user)
    store.values[later_handoff.PREFIX + str(user)] = json.dumps(
        {"module": "site_profile", "ref": str(run), "lease": "profile-lease", "kind": "session"}
    )
    assert asyncio.run(profile_leases.cancel_bound(store, user, run)) is True
    assert calls == [("profile-lease", "cancel")]
    assert later_handoff.PREFIX + str(user) not in store.values
