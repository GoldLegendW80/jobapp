"""Known ATS fallback keeps its code-owned strategy, exact shape and original budget."""

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.apply import approval_router, fallback, form_agent, full_auto, gate, run, submit
from app.apply.answers import Context, Documents
from app.apply.ats import adapter_for, base
from app.apply.ats.ashby import Ashby
from app.apply.ats.greenhouse import Greenhouse
from app.apply.ats.lever import Lever
from app.apply.models import AnswerSource, Application, Mode, State
from app.apply.submit import Row
from tests.apply.a8b_support import ScriptedFormAgent, two_steps, workable

URL = "https://jobs.ashbyhq.com/example/role"
TARGET = URL + "/application"


def row(**changes):
    original = Row(
        fallback.KEY,
        "Application form",
        "designated",
        {"version": 1, "vendor": "ashby", "form_url": URL, "target": TARGET},
        "identity",
        False,
    )
    return replace(original, **changes)


def test_only_the_exact_code_binding_selects_the_fallback():
    value = fallback.binding([row()], URL, "ashby")
    assert value == row().value
    assert fallback.target(adapter_for(URL), URL, TARGET) == TARGET


@pytest.mark.parametrize(
    "changes",
    [{"source": source.value} for source in AnswerSource if source is not AnswerSource.identity]
    + [
        {"kind": "text"},
        {"required": True},
        {"value": None},
        {"value": "ashby"},
        {"value": {}},
        *[
            {"value": {**row().value, key: value}}
            for key, value in [
                ("version", True),
                ("version", 2),
                ("vendor", "generic"),
                ("vendor", "greenhouse"),
                ("form_url", URL + "2"),
                ("target", "https://elsewhere.example/form"),
                ("target", "javascript:alert(1)"),
                ("target", "https://person@jobs.ashbyhq.com/example/role/application"),
                ("target", TARGET + "#changed"),
                ("extra", "untrusted"),
            ]
        ],
    ],
)
def test_neighbour_bindings_are_refused(changes):
    assert fallback.binding([row(**changes)], URL, "ashby") is None


@pytest.mark.parametrize("vendor", ["generic", "workday", "greenhouse", "", None])
def test_an_application_cannot_borrow_another_strategy(vendor):
    assert fallback.binding([row()], URL, vendor) is None


def test_duplicate_or_missing_bindings_are_refused():
    assert fallback.binding([], URL, "ashby") is None
    assert fallback.binding([row(), row()], URL, "ashby") is None


@pytest.mark.parametrize(
    "target",
    [
        "https://jobs.ashbyhq.com/example/other/application",
        "https://jobs.ashbyhq.com/other/role/application",
        "https://jobs.ashbyhq.com/evil/role/application",
        "https://evil.example/example/role/application",
        "http://jobs.ashbyhq.com/example/role/application",
        TARGET + "#fragment",
        "https://person@jobs.ashbyhq.com/example/role/application",
        "",
    ],
)
def test_only_the_same_known_form_target_can_be_bound(target):
    assert fallback.target(adapter_for(URL), URL, target) is None


def test_orm_and_submit_rows_share_one_validation_contract():
    stored = SimpleNamespace(
        field_key=fallback.KEY,
        kind="designated",
        source=AnswerSource.identity,
        required=False,
        value=row().value,
    )
    assert fallback.binding([stored], URL, "ashby") == row().value


def plan(mode=Mode.review):
    user = uuid.uuid4()
    sections = {"identity": {"full_name": "Ada Example", "email": "e2e-fallback@example.org"}}
    return run.Plan(
        user,
        uuid.uuid4(),
        URL,
        Context(user, sections, documents=Documents(cv_path="cv.pdf")),
        sections,
        "Robotics Engineer",
        "Example Robotics",
        "",
        "",
        {"cv.pdf": b"%PDF invented"},
        mode,
    )


def services(agent):
    return SimpleNamespace(
        form_agent=agent,
        stub_base=None,
        clock=lambda: datetime(2026, 10, 22, tzinfo=UTC),
        answer_agent=None,
    )


