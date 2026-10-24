"""Fallback preparation and model work share the original attempt's deadline."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.apply import form_agent, run
from app.apply.answers import Answer
from app.apply.ats import base, generic
from app.apply.models import State
from tests.apply.a8b_support import workable
from tests.apply.test_a8_known_fallback import plan


@pytest.mark.parametrize("phase", ["read", "refill"])
@pytest.mark.parametrize("elapsed", [0, 0.5, 0.999, 1, 1.01, 2])
def test_preparation_cannot_start_form_work_at_or_after_the_original_deadline(
    monkeypatch: pytest.MonkeyPatch, phase: str, elapsed: float
) -> None:
    asyncio.run(_preparation(monkeypatch, phase, elapsed))


async def _preparation(monkeypatch, phase, elapsed):
    now, started, in_timeout = [99.0], [], []
    field = base.Field("text:first name#1", "First name", "text", ref="e1")
    previous = base.Report(
        outcomes=[base.Outcome(field, Answer("Ada", "identity.first_name"), base.Filled("filled"))]
    )
    actual_timeout = asyncio.timeout

    @asynccontextmanager
    async def bounded(delay):
        async with actual_timeout(delay):
            in_timeout.append(True)
            try:
                yield
            finally:
                in_timeout.pop()

    class Delayed(generic.Generic):
        async def read(self, tools):
            assert in_timeout, "preparatory read is outside the attempt timeout"
            if phase == "read":
                now[0] += elapsed
            return base.Discovery([field], [], [], []), ""

        async def fill(self, tools, item, answer):
            assert in_timeout, "prior-progress refill is outside the attempt timeout"
            if phase == "refill":
                now[0] += elapsed
            return base.Filled("filled")

    async def fill(agent, session):
        assert in_timeout
        started.append((session.clock(), session.deadline, session.token_budget))
        assert session.outcomes[field.key].answer.value == "Ada"
        return form_agent.FormReport(stopped="needs_person", needs_person="unknown"), {}

    async def shot(*args):
        return None

    monkeypatch.setattr(run.asyncio, "timeout", bounded)
    monkeypatch.setattr(run.forms, "fill", fill)
    monkeypatch.setattr(run.shots, "take", shot)
    current = plan()
    current.form_deadline = 100.0
    services = SimpleNamespace(
        clock=lambda: datetime.fromtimestamp(now[0], UTC), form_agent=object(), stub_base=None
    )
    cost = {"input_tokens": 100, "output_tokens": 10}
    adapter = Delayed()
    adapter.shapes = {}
    result = await run._drive_generic(
        services,
        current,
        adapter,
        workable(),
        strategy={"vendor": "ashby"},
        previous_cost=cost,
        previous_report=previous,
    )
    assert started == ([(99.0 + elapsed, 100.0, 33_890)] if elapsed < 1 else [])
    assert current.form_deadline == 100.0
    if elapsed >= 1:
        assert (result.state, result.needs, result.cost) == (
            State.needs_person,
            "form_changed",
            cost,
        )


@pytest.mark.parametrize("phase", ["read", "refill", "form"])
@pytest.mark.parametrize("failure", [TimeoutError, asyncio.CancelledError])
def test_preparation_timeout_stops_but_external_cancellation_propagates(
    monkeypatch: pytest.MonkeyPatch, phase: str, failure: type[BaseException]
) -> None:
    async def check():
        field = base.Field("text:first name#1", "First name", "text", ref="e1")
        previous = base.Report(
            outcomes=[
                base.Outcome(field, Answer("Ada", "identity.first_name"), base.Filled("filled"))
            ]
        )
        completed = []

        class Interrupted(generic.Generic):
            async def read(self, tools):
                if phase == "read":
                    raise failure()
                return base.Discovery([field], [], [], []), ""

            async def fill(self, tools, item, answer):
                if phase == "refill":
                    raise failure()
                return base.Filled("filled")

        async def fill(agent, session):
            completed.append("form")
            raise failure()

        monkeypatch.setattr(run.forms, "fill", fill)
        current = plan()
        current.form_deadline = 100.0
        services = SimpleNamespace(
            clock=lambda: datetime.fromtimestamp(99, UTC), form_agent=object()
        )
        call = run._drive_generic(
            services,
            current,
            Interrupted(),
            workable(),
            strategy={"vendor": "ashby"},
            previous_report=previous,
        )
        if failure is asyncio.CancelledError:
            with pytest.raises(asyncio.CancelledError):
                await call
        else:
            result = await call
            assert (result.state, result.needs) == (State.needs_person, "form_changed")
        assert completed == (["form"] if phase == "form" else [])

    asyncio.run(check())
