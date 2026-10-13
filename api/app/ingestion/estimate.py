"""The token estimate of an ingestion run and its ceiling. Pure, and every figure can be checked
by hand (tasks/ingestion/todo.md, "Tokens: the estimate and the ceiling").

Characters divided by 4 understates twice: French prose runs nearer 3 characters per token, and
a step sends its growing context again at every turn. So a step is counted turn by turn.

- An extraction step over a part of `c` characters, in `n` turns:
  `input(n) = n*F + T*(2n+1-R)/2`, with `T = ceil(c/3)` and `R = ceil(c/30,000)` reads.
  Low: `n = R + 2`. High: `n = min(40, 2R + 8)`.
- A transcription step of `k` images, in `n` turns: `input(n) = n*F + 1,600*k*(n+1-k)`.
  Low: `n = 2k + 1`. High: `n = min(40, 3k + 4)`. The formula keeps each image in context one
  turn longer than it stays (the exact sum is `k*(n-k)`): kept as written, on the safe side.
- A step writes 1,500 tokens at the low end and 8,000 at the high end (assumption A13). A13
  describes an extraction step. The output of a transcription step is the transcriptions
  themselves, up to 20,000 characters per image and eight images, so tens of thousands of
  tokens, and inside any step the output is only counted when the step's result arrives. The
  figures are kept as they are until Checkpoint G replaces A13 by a measured one.

`L` and `H` are the sums over the steps that are not reused, input plus output, computed exactly
(halves included) and each rounded up once, at the end. The ceiling is `C = 2 * H` (decision
10): a run pauses there and goes on only when the user says so. A reused step counts zero.

The backstop of one step, `ceil(1.25 * input(40)) + 100,000`, is not the user's protection (the
ceiling is): it stops a single step whose context grows faster than the formula assumes.
`budget(step, remaining)` is what the engine writes as the step's `token_budget`: a whole number
above zero, or None, and then the step is not created and the run pauses.

Every number is an estimate. Nothing here is a bill, and nothing here reads a document.
"""

import functools
import math
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction

from app.ingestion import plan
from app.ingestion.models import TEXT_MAX_CHARS

F = 7_000  # tokens of fixed context per turn (A11)
CHARS_PER_TOKEN = 3  # A12
IMAGE_TOKENS = 1_600  # one page image (G0)
READ_CHARS = 30_000  # one read of the document tool
OUTPUT_LOW = 1_500  # A13
OUTPUT_HIGH = 8_000  # A13
TURNS_MAX = 40
BACKSTOP_FACTOR = Fraction(5, 4)
BACKSTOP_MARGIN = 100_000


