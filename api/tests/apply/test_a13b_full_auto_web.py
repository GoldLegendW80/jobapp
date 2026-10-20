"""What the Full auto pages read (apply A13b; tasks/apply/todo.md, "A13b"): the queue's rehearsals
and held items with the end of their hold and the server's time, the job page's countdown, the
settings the card shows, and the daily summary. On a real Postgres, with A13a's fill and submit
jobs on an injected clock; the api's own clock is fixed at `AT`."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.apply import caps
from app.apply.models import Application, Mode, State
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import application
from tests.apply.test_a13_full_auto import (
    HOLD,
    HOUR,
    _fit,
    api,
    filled,
    held,
    job,
    rehearsed,
    stub,
    turn_on,
)
from tests.identity.api_helpers import Api

__all__ = ["api", "stub"]


def queue(api: Api, person: Person) -> dict:
    found = api.get("/api/apply/queue", token=api.login_token(person.email))
    assert found.status_code == 200
    return found.json()


def item(body: dict, app_id: uuid.UUID) -> dict | None:
    return next((one for one in body["items"] if one["id"] == str(app_id)), None)


def at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def test_a_rehearsal_waits_for_the_person_until_answered_no(
    api: Api, db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = rehearsed(db, factory, person_a, stub)
    body = queue(api, person_a)
    shown = item(body, app_id)
    assert shown is not None
    assert (shown["state"], shown["rehearsal"], shown["hold_until"]) == ("rehearsed", True, None)
    assert body["waiting_on_you"] == 1
    token = api.login_token(person_a.email)
    assert api.get("/api/apply/queue/count", token=token).json()["waiting_on_you"] == 1

    answer = api.post(f"/api/applications/{app_id}/rehearsal", token=token, json={"answer": "no"})
    assert answer.status_code == 200
    after = queue(api, person_a)
    assert (item(after, app_id), after["waiting_on_you"]) == (None, 0)


def test_a_rehearsal_answered_yes_stays_in_the_queue_ready(
    api: Api, db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = rehearsed(db, factory, person_a, stub)
    token = api.login_token(person_a.email)
    answer = api.post(f"/api/applications/{app_id}/rehearsal", token=token, json={"answer": "yes"})
    assert answer.status_code == 200
    shown = item(queue(api, person_a), app_id)
    assert shown is not None
    assert (shown["state"], shown["mode"], shown["rehearsal"]) == ("ready", "review", False)


def test_a_review_first_dry_run_is_never_a_rehearsal_of_the_queue(
    api: Api, db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = rehearsed(db, factory, person_a, stub)
    db.execute(sa.update(Application).where(Application.id == app_id).values(mode=Mode.review))
    db.commit()
    body = queue(api, person_a)
    assert (item(body, app_id), body["waiting_on_you"]) == (None, 0)


def test_a_held_item_carries_the_end_of_its_hold_and_the_servers_time(
    api: Api,
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    person_b: Person,
    stub: str,
) -> None:
    turn_on(db, person_a)
    app_id = held(db, factory, person_a, stub)
    body = queue(api, person_a)
    shown = item(body, app_id)
    assert shown is not None
    assert (shown["state"], shown["rehearsal"]) == ("held", False)
    assert at(shown["hold_until"]) == AT + HOLD
    assert at(body["now"]) == AT
    assert body["waiting_on_you"] == 0  # under way: the app works on it

    token = api.login_token(person_a.email)
    detail = api.get(f"/api/applications/{app_id}", token=token).json()
    assert (at(detail["hold_until"]), at(detail["now"])) == (AT + HOLD, AT)
    other = api.get(f"/api/applications/{app_id}", token=api.login_token(person_b.email))
    assert other.status_code == 404
    assert item(queue(api, person_b), app_id) is None

    cancelled = api.post(f"/api/applications/{app_id}/cancel", token=token)
    assert (cancelled.status_code, cancelled.json()["state"]) == (200, "cancelled")
    gone = api.get(f"/api/applications/{app_id}", token=token).json()
    assert gone["hold_until"] is None
    assert item(queue(api, person_a), app_id) is None
    _, form = job(factory, stub, app_id, AT + HOLD)
    assert form.gate_clicks == []
    assert application(db, app_id).state is State.cancelled


def test_the_summary_counts_submitted_held_and_sent_to_review_with_the_reason(
    api: Api,
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    person_b: Person,
    stub: str,
) -> None:
    token = api.login_token(person_a.email)
    empty = api.get("/api/apply/summary", token=token)
    assert empty.status_code == 200
    assert empty.headers["cache-control"] == "no-store"
    since = caps.day_start(AT, caps.settings_of(db, person_a.user_id).timezone)
    first = empty.json()
    assert at(first["since"]) == since
    assert (first["submitted"], first["held"], first["sent_to_review"]) == (0, 0, [])

    turn_on(db, person_a)
    app_id = held(db, factory, person_a, stub)
    body = api.get("/api/apply/summary", token=token).json()
    assert (body["submitted"], body["held"]) == (0, 1)

    assert job(factory, stub, app_id, AT + HOLD)[0] == "submitted"
    body = api.get("/api/apply/summary", token=token).json()
    assert (body["submitted"], body["held"], body["sent_to_review"]) == (1, 0, [])
    other = api.get("/api/apply/summary", token=api.login_token(person_b.email)).json()
    assert (other["submitted"], other["held"]) == (0, 0)


def test_the_summary_names_why_an_item_went_to_review(
    api: Api, db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = filled(db, factory, person_a, stub)
    _fit(db, person_a, 10)
    db.commit()
    assert job(factory, stub, app_id, AT + HOUR)[0] == "below_min_fit"
    body = api.get("/api/apply/summary", token=api.login_token(person_a.email)).json()
    [reason] = body["sent_to_review"]
    assert (reason["code"], reason["count"]) == ("below_min_fit", 1)
    assert reason["sentence"].startswith("This posting's fit is below your threshold")
    shown = item(queue(api, person_a), app_id)
    assert shown is not None
    assert shown["reason"]["code"] == "below_min_fit"


def test_the_settings_name_the_hold_floor_and_the_rehearsals(
    api: Api, db: Session, person_a: Person
) -> None:
    token = api.login_token(person_a.email)
    on = api.request(
        "PUT", "/api/apply/settings", token=token, json={"mode_after_like": "full_auto"}
    ).json()
    assert (on["hold_s"], on["hold_min_s"], on["rehearsals"], on["rehearsals_done"]) == (
        900,
        900,
        3,
        0,
    )
    assert at(on["full_auto_since"]) == AT
    # A hold stored below a floor raised since reads as the floor, the hold in force.
    db.execute(
        sa.text("UPDATE apply_settings SET hold_s = 60 WHERE user_id = :u"),
        {"u": person_a.user_id},
    )
    db.commit()
    assert api.get("/api/apply/settings", token=token).json()["hold_s"] == 900


def test_a_full_auto_item_ready_for_the_apps_decision_is_under_way(
    api: Api, db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = filled(db, factory, person_a, stub)
    body = queue(api, person_a)
    shown = item(body, app_id)
    assert shown is not None
    assert (shown["state"], shown["mode"], shown["rehearsal"]) == ("ready", "full_auto", False)
    assert body["waiting_on_you"] == 0
    token = api.login_token(person_a.email)
    assert api.get("/api/apply/queue/count", token=token).json()["waiting_on_you"] == 0