SCRIPT = [
    ("fill_field", "First name"),
    ("fill_field", "Last name"),
    ("fill_field", "Email"),
    ("fill_field", "Resume"),
    ("designate_submit", "Submit application"),
]


@pytest.mark.parametrize("mode", [Mode.review, Mode.full_auto])
@pytest.mark.parametrize("phase", ["open", "fill"])
@pytest.mark.parametrize(
    ("known", "url"),
    [
        (Ashby, URL),
        (Greenhouse, "https://boards.greenhouse.io/example/jobs/123"),
        (Lever, "https://jobs.lever.co/example/12345678-1234-1234-1234-123456789abc"),
    ],
)
def test_known_form_change_routes_once_on_the_existing_page(mode, phase, known, url):
    asyncio.run(_known_form_change(mode, phase, known, url))


async def _known_form_change(mode, phase, known, url):
    form = workable()
    target = known().form_url(url)

    class Changed(known):
        async def open(self, tools, posting_url):
            await tools.navigate(target)
            return "form_changed" if phase == "open" else ""

        async def fill_form(self, tools, context):
            return base.Report(stopped="form_changed")

    seen = []
    scripted = ScriptedFormAgent(SCRIPT)

    async def agent(session):
        seen.append(session)
        return await scripted(session)

    current = plan(mode)
    current.form_url = url
    result = await run._drive(services(agent), current, form, Changed())
    assert result.state is State.ready
    assert len(seen) == 1
    assert seen[0].tools is form
    assert seen[0].token_budget == 34_000
    assert seen[0].turn_budget == form_agent.MAX_TURNS
    assert form.submit_clicks == 0
    assert result.strategy == {
        "version": 1,
        "vendor": known.name,
        "form_url": url,
        "target": target,
    }
    assert result.report.designated == "Submit application"


def test_a_saved_fallback_is_replayed_without_calling_the_known_adapter(monkeypatch):
    asyncio.run(_saved_fallback(monkeypatch))


async def _saved_fallback(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("known adapter or model was called during approved generic replay")

    monkeypatch.setattr(Ashby, "open", forbidden)
    form = workable()
    opened = await submit._open(
        services(forbidden),
        SimpleNamespace(form_url=URL, rows=[row()]),
        form,
    )
    assert isinstance(opened, fallback.Generic)
    assert form.url == TARGET


@pytest.mark.parametrize(
    ("problem", "expected"),
    [
        (None, None),
        ("below_min_fit", "below_min_fit"),
        ("fields_left", "fields_left"),
        ("agent_designated", "agent_designated"),
        ("captcha_seen", "captcha_seen"),
        ("never_apply_text", "never_apply_text"),
        ("model_check_not_run", "model_check_not_run"),
        ("model_check_failed", "model_check_failed"),
    ],
)
def test_a_trusted_designation_keeps_every_other_full_auto_check(monkeypatch, problem, expected):
    application = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        ats="ashby",
        form_url=URL,
        updated_at=datetime(2026, 10, 22, tzinfo=UTC),
    )
    bound = SimpleNamespace(
        field_key=fallback.KEY,
        kind="designated",
        source=AnswerSource.identity,
        required=False,
        value=row().value,
    )
    designated = SimpleNamespace(
        field_key="designated:submit",
        kind="designated",
        source=AnswerSource.agent,
        required=False,
        value="Submit application",
    )
    rows = [designated] if problem == "agent_designated" else [bound, designated]
    if problem == "fields_left":
        rows.append(SimpleNamespace(kind="text", source=AnswerSource.left, field_key="left"))
    db = SimpleNamespace(scalars=lambda query: rows)
    monkeypatch.setattr(full_auto.caps, "linkedin_daily", lambda *args: False)
    monkeypatch.setattr(full_auto.caps, "settings_of", lambda *args: SimpleNamespace(min_fit=80))
    monkeypatch.setattr(full_auto, "_captcha_seen", lambda *args: problem == "captcha_seen")
    monkeypatch.setattr(full_auto.service, "_rules", lambda *args: [])
    monkeypatch.setattr(
        full_auto.guardrails,
        "never_apply",
        lambda *args, **kwargs: "never_apply" if problem == "never_apply_text" else None,
    )
    monkeypatch.setattr(
        full_auto.model_check,
        "code",
        lambda *args: problem if problem and problem.startswith("model_check") else None,
    )
    posting = SimpleNamespace(fit=79 if problem == "below_min_fit" else 100)
    assert full_auto.checks(db, application, posting) == expected