def _ceil_div(value: int, by: int) -> int:
    return -(-value // by)


def text_tokens(characters: int) -> int:
    """`T`: the tokens of a part."""
    return _ceil_div(characters, CHARS_PER_TOKEN)


def reads(characters: int) -> int:
    """`R`: the reads a part takes."""
    return _ceil_div(characters, READ_CHARS)


def extraction_input(characters: int, turns: int) -> Fraction:
    """Every turn pays `F`; the chunk read at turn `i` is sent again at each later turn."""
    return Fraction(turns * F) + Fraction(
        text_tokens(characters) * (2 * turns + 1 - reads(characters)), 2
    )


def transcription_input(images: int, turns: int) -> Fraction:
    return Fraction(turns * F + IMAGE_TOKENS * images * (turns + 1 - images))


@dataclass(frozen=True)
class StepEstimate:
    """One step that calls the model. `fresh` is the input seen for the first time (the fixed
    context once, and the text or the images once): what a low cost pays as a cache write."""

    low_input: Fraction
    high_input: Fraction
    fresh: int
    backstop: int

    @property
    def low(self) -> Fraction:
        return self.low_input + OUTPUT_LOW

    @property
    def high(self) -> Fraction:
        return self.high_input + OUTPUT_HIGH


def _backstop(input_at_most: Fraction) -> int:
    return math.ceil(BACKSTOP_FACTOR * input_at_most) + BACKSTOP_MARGIN


def extraction(characters: int) -> StepEstimate:
    if characters < 1:
        raise ValueError("an extraction step reads at least one character")
    r = reads(characters)
    return StepEstimate(
        low_input=extraction_input(characters, r + 2),
        high_input=extraction_input(characters, min(TURNS_MAX, 2 * r + 8)),
        fresh=F + text_tokens(characters),
        backstop=_backstop(extraction_input(characters, TURNS_MAX)),
    )


def transcription(images: int) -> StepEstimate:
    if not 1 <= images <= plan.GROUP_MAX_IMAGES:
        raise ValueError(f"a transcription step reads 1 to {plan.GROUP_MAX_IMAGES} images")
    return StepEstimate(
        low_input=transcription_input(images, 2 * images + 1),
        high_input=transcription_input(images, min(TURNS_MAX, 3 * images + 4)),
        fresh=F + IMAGE_TOKENS * images,
        backstop=_backstop(transcription_input(images, TURNS_MAX)),
    )


def of_step(step: plan.Step) -> StepEstimate:
    """What the step costs when it calls the model (a reused one does not: `run` skips it)."""
    if step.kind is plan.StepKind.transcribe:
        return transcription(len(step.images))
    return extraction(step.characters)


@dataclass(frozen=True)
class RunEstimate:
    """`low`, `high` and `ceiling` are `L`, `H` and `C`, whole tokens. The exact sums beside
    them are what pricing.py prices."""

    steps: int
    reused: int
    low: int
    high: int
    ceiling: int
    low_input: Fraction
    low_fresh: int
    low_output: int
    high_input: Fraction
    high_output: int


def run(steps: Iterable[plan.Step]) -> RunEstimate:
    """The range and the ceiling of a run over these steps."""
    count = reused = fresh = calls = 0
    low_input = high_input = Fraction(0)
    for step in steps:
        count += 1
        if step.reused:
            reused += 1
            continue
        one = of_step(step)
        calls += 1
        fresh += one.fresh
        low_input += one.low_input
        high_input += one.high_input
    low = math.ceil(low_input + calls * OUTPUT_LOW)
    high = math.ceil(high_input + calls * OUTPUT_HIGH)
    return RunEstimate(
        steps=count,
        reused=reused,
        low=low,
        high=high,
        ceiling=2 * high,
        low_input=low_input,
        low_fresh=fresh,
        low_output=calls * OUTPUT_LOW,
        high_input=high_input,
        high_output=calls * OUTPUT_HIGH,
    )


def budget(step: plan.Step, remaining: float | Fraction) -> int | None:
    """The `token_budget` of a step that is about to call the model: the smaller of its backstop
    and of what remains under the ceiling (`C` minus the tokens spent), as an `int` above zero.

    None means "do not create the step": what remains is less than the step's low estimate, so
    it would start, be interrupted at its first call and be paid twice after Continue. The
    engine then pauses the run on `token_ceiling`. A remainder that a division left fractional
    is rounded down first; the runner reads anything but an `int` above zero as "no call".

    A reused step calls no model, so it has no budget and "pause" would be as wrong an answer as
    a number: asking is a mistake of the caller, and raises `ValueError`. The engine marks a
    reused step done without asking."""
    if step.reused:
        raise ValueError("a reused step calls no model and has no budget")
    if isinstance(remaining, float) and not math.isfinite(remaining):
        whole = None if remaining > 0 else 0  # inf: only the backstop bounds; nan and -inf: stop
    else:
        whole = math.floor(remaining)
    one = of_step(step)
    if whole is not None and whole < math.ceil(one.low):
        return None
    return one.backstop if whole is None else min(one.backstop, whole)


@dataclass(frozen=True)
class Maximum:
    documents: tuple[plan.Document, ...]
    estimate: RunEstimate


def _document_high(characters: int, images: int) -> tuple[Fraction, int]:
    """The high estimate and the number of steps of one document counted without its text."""
    total = characters + plan.IMAGE_TEXT_CHARS * images
    parts = plan.equal_parts(total)
    full, last = divmod(images, plan.GROUP_MAX_IMAGES)
    group_sizes = [plan.GROUP_MAX_IMAGES] * full + ([last] if last else [])
    high = sum((extraction(size).high for size in parts), Fraction(0))
    high += sum((transcription(size).high for size in group_sizes), Fraction(0))
    return high, len(parts) + len(group_sizes)


def _twice(high: Fraction, steps: int) -> tuple[int, int]:
    return int(2 * high), steps


def _image_documents() -> list[list[list[tuple[int, tuple[int, ...]] | None]]]:
    """`best[d][s][i]`: the largest high estimate of exactly `d` documents that hold images and
    no text, within `s` steps and `i` images, with the image counts that reach it. The estimates
    are doubled here, which makes them whole numbers (the formulas only ever leave a half)."""
    worth = [_twice(*_document_high(0, images)) for images in range(plan.RUN_MAX_IMAGES + 1)]
    steps, images = plan.RUN_MAX_STEPS + 1, plan.RUN_MAX_IMAGES + 1
    zero: tuple[int, tuple[int, ...]] = (0, ())
    best: list[list[list[tuple[int, tuple[int, ...]] | None]]] = [
        [[zero] * images for _ in range(steps)]
    ]
    for count in range(1, plan.RUN_MAX_DOCUMENTS + 1):
        before = best[-1]
        layer: list[list[tuple[int, tuple[int, ...]] | None]] = []
        for s in range(steps):
            row: list[tuple[int, tuple[int, ...]] | None] = [None] * images
            for i in range(count, images):
                found = None
                for own in range(1, i - count + 2):
                    high, used = worth[own]
                    if used > s:
                        break  # more images never take fewer steps
                    rest = before[s - used][i - own]
                    if rest is not None and (found is None or high + rest[0] > found[0]):
                        found = (high + rest[0], (*rest[1], own))
                row[i] = found
            layer.append(row)
        best.append(layer)
    return best


@functools.cache
def maximum_under_limits() -> Maximum:
    """The largest ceiling this search finds among the selections the limits of a run allow,
    with transcription on: no document quotes a maximum, this function computes one.

    The fixed context makes a small document dearer per character than a large one, so the
    largest selection is not the fullest. The search covers this family, which holds the cases
    the reviews computed by hand: `p` full parts of PART_MAX_CHARS characters packed into
    documents of the largest size a document may have; documents that hold only images, every
    way of sharing the images among them (a table over documents, steps and images); and
    documents of one character filling what is left of the documents and the steps. A document
    that holds both a long text and images is outside the family, so the figure is the largest
    of the family, which the test pins, not a proof over every selection.

    The winner is planned and estimated by the same code as any selection: the figure returned
    is the estimate of a selection that `plan.build` accepts whole."""
    per_document = TEXT_MAX_CHARS // plan.PART_MAX_CHARS
    best_images = _image_documents()
    tiny_high, _ = _twice(*_document_high(1, 0))
    # Thirty documents of one character are always in the family, so there is a start.
    top: tuple[int, list[int], tuple[int, ...], int] = (-1, [], (), 0)
    for parts in range(plan.RUN_MAX_CHARS // plan.PART_MAX_CHARS + 1):
        big = _ceil_div(parts, per_document)
        for image_documents in range(plan.RUN_MAX_DOCUMENTS - big + 1):
            for image_steps in range(plan.RUN_MAX_STEPS - parts + 1):
                images = best_images[image_documents][image_steps][plan.RUN_MAX_IMAGES]
                if images is None:
                    continue
                tiny = min(
                    plan.RUN_MAX_DOCUMENTS - big - image_documents,
                    plan.RUN_MAX_STEPS - parts - image_steps,
                )
                sizes = [
                    min(per_document, parts - at) * plan.PART_MAX_CHARS
                    for at in range(0, parts, per_document)
                ]
                over = sum(sizes) + tiny - plan.RUN_MAX_CHARS
                if over > 0:
                    sizes[-1] -= over  # the characters of the small documents come off a large one
                high = sum(_twice(*_document_high(size, 0))[0] for size in sizes)
                high += images[0] + tiny * tiny_high
                if high > top[0]:
                    top = (high, sizes, images[1], tiny)
    _, sizes, image_counts, tiny = top
    documents: list[plan.Document] = []

    def add(characters: int, images: int) -> None:
        number = len(documents) + 1
        documents.append(
            plan.Document(
                id=uuid.UUID(int=number),
                sha256=f"{number:064x}",
                characters=characters,
                images=tuple(range(images)),
            )
        )

    for size in sizes:
        add(size, 0)
    for count in image_counts:
        add(0, count)
    for _ in range(tiny):
        add(1, 0)
    planned = plan.build(documents, model=plan.MODEL, transcription=True)
    if planned.refused:  # pragma: no cover - the family respects every limit by construction
        raise AssertionError("the largest selection of the search is not planned whole")
    return Maximum(documents=tuple(documents), estimate=run(planned.steps))
