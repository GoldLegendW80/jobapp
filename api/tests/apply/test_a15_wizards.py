"""Known wizards bind every step and replay the approved answers without a model."""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.apply import answers, run, signin, submit
from app.apply.ats import adapter_for, base, generic
from app.apply.ats.google_careers import GoogleCareers
from app.apply.ats.vie import Vie
from app.apply.ats.workday import Workday
from app.browser.handoff import Outcome
from tests.apply.a15_support import BASE, WizardForm, url
from tests.profile.conftest import full


def context():
    return answers.Context(
        uuid.uuid4(),
        {k: full(k) for k in ("identity", "answers")},
        documents=answers.Documents("cv.pdf", "letter.pdf", "Own letter"),
    )


def rows(report):
    app = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4())
    return [
        submit.Row(r.field_key, r.label, r.kind, r.value, r.source.value, r.required)
        for r in run._answer_rows(app, report)
    ]


@pytest.mark.parametrize("kind", ["workday", "google_careers", "vie"])
def test_steps_are_filled_by_code_and_designated_without_a_submit_click(kind):
    async def go():
        form = WizardForm(kind=kind)
        adapter = adapter_for(url(kind), stub_base=BASE)
        assert isinstance(adapter, generic.Deterministic)
        assert await adapter.open(form, url(kind)) == ""
        report = await adapter.fill_form(form, context())
        assert not report.stopped
        assert report.designated == "Submit application"
        assert report.steps == (["Next", "Next"] if kind == "workday" else [])
        assert form.submit_clicks == 0
        assert not form.gate_clicks
        assert all(o.filled.status == "filled" for o in report.outcomes)
        if kind == "google_careers":
            assert form.parsed
            assert form.spec("First name").value != "Parser changed this"
            assert report.target == form.url
        if kind == "vie":
            assert form.spec("Motivation message").value == full("answers")["motivation"]
        return adapter, report

    asyncio.run(go())


@pytest.mark.parametrize("kind", ["workday", "google_careers", "vie"])
def test_approved_replay_retains_job_context_and_reads_every_ordered_step(kind):
    async def go():
        form = WizardForm(kind=kind)
        adapter = adapter_for(url(kind), stub_base=BASE)
        assert await adapter.open(form, url(kind)) == ""
        report = await adapter.fill_form(form, context())
        approved = rows(report)
        fresh = WizardForm(kind=kind)
        opened = adapter_for(url(kind), stub_base=BASE)
        assert await opened.open(fresh, url(kind)) == ""
        replay = generic.configured(opened, approved)
        assert replay.job_id == opened.job_id
        assert replay is not opened
        stages = generic.stages(replay, approved)
        assert len(stages) == (3 if kind == "workday" else 1)
        for stage in stages:
            checked = await submit._refilled(replay, fresh, stage)
            assert isinstance(checked, base.Discovery), checked
            if replay.step < len(replay.steps):
                assert await generic.advance(replay, fresh, checked) == ""
        assert await generic.recheck(replay, fresh, checked) == ""
        assert fresh.submit_clicks == 0
        fresh.url = url(kind, "1002")
        assert (await replay.discover(fresh))[1] == "form_changed"

    asyncio.run(go())


@pytest.mark.parametrize(
    ("variant", "needs"), [("expire", "signin"), ("payment", "payment_requested")]
)
def test_each_transition_stops_before_touching_a_person_only_page(variant, needs):
    async def go():
        form = WizardForm(variant=variant)
        adapter = Workday(BASE)
        assert await adapter.open(form, url()) == ""
        report = await adapter.fill_form(form, context())
        assert (report.stopped, report.needs_person) == ("needs_person", needs)
        assert form.submit_clicks == 0
        assert not any(call == "upload" for call, _ in form.calls)

    asyncio.run(go())


