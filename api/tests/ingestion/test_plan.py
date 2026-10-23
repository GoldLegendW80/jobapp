"""plan.py, pure: the cut, the groups, the identity and the reuse rule, the limits of a run and
the 60 step rule. No database: every document here is a handful of numbers."""

import itertools
import uuid

import pytest

from app.agents.models import CredentialKind
from app.ingestion import plan
from app.ingestion.plan import Document, Identity, LimitError, Refusal, StepKind

OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"


def doc(number: int, characters: int = 0, images: int = 0, **fields: object) -> Document:
    return Document(
        id=uuid.UUID(int=number),
        sha256=f"{number:064x}",
        characters=characters,
        images=tuple(range(images)),
        **fields,  # type: ignore[arg-type]
    )


def sizes(parts: tuple[tuple[int, int], ...]) -> list[int]:
    return [end - start for start, end in parts]


# --- the cut


def test_a_text_of_exactly_one_part_is_not_cut() -> None:
    assert plan.cut("a" * 100_000) == ((0, 100_000),)


def test_one_character_more_makes_two_parts() -> None:
    assert plan.cut("a" * 100_001) == ((0, 100_000), (100_000, 100_001))


def test_an_empty_text_has_no_part() -> None:
    assert plan.cut("") == ()


def test_a_part_ends_at_the_last_paragraph_end_that_fits() -> None:
    text = "a" * 40_000 + "\n\n" + "b" * 50_000 + "\n\n" + "c" * 30_000
    parts = plan.cut(text)
    assert parts == ((0, 90_004), (90_004, 120_004))
    assert text[parts[1][0] :] == "c" * 30_000


def test_a_text_without_a_paragraph_end_is_cut_at_a_line_end() -> None:
    text = "a" * 60_000 + "\n" + "b" * 60_000
    assert plan.cut(text) == ((0, 60_001), (60_001, 120_001))


def test_a_text_with_no_break_at_all_is_cut_at_the_limit() -> None:
    assert sizes(plan.cut("a" * 250_000)) == [100_000, 100_000, 50_000]


def test_a_break_at_the_very_start_of_the_window_is_not_taken() -> None:
    # #115 N4: an empty line then 99,999 characters with no break made a part of two characters.
    text = "\n\n" + "a" * 99_999
    assert plan.cut(text) == ((0, 100_000), (100_000, 100_001))


@pytest.mark.parametrize(
    ("at", "first"),
    [
        (0, 100_000),
        (49_997, 100_000),  # the break ends at 49,999: under the floor
        (49_998, 100_000),  # ends at 50,000, its mark starts under the floor
        (
            49_999,
            50_001,
        ),  # the empty line starts under the floor, its 2nd line feed is one
        (50_000, 50_002),  # the first break past the floor is taken
        (99_998, 100_000),
    ],
)
def test_the_floor_of_a_part_is_half_the_window(at: int, first: int) -> None:
    text = "a" * at + "\n\n" + "b" * (150_000 - at)
    assert plan.cut(text)[0] == (0, first)


def test_a_line_end_under_the_floor_is_not_taken_either() -> None:
    text = "a" * 10_000 + "\n" + "b" * 120_000
    assert sizes(plan.cut(text)) == [100_000, 30_001]


def test_no_part_but_the_last_is_under_the_floor() -> None:
    text = ("x" * 30_000 + "\n\n") * 20
    parts = plan.cut(text)
    assert all(size > plan.PART_MIN_BREAK for size in sizes(parts)[:-1])
    assert parts[-1][1] == len(text)


def test_the_parts_touch_cover_the_text_and_fit() -> None:
    paragraph = "word " * 700 + "\n\n"
    text = paragraph * 130
    parts = plan.cut(text)
    assert parts[0][0] == 0
    assert parts[-1][1] == len(text)
    assert all(a[1] == b[0] for a, b in itertools.pairwise(parts))
    assert all(1 <= size <= plan.PART_MAX_CHARS for size in sizes(parts))
    assert all(text[start:end].endswith("\n\n") for start, end in parts)


