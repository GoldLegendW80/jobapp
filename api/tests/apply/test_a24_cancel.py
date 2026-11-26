"""Cancel an application at any moment (A24; owner, 2026-11-26: "je veux pouvoir cancel une
application à tout moment"), on a real Postgres:

- `gate.cancel` takes every state but an end, in every mode: `cancelled` with
  `cancelled_by_person` before any act on a submit element, `submit_unknown` with the same code
  after one (the form may have been received; never sent again by the app);
- the application's agent run is cancelled through the runs' own cancel (`runs.request_cancel`,
  what Activity's Cancel calls), and the Cancel of that run in Activity ends the application as
  the application's own Cancel does, the lease released and the run's cost kept; a confirmation
  the agent read before either Cancel reached its run ends it with the send, never "Nothing was
  sent"; a Cancel during the pace's pause stops the act it waited for;
- only the person's "It was not sent" ends a `submit_unknown` `cancelled`, and the posting is
  free for their next Apply;
- Cancel all stops exactly the applications the confirmation named.

No browser, no model, no company: the agent is scripted on A19's fake job site. Every account
is `e2e-*@example.org`; the companies are invented."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.agents import runs
from app.agents.models import AgentRun, RunStatus
from app.apply import applier, confirm, gate, service
from app.apply.models import Application, ApplicationAnswer, ApplicationEvent, Mode, State
from app.jobs.models import JobStatus
from app.main import create_app
from tests.apply.a6_support import FakeQueue
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import application, events, started, status
from tests.apply.test_a6_gate import _password
from tests.apply.test_a12_likes import SECRET_KEY
from tests.apply.test_a19_applier import Site, svc
from tests.apply.test_a20_review_first import ticked
from tests.conftest import make_settings
from tests.identity.api_helpers import Api

GOING = (
    State.queued,
    State.filling,
    State.needs_person,
    State.ready,
    State.approved,
    State.held,
    State.refused_by_site,
)


def put(db: Session, app_id: uuid.UUID, state: State, **values: Any) -> None:
    clicked = state in (State.submitting, State.submit_unknown, State.submitted)
    db.execute(
        sa.update(Application)
        .where(Application.id == app_id)
        .values(state=state, submitted_at=AT if clicked else None, **values)
    )
    db.commit()


def agent_run(
    db: Session,
    person: Person,
    app_id: uuid.UUID,
    *,
    agent: str = applier.NAME,
    status: RunStatus = RunStatus.running,
) -> uuid.UUID:
    """A run of the application's workflow in Activity, as `RuntimeApplier` creates it."""
    flow = runs.WorkflowRef("application", app_id, "apply")
    run_id = runs.create_run(db, person.user_id, agent, None, {}, applier.MODEL, AT, flow).id
    if status is not RunStatus.queued:
        runs.claim(db, run_id, AT)
    if status in runs.FINAL_STATUSES:
        db.execute(sa.update(AgentRun).where(AgentRun.id == run_id).values(status=status))
    db.commit()
    return run_id


def run_row(db: Session, run_id: uuid.UUID) -> AgentRun:
    db.expire_all()
    found = db.get(AgentRun, run_id)
    assert found is not None
    return found


def cancel(factory: sessionmaker[Session], person: Person, app_id: uuid.UUID) -> Application:
    with factory() as s:
        found = gate.cancel(s, person.user_id, app_id, AT, reject=False)
        s.commit()
        return found


def kinds(db: Session, app_id: uuid.UUID) -> list[str]:
    return [e.kind for e in events(db, app_id)]


# --- the table: every state and mode -------------------------------------------------------------


