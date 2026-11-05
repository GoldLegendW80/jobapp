"""The owner's pacing of One click Apply's agent (A19, 2026-11-05: "you can add a random pacing to
feel more like human like between 3 to 8 seconds."), on a virtual clock: between two acts on the
page at least a pause drawn in `applier.PACE_S`, counted from the end of the previous act (its
snapshot returned), so a long model turn is not padded further; reads are not paced."""

from __future__ import annotations

import asyncio
from typing import Any

from app.apply import applier


class Tools:
    def __init__(self, clock: list[float], took: float) -> None:
        self.clock, self.took, self.started, self.ended = clock, took, [], []

    async def call(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        if name == "browser_snapshot":
            self.clock[0] += 0.5  # the snapshot after an act takes its own time
            return {"ok": True, "url": "https://jobs.example.org/a", "snapshot": "- text"}
        if name in ("browser_wait_for", "browser_take_screenshot"):
            return {"ok": True, "data": "aGk="}  # reads take no time on this clock
        self.started.append((name, self.clock[0]))
        self.clock[0] += self.took
        return {"ok": True}


def session(draws: list[float], took: float = 1.0) -> tuple[applier.ApplySession, Tools, list]:
    clock = [0.0]
    tools = Tools(clock, took)
    drawn: list[tuple[float, float]] = []

    def draw(low: float, high: float) -> float:
        drawn.append((low, high))
        return draws.pop(0)

    async def sleep(seconds: float) -> None:
        clock[0] += seconds

    async def no(*_: Any) -> Any:
        return None

    made = applier.ApplySession(
        tools=tools, capability="cap", stop=no, arm=no, monotonic=lambda: clock[0],
        sleep=sleep, draw=draw,
    )  # fmt: skip
    return made, tools, clock


def test_every_pair_of_acts_is_a_draw_apart_from_the_end_of_the_first() -> None:
    made, tools, clock = session([3.0, 8.0, 5.5])

    async def go() -> list[float]:
        ends = []
        for name, args in (
            ("browser_navigate", {"url": "https://jobs.example.org/a"}),
            ("browser_click", {"ref": "e1"}),
            ("browser_type", {"ref": "e2", "text": "Alex"}),
            ("browser_check", {"ref": "e3"}),
        ):
            await (made.navigate(args["url"]) if name == "browser_navigate"
                   else made.interact(name, args))  # fmt: skip
            ends.append(clock[0])
        return ends

    ends = asyncio.run(go())
    starts = [at for _, at in tools.started]
    # Both ends of the range and one inside, each counted from the previous act's snapshot.
    assert [round(starts[i + 1] - ends[i], 6) for i in range(3)] == [3.0, 8.0, 5.5]
    assert applier.PACE_S == (3.0, 8.0)


def test_a_long_model_turn_is_not_padded_and_reads_are_not_paced() -> None:
    made, tools, clock = session([8.0, 8.0])

    async def go() -> None:
        await made.interact("browser_click", {"ref": "e1"})
        end = clock[0]
        clock[0] += 20.0  # the model thought for 20 s
        await made.snapshot()
        await made.wait_for("Apply", None)
        await made.screenshot()
        assert clock[0] == end + 20.5  # reads ran at once
        await made.interact("browser_click", {"ref": "e2"})

    asyncio.run(go())
    starts = [at for _, at in tools.started]
    assert starts[-1] == 1.0 + 0.5 + 20.0 + 0.5  # no sleep: the turn was longer than the draw


def test_a_hover_and_a_tab_list_are_not_paced() -> None:
    made, tools, _ = session([8.0])

    async def go() -> None:
        await made.interact("browser_click", {"ref": "e1"})
        await made.interact("browser_hover", {"ref": "e2"})
        await made.interact("browser_tabs", {"action": "list"})

    asyncio.run(go())
    assert [round(at, 6) for _, at in tools.started] == [0.0, 1.5, 3.0]
