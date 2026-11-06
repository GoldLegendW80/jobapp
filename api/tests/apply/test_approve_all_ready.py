"""Approve all ready (owner, 2026-11-05, answer 6; `approval_router.approve_ready`), on a real
Postgres at head: the person's one click approves exactly the items the confirmation named, each
through the approve route's own path, so the gate decides each one and its audit trail is the one
a single approval writes. An item changed or no longer waiting since the confirmation opened is
left out with its sentence. Every account is `e2e-*@example.org`; the companies are invented.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import quote

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.apply import gate
from app.apply.models import Application, ApplicationEvent, Authorisation, State
from app.main import create_app
from app.queue import apply_submit_job_id
from tests.apply.a6_support import FakeQueue
from tests.apply.conftest import AT, Person
from tests.apply.test_a6_gate import _password
from tests.apply.test_a12_likes import SECRET_KEY, filled, form, like, never_apply, posting
from tests.conftest import make_settings
from tests.identity.api_helpers import Api

PATH = "/api/apply/queue/approve"


@pytest.fixture
def api_app(migrated_url: str, forms_base_url: str) -> FastAPI:
    settings = make_settings(
        migrated_url, secret_key=SECRET_KEY, apply_stub_base_url=forms_base_url
    )
    app = create_app(settings, clock=lambda: AT)
    app.state.queue = FakeQueue()
    return app


def _ready(
    db: Session, factory: sessionmaker[Session], person: Person, stub: str, *titles: str
) -> list[uuid.UUID]:
    found = []
    for title in titles:
        job = posting(db, person.user_id, form(stub), title=title)
        application = like(db, person, job).application.id
        assert filled(factory, application, stub) == "ready"
        found.append(application)
    return found


def _trail(db: Session, application_id: uuid.UUID) -> list[tuple[str, list[str], Any]]:
    """The events a decision writes, with the keys of their data and the outcome."""
    rows = db.execute(
        sa.select(ApplicationEvent.kind, ApplicationEvent.data)
        .where(
            ApplicationEvent.application_id == application_id,
            ApplicationEvent.kind.in_(("authorisation", "decision")),
        )
        .order_by(ApplicationEvent.id)
    ).all()
    return [(kind, sorted(data), data.get("outcome")) for kind, data in rows]


def test_approve_all_ready_approves_exactly_what_the_confirmation_named(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    person_b: Person,
    forms_base_url: str,
    api_app: FastAPI,
) -> None:
    stub = forms_base_url
    _password(factory, person_a, person_b)
    single, first, changed, gone, unnamed = _ready(
        db,
        factory,
        person_a,
        stub,
        "Robotics Engineer",
        "ML Engineer",
        "Data Engineer",
        "Platform Engineer",
        "Agents Engineer",
    )
    [theirs] = _ready(db, factory, person_b, stub, "Robotics Engineer")
    with TestClient(api_app, follow_redirects=False) as client:
        api_app.state.queue = queue = FakeQueue()
        api = Api(client)
        token, token_b = api.login_token(person_a.email), api.login_token(person_b.email)

        def shown(application_id: uuid.UUID, as_token: str = token) -> str:
            view = api.get(f"/api/applications/{application_id}/approval", token=as_token)
            return str(view.json()["answers_hash"])

        # The single approval, for its trail.
        assert (
            api.post(
                f"/api/applications/{single}/approve",
                token=token,
                json={"answers_hash": shown(single)},
            ).status_code
            == 200
        )

        # The confirmation opens on four items; then one changes and one is rejected elsewhere.
        named = {one: shown(one) for one in (first, changed, gone)}
        named[theirs] = shown(theirs, token_b)
        view = api.get(f"/api/applications/{changed}/approval", token=token).json()
        field = next(a for a in view["answers"] if a["kind"] in ("text", "textarea"))
        edit = api.request(
            "PUT",
            f"/api/applications/{changed}/answers/{quote(field['field_key'], safe='')}",
            token=token,
            json={"value": "Another answer, typed after the confirmation opened."},
        )
        assert edit.status_code == 200
        assert api.post(f"/api/applications/{gone}/reject", token=token).status_code == 200

        body = {
            "items": [
                {"application_id": str(one), "answers_hash": sent} for one, sent in named.items()
            ]
        }
        answer = api.post(PATH, token=token, json=body)
        assert answer.status_code == 200
        assert answer.headers["cache-control"] == "no-store"
        out = answer.json()
        results = {r["application_id"]: r for r in out["results"]}
        assert [r["application_id"] for r in out["results"]] == [str(one) for one in named]
        assert (out["approved"], out["left_out"]) == (1, 3)
        assert results[str(first)] == {
            "application_id": str(first),
            "approved": True,
            "state": "approved",
            "code": None,
            "detail": None,
        }
        stale = results[str(changed)]
        assert (stale["approved"], stale["code"], stale["state"]) == (False, "stale", "ready")
        assert stale["detail"] == gate.SENTENCES["stale"]
        assert (results[str(gone)]["code"], results[str(gone)]["detail"]) == (
            "not_ready",
            gate.SENTENCES["not_ready"],
        )
        # Another person's item is not found, as a missing one.
        assert (results[str(theirs)]["approved"], results[str(theirs)]["code"]) == (
            False,
            "not_found",
        )

    db.expire_all()
    states = {one: db.get(Application, one).state for one in (first, changed, gone, unnamed)}  # type: ignore[union-attr]
    assert states == {
        first: State.approved,
        changed: State.ready,
        gone: State.cancelled,
        unnamed: State.ready,  # never named: never approved
    }
    assert db.get(Application, theirs).state is State.ready  # type: ignore[union-attr]
    # One submit lease per approval, the single one and the batch's one.
    submits = [job_id for function, _, _, job_id in queue.enqueued if function == "apply_submit"]
    assert sorted(submits) == sorted(apply_submit_job_id(one) for one in (single, first))
    # The batch writes the trail a single approval writes, and nothing for what it left out.
    assert _trail(db, first) == _trail(db, single)
    assert [kind for kind, _, _ in _trail(db, first)] == ["authorisation", "decision"]
    for left in (changed, unnamed, theirs):
        assert _trail(db, left) == []
    assert (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(Authorisation)
            .where(Authorisation.application_id.in_((changed, gone, unnamed, theirs)))
        )
        == 0
    )


def test_the_gate_decides_each_item_and_one_refusal_fails_alone(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    forms_base_url: str,
    api_app: FastAPI,
) -> None:
    stub = forms_base_url
    _password(factory, person_a)
    kept, refused = _ready(db, factory, person_a, stub, "Robotics Engineer", "Warehouse Analyst")
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        token = api.login_token(person_a.email)
        named = [
            {
                "application_id": str(one),
                "answers_hash": api.get(f"/api/applications/{one}/approval", token=token).json()[
                    "answers_hash"
                ],
            }
            for one in (refused, kept)
        ]
        # A rule of the person, saved after the confirmation opened: the gate refuses that one.
        never_apply(db, person_a.user_id, "No analyst roles: analyst (example rule)")
        out = api.post(PATH, token=token, json={"items": named}).json()
    first, second = out["results"]
    assert (first["approved"], first["code"], first["state"]) == (
        False,
        "never_apply_role",
        "blocked",
    )
    assert first["detail"] == gate.SENTENCES["never_apply_role"]
    assert (second["approved"], second["state"]) == (True, "approved")
    db.expire_all()
    assert db.get(Application, refused).state is State.blocked  # type: ignore[union-attr]
    # The refusal is the gate's own decision event, as for a single approval.
    assert [outcome for kind, _, outcome in _trail(db, refused) if kind == "decision"] == [
        "refused"
    ]


def test_the_body_names_each_item_once_and_the_route_is_the_persons(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    api_app: FastAPI,
) -> None:
    _password(factory, person_a)
    one = {"application_id": str(uuid.uuid4()), "answers_hash": "0" * gate.HASH_HEX}
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        token = api.login_token(person_a.email)
        assert api.post(PATH, token=token, json={"items": [one, one]}).status_code == 422
        assert api.post(PATH, token=token, json={"items": []}).status_code == 422
        short = {**one, "answers_hash": "0"}
        assert api.post(PATH, token=token, json={"items": [short]}).status_code == 422
        assert api.post(PATH, token=token, json={"items": [one], "all": True}).status_code == 422
        too_many = [
            {"application_id": str(uuid.uuid4()), "answers_hash": "0" * gate.HASH_HEX}
            for _ in range(201)
        ]
        assert api.post(PATH, token=token, json={"items": too_many}).status_code == 422
        assert api.post(PATH, token=token, json={"items": [one]}, csrf=False).status_code == 403
        assert api.post(PATH, json={"items": [one]}).status_code == 401
        missing = api.post(PATH, token=token, json={"items": [one]}).json()
    assert missing["results"][0]["code"] == "not_found"