@pytest.mark.parametrize("mode", [Mode.review, Mode.full_auto, Mode.direct])
@pytest.mark.parametrize("state", [*GOING, State.submitting])
def test_a_cancel_is_taken_in_every_state_before_an_end_and_says_what_is_true(
    db: Session, factory: sessionmaker[Session], person_a: Person, mode: Mode, state: State
) -> None:
    app_id = started(db, person_a, mode)
    put(db, app_id, state)
    running = agent_run(db, person_a, app_id)
    queued = agent_run(db, person_a, app_id, status=RunStatus.queued)

    cancel(factory, person_a, app_id)

    found = application(db, app_id)
    end = State.submit_unknown if state is State.submitting else State.cancelled
    assert (found.state, found.error_code) == (end, gate.CANCELLED_BY_PERSON)
    # The run's own cancel: a running one is interrupted by its runner, a queued one never runs.
    assert run_row(db, running).cancel_requested_at is not None
    assert run_row(db, queued).status is RunStatus.cancelled
    assert kinds(db, app_id).count("agent_run_cancelled") == 2
    if end is State.submit_unknown:
        # Maybe sent: the posting stays held, so a new Apply is refused as a duplicate.
        assert found.submitted_at is not None
        with pytest.raises(service.RefusedError) as refused:
            service.start(db, person_a.user_id, person_a.posting_id, mode, False, now=AT)
        assert refused.value.refusal.code == "submit_unknown"
        db.rollback()


@pytest.mark.parametrize(
    "state", [State.submit_unknown, State.submitted, State.cancelled, State.failed]
)
def test_an_end_is_not_cancelled_and_its_runs_are_left(
    db: Session, factory: sessionmaker[Session], person_a: Person, state: State
) -> None:
    app_id = started(db, person_a, Mode.direct)
    put(db, app_id, state)
    with factory() as s, pytest.raises(gate.RefusedError, match="not_cancellable"):
        gate.cancel(s, person_a.user_id, app_id, AT, reject=False)
    assert application(db, app_id).state is state


def test_only_the_application_s_own_apply_agent_run_is_cancelled(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    put(db, app_id, State.filling)
    other = uuid.uuid4()
    mine = agent_run(db, person_a, app_id)
    answer = agent_run(db, person_a, app_id, agent="apply_answer")
    elsewhere = agent_run(db, person_a, other)
    done = agent_run(db, person_a, app_id, status=RunStatus.done)
    cancel(factory, person_a, app_id)
    assert run_row(db, mine).cancel_requested_at is not None
    # An adapter's fill reads the state and stops as before; another application's run goes on.
    for kept in (answer, elsewhere):
        assert run_row(db, kept).cancel_requested_at is None
    assert run_row(db, done).status is RunStatus.done


def test_an_email_application_being_sent_keeps_the_mail_gate_s_end(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.review)
    put(db, app_id, State.submitting, ats="email")
    with factory() as s, pytest.raises(gate.RefusedError, match="not_cancellable"):
        gate.cancel(s, person_a.user_id, app_id, AT, reject=False)
    put(db, app_id, State.submit_unknown, ats="email")
    with factory() as s, pytest.raises(gate.RefusedError, match="not_unknown"):
        gate.not_sent(s, person_a.user_id, app_id, AT)


@pytest.mark.parametrize("state", [State.filling, State.ready, State.approved])
def test_after_a_traced_step_act_review_first_s_cancel_says_it_may_have_been_sent(
    db: Session, factory: sessionmaker[Session], person_a: Person, state: State
) -> None:
    from app.apply import run

    app_id = ticked(db, person_a)
    found = application(db, app_id)
    run._event(db, found, applier.STEP_SENT, AT)
    db.commit()
    put(db, app_id, state)
    cancel(factory, person_a, app_id)
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.submit_unknown, gate.CANCELLED_BY_PERSON)


