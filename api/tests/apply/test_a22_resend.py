"""The applications the old document rule stopped go back to sending by themselves (apply A22,
issue #777), on a real Postgres, A5's fill on A4's modelled Ashby form and A6's fake submit form.
Each stopped item is written as the gate wrote it before A21: the Like's or the click's
authorisation, the refusal `documents_review` naming it, and the hand over to Review first. Every
submission counted here is a call of the fake form's `gate_click`; no browser, no model."""

from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import worker
from app.apply import full_auto, gate, resend, submit
from app.apply.caps_models import ApplySettings
from app.apply.models import (
    Application,
    ApplicationAnswer,
    Authorisation,
    AuthorisationKind,
    Mode,
    State,
)
from app.documents.models import Kind, RenderState
from tests.apply import test_738_one_click as one_click
from tests.apply.conftest import AT, Person
from tests.apply.test_356_render_wait import land, second_version
from tests.apply.test_a5_run import WHY, application, events
from tests.apply.test_a6_gate import approve, stub
from tests.apply.test_a13_full_auto import (
    HOLD,
    HOUR,
    SECOND,
    WEEK,
    api,
    authorisations,
    filled,
    job,
    turn_on,
)
from tests.identity.api_helpers import Api

__all__ = ["api", "stub"]

STOPPED_AT = AT + HOUR
LATER = AT + 2 * HOUR


def letter_corrected(db: Session, app_id: uuid.UUID) -> None:
    """The letter as the old rule refused it: corrections by the fact check."""
    found = application(db, app_id)
    letter = found.document_versions["letter"]
    found.document_versions = {
        **found.document_versions,
        "letter": {**letter, "corrections": ["A claim the fact check changed"]},
    }
    db.commit()


def stopped_by_the_old_rule(
    db: Session, app_id: uuid.UUID, now: datetime = STOPPED_AT
) -> uuid.UUID:
    """What `gate.decide` wrote before 2026-11-09 for a Full auto or one click Apply item whose
    documents needed a look: the mode's authorisation, bound to the current hash and unspent, the
    refusal naming it, and the hand over to Review first (`full_auto.to_review`)."""
    letter_corrected(db, app_id)
    found = application(db, app_id)
    kind = gate.KIND_OF_MODE[found.mode]
    row = Authorisation(
        id=uuid.uuid4(),
        user_id=found.user_id,
        application_id=found.id,
        kind=kind,
        answers_hash=gate.current_hash(db, found),
        created_at=now,
        expires_at=found.created_at + WEEK,
    )
    db.add(row)
    db.flush()
    gate._event(db, found, "authorisation", now, authorisation_id=str(row.id), kind=kind.value)
    full_auto.to_review(db, found, "documents_review", now)
    gate._event(
        db,
        found,
        "decision",
        now,
        outcome="refused",
        code="documents_review",
        authorisation_id=str(row.id),
    )
    db.commit()
    assert (found.state, found.mode, found.error_code) == (
        State.ready,
        Mode.review,
        "documents_review",
    )
    return row.id


def full_auto_stopped(
    db: Session, factory: sessionmaker[Session], person: Person, stub: str, **on: Any
) -> uuid.UUID:
    turn_on(db, person, **on)
    app_id = filled(db, factory, person, stub)
    stopped_by_the_old_rule(db, app_id)
    return app_id


def one_click_stopped(
    db: Session, factory: sessionmaker[Session], person: Person, stub: str
) -> uuid.UUID:
    one_click.low_fit(db, person)
    app_id = one_click.filled(db, factory, person, stub)
    stopped_by_the_old_rule(db, app_id)
    return app_id


def kinds(db: Session, app_id: uuid.UUID) -> list[str]:
    return [e.kind for e in events(db, app_id)]


def resent(factory: sessionmaker[Session], now: datetime = LATER) -> int:
    return resend.run(factory, now)


# --- back to sending by itself -------------------------------------------------------------------


