"""The later handoff routes with a real owned application, without a real sign in provider."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.apply import signin
from app.apply.models import Mode, State
from app.browser.later_router import Reference, router
from app.browser.leases import Lease, View
from app.browser.router import get_leases
from app.identity import deps
from tests.apply.conftest import AT
from tests.apply.test_a5_run import application, started


class Store:
    session_max_s = 1800

    def __init__(self):
        self.values, self.lease, self.opened, self.released = {}, None, [], []

    def clock(self):
        return AT.timestamp()

    async def _pool(self):
        return self

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, **kwargs):
        self.values[key] = value

    async def delete(self, key):
        self.values.pop(key, None)

    async def current(self, user):
        lease = self.lease if self.lease and self.lease.user_id == user else None
        return View(lease=lease, held_by_other=False, waiting=())

    async def acquire(self, user, purpose, automation, *, address):
        self.opened.append((user, purpose, automation, address))
        self.lease = Lease(
            lease_id="later-lease",
            user_id=user,
            purpose=purpose,
            automation=automation,
            profile_id="fixture",
            max_s=1800,
            idle_s=300,
            started_at=self.clock(),
            active_since=self.clock(),
            last_input=self.clock(),
        )
        return SimpleNamespace(lease=self.lease, waiting=None)

    async def release(self, user, lease, reason):
        self.released.append((user, lease, reason))
        self.lease = None


@pytest.fixture
def later(db, factory, person_a):
    ref = started(db, person_a)
    item = application(db, ref)
    item.state, item.needs, item.mode = State.needs_person, "signin", Mode.full_auto
    db.commit()
    store = Store()
    app = FastAPI()
    app.include_router(router)
    app.state.sessionmaker, app.state.clock = factory, lambda: AT
    queued = []

    async def enqueue(name, argument, **kwargs):
        queued.append((name, argument, kwargs))

    app.state.queue = SimpleNamespace(enqueue=enqueue)
    app.dependency_overrides[get_leases] = lambda: store
    app.dependency_overrides[deps.current_user] = lambda: SimpleNamespace(id=person_a.user_id)
    signin.register()
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            app=app,
            store=store,
            queued=queued,
            ref=ref,
            body={"module": "application", "ref": str(ref)},
        )


def test_later_done_ends_the_person_session_and_queues_exactly_one_review_fill(later, db, person_a):
    assert later.client.post("/api/browser/handoff/later", json=later.body).status_code == 201
    assert later.store.opened[0][:3] == (str(person_a.user_id), "signin", False)
    assert later.store.opened[0][3] == application(db, later.ref).form_url
    for _ in range(2):
        assert (
            later.client.post("/api/browser/handoff/later/done", json=later.body).status_code == 204
        )
    assert application(db, later.ref).state is State.queued
    assert application(db, later.ref).mode is Mode.review
    assert len(later.queued) == 1
    assert later.queued[0][0:2] == ("apply_fill", str(later.ref))


def _asked(db, ref, kind: str, state: State = State.needs_person) -> None:
    """The apply agent's question with nobody watching (A19): the run ended, the item waits."""
    from app.apply import applier

    item = application(db, ref)
    item.state, item.mode = state, Mode.direct
    item.needs = applier.QUESTION if state is State.needs_person else None
    item.submitted_at = AT if state is State.submit_unknown else None
    label = {"action": "Verification code", "fact": "Years of Rust"}[kind]
    applier._ask(db, item, {"label": label, "kind": kind, "host": "jobs.example.org"})
    db.commit()


@pytest.mark.parametrize("state", [State.needs_person, State.submit_unknown])
def test_an_action_the_agent_asked_for_opens_the_site_s_own_session(later, db, person_a, state):
    """A code, a check or a password the apply agent asked for is the person's on the site
    (SPEC-apply, "the site's own session for an action"), also after a control that may have
    sent the form; their own "I did it, go on" still answers the question."""
    _asked(db, later.ref, "action", state)
    assert later.client.post("/api/browser/handoff/later", json=later.body).status_code == 201
    assert later.store.opened == [
        (str(person_a.user_id), "signin", False, application(db, later.ref).form_url)
    ]
    done = later.client.post("/api/browser/handoff/later/done", json=later.body)
    assert done.status_code == 204
    assert later.store.released == [(str(person_a.user_id), "later-lease", "signed_in")]
    db.expire_all()
    assert application(db, later.ref).state is state  # waits for the person's answer
    assert later.queued == []


