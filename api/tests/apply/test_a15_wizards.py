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
        tools = signin.Tools(form, url(), request=ask, adapter=adapter)
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


def test_unknown_required_early_step_stops_once_then_person_answer_reaches_final_designation():
    from tests.apply.a4_support import Spec

    async def go():
        form = WizardForm()
        form.fields.insert(3, Spec("Your project codename", "text", True))
        form.groups[0] += 1
        adapter = Workday(BASE)
        assert await adapter.open(form, url()) == ""
        report = await adapter.fill_form(form, context())
        assert (report.stopped, report.needs_person) == ("needs_person", "required_left")
        assert form.step == 0
        assert report.steps == []
        assert not report.designated
        assert not any(call == "click" for call, _ in form.calls)
        assert len(report.outcomes) == 4
        report.shapes = adapter.seen_shapes
        partial = rows(report)
        assert next(r.value for r in partial if r.key == "designated:incomplete") == "true"
        assert not any(r.key == "designated:submit" for r in partial)
        unknown = next(o.field for o in report.outcomes if o.field.label == "Your project codename")
        fresh = WizardForm()
        fresh.fields.insert(3, Spec("Your project codename", "text", True))
        fresh.groups[0] += 1
        resumed = Workday(BASE)
        resumed.person_answers = {
            unknown.key: generic.PersonAnswer(
                unknown.label, unknown.kind, tuple(unknown.options), "Example Cedar"
            )
        }
        resumed.person_shapes = adapter.seen_shapes
        assert await resumed.open(fresh, url()) == ""
        complete = await resumed.fill_form(fresh, context())
        assert not complete.stopped
        assert complete.designated == "Submit application"
        assert fresh.spec("Your project codename").value == "Example Cedar"
        assert not fresh.gate_clicks

    asyncio.run(go())


@pytest.mark.parametrize("change", ["reorder", "delete", "kind", "label", "required", "options"])
def test_person_continuation_refuses_changed_or_reordered_field_shapes(change):
    async def go():
        form = WizardForm()
        adapter = Workday(BASE)
        assert await adapter.open(form, url()) == ""
        _found, code = await adapter.discover(form)
        assert not code
        original = adapter.seen_shapes
        if change == "reorder":
            form.fields[0], form.fields[1] = form.fields[1], form.fields[0]
        elif change == "delete":
            form.fields.pop(0)
            form.groups[0] -= 1
        elif change == "kind":
            form.fields[0].kind = "checkbox"
        elif change == "label":
            form.fields[0].label = "Changed name"
        elif change == "required":
            form.fields[0].required = False
        else:
            form.fields[0].kind, form.fields[0].options = "select", ("Other",)
        adapter.person_shapes = original
        assert (await adapter.discover(form))[1] == "form_changed"
        assert not form.acted()

    asyncio.run(go())


@pytest.mark.parametrize("code", ["signed_in", "nobody_watching", "signin_cancelled"])
def test_known_form_redirect_to_external_signin_never_broadens_the_automated_host(code):
    async def go():
        form = WizardForm()
        original_navigate = form.navigate
        attempts = []
        signed = False

        async def navigate(target):
            attempts.append(target)
            if "apply=manual" in target and not signed:
                return {"ok": False, "error": "host_not_allowed", "url": form.url}
            return await original_navigate(target)

        form.navigate = navigate
        asked = []

        async def ask(tools, host, reason):
            nonlocal signed
            asked.append(host)
            signed = code == "signed_in"
            return Outcome(code)

        adapter = Workday(BASE)
        tools = signin.Tools(form, url(), request=ask, adapter=adapter)
        result = await adapter.open(tools, url())
        assert result == ("" if code == "signed_in" else "signin")
        assert asked == ["atsforms"]
        assert all(target.startswith(url()) for target in attempts)
        assert adapter.job_id == "fixture:1001"

    asyncio.run(go())


def test_workday_summary_compares_the_displayed_yes_no_choice():
    from tests.apply.a4_support import Spec

    async def go():
        form = WizardForm()
        form.fields.insert(3, Spec("Will you need sponsorship?", "yesno", True))
        form.groups[0] += 1
        adapter = Workday(BASE)
        assert await adapter.open(form, url()) == ""
        found, code = await adapter.discover(form)
        assert not code
        item = next(f for f in found.fields if f.kind == "yes_no")
        adapter.person_answers = {
            item.key: generic.PersonAnswer(item.label, item.kind, tuple(item.options), False)
        }
        report = await adapter.fill_form(form, context())
        assert not report.stopped
        assert report.designated
        assert form.spec(item.label).value == "No"

    asyncio.run(go())


