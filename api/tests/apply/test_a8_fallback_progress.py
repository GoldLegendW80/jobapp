"""Controlled fallback stops retain answer evidence without renewing permission or budgets."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.apply import form_agent, run
from app.apply.answers import Answer
from app.apply.ats import base, generic
from app.apply.models import AnswerSource, Application, ApplicationAnswer, State
from tests.apply.a8b_support import workable
from tests.apply.conftest import AT
from tests.apply.test_a5_run import started
from tests.apply.test_a8_known_fallback import plan


async def attempt(monkeypatch, phase, elapsed=1.0, interruption="", wizard=False, change=None):
    now, events, models, timers = [99.0], [], [], []
    first = base.Field("text:first name#1", "First name", "text", ref="old1")
    second = base.Field("text:summary#1", "Summary", "text", ref="old2")
    if wizard:
        second = replace(second, key="s2/" + second.key)
    old = [
        base.Outcome(first, Answer("Exact person edit", "person"), base.Filled("filled")),
        base.Outcome(
            second, Answer("Exact document prose", "documents.letter_text"), base.Filled("filled")
        ),
    ]
    previous = form_agent.FormReport(
        outcomes=old,
        skipped=[base.Skipped("Unknown earlier choice", "button", "unsupported_widget")],
        steps=["Next"] if wizard else [],
        designated="Submit application",
        designated_ref="old-submit",
    )
    current = (
        [replace(second, ref="live2")]
        if wizard
        else [replace(first, ref="live1"), replace(second, ref="live2")]
    )
    if change:
        current[0] = replace(current[0], **change)
    extra = base.Field(("s2/" if wizard else "") + "text:extra#1", "Extra", "text", ref="live3")
    if phase == "partial":
        current.append(extra)
    actual_timeout = asyncio.timeout
    crossed = False

    def timeout(delay):
        timer = actual_timeout(delay)
        timers.append(timer)
        return timer

    async def boundary(name):
        nonlocal crossed
        events.append(name)
        if crossed or name != phase:
            return
        crossed = True
        now[0] += elapsed
        if interruption == "timeout":
            timers[0].reschedule(asyncio.get_running_loop().time())
            await asyncio.Future()
        elif interruption == "cancel":
            raise asyncio.CancelledError()

    class Adapter(generic.Generic):
        async def read(self, tools):
            await boundary("read")
            self.shapes[str(self.step)] = json.dumps(
                [[f.key, f.label, f.kind, list(f.options), f.required] for f in current]
            )
            return base.Discovery(current, [], [], [], root=base.Node("root")), ""

        async def fill(self, tools, item, answer):
            await boundary("refill:" + item.ref)
            return base.Filled("filled")

        def resolve(self, item, context):
            return Answer("New verified progress", "identity.full_name")

    async def model(session):
        models.append(
            (session.clock(), session.deadline, session.token_budget, session.turn_budget)
        )
        if phase == "changed_model":
            current[0] = replace(
                current[0], label="Changed live question", required=True, ref="new2"
            )
            await session.read_form()
            await boundary("changed_model")
        elif phase == "partial":
            await session.fill_field(extra.ref)
            session.designated = "Submit application"
            await boundary("partial")
        else:
            await boundary("model")
        if phase == "model" and session.clock() >= session.deadline:
            # These tool calls must be refused without another browser read or action.
            await session.fill_field(current[0].ref)
            await session.next_step("next")
            await session.designate_submit("submit")
        return form_agent.Ran(stopped="cancelled")

    async def shot(*args):
        return None

    monkeypatch.setattr(run.asyncio, "timeout", timeout)
    monkeypatch.setattr(run.shots, "take", shot)
    current_plan = plan()
    current_plan.form_deadline = 100.0
    services = SimpleNamespace(
        clock=lambda: datetime.fromtimestamp(now[0], UTC),
        form_agent=None if phase == "no_agent" else model,
    )
    adapter = Adapter()
    adapter.step = int(wizard)
    adapter.shapes = {"0": "earlier-shape"} if wizard else {}
    previous.shapes = dict(adapter.shapes)
    if phase == "before":
        now[0] = 100.0
    spent = {"input_tokens": 137, "output_tokens": 0}
    if phase == "tokens":
        spent["input_tokens"] = 34_000
    result = await run._drive_generic(
        services,
        current_plan,
        adapter,
        workable(),
        strategy={"vendor": "ashby"},
        previous_report=previous,
        previous_cost=spent,
    )
    application = SimpleNamespace(id=current_plan.application_id, user_id=current_plan.user_id)
    rows = run._answer_rows(application, result.report, result.strategy)
    return result, rows, events, models, old


@pytest.mark.parametrize("wizard", [False, True])
@pytest.mark.parametrize("elapsed", [1.0, 1.000001, 2.0])
@pytest.mark.parametrize("phase", ["read", "refill:live1", "refill:live2"])
def test_deadline_stop_preserves_exact_answers_steps_and_persisted_rows(
    monkeypatch, wizard, elapsed, phase
):
    if wizard and phase == "refill:live1":
        phase = "read"

    async def check():
        result, rows, events, models, old = await attempt(
            monkeypatch, phase, elapsed, wizard=wizard
        )
        assert (result.state, result.needs) == (State.needs_person, "form_changed")
        assert not models
        by_key = {row.field_key: row for row in rows}
        for outcome in old:
            row = by_key[outcome.field.key]
            assert row.value == outcome.answer.value
            assert row.source == (
                AnswerSource.person if outcome.answer.source == "person" else AnswerSource.document
            )
            assert row.approved is False
        assert any(row.label == "Unknown earlier choice" for row in rows)
        assert result.report.steps == (["Next"] if wizard else [])
        assert result.report.designated == result.report.designated_ref == ""
        assert generic.SUBMIT_KEY not in by_key
        if wizard:
            assert by_key["designated:shape:0"].value == "earlier-shape"
            assert by_key["designated:step:1"].value == "Next"
        if phase == "read":
            assert not any(event.startswith("refill:") for event in events)
        elif phase == "refill:live1":
            assert "refill:live2" not in events

    asyncio.run(check())


@pytest.mark.parametrize("phase", ["read", "refill:live2", "model", "partial"])
@pytest.mark.parametrize("interruption", ["timeout", "cancel"])
def test_actual_timeout_keeps_progress_and_external_cancellation_propagates(
    monkeypatch, phase, interruption
):
    async def check():
        if interruption == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await attempt(monkeypatch, phase, elapsed=0, interruption=interruption, wizard=True)
            return
        result, rows, _, models, old = await attempt(
            monkeypatch, phase, elapsed=0, interruption=interruption, wizard=True
        )
        by_key = {row.field_key: row for row in rows}
        assert result.needs == "form_changed"
        assert [by_key[o.field.key].value for o in old] == [o.answer.value for o in old]
        assert result.report.steps == ["Next"]
        assert result.report.designated == result.report.designated_ref == ""
        assert bool(models) == (phase in {"model", "partial"})
        if phase == "partial":
            assert by_key["s2/text:extra#1"].value == "New verified progress"

    asyncio.run(check())


@pytest.mark.parametrize("phase", ["before", "tokens"])
def test_exhaustion_before_preparation_preserves_prior_evidence_without_work(monkeypatch, phase):
    async def check():
        result, rows, events, models, old = await attempt(monkeypatch, phase, wizard=True)
        assert result.needs == "form_changed"
        assert not events
        assert not models
        by_key = {row.field_key: row for row in rows}
        assert [by_key[o.field.key].value for o in old] == [o.answer.value for o in old]
        assert result.report.steps == ["Next"]
        assert result.report.designated == ""

    asyncio.run(check())


@pytest.mark.parametrize(
    "change",
    [
        {"label": "Changed"},
        {"kind": "select"},
        {"options": ("New",)},
        {"required": True},
        {"key": "s2/text:other#1"},
    ],
)
def test_changed_field_shapes_are_unresolved_and_old_values_are_not_rebound(monkeypatch, change):
    async def check():
        result, rows, _, models, _ = await attempt(monkeypatch, "read", wizard=True, change=change)
        by_key = {row.field_key: row for row in rows}
        assert by_key["text:first name#1"].value == "Exact person edit"
        current_key = change.get("key", "s2/text:summary#1")
        assert by_key[current_key].value is None
        assert by_key[current_key].source == AnswerSource.left
        assert result.report.designated == ""
        assert not models

    asyncio.run(check())


def test_live_fallback_retains_deadline_budget_and_current_refs(monkeypatch):
    async def check():
        result, rows, _, models, old = await attempt(monkeypatch, "read", elapsed=0.5, wizard=True)
        assert models == [(99.5, 100.0, 33_863, 30)]
        assert [o.field.ref for o in result.report.outcomes] == ["old1", "live2"]
        by_key = {row.field_key: row for row in rows}
        assert [by_key[o.field.key].value for o in old] == [o.answer.value for o in old]

    asyncio.run(check())


@pytest.mark.parametrize("elapsed", [1.0, 1.000001, 2.0])
def test_model_transition_at_expiry_retains_answers_and_starts_no_more_browser_work(
    monkeypatch, elapsed
):
    async def check():
        result, rows, events, models, old = await attempt(
            monkeypatch, "model", elapsed, wizard=True
        )
        assert models == [(99.0, 100.0, 33_863, 30)]
        assert events == ["read", "refill:live2", "model"]
        assert result.needs == "form_changed"
        assert result.report.designated == result.report.designated_ref == ""
        by_key = {row.field_key: row for row in rows}
        assert [by_key[o.field.key].value for o in old] == [o.answer.value for o in old]

    asyncio.run(check())


def test_changed_live_read_before_timeout_retains_closed_person_but_drops_current_answer(
    monkeypatch,
):
    async def check():
        result, rows, _, _, _ = await attempt(
            monkeypatch, "changed_model", elapsed=0, interruption="timeout", wizard=True
        )
        by_key = {row.field_key: row for row in rows}
        assert by_key["text:first name#1"].value == "Exact person edit"
        current = by_key["s2/text:summary#1"]
        assert (current.label, current.required, current.value, current.source) == (
            "Changed live question",
            True,
            None,
            AnswerSource.left,
        )
        assert result.report.outcomes[-1].field.ref == "new2"
        assert result.report.designated == ""

    asyncio.run(check())


@pytest.mark.parametrize(
    "phase", ["before", "tokens", "no_agent", "read", "refill:live2", "model", "partial"]
)
def test_real_finish_replaces_rows_without_losing_closed_person_or_verified_document(
    db, person_a, monkeypatch, phase
):
    application_id = started(db, person_a)
    application = db.get(Application, application_id)
    application.state = State.filling
    result, _, _, _, old = asyncio.run(
        attempt(
            monkeypatch,
            phase,
            elapsed=0 if phase == "partial" else 1,
            interruption="timeout" if phase == "partial" else "",
            wizard=True,
        )
    )
    prior = form_agent.FormReport(outcomes=old, steps=["Next"])
    db.add_all(run._answer_rows(application, prior))
    db.commit()
    assert run.finish(db, application_id, result, AT, AT) == "needs_person"
    db.commit()
    db.expire_all()
    persisted = {
        row.field_key: row
        for row in db.scalars(
            sa.select(ApplicationAnswer).where(ApplicationAnswer.application_id == application_id)
        )
    }
    assert persisted["text:first name#1"].value == "Exact person edit"
    assert persisted["text:first name#1"].source is AnswerSource.person
    assert persisted["s2/text:summary#1"].value == "Exact document prose"
    assert persisted["s2/text:summary#1"].source is AnswerSource.document
    assert persisted["s2/text:summary#1"].document_letter is True
    assert persisted["designated:step:1"].value == "Next"
    assert generic.SUBMIT_KEY not in persisted
    assert all(not row.approved for row in persisted.values())
    if phase == "partial":
        assert persisted["s2/text:extra#1"].value == "New verified progress"
    assert db.get(Application, application_id).state is State.needs_person


def test_real_finish_does_not_rebind_a_person_answer_to_a_changed_required_field(
    db, person_a, monkeypatch
):
    application_id = started(db, person_a)
    application = db.get(Application, application_id)
    application.state = State.filling
    result, _, _, _, old = asyncio.run(attempt(monkeypatch, "read", change={"required": True}))
    db.add_all(run._answer_rows(application, form_agent.FormReport(outcomes=old)))
    db.commit()
    assert run.finish(db, application_id, result, AT, AT) == "needs_person"
    db.commit()
    db.expire_all()
    changed = db.scalar(
        sa.select(ApplicationAnswer).where(
            ApplicationAnswer.application_id == application_id,
            ApplicationAnswer.field_key == "text:first name#1",
        )
    )
    assert changed.required is True
    assert changed.source is AnswerSource.left
    assert changed.value is None
    assert changed.approved is False
