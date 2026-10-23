"""A posting skipped through another route must stop before any automated submission (#528)."""

from __future__ import annotations

from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import proposals
from app.apply import service
from app.apply.models import Application, Mode, State
from app.jobs import service as jobs
from app.jobs.models import Job, JobStatus
from tests.apply.a6_support import submit_ashby
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import application, events
from tests.apply.test_a6_gate import approve, frozen, ready, run_submit, submit_services
from tests.apply.test_a6_recheck import at, at_the_dwell
from tests.apply.test_a13_full_auto import HOLD, filled, job, turn_on


@pytest.fixture
def stub(forms_base_url: str) -> str:
    return forms_base_url


ROUTES = ("edit", "bulk", "proposal", "undo")


def skip(factory: sessionmaker[Session], person: Person, route: str) -> None:
    with factory() as db:
        if route == "bulk":
            jobs.bulk_update(db, person.user_id, [person.posting_id], AT, status=JobStatus.skipped)
        elif route == "proposal":
            proposal = proposals.create(
                db,
                person.user_id,
                target="job",
                target_id=person.posting_id,
                kind="job_status",
                payload={"status": "skipped"},
                run_id=None,
                now=AT,
            )
            assert proposals.accept(db, person.user_id, proposal.id, AT) is not None
        else:
            posting = jobs.lock_job(db, person.user_id, person.posting_id)
            assert posting is not None
            jobs.edit_job(db, posting, jobs.JobChanges(status=JobStatus.skipped), AT)
            if route == "undo":
                changed = jobs.bulk_update(
                    db, person.user_id, [person.posting_id], AT, status=JobStatus.found
                )
                assert jobs.undo_bulk(db, person.user_id, changed.undo, AT).restored == [
                    person.posting_id
                ]
        db.commit()


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("mode", [Mode.review, Mode.full_auto, Mode.manual])
def test_start_refuses_a_passed_posting_but_apply_myself_only_warns(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    route: str,
    mode: Mode,
) -> None:
    skip(factory, person_a, route)
    if mode is Mode.manual:
        made = service.start(
            db, person_a.user_id, person_a.posting_id, mode, False, now=AT, stub_base=stub
        )
        assert made.warnings == ("passed",)
        assert made.application.state is State.queued
        return
    with pytest.raises(service.RefusedError) as caught:
        service.start(
            db, person_a.user_id, person_a.posting_id, mode, False, now=AT, stub_base=stub
        )
    assert caught.value.refusal.code == "passed"
    assert "Undo the Pass first" in caught.value.refusal.sentence
    db.rollback()
    assert db.scalar(sa.select(sa.func.count()).select_from(Application)) == 0


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("mode", [Mode.review, Mode.full_auto])
@pytest.mark.parametrize("phase", ["decision", "prepare", "click"])
def test_a_late_skip_stops_the_decision_and_both_submission_rechecks(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    route: str,
    mode: Mode,
    phase: str,
) -> None:
    if mode is Mode.full_auto:
        turn_on(db, person_a)
        app_id = filled(db, factory, person_a, stub)
    else:
        app_id = ready(db, factory, person_a, stub)

    if phase == "decision":
        skip(factory, person_a, route)
        if mode is Mode.full_auto:
            result, form = job(factory, stub, app_id, AT)
            assert result == "passed"
            assert form.gate_clicks == []
        else:
            decision = approve(factory, person_a, app_id)
            assert (decision.passed, decision.code) == (False, "passed")
    else:
        if mode is Mode.full_auto:
            assert job(factory, stub, app_id, AT)[0] == "held"
        else:
            assert approve(factory, person_a, app_id).passed
        form = submit_ashby()
        if phase == "click":
            at_the_dwell(form, lambda: skip(factory, person_a, route))
        else:
            skip(factory, person_a, route)
        svc = submit_services(factory, form, stub)
        svc.clock = at(AT + HOLD)
        run_submit(svc, app_id)
        assert form.gate_clicks == []

    assert application(db, app_id).error_code == "passed"
    assert "click" not in [event.kind for event in events(db, app_id)]
    assert frozen(db, person_a.cv_version_id, person_a.letter_version_id) == [None, None]
    posting = db.get(Job, person_a.posting_id)
    assert posting is not None
    assert posting.status is JobStatus.skipped


def test_undoing_the_pass_allows_a_fresh_start(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    skip(factory, person_a, "bulk")
    posting = jobs.lock_job(db, person_a.user_id, person_a.posting_id)
    assert posting is not None
    jobs.set_status(db, posting, JobStatus.found, AT + timedelta(seconds=1), via="undo")
    db.commit()
    made = service.start(
        db, person_a.user_id, person_a.posting_id, Mode.review, False, now=AT, stub_base=stub
    )
    assert made.warnings == ()


def test_a_manual_warning_keeps_the_duplicate_application_refusal(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    made = service.start(
        db, person_a.user_id, person_a.posting_id, Mode.manual, False, now=AT, stub_base=stub
    )
    skip(factory, person_a, "bulk")
    with pytest.raises(service.RefusedError) as caught:
        service.start(
            db, person_a.user_id, person_a.posting_id, Mode.manual, False, now=AT, stub_base=stub
        )
    assert (caught.value.refusal.code, caught.value.refusal.application_id) == (
        "in_progress",
        made.application.id,
    )
