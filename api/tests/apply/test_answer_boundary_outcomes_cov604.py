"""Personal sources fit the visible widget or remain for the person to answer."""

import asyncio
import copy
import json
import threading
import uuid

import pytest
import sqlalchemy as sa

from app.agents import credentials, runner, runs
from app.agents.failures import INTERNAL
from app.agents.models import AgentRun, RunStatus
from app.apply import answers, form_agent, gate, model_check
from app.apply.ats import generic
from app.apply.bank import AnswerBank
from app.apply.models import Application, ApplicationEvent, Mode, State
from tests.apply import a11_support
from tests.apply.a4_support import Spec
from tests.apply.a8b_support import CustomForm
from tests.apply.conftest import AT
from tests.apply.test_a5_answer_agent import runs_of
from tests.apply.test_a13c_check import TRUE_TEXT, CheckRuntime, checks_of, item, rows
from tests.profile.conftest import full

OWNER = uuid.UUID(int=604)


def context(changes=None, *, bank=None):
    sections = {name: full(name) for name in ("identity", "experience", "skills", "answers")}
    sections["identity"]["email"] = "e2e-answer-boundary@example.org"
    sections["answers"]["leave_blank"] = []
    for name, values in (changes or {}).items():
        sections[name].update(values)
    return answers.Context(OWNER, sections, answers.Posting.at("Paris, France"), bank=bank)


@pytest.mark.parametrize(
    ("label", "kind", "options", "changes", "reason"),
    [
        (
            "Phone country code",
            "text",
            (),
            {"identity": {"phone": "06 00 00 00 00"}},
            "no_phone_country_code",
        ),
        (
            "Phone country code",
            "select",
            ("France +33", "France mainland +33"),
            {},
            "no_matching_option",
        ),
        ("Location", "text", (), {"identity": {"city": None, "country": None}}, "no_location"),
        ("Languages", "text", (), {"identity": {"languages": []}}, "no_languages"),
        ("Languages", "yes_no", (), {}, "not_a_yes_or_no"),
        (
            "English level",
            "text",
            (),
            {"identity": {"languages": [{"name": "English", "level": None}]}},
            "no_language_level",
        ),
        ("English level", "date", (), {}, "not_a_yes_or_no"),
        ("Do you speak English?", "textarea", (), {}, "not_a_text_question"),
        (
            "Are you willing to relocate?",
            "yes_no",
            (),
            {"answers": {"relocation": None}},
            "no_relocation",
        ),
        ("Are you willing to work on site?", "textarea", (), {}, "not_a_yes_or_no"),
        ("Do you have 3 years of experience?", "textarea", (), {}, "not_a_yes_or_no"),
        ("Years of experience", "yes_no", (), {}, "no_threshold"),
        ("Full name", "file", (), {}, "not_a_file"),
        ("Full name", "date", (), {}, "not_a_date"),
        ("Notice period", "yes_no", (), {}, "not_a_yes_or_no"),
        (
            "Earliest start date",
            "text",
            (),
            {"answers": {"earliest_start": None}},
            "no_earliest_start",
        ),
        ("Are you willing to work on site?", "date", (), {}, "not_a_date"),
        ("I am willing to join optional mailing lists", "checkbox", (), {}, "person_choice"),
        ("", "select", ("Yes", "No"), {}, "no_label"),
        (
            "Graduation year",
            "text",
            (),
            {"answers": {"highest_degree_year": None}, "experience": {"education": []}},
            "no_graduation_year",
        ),
        (
            "Work authorisation",
            "select",
            ("Authorized to work", "Requires sponsorship"),
            {
                "answers": {
                    "work_authorization": [
                        {
                            "country": "France",
                            "answer": "The person must clarify.",
                            "needs_sponsorship": None,
                        }
                    ]
                }
            },
            "countries_disagree_or_unstated",
        ),
    ],
)
def test_source_and_widget_refusals_preserve_the_profile_and_saved_answers(
    label, kind, options, changes, reason
):
    saved = AnswerBank(OWNER).with_answer("Favourite robot", "Saved answer")
    ctx = context(changes, bank=saved)
    before = copy.deepcopy(ctx.sections)
    field = answers.FormField(label, kind, options, True)
    result = answers.resolve(field, ctx)
    assert result == answers.Left(reason, options, True)
    assert ctx.sections == before
    assert ctx.bank == saved


