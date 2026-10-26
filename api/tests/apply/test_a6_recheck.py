"""Apply A6, second round (review of #315): the gate's checks again between the decision and the
click (`submit.recheck`, in `prepare` and in `arm`, under the gate's locks), one click for two
jobs at once, a confirmed submission whose posting status cannot be set, the pause's status line,
and the person's own answers (`PUT /api/applications/{id}/answers/{field}`)."""

from __future__ import annotations

import asyncio
import dataclasses
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.apply import admin, gate, guardrails, states, submit
from app.apply.models import AnswerSource, Application, ApplicationAnswer, State
from app.jobs import service as jobs_service
from app.jobs.models import Job, JobStatus
from app.main import create_app
from tests.apply.a4_support import Spec
from tests.apply.a5_support import FakeAgent
from tests.apply.a6_support import FakeQueue, FakeRedis, GateLeases, SubmitForm, submit_ashby
from tests.apply.conftest import AT, Person, add_posting
from tests.apply.test_a5_run import (
    WHY,
    application,
    events,
    fill,
    run_ashby,
    services,
    started,
)
from tests.apply.test_a6_gate import (
    SECRET_KEY,
    TTL,
    _password,
    approve,
    frozen,
    ready,
    run_submit,
    submit_services,
)
from tests.conftest import make_settings
from tests.identity.api_helpers import Api

BARRIER_S = 60  # a guard against a hang only: both jobs reach the dwell at once


@pytest.fixture
def stub(forms_base_url: str) -> str:
    return forms_base_url


@pytest.fixture
def api_app(migrated_url: str, stub: str) -> FastAPI:
    settings = make_settings(migrated_url, secret_key=SECRET_KEY, apply_stub_base_url=stub)
    app = create_app(settings, clock=lambda: AT)
    app.state.queue = FakeQueue()
    return app


def at(when: datetime) -> Any:
    return lambda: when


def clicks(*forms: SubmitForm) -> int:
    return sum(len(f.gate_clicks) for f in forms)


def at_the_dwell(form: SubmitForm, then: Any) -> None:
    """Run `then()` once, when the submit lease starts the dwell before the click."""
    done: list[bool] = []

    def hook(f: Any, _: int) -> None:
        name, args = f.calls[-1]
        if name == "wait_for" and args.get("time") == submit.DWELL_S and not done:
            done.append(True)
            then()

    form.on_call = hook


def second_board(db: Session, person: Person, stub: str) -> Person:
    """The same role (company and title) on another posting with another form address."""
    posting, versions = add_posting(
        db, person.user_id, stub + "/ashby/example-robotics/applied-ai-engineer-eu/application"
    )
    db.commit()
    return dataclasses.replace(
        person,
        posting_id=posting,
        cv_version_id=versions[next(k for k in versions if k.value == "cv")],
        letter_version_id=versions[next(k for k in versions if k.value == "letter")],
    )


def set_posting(factory: sessionmaker[Session], person: Person, to: JobStatus) -> None:
    with factory() as s:
        posting = jobs_service.lock_job(s, person.user_id, person.posting_id)
        assert posting is not None
        jobs_service.set_status(s, posting, to, AT, via="mail")
        s.commit()


# --- the approval's validity at the click -----------------------------------------------------


@pytest.mark.parametrize(
    ("after", "clicked"),
    [(TTL - 1, True), (TTL, False), (TTL + 1, False), (5 * 86_400, False)],
)
def test_an_approval_is_clicked_only_within_its_validity(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    after: int,
    clicked: bool,
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id, now=AT).passed
    form, leases = submit_ashby(), GateLeases()
    svc = submit_services(factory, form, stub, leases=leases)
    svc.clock = at(AT + timedelta(seconds=after))
    written = run_submit(svc, app_id)
    found = application(db, app_id)
    if clicked:
        assert written == "submitted"
        assert len(form.gate_clicks) == 1
        return
    assert written == "authorisation_expired"
    assert (found.state, found.error_code) == (State.ready, "authorisation_expired")
    assert form.gate_clicks == []
    assert leases.acquired == []  # stopped before the browser
    assert frozen(db, person_a.cv_version_id) == [None]


