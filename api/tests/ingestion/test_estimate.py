"""estimate.py, pure: the five rows of the arithmetic table and the two worked selections of
"Tokens", to the token; the range and the ceiling; the budget of a step; the largest ceiling the
limits allow."""

import itertools
import math
import uuid
from fractions import Fraction

import pytest

from app.ingestion import estimate, plan

OPUS = "claude-opus-5-5"


def doc(number: int, characters: int = 0, images: int = 0) -> plan.Document:
    return plan.Document(uuid.UUID(int=number), f"{number:064x}", characters, tuple(range(images)))


def of(*documents: plan.Document, **options: object) -> estimate.RunEstimate:
    planned = plan.build(documents, model=OPUS, transcription=True, **options)  # type: ignore[arg-type]
    return estimate.run(planned.steps)


def extraction_step(characters: int) -> plan.Step:
    return plan.build([doc(1, characters)], model=OPUS).steps[0]


# --- the arithmetic table


@pytest.mark.parametrize(
    ("characters", "tokens", "reads", "low", "high", "backstop"),
    [
        (30_000, 10_000, 1, Fraction(51_000), Fraction(170_000), 950_000),
        (50_000, 16_667, 2, Fraction(863_345, 10), Fraction(2_756_705, 10), 1_272_934),
        (100_000, 33_334, 4, Fraction(192_003), Fraction(595_343), 2_054_199),
    ],
)
def test_an_extraction_step_matches_the_table(
    characters: int, tokens: int, reads: int, low: Fraction, high: Fraction, backstop: int
) -> None:
    assert estimate.text_tokens(characters) == tokens
    assert estimate.reads(characters) == reads
    step = estimate.extraction(characters)
    assert (step.low_input, step.high_input, step.backstop) == (low, high, backstop)
    assert step.low == low + 1_500
    assert step.high == high + 8_000
    assert step.fresh == 7_000 + tokens


@pytest.mark.parametrize(
    ("images", "low", "high", "backstop"),
    [(8, 247_000, 464_800, 978_000), (4, 101_400, 195_200, 746_000)],
)
def test_a_transcription_step_matches_the_table(
    images: int, low: int, high: int, backstop: int
) -> None:
    step = estimate.transcription(images)
    assert (step.low_input, step.high_input, step.backstop) == (low, high, backstop)
    assert step.fresh == 7_000 + 1_600 * images


def test_a_step_reads_something() -> None:
    for call, size in ((estimate.extraction, 0), (estimate.transcription, 0)):
        with pytest.raises(ValueError, match=r"at least one|1 to 8"):
            call(size)
    with pytest.raises(ValueError, match="1 to 8 images"):
        estimate.transcription(9)


# --- the two worked selections


def test_the_typical_dossier_of_three_documents_of_50_000_characters() -> None:
    """L = 3 * (86,334.5 + 1,500) = 263,503.5, rounded up once; H = 3 * (275,670.5 + 8,000) =
    851,011.5, rounded up once, then doubled."""
    run = of(doc(1, 50_000), doc(2, 50_000), doc(3, 50_000))
    assert (run.low, run.high, run.ceiling) == (263_504, 851_012, 1_702_024)
    assert (run.steps, run.reused) == (3, 0)
    assert run.low_input == Fraction(2_590_035, 10)
    assert run.high_input == Fraction(8_270_115, 10)
    assert (run.low_fresh, run.low_output, run.high_output) == (71_001, 4_500, 24_000)


def test_the_selection_of_1_500_000_characters_and_60_images() -> None:
    """H: 15 parts of 100,000 are 15 * 603,343 = 9,050,145; 60 images are seven steps of 8 and
    one of 4, 7 * 472,800 + 203,200 = 3,512,800; their text, 180,000 characters, is two parts of
    90,000, 2 * 496,000 = 992,000. L, the same way: 15 * 193,503 + 7 * 248,500 + 102,900 +
    2 * 156,500 = 5,057,945."""
    run = of(*(doc(n, 100_000) for n in range(1, 16)), doc(16, images=60))
    assert (run.low, run.high, run.ceiling) == (5_057_945, 13_554_945, 27_109_890)
    assert run.steps == 15 + 8 + 2


# --- the range


def test_nothing_to_read_is_zero() -> None:
    run = estimate.run([])
    assert (run.steps, run.low, run.high, run.ceiling) == (0, 0, 0, 0)


def test_a_run_whose_every_step_is_reused_costs_nothing() -> None:
    documents = (doc(1, 150_000), doc(2, images=9))
    finished = {s.identity for s in plan.build(documents, model=OPUS, transcription=True).steps}
    run = of(*documents, finished=finished)
    assert (run.steps, run.reused) == (5, 5)
    assert (run.low, run.high, run.ceiling) == (0, 0, 0)
    assert run.low_fresh == run.low_output == run.high_output == 0


def test_only_the_steps_that_are_not_reused_count() -> None:
    documents = (doc(1, 50_000), doc(2, 50_000), doc(3, 50_000))
    finished = {plan.build(documents, model=OPUS).steps[0].identity}
    run = of(*documents, finished=finished)
    assert (run.steps, run.reused) == (3, 1)
    assert run.high == 2 * 283_670 + 1
    assert run.ceiling == 2 * run.high


def test_a_step_never_costs_less_for_more_text() -> None:
    sizes = [1, 2, 3, 4, 29_999, 30_000, 30_001, 59_999, 60_000, 60_001, 90_000, 90_001, 99_999]
    sizes += list(range(1_000, 100_001, 1_000))
    steps = [estimate.extraction(size) for size in sorted(sizes)]
    for smaller, larger in itertools.pairwise(steps):
        assert smaller.low <= larger.low
        assert smaller.high <= larger.high
        assert smaller.low <= smaller.high < smaller.backstop
    # The backstop is not held to this: `input(40)` loses half a `T` at each read more, so it
    # dips by a few thousand tokens where `R` steps up (950,000 at 30,000 characters, 943,800 at
    # one more). It is a technical stop far above the high estimate, as asserted above.
    assert estimate.extraction(30_001).backstop == 943_800


