"""Fakes for the gate and the submit lease of A6: a lease store that hands out the gate's
capability (browser B5), a form that answers the gate's click as the fake ATS's variants do
(confirmed, slow, silent, refused with errors), and the api's queue with a Redis flag. Every
call is recorded; nothing here reaches a browser, a model or a company."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.browser.client import BrowserError
from tests.apply.a5_support import FakeLeases, Lease, RunForm, Waiting, run_ashby

CONFIRMED = "Thank you for applying to Example Robotics"
CAPABILITY = "gate-capability-" + "c" * 16


@dataclass
class Acquired:
    lease: Lease | None = None
    waiting: Waiting | None = None
    token: str | None = None
    capability: str | None = None


@dataclass
class GateLeases(FakeLeases):
    """B3's store as the submit lease sees it: `apply_submit` comes with the gate's capability."""

    capability: str | None = CAPABILITY

    async def acquire(self, user_id: str, purpose: str, automation: bool, **rest: Any) -> Any:
        got = await super().acquire(user_id, purpose, automation, **rest)
        return Acquired(got.lease, got.waiting, got.token, self.capability)


class SubmitForm(RunForm):
    """A fresh form for the submit lease. `answer` is what the site does after the gate's click:
    `confirm` (a confirmation page), `slow` (the confirmation after `slow_reads` more reads),
    `silent` (nothing changes), `errors` (the first required field shows an error)."""

    def __init__(self, *args: Any, answer: str = "confirm", slow_reads: int = 5, **kw: Any):
        super().__init__(*args, **kw)
        self.answer = answer
        self.slow_reads = slow_reads
        self.gate_clicks: list[dict[str, str]] = []
        self.crash_on_click = False
        self._after: int | None = None

    async def gate_click(self, ref: str, capability: str, element: str = "") -> dict[str, Any]:
        self._log("gate_click", ref=ref)
        target = self._target(ref)
        if target is None:
            return {"ok": False, "error": "stale_ref"}
        self.gate_clicks.append({"what": target[0], "capability": capability})
        if self.crash_on_click:
            raise BrowserError("ended")
        if target[0] != "submit":
            return {"ok": True}
        if self.answer == "confirm":
            self._other = CONFIRMED
        elif self.answer == "slow":
            self._after = self.slow_reads
        elif self.answer == "errors":
            next(s for s in self.fields if s.required).error = "This field is required."
        return {"ok": True}

    async def snapshot(self) -> dict[str, Any]:
        if self._after is not None:
            self._after -= 1
            if self._after <= 0:
                self._other, self._after = CONFIRMED, None
        return await super().snapshot()


def submit_ashby(**rest: Any) -> SubmitForm:
    form = run_ashby()
    return SubmitForm(form.fields, submit=form.submit, title=form.title, **rest)


async def fake_click(tools: Any, ref: str, capability: str, element: str = "") -> dict[str, Any]:
    """`app.browser.tools.gate_click` over a SubmitForm."""
    result: dict[str, Any] = await tools.gate_click(ref, capability, element)
    return result


@dataclass
class FakeRedis:
    flags: dict[str, str] = field(default_factory=dict)
    broken: bool = False

    async def get(self, name: str) -> Any:
        if self.broken:
            raise ConnectionError("redis down")
        return self.flags.get(name)

    async def set(self, name: str, value: str) -> Any:
        self.flags[name] = value

    async def delete(self, *names: str) -> Any:
        for name in names:
            self.flags.pop(name, None)


@dataclass
class FakeQueue:
    """The api's `app.state.queue`: its pool is a FakeRedis, its enqueues recorded."""

    redis: FakeRedis = field(default_factory=FakeRedis)
    enqueued: list[tuple[str, tuple[Any, ...], str, str]] = field(default_factory=list)

    async def pool(self) -> FakeRedis:
        return self.redis

    async def enqueue(self, function: str, *args: Any, queue: str, job_id: str) -> bool:
        if any(job_id == e[3] for e in self.enqueued):
            return False
        self.enqueued.append((function, args, queue, job_id))
        return True

    async def close(self) -> None:
        return None