def test_an_approval_that_expires_during_the_refill_is_not_clicked(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id, now=AT).passed
    form = submit_ashby()
    svc = submit_services(factory, form, stub)
    times = iter([AT + timedelta(seconds=TTL - 1)])
    svc.clock = lambda: next(times, AT + timedelta(seconds=TTL))
    assert run_submit(svc, app_id) == "ready"
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.ready, "authorisation_expired")
    assert form.gate_clicks == []
    assert any(c[0] == "wait_for" for c in form.calls)  # it got as far as the dwell


# --- the posting's status at the click --------------------------------------------------------


@pytest.mark.parametrize(
    ("to", "state", "code"),
    [
        (JobStatus.applied, State.blocked, "already_applied"),
        (JobStatus.closed, State.posting_closed, "posting_closed"),
    ],
)
@pytest.mark.parametrize("when", ["before_the_lease", "at_the_dwell"])
def test_a_posting_applied_or_closed_after_the_decision_is_never_clicked(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    to: JobStatus,
    state: State,
    code: str,
    when: str,
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    form = submit_ashby()
    if when == "before_the_lease":
        set_posting(factory, person_a, to)
    else:
        at_the_dwell(form, lambda: set_posting(factory, person_a, to))
    run_submit(submit_services(factory, form, stub), app_id)
    found = application(db, app_id)
    assert (found.state, found.error_code) == (state, code)
    assert form.gate_clicks == []
    assert found.submitted_at is None
    # never frozen by apply (an applied status freezes by its own reason, documents D14)
    assert frozen(db, person_a.cv_version_id) != ["apply"]


# --- one role, two boards ---------------------------------------------------------------------


def test_the_same_role_on_two_boards_approved_in_turn_is_clicked_once(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    other = second_board(db, person_a, stub)
    first, second = ready(db, factory, person_a, stub), ready(db, factory, other, stub)
    assert approve(factory, person_a, first).passed
    assert approve(factory, other, second).passed  # nothing clicked yet: both pass the gate
    one, two = submit_ashby(), submit_ashby()
    assert run_submit(submit_services(factory, one, stub), first) == "submitted"
    assert run_submit(submit_services(factory, two, stub), second) == "same_role"
    found = application(db, second)
    assert (found.state, found.error_code) == (State.blocked, "same_role")
    assert len(one.gate_clicks) == 1
    assert two.gate_clicks == []


def test_the_same_role_on_two_boards_submitted_at_once_is_clicked_once(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    other = second_board(db, person_a, stub)
    first, second = ready(db, factory, person_a, stub), ready(db, factory, other, stub)
    assert approve(factory, person_a, first).passed
    assert approve(factory, other, second).passed
    barrier = threading.Barrier(2, timeout=BARRIER_S)
    forms = {first: submit_ashby(), second: submit_ashby()}
    for form in forms.values():
        at_the_dwell(form, barrier.wait)

    def job(app_id: uuid.UUID) -> str:
        return run_submit(submit_services(factory, forms[app_id], stub), app_id)

    with ThreadPoolExecutor(2) as pool:
        results = dict(zip(forms, pool.map(job, forms), strict=True))
    assert sorted(results.values()) == ["blocked", "submitted"]
    assert clicks(*forms.values()) == 1
    (lost,) = [a for a, r in results.items() if r == "blocked"]
    assert application(db, lost).error_code == "same_role"


def test_a_click_under_way_on_the_same_role_is_a_duplicate(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    """`submitting` has no `submitted_at` yet: the same role elsewhere is refused all the same."""
    other = second_board(db, person_a, stub)
    first, second = ready(db, factory, person_a, stub), ready(db, factory, other, stub)
    assert approve(factory, person_a, first).passed
    with factory() as s:
        found = s.get(Application, first)
        assert found is not None
        states.move(found, State.submitting, AT)
        s.commit()
    with factory() as s:
        posting = s.get(Job, other.posting_id)
        assert posting is not None
        key = application(db, second).canonical_key
        refusal = guardrails.duplicates(s, person_a.user_id, posting, key, AT, exclude=second)
    assert refusal is not None
    assert refusal.code == "same_role"


def test_two_submit_jobs_at_once_on_one_approval_click_once(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    barrier = threading.Barrier(2, timeout=BARRIER_S)
    forms = [submit_ashby(), submit_ashby()]
    for form in forms:
        at_the_dwell(form, barrier.wait)
    with ThreadPoolExecutor(2) as pool:
        results = sorted(
            pool.map(lambda f: run_submit(submit_services(factory, f, stub), app_id), forms)
        )
    assert results == ["submitted", "superseded"]
    assert clicks(*forms) == 1
    assert application(db, app_id).state is State.submitted


# --- a confirmed submission is never lost ------------------------------------------------------


@pytest.mark.parametrize("failure", ["python", "database"])
def test_a_confirmed_submission_whose_status_cannot_be_set_stays_submitted(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """`database`: the status write goes through and then the statement after it fails in
    Postgres, which aborts the transaction unless the write sits in a savepoint (`begin_nested`):
    without it the record of the submission would fail too."""
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed

    def refused(session: Session, posting: Job, *args: Any, **kw: Any) -> bool:
        if failure == "database":
            session.execute(
                sa.update(Job).where(Job.id == posting.id).values(status=JobStatus.applied)
            )
            session.execute(sa.text("SELECT 1 / 0"))
        raise RuntimeError("refused")

    monkeypatch.setattr(submit.jobs_service, "set_status", refused)
    form = submit_ashby()
    assert run_submit(submit_services(factory, form, stub), app_id) == "submitted"
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.submitted, "status_not_set")
    assert found.submitted_at is not None
    assert len(form.gate_clicks) == 1
    assert frozen(db, person_a.cv_version_id, person_a.letter_version_id) == ["apply", "apply"]
    db.expire_all()
    assert db.get(Job, person_a.posting_id).status is JobStatus.staged  # type: ignore[union-attr]
    kinds = [e.kind for e in events(db, app_id)]
    assert {"status_not_set", "submitted"} <= set(kinds)
    assert gate.SENTENCES["status_not_set"]


# --- the table and the pause -------------------------------------------------------------------


def test_a_rehearsal_is_only_recorded_after_the_gate() -> None:
    sources = {s for s, targets in states.TRANSITIONS.items() if State.rehearsed in targets}
    assert sources == {State.approved, State.held}


def test_the_pause_status_says_when_it_cannot_be_read() -> None:
    redis = FakeRedis()
    assert asyncio.run(admin.status(redis)) == "running"
    asyncio.run(admin.pause(redis))
    assert asyncio.run(admin.status(redis)) == "paused"
    assert asyncio.run(admin.status(FakeRedis(broken=True))) == "unknown, treated as paused"


# --- the person's own answers ------------------------------------------------------------------


def _row(factory: sessionmaker[Session], app_id: uuid.UUID, label: str) -> ApplicationAnswer:
    with factory() as s:
        rows = s.execute(
            sa.select(ApplicationAnswer).where(ApplicationAnswer.application_id == app_id)
        ).scalars()
        return next(r for r in rows if r.label == label)


def _left(factory: sessionmaker[Session], app_id: uuid.UUID) -> ApplicationAnswer:
    """The field the fill left for the person: the location typeahead (see `run_ashby`)."""
    row = _row(factory, app_id, "Current location")
    assert (row.source, row.kind) == (AnswerSource.left, "combobox")
    return row


def test_the_person_answers_a_field_left_for_them(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    person_b: Person,
    stub: str,
    api_app: FastAPI,
) -> None:
    app_id = ready(db, factory, person_a, stub)
    left = _left(factory, app_id)
    _password(factory, person_a, person_b)
    mine = "Marker " + uuid.uuid4().hex
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        token_a, token_b = api.login_token(person_a.email), api.login_token(person_b.email)
        base = f"/api/applications/{app_id}"
        path = f"{base}/answers/{quote(left.field_key, safe='')}"  # encodeURIComponent
        before = api.get(f"{base}/approval", token=token_a).json()["answers_hash"]

        assert api.request("PUT", path, token=token_b, json={"value": mine}).status_code == 404
        no_csrf = api.request("PUT", path, token=token_a, csrf=False, json={"value": mine})
        assert no_csrf.status_code == 403
        unknown = api.request(
            "PUT", f"{base}/answers/no-such-field", token=token_a, json={"value": mine}
        )
        assert (unknown.status_code, unknown.json()["code"]) == (422, "unknown_field")
        wrong = api.request("PUT", path, token=token_a, json={"value": True})
        assert (wrong.status_code, wrong.json()["code"]) == (422, "wrong_type")
        too_long = api.request("PUT", path, token=token_a, json={"value": "x" * 5001})
        assert too_long.status_code == 422

        first = api.request("PUT", path, token=token_a, json={"value": "first"})
        assert first.status_code == 200, first.text
        assert first.headers["cache-control"] == "no-store"
        written = api.request("PUT", path, token=token_a, json={"value": mine})
        assert written.status_code == 200
        view = written.json()
        (row,) = [a for a in view["answers"] if a["field_key"] == left.field_key]
        assert (row["value"], row["source"], row["you_answer"]) == (mine, "person", False)
        assert view["answers_hash"] != before
        assert view["answers_hash"] != first.json()["answers_hash"]
        # the approval of the form before the answer no longer matches
        stale = api.post(f"{base}/approve", token=token_a, json={"answers_hash": before})
        assert (stale.status_code, stale.json()["code"]) == (409, "stale")
        assert mine not in str([e.data for e in events(db, app_id)])


def test_an_answer_after_the_approval_voids_it_and_is_refilled_at_the_click(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    left = _row(factory, app_id, WHY)
    assert approve(factory, person_a, app_id).passed
    mine = "Marker " + uuid.uuid4().hex
    with factory() as s:
        gate.set_answer(s, person_a.user_id, app_id, left.field_key, mine, AT)
        s.commit()
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.ready, "stale")
    assert approve(factory, person_a, app_id).passed
    form = submit_ashby()
    assert run_submit(submit_services(factory, form, stub), app_id) == "submitted"
    assert mine in [c[1]["text"] for c in form.calls if c[0] == "type"]
    assert len(form.gate_clicks) == 1


@pytest.mark.parametrize("value", [None, "  My exact answer.  ", "Remote, please", "I hold a PhD."])
def test_a_refill_keeps_the_person_s_answer(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str, value: str | None
) -> None:
    app_id = ready(db, factory, person_a, stub)
    left = _row(factory, app_id, WHY)
    mine = value if value is not None else "Marker " + uuid.uuid4().hex
    with factory() as s:
        gate.set_answer(s, person_a.user_id, app_id, left.field_key, mine, AT)
        found = s.get(Application, app_id)
        assert found is not None
        states.move(found, State.queued, AT)
        s.commit()
    agent = FakeAgent(lambda q: ("The agent's answer", "motivation"))
    form = run_ashby()
    assert fill(services(factory, form, None, agent, stub_base=stub), app_id) == "ready"
    assert not agent.requests
    assert any(name == "type" and args.get("text") == mine for name, args in form.calls)
    with factory() as s:
        row = s.get(ApplicationAnswer, (app_id, left.field_key))
        assert row is not None
        assert (row.value, row.source) == (mine, AnswerSource.person)


NEWS = "Send me news about Example Robotics."


def test_a_box_the_person_left_unticked_is_not_ticked_at_the_click(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    """A7's journey on the fake Greenhouse form: the person answers No to a box the fill left
    (the marketing consent). The refill types nothing for it, the read back sees it unticked,
    and the click goes; before, the refill took the adapter's `left` for a failure and stopped
    on `form_changed`."""
    app_id = started(db, person_a)
    form = run_ashby()
    form.fields.append(Spec(NEWS, "checkbox"))
    agent = FakeAgent(lambda q: ("I want to build agents.", "motivation"))
    assert fill(services(factory, form, None, agent, stub_base=stub), app_id) == "ready"
    box = _row(factory, app_id, NEWS)
    assert (box.kind, box.source, box.value) == ("checkbox", AnswerSource.left, None)
    with factory() as s:
        gate.set_answer(s, person_a.user_id, app_id, box.field_key, False, AT)
        s.commit()
    assert approve(factory, person_a, app_id).passed
    news = Spec(NEWS, "checkbox")
    clicked = submit_ashby()
    clicked.fields.append(news)
    assert run_submit(submit_services(factory, clicked, stub), app_id) == "submitted"
    assert not news.checked
    assert len(clicked.gate_clicks) == 1


def _waiting_for_required(
    factory: sessionmaker[Session], app_id: uuid.UUID, needs: str, keys: list[str]
) -> None:
    """The application as a fill that stopped with `needs` leaves it: `keys` required and left."""
    with factory() as s:
        for key in keys:
            row = s.get(ApplicationAnswer, (app_id, key))
            assert row is not None
            row.required, row.value, row.source = True, None, AnswerSource.left
        found = s.get(Application, app_id)
        assert found is not None
        states.move(found, State.needs_person, AT)
        found.needs = needs
        s.commit()


def test_the_last_required_field_answered_makes_the_form_ready(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    """A7's journey on the fake Greenhouse form: the fill left a required question (work
    authorisation), so it ended `needs_person` with `required_left`. Once the person answered
    every required field the form waits for the approval, and only then."""
    app_id = ready(db, factory, person_a, stub)
    keys = [_left(factory, app_id).field_key, _row(factory, app_id, WHY).field_key]
    _waiting_for_required(factory, app_id, "required_left", keys)
    with factory() as s:
        gate.set_answer(s, person_a.user_id, app_id, keys[0], "Paris", AT)
        s.commit()
    found = application(db, app_id)
    assert (found.state, found.needs) == (State.needs_person, "required_left")
    with factory() as s:
        gate.set_answer(s, person_a.user_id, app_id, keys[1], "Marker " + uuid.uuid4().hex, AT)
        s.commit()
    found = application(db, app_id)
    assert (found.state, found.needs) == (State.ready, None)
    assert approve(factory, person_a, app_id).passed


@pytest.mark.parametrize("needs", ["captcha", "email_code", "signin", "form_changed"])
def test_an_answer_does_not_end_a_step_that_is_not_a_left_field(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str, needs: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    key = _left(factory, app_id).field_key
    _waiting_for_required(factory, app_id, needs, [key])
    with factory() as s:
        if needs == "signin":  # behind a sign in wall, no answer before the fill after it
            with pytest.raises(gate.RefusedError, match="signin_first"):
                gate.set_answer(s, person_a.user_id, app_id, key, "Paris", AT)
        else:
            gate.set_answer(s, person_a.user_id, app_id, key, "Paris", AT)
        s.commit()
    found = application(db, app_id)
    assert (found.state, found.needs) == (State.needs_person, needs)


def test_only_fields_of_the_form_before_the_click_take_an_answer(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    with factory() as s:
        rows = list(
            s.execute(
                sa.select(ApplicationAnswer).where(ApplicationAnswer.application_id == app_id)
            ).scalars()
        )
    files = [r.field_key for r in rows if r.kind == "file"]
    assert files
    with factory() as s:
        for key in (*files, "skipped:0:x", "nothing"):
            with pytest.raises(gate.InvalidAnswerError, match="unknown_field"):
                gate.set_answer(s, person_a.user_id, app_id, key, "x", AT)
            s.rollback()
        with pytest.raises(gate.NotFoundError):
            gate.set_answer(s, uuid.uuid4(), app_id, files[0], "x", AT)
        s.rollback()
    with factory() as s:
        found = s.get(Application, app_id)
        assert found is not None
        states.move(found, State.cancelled, AT)
        s.commit()
    with factory() as s, pytest.raises(gate.RefusedError, match="not_editable"):
        gate.set_answer(s, person_a.user_id, app_id, _left(factory, app_id).field_key, "x", AT)


# --- the kinds of field the person can answer --------------------------------------------------

CHOICES = ["Remote", "On site"]


def _make(
    factory: sessionmaker[Session],
    app_id: uuid.UUID,
    key: str,
    kind: str,
    options: list[str] | None = None,
) -> None:
    """Turn a stored field into one of `kind` with `options`, as the form would have offered."""
    with factory() as s:
        row = s.get(ApplicationAnswer, (app_id, key))
        assert row is not None
        row.kind, row.options = kind, options
        s.commit()


def _put(
    factory: sessionmaker[Session], person: Person, app_id: uuid.UUID, key: str, value: Any
) -> str:
    """The code of the refusal, or `ok`."""
    with factory() as s:
        try:
            gate.set_answer(s, person.user_id, app_id, key, value, AT)
            s.commit()
        except gate.InvalidAnswerError as exc:
            s.rollback()
            return exc.code
        return "ok"


# (kind, options, accepted, wrong type, not an option): a missing entry is a case that does not
# apply to the kind
KINDS: list[tuple[str, list[str] | None, Any, Any, Any]] = [
    ("text", None, "Bordeaux", True, None),
    ("textarea", None, "Why I want to join.\nSecond line.", True, None),
    ("select", CHOICES, "Remote", True, "Hybrid"),
    ("combobox", CHOICES, "On site", False, "remote"),
    ("single", CHOICES, "Remote", True, " Remote"),
    ("date", None, "2026-11-02", True, None),
    ("checkbox", None, True, "yes", None),
    ("yes_no", None, False, "no", None),
]


@pytest.mark.parametrize(("kind", "options", "accepted", "wrong", "other"), KINDS)
def test_every_kind_of_field_takes_its_own_type_of_value(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    kind: str,
    options: list[str] | None,
    accepted: Any,
    wrong: Any,
    other: Any,
) -> None:
    app_id = ready(db, factory, person_a, stub)
    key = _left(factory, app_id).field_key
    _make(factory, app_id, key, kind, options)
    assert _put(factory, person_a, app_id, key, wrong) == "wrong_type"
    if other is not None:
        assert _put(factory, person_a, app_id, key, other) == "not_an_option"
    assert _put(factory, person_a, app_id, key, accepted) == "ok"
    row = _row(factory, app_id, "Current location")
    assert (row.value, row.source) == (accepted, AnswerSource.person)


@pytest.mark.parametrize("kind", ["select", "combobox", "single"])
def test_a_choice_without_known_options_is_taken_as_typed(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str, kind: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    key = _left(factory, app_id).field_key
    _make(factory, app_id, key, kind)
    assert _put(factory, person_a, app_id, key, "Anything") == "ok"


@pytest.mark.parametrize(
    "value", ["02/11/2026", "2026-02-30", "2026-13-01", "20261102", "2026-11-2"]
)
def test_a_date_is_an_iso_calendar_date(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str, value: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    key = _left(factory, app_id).field_key
    _make(factory, app_id, key, "date")
    assert _put(factory, person_a, app_id, key, value) == "wrong_type"


@pytest.mark.parametrize(("kind", "code"), [("multi", "wrong_type"), ("file", "unknown_field")])
def test_a_field_that_takes_no_single_value_refuses_one(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str, kind: str, code: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    key = _left(factory, app_id).field_key
    _make(factory, app_id, key, kind, CHOICES)
    assert _put(factory, person_a, app_id, key, "Remote") == code


def test_the_approval_view_offers_the_options_and_the_value_is_a_strict_boolean(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    person_b: Person,
    stub: str,
    api_app: FastAPI,
) -> None:
    app_id = ready(db, factory, person_a, stub)
    key = _left(factory, app_id).field_key
    _make(factory, app_id, key, "select", CHOICES)
    _password(factory, person_a, person_b)
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        token = api.login_token(person_a.email)
        base = f"/api/applications/{app_id}"
        path = f"{base}/answers/{quote(key, safe='')}"
        view = api.get(f"{base}/approval", token=token).json()
        shown = {a["field_key"]: a for a in view["answers"]}
        assert shown[key]["options"] == CHOICES
        with factory() as s:  # the run stored what the form offered, and the view returns it
            stored = {
                r.field_key: r.options
                for r in s.execute(
                    sa.select(ApplicationAnswer).where(ApplicationAnswer.application_id == app_id)
                ).scalars()
            }
        assert {k: a["options"] for k, a in shown.items()} == stored
        assert any(o for k, o in stored.items() if k != key)  # another field has its own
        assert any(o is None for o in stored.values())  # and a field without options reads null
        refused = api.request("PUT", path, token=token, json={"value": "Hybrid"})
        assert (refused.status_code, refused.json()["code"]) == (422, "not_an_option")
        chosen = api.request("PUT", path, token=token, json={"value": "On site"})
        assert chosen.status_code == 200, chosen.text
        (row,) = [a for a in chosen.json()["answers"] if a["field_key"] == key]
        assert (row["value"], row["options"]) == ("On site", CHOICES)
        for kind in ("checkbox", "yes_no"):
            _make(factory, app_id, key, kind)
            for bad in (1, 0, "true"):
                got = api.request("PUT", path, token=token, json={"value": bad})
                assert got.status_code == 422, (kind, bad)
            ok = api.request("PUT", path, token=token, json={"value": True})
            assert ok.status_code == 200, ok.text