@pytest.mark.parametrize(
    ("label", "kind", "options", "changes", "value", "source"),
    [
        ("Phone country code", "text", (), {}, "+33", "identity.phone"),
        (
            "Languages",
            "select",
            ("French (Français)", "English"),
            {"identity": {"languages": [{"name": "French", "level": "Native"}]}},
            "French (Français)",
            "identity.languages",
        ),
        (
            "Graduation year",
            "text",
            (),
            {"answers": {"highest_degree_year": None}},
            "2025",
            "experience.education",
        ),
        (
            "Years of experience",
            "select",
            ("Up to 4 years", "5+ years", "Prefer not to answer"),
            {
                "skills": {"years_experience": None},
                "answers": {"years_experience_stated": "3.5 years"},
            },
            "Up to 4 years",
            "skills.years_experience",
        ),
        (
            "Gender",
            "select",
            ("Woman", "Prefer not to answer"),
            {},
            "Woman",
            "answers.eeo_answers.gender",
        ),
        (
            "Willing to relocate?",
            "yes_no",
            (),
            {"answers": {"relocation": "No"}},
            False,
            "answers.relocation",
        ),
        (
            "Are you legally authorized to work in this country?",
            "textarea",
            (),
            {
                "answers": {
                    "work_authorization": [
                        {
                            "country": "Resident in France",
                            "answer": "My exact authorisation sentence.",
                            "needs_sponsorship": False,
                        }
                    ]
                }
            },
            "My exact authorisation sentence.",
            "answers.work_authorization",
        ),
    ],
)
def test_exact_personal_sources_survive_the_widget_boundary(
    label, kind, options, changes, value, source
):
    ctx = context(changes, bank=AnswerBank(OWNER).with_answer(label, "Stale saved answer"))
    result = answers.resolve(answers.FormField(label, kind, options, False), ctx)
    assert isinstance(result, answers.Answer)
    assert (result.value, result.source) == (value, source)


@pytest.mark.parametrize(
    "options", [("",), ("Beginner", "5+ years"), ("1 year 3 months", "5+ years")]
)
def test_experience_options_with_no_single_readable_band_do_not_invent_a_choice(options):
    result = answers.resolve(answers.FormField("Years of experience", "select", options), context())
    assert result == answers.Left("no_matching_option", options, False)


def test_a_foreign_saved_bank_cannot_be_attached_to_a_persons_context():
    bank = AnswerBank(uuid.UUID(int=605)).with_answer("Preferred name", "Other Candidate")
    with pytest.raises(ValueError, match="belongs to another person"):
        context(bank=bank)


@pytest.mark.parametrize("code", ["captcha", "ended", "host_not_allowed"])
def test_a_browser_refusal_during_prefill_stops_before_the_next_personal_field(code):
    class RefusingForm(CustomForm):
        async def type(self, ref, text, element="", submit=False):
            self._log("refused", ref=ref)
            return {"ok": False, "error": code}

    async def go():
        form = RefusingForm([[Spec("Full name", "text"), Spec("Email", "text")]])
        session = form_agent.FormSession(
            generic.Generic(), form, context(), str(OWNER), deadline=100, clock=lambda: 1
        )
        await session.prefill()
        report = await session.finish()
        assert len(report.outcomes) == 2
        first, second = report.outcomes
        assert (first.filled.status, first.filled.code) == ("failed", code)
        assert second.filled.status == "left"
        assert form.spec("Full name").value == form.spec("Email").value == ""
        assert [name for name, args in form.calls].count("refused") == 1
        assert report.designated == ""
        if code == "captcha":
            assert (report.stopped, report.needs_person) == ("needs_person", "captcha")
        else:
            assert report.stopped == code
            assert report.refused == {code: 1}
            text, error = await session.fill_field(form.ref_of("Email"))
            assert error
            assert text == "The fill has stopped. Stop now."
        assert form.gate_clicks == []
        assert form.submit_clicks == 0

    asyncio.run(go())


@pytest.mark.parametrize("ref_kind", ["gone", "field", "unrelated", "step_limit"])
def test_untrusted_step_targets_cannot_click_or_advance_the_form(ref_kind):
    async def go():
        form = CustomForm(
            [[Spec("Full name", "text")], [Spec("Email", "text")]],
            extras=(("button", "Subscribe"),),
        )
        session = form_agent.FormSession(generic.Generic(), form, context(), str(OWNER))
        if ref_kind == "gone":
            ref = "missing-ref"
        elif ref_kind == "field":
            ref = form.ref_of("Full name")
        elif ref_kind == "unrelated":
            ref = form.ref_of("Subscribe")
        else:
            ref = form.ref_of("Next")
            session.steps = ["Next"] * generic.STEPS_MAX
        before = list(session.steps)
        _, error = await session.next_step(ref)
        assert error
        report = await session.finish()
        assert report.refused == {
            {
                "gone": "not_a_button",
                "field": "not_a_button",
                "unrelated": "not_a_step",
                "step_limit": "too_many_steps",
            }[ref_kind]: 1
        }
        assert session.steps == before
        assert form.step == session.adapter.step == 0
        assert not any(name == "click" for name, args in form.calls)
        assert form.submit_clicks == 0

    asyncio.run(go())