@pytest.mark.parametrize("used", [0, 1, 5000, 33999, 34000, 35000])
def test_fallback_gets_only_the_known_attempts_remaining_tokens(used):
    async def check():
        form = workable()
        await form.navigate(TARGET)
        seen = []

        async def agent(session):
            seen.append(session.token_budget)
            return form_agent.Ran(stopped="cancelled")

        current = plan()
        current.form_deadline = services(agent).clock().timestamp() + 60
        await run._known_fallback(
            services(agent),
            current,
            Ashby(),
            form,
            cost={"agent": "ran", "input_tokens": used, "output_tokens": 0},
        )
        assert seen == ([34000 - used] if used < 34000 else [])
        assert current.fallback_used
        await run._known_fallback(services(agent), current, Ashby(), form)
        assert len(seen) <= 1

    asyncio.run(check())


def test_an_expired_attempt_cannot_start_a_new_generic_budget():
    async def check():
        form = workable()
        await form.navigate(TARGET)

        async def forbidden(session):
            raise AssertionError("expired attempt started a new model")

        current = plan()
        current.form_deadline = services(forbidden).clock().timestamp()
        result = await run._known_fallback(services(forbidden), current, Ashby(), form)
        assert result.needs == "form_changed"
        assert form.submit_clicks == 0

    asyncio.run(check())


@pytest.mark.parametrize("source", ["person", "agent", "answers", "left"])
def test_modified_binding_stops_before_navigation_or_typing(source):
    async def check():
        form = workable()
        result = await submit._open(
            services(None),
            SimpleNamespace(form_url=URL, rows=[row(source=source)]),
            form,
        )
        assert isinstance(result, submit.Stop)
        assert (result.state, result.code) == (State.ready, "form_changed")
        assert form.url == ""
        assert not form.submit_clicks

    asyncio.run(check())


@pytest.mark.parametrize("phase", ["open", "fill"])
@pytest.mark.parametrize(
    "code", ["captcha", "signin", "posting_closed", "snapshot_cut", "page_error"]
)
def test_other_known_adapter_failures_never_start_the_form_agent(phase, code):
    async def check():
        form = workable()

        class Other(Ashby):
            async def open(self, tools, posting_url):
                await tools.navigate(TARGET)
                return code if phase == "open" else ""

            async def fill_form(self, tools, context):
                return base.Report(stopped=code)

        async def forbidden(session):
            raise AssertionError("another adapter error started generic")

        current = plan()
        result = await run._drive(services(forbidden), current, form, Other())
        assert result.state is not State.ready
        assert not current.fallback_used

    asyncio.run(check())


def test_unknown_full_auto_keeps_its_review_first_boundary():
    async def check():
        current = plan(Mode.full_auto)
        form = workable()

        async def forbidden(session):
            raise AssertionError("unknown site started a Full auto model")

        result = await run._drive_generic(services(forbidden), current, fallback.Generic(), form)
        assert (result.state, result.needs) == (State.needs_person, "no_adapter")
        assert not current.fallback_used

    asyncio.run(check())


