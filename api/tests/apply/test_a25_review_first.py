"""The apply agent's wait for the person in Review first (A25 on A20's run), on a real Postgres:
an answer and the run goes on to apply_ready; after a step act browserd traced, a Cancel, a
Reject, Activity's Cancel, nobody in time, a crash and the worker's start all end `submit_unknown`
(the form may have been sent), never an error; a send the agent says it clicked is a possible
send, so Try again runs it once more and it looks on the site first; the asks stay out of the
item's approval answers. The fake sites of A20 with browserd's ask (`AskSite`)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.apply import applier, gate
from app.apply.models import Mode, State
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import application, events
from tests.apply.test_a19_applier import CHOSEN, STEP_2
from tests.apply.test_a20_review_first import ReviewSite, ticked
from tests.apply.test_a20_round1 import StepSite
from tests.apply.test_a24_cancel import put
from tests.apply.test_a25_waits import SENTENCE, AskSite, answer, go_apply, svc, waits


class AskReview(AskSite, ReviewSite):
    """A20's review lease (Next passes untraced) with browserd's ask."""


class AskStep(AskSite, StepSite):
    """A20's review lease whose Next browserd traces (`step_submit`), with browserd's ask."""


def kinds(db: Session, app_id: uuid.UUID) -> list[str]:
    return [e.kind for e in events(db, app_id)]


def review_asking(*, step_first: bool) -> Any:
    """Fills step 1 (and moves on with Next first when `step_first`), asks, then reads the page,
    fills the rest and names the send with apply_ready."""

    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        go.briefs.append(json.loads(message))  # type: ignore[attr-defined]
        await session.navigate(go.briefs[-1]["posting"]["url"])  # type: ignore[attr-defined]
        await session.interact("browser_click", {"ref": "e1"})
        await session.interact("browser_type", {"ref": "e8", "text": "One month"})
        if step_first:
            await session.interact("browser_click", {"ref": "e6"})
        go.said = await session.ask_person("signin", SENTENCE)  # type: ignore[attr-defined]
        if session.stopped:
            return applier.Ran(5, 1)
        await session.snapshot()
        if session.url != STEP_2:
            await session.interact("browser_click", {"ref": "e6"})
        await session.interact("browser_type", {"ref": "e7", "text": "Your robots."})
        await session.interact("browser_click", {"ref": "e9"})
        session.ready_at("e9", [CHOSEN])
        return applier.Ran(5, 1)

    go.briefs = []  # type: ignore[attr-defined]
    return go


def test_an_answer_and_the_run_goes_on_to_apply_ready(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = ticked(db, person_a)
    seen: dict[str, Any] = {}

    def during(_: AskSite) -> None:
        found = application(db, app_id)
        seen["state"] = (found.state, found.needs)
        answer(factory, person_a, app_id, "I signed in.")

    services_ = svc(factory, AskReview({1: during}), review_asking(step_first=False))
    handed: list[applier.Held] = []

    async def hand_over(held: applier.Held) -> bool:
        handed.append(held)
        return True

    services_.hand_over = hand_over
    assert go_apply(services_, app_id) == State.ready.value
    assert seen["state"] == (State.needs_person, applier.WAITS)
    found = application(db, app_id)
    assert (found.state, found.needs) == (State.ready, None)
    assert len(handed) == 1
    assert applier.chat(db, found)[0]["end"] == "answer"
    assert applier.STEP_SENT not in kinds(db, app_id)


@pytest.mark.parametrize("how", ["cancel", "reject"])
def test_a_cancel_or_a_reject_during_a_wait_after_a_traced_step_act(
    db: Session, factory: sessionmaker[Session], person_a: Person, how: str
) -> None:
    """The form may have been sent by the step act: `submit_unknown` with `cancelled_by_person`,
    never an error, and the card said so while it waited (`after_send`)."""
    from app.apply.router import _question

    app_id = ticked(db, person_a)
    seen: dict[str, Any] = {}

    def stop(_: AskSite) -> None:
        with factory() as s:
            seen["after_send"] = _question(s, application(s, app_id)).after_send
            gate.cancel(s, person_a.user_id, app_id, AT, reject=how == "reject")
            s.commit()

    site, script = AskStep({1: stop}), review_asking(step_first=True)
    assert go_apply(svc(factory, site, script), app_id) == "superseded"
    assert seen["after_send"] is True
    found = application(db, app_id)
    assert (found.state, found.error_code, found.needs) == (
        State.submit_unknown,
        gate.CANCELLED_BY_PERSON,
        None,
    )
    assert applier.chat(db, found)[0]["end"] == "cancelled"
    assert kinds(db, app_id).count(applier.STEP_SENT) == 1
    assert site.gate_clicks == []


def stepped_and_waiting(db: Session, factory: sessionmaker[Session], app_id: uuid.UUID) -> None:
    """A Review first run traced a step act, then asked and waits (its database steps, no run)."""
    from app.apply import run

    put(db, app_id, State.filling)
    with factory() as s:
        run._event(s, application(s, app_id), applier.STEP_SENT, AT)
        s.commit()
    waits(db, factory, app_id, "signin")


@pytest.mark.parametrize("end", ["activity_cancel", "crash", "worker_start"])
def test_activity_s_cancel_a_crash_or_the_worker_s_start_during_that_wait(
    db: Session, factory: sessionmaker[Session], person_a: Person, end: str
) -> None:
    app_id = ticked(db, person_a)
    stepped_and_waiting(db, factory, app_id)
    with factory() as s:
        if end == "worker_start":
            assert applier.reset_interrupted(s, AT) == 1
        elif end == "crash":
            assert applier.interrupted(s, app_id, AT) == State.submit_unknown.value
        else:
            session = applier.ApplySession(None, None, None, None, review=True)  # type: ignore[arg-type]
            applier.finish(s, app_id, session, applier.Ran(stopped="cancelled"), AT)
        s.commit()
    found = application(db, app_id)
    code = gate.CANCELLED_BY_PERSON if end == "activity_cancel" else "sent_before_confirm"
    assert (found.state, found.error_code, found.needs) == (State.submit_unknown, code, None)
    ended = "cancelled" if end == "activity_cancel" else "interrupted"
    assert applier.chat(db, found)[0]["end"] == ended


def test_nobody_in_time_after_a_traced_step_act_is_submit_unknown(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = ticked(db, person_a)
    site, script = AskStep(), review_asking(step_first=True)
    assert go_apply(svc(factory, site, script), app_id) == State.submit_unknown.value
    assert script.said == (applier.ENDS[applier.PERSON_LATE], True)
    found = application(db, app_id)
    assert (found.state, found.error_code, found.needs) == (
        State.submit_unknown,
        "sent_before_confirm",
        None,
    )
    assert applier.chat(db, found)[0]["end"] == applier.PERSON_LATE
    assert site.gate_clicks == []


@pytest.mark.parametrize(("outcome", "sent"), [("failed", "yes"), ("failed", "unsure"),
                                               ("submitted", None)])  # fmt: skip
def test_try_again_after_the_agent_s_word_of_a_send_runs_it_once_more_looking_first(
    db: Session, factory: sessionmaker[Session], person_a: Person, outcome: str, sent: str | None
) -> None:
    """No step act traced, no click of the gate: the agent says it clicked a send, or reports a
    confirmation. Maybe sent, and Try again is one more run of the agent in Review first whose
    first message says so (`earlier_send`), never an empty item with the old Approve."""
    app_id = ticked(db, person_a)

    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        await session.navigate(json.loads(message)["posting"]["url"])
        await session.snapshot()
        session.finish(outcome, "It may have gone.", session.url, sent=sent)
        return applier.Ran(1, 1)

    assert go_apply(svc(factory, AskReview(), go), app_id) == State.submit_unknown.value
    found = application(db, app_id)
    assert found.error_code == "sent_before_confirm"
    assert applier.SAID_SENT_EVENT in kinds(db, app_id)
    assert applier.ran_agent(db, found)
    with factory() as s:
        gate.retry(s, person_a.user_id, app_id, AT)
        s.commit()
    found = application(db, app_id)
    assert (found.state, found.mode) == (State.queued, Mode.review)
    script = review_asking(step_first=False)
    go_apply(svc(factory, AskReview(), script), app_id)
    assert script.briefs[0]["earlier_send"] is True
    assert script.briefs[0]["review_first"] is True


def test_the_asks_are_no_answers_of_the_form(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    from app.apply.approval_router import _answers

    app_id = ticked(db, person_a)
    waits(db, factory, app_id, "signin")
    found = application(db, app_id)
    assert applier.chat(db, found)  # the ask is stored
    keys = [row.field_key for row in gate.rows_of(db, found)]
    assert not [k for k in keys if k.startswith(applier.ASK_KEY)]
    served = [a.field_key for a in _answers(db, found)]
    assert not [k for k in served if k.startswith(applier.ASK_KEY)]
