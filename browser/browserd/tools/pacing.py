"""The human pacing of SPEC-browser, in one place: its bounds are the constants below, and every
tool of browserd/tools waits through a `Pacer`, which takes its sleep and its random source from
the caller (the tests give fakes and read the delays back; no test sleeps).

- typing: TYPE_MS per character, uniform with jitter; a text of PASTE_FROM characters or more
  (a cover letter's text) is pasted in one insertion, as a person pastes it, after a dwell;
- before every action: a dwell of DWELL_S;
- before a click: the element scrolled into view and hovered, then HOVER_S;
- after a navigation: a reading pause of READ_S.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable

TYPE_MS = (60, 140)
PASTE_FROM = 200
DWELL_S = (1.0, 3.0)
HOVER_S = (0.2, 0.6)
READ_S = (1.5, 4.0)

Sleep = Callable[[float], Awaitable[None]]


class Pacer:
    def __init__(
        self, sleep: Sleep = asyncio.sleep, rng: random.Random | None = None
    ) -> None:
        self.sleep, self.rng = sleep, rng or random.SystemRandom()

    def _pick(self, bounds: tuple[float, float]) -> float:
        low, high = bounds
        return self.rng.uniform(low, high)

    def key_delays_s(self, count: int) -> list[float]:
        """One delay per character, each within TYPE_MS."""
        return [self._pick(TYPE_MS) / 1000 for _ in range(count)]

    async def dwell(self) -> float:
        seconds = self._pick(DWELL_S)
        await self.sleep(seconds)
        return seconds

    async def hover(self) -> float:
        seconds = self._pick(HOVER_S)
        await self.sleep(seconds)
        return seconds

    async def read(self) -> float:
        seconds = self._pick(READ_S)
        await self.sleep(seconds)
        return seconds
