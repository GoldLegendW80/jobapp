"""D9: the token estimate of a writer run (app/documents/estimate.py). Every figure below is
worked by hand from the formulas of the module docstring, written as literals: a change of a
formula or an assumption fails here and must say so under "Measurements" in the plan."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest

from app.documents import estimate
from app.ingestion import pricing


def test_a_writer_run_worked_by_hand() -> None:
    # F = 7,000, P = 3,000/3 = 1,000, R = 9,000/3 = 3,000, O_low = ceil(4,000/3) = 1,334,
    # O_high = ceil(8,000/3) = 2,667, n_low = 3, n_high = 10, k = 4.
    found = estimate.writer(3_000, 9_000, turns=10, submits=4)
    assert found.low_input == 3 * 8_000 + 2 * 3_000 + 1 * 1_334 == 31_334
    assert found.low_output == 1_334 + 3 * 500 == 2_834
    assert found.low == 34_168
    assert found.high_input == 10 * 8_000 + 9 * (3_000 + 4 * 2_667) == 203_012
    assert found.high_output == 4 * 2_667 + 10 * 500 == 15_668
    assert found.high == 218_680
    assert found.ceiling == 2 * 218_680
    assert found.low_fresh == 8_000 + 3_000 + 1_334


def test_an_empty_profile_still_pays_the_fixed_context() -> None:
    found = estimate.writer(0, 0, turns=10, submits=4)
    assert (found.low, found.high, found.ceiling) == (25_168, 181_680, 363_360)


@pytest.mark.parametrize("reads", [0, 1, 2, 3, 3_000, 9_001, 60_000, 400_000])
def test_more_to_read_never_costs_less(reads: int) -> None:
    smaller = estimate.writer(1_000, reads)
    larger = estimate.writer(1_000, reads + 3)
    assert smaller.low <= larger.low
    assert smaller.high <= larger.high
    assert 0 < smaller.low < smaller.high < smaller.ceiling == 2 * smaller.high


def test_fewer_turns_than_the_low_end_are_counted_as_the_low_end() -> None:
    assert estimate.writer(0, 0, turns=1, submits=1).high >= estimate.writer(0, 0).low


def test_a_posting_is_its_cv_and_its_letter() -> None:
    pair = estimate.posting(9_000, 2_000, 6_000, turns=10, submits=4)
    cv_prompt = estimate.prompt_chars("cv")
    letter_prompt = estimate.prompt_chars("letter") or cv_prompt
    cv = estimate.writer(cv_prompt, 17_000)
    letter = estimate.writer(letter_prompt, 17_000 + estimate.CONTENT_LOW_CHARS)
    assert pair.high == cv.high + letter.high
    assert pair.low == cv.low + letter.low
    assert pair.ceiling == 2 * pair.high
    assert pair.steps == 2


def test_combine_and_times() -> None:
    one = estimate.writer(100, 200)
    assert estimate.times(one, 0).high == 0
    assert estimate.times(one, 0).low == 0
    three = estimate.times(one, 3)
    assert three.high == 3 * one.high
    assert three.low_input == 3 * one.low_input
    assert isinstance(three.high_input, Fraction)


def test_as_json_holds_numbers_only() -> None:
    found = estimate.writer(3_000, 9_000, turns=10, submits=4)
    assert estimate.as_json(found) == {"low": 34_168, "high": 218_680, "ceiling": 437_360}


def test_the_estimate_is_priced_like_an_ingestion_run() -> None:
    found = estimate.writer(3_000, 9_000, turns=10, submits=4)
    costs = pricing.costs(found, "claude-sonnet-5-5")
    # high: 203,012 input at 2 dollars and 15,668 output at 10 dollars, per million.
    assert costs.high == Decimal("0.56")  # 0.406024 + 0.15668 = 0.562704
    assert costs.low < costs.high < costs.ceiling


def test_the_base_writer_prompt_is_counted() -> None:
    assert estimate.prompt_chars("base") > 1_000
    assert estimate.prompt_chars("no-such-writer") == 0


def test_the_worked_figures_of_the_plan() -> None:
    # "Measurements / D9" in tasks/documents/todo.md: change both together.
    assert (estimate.prompt_chars("base"), estimate.prompt_chars("cv")) == (2_678, 3_112)
    base = estimate.base_profile(1_396, turns=10, submits=4)
    assert (base.low, base.high, base.ceiling) == (28_779, 194_804, 389_608)
    costs = pricing.costs(base, "claude-sonnet-5-5")
    assert (costs.low, costs.high) == (Decimal("0.05"), Decimal("0.51"))
    pair = estimate.posting(1_396, 1_503, 6_000, turns=10, submits=4)
    assert estimate.prompt_chars("letter") == 3_789
    assert (pair.low, pair.high, pair.ceiling) == (71_773, 451_773, 903_546)
    costs = pricing.costs(pair, "claude-sonnet-5-5")
    assert (costs.low, costs.high) == (Decimal("0.13"), Decimal("1.15"))
