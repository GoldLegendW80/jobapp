"""Full auto, the backend (apply A13a; tasks/apply/todo.md, "A13a"), on a real Postgres, A5's fill
on A4's modelled Ashby form and A6's fake submit form: the Like, the decision, the hold, the
recheck and the click, each on an injected clock. Every submission counted here is a call of the
fake form's `gate_click`; no browser, no model, no company."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, cast

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.apply import caps, full_auto, gate, likes, service, states, submit
from app.apply.caps_models import ApplySettings, LikeMode
from app.apply.models import (
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    Authorisation,
    AuthorisationKind,
    Mode,
    State,
)
from app.jobs.models import Job
from app.main import create_app
from tests.apply import a11_support as a11
from tests.apply.a5_support import FakeAgent
from tests.apply.a6_support import FakeQueue, SubmitForm, submit_ashby
from tests.apply.conftest import AT, Person, add_posting
from tests.apply.test_a5_run import WHY, application, events, fill, located, run_ashby, services
from tests.apply.test_a6_gate import ON, SECRET_KEY, approve, run_submit, stub, submit_services
from tests.apply.test_a6_recheck import at, at_the_dwell
from tests.apply.test_start import rules
from tests.conftest import make_settings
from tests.identity.api_helpers import PASSWORD, Api

__all__ = ["stub"]

HOUR = timedelta(hours=1)
HOLD = timedelta(seconds=900)
WEEK = timedelta(days=7)
SECOND = timedelta(seconds=1)
WHY_TEXT = "I want to build agents for warehouse operations."
LOCATION = "Current location"


def turn_on(
    db: Session, person: Person, *, since: datetime = AT - HOUR, done: int = 3, **values: Any
) -> None:
    """Full auto on for `person` since `since`, `done` rehearsals answered Yes, fit 90."""
    db.add(
        ApplySettings(
            user_id=person.user_id,
            mode_after_like=LikeMode.full_auto,
            full_auto_since=since,
            rehearsals_done=done,
            updated_at=since,
            **values,
        )
    )
    db.execute(sa.update(Job).where(Job.user_id == person.user_id).values(fit=90))
    db.commit()


def liked(db: Session, person: Person, stub: str, *, now: datetime = AT) -> uuid.UUID:
    located(db, person)
    return likes.on_like(
        db, person.user_id, person.posting_id, now=now, stub_base=stub
    ).application.id


def answered(db: Session, app_id: uuid.UUID) -> None:
    """The form as a Full auto item passes it: the free text and the location from the bank."""
    for label, value in ((WHY, WHY_TEXT), (LOCATION, "Paris, France")):
        db.execute(
            sa.update(ApplicationAnswer)
            .where(ApplicationAnswer.application_id == app_id, ApplicationAnswer.label == label)
            .values(source="bank", value=value)
        )
    db.commit()


def filled(
    db: Session, factory: sessionmaker[Session], person: Person, stub: str, *, clean: bool = True
) -> uuid.UUID:
    """A Like of `person`'s posting, filled by A5 and `ready` for Full auto's decision."""
    app_id = liked(db, person, stub)
    agent = FakeAgent(lambda q: (WHY_TEXT, "motivation") if q.label == WHY else ("leave", ""))
    assert fill(services(factory, run_ashby(), None, agent, stub_base=stub), app_id) == "ready"
    if clean:
        answered(db, app_id)
    return app_id


def job(
    factory: sessionmaker[Session],
    stub: str,
    app_id: uuid.UUID,
    when: datetime,
    *,
    form: SubmitForm | None = None,
    **rest: Any,
) -> tuple[str, SubmitForm]:
    """One submit job (the decision of a `ready` Full auto item, or the submit lease) at `when`."""
    form = form or submit_ashby()
    svc = submit_services(factory, form, stub, **rest)
    svc.clock = at(when)
    return run_submit(svc, app_id), form


def held(
    db: Session, factory: sessionmaker[Session], person: Person, stub: str, when: datetime = AT
) -> uuid.UUID:
    app_id = filled(db, factory, person, stub)
    assert job(factory, stub, app_id, when)[0] == "held"
    return app_id


def reviewed(db: Session, app_id: uuid.UUID, code: str) -> None:
    found = application(db, app_id)
    assert (found.state, found.mode, found.dry_run, found.error_code) == (
        State.ready,
        Mode.review,
        False,
        code,
    )
    assert "sent_to_review" in [e.kind for e in events(db, app_id)]


def authorisations(db: Session, app_id: uuid.UUID) -> list[Authorisation]:
    db.expire_all()
    return list(
        db.execute(sa.select(Authorisation).where(Authorisation.application_id == app_id)).scalars()
    )


# --- the order ---------------------------------------------------------------------------------


def test_the_order_decision_held_hold_recheck_click(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = filled(db, factory, person_a, stub)
    found = application(db, app_id)
    assert (found.mode, found.dry_run, found.created_at) == (Mode.full_auto, False, AT)
    [like] = [e for e in events(db, app_id) if e.kind == "liked"]
    assert like.data["full_auto"]["min_fit"] == 80
    assert like.data["full_auto"]["hold_s"] == 900
    assert app_id in submit.due(db, AT + HOUR)

    decided_at = AT + HOUR
    assert job(factory, stub, app_id, decided_at)[0] == "held"
    found = application(db, app_id)
    assert found.state is State.held
    [row] = authorisations(db, app_id)
    assert row.kind is AuthorisationKind.full_auto_like
    assert (row.created_at, row.expires_at, row.used_at) == (decided_at, AT + WEEK, decided_at)
    assert bytes(row.answers_hash) == bytes(found.answers_hash)

    until = decided_at + HOLD
    assert full_auto.hold_until(db, found) == until
    assert app_id not in submit.due(db, until - SECOND)
    written, form = job(factory, stub, app_id, until - SECOND)
    assert (written, form.gate_clicks) == ("holding", [])
    assert application(db, app_id).state is State.held

    assert app_id in submit.due(db, until)
    written, form = job(factory, stub, app_id, until)
    assert written == "submitted"
    assert [c["what"] for c in form.gate_clicks] == ["submit"]
    kinds = [e.kind for e in events(db, app_id)]
    order = [kinds.index(k) for k in ("authorisation", "decision", "click", "submitted")]
    assert order == sorted(order)


def test_a_like_before_full_auto_was_on_is_a_review_item(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a, since=AT + SECOND)
    app_id = liked(db, person_a, stub)
    assert application(db, app_id).mode is Mode.review
    assert app_id not in submit.due(db, AT + HOUR)


def test_full_auto_turned_off_and_on_again_after_the_like_sends_it_to_review(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = filled(db, factory, person_a, stub)
    db.execute(
        sa.update(ApplySettings)
        .where(ApplySettings.user_id == person_a.user_id)
        .values(full_auto_since=AT + HOUR)
    )
    db.commit()
    assert job(factory, stub, app_id, AT + 2 * HOUR)[0] == "full_auto_off"
    reviewed(db, app_id, "full_auto_off")
    assert authorisations(db, app_id) == []


@pytest.mark.parametrize(("after", "written"), [(WEEK - SECOND, "held"), (WEEK, "expired")])
def test_a_decision_seven_days_after_the_like_writes_no_authorisation(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    after: timedelta,
    written: str,
) -> None:
    turn_on(db, person_a)
    app_id = filled(db, factory, person_a, stub)
    got = job(factory, stub, app_id, AT + after)[0]
    if written == "held":
        assert got == "held"
        [row] = authorisations(db, app_id)
        assert row.expires_at == AT + WEEK > row.created_at
        return
    assert got == "authorisation_expired"
    reviewed(db, app_id, "authorisation_expired")
    assert authorisations(db, app_id) == []


@pytest.mark.parametrize(("click", "clicked"), [(WEEK - SECOND, True), (WEEK + SECOND, False)])
def test_the_like_is_valid_seven_days_at_the_click(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    click: timedelta,
    clicked: bool,
) -> None:
    turn_on(db, person_a)
    app_id = held(db, factory, person_a, stub, AT + timedelta(days=6))
    written, form = job(factory, stub, app_id, AT + click)
    if clicked:
        assert (written, len(form.gate_clicks)) == ("submitted", 1)
        return
    assert (written, form.gate_clicks) == ("authorisation_expired", [])
    reviewed(db, app_id, "authorisation_expired")


# --- each check sends the item to the review queue ------------------------------------------------


def _fit(db: Session, person: Person, fit: int | None) -> None:
    db.execute(sa.update(Job).where(Job.id == person.posting_id).values(fit=fit))


def _left(db: Session, person: Person, app_id: uuid.UUID) -> None:
    db.execute(
        sa.update(ApplicationAnswer)
        .where(ApplicationAnswer.application_id == app_id, ApplicationAnswer.label == LOCATION)
        .values(source="left", value=None)
    )


def _agent_text(db: Session, person: Person, app_id: uuid.UUID) -> None:
    db.execute(
        sa.update(ApplicationAnswer)
        .where(ApplicationAnswer.application_id == app_id, ApplicationAnswer.label == WHY)
        .values(source="agent")
    )


def _designated(db: Session, person: Person, app_id: uuid.UUID) -> None:
    db.add(
        ApplicationAnswer(
            application_id=app_id,
            user_id=person.user_id,
            field_key="designated:submit",
            label="Submit button",
            kind="designated",
            value="Submit Application",
            source="agent",
        )
    )


def _captcha(db: Session, person: Person, app_id: uuid.UUID) -> None:
    db.add(
        ApplicationEvent(
            application_id=app_id,
            user_id=person.user_id,
            at=AT,
            kind="needs_person",
            data={"needs": "captcha"},
        )
    )


def _source(db: Session, person: Person, app_id: uuid.UUID) -> None:
    db.execute(sa.update(Application).where(Application.id == app_id).values(ats="workday"))


def _overflow(db: Session, person: Person, app_id: uuid.UUID) -> None:
    found = db.get(Application, app_id)
    assert found is not None
    versions = {k: {**v, "fit": "overflow"} for k, v in found.document_versions.items()}
    found.document_versions = versions


def _text_rule(db: Session, person: Person, app_id: uuid.UUID) -> None:
    db.commit()
    rules(db, person.user_id, "No warehouse roles: warehouse")


CHECKS = [
    ("below_min_fit", lambda db, p, a: _fit(db, p, 79)),
    ("below_min_fit", lambda db, p, a: _fit(db, p, None)),
    ("fields_left", _left),
    ("agent_text", _agent_text),
    ("agent_designated", _designated),
    ("captcha_seen", _captcha),
    ("unsupported_source", _source),
    ("documents_review", _overflow),
    ("never_apply_text", _text_rule),
]


@pytest.mark.parametrize(("code", "spoil"), CHECKS)
def test_each_full_auto_check_sends_the_item_to_the_queue_with_its_reason(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    code: str,
    spoil: Any,
) -> None:
    turn_on(db, person_a)
    app_id = filled(db, factory, person_a, stub)
    spoil(db, person_a, app_id)
    db.commit()
    written, form = job(factory, stub, app_id, AT + HOUR)
    assert (written, form.gate_clicks) == (code, [])
    reviewed(db, app_id, code)
    assert gate.SENTENCES[code]
    [row] = authorisations(db, app_id)
    assert row.used_at is None  # refused: never spent


def test_a_check_failing_after_the_decision_stops_the_click(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = held(db, factory, person_a, stub)
    _fit(db, person_a, 10)
    db.commit()
    written, form = job(factory, stub, app_id, AT + HOLD)
    assert (written, form.gate_clicks) == ("below_min_fit", [])
    reviewed(db, app_id, "below_min_fit")


def test_a_never_apply_term_in_the_text_only_makes_a_review_item_at_the_like(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    rules(db, person_a.user_id, "No warehouse roles: warehouse")
    app_id = liked(db, person_a, stub)
    assert application(db, app_id).mode is Mode.review
    [like] = [e for e in events(db, app_id) if e.kind == "liked"]
    assert like.data["not_full_auto"] == "never_apply_text"


def test_a_never_apply_title_refuses_the_full_auto_like(
    db: Session, person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    rules(db, person_a.user_id, "No AI roles: applied")
    with pytest.raises(service.RefusedError) as refused:
        liked(db, person_a, stub)
    assert refused.value.refusal.code == "never_apply_role"


def test_a_review_item_from_full_auto_keeps_the_never_apply_refusal(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = filled(db, factory, person_a, stub, clean=False)
    assert job(factory, stub, app_id, AT + HOUR)[0] == "fields_left"
    reviewed(db, app_id, "fields_left")
    rules(db, person_a.user_id, "No cybersecurity roles: cyber, applied")
    decision = approve(factory, person_a, app_id, now=AT + 2 * HOUR)
    assert (decision.passed, decision.code, decision.state) == (
        False,
        "never_apply_role",
        State.blocked,
    )


# --- the caps, the ceiling and the pace -----------------------------------------------------------


def test_the_safety_ceiling_sends_the_rest_of_the_day_to_the_queue(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a, safety_ceiling=2)
    for company in ("Northwind Labs", "Contoso Systems"):
        a11.clicked(db, person_a.user_id, AT, company=company)
    db.commit()
    app_id = filled(db, factory, person_a, stub)
    assert job(factory, stub, app_id, AT + HOUR)[0] == "safety_ceiling"
    reviewed(db, app_id, "safety_ceiling")


def test_a_second_posting_at_the_same_company_within_30_days_goes_to_the_queue(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    a11.clicked(db, person_a.user_id, AT - timedelta(days=29), title="Robotics Engineer")
    db.commit()
    app_id = filled(db, factory, person_a, stub)
    assert job(factory, stub, app_id, AT + HOUR)[0] == "full_auto_company"
    reviewed(db, app_id, "full_auto_company")


def test_the_pace_keeps_an_item_held_past_its_hold_with_its_like_valid(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = held(db, factory, person_a, stub)
    other = a11.clicked(db, person_a.user_id, AT, company="Northwind Labs")
    for i in range(20):
        a11.click(db, other, AT + HOLD - HOUR + i * SECOND)
    db.commit()
    written, form = job(factory, stub, app_id, AT + HOLD)
    assert (written, form.gate_clicks) == ("paced", [])
    found = application(db, app_id)
    assert (found.state, found.needs, found.mode) == (State.held, "host_pace", Mode.full_auto)
    written, form = job(factory, stub, app_id, AT + HOLD + timedelta(days=1))
    assert (written, len(form.gate_clicks)) == ("submitted", 1)


@pytest.mark.parametrize(
    ("after", "written"),
    [(timedelta(hours=25), "submitted"), (WEEK + SECOND, "authorisation_expired")],
)
def test_a_review_approval_held_only_by_the_pace_stays_valid_up_to_seven_days(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    after: timedelta,
    written: str,
) -> None:
    app_id = filled(db, factory, person_a, stub, clean=False)
    assert approve(factory, person_a, app_id, now=AT).passed
    other = a11.clicked(db, person_a.user_id, AT, company="Northwind Labs")
    for i in range(20):
        a11.click(db, other, AT + i * SECOND)
    db.commit()
    assert job(factory, stub, app_id, AT + HOUR)[0] == "paced"
    got, form = job(factory, stub, app_id, AT + after)
    assert got == written
    assert len(form.gate_clicks) == (1 if written == "submitted" else 0)
    if written != "submitted":
        found = application(db, app_id)
        assert (found.state, found.error_code) == (State.ready, "authorisation_expired")


# --- Cancel, the pauses, the read back -----------------------------------------------------------


def test_cancel_during_the_hold_stops_it(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = held(db, factory, person_a, stub)
    with factory() as s:
        gate.cancel(s, person_a.user_id, app_id, AT + SECOND, reject=False)
        s.commit()
    written, form = job(factory, stub, app_id, AT + HOLD)
    assert (written, form.gate_clicks) == ("not_approved", [])
    assert application(db, app_id).state is State.cancelled


def test_cancel_at_the_last_moment_before_the_click_wins(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = held(db, factory, person_a, stub)
    form = submit_ashby()

    def cancel() -> None:
        with factory() as s:
            gate.cancel(s, person_a.user_id, app_id, AT + HOLD, reject=False)
            s.commit()

    at_the_dwell(form, cancel)
    written, _ = job(factory, stub, app_id, AT + HOLD, form=form)
    assert (written, form.gate_clicks) == ("superseded", [])
    assert application(db, app_id).state is State.cancelled


def test_the_persons_pause_during_the_hold_sends_it_to_the_queue(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = held(db, factory, person_a, stub)
    with factory() as s:
        caps.save_settings(s, person_a.user_id, AT, paused=True, min_fit=None)
        s.commit()
    written, form = job(factory, stub, app_id, AT + HOLD)
    assert (written, form.gate_clicks) == ("paused_by_you", [])
    reviewed(db, app_id, "paused_by_you")


def test_the_administrators_pause_between_decision_and_click_sends_it_to_the_queue(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = held(db, factory, person_a, stub)
    written, form = job(factory, stub, app_id, AT + HOLD, paused=True)
    assert (written, form.gate_clicks) == ("ready", [])
    reviewed(db, app_id, "paused")


def test_the_administrators_pause_at_the_decision_sends_it_to_the_queue(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = filled(db, factory, person_a, stub)
    assert job(factory, stub, app_id, AT + HOUR, paused=True)[0] == "paused"
    reviewed(db, app_id, "paused")


def test_answers_that_read_back_different_at_the_click_go_to_the_queue(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = held(db, factory, person_a, stub)
    form = submit_ashby()
    form.fields = [f for f in form.fields if f.label != "Phone"]
    written, _ = job(factory, stub, app_id, AT + HOLD, form=form)
    assert (written, form.gate_clicks) == ("ready", [])
    reviewed(db, app_id, "form_changed")


# --- rehearsals ----------------------------------------------------------------------------------


def rehearsed(db: Session, factory: sessionmaker[Session], person: Person, stub: str) -> uuid.UUID:
    turn_on(db, person, done=0)
    app_id = filled(db, factory, person, stub)
    assert application(db, app_id).dry_run
    assert job(factory, stub, app_id, AT + HOUR)[0] == "held"
    found = application(db, app_id)
    assert full_auto.hold_until(db, found) == AT + HOUR  # no hold for a rehearsal
    written, form = job(factory, stub, app_id, AT + HOUR)
    assert (written, form.gate_clicks) == ("rehearsed", [])
    return app_id


def yes(factory: sessionmaker[Session], person: Person, app_id: uuid.UUID) -> Application:
    with factory() as s:
        found = full_auto.confirm_rehearsal(s, person.user_id, app_id, AT + 2 * HOUR)
        s.commit()
        return found


def test_a_rehearsal_confirmed_lands_ready_and_one_approve_clicks_it_once(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = rehearsed(db, factory, person_a, stub)
    yes(factory, person_a, app_id)
    found = application(db, app_id)
    assert (found.state, found.mode, found.dry_run) == (State.ready, Mode.review, False)
    assert caps.settings_of(db, person_a.user_id).rehearsals_done == 1
    with pytest.raises(gate.RefusedError) as again:
        yes(factory, person_a, app_id)
    assert again.value.code == "not_rehearsal"
    assert caps.settings_of(db, person_a.user_id).rehearsals_done == 1

    assert approve(factory, person_a, app_id, now=AT + 3 * HOUR).passed
    written, form = job(factory, stub, app_id, AT + 3 * HOUR)
    assert (written, len(form.gate_clicks)) == ("submitted", 1)


def test_yes_after_another_application_took_the_posting_is_409(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = rehearsed(db, factory, person_a, stub)
    service.start(
        db, person_a.user_id, person_a.posting_id, Mode.review, False, now=AT, stub_base=stub
    )
    with pytest.raises(gate.RefusedError) as taken:
        yes(factory, person_a, app_id)
    assert taken.value.code == "rehearsal_taken"
    assert application(db, app_id).state is State.rehearsed
    assert caps.settings_of(db, person_a.user_id).rehearsals_done == 0


def test_yes_after_the_same_form_was_started_on_another_posting_is_409(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = rehearsed(db, factory, person_a, stub)
    other, _ = add_posting(
        db,
        person_a.user_id,
        stub + "/ashby/example-robotics/applied-ai-engineer/application?utm_source=board",
    )
    db.execute(sa.update(Job).where(Job.id == other).values(title="Data Engineer"))
    db.commit()
    service.start(db, person_a.user_id, other, Mode.review, False, now=AT, stub_base=stub)
    with pytest.raises(gate.RefusedError) as taken:
        yes(factory, person_a, app_id)
    assert taken.value.code == "rehearsal_taken"
    assert application(db, app_id).state is State.rehearsed


def test_no_keeps_full_auto_in_rehearsal_and_frees_the_posting(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = rehearsed(db, factory, person_a, stub)
    with factory() as s:
        full_auto.decline_rehearsal(s, person_a.user_id, app_id, AT + 2 * HOUR)
        s.commit()
    assert application(db, app_id).state is State.rehearsed
    assert caps.settings_of(db, person_a.user_id).rehearsals_done == 0
    again = likes.on_like(
        db, person_a.user_id, person_a.posting_id, now=AT + 3 * HOUR, stub_base=stub
    )
    assert again.application.dry_run  # still a rehearsal


def test_yes_on_a_review_first_dry_run_is_refused(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = filled(db, factory, person_a, stub)
    db.execute(sa.update(Application).where(Application.id == app_id).values(dry_run=True))
    db.commit()
    assert approve(factory, person_a, app_id, now=AT).passed
    assert job(factory, stub, app_id, AT)[0] == "rehearsed"
    with pytest.raises(gate.RefusedError) as refused:
        yes(factory, person_a, app_id)
    assert refused.value.code == "not_rehearsal"
    assert application(db, app_id).state is State.rehearsed


def test_a_refused_rehearsal_becomes_a_real_review_item(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a, done=0)
    app_id = filled(db, factory, person_a, stub, clean=False)
    assert job(factory, stub, app_id, AT + HOUR)[0] == "fields_left"
    reviewed(db, app_id, "fields_left")


# --- races ---------------------------------------------------------------------------------------


def _decide(factory: sessionmaker[Session], app_id: uuid.UUID) -> str:
    with factory() as s:
        out = full_auto.decide(s, app_id, AT + HOUR, ON)
        s.commit()
        return out


def test_two_decisions_at_once_hold_it_once(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = filled(db, factory, person_a, stub)
    with ThreadPoolExecutor(2) as pool:
        outs = sorted(pool.map(lambda _: _decide(factory, app_id), range(2)))
    assert outs == ["held", "superseded"]
    assert len(authorisations(db, app_id)) == 1


def test_two_decisions_for_one_company_take_one_slot(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    first = filled(db, factory, person_a, stub)
    other, _ = add_posting(
        db, person_a.user_id, stub + "/ashby/example-robotics/robotics-engineer/application"
    )
    db.execute(
        sa.update(Job)
        .where(Job.id == other)
        .values(title="Robotics Engineer", fit=90, location="Paris, France")
    )
    db.commit()
    second = likes.on_like(db, person_a.user_id, other, now=AT, stub_base=stub).application.id
    agent = FakeAgent(lambda q: (WHY_TEXT, "motivation") if q.label == WHY else ("leave", ""))
    assert fill(services(factory, run_ashby(), None, agent, stub_base=stub), second) == "ready"
    answered(db, second)
    with ThreadPoolExecutor(2) as pool:
        outs = sorted(pool.map(lambda i: _decide(factory, i), (first, second)))
    assert outs == ["full_auto_company", "held"]


def test_a_decision_against_the_persons_approve_passes_once(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = filled(db, factory, person_a, stub)

    def person() -> str:
        try:
            decision = approve(factory, person_a, app_id, now=AT + HOUR)
        except gate.RefusedError as exc:
            return exc.code
        return "approved" if decision.passed else str(decision.code)

    with ThreadPoolExecutor(2) as pool:
        machine = pool.submit(_decide, factory, app_id)
        human = pool.submit(person)
        outs = {machine.result(), human.result()}
    assert "held" in outs
    assert "approved" not in outs
    assert application(db, app_id).state is State.held


# --- the settings, the route, isolation ----------------------------------------------------------


@pytest.fixture
def api(
    migrated_url: str, stub: str, factory: sessionmaker[Session], person_a: Person, person_b: Person
) -> Iterator[Api]:
    from app.identity import security

    with factory() as s:
        for person in (person_a, person_b):
            s.execute(
                sa.text("UPDATE users SET password_hash = :h WHERE id = :u"),
                {"h": security.hash_password(PASSWORD), "u": person.user_id},
            )
        s.commit()
    app = create_app(
        make_settings(migrated_url, secret_key=SECRET_KEY, apply_stub_base_url=stub),
        clock=lambda: AT,
    )
    app.state.queue = FakeQueue()
    with TestClient(app, follow_redirects=False) as client:
        yield Api(client)


def test_turning_full_auto_on_records_the_date_and_the_hold_has_a_floor(
    api: Api, db: Session, person_a: Person, person_b: Person
) -> None:
    token = api.login_token(person_a.email)
    put = api.request(
        "PUT", "/api/apply/settings", token=token, json={"mode_after_like": "full_auto"}
    )
    assert put.status_code == 200
    assert (put.json()["mode_after_like"], put.json()["full_auto_available"]) == (
        "full_auto",
        True,
    )
    values = caps.settings_of(db, person_a.user_id)
    assert values.full_auto_since == AT
    assert caps.settings_of(db, person_b.user_id).mode_after_like is LikeMode.review
    short = api.request("PUT", "/api/apply/settings", token=token, json={"hold_s": 899})
    assert (short.status_code, short.json()["code"]) == (422, "hold_too_short")
    longer = api.request("PUT", "/api/apply/settings", token=token, json={"hold_s": 1800})
    assert (longer.status_code, longer.json()["hold_s"]) == (200, 1800)


def test_full_auto_turned_off_makes_held_items_review_items_that_an_approve_clicks_once(
    api: Api, db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = held(db, factory, person_a, stub)
    token = api.login_token(person_a.email)
    off = api.request("PUT", "/api/apply/settings", token=token, json={"mode_after_like": "review"})
    assert off.status_code == 200
    reviewed(db, app_id, "full_auto_off")
    assert approve(factory, person_a, app_id, now=AT + HOUR).passed
    written, form = job(factory, stub, app_id, AT + HOUR)
    assert (written, len(form.gate_clicks)) == ("submitted", 1)


def test_the_rehearsal_route_answers_yes_once_and_hides_another_persons(
    api: Api, db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = rehearsed(db, factory, person_a, stub)
    token_b = api.login_token("e2e-apply-b@example.org")
    path = f"/api/applications/{app_id}/rehearsal"
    assert api.post(path, token=token_b, json={"answer": "yes"}).status_code == 404
    token_a = api.login_token(person_a.email)
    assert api.post(path, token=token_a, json={"answer": "maybe"}).status_code == 422
    first = api.post(path, token=token_a, json={"answer": "yes"})
    assert (first.status_code, first.json()["state"]) == (200, "ready")
    second = api.post(path, token=token_a, json={"answer": "yes"})
    assert (second.status_code, second.json()["code"]) == (409, "not_rehearsal")


def test_bs_full_auto_never_touches_as_likes(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person, stub: str
) -> None:
    turn_on(db, person_b)
    db.execute(sa.update(Job).where(Job.user_id == person_a.user_id).values(fit=90))
    db.commit()
    app_id = filled(db, factory, person_a, stub)
    assert application(db, app_id).mode is Mode.review
    assert app_id not in submit.due(db, AT + HOUR)


def test_the_rehearsed_exit_is_the_only_new_move() -> None:
    assert states.TRANSITIONS[State.rehearsed] == {State.ready}


# --- one Like, one click -------------------------------------------------------------------------

LATER = AT + 3 * HOUR


def _clicked(
    factory: sessionmaker[Session], person: Person, stub: str, app_id: uuid.UUID, answer: str
) -> None:
    """The held item clicked after its hold, the site answering `answer`, then "Try again"."""
    written, form = job(factory, stub, app_id, AT + HOLD, form=submit_ashby(answer=answer))
    assert (written, len(form.gate_clicks)) == (
        "submit_unknown" if answer == "silent" else "refused_by_site",
        1,
    )
    with factory() as s:
        gate.retry(s, person.user_id, app_id, AT + HOLD + SECOND)
        s.commit()


def _interrupted(
    factory: sessionmaker[Session], person: Person, stub: str, app_id: uuid.UUID
) -> None:
    with factory() as s:
        assert submit.interrupted(s, app_id, AT + SECOND, "interrupted") == "ready"
        s.commit()


def _read_back(
    factory: sessionmaker[Session], person: Person, stub: str, app_id: uuid.UUID
) -> None:
    form = submit_ashby()
    form.fields = [f for f in form.fields if f.label != "Phone"]
    assert job(factory, stub, app_id, AT + HOLD, form=form)[0] == "ready"


def _turned_off(
    factory: sessionmaker[Session], person: Person, stub: str, app_id: uuid.UUID
) -> None:
    with factory() as s:
        ids = full_auto.turn(s, person.user_id, False, AT + SECOND)
        s.commit()
    with factory() as s:
        assert [full_auto.release(s, person.user_id, i, AT + SECOND) for i in ids] == [True]
        s.commit()


def _paused(factory: sessionmaker[Session], person: Person, stub: str, app_id: uuid.UUID) -> None:
    with factory() as s:
        caps.save_settings(s, person.user_id, AT, paused=True, min_fit=None)
        s.commit()
    assert job(factory, stub, app_id, AT + HOLD)[0] == "paused_by_you"
    with factory() as s:
        caps.save_settings(s, person.user_id, AT, paused=False, min_fit=None)
        s.commit()


BACK_TO_READY = {
    "try_again_after_submit_unknown": lambda f, p, s, a: _clicked(f, p, s, a, "silent"),
    "try_again_after_refused_by_site": lambda f, p, s, a: _clicked(f, p, s, a, "errors"),
    "crash_before_the_click": _interrupted,
    "read_back_differs": _read_back,
    "full_auto_turned_off": _turned_off,
    "persons_pause": _paused,
}


@pytest.mark.parametrize("path", sorted(BACK_TO_READY))
def test_every_way_back_to_ready_needs_the_persons_approve_to_click(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str, path: str
) -> None:
    turn_on(db, person_a)
    app_id = held(db, factory, person_a, stub)
    BACK_TO_READY[path](factory, person_a, stub, app_id)
    found = application(db, app_id)
    assert (found.state, found.mode, found.dry_run) == (State.ready, Mode.review, False)
    assert "sent_to_review" in [e.kind for e in events(db, app_id)]
    before = len(authorisations(db, app_id))
    for when in (AT + HOLD + HOUR, AT + 2 * HOLD + HOUR):
        assert app_id not in submit.due(db, when)
        written, form = job(factory, stub, app_id, when)
        assert (written, form.gate_clicks) == ("not_approved", [])
    assert len(authorisations(db, app_id)) == before
    likes_rows = [
        a for a in authorisations(db, app_id) if a.kind is AuthorisationKind.full_auto_like
    ]
    assert len(likes_rows) == 1

    assert approve(factory, person_a, app_id, now=LATER).passed
    written, form = job(factory, stub, app_id, LATER)
    assert (written, len(form.gate_clicks)) == ("submitted", 1)


def test_a_like_already_decided_is_never_decided_again(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = held(db, factory, person_a, stub)
    # Any path that would bring the item back to `ready` still in Full auto.
    db.execute(sa.update(Application).where(Application.id == app_id).values(state=State.ready))
    db.commit()
    written, form = job(factory, stub, app_id, AT + HOLD)
    assert (written, form.gate_clicks) == ("authorisation_used", [])
    reviewed(db, app_id, "authorisation_used")
    assert len(authorisations(db, app_id)) == 1
    assert job(factory, stub, app_id, AT + 2 * HOLD)[0] == "not_approved"


# --- the click bound to the decision the lease read ----------------------------------------------


def _why_key(factory: sessionmaker[Session], app_id: uuid.UUID) -> str:
    with factory() as s:
        rows = s.execute(
            sa.select(ApplicationAnswer).where(ApplicationAnswer.application_id == app_id)
        ).scalars()
        return next(r.field_key for r in rows if r.label == WHY)


def _typed(form: SubmitForm) -> list[str]:
    return [c[1]["text"] for c in form.calls if c[0] == "type"]


def _edit_and_approve(
    factory: sessionmaker[Session], person: Person, app_id: uuid.UUID, value: str
) -> None:
    when = AT + HOLD + SECOND
    with factory() as s:
        gate.set_answer(s, person.user_id, app_id, _why_key(factory, app_id), value, when)
        s.commit()
    assert approve(factory, person, app_id, now=when).passed


def _superseded_then_clicked(
    db: Session,
    factory: sessionmaker[Session],
    stub: str,
    app_id: uuid.UUID,
    form: SubmitForm,
    mine: str,
) -> None:
    written, _ = job(factory, stub, app_id, AT + HOLD, form=form)
    assert (written, form.gate_clicks) == ("superseded", [])
    assert WHY_TEXT in _typed(form)  # the lease refilled what it read, and did not click it
    found = application(db, app_id)
    assert (found.state, found.mode) == (State.approved, Mode.review)
    assert "click" not in [e.kind for e in events(db, app_id)]
    written, form = job(factory, stub, app_id, AT + HOLD + HOUR)
    assert (written, len(form.gate_clicks)) == ("submitted", 1)
    assert mine in _typed(form)
    assert WHY_TEXT not in _typed(form)


def test_full_auto_off_mid_lease_then_an_edit_and_approve_never_clicks_the_old_values(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = held(db, factory, person_a, stub)
    mine = "Marker " + uuid.uuid4().hex
    form = submit_ashby()

    def the_person_changes_it() -> None:
        _turned_off(factory, person_a, stub, app_id)
        _edit_and_approve(factory, person_a, app_id, mine)

    at_the_dwell(form, the_person_changes_it)
    _superseded_then_clicked(db, factory, stub, app_id, form, mine)


def test_a_review_first_edit_and_approve_mid_lease_never_clicks_the_old_values(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = filled(db, factory, person_a, stub)
    assert application(db, app_id).mode is Mode.review
    assert approve(factory, person_a, app_id, now=AT + HOLD).passed
    mine = "Marker " + uuid.uuid4().hex
    form = submit_ashby()
    at_the_dwell(form, lambda: _edit_and_approve(factory, person_a, app_id, mine))
    _superseded_then_clicked(db, factory, stub, app_id, form, mine)


def test_a_rehearsal_released_mid_lease_and_approved_is_not_clicked_on_its_dry_run_plan(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a, done=0)
    app_id = filled(db, factory, person_a, stub)
    assert job(factory, stub, app_id, AT + HOUR)[0] == "held"
    form = submit_ashby()

    def released_and_approved() -> None:
        _turned_off(factory, person_a, stub, app_id)
        assert approve(factory, person_a, app_id, now=AT + HOUR + SECOND).passed

    at_the_dwell(form, released_and_approved)
    written, _ = job(factory, stub, app_id, AT + HOUR, form=form)
    assert (written, form.gate_clicks) == ("superseded", [])
    found = application(db, app_id)
    assert (found.state, found.dry_run) == (State.approved, False)
    written, form = job(factory, stub, app_id, AT + 2 * HOUR)
    assert (written, len(form.gate_clicks)) == ("submitted", 1)


def _spent_again(db: Session, found: Application) -> None:
    db.add(
        Authorisation(
            id=uuid.uuid4(),
            user_id=found.user_id,
            application_id=found.id,
            kind=AuthorisationKind.person_approval,
            answers_hash=bytes(found.answers_hash or b""),
            created_at=AT,
            expires_at=AT + HOUR,
            used_at=AT + SECOND,
        )
    )


def _hash_changed(db: Session, found: Application) -> None:
    found.answers_hash = bytes(32)


def _dry_run_changed(db: Session, found: Application) -> None:
    found.dry_run = not found.dry_run


@pytest.mark.parametrize(
    "change",
    [_spent_again, _hash_changed, _dry_run_changed, None],
    ids=["authorisation", "answers_hash", "dry_run", "unchanged"],
)
def test_arm_clicks_only_the_decision_the_plan_was_read_from(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str, change: Any
) -> None:
    app_id = filled(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    with factory() as s:
        plan = submit.prepare(s, app_id, AT)
        s.commit()
    assert isinstance(plan, submit.Plan)
    if change is not None:
        with factory() as s:
            found = s.get(Application, app_id)
            assert found is not None
            change(s, found)
            s.commit()
    read = submit.Read(adapter=cast(Any, None), submit_ref="e1")
    with factory() as s:
        written = submit.arm(s, plan, AT + SECOND, ON, read)
        s.commit()
    kinds = [e.kind for e in events(db, app_id)]
    if change is None:
        assert (written, application(db, app_id).state) == ("submitting", State.submitting)
        assert "click" in kinds
        return
    assert (written, application(db, app_id).state) == ("superseded", State.approved)
    assert not {"click", "screenshot_failed", "rehearsed"} & set(kinds)
    with factory() as s:
        assert submit.stopped(s, plan, submit.Stop(State.ready, "form_changed"), AT) == "superseded"
        s.commit()
    assert application(db, app_id).state is State.approved


# --- the minor points ----------------------------------------------------------------------------


def test_unreadable_rules_at_a_full_auto_like_record_their_own_reason(
    db: Session, person_a: Person, monkeypatch: pytest.MonkeyPatch
) -> None:
    turn_on(db, person_a)
    monkeypatch.setattr(service, "_rules", lambda db, user_id: None)
    posting = db.get(Job, person_a.posting_id)
    assert posting is not None
    values = caps.settings_of(db, person_a.user_id)
    assert full_auto.mode_of_like(db, person_a.user_id, posting, values, AT) == (
        Mode.review,
        "rules_unreadable",
    )


@pytest.mark.parametrize("short", [899, 60, 59, 1, 0, -1])
def test_any_hold_under_the_floor_is_hold_too_short(api: Api, person_a: Person, short: int) -> None:
    token = api.login_token(person_a.email)
    put = api.request("PUT", "/api/apply/settings", token=token, json={"hold_s": short})
    assert (put.status_code, put.json()["code"]) == (422, "hold_too_short")