def test_review_first_s_held_session_ends_on_the_cancel_and_sends_nothing(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = ticked(db, person_a)
    put(db, app_id, State.ready)
    cancel(factory, person_a, app_id)
    # The hold reads the state at its next poll and hands the lease back (confirm.hold).
    with factory() as s:
        assert confirm._state(s, app_id) is State.cancelled
        assert not confirm.live(s, application(s, app_id), AT)


# --- the two paths: the application's Cancel and the Cancel of its run in Activity ---------------


class Runner:
    """The apply agent's run as the agents runner drives it: a run in Activity for the
    application (`applier.start_run`), the agent's acts, then the runner's poll of the run's
    cancel (`runs.cancel_requested`, every 0.5 s in the runner) ending it `cancelled` with what
    it spent. `stop` names where the person clicks Cancel, in another session, mid run."""

    def __init__(
        self, factory: sessionmaker[Session], person: Person, stop: str, *, after_submit: bool
    ) -> None:
        self.factory, self.person, self.stop = factory, person, stop
        self.after_submit = after_submit
        self.run_id: uuid.UUID | None = None

    def click(self, app_id: uuid.UUID) -> None:
        with self.factory() as s:
            if self.stop == "application":
                gate.cancel(s, self.person.user_id, app_id, AT, reject=False)
            else:
                assert self.run_id is not None
                runs.request_cancel(s, self.person.user_id, self.run_id, AT)
            s.commit()

    async def __call__(self, session: applier.ApplySession, message: str) -> applier.Ran:
        app_id = session.flow.run_id if session.flow else None
        assert app_id is not None
        with self.factory() as s:
            created = applier.start_run(s, self.person.user_id, session, {}, AT)
            assert isinstance(created, uuid.UUID)
            runs.claim(s, created, AT)
            s.commit()
        self.run_id = created
        await session.navigate(json.loads(message)["posting"]["url"])
        await session.interact("browser_click", {"ref": "e1"})
        await session.interact("browser_type", {"ref": "e8", "text": "One month"})
        if self.after_submit:
            await session.interact("browser_click", {"ref": "e6"})  # Next: a submit element
        self.click(app_id)
        with self.factory() as s:
            if not runs.cancel_requested(s, created):
                return applier.Ran(1, 1)
            runs.finish(
                s,
                created,
                runs.Outcome(status=RunStatus.cancelled, input_tokens=700, output_tokens=90),
                AT,
            )
            s.commit()
        return applier.Ran(700, 90, "cancelled")


@pytest.mark.parametrize("after_submit", [False, True])
def test_the_application_s_cancel_and_its_run_s_cancel_in_activity_end_alike(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    after_submit: bool,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(applier.credentials, "get", lambda db, user_id: object())
    ends = {}
    for stop in ("application", "activity"):
        app_id = started(db, person_a, Mode.direct)
        script = Runner(factory, person_a, stop, after_submit=after_submit)
        services_ = svc(factory, Site(), script)
        written = asyncio.run(applier.apply(services_, app_id))
        found = application(db, app_id)
        ends[stop] = (found.state, found.error_code)
        # The run stopped through its own cancel, its cost kept, the lease handed back.
        assert script.run_id is not None
        assert run_row(db, script.run_id).cancel_requested_at is not None
        spent = (found.cost.get("input_tokens"), found.cost.get("output_tokens"))
        assert spent == (700, 90), (stop, found.cost)
        assert [r[2] for r in services_.fill.leases.released] == ["apply_agent_done"]
        assert written == ("superseded" if stop == "application" else found.state.value)
        # Free the posting for the next path's application.
        with factory() as s:
            if found.state is State.submit_unknown:
                gate.not_sent(s, person_a.user_id, app_id, AT)
            s.commit()
        db.expire_all()
    end = State.submit_unknown if after_submit else State.cancelled
    assert ends == {
        "application": (end, gate.CANCELLED_BY_PERSON),
        "activity": (end, gate.CANCELLED_BY_PERSON),
    }


def test_a_cancel_before_the_run_is_created_starts_no_run(
    db: Session, factory: sessionmaker[Session], person_a: Person, monkeypatch: Any
) -> None:
    app_id = started(db, person_a, Mode.direct)
    put(db, app_id, State.filling)
    monkeypatch.setattr(applier.credentials, "get", lambda db, user_id: object())
    session = applier.ApplySession(
        tools=None,
        capability=None,
        stop=lambda: asyncio.sleep(0),  # type: ignore[arg-type]
        arm=lambda: asyncio.sleep(0),  # type: ignore[arg-type]
        flow=runs.WorkflowRef("application", app_id, "apply"),
    )
    cancel(factory, person_a, app_id)
    with factory() as s:
        assert applier.start_run(s, person_a.user_id, session, {}, AT) == "superseded"
        s.commit()
    count = db.scalar(
        sa.select(sa.func.count(AgentRun.id)).where(AgentRun.workflow_run_id == app_id)
    )
    assert count == 0


# --- a confirmation the agent read before the Cancel reached its run ------------------------------

SUMMARY = "Sent, the site thanked us."
THANKS = "https://example.org/thanks"
# Where the run stood, and the end its own end records once the agent read the confirmation.
CONFIRMED = {
    "direct_filling": (Mode.direct, State.filling, False, State.submitted, None),
    "direct_submitting": (Mode.direct, State.submitting, False, State.submitted, None),
    "review_filling": (
        Mode.review,
        State.filling,
        False,
        State.submit_unknown,
        "sent_before_confirm",
    ),
    "review_clicked": (Mode.review, State.submitting, True, State.submitted, None),
}


def reported(
    app_id: uuid.UUID, *, review: bool = False, clicked: bool = False, done: bool = True
) -> applier.ApplySession:
    """The worker's session of the run, after apply_done `submitted` on a page it read."""
    session = applier.ApplySession(
        tools=None,
        capability=None,
        stop=lambda: asyncio.sleep(0),  # type: ignore[arg-type]
        arm=lambda: asyncio.sleep(0),  # type: ignore[arg-type]
        flow=runs.WorkflowRef("application", app_id, "apply"),
        review=review,
    )
    session.clicked = clicked
    if done:
        session.done = ("submitted", SUMMARY, THANKS)
    return session


def finished(
    factory: sessionmaker[Session], app_id: uuid.UUID, session: applier.ApplySession
) -> str:
    with factory() as s:
        written = applier.finish(s, app_id, session, applier.Ran(700, 90, "cancelled"), AT)
        s.commit()
    return written


def kept(db: Session, app_id: uuid.UUID) -> None:
    """The run's report and cost, as the agent left them."""
    found = application(db, app_id)
    assert (found.cost["input_tokens"], found.cost["output_tokens"]) == (700, 90)
    report = db.get(ApplicationAnswer, (app_id, applier.REPORT_KEY))
    assert report is not None
    assert (report.value["summary"], report.value["confirmation_url"]) == (SUMMARY, THANKS)


def begun(db: Session, person: Person, mode: Mode, state: State) -> uuid.UUID:
    app_id = ticked(db, person) if mode is Mode.review else started(db, person, mode)
    put(db, app_id, state)
    return app_id


@pytest.mark.parametrize("case", list(CONFIRMED))
def test_a_run_cancelled_in_activity_after_a_confirmation_it_read_records_the_send(
    db: Session, factory: sessionmaker[Session], person_a: Person, case: str
) -> None:
    mode, state, clicked, end, code = CONFIRMED[case]
    app_id = begun(db, person_a, mode, state)
    session = reported(app_id, review=mode is Mode.review, clicked=clicked)

    written = finished(factory, app_id, session)

    found = application(db, app_id)
    assert (written, found.state, found.error_code) == (end.value, end, code)
    assert "cancelled" not in kinds(db, app_id)
    kept(db, app_id)
    if end is State.submitted:
        assert status(db, person_a) is JobStatus.applied


def test_a_run_cancelled_in_activity_after_a_traced_step_act_says_it_may_have_been_sent(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    from app.apply import run

    app_id = begun(db, person_a, Mode.review, State.filling)
    run._event(db, application(db, app_id), applier.STEP_SENT, AT)
    db.commit()
    session = reported(app_id, review=True, done=False)
    session.step_sent = True

    finished(factory, app_id, session)

    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.submit_unknown, gate.CANCELLED_BY_PERSON)


@pytest.mark.parametrize("case", list(CONFIRMED))
def test_the_application_s_cancel_after_a_confirmation_the_agent_read_ends_with_the_send(
    db: Session, factory: sessionmaker[Session], person_a: Person, case: str
) -> None:
    mode, state, clicked, _, _ = CONFIRMED[case]
    app_id = begun(db, person_a, mode, state)
    session = reported(app_id, review=mode is Mode.review, clicked=clicked)
    # The card's Cancel cannot see the worker's session: it writes the Cancel's end first.
    assert cancel(factory, person_a, app_id).error_code == gate.CANCELLED_BY_PERSON

    finished(factory, app_id, session)

    found = application(db, app_id)
    kept(db, app_id)
    assert found.submitted_at is not None
    if case == "review_filling":
        # A20: a confirmation before the gate's click is a send by a step control, maybe sent.
        assert (found.state, found.error_code) == (State.submit_unknown, gate.CANCELLED_BY_PERSON)
        return
    # The person reads that their Cancel came after the send.
    assert (found.state, found.error_code) == (State.submitted, applier.SENT_BEFORE_CANCEL)
    assert status(db, person_a) is JobStatus.applied


def test_a_send_confirmed_before_the_cancel_leaves_a_new_application_its_posting(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = begun(db, person_a, Mode.direct, State.filling)
    cancel(factory, person_a, app_id)
    again = started(db, person_a, Mode.direct)

    assert finished(factory, app_id, reported(app_id)) == "superseded"

    assert application(db, app_id).state is State.cancelled
    assert application(db, again).state is State.queued
    kept(db, app_id)


@pytest.mark.parametrize(
    ("review", "clicked", "done", "end"),
    [
        (False, False, False, State.submit_unknown),
        (True, True, True, State.submitted),
        (True, True, False, State.submit_unknown),
        (True, False, False, State.submit_unknown),
    ],
)
def test_every_end_after_a_click_keeps_the_run_s_cost(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    review: bool,
    clicked: bool,
    done: bool,
    end: State,
) -> None:
    app_id = begun(db, person_a, Mode.review if review else Mode.direct, State.submitting)
    session = reported(app_id, review=review, clicked=clicked, done=done)
    with factory() as s:
        applier.finish(s, app_id, session, applier.Ran(700, 90), AT)
        s.commit()
    found = application(db, app_id)
    assert found.state is end
    assert found.cost == {"input_tokens": 700, "output_tokens": 90, "acts": 0}


@pytest.mark.parametrize("cancelled", [False, True])
def test_a_cancel_during_the_pace_s_pause_stops_the_act_it_waited_for(cancelled: bool) -> None:
    sent: list[str] = []
    going = {"alive": True}

    class Tools:
        async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            sent.append(name)
            return {"ok": True, "snapshot": '- button "Next" [ref=e1]', "url": THANKS}

    async def alive() -> bool:
        return going["alive"]

    async def pause(seconds: float) -> None:
        going["alive"] = not cancelled  # the person's Cancel lands during the pause

    session = applier.ApplySession(
        tools=Tools(),
        capability=None,
        stop=lambda: asyncio.sleep(0),  # type: ignore[arg-type]
        arm=lambda: asyncio.sleep(0),  # type: ignore[arg-type]
        alive=alive,
        sleep=pause,
        monotonic=lambda: 10.0,
        draw=lambda low, high: 5.0,
    )
    session.last_act = 9.0

    _, error = asyncio.run(session.interact("browser_click", {"ref": "e1"}))

    if cancelled:
        assert (error, sent, session.stopped) == (True, [], "superseded")
    else:
        assert sent == ["browser_click", "browser_snapshot"]


# --- "It was not sent", and Cancel all -----------------------------------------------------------


@pytest.fixture
def api_app(migrated_url: str, forms_base_url: str) -> FastAPI:
    settings = make_settings(
        migrated_url, secret_key=SECRET_KEY, apply_stub_base_url=forms_base_url
    )
    app = create_app(settings, clock=lambda: AT)
    app.state.queue = FakeQueue()
    return app


def test_it_was_not_sent_ends_it_cancelled_and_the_person_may_apply_again(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    person_b: Person,
    api_app: FastAPI,
) -> None:
    _password(factory, person_a, person_b)
    app_id = started(db, person_a, Mode.direct)
    put(db, app_id, State.submit_unknown, error_code=gate.CANCELLED_BY_PERSON)
    path = f"/api/applications/{app_id}"
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        token, token_b = api.login_token(person_a.email), api.login_token(person_b.email)
        # A Cancel never says it was not sent: only the person's own word does.
        refused = api.post(f"{path}/cancel", token=token)
        assert (refused.status_code, refused.json()["code"]) == (409, "not_cancellable")
        assert api.post(f"{path}/not-sent", token=token_b).status_code == 404
        answer = api.post(f"{path}/not-sent", token=token)
        assert answer.status_code == 200
        assert answer.json()["state"] == "cancelled"
        again = api.post(f"{path}/not-sent", token=token)
        assert (again.status_code, again.json()["code"]) == (409, "not_unknown")
    found = application(db, app_id)
    assert (found.state, found.error_code, found.submitted_at) == (
        State.cancelled,
        gate.NOT_SENT,
        None,
    )
    said = [e for e in events(db, app_id) if e.kind == "cancelled"]
    assert said[-1].data == {"code": gate.NOT_SENT}
    # The app never sends it again by itself: the person's own Apply starts a new one.
    again_id = started(db, person_a, Mode.direct)
    assert again_id != app_id


def test_cancel_all_stops_exactly_what_the_confirmation_named(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    person_b: Person,
    forms_base_url: str,
    api_app: FastAPI,
) -> None:
    from tests.apply.conftest import add_posting

    _password(factory, person_a, person_b)
    first = started(db, person_a, Mode.direct)
    posting_2, posting_3, posting_4 = (
        add_posting(db, person_a.user_id, f"{forms_base_url}/ashby/example-robotics/a24-{n}")[0]
        for n in (2, 3, 4)
    )
    db.commit()
    sending = service.start(db, person_a.user_id, posting_2, Mode.direct, False, now=AT)
    ended = service.start(db, person_a.user_id, posting_3, Mode.direct, False, now=AT)
    unnamed = service.start(db, person_a.user_id, posting_4, Mode.direct, False, now=AT)
    second, third, fourth = (one.application.id for one in (sending, ended, unnamed))
    put(db, first, State.filling)
    put(db, second, State.submitting)
    put(db, third, State.submitted)
    theirs = started(db, person_b, Mode.direct)
    run_id = agent_run(db, person_a, first)
    named = [first, second, third, theirs]
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        token = api.login_token(person_a.email)
        twice = api.post(
            "/api/apply/queue/cancel",
            token=token,
            json={"application_ids": [str(first), str(first)]},
        )
        assert twice.status_code == 422
        answer = api.post(
            "/api/apply/queue/cancel",
            token=token,
            json={"application_ids": [str(one) for one in named]},
        )
    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "no-store"
    out = answer.json()
    assert [r["application_id"] for r in out["results"]] == [str(one) for one in named]
    assert (out["stopped"], out["left_out"]) == (2, 2)
    results = {r["application_id"]: r for r in out["results"]}
    assert (results[str(first)]["stopped"], results[str(first)]["state"]) == (True, "cancelled")
    assert results[str(second)]["state"] == "submit_unknown"
    assert results[str(third)]["code"] == "not_cancellable"
    assert results[str(third)]["detail"] == gate.SENTENCES["not_cancellable"]
    assert results[str(theirs)]["code"] == "not_found"
    assert application(db, fourth).state is State.queued
    assert application(db, theirs).state is State.queued
    assert run_row(db, run_id).cancel_requested_at is not None
    stopped = db.scalars(
        sa.select(ApplicationEvent.application_id).where(ApplicationEvent.kind == "cancelled")
    ).all()
    assert set(stopped) == {first, second}
