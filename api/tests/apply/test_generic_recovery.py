"""Framed takeover and credential-free filling keep the original safety boundaries."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.agents import runner
from app.apply import fallback, form_agent, run, submit
from app.apply.ats import base, generic
from app.apply.ats.ashby import Ashby
from app.apply.ats.greenhouse import Greenhouse
from app.apply.models import Application, Mode, State
from app.apply.submit import Row
from tests.apply.a4_support import Spec
from tests.apply.a8b_support import CustomForm, ScriptedFormAgent, two_steps, workable
from tests.apply.test_a8_known_fallback import SCRIPT, TARGET, URL, plan, row, services

COMPANY = "https://careers.example.org/jobs/123"


@pytest.mark.parametrize("host", ["jobs.ashbyhq.com", "JOBS.ASHBYHQ.COM", "Jobs.Ashbyhq.Com"])
@pytest.mark.parametrize("port", ["", ":443"])
@pytest.mark.parametrize(
    "target",
    [
        TARGET,
        TARGET.replace("jobs.ashbyhq.com", "JOBS.ASHBYHQ.COM"),
        TARGET.replace("jobs.ashbyhq.com", "jobs.ashbyhq.com:443"),
        TARGET.replace("jobs.ashbyhq.com", "JOBS.ASHBYHQ.COM:443"),
    ],
)
def test_host_case_and_default_port_bind_the_browser_canonical_origin(host, port, target):
    original = f"https://{host}{port}/example/role"
    assert fallback.target(Ashby(), original, target) == TARGET
    assert fallback.binding([row(value={**row().value, "form_url": original})], original, "ashby")


@pytest.mark.parametrize(
    "origin",
    [
        "https://jobs.ashbyhq.com:wrong",
        "https://jobs.ashbyhq.com:65536",
        "https://jobs.ashbyhq.com:",
        "https://jobs.ashbyhq.com:-1",
        "https://jobs.ashbyhq.com:0",
        "https://jobs.ashbyhq.com:444",
        "https://jobs.ashbyhq.com.evil.example",
        "https://person@jobs.ashbyhq.com",
        "https://person:secret@jobs.ashbyhq.com",
        "http://jobs.ashbyhq.com",
        "https://other.example",
        "//jobs.ashbyhq.com",
    ],
)
def test_origin_neighbours_refuse_without_raising(origin):
    target = origin + "/example/role/application"
    assert fallback.target(Ashby(), URL, target) is None
    assert fallback.target(Ashby(), target, TARGET) is None
    assert fallback.binding([row(value={**row().value, "target": target})], URL, "ashby") is None


@pytest.mark.parametrize("host", ["jobs.ashbyhq.com", "JOBS.ASHBYHQ.COM", "Jobs.Ashbyhq.Com"])
@pytest.mark.parametrize("port", ["", ":443"])
def test_saved_fallback_replay_uses_the_canonical_host_and_default_port(host, port):
    async def check():
        original = f"https://{host}{port}/example/role"
        approved = row(value={**row().value, "form_url": original})
        form = workable()
        opened = await submit._open(
            services(None), SimpleNamespace(form_url=original, rows=[approved]), form
        )
        assert isinstance(opened, fallback.Generic)
        assert form.url == TARGET
        assert not form.acted()

    asyncio.run(check())


@pytest.mark.parametrize(
    "authority",
    [
        "jobs.ashbyhq.com:wrong",
        "jobs.ashbyhq.com:65536",
        "jobs.ashbyhq.com:-1",
        "jobs.ashbyhq.com:0",
        "jobs.ashbyhq.com:444",
        "jobs.ashbyhq.com:",
        "person@jobs.ashbyhq.com",
        "jobs.ashbyhq.com.evil.example",
    ],
)
def test_invalid_saved_fallback_origins_refuse_before_navigation(authority):
    async def check():
        original = f"https://{authority}/example/role"
        form = workable()
        opened = await submit._open(
            services(None),
            SimpleNamespace(
                form_url=original, rows=[row(value={**row().value, "form_url": original})]
            ),
            form,
        )
        assert isinstance(opened, submit.Stop)
        assert opened.code == "form_changed"
        assert not form.url
        assert not form.acted()

    asyncio.run(check())


@pytest.mark.parametrize("mode", [Mode.review, Mode.full_auto])
@pytest.mark.parametrize("phase", ["open", "fill"])
def test_framed_known_recovery_fills_only_the_frame_and_replays_without_model(mode, phase):
    async def check():
        form = workable(frame=True)

        class Changed(Greenhouse):
            async def open(self, tools, posting_url):
                await tools.navigate(COMPANY)
                return "form_changed" if phase == "open" else ""

            async def fill_form(self, tools, context):
                return base.Report(stopped="form_changed")

        current = plan(mode)
        current.form_url = COMPANY
        result = await run._drive(
            services(ScriptedFormAgent(SCRIPT)), current, form, Changed(framed=True)
        )
        assert result.state is State.ready
        assert result.review_reason == "frame_origin_unverified"
        assert result.strategy == {
            "version": 1,
            "vendor": "greenhouse",
            "form_url": COMPANY,
            "target": COMPANY,
            "frame": 0,
        }
        assert result.cost["fallback"] == "greenhouse"
        assert not form.submit_clicks
        assert all(
            call[1]["ref"].startswith("f1")
            for call in form.acted()
            if call[0] in {"type", "upload"}
        )
        application = Application(id=current.application_id, user_id=current.user_id)
        stored = run._answer_rows(application, result.report, result.strategy)
        rows = [
            Row(r.field_key, r.label, r.kind, r.value, r.source.value, r.required) for r in stored
        ]
        assert fallback.binding(rows, COMPANY, "greenhouse") is None  # no Full auto exception
        prepared = SimpleNamespace(form_url=COMPANY, rows=rows, pdfs=current.pdfs)
        checked = await submit.ready_to_click(services(None), prepared, form)
        assert isinstance(checked, submit.Read)
        form.fields[0] = replace(form.fields[0], required=False)
        refused = await submit.ready_to_click(services(None), prepared, form)
        assert isinstance(refused, submit.Stop)
        assert refused.code == "form_changed"
        assert not form.submit_clicks

    asyncio.run(check())


@pytest.mark.parametrize("current", [COMPANY, COMPANY.upper().replace("/JOBS/", "/jobs/")])
def test_framed_target_binds_the_company_address_without_claiming_frame_origin(current):
    assert fallback.target(Greenhouse(framed=True), COMPANY, current) == COMPANY
    assert (
        fallback.target(Greenhouse(framed=True), COMPANY, "https://elsewhere.example/jobs/123")
        is None
    )


def test_no_credential_runtime_prefills_from_existing_sources_without_a_model(monkeypatch):
    async def check():
        current = plan()
        current.context.sections["answers"] = {"notice_period": "30 days"}
        form = CustomForm(
            [
                [
                    Spec("Full name", "text"),
                    Spec("Email", "text"),
                    Spec("Resume", "file"),
                    Spec("Notice period", "text"),
                    Spec("Current salary", "text"),
                    Spec("Join our talent community", "checkbox"),
                    Spec("Unknown question", "text"),
                ]
            ]
        )
        for name, data in current.pdfs.items():
            await form.put_file(name, data)
        session = form_agent.FormSession(
            generic.Generic(), form, current.context, str(current.user_id)
        )
        monkeypatch.setattr(form_agent.credentials, "get", lambda *args: None)
        monkeypatch.setattr(
            form_agent.answer_agents, "_in_db", lambda sessions, function: function(None)
        )

        async def forbidden(*args):
            raise AssertionError("credential-free fill ran a model")

        monkeypatch.setattr(runner, "execute", forbidden)
        agent = form_agent.RuntimeFormAgent(lambda: SimpleNamespace(sessions=None))
        report, cost = await form_agent.fill(agent, session)
        assert cost["stopped"] == "no_credential"
        assert cost["input_tokens"] == cost["output_tokens"] == 0
        assert [f.value for f in form.fields[:4]] == [
            "Ada Example",
            "e2e-fallback@example.org",
            "cv.pdf",
            "30 days",
        ]
        assert [f.value for f in form.fields[4:]] == ["", "", ""]
        assert not form.fields[5].checked
        assert not report.designated
        assert not form.submit_clicks
        assert {o.answer.source for o in report.outcomes if o.filled.status == "filled"} == {
            "identity.full_name",
            "identity.email",
            "documents.cv",
            "answers.notice_period",
        }
        assert len([o for o in report.outcomes if o.filled.status == "left"]) == 3
        assert not [c for c in form.acted() if c[0] in {"click", "check", "select_option"}]

    asyncio.run(check())


@pytest.mark.parametrize("control", ["expired", "captcha", "signin", "lease_ended"])
def test_no_credential_prefill_stops_at_time_pending_and_lease_guards(monkeypatch, control):
    async def check():
        current = plan()
        form = workable()
        if control == "captcha":
            form.extras.append(("checkbox", "I'm not a robot"))
        elif control == "signin":
            form.fields.append(Spec("Password", "text"))
            form.groups[0] += 1
        elif control == "lease_ended":

            async def ended():
                return {"ok": False, "error": "lease_ended"}

            form.snapshot = ended
        session = form_agent.FormSession(
            generic.Generic(),
            form,
            current.context,
            str(current.user_id),
            clock=lambda: 100.0,
            deadline=100.0 if control == "expired" else 200.0,
        )
        monkeypatch.setattr(form_agent.credentials, "get", lambda *args: None)
        monkeypatch.setattr(
            form_agent.answer_agents, "_in_db", lambda sessions, function: function(None)
        )
        report, _ = await form_agent.fill(
            form_agent.RuntimeFormAgent(lambda: SimpleNamespace(sessions=None)), session
        )
        assert not form.acted()
        assert not report.designated
        assert not form.submit_clicks

    asyncio.run(check())


@pytest.mark.parametrize("state", [State.ready, State.needs_person])
@pytest.mark.parametrize("mode", [Mode.review, Mode.full_auto])
def test_frame_review_handover_is_persisted_under_the_existing_finish_lock(
    monkeypatch, state, mode
):
    current = plan(mode)
    application = Application(
        id=current.application_id,
        user_id=current.user_id,
        state=State.filling,
        mode=mode,
        dry_run=True,
    )
    events = []
    monkeypatch.setattr(run, "_locked", lambda *args: (application, SimpleNamespace()))
    monkeypatch.setattr(run, "_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        run.full_auto.service, "_event", lambda *args, **kwargs: events.append(kwargs)
    )
    empty = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
    db = SimpleNamespace(execute=lambda *args: empty, add_all=lambda *args: None)
    result = run.Result(
        state,
        needs="field_left" if state is State.needs_person else None,
        review_reason="frame_origin_unverified",
    )
    now = services(None).clock()
    assert run.finish(db, application.id, result, now, now) == state.value
    assert application.mode is Mode.review
    assert application.state is state
    if mode is Mode.full_auto:
        assert not application.dry_run
        assert events == [{"code": "frame_origin_unverified"}]
    else:
        assert not events


@pytest.mark.parametrize("frame", [-1, 100, True, "0", None, {}, 1.5])
def test_malformed_frame_bindings_cannot_select_replay(frame):
    value = {
        "version": 1,
        "vendor": "greenhouse",
        "form_url": COMPANY,
        "target": COMPANY,
        "frame": frame,
    }
    assert fallback.binding([row(value=value)], COMPANY, "greenhouse", allow_frame=True) is None


def test_frame_scope_never_reads_a_neighbouring_company_field():
    async def check():
        current = plan()
        form = workable(frame=True)
        render = form.render

        def with_neighbour():
            return render() + '\n  - textbox "Email" [ref=e999]'

        form.render = with_neighbour
        adapter = fallback.Generic(known=Greenhouse(framed=True), frame=0)
        session = form_agent.FormSession(adapter, form, current.context, str(current.user_id))
        view, _ = await session.read_form()
        assert "e999" not in view
        adapter.frame = 1
        found, code = await adapter.discover(form)
        assert found is None or code == "form_changed"
        assert not form.acted()

    asyncio.run(check())


def test_no_credential_prefill_preserves_the_persons_exact_local_answer(monkeypatch):
    async def check():
        current = plan()
        form = workable()
        adapter = generic.Generic()
        found, _ = await adapter.read(form)
        item = next(item for item in found.fields if item.label == "Email")
        adapter.person_answers[item.key] = base.PersonAnswer(
            item.label, item.kind, tuple(item.options), "e2e-person-edit@example.org"
        )
        session = form_agent.FormSession(adapter, form, current.context, str(current.user_id))
        monkeypatch.setattr(form_agent.credentials, "get", lambda *args: None)
        monkeypatch.setattr(
            form_agent.answer_agents, "_in_db", lambda sessions, function: function(None)
        )
        report, _ = await form_agent.fill(
            form_agent.RuntimeFormAgent(lambda: SimpleNamespace(sessions=None)), session
        )
        assert form.fields[2].value == "e2e-person-edit@example.org"
        assert (
            next(o for o in report.outcomes if o.field.label == "Email").answer.source == "person"
        )
        assert not form.submit_clicks

    asyncio.run(check())


@pytest.mark.parametrize("control", ["expired_after_first", "lease_ended_after_first"])
def test_deterministic_prefill_keeps_completed_values_and_stops_before_another_field(
    monkeypatch, control
):
    async def check():
        current = plan()
        form = workable()
        now = [100.0]
        original = form.type

        async def first_only(*args, **kwargs):
            result = await original(*args, **kwargs)
            if control == "expired_after_first":
                now[0] = 200.0
            else:

                async def ended():
                    return {"ok": False, "error": "lease_ended"}

                form.snapshot = ended
            return result

        form.type = first_only
        session = form_agent.FormSession(
            generic.Generic(),
            form,
            current.context,
            str(current.user_id),
            deadline=200.0,
            clock=lambda: now[0],
        )
        monkeypatch.setattr(form_agent.credentials, "get", lambda *args: None)
        monkeypatch.setattr(
            form_agent.answer_agents, "_in_db", lambda sessions, function: function(None)
        )
        report, _ = await form_agent.fill(
            form_agent.RuntimeFormAgent(lambda: SimpleNamespace(sessions=None)), session
        )
        assert len([call for call in form.acted() if call[0] == "type"]) == 1
        assert not [call for call in form.acted() if call[0] in {"click", "check", "upload"}]
        assert not report.designated
        if control == "expired_after_first":
            assert report.stopped == "budget_exceeded"
            assert len([o for o in report.outcomes if o.filled.status == "filled"]) == 1
        else:
            assert report.stopped == "lease_ended"
        assert not form.submit_clicks

    asyncio.run(check())


def test_framed_midway_takeover_replays_the_original_steps_and_shapes_without_a_model():
    async def check():
        current = plan()
        current.form_url = COMPANY
        form = two_steps(frame=True)

        class Midway(Greenhouse):
            async def fill_form(self, tools, context):
                reader = fallback.Generic(known=self, frame=0)
                session = form_agent.FormSession(reader, tools, context, str(current.user_id))
                await ScriptedFormAgent([*SCRIPT[:3], ("next_step", "Next")])(session)
                report = await session.finish()
                self.seen_shapes = reader.shapes
                report.stopped = "form_changed"
                return report

        scripted = ScriptedFormAgent([("fill_field", "Resume"), ("designate_submit", "Submit")])
        result = await run._drive(services(scripted), current, form, Midway(framed=True))
        assert result.state is State.ready
        assert result.report.steps == ["Next"]
        assert len(result.report.outcomes) == 5
        assert result.review_reason == "frame_origin_unverified"
        application = Application(id=current.application_id, user_id=current.user_id)
        stored = run._answer_rows(application, result.report, result.strategy)
        rows = [
            Row(r.field_key, r.label, r.kind, r.value, r.source.value, r.required) for r in stored
        ]
        form.step = 0
        checked = await submit.ready_to_click(
            services(None), SimpleNamespace(form_url=COMPANY, rows=rows, pdfs=current.pdfs), form
        )
        assert isinstance(checked, submit.Read)
        assert form.step == 1
        assert not form.submit_clicks

    asyncio.run(check())
