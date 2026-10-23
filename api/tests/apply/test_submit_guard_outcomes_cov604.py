"""A lost lease or unavailable approved document cannot turn into a second submit."""

import uuid

import pytest
import sqlalchemy as sa

from app.apply import submit
from app.apply.models import Application, ApplicationAnswer, State
from app.browser.client import BrowserError
from app.browser.leases import StoreUnavailableError
from app.documents import freeze as documents
from app.jobs.models import JobStatus
from tests.apply.a6_support import GateLeases, submit_ashby
from tests.apply.conftest import AT
from tests.apply.test_a5_run import application, events, status
from tests.apply.test_a6_gate import approve, frozen, ready, run_submit, submit_services


@pytest.mark.parametrize("error", [BrowserError("unavailable"), StoreUnavailableError()])
def test_submit_lease_outage_keeps_approval_without_opening_or_freezing(
    db, factory, person_a, forms_base_url, error
):
    app_id = ready(db, factory, person_a, forms_base_url)
    assert approve(factory, person_a, app_id).passed
    form, leases = submit_ashby(), GateLeases(error=error)
    result = run_submit(submit_services(factory, form, forms_base_url, leases=leases), app_id)
    assert result == "lease_unavailable"
    found = application(db, app_id)
    assert (found.state, found.needs) == (State.approved, "browser_unavailable")
    assert form.calls == []
    assert leases.released == []
    assert frozen(db, person_a.cv_version_id, person_a.letter_version_id) == [None, None]
    assert status(db, person_a) is JobStatus.staged


def test_restarted_submit_lease_is_released_without_replaying_approved_values(
    db, factory, person_a, forms_base_url
):
    app_id = ready(db, factory, person_a, forms_base_url)
    assert approve(factory, person_a, app_id).passed
    form, leases = submit_ashby(), GateLeases(token=None)
    result = run_submit(submit_services(factory, form, forms_base_url, leases=leases), app_id)
    assert result == "lease_restarted"
    assert application(db, app_id).state is State.approved
    assert leases.released == [(str(person_a.user_id), "lease-1", "apply_restart")]
    assert form.calls == []
    assert frozen(db, person_a.cv_version_id) == [None]


@pytest.mark.parametrize("error", [BrowserError("ended"), RuntimeError("synthetic outage")])
def test_submit_crash_before_page_opens_returns_to_fresh_approval(
    db, factory, person_a, forms_base_url, monkeypatch, error
):
    app_id = ready(db, factory, person_a, forms_base_url)
    assert approve(factory, person_a, app_id).passed
    form, leases = submit_ashby(), GateLeases()

    async def unavailable(url):
        raise error

    monkeypatch.setattr(form, "navigate", unavailable)
    result = run_submit(submit_services(factory, form, forms_base_url, leases=leases), app_id)
    assert result == "ready"
    found = application(db, app_id)
    expected = "ended" if isinstance(error, BrowserError) else "internal_error"
    assert (found.state, found.error_code) == (State.ready, expected)
    assert form.gate_clicks == []
    assert "click" not in [event.kind for event in events(db, app_id)]
    assert len(leases.released) == 1
    assert frozen(db, person_a.cv_version_id, person_a.letter_version_id) == [None, None]


@pytest.mark.parametrize("error", [BrowserError("unavailable"), StoreUnavailableError()])
def test_release_failure_preserves_a_confirmed_submission_and_cannot_replay(
    db, factory, person_a, forms_base_url, monkeypatch, error
):
    app_id = ready(db, factory, person_a, forms_base_url)
    assert approve(factory, person_a, app_id).passed
    form, leases = submit_ashby(), GateLeases()

    async def unavailable(*args):
        raise error

    monkeypatch.setattr(leases, "release", unavailable)
    env = submit_services(factory, form, forms_base_url, leases=leases)
    assert run_submit(env, app_id) == "submitted"
    assert run_submit(env, app_id) == "not_approved"
    assert application(db, app_id).state is State.submitted
    assert len(form.gate_clicks) == 1
    assert len(leases.acquired) == 1
    assert frozen(db, person_a.cv_version_id, person_a.letter_version_id) == ["apply", "apply"]
    assert status(db, person_a) is JobStatus.applied


def test_approved_pdf_unavailable_at_replay_requires_new_approval_before_any_lease(
    db, factory, person_a, forms_base_url, monkeypatch
):
    app_id = ready(db, factory, person_a, forms_base_url)
    assert approve(factory, person_a, app_id).passed
    monkeypatch.setattr(documents, "pdf", lambda *args: None)
    form, leases = submit_ashby(), GateLeases()
    assert run_submit(submit_services(factory, form, forms_base_url, leases=leases), app_id) == (
        "no_documents"
    )
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.ready, "no_documents")
    assert leases.acquired == []
    assert form.calls == []
    assert frozen(db, person_a.cv_version_id) == [None]


def test_changed_persisted_answer_is_refused_before_browser_replay(
    db, factory, person_a, forms_base_url
):
    app_id = ready(db, factory, person_a, forms_base_url)
    assert approve(factory, person_a, app_id).passed
    db.execute(
        sa.update(ApplicationAnswer)
        .where(ApplicationAnswer.application_id == app_id, ApplicationAnswer.kind == "text")
        .values(value="Changed after approval")
    )
    db.commit()
    form, leases = submit_ashby(), GateLeases()
    assert run_submit(submit_services(factory, form, forms_base_url, leases=leases), app_id) == (
        "stale"
    )
    assert (application(db, app_id).state, application(db, app_id).error_code) == (
        State.ready,
        "stale",
    )
    assert leases.acquired == []
    assert form.calls == []
    assert frozen(db, person_a.cv_version_id) == [None]


def test_deleted_application_submit_and_interruption_return_gone_without_browser(factory):
    missing = uuid.uuid4()
    form, leases = submit_ashby(), GateLeases()
    env = submit_services(factory, form, "http://ats-forms.test", leases=leases)
    assert run_submit(env, missing) == "gone"
    with factory() as db:
        assert submit.interrupted(db, missing, AT, "ended") == "gone"
        assert db.get(Application, missing) is None
    assert form.calls == []
    assert leases.acquired == []
