"""Easy Apply on invented forms only: guards, replay, counted final clicks and isolation."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.apply import caps, gate, guardrails, linkedin
from app.apply.ats import adapter_for, generic
from app.apply.ats.linkedin import LinkedIn
from app.apply.models import AnswerSource, Mode, State
from app.jobs.models import Job
from tests.apply.a5_support import FakeLeases
from tests.apply.a16_support import URL, LinkedInForm
from tests.apply.conftest import AT
from tests.apply.test_a4_adapters import context
from tests.apply.test_a5_run import application, fields, fill, services, started
from tests.apply.test_a6_gate import approve, run_submit, submit_services
from tests.apply.test_a6_recheck import at
from tests.apply.test_a13_full_auto import HOLD, job, turn_on
from tests.apply.test_b10_linkedin_guard import switch


@pytest.fixture
def stub(forms_base_url):
    return forms_base_url


def start(db, person, mode=Mode.review):
    db.execute(sa.update(Job).where(Job.id == person.posting_id).values(url=URL))
    db.commit()
    return started(db, person, mode)


def test_known_url_and_subclass_survive_approved_replay():
    assert isinstance(adapter_for(URL), LinkedIn)
    assert guardrails.ats_of(URL + "?gh_jid=1") == "linkedin"
    assert guardrails.canonical_key(URL + "?tracking=x") == "linkedin:4100000001"
    for url in ["https://evil.example/jobs/view/4100000001/", "https://www.linkedin.com/in/1/"]:
        assert LinkedIn().form_url(url) is None


def test_checkpoint_stop_mapping_survives_a_forbidden_page_status():
    from app.apply import run, submit

    assert run._stopped("linkedin_paused") == (
        State.needs_person,
        "linkedin_paused",
        "linkedin_paused",
    )
    assert run._stopped("needs_person", "linkedin_paused") == (
        State.needs_person,
        "linkedin_paused",
        "linkedin_paused",
    )
    stopped = submit._stop_of("needs_person", "linkedin_paused")
    assert (stopped.state, stopped.code, stopped.needs) == (
        State.needs_person,
        "linkedin_paused",
        "linkedin_paused",
    )
    result = run.Result(State.needs_person, needs="linkedin_paused", error="linkedin_paused")
    assert asyncio.run(run._checked(result, SimpleNamespace(status=403))) is result


def test_replay_keeps_job_context_and_has_independent_mutable_state():
    original = LinkedIn()
    original.job_id = "4100000001"
    rows = [
        SimpleNamespace(key=generic.SUBMIT_KEY, value="Submit application", label="Submit"),
        SimpleNamespace(key="first name", value="Synthetic", label="First name"),
    ]
    first = generic.configured(original, rows)
    second = generic.configured(original, rows)
    assert isinstance(first, LinkedIn)
    assert isinstance(second, LinkedIn)
    first.step = 2
    first.expected.append(("Email", "e2e-copy@example.org"))
    assert original.step == second.step == 0
    assert original.expected == []
    assert second.expected == [("First name", "Synthetic")]
    assert first.job_id == second.job_id == original.job_id == "4100000001"


@pytest.mark.parametrize("follow", [False, True])
def test_modal_fill_designates_without_click_and_declines_follow(follow):
    form, adapter = LinkedInForm(follow=follow), LinkedIn()

    async def go():
        await form.put_file("cv.pdf", b"%PDF-synthetic")
        assert await adapter.open(form, URL) == ""
        return await adapter.fill_form(form, context())

    report = asyncio.run(go())
    assert report.stopped == ""
    assert report.designated == "Submit application"
    assert report.steps == ["Next", "Review"]
    assert form.submit_clicks == 0
    assert form.gate_clicks == []
    if follow:
        assert form.fields[-1].checked is False


def test_review_approves_then_submits_exactly_once(db, factory, person_a, stub):
    switch(factory, person_a)
    app_id = start(db, person_a)
    form = LinkedInForm()
    assert fill(services(factory, form, stub_base=stub), app_id) == "ready"
    rows = fields(db, app_id)
    assert rows["Submit button, clicked after your approval"].source is AnswerSource.identity
    assert rows["Follow this company"].value is False
    assert form.gate_clicks == []
    with pytest.raises(gate.InvalidAnswerError):
        gate.set_answer(db, person_a.user_id, app_id, generic.SUBMIT_KEY, "Apply", AT)
    db.rollback()
    approve(factory, person_a, app_id)
    fresh = LinkedInForm()
    svc = submit_services(factory, fresh, stub)
    assert run_submit(svc, app_id) == "submitted"
    assert fresh.fields[-1].checked is False
    assert len(fresh.gate_clicks) == 1
    assert run_submit(svc, app_id) == "not_approved"
    assert len(fresh.gate_clicks) == 1


@pytest.mark.parametrize("changed", [False, True])
def test_final_review_without_fields_still_checks_structured_summary(
    db, factory, person_a, stub, changed
):
    switch(factory, person_a)
    app_id = start(db, person_a)
    assert fill(services(factory, LinkedInForm(follow=False), stub_base=stub), app_id) == "ready"
    approve(factory, person_a, app_id)
    form = LinkedInForm(follow=False, changed_summary=changed)
    written = run_submit(submit_services(factory, form, stub), app_id)
    assert written == ("ready" if changed else "submitted")
    if changed:
        assert application(db, app_id).error_code == "form_changed"
    assert len(form.gate_clicks) == (0 if changed else 1)


def test_unknown_required_question_stays_left_in_review(db, factory, person_a, stub):
    switch(factory, person_a)
    app_id = start(db, person_a, Mode.full_auto)
    form = LinkedInForm(unknown=True)
    assert fill(services(factory, form, stub_base=stub), app_id) == "needs_person"
    found = application(db, app_id)
    assert (found.mode, found.needs) == (Mode.review, "required_left")
    assert form.fields[-2].value == ""
    assert form.gate_clicks == []


@pytest.mark.parametrize("revocation", ["switch", "version"])
def test_full_auto_held_then_current_consent_checked_again(
    db, factory, person_a, stub, monkeypatch, revocation
):
    switch(factory, person_a)
    turn_on(db, person_a)
    app_id = start(db, person_a, Mode.full_auto)
    assert fill(services(factory, LinkedInForm(), stub_base=stub), app_id) == "ready"
    assert job(factory, stub, app_id, AT, form=LinkedInForm())[0] == "held"
    if revocation == "switch":
        switch(factory, person_a, enabled=False)
    else:
        monkeypatch.setattr(linkedin.linkedin_guard, "TEXT_VERSION", 2)
    form = LinkedInForm()
    assert job(factory, stub, app_id, AT + HOLD, form=form)[0] == "linkedin_off"
    assert application(db, app_id).mode is Mode.review
    assert form.gate_clicks == []


def test_full_auto_sends_once_after_hold(db, factory, person_a, stub):
    switch(factory, person_a)
    turn_on(db, person_a)
    app_id = start(db, person_a, Mode.full_auto)
    assert fill(services(factory, LinkedInForm(), stub_base=stub), app_id) == "ready"
    assert job(factory, stub, app_id, AT, form=LinkedInForm())[0] == "held"
    form = LinkedInForm()
    assert job(factory, stub, app_id, AT + HOLD, form=form)[0] == "submitted"
    assert len(form.gate_clicks) == 1


def test_first_three_likes_rehearse_before_full_auto_goes_live(db, factory, person_a, stub):
    from app.apply import full_auto, likes
    from tests.apply.conftest import add_posting

    switch(factory, person_a)
    turn_on(db, person_a, done=0)
    for number in range(3):
        posting, _ = add_posting(
            db, person_a.user_id, f"https://www.linkedin.com/jobs/view/{4100000201 + number}/"
        )
        db.execute(
            sa.update(Job)
            .where(Job.id == posting)
            .values(
                title=f"Robotics Specialist {number}", company=f"Synthetic Company {number}", fit=90
            )
        )
        db.commit()
        app_id = likes.on_like(db, person_a.user_id, posting, now=AT, stub_base=stub).application.id
        form = LinkedInForm()
        assert fill(services(factory, form, stub_base=stub), app_id) == "ready"
        submitted = LinkedInForm()
        assert job(factory, stub, app_id, AT, form=submitted)[0] == "held"
        assert job(factory, stub, app_id, AT, form=submitted)[0] == "rehearsed"
        assert submitted.gate_clicks == []
        full_auto.confirm_rehearsal(db, person_a.user_id, app_id, AT)
        db.commit()
        assert caps.settings_of(db, person_a.user_id).rehearsals_done == number + 1


def test_second_person_waits_for_shared_linkedin_host_interval(
    db, factory, person_a, person_b, stub
):
    from tests.apply import a11_support as a11

    switch(factory, person_a)
    switch(factory, person_b)
    app_id = start(db, person_b)
    assert fill(services(factory, LinkedInForm(), stub_base=stub), app_id) == "ready"
    approve(factory, person_b, app_id)
    sent = a11.clicked(
        db, person_a.user_id, AT, ats="linkedin", form_url=URL, company="A Different Company"
    )
    a11.click(db, sent, AT)
    db.commit()
    form = LinkedInForm()
    svc = submit_services(factory, form, stub)
    svc.clock = at(AT + timedelta(seconds=599))
    assert run_submit(svc, app_id) == "paced"
    assert application(db, app_id).needs == "host_pace"
    assert form.gate_clicks == []
    assert application(db, app_id).state is State.approved
    svc.clock = at(AT + timedelta(seconds=600))
    assert run_submit(svc, app_id) == "submitted"
    assert len(form.gate_clicks) == 1


def test_checkpoint_commits_seven_day_pause_and_moves_held_to_review(db, factory, person_a, stub):
    switch(factory, person_a)
    turn_on(db, person_a)
    app_id = start(db, person_a, Mode.full_auto)
    assert fill(services(factory, LinkedInForm(), stub_base=stub), app_id) == "ready"
    assert job(factory, stub, app_id, AT, form=LinkedInForm())[0] == "held"
    form = LinkedInForm(wall="Unusual activity")
    assert job(factory, stub, app_id, AT + HOLD, form=form)[0] in ("needs_person", "superseded")
    found = application(db, app_id)
    assert (found.mode, found.state) == (Mode.review, State.ready)
    assert found.error_code == "linkedin_paused"
    assert form.gate_clicks == []
    assert linkedin.linkedin_guard.paused_until(db, person_a.user_id, AT + HOLD) > AT + timedelta(
        days=6
    )


def test_checkpoint_current_fill_keeps_visible_reason(db, factory, person_a, stub):
    switch(factory, person_a)
    app_id = start(db, person_a)
    form = LinkedInForm(wall="Security verification")
    assert fill(services(factory, form, stub_base=stub), app_id) == "needs_person"
    found = application(db, app_id)
    assert (found.state, found.needs, found.error_code) == (
        State.needs_person,
        "linkedin_paused",
        "linkedin_paused",
    )
    assert form.openings == 0
    assert linkedin.linkedin_guard.paused_until(db, person_a.user_id, AT) > AT + timedelta(days=6)


def test_checkpoint_current_review_submit_keeps_visible_reason(db, factory, person_a, stub):
    switch(factory, person_a)
    app_id = start(db, person_a)
    assert fill(services(factory, LinkedInForm(), stub_base=stub), app_id) == "ready"
    approve(factory, person_a, app_id)
    form = LinkedInForm(wall="Unusual activity")
    assert run_submit(submit_services(factory, form, stub), app_id) == "needs_person"
    found = application(db, app_id)
    assert (found.state, found.needs, found.error_code) == (
        State.needs_person,
        "linkedin_paused",
        "linkedin_paused",
    )
    assert form.gate_clicks == []


def test_person_b_without_consent_never_acquires_a_lease(db, factory, person_a, person_b, stub):
    switch(factory, person_a)
    app_id = start(db, person_b)
    leases, form = FakeLeases(), LinkedInForm()
    assert fill(services(factory, form, leases, stub_base=stub), app_id) == "blocked"
    assert leases.acquired == []
    assert form.openings == 0


def test_company_site_gets_a_new_restricted_lease(db, factory, person_a, stub):
    switch(factory, person_a)
    app_id = start(db, person_a)
    target = "https://boards.greenhouse.io/northwind-labs/jobs/4012345"
    form, leases = LinkedInForm(company=target), FakeLeases()
    assert fill(services(factory, form, leases, stub_base=stub), app_id) == "queued"
    found = application(db, app_id)
    assert (found.form_url, found.ats) == (target, "greenhouse")
    assert form.openings == 0
    assert all("atsforms" not in str(call) for call in leases.acquired)
    assert len(leases.acquired) == 1
    assert leases.released


def test_expired_signin_uses_owned_handoff_and_full_auto_goes_to_review(
    db, factory, person_a, stub
):
    switch(factory, person_a)
    app_id = start(db, person_a, Mode.full_auto)
    form = LinkedInForm(wall="Sign in")
    assert fill(services(factory, form, stub_base=stub), app_id) == "needs_person"
    found = application(db, app_id)
    assert (found.mode, found.needs) == (Mode.review, "signin")
    assert form.openings == 0


def test_below_threshold_goes_to_review(db, factory, person_a, stub):
    switch(factory, person_a)
    turn_on(db, person_a)
    db.execute(sa.update(Job).where(Job.id == person_a.posting_id).values(fit=20))
    db.commit()
    app_id = start(db, person_a, Mode.full_auto)
    assert fill(services(factory, LinkedInForm(), stub_base=stub), app_id) == "ready"
    assert job(factory, stub, app_id, AT, form=LinkedInForm())[0] == "below_min_fit"
    assert application(db, app_id).mode is Mode.review


def test_sixteenth_in_24_hours_goes_to_review(db, factory, person_a, stub):
    from tests.apply import a11_support as a11

    switch(factory, person_a)
    turn_on(db, person_a)
    for index in range(15):
        sent = a11.clicked(
            db,
            person_a.user_id,
            AT - timedelta(minutes=index + 1),
            company=f"Other Company {index}",
            ats="linkedin",
            form_url=f"https://www.linkedin.com/jobs/view/{100 + index}/",
        )
        a11.click(db, sent, AT - timedelta(minutes=index + 1))
    db.commit()
    app_id = start(db, person_a, Mode.full_auto)
    assert fill(services(factory, LinkedInForm(), stub_base=stub), app_id) == "ready"
    form = LinkedInForm()
    assert job(factory, stub, app_id, AT, form=form)[0] == "linkedin_daily"
    assert application(db, app_id).mode is Mode.review
    assert form.gate_clicks == []


@pytest.mark.parametrize(
    "url",
    [
        "https://WWW.LINKEDIN.COM/jobs/view/1",
        "https://www.linkedin.com./jobs/view/1",
        "https://www.linkedin。com/jobs/view/1",
        "https://www.linkedin%2ecom/jobs/view/1",
        "https://fr.linkedin.com/jobs/view/1",
    ],
)
def test_host_aliases_still_require_current_consent(url):
    assert caps.host_of("other", url) == "linkedin"