@pytest.mark.parametrize(
    "code", ["signed_in", "nobody_watching", "signin_cancelled", "signin_timeout", "turn_ended"]
)
def test_expired_midwizard_handoff_preserves_the_exact_job_and_does_not_type_credentials(code):
    async def go():
        form = WizardForm(variant="expire")
        asked = []

        async def ask(tools, host, reason):
            asked.append(host)
            if code == "signed_in":
                form.wall = ""
            return Outcome(code)

        adapter = Workday(BASE)
        tools = signin.Tools(form, url(), request=ask, pending=adapter.pending_step)
        assert await adapter.open(tools, url()) == ""
        report = await adapter.fill_form(tools, context())
        assert asked == ["atsforms"]
        assert form.url.startswith(url())
        assert report.stopped == ("" if code == "signed_in" else "needs_person")
        assert not any(
            call == "type" and args.get("element") == "Password" for call, args in form.calls
        )
        assert not form.gate_clicks

    asyncio.run(go())


@pytest.mark.parametrize(("adapter", "kind"), [(GoogleCareers, "google_careers"), (Vie, "vie")])
def test_candidate_profile_wall_is_left_for_the_person(adapter, kind):
    async def go():
        form = WizardForm(kind=kind, variant="incomplete")
        one = adapter(BASE)
        assert await one.open(form, url(kind)) == ""
        report = await one.fill_form(form, context())
        assert (report.stopped, report.needs_person) == ("needs_person", "profile_incomplete")
        assert not form.acted()

    asyncio.run(go())


def test_workday_summary_must_match_all_approved_answers():
    async def go():
        form = WizardForm(variant="summary")
        adapter = Workday(BASE)
        assert await adapter.open(form, url()) == ""
        report = await adapter.fill_form(form, context())
        assert report.stopped == "form_changed"
        assert not report.designated

    asyncio.run(go())


def test_replay_preserves_pending_step_codes():
    for code in ("signin", "captcha", "payment_requested", "profile_incomplete"):
        stop = submit._stop_of(code)
        assert stop.state.value == "needs_person"
        assert stop.needs == code


@pytest.mark.parametrize(
    ("target", "accepted"),
    [
        ("https://www.google.com/about/careers/applications/jobs/apply?jobId=opaque_A12", True),
        ("/about/careers/applications/jobs/apply?jobId=opaque-A12", True),
        ("https://evil.example.org/about/careers/applications/jobs/apply?jobId=opaque", False),
        (
            "https://www.google.com.evil.example.org/about/careers/applications/jobs/apply?jobId=opaque",
            False,
        ),
        ("https://careers.google.com/applications/jobs/apply?jobId=opaque", False),
        ("https://www.google.com:8443/about/careers/applications/jobs/apply?jobId=opaque", False),
        ("https://user@www.google.com/about/careers/applications/jobs/apply?jobId=opaque", False),
        ("/about/careers/applications/jobs/apply?jobId=opaque&jobId=other", False),
        ("/about/careers/applications/jobs/apply?jobId=opaque&redirect=evil", False),
        ("/about/careers/applications/jobs/apply?jobId=opaque#fragment", False),
        ("/about/careers/applications/jobs/apply?jobId=", False),
        ("/about/careers/applications/jobs/delete?jobId=opaque", False),
        ("/about/careers/applications/jobs/results/123-engineer/apply", False),
    ],
)
def test_google_uses_only_the_unique_actual_same_origin_apply_target(target, accepted):
    adapter = GoogleCareers()
    adapter.job_id = "123"
    page = f'- link "Apply" [ref=e1]:\n  - /url: {target}'
    original = "https://www.google.com/about/careers/applications/jobs/results/123-engineer"
    assert bool(adapter.apply_target(page, original)) is accepted
    if accepted:
        other = (
            page + '\n- link "Apply" [ref=e2]:\n'
            "  - /url: /about/careers/applications/jobs/apply?jobId=other"
        )
        assert not adapter.apply_target(other, original)


def test_google_changed_apply_target_invalidates_the_approval():
    async def go():
        form = WizardForm(kind="google_careers")
        adapter = GoogleCareers(BASE)
        assert await adapter.open(form, url("google_careers")) == ""
        approved = rows(await adapter.fill_form(form, context()))
        target = next(row for row in approved if row.key == "designated:target")
        changed = submit.Row(
            target.key, target.label, target.kind, "other", target.source, target.required
        )
        approved = [changed if row.key == target.key else row for row in approved]
        replay = generic.configured(adapter, approved)
        assert (await replay.discover(form))[1] == "form_changed"

    asyncio.run(go())
