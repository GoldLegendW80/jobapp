"""The one page fit (fit.py): the binary search, the linear walk, the overflow. The renderer is a
stand in that answers a page count per step, so these count renders exactly."""

import asyncio

import pytest

from app.documents import fit
from app.documents.render_client import Rendered, RenderError

STEPS = tuple(range(8))


def renderer(pages: list[int]) -> tuple[list[int], object]:
    """A render that reads the step from the HTML and answers its page count."""
    asked: list[int] = []

    async def render(html: str) -> Rendered:
        step = int(html.removeprefix("step "))
        asked.append(step)
        return Rendered(pdf=f"%PDF- step {step}".encode(), pages=pages[step])

    return asked, render


def run(pages: list[int], steps: tuple = STEPS) -> tuple[fit.Fitted, list[int]]:
    asked, render = renderer(pages)
    fitted = asyncio.run(fit.fit(steps, lambda step: f"step {step}", render=render))
    return fitted, asked


def test_a_document_that_fits_at_the_first_step_costs_one_render() -> None:
    fitted, asked = run([1] * 8)
    assert (fitted.step, fitted.pages, fitted.fit, fitted.renders) == (0, 1, True, 1)
    assert asked == [0]
    assert fitted.pdf == b"%PDF- step 0"


def test_a_document_that_fits_only_at_the_last_step_costs_four_renders() -> None:
    fitted, asked = run([2, 2, 2, 2, 2, 2, 2, 1])
    assert (fitted.step, fitted.fit, fitted.renders) == (7, True, 4)
    assert asked == [0, 4, 6, 7]


def test_a_document_that_never_fits_keeps_the_tightest_pdf() -> None:
    fitted, asked = run([3, 3, 3, 2, 2, 2, 2, 2])
    assert (fitted.step, fitted.pages, fitted.fit, fitted.renders) == (7, 2, False, 4)
    assert fitted.pdf == b"%PDF- step 7"
    assert len(asked) == len(set(asked)), "no step is rendered twice"


@pytest.mark.parametrize("first_fit", range(8))
def test_the_loosest_step_that_fits_is_kept_whichever_it_is(first_fit: int) -> None:
    pages = [2] * first_fit + [1] * (8 - first_fit)
    fitted, asked = run(pages)
    assert (fitted.step, fitted.fit) == (first_fit, True)
    assert fitted.renders <= 4
    assert sorted(asked) == sorted(set(asked))


def test_a_template_of_one_step_and_of_two_steps() -> None:
    assert run([1], steps=(0,))[0].renders == 1
    over, _ = run([2], steps=(0,))
    assert (over.step, over.fit) == (0, False)
    fitted, asked = run([2, 1], steps=(0, 1))
    assert (fitted.step, fitted.fit, asked) == (1, True, [0, 1])


def test_a_broken_monotonic_property_falls_back_to_the_linear_walk() -> None:
    # Step 2 gives more pages than step 0: the search stops trusting the halves and walks.
    fitted, asked = run([2, 2, 3, 2, 1, 1, 1, 1])
    assert (fitted.step, fitted.fit) == (4, True)
    assert asked == [0, 4, 2, 1, 3], "the walk reuses the renders already made"


def test_the_linear_walk_finds_a_fit_the_halves_would_miss() -> None:
    # Not monotonic at all: only step 3 holds one page.
    fitted, asked = run([2, 2, 2, 1, 2, 3, 3, 3])
    assert (fitted.step, fitted.fit) == (3, True)
    assert asked[:3] == [0, 4, 6]


def test_the_steps_are_handed_to_build_as_they_are() -> None:
    seen: list[object] = []

    async def render(html: str) -> Rendered:
        return Rendered(pdf=b"%PDF-", pages=1 if html == "tight" else 2)

    def build(step: dict) -> str:
        seen.append(step)
        return step["name"]

    steps = [{"name": "loose"}, {"name": "middle"}, {"name": "tight"}]
    fitted = asyncio.run(fit.fit(steps, build, render=render))
    assert (fitted.step, fitted.fit) == (2, True)
    assert seen[0] is steps[0]


def test_a_render_error_ends_the_fit_with_it() -> None:
    async def render(html: str) -> Rendered:
        raise RenderError("renderer_unavailable")

    with pytest.raises(RenderError) as raised:
        asyncio.run(fit.fit(STEPS, str, render=render))
    assert raised.value.code == "renderer_unavailable"


def test_no_step_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one step"):
        asyncio.run(fit.fit((), str, render=renderer([])[1]))