@pytest.mark.parametrize("person_override", [True, False])
def test_french_no_choice_replays_from_normal_and_person_boolean_answers(person_override):
    from dataclasses import replace

    from tests.apply.a4_support import Spec

    label = "Will you now or in the future require visa sponsorship?"

    def form():
        page = WizardForm(french=True)
        page.fields.insert(3, Spec(label, "yesno", True))
        page.groups[0] += 1
        return page

    async def go():
        first = form()
        adapter = Workday(BASE)
        assert await adapter.open(first, url()) == ""
        found, code = await adapter.discover(first)
        assert not code
        item = next(f for f in found.fields if f.kind == "yes_no")
        if person_override:
            adapter.person_answers = {
                item.key: generic.PersonAnswer(item.label, item.kind, tuple(item.options), False)
            }
        own = replace(context(), posting=answers.Posting.at("Paris, France"))
        report = await adapter.fill_form(first, own)
        assert not report.stopped
        approved = rows(report)
        if person_override:
            approved = [
                replace(row, value=False, source="person") if row.key == item.key else row
                for row in approved
            ]
        fresh, opened = form(), Workday(BASE)
        assert await opened.open(fresh, url()) == ""
        replay = generic.configured(opened, approved)
        for stage in generic.stages(replay, approved):
            checked = await submit._refilled(replay, fresh, stage)
            assert isinstance(checked, base.Discovery), checked
            if replay.step < len(replay.steps):
                assert await generic.advance(replay, fresh, checked) == ""
        assert await generic.recheck(replay, fresh, checked) == ""
        for changed in ("Yes", "Maybe"):
            fresh.spec(label).value = changed
            found, code = await replay.discover(fresh)
            assert not code
            assert await generic.recheck(replay, fresh, found) == "form_changed"
        assert not fresh.gate_clicks

    asyncio.run(go())


@pytest.mark.parametrize("shape", ["inline", "nested", "mixed"])
def test_summary_value_shapes_preserve_the_exact_typed_multiset(shape):
    def group(label, value, inline):
        body = f": {value}" if inline else f":\n    - generic: {value}"
        return f'  - group "{label}"{body}'

    adapter = Workday()
    adapter.expected = [("First name", "Alex"), ("Sponsorship", False)]
    snapshot = '- region "Application summary":\n' + group("First name", "Alex", shape != "nested")
    snapshot += "\n" + group("Sponsorship", "Non", shape == "inline")
    root = base.parse_snapshot(snapshot)
    assert not adapter.summary_problem(root)
    assert adapter.summary_problem(base.parse_snapshot(snapshot.replace("Non", "Oui")))
    assert adapter.summary_problem(
        base.parse_snapshot(snapshot + "\n" + group("First name", "Alex", True))
    )
    missing = '- region "Application summary":\n' + group("Sponsorship", "Non", True)
    assert adapter.summary_problem(base.parse_snapshot(missing))


def test_summary_duplicate_labels_keep_boolean_and_text_provenance():
    adapter = Workday()
    adapter.expected = [("Answer", False), ("Answer", "No")]
    snapshot = '- region "Application summary":\n  - group "Answer": No\n  - group "Answer": Non'
    assert not adapter.summary_problem(base.parse_snapshot(snapshot))
    assert adapter.summary_problem(base.parse_snapshot(snapshot.replace("Non", "Oui")))


SIGNED_OUT_HEADER = """- banner:
  - link "Sign in" [ref=e1]
  - button "Create Account" [ref=e2]
- navigation:
  - link "Sign In" [ref=e3]
- main:
  - heading "Robotics Engineer" [ref=e4]
  - textbox "First name" [ref=e5]
- contentinfo:
  - link "Connectez-vous" [ref=e6]
"""


@pytest.mark.parametrize("adapter", [Workday, GoogleCareers, Vie])
def test_a_sign_in_in_the_site_header_menu_or_footer_is_no_wall(adapter):
    assert adapter(BASE).pending_at(base.parse_snapshot(SIGNED_OUT_HEADER)) == ""


@pytest.mark.parametrize(
    "page",
    [
        '- main:\n  - heading "Sign in" [ref=e1]\n  - textbox "Email" [ref=e2]\n',
        '- dialog "Apply":\n  - button "Create Account" [ref=e1]\n  - button "Sign In" [ref=e2]\n',
        '- heading "Sign in to apply" [ref=e1]\n',
        '- banner:\n  - link "Sign in" [ref=e1]\n- main:\n  - button "Sign in" [ref=e2]\n',
    ],
)
def test_a_sign_in_in_the_page_body_is_still_a_wall(page):
    assert Workday(BASE).pending_at(base.parse_snapshot(page)) == "signin"


@pytest.mark.parametrize(
    "path",
    [
        "/en-US/Careers/job/Paris/Robotics-Engineer_R1234",
        "/fr-FR/Careers/job/Paris/Robotics-Engineer_R1234",
        "/Careers/job/Paris/Robotics-Engineer_R1234",
        "/de-DE/Careers/job/Paris/Robotics-Engineer_R1234/apply",
    ],
)
def test_workday_key_is_one_job_in_every_locale(path):
    from app.apply import guardrails

    url = "https://example.wd3.myworkdayjobs.com" + path
    assert guardrails.canonical_key(url) == (
        "workday:example.wd3.myworkdayjobs.com/Careers/job/Paris/Robotics-Engineer_R1234"
    )
    assert Workday().form_url(url) == (
        "https://example.wd3.myworkdayjobs.com"
        + path.removesuffix("/apply").removesuffix("/")
        + "/apply/applyManually"
    )


def test_workday_key_keeps_other_tenants_and_jobs_apart():
    from app.apply import guardrails

    one = "https://example.wd3.myworkdayjobs.com/en-US/Careers/job/Paris/Robotics-Engineer_R1234"
    assert guardrails.canonical_key(one) != guardrails.canonical_key(
        one.replace("example.", "northwind.")
    )
    assert guardrails.canonical_key(one) != guardrails.canonical_key(one.replace("R1234", "R1235"))
    assert guardrails.canonical_key(one) != guardrails.canonical_key(
        one.replace("/en-US/Careers", "/en-US/Interns")
    )
