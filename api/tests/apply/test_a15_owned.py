"""Owned runs, real persistence, partial wizard continuation and the existing submit gate."""

from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa

from app.apply import caps, gate, guardrails, run, service
from app.apply.models import AnswerSource, ApplicationAnswer, Mode, State
from app.jobs.models import Job
from tests.apply.a4_support import Spec
from tests.apply.a5_support import FakeAgent
from tests.apply.a15_support import WizardForm, url
from tests.apply.conftest import AT
from tests.apply.test_a5_run import application, fill, services
from tests.apply.test_a6_gate import approve, run_submit, submit_services


@pytest.fixture(autouse=True)
def configured_fixture_origin(monkeypatch, forms_base_url):
    original = caps._setting
    monkeypatch.setattr(
        caps,
        "_setting",
        lambda key: forms_base_url if key == "apply_stub_base_url" else original(key),
    )


def start_wizard(db, person, base, kind="workday", mode=Mode.review):
    address = url(kind, base=base)
    posting = db.get(Job, person.posting_id)
    posting.url = address
    db.commit()
    ref = service.start(
        db, person.user_id, person.posting_id, mode, False, now=AT, stub_base=base
    ).application.id
    return ref


def unknown_form():
    form = WizardForm()
    form.fields.insert(3, Spec("Your project codename", "text", True))
    form.groups[0] += 1
    return form


def answers_of(db, ref):
    db.expire_all()
    return list(
        db.scalars(sa.select(ApplicationAnswer).where(ApplicationAnswer.application_id == ref))
    )


@pytest.mark.parametrize("kind", ["workday", "google_careers", "vie"])
def test_owned_wizard_fills_approves_and_submits_exactly_once(
    db, factory, person_a, person_b, forms_base_url, kind
):
    ref = start_wizard(db, person_a, forms_base_url, kind)
    form, model = WizardForm(kind=kind), FakeAgent()
    assert (
        fill(services(factory, form, answer_agent=model, stub_base=forms_base_url), ref) == "ready"
    )
    assert model.requests == []
    app = application(db, ref)
    assert app.ats == kind
    assert not form.gate_clicks
    assert form.submit_clicks == 0
    rows = answers_of(db, ref)
    assert any(
        r.field_key == "designated:submit" and r.source is AnswerSource.identity for r in rows
    )
    with factory() as other, pytest.raises(gate.NotFoundError):
        gate.set_answer(other, person_b.user_id, ref, rows[0].field_key, "Other", AT)
    assert approve(factory, person_a, ref).passed
    fresh = WizardForm(kind=kind)
    assert run_submit(submit_services(factory, fresh, forms_base_url), ref) == "submitted"
    assert len(fresh.gate_clicks) == 1
    assert fresh.submit_clicks == 0
    assert run_submit(submit_services(factory, fresh, forms_base_url), ref) == "not_approved"
    assert len(fresh.gate_clicks) == 1


def test_partial_wizard_last_person_answer_queues_a_new_fill_before_final_approval(
    db, factory, person_a, person_b, forms_base_url
):
    ref = start_wizard(db, person_a, forms_base_url)
    first = unknown_form()
    assert fill(services(factory, first, stub_base=forms_base_url), ref) == "needs_person"
    app = application(db, ref)
    assert app.needs == "required_left"
    assert first.step == 0
    rows = answers_of(db, ref)
    left = next(r for r in rows if r.label == "Your project codename")
    marker = next(r for r in rows if r.field_key == "designated:incomplete")
    assert not any(r.field_key == "designated:submit" for r in rows)
    with factory() as owned:
        for key in (marker.field_key, "designated:shape:0", "designated:submit"):
            with pytest.raises(gate.InvalidAnswerError):
                gate.set_answer(owned, person_a.user_id, ref, key, "forged", AT)
            owned.rollback()
    with pytest.raises(gate.RefusedError, match="not_ready"):
        approve(factory, person_a, ref)
    with factory() as owned:
        updated = gate.set_answer(owned, person_a.user_id, ref, left.field_key, "Example Cedar", AT)
        assert updated.state is State.queued
        owned.commit()
    assert ref in run.due(db, AT)
    resumed = unknown_form()
    assert fill(services(factory, resumed, stub_base=forms_base_url), ref) == "ready"
    rows = answers_of(db, ref)
    assert not any(r.field_key == "designated:incomplete" for r in rows)
    assert next(r for r in rows if r.label == left.label).source is AnswerSource.person
    assert resumed.spec(left.label).value == "Example Cedar"
    assert approve(factory, person_a, ref).passed
    final = unknown_form()
    assert run_submit(submit_services(factory, final, forms_base_url), ref) == "submitted"
    assert len(final.gate_clicks) == 1


def test_two_last_answer_writes_queue_only_one_continuation(db, factory, person_a, forms_base_url):
    ref = start_wizard(db, person_a, forms_base_url)
    assert fill(services(factory, unknown_form(), stub_base=forms_base_url), ref) == "needs_person"
    left = next(r for r in answers_of(db, ref) if r.label == "Your project codename")

    def write():
        with factory() as owned:
            try:
                app = gate.set_answer(
                    owned, person_a.user_id, ref, left.field_key, "Example Cedar", AT
                )
                owned.commit()
                return app.state.value
            except gate.RefusedError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: write(), range(2)))
    assert sorted(results) == ["not_editable", "queued"]
    assert run.due(db, AT).count(ref) == 1


@pytest.mark.parametrize("kind", ["workday", "google_careers", "vie"])
def test_full_auto_known_wizards_switch_to_review_before_a_partial_continuation(
    db, factory, person_a, forms_base_url, kind
):
    ref = start_wizard(db, person_a, forms_base_url, kind, Mode.full_auto)
    form = unknown_form() if kind == "workday" else WizardForm(kind=kind)
    fill(services(factory, form, stub_base=forms_base_url), ref)
    assert application(db, ref).mode is Mode.review
    assert not form.gate_clicks


def test_vie_external_application_uses_a_new_guarded_lease_and_duplicate_checks(
    db, factory, person_a, forms_base_url
):
    ref = start_wizard(db, person_a, forms_base_url, "vie")
    form = WizardForm(kind="vie")
    target = forms_base_url + "/greenhouse/northwind-labs/jobs/4012348"

    async def snapshot():
        return {
            "ok": True,
            "url": form.url,
            "snapshot": f'- link "Apply on company site" [ref=e1]:\n  - /url: {target}',
        }

    form.snapshot = snapshot
    svc = services(factory, form, stub_base=forms_base_url)
    assert fill(svc, ref) == "queued"
    app = application(db, ref)
    assert (app.form_url, app.ats) == (target, "greenhouse")
    assert app.canonical_key == guardrails.canonical_key(guardrails.unstub(target, forms_base_url))
    assert len(svc.leases.acquired) == 1
    assert "127.0.0.1" in svc.leases.acquired[0]["hosts"]
    assert svc.leases.released[0][2] == "company_site"
    assert all(call == "put_file" for call, _ in form.acted())