def test_a_missing_field_ref_returns_an_error_and_does_not_disclose_or_type_personal_data():
    async def go():
        form = CustomForm([[Spec("Full name", "text")]])
        session = form_agent.FormSession(generic.Generic(), form, context(), str(OWNER))
        text, error = await session.resolve_answer("old-ref")
        assert error
        assert text == "No field has this ref. Read the form again."
        assert "Ada Example" not in text
        assert session.outcomes == {}
        assert not any(name == "type" for name, args in form.calls)

    asyncio.run(go())


def test_a_browser_timeout_during_prefill_leaves_the_fields_unfilled():
    class TimedOutForm(CustomForm):
        timed_out = False

        async def snapshot(self):
            if not self.timed_out:
                self.timed_out = True
                raise TimeoutError("invented snapshot timeout")
            return await super().snapshot()

    async def go():
        form = TimedOutForm([[Spec("Full name", "text")]])
        session = form_agent.FormSession(
            generic.Generic(), form, context(), str(OWNER), deadline=100, clock=lambda: 1
        )
        await session.prefill()
        report = await session.finish()
        assert report.stopped == "budget_exceeded"
        assert [outcome.filled.status for outcome in report.outcomes] == ["left"]
        assert form.spec("Full name").value == ""
        assert report.designated == ""
        assert not any(name in {"type", "click"} for name, args in form.calls)

    asyncio.run(go())


def test_a_captcha_shown_by_the_form_prevents_resolving_or_filling_a_field():
    async def go():
        form = CustomForm([[Spec("Full name", "text")]], extras=(("checkbox", "I am not a robot"),))
        session = form_agent.FormSession(generic.Generic(), form, context(), str(OWNER))
        text, error = await session.resolve_answer(form.ref_of("Full name"))
        assert error
        assert text == "A step only the person can take is shown. Stop now."
        report = await session.finish()
        assert (report.stopped, report.needs_person) == ("needs_person", "captcha")
        assert form.spec("Full name").value == ""
        assert not any(name in {"type", "click"} for name, args in form.calls)

    asyncio.run(go())


def test_a_submit_designation_changed_by_the_page_is_removed_from_the_final_report():
    async def go():
        form = CustomForm([[Spec("Full name", "text"), Spec("Email", "text")]])
        session = form_agent.FormSession(generic.Generic(), form, context(), str(OWNER))
        _, error = await session.designate_submit(form.ref_of("Submit application"))
        assert not error
        form.submit = "Send application"
        report = await session.finish()
        assert report.designated == report.designated_ref == ""
        assert report.refused == {"designation_changed": 1}
        assert form.submit_clicks == 0
        assert form.gate_clicks == []

    asyncio.run(go())


def check_request(person):
    return model_check.Request(
        user_id=str(person.user_id),
        texts=(model_check.Text("why", "Why Example Robotics?", TRUE_TEXT),),
        profile={},
        posting_title="Applied AI Engineer",
        posting_company="Example Robotics",
        posting_text="Build warehouse robots.",
    )


@pytest.mark.parametrize("has_credential", [True, False])
@pytest.mark.parametrize("kind", ["check", "form"])
def test_cancelled_run_creation_releases_the_slot_or_preserves_the_no_run_refusal(
    db, factory, migrated_url, person_a, monkeypatch, has_credential, kind
):
    runtime = CheckRuntime(factory, migrated_url)
    if has_credential:
        runtime.give_key(person_a)
    entered, release = threading.Event(), threading.Event()
    read_credential = credentials.get

    def held(*args, **kwargs):
        entered.set()
        release.wait()
        return read_credential(*args, **kwargs)

    monkeypatch.setattr(credentials, "get", held)

    async def go():
        if kind == "check":
            work = runtime.check()(check_request(person_a))
        else:
            form = CustomForm([[Spec("Full name", "text")]])
            session = form_agent.FormSession(
                generic.Generic(), form, context(), str(person_a.user_id)
            )
            work = form_agent.RuntimeFormAgent(runtime.context)(session)
        task = asyncio.create_task(work)
        try:
            await asyncio.to_thread(entered.wait)
            task.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    made = runs_of(db, person_a.user_id)
    if has_credential:
        assert [(r.status, r.error) for r in made] == [(RunStatus.failed, runs.STOPPED)]
    else:
        assert made == []
    assert runtime.backends.made == []
    assert model_check._PENDING == {}
    assert model_check._REPORTED == {}


