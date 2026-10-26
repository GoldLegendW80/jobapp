"""Failures at browser boundaries leave a durable, guarded application outcome."""

import asyncio
from datetime import timedelta

import pytest
import sqlalchemy as sa

from app.apply import failures, run
from app.apply.models import Application, State
from app.browser.client import BrowserError
from tests.apply.a5_support import FakeAgent, FakeLeases, run_ashby
from tests.apply.test_a5_run import application, fields, services, started, why_answer


@pytest.mark.parametrize("reported", ["file_rejected", ""])
def test_document_transfer_failure_never_starts_typing(
    db, factory, person_a, forms_base_url, reported
):
    form = run_ashby()

    async def rejected(name, data):
        return {"ok": False, "error": reported}

    form.put_file = rejected
    leases = FakeLeases()
    app_id = started(db, person_a)
    env = services(factory, form, leases, stub_base=forms_base_url)
    assert asyncio.run(run.fill(env, app_id)) == "failed"
    app = application(db, app_id)
    assert app.error_code == (reported or "put_file_failed")
    assert fields(db, app_id) == {}
    assert not any(call[0] == "type" for call in form.calls)
    assert len(leases.released) == 1


def test_cancel_while_acquiring_the_lease_prevents_the_first_browser_action(
    db, factory, person_a, forms_base_url
):
    app_id = started(db, person_a)

    class CancelledDuringAcquire(FakeLeases):
        async def acquire(self, *args, **kwargs):
            acquired = await super().acquire(*args, **kwargs)
            with factory() as other:
                other.execute(
                    sa.update(Application)
                    .where(Application.id == app_id)
                    .values(state=State.cancelled)
                )
                other.commit()
            return acquired

    leases, form = CancelledDuringAcquire(), run_ashby()
    assert (
        asyncio.run(run.fill(services(factory, form, leases, stub_base=forms_base_url), app_id))
        == "not_queued"
    )
    app = application(db, app_id)
    assert app.state is State.cancelled
    assert app.attempt == 0
    assert form.calls == []
    assert fields(db, app_id) == {}
    assert len(leases.released) == 1


def test_lost_release_does_not_erase_a_completed_fill(db, factory, person_a, forms_base_url):
    class LostRelease(FakeLeases):
        async def release(self, *args, **kwargs):
            raise BrowserError("ended")

    form = run_ashby()
    app_id = started(db, person_a)
    env = services(factory, form, LostRelease(), FakeAgent(why_answer), stub_base=forms_base_url)
    assert asyncio.run(run.fill(env, app_id)) == "ready"
    app = application(db, app_id)
    assert app.state is State.ready
    assert app.error_code is None
    assert fields(db, app_id)["Why Example Robotics?"].value


@pytest.mark.parametrize("opens", [False, True])
def test_unknown_form_opening_failure_or_missing_runtime_never_approves(
    db, factory, person_a, forms_base_url, opens
):
    app_id = started(db, person_a)
    db.execute(
        sa.update(Application)
        .where(Application.id == app_id)
        .values(form_url="https://portal.example/candidate/form")
    )
    db.commit()
    form = run_ashby()
    if not opens:

        async def unavailable(url):
            return {"ok": False, "error": "danger"}

        form.navigate = unavailable
    result = asyncio.run(run.fill(services(factory, form, stub_base=forms_base_url), app_id))
    app = application(db, app_id)
    if opens:
        assert result == "needs_person"
        assert (app.state, app.needs) == (State.needs_person, "no_adapter")
    else:
        assert result == "failed"
        assert app.error_code == "danger"
    assert not any(row.approved for row in fields(db, app_id).values())


@pytest.mark.parametrize("code", ["navigation_failed", "page_error"])
def test_navigation_result_uses_only_existing_delayed_retries(
    db, factory, person_a, forms_base_url, code
):
    app_id = started(db, person_a)
    leases = FakeLeases()
    for attempt, delay in ((1, 600), (2, 3600), (3, None)):
        form = run_ashby()

        async def unavailable(url):
            return {"ok": False, "error": code}

        form.navigate = unavailable
        env = services(factory, form, leases, stub_base=forms_base_url, retry_delays_s=(600, 3600))
        expected = "queued" if delay is not None and code == "navigation_failed" else "failed"
        assert asyncio.run(run.fill(env, app_id)) == expected
        app = application(db, app_id)
        assert (app.state.value, app.error_code, app.attempt) == (expected, code, attempt)
        assert len(leases.released) == attempt
        assert form.submit_clicks == 0
        assert fields(db, app_id) == {}
        if expected == "failed":
            assert app_id not in run.due(db, app.updated_at + timedelta(days=30), (600, 3600))
            break
        assert app.needs == failures.RETRY
        assert app_id not in run.due(db, app.updated_at + timedelta(seconds=delay - 1), (600, 3600))
        assert app_id in run.due(db, app.updated_at + timedelta(seconds=delay), (600, 3600))


def test_navigation_failure_cannot_requeue_a_cancelled_fill(db, factory, person_a, forms_base_url):
    app_id = started(db, person_a)
    form, leases = run_ashby(), FakeLeases()

    async def unavailable(url):
        with factory() as other:
            other.execute(
                sa.update(Application).where(Application.id == app_id).values(state=State.cancelled)
            )
            other.commit()
        return {"ok": False, "error": "navigation_failed"}

    form.navigate = unavailable
    env = services(factory, form, leases, stub_base=forms_base_url, retry_delays_s=(600, 3600))
    assert asyncio.run(run.fill(env, app_id)) == "superseded"
    assert application(db, app_id).state is State.cancelled
    assert len(leases.released) == 1
    assert form.submit_clicks == 0