def test_equal_parts_are_as_few_as_fit_and_of_equal_size() -> None:
    assert plan.equal_parts(0) == ()
    assert plan.equal_parts(100_000) == (100_000,)
    assert plan.equal_parts(100_001) == (50_001, 50_000)
    assert plan.equal_parts(180_000) == (90_000, 90_000)
    assert sum(plan.equal_parts(499_984)) == 499_984


def test_images_go_in_groups_of_at_most_eight_in_order() -> None:
    assert plan.groups((2, 0, 1)) == ((0, 1, 2),)
    assert plan.groups(range(9)) == (tuple(range(8)), (8,))
    assert plan.groups(()) == ()


# --- the model


def test_the_model_follows_the_kind_of_credential() -> None:
    assert plan.model_for(None) == OPUS
    assert plan.model_for(CredentialKind.claude_subscription) == OPUS
    assert plan.model_for("anthropic_api_key") == SONNET
    assert plan.models() == {OPUS, SONNET}
    with pytest.raises(ValueError, match="a_kind_that_does_not_exist"):
        plan.model_for("a_kind_that_does_not_exist")


# --- the steps and their identity


def test_a_text_document_is_one_extraction_step_per_part() -> None:
    planned = plan.build([doc(1, 30_000), doc(2, 250_000)], model=OPUS)
    assert [(step.kind, step.part, step.characters) for step in planned.steps] == [
        (StepKind.extract, 0, 30_000),
        (StepKind.extract, 0, 83_334),
        (StepKind.extract, 1, 83_333),
        (StepKind.extract, 2, 83_333),
    ]
    assert planned.refused == ()
    assert planned.planned_documents == (uuid.UUID(int=1), uuid.UUID(int=2))
    assert planned.steps[0].identity == Identity(f"{1:064x}", "extract", (0,), 1, OPUS)


def test_the_parts_of_a_cut_text_keep_their_offsets() -> None:
    cuts = ((0, 90_004), (90_004, 120_004))
    planned = plan.build([doc(1, 120_004, parts=cuts)], model=OPUS)
    assert [(s.start, s.end, s.characters, s.predicted) for s in planned.steps] == [
        (0, 90_004, 90_004, False),
        (90_004, 120_004, 30_000, False),
    ]


def test_a_document_with_images_is_refused_by_name_while_transcription_is_off() -> None:
    assert plan.TRANSCRIPTION is True  # since G14; a caller can still plan without it
    planned = plan.build([doc(1, 1_000), doc(2, images=3)], model=OPUS, transcription=False)
    assert planned.refused == ((uuid.UUID(int=2), Refusal.awaiting_transcription),)
    assert planned.planned_documents == (uuid.UUID(int=1),)


def test_transcription_steps_come_first_then_the_extractions() -> None:
    planned = plan.build(
        [doc(1, 1_000), doc(2, images=3), doc(3, images=9)], model=OPUS, transcription=True
    )
    assert [(step.kind, step.document_id.int, len(step.images)) for step in planned.steps] == [
        (StepKind.transcribe, 2, 3),
        (StepKind.transcribe, 3, 8),
        (StepKind.transcribe, 3, 1),
        (StepKind.extract, 1, 0),
        (StepKind.extract, 2, 0),
        (StepKind.extract, 3, 0),
    ]
    second = planned.steps[2]
    assert second.identity == Identity(f"{3:064x}", "transcribe", (8,), 1, OPUS)
    # An image is counted at 3,000 characters for its later extraction (A10).
    assert [(s.characters, s.predicted) for s in planned.steps[3:]] == [
        (1_000, False),
        (9_000, True),
        (27_000, True),
    ]