def test_check_cancelled_before_the_runner_claims_it_does_not_hold_a_persons_run_slot(
    db, factory, migrated_url, person_a, monkeypatch
):
    runtime = CheckRuntime(factory, migrated_url)
    runtime.give_key(person_a)

    async def go():
        entered = asyncio.Event()

        async def not_claimed(context, run_id):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(runner, "execute", not_claimed)
        task = asyncio.create_task(runtime.check()(check_request(person_a)))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    made = runs_of(db, person_a.user_id)
    assert [(r.status, r.error) for r in made] == [(RunStatus.failed, runs.STOPPED)]
    assert runtime.backends.made == []
    assert model_check._PENDING == {}
    assert model_check._REPORTED == {}


def test_a_check_run_without_a_waiting_request_fails_before_calling_the_model(
    db, factory, migrated_url, person_a
):
    runtime = CheckRuntime(factory, migrated_url)
    runtime.give_key(person_a)
    made = runs.create_run(
        db,
        person_a.user_id,
        model_check.NAME,
        None,
        {"request": "f" * 32, "token_budget": model_check.TOKEN_BUDGET},
        model_check.MODEL,
        AT,
    )
    db.commit()
    assert asyncio.run(runner.execute(runtime.context(), made.id)) is RunStatus.failed
    db.expire_all()
    ended = db.get(AgentRun, made.id)
    assert ended.status is RunStatus.failed
    assert ended.error == INTERNAL
    assert runtime.backends.made == []
    assert model_check._PENDING == {}


@pytest.mark.parametrize(
    ("state", "mode"), [(State.queued, Mode.full_auto), (State.ready, Mode.review)]
)
def test_the_model_check_does_not_spend_a_call_on_an_item_outside_full_auto_ready(
    db, person_a, state, mode
):
    app = a11_support.application(db, person_a.user_id, person_a.posting_id, state=state, mode=mode)
    db.commit()
    assert model_check.request_for(db, app.id, AT) == "superseded"
    assert checks_of(db, app.id) == []
    assert app.cost == {}


def test_a_check_for_a_deleted_item_stores_no_result_or_cost(db):
    app_id = uuid.uuid4()
    outcome = model_check.Outcome(model_check.Verdict.passed, "checked")
    assert model_check.request_for(db, app_id, AT) == "gone"
    assert (
        model_check.record(
            db,
            app_id,
            "0" * 64,
            "0" * 64,
            outcome,
            model_check.Reply(input_tokens=10, output_tokens=2),
            AT,
        )
        == "gone"
    )
    db.commit()
    assert db.scalars(sa.select(ApplicationEvent)).all() == []


def test_an_invalid_persisted_check_verdict_does_not_authorise_agent_text(
    db, factory, person_a, forms_base_url
):
    app_id = item(db, factory, person_a, forms_base_url, TRUE_TEXT)
    app = db.get(Application, app_id)
    digest = gate.current_hash(db, app).hex()
    read = model_check.read_digest(model_check.texts_of(gate.rows_of(db, app)))
    assert (
        model_check.record(
            db,
            app_id,
            digest,
            read,
            model_check.Outcome(model_check.Verdict.passed, "checked"),
            model_check.Reply(input_tokens=10, output_tokens=2),
            AT,
        )
        == "passed"
    )
    db.commit()
    event = db.scalar(
        sa.select(ApplicationEvent).where(
            ApplicationEvent.application_id == app_id, ApplicationEvent.kind == model_check.EVENT
        )
    )
    event.data = {**event.data, "verdict": "unknown-imported-verdict"}
    db.commit()
    expected = model_check.CODE_OF[model_check.Verdict.failed]
    assert model_check.code(db, app, rows(db, app_id)) == expected
    assert app.state is State.ready
    assert not any(e.kind == "click" for e in db.scalars(sa.select(ApplicationEvent)))
    assert json.dumps(checks_of(db, app_id)).find(TRUE_TEXT) == -1