@pytest.mark.parametrize("changed", [False, True])
def test_exact_fallback_refill_and_changed_structure_need_no_model(changed):
    async def check():
        current = plan()
        form = workable()
        await form.navigate(TARGET)
        for name, data in current.pdfs.items():
            await form.put_file(name, data)
        current.form_deadline = services(None).clock().timestamp() + 60
        result = await run._known_fallback(
            services(ScriptedFormAgent(SCRIPT)),
            current,
            Ashby(),
            form,
        )
        assert result.state is State.ready
        application = Application(id=current.application_id, user_id=current.user_id)
        stored = run._answer_rows(application, result.report, result.strategy)
        approved = [
            Row(r.field_key, r.label, r.kind, r.value, r.source.value, r.required) for r in stored
        ]
        if changed:
            form.fields[0] = replace(form.fields[0], required=False)
        prepared = SimpleNamespace(form_url=URL, rows=approved, pdfs=current.pdfs)
        checked = await submit.ready_to_click(services(None), prepared, form)
        if changed:
            assert isinstance(checked, submit.Stop)
            assert checked.code == "form_changed"
        else:
            assert isinstance(checked, submit.Read)
            assert isinstance(checked.adapter, fallback.Generic)
        assert not form.submit_clicks

    asyncio.run(check())


def test_strategy_is_hidden_from_product_answers_and_bound_to_the_approval_hash(monkeypatch):
    current = plan()
    application = Application(id=current.application_id, user_id=current.user_id, ats="ashby")
    stored = run._answer_rows(application, base.Report(), row().value)
    monkeypatch.setattr(gate, "rows_of", lambda *args: stored)
    assert approval_router._answers(None, application) == []
    before = gate.answers_hash(stored, {})
    stored[0].value = {**stored[0].value, "target": TARGET + "?changed=1"}
    assert gate.answers_hash(stored, {}) != before


def test_the_person_cannot_grant_or_edit_the_strategy(monkeypatch):
    current = plan()
    application = Application(
        id=current.application_id,
        user_id=current.user_id,
        state=State.ready,
    )
    strategy = run._answer_rows(application, base.Report(), row().value)[0]
    monkeypatch.setattr(gate, "lock", lambda *args: (application, None))
    db = SimpleNamespace(execute=lambda *args: SimpleNamespace(scalar_one_or_none=lambda: strategy))
    with pytest.raises(gate.InvalidAnswerError, match="unknown_field"):
        gate.set_answer(
            db,
            current.user_id,
            current.application_id,
            fallback.KEY,
            "generic",
            services(None).clock(),
        )


def test_successful_known_adapter_keeps_its_strategy_without_a_form_model():
    async def check():
        form = workable()

        class Works(Ashby):
            async def fill_form(self, tools, context):
                return base.Report()

        async def forbidden(session):
            raise AssertionError("known success called generic")

        current = plan()
        result = await run._drive(services(forbidden), current, form, Works())
        assert result.state is State.ready
        assert result.strategy is None
        assert not current.fallback_used

    asyncio.run(check())


def test_midway_fallback_keeps_and_replays_the_earlier_known_steps():
    async def check():
        current = plan()
        form = two_steps()

        class Midway(Ashby):
            async def fill_form(self, tools, context):
                reader = fallback.Generic(known=self)
                session = form_agent.FormSession(reader, tools, context, str(current.user_id))
                await ScriptedFormAgent([*SCRIPT[:3], ("next_step", "Next")])(session)
                report = await session.finish()
                self.step, self.seen_shapes = reader.step, reader.shapes
                report.stopped = "form_changed"
                return report

        scripted = ScriptedFormAgent([("fill_field", "Resume"), ("designate_submit", "Submit")])
        result = await run._drive(services(scripted), current, form, Midway())
        assert result.state is State.ready
        assert result.report.steps == ["Next"]
        assert len(result.report.outcomes) == 5
        application = Application(id=current.application_id, user_id=current.user_id)
        stored = run._answer_rows(application, result.report, result.strategy)
        rows = [
            Row(r.field_key, r.label, r.kind, r.value, r.source.value, r.required) for r in stored
        ]
        form.step = 0  # the submit lease starts a fresh form
        checked = await submit.ready_to_click(
            services(None),
            SimpleNamespace(form_url=URL, rows=rows, pdfs=current.pdfs),
            form,
        )
        assert isinstance(checked, submit.Read)
        assert form.step == 1
        assert not form.submit_clicks

    asyncio.run(check())
