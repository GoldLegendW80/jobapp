"""The one page fit (task D3 of the documents plan; spec, "The one page fit, per template").

`fit(steps, build) -> Fitted` keeps the loosest step that holds one A4 page. `steps` are a
template's density steps, loosest first; `build(step)` returns the HTML of the document at that
step; each HTML goes through the renderer (render_client.render).

Fit grows with tightness, so the search is binary: the loosest step first (a document that fits
there costs one render), then halves of the rest. With eight steps, a document that fits only at
the tightest costs four renders, and one that never fits costs the same four and keeps the
tightest step's PDF (`fit` False: an overflow, never a refusal; the caller decides what to do).

The linear walk is the fallback: when two renders contradict the monotonic property (a tighter
step with more pages than a looser one), the binary search cannot be trusted, and the steps are
tried loosest first until one fits, reusing every render already made.

A RenderError of any step ends the fit with that error.
"""

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from itertools import pairwise

from app.documents import render_client
from app.documents.render_client import Rendered

logger = logging.getLogger("app.documents.fit")


@dataclass(frozen=True)
class Fitted:
    step: int  # the index of the step in `steps`
    pages: int
    pdf: bytes
    fit: bool  # one page; False is an overflow at the tightest step
    renders: int  # renders this fit made


async def fit[Step](
    steps: Sequence[Step],
    build: Callable[[Step], str],
    *,
    render: Callable[[str], Awaitable[Rendered]] | None = None,
) -> Fitted:
    if not steps:
        raise ValueError("a template has at least one step")
    render = render or render_client.render
    seen: dict[int, Rendered] = {}
    started = time.monotonic()

    async def at(index: int) -> Rendered:
        if index not in seen:
            seen[index] = await render(build(steps[index]))
        return seen[index]

    def monotonic() -> bool:
        pages = [seen[index].pages for index in sorted(seen)]
        return all(looser >= tighter for looser, tighter in pairwise(pages))

    def done(index: int, walked: bool) -> Fitted:
        result = Fitted(
            step=index,
            pages=seen[index].pages,
            pdf=seen[index].pdf,
            fit=seen[index].pages == 1,
            renders=len(seen),
        )
        logger.info(
            "documents.fit.done",
            extra={
                "steps": len(steps),
                "step": index,
                "pages": result.pages,
                "fit": result.fit,
                "renders": result.renders,
                "walked": walked,
                "ms": int((time.monotonic() - started) * 1000),
            },
        )
        return result

    last = len(steps) - 1
    if (await at(0)).pages == 1:
        return done(0, walked=False)
    low, high, best = 1, last, None
    while low <= high:
        middle = (low + high) // 2
        rendered = await at(middle)
        if not monotonic():
            break
        if rendered.pages == 1:
            best, high = middle, middle - 1
        else:
            low = middle + 1
    else:
        return done(best if best is not None else last, walked=False)
    for index in range(len(steps)):
        if (await at(index)).pages == 1:
            return done(index, walked=True)
    return done(last, walked=True)