def test_a_full_auto_item_goes_back_and_is_sent_after_its_hold(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = full_auto_stopped(db, factory, person_a, stub)
    [like] = authorisations(db, app_id)
    assert app_id not in submit.due(db, LATER)

    assert resent(factory) == 1
    found = application(db, app_id)
    assert (found.state, found.mode, found.dry_run, found.error_code) == (
        State.ready,
        Mode.full_auto,
        False,
        None,
    )
    [moved] = [e for e in events(db, app_id) if e.kind == resend.MOVED]
    assert moved.data == {"mode": "full_auto", "authorisation_id": str(like.id)}
    assert app_id in submit.due(db, LATER)

    assert job(factory, stub, app_id, LATER)[0] == "held"
    assert app_id not in submit.due(db, LATER + HOLD - SECOND)
    written, form = job(factory, stub, app_id, LATER + HOLD)
    assert written == "submitted"
    assert [c["what"] for c in form.gate_clicks] == ["submit"]
    # The Like's own row, spent once by this click; no second authorisation written.
    [spent] = authorisations(db, app_id)
    assert (spent.id, spent.kind) == (like.id, AuthorisationKind.full_auto_like)
    assert spent.used_at == LATER


def test_a_one_click_apply_item_goes_back_and_is_decided_allowed(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = one_click_stopped(db, factory, person_a, stub)
    [click] = authorisations(db, app_id)

    assert resent(factory) == 1
    assert application(db, app_id).mode is Mode.direct
    written, form = job(factory, stub, app_id, LATER)
    assert (written, form.gate_clicks) == ("approved", [])
    decisions = [e.data for e in events(db, app_id) if e.kind == "decision"]
    assert [(d["outcome"], d["code"]) for d in decisions] == [
        ("refused", "documents_review"),
        ("approved", None),
    ]
    assert decisions[-1]["authorisation_id"] == str(click.id)
    written, form = job(factory, stub, app_id, LATER)
    assert written == "submitted"
    assert [c["what"] for c in form.gate_clicks] == ["submit"]
    assert [a.id for a in authorisations(db, app_id)] == [click.id]


def test_a_rehearsal_goes_back_as_a_rehearsal(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = full_auto_stopped(db, factory, person_a, stub, done=0)
    assert application(db, app_id).dry_run is False  # the hand over cleared it
    assert resent(factory) == 1
    assert application(db, app_id).dry_run is True
    assert job(factory, stub, app_id, LATER)[0] == "held"
    written, form = job(factory, stub, app_id, LATER)
    assert (written, form.gate_clicks) == ("rehearsed", [])


def test_the_gate_checks_everything_again_after_the_move(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    """The person paused automatic applications since: the gate refuses, the item goes back to
    Review first with that reason, and its Like is never decided a third time."""
    app_id = full_auto_stopped(db, factory, person_a, stub)
    assert resent(factory) == 1
    db.execute(
        sa.update(ApplySettings)
        .where(ApplySettings.user_id == person_a.user_id)
        .values(paused=True)
    )
    db.commit()
    written, form = job(factory, stub, app_id, LATER)
    assert (written, form.gate_clicks) == ("paused_by_you", [])
    found = application(db, app_id)
    assert (found.state, found.mode, found.error_code) == (
        State.ready,
        Mode.review,
        "paused_by_you",
    )
    assert full_auto.before_gate(db, found, LATER) == "authorisation_used"
    assert resent(factory, LATER + HOUR) == 0
    db.execute(
        sa.update(ApplySettings)
        .where(ApplySettings.user_id == person_a.user_id)
        .values(paused=False)
    )
    db.commit()
    assert approve(factory, person_a, app_id, now=LATER + HOUR).passed


# --- left waiting for the person's Approve -------------------------------------------------------


def _documents_changed(db: Session, person: Person, app_id: uuid.UUID) -> None:
    land(db, person, second_version(db, person, Kind.letter, RenderState.idle))


def _answer_edited(db: Session, person: Person, app_id: uuid.UUID) -> None:
    db.execute(
        sa.update(ApplicationAnswer)
        .where(ApplicationAnswer.application_id == app_id, ApplicationAnswer.label == WHY)
        .values(value="I edited this answer myself.", source="person")
    )
    db.commit()


def _full_auto_off(db: Session, person: Person, app_id: uuid.UUID) -> None:
    full_auto.turn(db, person.user_id, False, STOPPED_AT + SECOND)
    db.commit()


def _nothing(db: Session, person: Person, app_id: uuid.UUID) -> None:
    pass


@pytest.mark.parametrize(
    ("since", "when", "code"),
    [
        (_documents_changed, LATER, "resend_changed"),
        (_answer_edited, LATER, "resend_changed"),
        (_full_auto_off, LATER, "resend_full_auto_off"),
        (_nothing, AT + WEEK, "resend_expired"),
    ],
)
def test_an_item_whose_approval_no_longer_holds_waits_with_its_reason(
    api: Api,
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    since: Any,
    when: datetime,
    code: str,
) -> None:
    app_id = full_auto_stopped(db, factory, person_a, stub)
    since(db, person_a, app_id)

    assert resent(factory, when) == 1
    found = application(db, app_id)
    assert (found.state, found.mode, found.error_code) == (State.ready, Mode.review, code)
    [kept] = [e for e in events(db, app_id) if e.kind == resend.KEPT]
    assert kept.data["code"] == code
    assert resend.MOVED not in kinds(db, app_id)
    assert app_id not in submit.due(db, when)

    token = api.login_token(person_a.email)
    [card] = api.get("/api/apply/queue", token=token).json()["items"]
    assert card["reason"] == {"code": code, "sentence": resend.SENTENCES[code]}
    detail = api.get(f"/api/applications/{app_id}", token=token).json()
    assert resend.KEPT in [e["kind"] for e in detail["events"]]


@pytest.mark.parametrize(("after", "mode"), [(WEEK - SECOND, Mode.direct), (WEEK, Mode.review)])
def test_a_one_click_item_one_second_before_and_at_the_end_of_its_click(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str, after: Any, mode: Mode
) -> None:
    app_id = one_click_stopped(db, factory, person_a, stub)
    assert resent(factory, application(db, app_id).created_at + after) == 1
    found = application(db, app_id)
    assert found.mode is mode
    assert found.error_code == (None if mode is Mode.direct else "resend_expired")


# --- untouched -----------------------------------------------------------------------------------


# Sent: the person's Approve and the click, below (a `submitted` row needs its click's time).
@pytest.mark.parametrize("state", [State.cancelled, State.posting_closed, State.blocked])
def test_an_item_cancelled_or_closed_since_is_untouched(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str, state: State
) -> None:
    app_id = full_auto_stopped(db, factory, person_a, stub)
    db.execute(sa.update(Application).where(Application.id == app_id).values(state=state))
    db.commit()
    before = kinds(db, app_id)
    assert resent(factory) == 0
    assert kinds(db, app_id) == before
    assert application(db, app_id).mode is Mode.review


def test_an_item_the_person_approved_and_sent_since_is_untouched(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = full_auto_stopped(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id, now=LATER).passed
    assert job(factory, stub, app_id, LATER)[0] == "submitted"
    before = kinds(db, app_id)
    assert resent(factory, LATER + HOUR) == 0
    assert kinds(db, app_id) == before


def test_an_item_cancelled_by_the_person_is_untouched(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = full_auto_stopped(db, factory, person_a, stub)
    gate.cancel(db, person_a.user_id, app_id, LATER, reject=False)
    db.commit()
    assert resent(factory) == 0
    assert application(db, app_id).state is State.cancelled


def test_other_review_items_are_untouched(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    """A Full auto item sent to review for another reason, and one refused `documents_review`
    without a decision naming its Like (as a test of A21 writes it), stay as they are."""
    turn_on(db, person_a)
    app_id = filled(db, factory, person_a, stub)
    full_auto.to_review(db, application(db, app_id), "documents_review", STOPPED_AT)
    db.commit()
    assert resent(factory) == 0
    assert application(db, app_id).mode is Mode.review


# --- once ----------------------------------------------------------------------------------------


def test_a_second_run_moves_nothing(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person, stub: str
) -> None:
    moved = full_auto_stopped(db, factory, person_a, stub)
    kept = one_click_stopped(db, factory, person_b, stub)
    _answer_edited(db, person_b, kept)
    assert resent(factory) == 2
    after_first = {app_id: kinds(db, app_id) for app_id in (moved, kept)}
    assert resent(factory, LATER + HOUR) == 0
    assert {app_id: kinds(db, app_id) for app_id in (moved, kept)} == after_first
    assert (application(db, moved).mode, application(db, kept).mode) == (
        Mode.full_auto,
        Mode.review,
    )


def test_two_runs_at_once_move_an_item_once(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = full_auto_stopped(db, factory, person_a, stub)
    with ThreadPoolExecutor(2) as pool:
        done = list(pool.map(lambda _: resent(factory), range(2)))
    assert sorted(done) == [0, 1]
    assert kinds(db, app_id).count(resend.MOVED) == 1


def test_a_list_read_before_another_run_moved_the_item_moves_nothing(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    """The list is read without the locks: under them, the item is read again."""
    app_id = full_auto_stopped(db, factory, person_a, stub)
    [(found, user_id, authorisation_id)] = list(db.execute(resend.stopped()))
    db.rollback()
    assert resent(factory) == 1
    with factory() as other:
        assert resend.move(other, user_id, found, authorisation_id, LATER) is None
        other.commit()
    assert kinds(db, app_id).count(resend.MOVED) == 1


def test_the_tick_runs_it(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    class Pool:
        async def enqueue_job(self, *args: Any, **kwargs: Any) -> None:
            return None

        async def delete(self, *args: Any) -> None:
            return None

    app_id = full_auto_stopped(db, factory, person_a, stub)
    ctx = {"sessions": factory, "redis": Pool(), "clock": lambda: LATER}
    asyncio.run(worker.apply_tick(ctx))
    assert application(db, app_id).mode is Mode.full_auto
    asyncio.run(worker.apply_tick(ctx))
    assert kinds(db, app_id).count(resend.MOVED) == 1


@pytest.mark.parametrize("code", sorted(resend.SENTENCES))
def test_each_reason_reads_on_the_card_without_a_dash(code: str) -> None:
    assert gate.SENTENCES[code] == resend.SENTENCES[code]
    assert not any(dash in resend.SENTENCES[code] for dash in (" - ", "—", "–"))