def test_a_document_more_never_lowers_the_estimate() -> None:
    documents = [doc(n, 7_000 * n) for n in range(1, 13)]
    runs = [of(*documents[:count]) for count in range(1, 13)]
    for fewer, more in itertools.pairwise(runs):
        assert fewer.low < more.low
        assert fewer.high < more.high
        assert fewer.ceiling < more.ceiling


def test_counted_without_its_text_one_character_past_a_full_part_is_two_halves() -> None:
    """The pure case only: a document given by its size, without the offsets of its parts (a
    text that is not there yet, or a caller of `plan.build`), is split into equal parts, and the
    formula as "Tokens" writes it counts a full part dearer than two halves. Asserted so that
    nobody takes the dip for a bug and smooths it. The route cuts the real text, where one
    character more can count higher or lower depending on where the text breaks: that case is
    `test_at_the_edge_of_a_part_the_estimate_follows_where_the_text_breaks` in
    `test_estimate_api.py`."""
    assert of(doc(1, 100_000)).high == 603_343
    assert of(doc(1, 100_001)).high == 2 * 275_670 + 1 + 2 * 8_000  # 567,341


def test_the_largest_document_is_five_full_parts() -> None:
    run = of(doc(1, 500_000))
    assert run.steps == 5
    assert run.high == 5 * 603_343
    assert run.ceiling == 10 * 603_343


def test_the_low_estimate_never_passes_the_high_one() -> None:
    for images in range(1, 9):
        step = estimate.transcription(images)
        assert step.low < step.high < step.backstop


# --- the budget of a step


def test_the_budget_is_what_remains_when_that_is_exactly_the_low_estimate() -> None:
    step = extraction_step(50_000)
    low = math.ceil(estimate.of_step(step).low)  # 87,834.5 rounded up
    assert low == 87_835
    assert estimate.budget(step, low) == low
    assert estimate.budget(step, low - 1) is None


def test_a_reused_step_has_no_budget_and_asking_is_an_error() -> None:
    """Neither a number nor None ("pause the run") is true of a step that calls no model."""
    documents = [doc(1, 50_000)]
    fresh = plan.build(documents, model=OPUS).steps[0]
    reused = plan.build(documents, model=OPUS, finished={fresh.identity}).steps[0]
    assert (fresh.reused, reused.reused) == (False, True)
    assert estimate.budget(fresh, 10_000_000) == estimate.of_step(fresh).backstop
    for remaining in (10_000_000, 0, float("inf")):
        with pytest.raises(ValueError, match="reused"):
            estimate.budget(reused, remaining)


@pytest.mark.parametrize("remaining", [1, 0, -1, -10_000_000, float("nan"), float("-inf")])
def test_no_step_is_created_when_too_little_remains(remaining: float) -> None:
    assert estimate.budget(extraction_step(50_000), remaining) is None


def test_the_backstop_bounds_the_budget_when_much_remains() -> None:
    step = extraction_step(50_000)
    assert estimate.budget(step, 10**9) == 1_272_934
    assert estimate.budget(step, float("inf")) == 1_272_934
    assert estimate.budget(step, 1_272_933) == 1_272_933


@pytest.mark.parametrize(
    ("remaining", "expected"),
    [
        (150_000.0, 150_000),
        (150_000.9, 150_000),
        (Fraction(1_702_024, 3), 567_341),
        (1_702_024 / 7, 243_146),
        (87_834.99, None),  # below the low estimate once rounded down
    ],
)
def test_a_fractional_remainder_gives_a_whole_budget(
    remaining: float | Fraction, expected: int | None
) -> None:
    budget = estimate.budget(extraction_step(50_000), remaining)
    assert budget == expected
    assert budget is None or (type(budget) is int and budget > 0)


def test_the_budget_of_a_transcription_step() -> None:
    step = plan.build([doc(1, images=8)], model=OPUS, transcription=True).steps[0]
    assert step.kind is plan.StepKind.transcribe
    assert estimate.budget(step, 248_500) == 248_500
    assert estimate.budget(step, 248_499) is None
    assert estimate.budget(step, 5_000_000) == 978_000


# --- the largest ceiling


def test_the_largest_ceiling_the_limits_allow_is_computed_not_quoted() -> None:
    largest = estimate.maximum_under_limits()
    documents, run = largest.documents, largest.estimate
    assert (run.high, run.ceiling) == (15_665_818, 31_331_636)
    # It is a selection the limits allow, planned whole by the same code as any other.
    assert len(documents) <= plan.RUN_MAX_DOCUMENTS
    assert sum(d.characters for d in documents) <= plan.RUN_MAX_CHARS
    assert sum(len(d.images) for d in documents) <= plan.RUN_MAX_IMAGES
    assert run.steps <= plan.RUN_MAX_STEPS
    assert estimate.run(plan.build(documents, model=OPUS, transcription=True).steps) == run
    # It is above the two selections the reviews computed by hand.
    worked = of(*(doc(n, 100_000) for n in range(1, 16)), doc(16, images=60))
    review = of(
        *(doc(n, 100_000 - (1 if n == 1 else 0) * 15) for n in range(1, 16)),
        *(doc(n, 1, images=4) for n in range(16, 31)),
    )
    assert worked.ceiling < run.ceiling
    assert review.ceiling < run.ceiling