def test_a_document_with_neither_text_nor_images_is_refused_as_empty() -> None:
    planned = plan.build([doc(1), doc(2, 5)], model=OPUS)
    assert planned.refused == ((uuid.UUID(int=1), Refusal.empty),)


def test_a_step_whose_identity_is_finished_is_reused() -> None:
    documents = [doc(1, 150_000), doc(2, images=9)]
    fresh = plan.build(documents, model=OPUS, transcription=True)
    assert not any(step.reused for step in fresh.steps)
    finished = {step.identity for step in fresh.steps}
    again = plan.build(documents, model=OPUS, finished=finished, transcription=True)
    assert all(step.reused for step in again.steps)


def test_reading_a_document_again_reuses_nothing_of_it() -> None:
    documents = [doc(1, 150_000), doc(2, 10)]
    finished = {step.identity for step in plan.build(documents, model=OPUS).steps}
    planned = plan.build(documents, model=OPUS, finished=finished, reread={uuid.UUID(int=1)})
    assert [(step.document_id.int, step.reused) for step in planned.steps] == [
        (1, False),
        (1, False),
        (2, True),
    ]


def test_the_model_and_the_extractor_version_are_part_of_the_identity() -> None:
    documents = [doc(1, 10)]
    finished = {step.identity for step in plan.build(documents, model=OPUS).steps}
    assert not plan.build(documents, model=SONNET, finished=finished).steps[0].reused
    newer = plan.build(documents, model=OPUS, finished=finished, extractor_version=2)
    assert not newer.steps[0].reused
    other_file = [Document(uuid.UUID(int=1), "ab" * 32, 10)]
    assert not plan.build(other_file, model=OPUS, finished=finished).steps[0].reused


# --- the limits


def test_no_document_is_refused() -> None:
    with pytest.raises(LimitError) as refused:
        plan.build([], model=OPUS)
    assert refused.value.code == "no_document"


def test_thirty_documents_pass_and_thirty_one_are_refused() -> None:
    assert len(plan.build([doc(n, 10) for n in range(1, 31)], model=OPUS).steps) == 30
    with pytest.raises(LimitError) as refused:
        plan.build([doc(n, 10) for n in range(1, 32)], model=OPUS)
    assert refused.value.code == "documents"
    assert "31" in refused.value.message
    assert "30" in refused.value.message


def test_more_than_the_characters_of_a_run_is_refused() -> None:
    full = [doc(n, 500_000) for n in range(1, 4)]
    assert len(plan.build(full, model=OPUS).steps) == 15
    with pytest.raises(LimitError) as refused:
        plan.build([*full, doc(4, 1)], model=OPUS)
    assert refused.value.code == "characters"
    assert "1,500,001" in refused.value.message


def test_sixty_one_images_are_refused_whether_or_not_they_would_be_read() -> None:
    documents = [doc(1, images=30), doc(2, images=30)]
    assert len(plan.build(documents, model=OPUS, transcription=True).steps) == 10
    for transcription in (True, False):
        with pytest.raises(LimitError) as refused:
            plan.build([*documents, doc(3, images=1)], model=OPUS, transcription=transcription)
        assert refused.value.code == "images"


def test_the_document_that_would_make_a_sixty_first_step_is_left_out() -> None:
    # 28 documents of two parts are 56 steps; the next one of five parts does not fit, a later
    # one of four parts does, and nothing fits after the sixtieth step.
    documents = [doc(n, 40_000, parts=((0, 20_000), (20_000, 40_000))) for n in range(1, 29)]
    five = tuple((at, at + 10) for at in range(0, 50, 10))
    documents += [doc(29, 50, parts=five), doc(30, 40, parts=five[:4])]
    planned = plan.build(documents, model=OPUS)
    assert len(planned.steps) == plan.RUN_MAX_STEPS == 60
    assert planned.refused == ((uuid.UUID(int=29), Refusal.over_steps),)
    assert uuid.UUID(int=30) in planned.planned_documents
