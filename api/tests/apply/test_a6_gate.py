"""The gate, the approval and the submission of A6 (tasks/apply/todo.md, "A6"), on a real
Postgres, A5's fill on A4's modelled Ashby form, and the fakes of a6_support: no browser, no
model, no company. Every submission counted here is a call of the fake form's `gate_click`."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app import worker
from app.apply import admin, gate, run, shots, states, submit
from app.apply.models import (
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    Authorisation,
    ShotKind,
    State,
)
from app.documents import store
from app.documents.models import Author, DocumentVersion, FitResult, Kind, Origin
from app.identity import security
from app.jobs import service as jobs_service
from app.jobs.models import Job, JobStatus
from app.main import create_app
from tests.apply.a5_support import FakeAgent
from tests.apply.a6_support import (
    CAPABILITY,
    FakeQueue,
    FakeRedis,
    GateLeases,
    SubmitForm,
    fake_click,
    submit_ashby,
)
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import (
    WHY,
    Clock,
    application,
    events,
    fill,
    run_ashby,
    services,
    started,
    status,
)
from tests.conftest import make_settings
from tests.documents.conftest import load
from tests.identity.api_helpers import PASSWORD, Api
from tests.profile.conftest import full

ON = gate.Switches(enabled=True, paused=False)
TTL = 86_400


@pytest.fixture
def stub(forms_base_url: str) -> str:
    return forms_base_url


def ready(
    db: Session,
    factory: sessionmaker[Session],
    person: Person,
    stub: str,
    *,
    dry_run: bool = False,
    why: str = "I want to build agents for warehouse operations.",
) -> uuid.UUID:
    """An application filled by A5 and waiting for the approval."""
    app_id = started(db, person, dry_run=dry_run)
    agent = FakeAgent(lambda q: (why, "motivation") if q.label == WHY else ("leave", ""))
    assert fill(services(factory, run_ashby(), None, agent, stub_base=stub), app_id) == "ready"
    return app_id


def approve(
    factory: sessionmaker[Session],
    person: Person,
    app_id: uuid.UUID,
    *,
    switches: gate.Switches = ON,
    now: Any = AT,
) -> gate.Decision:
    with factory() as db:
        found = db.get(Application, app_id)
        assert found is not None
        sent = gate.current_hash(db, found)
        authorisation = gate.authorise_approval(db, person.user_id, app_id, sent, now, TTL)
        decision = gate.decide(db, person.user_id, authorisation.id, now, switches=switches)
        db.commit()
        return decision


def submit_services(
    factory: sessionmaker[Session],
    form: SubmitForm,
    stub: str,
    *,
    leases: GateLeases | None = None,
    paused: bool | list[bool] = False,
    enabled: bool = True,
) -> submit.Services:
    async def is_paused() -> bool:
        return paused.pop(0) if isinstance(paused, list) else paused

    return submit.Services(
        sessions=factory,
        clock=Clock(),
        leases=leases or GateLeases(),
        tools_for=lambda user, lease, token: form,
        click=fake_click,
        paused=is_paused,
        enabled=enabled,
        stub_base=stub,
    )


def run_submit(svc: submit.Services, app_id: uuid.UUID) -> str:
    return asyncio.run(submit.submit(svc, app_id))


def frozen(db: Session, *version_ids: uuid.UUID) -> list[str | None]:
    db.expire_all()
    rows = db.execute(
        sa.select(DocumentVersion.id, DocumentVersion.frozen_reason).where(
            DocumentVersion.id.in_(version_ids)
        )
    ).all()
    found = {vid: reason for vid, reason in rows}
    return [getattr(found.get(v), "value", found.get(v)) for v in version_ids]


def kinds(db: Session, app_id: uuid.UUID) -> list[str]:
    return [e.kind for e in events(db, app_id)]


# --- the whole path ------------------------------------------------------------------------------


def test_an_approval_submits_once_and_records_the_application(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    decision = approve(factory, person_a, app_id)
    assert decision.passed
    assert decision.state is State.approved
    found = application(db, app_id)
    assert found.answers_hash is not None
    assert all(r.approved for r in db.execute(sa.select(ApplicationAnswer)).scalars())

    form, leases = submit_ashby(), GateLeases()
    assert run_submit(submit_services(factory, form, stub, leases=leases), app_id) == "submitted"

    found = application(db, app_id)
    assert found.state is State.submitted
    assert found.submitted_at is not None
    posting = db.get(Job, person_a.posting_id)
    assert posting is not None
    assert posting.status is JobStatus.applied
    assert posting.applied_on is not None
    assert frozen(db, person_a.cv_version_id, person_a.letter_version_id) == ["apply", "apply"]
    # exactly one click, the gate's, on the submit element, with the lease's capability
    assert [c["what"] for c in form.gate_clicks] == ["submit"]
    assert form.gate_clicks[0]["capability"] == CAPABILITY
    assert form.submit_clicks == 0  # the adapter never clicked it
    (asked,) = leases.acquired
    assert asked["purpose"] == "apply_submit"
    assert len(leases.released) == 1
    # the approved values were typed again, the left fields were not touched
    typed = [c[1]["text"] for c in form.calls if c[0] == "type"]
    assert "I want to build agents for warehouse operations." in typed
    assert set(form.placed) == {"cv.pdf", "letter.pdf"}
    taken = [s.kind.value for s in shots.of_application(db, person_a.user_id, app_id)]
    assert taken == ["before", "before", "after"]
    assert {"authorisation", "decision", "click", "submitted"} <= set(kinds(db, app_id))
    # the dwell came before the click
    order = [c[0] for c in form.calls]
    assert order.index("wait_for") < order.index("gate_click")


def test_a_rehearsal_goes_through_the_gate_and_stops_before_the_click(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub, dry_run=True)
    assert approve(factory, person_a, app_id).passed
    form = submit_ashby()
    assert run_submit(submit_services(factory, form, stub), app_id) == "rehearsed"
    assert application(db, app_id).state is State.rehearsed
    assert form.gate_clicks == []
    assert status(db, person_a) is JobStatus.staged
    assert frozen(db, person_a.cv_version_id) == [None]
    assert "before" in [s.kind.value for s in shots.of_application(db, person_a.user_id, app_id)]
    assert "submitting" not in [e.data.get("state") for e in events(db, app_id)]
    # and the table refuses it outright
    rehearsal = Application(id=uuid.uuid4(), state=State.approved, dry_run=True)
    with pytest.raises(states.TransitionError, match="rehearsal"):
        states.move(rehearsal, State.submitting, AT)


# --- the approval --------------------------------------------------------------------------------


def test_a_stale_hash_is_refused_with_the_current_one(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    found = application(db, app_id)
    seen = gate.current_hash(db, found)
    db.execute(
        sa.update(ApplicationAnswer)
        .where(ApplicationAnswer.application_id == app_id, ApplicationAnswer.label == WHY)
        .values(value="Another answer")
    )
    db.commit()
    with factory() as other, pytest.raises(gate.StaleError) as caught:
        gate.authorise_approval(other, person_a.user_id, app_id, seen, AT, TTL)
    assert caught.value.current != seen
    assert caught.value.current == gate.current_hash(db, application(db, app_id))
    assert db.execute(sa.select(sa.func.count()).select_from(Authorisation)).scalar() == 0


def test_the_hash_changes_with_every_value_source_field_and_version(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    found = application(db, app_id)
    rows = gate.rows_of(db, found)
    base = gate.answers_hash(rows, found.document_versions)
    assert gate.answers_hash(list(reversed(rows)), found.document_versions) == base
    row = next(r for r in rows if r.label == WHY)
    for attr, new in (("value", "x"), ("source", "person"), ("required", not row.required)):
        old = getattr(row, attr)
        setattr(row, attr, new)
        assert gate.answers_hash(rows, found.document_versions) != base, attr
        setattr(row, attr, old)
    assert gate.answers_hash(rows[1:], found.document_versions) != base
    versions = json.loads(json.dumps(found.document_versions))
    versions["cv"]["version_id"] = str(uuid.uuid4())
    assert gate.answers_hash(rows, versions) != base
    db.rollback()


def test_an_approval_is_used_once(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    with factory() as s:
        found = s.get(Application, app_id)
        assert found is not None
        auth = gate.authorise_approval(
            s, person_a.user_id, app_id, gate.current_hash(s, found), AT, TTL
        )
        auth_id = auth.id
        assert gate.decide(s, person_a.user_id, auth_id, AT, switches=ON).passed
        s.commit()
    with factory() as s:
        again = gate.decide(s, person_a.user_id, auth_id, AT, switches=ON)
        s.commit()
    assert not again.passed
    assert again.code == "authorisation_used"
    assert application(db, app_id).state is State.approved
    outcomes = [e.data["outcome"] for e in events(db, app_id) if e.kind == "decision"]
    assert outcomes == ["approved", "refused"]


@pytest.mark.parametrize(("after", "passed"), [(TTL - 1, True), (TTL, False), (TTL + 1, False)])
def test_an_expired_approval_is_refused(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    after: int,
    passed: bool,
) -> None:
    app_id = ready(db, factory, person_a, stub)
    with factory() as s:
        found = s.get(Application, app_id)
        assert found is not None
        auth = gate.authorise_approval(
            s, person_a.user_id, app_id, gate.current_hash(s, found), AT, TTL
        )
        s.commit()
        later = AT + timedelta(seconds=after)
        decision = gate.decide(s, person_a.user_id, auth.id, later, switches=ON)
        s.commit()
    assert decision.passed is passed
    if not passed:
        assert decision.code == "authorisation_expired"
        assert application(db, app_id).state is State.ready


def test_two_approvals_at_once_make_one_decision(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    with factory() as s:
        found = s.get(Application, app_id)
        assert found is not None
        seen = gate.current_hash(s, found)

    def one() -> str:
        with factory() as s:
            try:
                auth = gate.authorise_approval(s, person_a.user_id, app_id, seen, AT, TTL)
                decision = gate.decide(s, person_a.user_id, auth.id, AT, switches=ON)
                s.commit()
                return "approved" if decision.passed else str(decision.code)
            except gate.RefusedError as exc:
                s.rollback()
                return exc.code

    with ThreadPoolExecutor(2) as pool:
        results = sorted(pool.map(lambda _: one(), range(2)))
    assert results == ["approved", "not_ready"]
    used = db.execute(sa.select(Authorisation).where(Authorisation.used_at.is_not(None))).all()
    assert len(used) == 1


@pytest.mark.parametrize(
    ("switches", "code"),
    [
        (gate.Switches(enabled=False, paused=False), "submit_disabled"),
        (gate.Switches(enabled=True, paused=True), "paused"),
    ],
)
def test_a_kill_switch_refuses_the_decision_and_keeps_the_form(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    switches: gate.Switches,
    code: str,
) -> None:
    app_id = ready(db, factory, person_a, stub)
    decision = approve(factory, person_a, app_id, switches=switches)
    assert (decision.passed, decision.code, decision.state) == (False, code, State.ready)
    (event,) = [e for e in events(db, app_id) if e.kind == "decision"]
    assert event.data["outcome"] == "refused"
    assert event.data["code"] == code


def test_a_newer_document_after_the_fill_is_refused_and_nothing_frozen(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    document = store.get_or_create(db, person_a.user_id, person_a.posting_id, Kind.cv, at=AT)
    store.save_draft(
        db,
        person_a.user_id,
        document.id,
        {**load("written/cv_typical.json"), "headline": "A newer headline"},
        document.draft_revision,
        Author.user,
        at=AT,
    )
    made, _ = store.snapshot(db, person_a.user_id, document.id, Origin.edited, at=AT)
    store.attach_pdf(
        db,
        person_a.user_id,
        document.id,
        made.number,
        b"%PDF-1.4 newer",
        fit=FitResult.one_page,
        fit_step=0,
        pages=1,
    )
    db.commit()
    decision = approve(factory, person_a, app_id)
    assert (decision.passed, decision.code, decision.state) == (
        False,
        "documents_changed",
        State.ready,
    )
    assert frozen(db, person_a.cv_version_id, made.id) == [None, None]


def test_a_never_apply_rule_written_after_the_fill_blocks_it(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    db.execute(sa.update(Job).where(Job.id == person_a.posting_id).values(title="Cyber Engineer"))
    with factory() as s:
        from app.profile import service as profile_service

        profile = profile_service.get_profile(s, person_a.user_id, AT)
        search = {**full("search"), "never_apply": ["No cybersecurity roles: cyber, security"]}
        profile_service.save_section(s, person_a.user_id, "search", search, profile.revision, AT)
        s.commit()
    db.commit()
    decision = approve(factory, person_a, app_id)
    assert (decision.passed, decision.code, decision.state) == (
        False,
        "never_apply_role",
        State.blocked,
    )


def test_a_posting_applied_by_a_mail_during_the_fill_is_a_duplicate(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    posting = jobs_service.lock_job(db, person_a.user_id, person_a.posting_id)
    assert posting is not None
    jobs_service.set_status(db, posting, JobStatus.applied, AT, via="mail")
    db.commit()
    decision = approve(factory, person_a, app_id)
    assert (decision.passed, decision.code, decision.state) == (
        False,
        "already_applied",
        State.blocked,
    )


def test_one_person_never_approves_or_decides_for_another(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person, stub: str
) -> None:
    app_a = ready(db, factory, person_a, stub)
    app_b = ready(db, factory, person_b, stub)
    with factory() as s:
        found = s.get(Application, app_b)
        assert found is not None
        seen = gate.current_hash(s, found)
        with pytest.raises(gate.NotFoundError):
            gate.authorise_approval(s, person_a.user_id, app_b, seen, AT, TTL)
        s.rollback()
        auth = gate.authorise_approval(s, person_b.user_id, app_b, seen, AT, TTL)
        s.commit()
        with pytest.raises(gate.NotFoundError):
            gate.decide(s, person_a.user_id, auth.id, AT, switches=ON)
        s.rollback()
        # A's own authorisation can never name B's application: the schema refuses it
        found_a = s.get(Application, app_a)
        assert found_a is not None
        auth_a = gate.authorise_approval(
            s, person_a.user_id, app_a, gate.current_hash(s, found_a), AT, TTL
        )
        auth_a.application_id = app_b
        with pytest.raises(IntegrityError, match="fk_apply_authorisations_application_id"):
            s.flush()
        s.rollback()
    assert application(db, app_b).state is State.ready
    assert application(db, app_a).state is State.ready


# --- the submit lease ----------------------------------------------------------------------------


def test_the_pause_set_between_the_decision_and_the_click_cancels_it(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    form = submit_ashby()
    assert run_submit(submit_services(factory, form, stub, paused=True), app_id) == "cancelled"
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.cancelled, "paused")
    assert form.gate_clicks == []
    assert frozen(db, person_a.cv_version_id) == [None]


def test_the_setting_off_between_the_decision_and_the_click_cancels_it(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    form = submit_ashby()
    assert run_submit(submit_services(factory, form, stub, enabled=False), app_id) == "cancelled"
    assert application(db, app_id).error_code == "submit_disabled"
    assert form.gate_clicks == []


def test_no_confirmation_is_submit_unknown_frozen_and_never_clicked_again(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    form = submit_ashby(answer="silent")
    assert run_submit(submit_services(factory, form, stub), app_id) == "submit_unknown"
    found = application(db, app_id)
    assert found.state is State.submit_unknown
    assert found.submitted_at is not None
    assert len(form.gate_clicks) == 1
    assert frozen(db, person_a.cv_version_id, person_a.letter_version_id) == ["apply", "apply"]
    assert status(db, person_a) is JobStatus.staged
    # the job asked again does nothing
    assert run_submit(submit_services(factory, form, stub), app_id) == "not_approved"
    assert len(form.gate_clicks) == 1


def test_a_slow_confirmation_is_submitted(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    form = submit_ashby(answer="slow", slow_reads=6)
    assert run_submit(submit_services(factory, form, stub), app_id) == "submitted"
    assert len(form.gate_clicks) == 1
    waits = [c for c in form.calls if c[0] == "wait_for"]
    assert len(waits) >= 2  # the dwell, then the polls


def test_errors_after_the_click_are_refused_by_site_and_nothing_frozen(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    form = submit_ashby(answer="errors")
    assert run_submit(submit_services(factory, form, stub), app_id) == "refused_by_site"
    assert application(db, app_id).state is State.refused_by_site
    assert frozen(db, person_a.cv_version_id) == [None]
    assert status(db, person_a) is JobStatus.staged
    (event,) = [e for e in events(db, app_id) if e.kind == "refused_by_site"]
    assert event.data["errors"] == 1


def test_a_captcha_on_the_refilled_form_needs_the_person(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    form = submit_ashby()
    form.captcha = "beside"
    assert run_submit(submit_services(factory, form, stub), app_id) == "needs_person"
    found = application(db, app_id)
    assert (found.state, found.needs) == (State.needs_person, "captcha")
    assert form.gate_clicks == []


@pytest.mark.parametrize(
    "change",
    ["added_field", "unverified_yes_no", "page_prefilled_a_left_field", "rewritten_after_dwell"],
)
def test_a_difference_on_the_read_back_is_form_changed(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str, change: str
) -> None:
    from tests.apply.a4_support import Spec

    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    form = submit_ashby()
    if change == "added_field":
        form.fields.append(Spec("Favourite colour", "text", required=False))
    elif change == "unverified_yes_no":
        next(s for s in form.fields if s.kind == "yesno").pressed_state = False
    elif change == "page_prefilled_a_left_field":
        form.spec("Current location").value = "Berlin, Germany"
    else:

        def rewrite(f: Any, n: int) -> None:
            if f.calls[-1][0] == "wait_for":
                f.spec("Full name").value = "Someone Else"

        form.on_call = rewrite
    assert run_submit(submit_services(factory, form, stub), app_id) == "ready"
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.ready, "form_changed")
    assert form.gate_clicks == []
    # the approval is spent: a new one is needed
    used = db.execute(sa.select(Authorisation.used_at)).scalars().all()
    assert all(u is not None for u in used)


def test_a_lease_gone_after_the_click_is_submit_unknown(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    form = submit_ashby()
    form.crash_on_click = True
    assert run_submit(submit_services(factory, form, stub), app_id) == "submit_unknown"
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.submit_unknown, "ended")
    assert frozen(db, person_a.cv_version_id) == ["apply"]


def test_a_cancelled_job_after_the_click_ends_submit_unknown(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    form = submit_ashby(answer="silent")

    def cancel_after_click(f: Any, n: int) -> None:
        if f.gate_clicks and f.calls[-1][0] == "snapshot":
            raise asyncio.CancelledError

    form.on_call = cancel_after_click
    with pytest.raises(asyncio.CancelledError):
        run_submit(submit_services(factory, form, stub), app_id)
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.submit_unknown, "interrupted")
    assert len(form.gate_clicks) == 1


def test_a_cancelled_job_before_the_click_goes_back_to_ready(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    form = submit_ashby()

    def cancel_on_type(f: Any, n: int) -> None:
        if f.calls[-1][0] == "type":
            raise asyncio.CancelledError

    form.on_call = cancel_on_type
    with pytest.raises(asyncio.CancelledError):
        run_submit(submit_services(factory, form, stub), app_id)
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.ready, "interrupted")
    assert form.gate_clicks == []


def test_a_failed_record_rolls_the_freeze_back_with_it(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    with factory() as s:
        found = s.get(Application, app_id)
        assert found is not None
        found.state = State.submitting  # as `arm` commits it
        s.commit()

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("move failed")

    monkeypatch.setattr(states, "move", broken)  # after the freeze, in the same transaction
    with factory() as s:
        with pytest.raises(RuntimeError):
            submit.record(s, app_id, State.submitted, AT)
        s.rollback()
    assert frozen(db, person_a.cv_version_id) == [None]
    assert application(db, app_id).state is State.submitting


def test_a_failed_record_ends_submit_unknown_with_the_versions_frozen(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    keep = shots.store

    def broken(s: Session, app: Any, kind: ShotKind, *rest: Any) -> Any:
        if kind is ShotKind.after:  # the record's own screenshot, after the freeze
            raise RuntimeError("store failed")
        return keep(s, app, kind, *rest)

    monkeypatch.setattr(shots, "store", broken)
    form = submit_ashby()
    assert run_submit(submit_services(factory, form, stub), app_id) == "submit_unknown"
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.submit_unknown, "record_failed")
    assert frozen(db, person_a.cv_version_id) == ["apply"]
    assert status(db, person_a) is JobStatus.staged


def test_an_overflowing_version_is_sent_and_frozen(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    db.execute(
        sa.update(DocumentVersion)
        .where(DocumentVersion.id == person_a.cv_version_id)
        .values(fit=FitResult.overflow)
    )
    db.commit()
    app_id = ready(db, factory, person_a, stub)
    assert application(db, app_id).document_versions["cv"]["fit"] == "overflow"
    assert approve(factory, person_a, app_id).passed
    assert run_submit(submit_services(factory, submit_ashby(), stub), app_id) == "submitted"
    assert frozen(db, person_a.cv_version_id) == ["apply"]


def test_behind_another_lease_it_stays_approved(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    leases = GateLeases(waiting=2)
    form = submit_ashby()
    assert run_submit(submit_services(factory, form, stub, leases=leases), app_id) == (
        "waiting_lease"
    )
    assert application(db, app_id).state is State.approved
    with factory() as s:
        assert app_id in submit.due(s)
    assert form.gate_clicks == []


def test_a_lease_without_the_capability_never_clicks(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    form = submit_ashby()
    svc = submit_services(factory, form, stub, leases=GateLeases(capability=None))
    assert run_submit(svc, app_id) == "ready"
    assert application(db, app_id).error_code == "no_capability"
    assert form.gate_clicks == []


# --- the person's decisions after the click ------------------------------------------------------


def test_try_again_needs_a_new_approval(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    form = submit_ashby(answer="silent")
    assert run_submit(submit_services(factory, form, stub), app_id) == "submit_unknown"
    old = db.execute(sa.select(Authorisation.id)).scalar_one()
    with factory() as s:
        assert gate.retry(s, person_a.user_id, app_id, AT).state is State.ready
        s.commit()
    # the job asked for it does nothing, the old approval is spent, the table needs the gate
    assert run_submit(submit_services(factory, form, stub), app_id) == "not_approved"
    with factory() as s:
        again = gate.decide(s, person_a.user_id, old, AT, switches=ON)
        s.commit()
    assert again.code == "authorisation_used"
    for to in (State.approved, State.held):
        with pytest.raises(states.TransitionError, match="without a decision"):
            states.move(Application(state=State.ready), to, AT)
    assert len(form.gate_clicks) == 1
    # a new approval clicks again, once, past the pace of the host (apply A11)
    assert approve(factory, person_a, app_id).passed
    later = submit_services(factory, submit_ashby(), stub)
    later.clock = lambda: AT + timedelta(minutes=10)
    assert run_submit(later, app_id) == "submitted"


def test_it_was_submitted_records_the_application(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    run_submit(submit_services(factory, submit_ashby(answer="silent"), stub), app_id)
    with factory() as s:
        gate.confirm_submitted(s, person_a.user_id, app_id, AT)
        s.commit()
    assert application(db, app_id).state is State.submitted
    assert status(db, person_a) is JobStatus.applied
    with factory() as s, pytest.raises(gate.RefusedError, match="not_unknown"):
        gate.confirm_submitted(s, person_a.user_id, app_id, AT)


@pytest.mark.parametrize(
    ("state", "reject", "allowed"),
    [
        (State.ready, True, True),
        (State.needs_person, True, True),
        (State.approved, True, False),
        (State.approved, False, True),
        (State.queued, False, True),
        (State.refused_by_site, False, True),
        (State.submitting, False, False),
        (State.submit_unknown, False, False),
        (State.submitted, False, False),
        (State.rehearsed, False, False),
    ],
)
def test_reject_and_cancel_only_before_the_click(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    state: State,
    reject: bool,
    allowed: bool,
) -> None:
    app_id = started(db, person_a)
    db.execute(
        sa.update(Application)
        .where(Application.id == app_id)
        .values(state=state, submitted_at=AT if state in states.CLICKED else None)
    )
    db.commit()
    with factory() as s:
        if allowed:
            assert gate.cancel(s, person_a.user_id, app_id, AT, reject=reject).state is (
                State.cancelled
            )
        else:
            with pytest.raises(gate.RefusedError, match="not_cancellable"):
                gate.cancel(s, person_a.user_id, app_id, AT, reject=reject)
        s.commit()


def test_a_cancel_during_the_refill_stops_the_click(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    form = submit_ashby()

    def cancel_on_wait(f: Any, n: int) -> None:
        if f.calls[-1][0] == "wait_for" and not f.gate_clicks:
            with factory() as s:
                gate.cancel(s, person_a.user_id, app_id, AT, reject=False)
                s.commit()
            f.on_call = None

    form.on_call = cancel_on_wait
    assert run_submit(submit_services(factory, form, stub), app_id) == "superseded"
    assert application(db, app_id).state is State.cancelled
    assert form.gate_clicks == []


# --- the worker ----------------------------------------------------------------------------------


def test_a_submitting_left_by_a_dead_worker_is_submit_unknown_at_start(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person, stub: str
) -> None:
    app_a = ready(db, factory, person_a, stub)
    app_b = ready(db, factory, person_b, stub)
    assert approve(factory, person_a, app_a).passed
    db.execute(sa.update(Application).where(Application.id == app_a).values(state="submitting"))
    db.commit()
    ctx = {"sessions": factory, "clock": lambda: AT}
    assert asyncio.run(worker.reset_fills_at_start(ctx)) == 1
    found = application(db, app_a)
    assert (found.state, found.error_code) == (State.submit_unknown, "interrupted")
    assert frozen(db, person_a.cv_version_id) == ["apply"]
    assert application(db, app_b).state is State.ready


def test_the_queue_serves_the_submit_and_the_tick_asks_for_approved_ones() -> None:
    spec = worker.apply_queue()
    names = {f.name for f in spec.functions}
    assert {"apply_fill", "apply_submit", "apply_tick"} <= names
    assert spec.on_start is worker.reset_fills_at_start
    assert queues.apply_submit_job_id("x") == "apply-submit:x"


# --- the kill switch ------------------------------------------------------------------------------


def test_the_pause_flag_round_trips_and_fails_closed() -> None:
    redis = FakeRedis()
    assert asyncio.run(admin.paused(redis)) is False
    asyncio.run(admin.pause(redis))
    assert asyncio.run(admin.paused(redis)) is True
    asyncio.run(admin.resume(redis))
    assert asyncio.run(admin.paused(redis)) is False
    assert asyncio.run(admin.paused(FakeRedis(broken=True))) is True
    assert asyncio.run(admin.paused(None)) is True


# --- privacy -------------------------------------------------------------------------------------


def test_no_answer_or_image_reaches_an_event_or_a_log(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = "canary" + secrets.token_hex(8)
    caplog.set_level(logging.DEBUG)
    app_id = ready(db, factory, person_a, stub)
    # the person's own edit of an answer, planted as the canary
    db.execute(
        sa.update(ApplicationAnswer)
        .where(ApplicationAnswer.application_id == app_id, ApplicationAnswer.label == WHY)
        .values(value=f"I build agents {canary}")
    )
    db.commit()
    assert approve(factory, person_a, app_id).passed
    assert run_submit(submit_services(factory, submit_ashby(), stub), app_id) == "submitted"
    stored = db.execute(
        sa.select(ApplicationEvent.data).where(ApplicationEvent.application_id == app_id)
    ).scalars()
    assert canary not in json.dumps(list(stored))
    assert canary not in caplog.text
    assert CAPABILITY not in caplog.text
    assert canary in json.dumps(
        [r.value for r in db.execute(sa.select(ApplicationAnswer)).scalars()]
    )


# --- the routes ----------------------------------------------------------------------------------


SECRET_KEY = "e2e-only-not-a-secret-" + "0" * 24


@pytest.fixture
def api_app(migrated_url: str, stub: str) -> FastAPI:
    settings = make_settings(migrated_url, secret_key=SECRET_KEY, apply_stub_base_url=stub)
    app = create_app(settings, clock=lambda: AT)
    app.state.queue = FakeQueue()
    return app


def _password(factory: sessionmaker[Session], *people: Person) -> None:
    with factory() as s:
        for person in people:
            s.execute(
                sa.text("UPDATE users SET password_hash = :h WHERE id = :u"),
                {"h": security.hash_password(PASSWORD), "u": person.user_id},
            )
        s.commit()


def test_the_routes(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    person_b: Person,
    stub: str,
    api_app: FastAPI,
) -> None:
    app_id = ready(db, factory, person_a, stub)
    _password(factory, person_a, person_b)
    with TestClient(api_app, follow_redirects=False) as client:
        api_app.state.queue = queue = FakeQueue()
        api = Api(client)
        token_a, token_b = api.login_token(person_a.email), api.login_token(person_b.email)
        base = f"/api/applications/{app_id}"

        assert api.get(f"{base}/approval", token=token_b).status_code == 404
        view = api.get(f"{base}/approval", token=token_a)
        assert view.status_code == 200
        assert view.headers["cache-control"] == "no-store"
        body = view.json()
        assert body["state"] == "ready"
        assert len(body["answers_hash"]) == 64
        left = [a for a in body["answers"] if a["you_answer"]]
        assert all(a["source"] == "left" for a in left)
        (shot,) = body["screenshots"]
        image = api.get(f"{base}/screenshots/{shot['id']}", token=token_a)
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/jpeg"
        assert api.get(f"{base}/screenshots/{shot['id']}", token=token_b).status_code == 404

        approve_path = f"{base}/approve"
        good = {"answers_hash": body["answers_hash"]}
        assert api.post(approve_path, token=token_b, json=good).status_code == 404
        assert api.post(approve_path, token=token_a, json=good, csrf=False).status_code == 403
        assert api.post(approve_path, token=token_a, json={"answers_hash": "x"}).status_code == 422
        stale = api.post(approve_path, token=token_a, json={"answers_hash": "0" * 64})
        assert stale.status_code == 409
        assert stale.json()["code"] == "stale"
        assert stale.json()["answers_hash"] == body["answers_hash"]
        assert stale.json()["answers"]

        queue.redis.flags[admin.PAUSE_KEY] = "1"
        paused = api.post(approve_path, token=token_a, json=good)
        assert (paused.status_code, paused.json()["code"]) == (409, "paused")
        queue.redis.flags.clear()

        done = api.post(approve_path, token=token_a, json=good)
        assert done.status_code == 200, done.text
        assert done.json()["state"] == "approved"
        assert queue.enqueued == [
            ("apply_submit", (str(app_id),), queues.APPLY, queues.apply_submit_job_id(app_id))
        ]
        again = api.post(approve_path, token=token_a, json=good)
        assert (again.status_code, again.json()["code"]) == (409, "not_ready")
        assert len(queue.enqueued) == 1

        assert api.post(f"{base}/retry", token=token_a).json()["code"] == "not_retryable"
        assert api.post(f"{base}/submitted", token=token_a).json()["code"] == "not_unknown"
        assert api.post(f"{base}/cancel", token=token_b).status_code == 404
        for action in ("reject", "retry", "submitted"):
            assert api.post(f"{base}/{action}", token=token_b).status_code == 404, action
        cancelled = api.post(f"{base}/cancel", token=token_a)
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "cancelled"


def test_a_lock_held_past_its_timeout_is_busy_with_a_code(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    api_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = ready(db, factory, person_a, stub)
    _password(factory, person_a)

    def busy(*args: Any, **kwargs: Any) -> Any:
        raise jobs_service.JobBusyError(jobs_service.BUSY)

    monkeypatch.setattr(gate, "lock", busy)
    monkeypatch.setattr(jobs_service, "lock_job", busy)  # the start route (issue #296, item 2)
    other = another_posting(db, person_a)
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        token = api.login_token(person_a.email)
        stale = {"answers_hash": "0" * 64}
        answers = [
            api.post(f"/api/applications/{app_id}/cancel", token=token),
            api.post(f"/api/applications/{app_id}/approve", token=token, json=stale),
            api.post(f"/api/postings/{other}/applications", token=token, json={}),
        ]
    for answer in answers:
        assert answer.status_code == 409, answer.text
        assert answer.json()["code"] == "busy"
        assert answer.headers["cache-control"] == "no-store"


def another_posting(db: Session, person: Person) -> uuid.UUID:
    """Another posting of the same person, to start an application on."""
    found: uuid.UUID = db.execute(
        sa.text(
            "INSERT INTO jobs (user_id, slug, company, title, url) VALUES (:u, :s, 'Northwind "
            "Labs', 'Agent Engineer', 'https://jobs.ashbyhq.com/northwind/1/application') "
            "RETURNING id"
        ),
        {"u": person.user_id, "s": f"northwind-{uuid.uuid4().hex[:8]}"},
    ).scalar_one()
    db.commit()
    return found


def test_only_the_gate_passes_by_gate() -> None:
    from pathlib import Path

    root = Path(run.__file__).parent
    users = sorted(
        p.name
        for p in root.rglob("*.py")
        if p.name != "states.py" and "by_gate=True" in p.read_text(encoding="utf-8")
    )
    assert users == ["gate.py"]
