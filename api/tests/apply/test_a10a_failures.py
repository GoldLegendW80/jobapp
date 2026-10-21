"""Failures, retries and "needs you" of the apply run (tasks/apply/todo.md, "A10a"), on a real
Postgres, A5's fake lease store and A4's modelled forms. No browser, no model, no company: the
clock and the sleep are injected, the person's moves are made by the fake sleep."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.apply import failures, run, states
from app.apply.models import Application, Mode, State
from tests.apply.a5_support import FakeAgent, FakeLeases, RunForm, run_ashby
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import (
    application,
    events,
    fill,
    services,
    started,
    why_answer,
)
from tests.apply.test_a13_full_auto import answered, authorisations, job, liked, turn_on

__all__ = ["stub"]

DELAYS = (600, 3600)
CARD_PAGE = (
    '- heading "Checkout" [level=1] [ref=e1]\n'
    '- textbox "Card number" [ref=e2]\n'
    '- textbox "CVC" [ref=e3]\n'
    '- button "Pay" [ref=e4]'
)


@pytest.fixture
def stub(forms_base_url: str) -> str:
    return forms_base_url


class PersonMoves:
    """The person's moves while the run waits: `at` the sleep's number, `move` what they do."""

    def __init__(self, svc: run.Services, moves: dict[int, Callable[[], None]], step_s: int = 30):
        self.svc, self.moves, self.step_s, self.sleeps = svc, moves, step_s, 0

    async def __call__(self, seconds: float) -> None:
        self.sleeps += 1
        self.svc.clock.now += timedelta(seconds=self.step_s)  # type: ignore[attr-defined]
        if (move := self.moves.get(self.sleeps)) is not None:
            move()


def svc_for(factory: sessionmaker[Session], form: RunForm, stub: str, **rest: Any) -> run.Services:
    rest.setdefault("retry_delays_s", DELAYS)
    rest.setdefault("needs_person_wait_s", 1800)
    return services(factory, form, stub_base=stub, **rest)


def with_status(form: RunForm, status: int) -> None:
    opened = form.navigate

    async def navigate(url: str) -> dict[str, Any]:
        return {**(await opened(url)), "status": status}

    form.navigate = navigate  # type: ignore[method-assign]


def kinds(db: Session, app_id: uuid.UUID) -> list[str]:
    return [e.kind for e in events(db, app_id)]


# --- the retry rule ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("attempt", "code", "delay"),
    [
        (1, "ended", 600),
        (2, "ended", 3600),
        (3, "ended", None),
        (0, "ended", None),
        (1, "navigation_timeout", 600),
        (1, "navigation_failed", 600),
        (1, "http_5xx", 600),
        (1, "unavailable", 600),
        (1, "interrupted", 600),
        (2, "internal_error", 3600),
        (1, "submit", None),
        (1, "danger", None),
        (1, "form_changed", None),
        (1, "posting_closed", None),
        (1, "bad_args", None),
        (1, "", None),
        (1, None, None),
        (1, "ENDED", None),
        (1, "ended ", None),
    ],
)
def test_the_retry_rule_two_retries_after_600_then_3600_then_failed(
    attempt: int, code: str | None, delay: int | None
) -> None:
    assert failures.retry_delay(attempt, code, DELAYS) == delay


def test_the_delays_come_from_the_setting() -> None:
    assert failures.retry_delays() == (600, 3600)
    assert failures.needs_person_wait_s() == 1800


def test_a_crash_is_retried_twice_then_failed_and_the_tick_waits_for_its_time(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = started(db, person_a)
    leases = FakeLeases()
    for attempt, delay in ((1, 600), (2, 3600)):
        form = run_ashby()
        form.crash_at = 8
        assert fill(svc_for(factory, form, stub, leases=leases), app_id) == "queued"
        found = application(db, app_id)
        assert (found.state, found.needs, found.error_code, found.attempt) == (
            State.queued,
            failures.RETRY,
            "ended",
            attempt,
        )
        (retry,) = [e for e in events(db, app_id) if e.kind == "retry"][-1:]
        assert (retry.data["delay_s"], retry.data["attempt"]) == (delay, attempt)
        since = found.updated_at
        assert app_id not in run.due(db, since + timedelta(seconds=delay - 1), DELAYS)
        assert app_id in run.due(db, since + timedelta(seconds=delay + 1), DELAYS)
    form = run_ashby()
    form.crash_at = 8
    assert fill(svc_for(factory, form, stub, leases=leases), app_id) == "failed"
    found = application(db, app_id)
    assert (found.state, found.error_code, found.attempt) == (State.failed, "ended", 3)
    assert len(leases.released) == 3
    assert app_id not in run.due(db, AT + timedelta(days=30), DELAYS)


def test_a_retry_that_works_reaches_ready_and_never_past_it(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = started(db, person_a)
    form = run_ashby()
    form.crash_at = 8
    assert fill(svc_for(factory, form, stub), app_id) == "queued"
    form = run_ashby()
    agent = FakeAgent(why_answer)
    assert fill(svc_for(factory, form, stub, answer_agent=agent), app_id) == "ready"
    found = application(db, app_id)
    assert (found.state, found.attempt, found.needs, found.error_code) == (
        State.ready,
        2,
        None,
        None,
    )
    assert form.submit_clicks == 0


def test_a_retry_that_finds_the_posting_closed_ends_posting_closed(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = started(db, person_a)
    form = run_ashby()
    form.crash_at = 8
    assert fill(svc_for(factory, form, stub), app_id) == "queued"
    form = run_ashby()
    with_status(form, 404)
    assert fill(svc_for(factory, form, stub), app_id) == "posting_closed"
    assert application(db, app_id).state is State.posting_closed


def test_a_5xx_page_is_retried(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = started(db, person_a)
    form = run_ashby()
    with_status(form, 503)
    form.on_call = lambda f, n: setattr(f, "_other", "Service Unavailable")
    assert fill(svc_for(factory, form, stub), app_id) == "queued"
    found = application(db, app_id)
    assert (found.needs, found.error_code) == (failures.RETRY, failures.HTTP_5XX)


def test_an_interrupted_fill_is_queued_for_its_retry_until_none_is_left(
    db: Session, person_a: Person
) -> None:
    app_id = started(db, person_a)
    for attempt, expected in ((1, State.queued), (2, State.queued), (3, State.failed)):
        assert run.begin(db, app_id, AT)
        db.commit()
        assert application(db, app_id).attempt == attempt
        assert run.reset_interrupted(db, AT, DELAYS) == 1
        db.commit()
        found = application(db, app_id)
        assert (found.state, found.error_code) == (expected, "interrupted")


def test_a_full_auto_retry_keeps_its_like_for_the_one_decision(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = liked(db, person_a, stub)
    leases = FakeLeases()
    form = run_ashby()
    form.crash_at = 8
    assert fill(svc_for(factory, form, stub, leases=leases), app_id) == "queued"
    assert leases.acquired[0]["purpose"] == run.FULL_AUTO_PURPOSE
    found = application(db, app_id)
    assert (found.state, found.mode) == (State.queued, Mode.full_auto)
    assert all(a.used_at is None for a in authorisations(db, app_id))
    agent = FakeAgent(lambda q: ("I want to build agents.", "motivation"))
    assert fill(svc_for(factory, run_ashby(), stub, answer_agent=agent), app_id) == "ready"
    answered(db, app_id)
    assert job(factory, stub, app_id, AT + timedelta(minutes=30))[0] == "held"
    assert sum(a.used_at is not None for a in authorisations(db, app_id)) == 1


# --- the person ----------------------------------------------------------------------------------


def captcha_form() -> RunForm:
    form = run_ashby()
    form.captcha = "beside"
    return form


def test_a_captcha_solved_by_the_person_in_the_live_view_goes_on_to_ready(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = started(db, person_a)
    form, leases = captcha_form(), FakeLeases()
    svc = svc_for(factory, form, stub, leases=leases, answer_agent=FakeAgent(why_answer))
    seen: list[tuple[State, str | None]] = []

    def look() -> None:
        found = application(db, app_id)
        seen.append((found.state, found.needs))

    def solve() -> None:
        form.captcha = ""

    svc.sleep = PersonMoves(svc, {1: look, 2: solve})
    assert fill(svc, app_id) == "ready"
    assert seen == [(State.needs_person, "captcha")]
    found = application(db, app_id)
    assert (found.state, found.attempt) == (State.ready, 1)
    assert [k for k in kinds(db, app_id) if k in ("waiting_person", "person_done")] == [
        "waiting_person",
        "person_done",
    ]
    assert leases.acquired[0]["purpose"] == run.PURPOSE
    assert len(leases.released) == 1
    assert form.submit_clicks == 0


def test_the_person_never_comes_the_lease_ends_and_the_item_waits(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = started(db, person_a)
    form, leases = captcha_form(), FakeLeases()
    svc = svc_for(factory, form, stub, leases=leases, needs_person_wait_s=120)
    svc.sleep = PersonMoves(svc, {})
    assert fill(svc, app_id) == "needs_person"
    found = application(db, app_id)
    assert (found.state, found.needs) == (State.needs_person, "captcha")
    assert "person_not_back" in kinds(db, app_id)
    assert svc.sleep.sleeps <= 5  # type: ignore[attr-defined]
    assert len(leases.released) == 1


def test_the_person_acting_in_the_app_ends_the_wait(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = started(db, person_a)
    form, leases = captcha_form(), FakeLeases()
    svc = svc_for(factory, form, stub, leases=leases)

    def cancel() -> None:
        with factory() as other:
            found = other.get(Application, app_id)
            assert found is not None
            states.move(found, State.cancelled, AT)
            other.commit()

    svc.sleep = PersonMoves(svc, {1: cancel})
    assert fill(svc, app_id) == "person_acted"
    assert application(db, app_id).state is State.cancelled
    assert len(leases.released) == 1


def test_full_auto_never_waits_for_the_person(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    turn_on(db, person_a)
    app_id = liked(db, person_a, stub)
    form, leases = captcha_form(), FakeLeases()
    svc = svc_for(factory, form, stub, leases=leases)
    svc.sleep = PersonMoves(svc, {})
    assert fill(svc, app_id) == "needs_person"
    assert svc.sleep.sleeps == 0  # type: ignore[attr-defined]
    assert leases.acquired[0]["purpose"] == run.FULL_AUTO_PURPOSE
    found = application(db, app_id)
    assert (found.state, found.needs, found.mode) == (State.needs_person, "captcha", Mode.review)


def test_a_required_field_left_does_not_hold_the_lease(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = started(db, person_a)
    svc = svc_for(factory, run_ashby(), stub)
    svc.sleep = PersonMoves(svc, {})
    assert fill(svc, app_id) == "needs_person"
    assert application(db, app_id).needs == "required_left"
    assert svc.sleep.sleeps == 0  # type: ignore[attr-defined]


def test_a_payment_page_ends_the_attempt_with_nothing_typed(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = started(db, person_a)
    form = run_ashby()

    async def snapshot() -> dict[str, Any]:
        form._log("snapshot")
        return {"ok": True, "url": form.url, "snapshot": CARD_PAGE, "cut": False}

    form.snapshot = snapshot  # type: ignore[method-assign]
    assert fill(svc_for(factory, form, stub), app_id) == "needs_person"
    found = application(db, app_id)
    assert (found.state, found.needs) == (State.needs_person, failures.PAYMENT)
    assert not [c for c in form.calls if c[0] in ("type", "click", "select_option")]


def test_the_dot_counts_the_persons_own_items_only(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person, stub: str
) -> None:
    app_id = started(db, person_a)
    svc = svc_for(factory, run_ashby(), stub)
    assert fill(svc, app_id) == "needs_person"
    assert failures.needs_count(db, person_a.user_id) == 1
    assert failures.needs_count(db, person_b.user_id) == 0


# --- a payment page ------------------------------------------------------------------------------

PAY = [
    "Card number",
    "Card number *",
    "Credit card number",
    "Debit Card Number",
    "card no.",
    "Card #",
    "Payment card number",
    "CVV",
    "CVC",
    "cvv2",
    "CVC2 (required)",
    "Card verification code",
    "Card security code",
    "Numéro de carte",
    "Numéro de la carte bancaire",
    "NUMERO DE CARTE DE CREDIT",
    "Cryptogramme visuel",
    "Cryptogramme",
    "Code de sécurité de la carte",
]
NOT_PAY = [
    "Security code",
    "Verification code",
    "Enter the code we sent",
    "Expiration date",
    "ID card number",
    "Identity card number",
    "National ID card number",
    "Numéro de carte d'identité",
    "Numéro de carte vitale",
    "Gift card number",
    "Card",
    "Business card",
    "Phone number",
    "Card number of your library",
    "Cover letter",
    "Do you hold a work permit card?",
    "CVV please explain",
    "Your CV",
    "CV",
    "",
]


@pytest.mark.parametrize("label", PAY)
def test_a_card_field_is_a_payment_page(label: str) -> None:
    assert failures.payment_page(f'- textbox "{label}" [ref=e1]')
    assert failures.payment_page(f'- spinbutton "{label}" [ref=e1]')


@pytest.mark.parametrize("label", NOT_PAY)
def test_a_field_that_is_no_card_is_no_payment(label: str) -> None:
    assert not failures.payment_page(f'- textbox "{label}" [ref=e1]')


@pytest.mark.parametrize("role", ["button", "checkbox", "heading", "link", "combobox"])
def test_a_card_name_on_another_role_is_no_payment(role: str) -> None:
    assert not failures.payment_page(f'- {role} "Card number" [ref=e1]')


@pytest.mark.parametrize(
    ("name", "pay"),
    [
        ("Secure card payment input frame", True),
        ("Secure card number input frame", True),
        ("Secure payment input frame", True),
        ("reCAPTCHA", False),
        ("Application form", False),
    ],
)
def test_a_card_payment_frame(name: str, pay: bool) -> None:
    assert failures.payment_page(f'- iframe "{name}" [ref=e1]') is pay


def test_a_huge_page_is_read_in_one_pass() -> None:
    page = "\n".join(f'- textbox "Question {i}" [ref=e{i}]' for i in range(20_000))
    assert not failures.payment_page(page)
    assert failures.payment_page(page + '\n- textbox "Card number" [ref=e99999]')


def test_the_tick_skips_a_retry_not_due_and_asks_for_a_queued_item_without_needs(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = started(db, person_a)
    form = run_ashby()
    form.crash_at = 8
    assert fill(svc_for(factory, form, stub), app_id) == "queued"
    since = application(db, app_id).updated_at
    assert run.due(db, since, DELAYS) == []
    db.execute(sa.update(Application).where(Application.id == app_id).values(needs=None))
    db.commit()
    assert run.due(db, since, DELAYS) == [app_id]