def test_a_fact_question_or_another_person_opens_no_session(later, db, person_b):
    _asked(db, later.ref, "fact")
    assert later.client.post("/api/browser/handoff/later", json=later.body).status_code == 404
    _asked(db, later.ref, "action")
    later.app.dependency_overrides[deps.current_user] = lambda: SimpleNamespace(id=person_b.user_id)
    assert later.client.post("/api/browser/handoff/later", json=later.body).status_code == 404
    assert later.store.opened == []


def test_cancel_keeps_the_application_waiting(later, db):
    later.client.post("/api/browser/handoff/later", json=later.body)
    assert (
        later.client.post("/api/browser/handoff/later/cancel", json=later.body).status_code == 204
    )
    assert application(db, later.ref).state is State.needs_person
    assert application(db, later.ref).needs == "signin"
    assert later.queued == []


def test_another_person_cannot_open_or_callback_the_item(later, person_b):
    later.client.post("/api/browser/handoff/later", json=later.body)
    later.app.dependency_overrides[deps.current_user] = lambda: SimpleNamespace(id=person_b.user_id)
    for suffix in ("", "/done", "/cancel"):
        assert (
            later.client.post("/api/browser/handoff/later" + suffix, json=later.body).status_code
            == 404
        )
    assert later.queued == []
    assert later.store.released == []


def test_expired_session_keeps_the_item_waiting_and_can_start_again(later, db):
    later.client.post("/api/browser/handoff/later", json=later.body)
    later.store.lease = None
    assert later.client.post("/api/browser/handoff/later/done", json=later.body).status_code == 409
    assert application(db, later.ref).state is State.needs_person
    assert later.client.post("/api/browser/handoff/later", json=later.body).status_code == 201


@pytest.mark.parametrize("purpose", ["apply_fill", "look", "signin"])
def test_a_browser_busy_with_other_work_starts_nothing_and_the_item_keeps_waiting(
    later, db, person_a, purpose
):
    later.store.lease = Lease(
        lease_id="other-work",
        user_id=str(person_a.user_id),
        purpose=purpose,
        automation=purpose == "apply_fill",
        profile_id="fixture",
        max_s=1800,
        idle_s=300,
        started_at=later.store.clock(),
        active_since=later.store.clock(),
        last_input=later.store.clock(),
    )
    response = later.client.post("/api/browser/handoff/later", json=later.body)
    assert response.status_code == 409
    assert "busy" in response.json()["detail"]
    assert later.store.opened == []
    assert later.store.values == {}
    assert application(db, later.ref).state is State.needs_person
    assert later.client.post("/api/browser/handoff/later/done", json=later.body).status_code == 404
    assert later.store.released == []
    assert later.queued == []


def test_invalid_saved_address_starts_nothing(later, db):
    application(db, later.ref).form_url = "javascript:alert(1)"
    db.commit()
    assert later.client.post("/api/browser/handoff/later", json=later.body).status_code == 422
    assert later.store.opened == []


@pytest.mark.parametrize(
    "module", ["a" * 41, "", "Application", "application.x", {"name": "application"}]
)
def test_later_reference_still_refuses_unregistered_module_shapes(module):
    with pytest.raises(ValidationError):
        Reference(module=module, ref=uuid.uuid4())


def test_later_reference_accepts_the_full_registered_name_shape_and_uuid():
    ref = uuid.uuid4()
    value = Reference(module="a" * 40, ref=str(ref))
    assert value.module == "a" * 40
    assert value.ref == ref
    schema = Reference.model_json_schema()
    assert schema["properties"]["module"]["maxLength"] == 40
