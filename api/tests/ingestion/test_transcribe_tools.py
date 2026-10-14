"""The tools of the transcription step (ingestion G14), called as Claude Code calls them:
`view_image`, `store_transcription`, the binding they close over and the assembly of the
document's text when its last image is stored."""

import logging
import secrets as random_tokens
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents.crypto import Keyring
from app.ingestion import agents, engine, merge, plan, transcribe
from app.ingestion.models import (
    IMAGE_MAX_BYTES,
    IMAGE_TEXT_MAX_CHARS,
    TEXT_MAX_CHARS,
    IngestionDocument,
    IngestionImage,
    TextSource,
    TextStatus,
)
from app.ingestion.runs_models import IngestionRun, IngestionRunStep, RunStatus, StepStatus
from tests.agents.run_helpers import add_user, save_credential
from tests.ingestion.extract_helpers import add_document, add_step, delete_document, key, keyring
from tests.ingestion.transcribe_helpers import (
    add_scan,
    add_transcribe_step,
    binding_of,
    document_of,
    images_of,
    jpeg,
    shown,
    store,
    tools_for,
    view,
)
from tests.ingestion.transcribe_helpers import (
    transcribe as view_then_store,
)

__all__ = ["key", "keyring"]


@pytest.fixture
def alice_id(factory: sessionmaker[Session], keyring: Keyring) -> uuid.UUID:
    user = add_user(factory, "e2e-alice@example.org")
    save_credential(factory, keyring, user)
    return user


@pytest.fixture
def bob_id(factory: sessionmaker[Session], keyring: Keyring) -> uuid.UUID:
    user = add_user(factory, "e2e-bob@example.org")
    save_credential(factory, keyring, user)
    return user


def token() -> str:
    """A planted random marker: found nowhere but where the test put it."""
    return "marker" + random_tokens.token_hex(12)


# --- the definition and the binding -------------------------------------------------------------


def test_the_agent_is_internal_private_and_reads_with_the_model_of_the_credential() -> None:
    definition = agents.TRANSCRIBE
    assert definition.name == engine.TRANSCRIBE_NAME == agents.TRANSCRIBE_NAME
    assert engine.AGENT_OF[plan.StepKind.transcribe] == definition.name
    assert definition.tools == ("view_image", "store_transcription")
    assert (definition.internal, definition.private_io) == (True, True)
    assert definition.model == plan.MODEL
    assert dict(definition.model_by_credential) == plan.MODEL_BY_CREDENTIAL
    assert definition.on_final is agents.EXTRACT.on_final  # the engine's hook, as for extraction
    assert plan.TRANSCRIPTION is True


def test_a_step_is_bound_to_the_images_of_its_group_that_wait(
    factory: sessionmaker[Session], alice_id: uuid.UUID
) -> None:
    document = add_scan(factory, alice_id, ["Page one.", None, "Page three.", None])
    step = add_transcribe_step(factory, alice_id, document, [1, 3])
    assert binding_of(factory, step) == {
        "ingestion_run_id": str(step.ingestion_run_id),
        "step_id": str(step.step_id),
        "document_id": str(document),
        "positions": [1, 3],
    }
    view_then_store(tools_for(factory, step), 1, "Second page.")
    assert binding_of(factory, step)["positions"] == [3]  # type: ignore[index]


@pytest.mark.parametrize("change", ["step_done", "run_paused", "document_done", "other_file"])
def test_nothing_is_bound_when_there_is_nothing_to_transcribe(
    factory: sessionmaker[Session], alice_id: uuid.UUID, change: str
) -> None:
    document = add_scan(factory, alice_id, [None])
    step = add_transcribe_step(factory, alice_id, document, [0])
    with factory() as db, db.begin():
        if change == "step_done":
            db.execute(sa.update(IngestionRunStep).values(status=StepStatus.done))
        elif change == "run_paused":
            db.execute(sa.update(IngestionRun).values(status=RunStatus.paused, paused_reason="x"))
        elif change == "document_done":
            db.execute(sa.update(IngestionDocument).values(text_status=TextStatus.done))
        else:
            db.execute(sa.update(IngestionDocument).values(sha256=b"\x01" * 32))
    assert binding_of(factory, step) is None


def test_an_extraction_step_and_a_deleted_document_bind_nothing(
    factory: sessionmaker[Session], alice_id: uuid.UUID, bob_id: uuid.UUID
) -> None:
    text_step = add_step(factory, alice_id, add_document(factory, alice_id, "A text."))
    with factory() as db:
        run = db.get_one(agents.AgentRun, text_step.agent_run_id)
        assert agents.bind_transcription(db, run) is None
        run.input = {"step_id": "not an id"}
        assert agents.bind_transcription(db, run) is None
    document = add_scan(factory, bob_id, [None])
    step = add_transcribe_step(factory, bob_id, document, [0])
    delete_document(factory, document)
    assert binding_of(factory, step) is None


def test_a_group_whose_images_are_all_stored_binds_nothing_a_retry_sends_no_image(
    factory: sessionmaker[Session], alice_id: uuid.UUID
) -> None:
    document = add_scan(factory, alice_id, [None, None, None])
    first = add_transcribe_step(factory, alice_id, document, [0, 1])
    tools = tools_for(factory, first)
    view_then_store(tools, 0, "One.")
    view_then_store(tools, 1, "Two.")
    assert binding_of(factory, first) is None  # the document still waits for position 2


@pytest.mark.parametrize(
    "binding",
    [
        {},
        {"step_id": "x", "document_id": "y", "positions": [0]},
        {"positions": []},
        {"positions": list(range(plan.GROUP_MAX_IMAGES + 1))},
        {"positions": [0, 0]},
        {"positions": [-1]},
        {"positions": [True]},
        {"positions": 3},
    ],
)
def test_a_binding_that_is_not_a_group_of_images_is_refused(
    factory: sessionmaker[Session], alice_id: uuid.UUID, binding: dict[str, object]
) -> None:
    document = add_scan(factory, alice_id, [None])
    step = add_transcribe_step(factory, alice_id, document, [0])
    whole = {"step_id": str(step.step_id), "document_id": str(document), **binding}
    tools = tools_for(factory, step, binding=whole)
    assert view(tools, 0)["content"][0]["text"] == transcribe.NOT_FOUND
    assert store(tools, 0, "text") == (transcribe.NOT_FOUND, True)


# --- view_image ---------------------------------------------------------------------------------


def test_view_image_answers_one_image_block_the_bytes_as_stored(
    factory: sessionmaker[Session], alice_id: uuid.UUID
) -> None:
    document = add_scan(factory, alice_id, ["Page one.", None, None])
    step = add_transcribe_step(factory, alice_id, document, [1, 2])
    tools = tools_for(factory, step)
    for position in (1, 2):
        result = view(tools, position)
        assert "is_error" not in result
        assert len(result["content"]) == 1  # one image per result
        assert shown(result) == jpeg(position)
    assert len(jpeg(1)) <= IMAGE_MAX_BYTES


def test_view_image_shows_only_an_image_of_the_step_that_waits(
    factory: sessionmaker[Session], alice_id: uuid.UUID
) -> None:
    document = add_scan(factory, alice_id, [None, None, None, None])
    step = add_transcribe_step(factory, alice_id, document, [1, 3])
    tools = tools_for(factory, step)
    for position in (0, 2, 7):  # images of the document, but of another group, or of none
        result = view(tools, position)
        assert result["is_error"] is True
        assert result["content"] == [
            {"type": "text", "text": f"{transcribe.NOT_OF_STEP} Positions left: 1, 3."}
        ]
    for bad in (-1, "1", 1.0, True, None):
        assert view(tools, bad)["content"][0]["text"] == transcribe.BAD_POSITION
    view_then_store(tools, 1, "Stored.")
    again = view(tools, 1)  # stored: never shown, so never paid for, twice
    assert again["content"][0]["text"] == f"{transcribe.NOT_OF_STEP} Positions left: 3."
    view_then_store(tools, 3, "Stored too.")
    assert view(tools, 3)["content"][0]["text"] == transcribe.NONE_LEFT


def test_an_image_over_the_stored_limits_is_never_sent(
    factory: sessionmaker[Session], alice_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = add_scan(factory, alice_id, [None])
    step = add_transcribe_step(factory, alice_id, document, [0])
    tools = tools_for(factory, step)
    monkeypatch.setattr(transcribe, "IMAGE_MAX_BYTES", len(jpeg(0)) - 1)
    assert view(tools, 0)["content"] == [{"type": "text", "text": transcribe.UNREADABLE}]
    monkeypatch.setattr(transcribe, "IMAGE_MAX_BYTES", IMAGE_MAX_BYTES)
    monkeypatch.setattr(transcribe, "IMAGE_MAX_SIDE", 63)
    assert view(tools, 0)["content"] == [{"type": "text", "text": transcribe.UNREADABLE}]
    assert store(tools, 0, "text") == (transcribe.NOT_VIEWED, True)


def test_another_user_or_another_run_sees_nothing(
    factory: sessionmaker[Session], alice_id: uuid.UUID, bob_id: uuid.UUID
) -> None:
    document = add_scan(factory, alice_id, [None])
    step = add_transcribe_step(factory, alice_id, document, [0])
    binding = binding_of(factory, step)
    for tools in (
        tools_for(factory, step, user_id=bob_id, binding=binding),
        tools_for(factory, step, run_id=uuid.uuid4(), binding=binding),
    ):
        assert view(tools, 0)["content"] == [{"type": "text", "text": transcribe.NOT_FOUND}]
        assert store(tools, 0, "text") == (transcribe.NOT_FOUND, True)
    assert images_of(factory, document) == {0: None}


# --- store_transcription ------------------------------------------------------------------------


def test_store_before_view_twice_and_outside_the_step(
    factory: sessionmaker[Session], alice_id: uuid.UUID
) -> None:
    document = add_scan(factory, alice_id, [None, None, None])
    step = add_transcribe_step(factory, alice_id, document, [0, 1])
    tools = tools_for(factory, step)
    assert store(tools, 0, "Not viewed.") == (transcribe.NOT_VIEWED, True)
    assert store(tools, 2, "Outside.") == (
        f"{transcribe.NOT_OF_STEP} Positions left: 0, 1.",
        True,
    )
    assert view_then_store(tools, 0, "The first page.") == (
        {"stored": 0, "next_position": 1},
        False,
    )
    assert store(tools, 0, "The first page, again.") == (transcribe.ALREADY_STORED, True)
    assert view_then_store(tools, 1, "The second page.") == (
        {"stored": 1, "next_position": None},
        False,
    )
    assert images_of(factory, document) == {0: "The first page.", 1: "The second page.", 2: None}
    assert document_of(factory, document).text_status is TextStatus.awaiting_transcription
    for position, text in ((None, "x"), ("0", "x"), (-1, "x")):
        assert store(tools, position, text) == (transcribe.BAD_POSITION, True)
    for text in (None, 3, ["a"]):
        assert store(tools, 0, text) == (transcribe.BAD_CALL, True)


def test_a_text_over_the_limit_of_one_image_is_refused_and_one_at_the_limit_stored(
    factory: sessionmaker[Session], alice_id: uuid.UUID
) -> None:
    document = add_scan(factory, alice_id, [None, None])
    step = add_transcribe_step(factory, alice_id, document, [0, 1])
    tools = tools_for(factory, step)
    view(tools, 0)
    assert store(tools, 0, "a" * (IMAGE_TEXT_MAX_CHARS + 1)) == (transcribe.TOO_LONG, True)
    assert images_of(factory, document) == {0: None, 1: None}
    assert store(tools, 0, "a" * IMAGE_TEXT_MAX_CHARS)[1] is False


def test_a_text_that_takes_the_document_over_its_limit_is_refused(
    factory: sessionmaker[Session], alice_id: uuid.UUID
) -> None:
    room = 100
    document = add_scan(factory, alice_id, ["a" * (TEXT_MAX_CHARS - room - 2), None, None])
    step = add_transcribe_step(factory, alice_id, document, [1, 2])
    tools = tools_for(factory, step)
    assert view_then_store(tools, 1, "b" * (room - 10))[1] is False
    view(tools, 2)
    assert store(tools, 2, "c" * 11) == (transcribe.DOCUMENT_FULL, True)
    assert images_of(factory, document)[2] is None
    assert store(tools, 2, "c" * 10)[1] is False  # exactly the limit
    assert len(document_of(factory, document).text) == TEXT_MAX_CHARS


def test_a_form_feed_and_control_characters_in_a_transcription(
    factory: sessionmaker[Session], alice_id: uuid.UUID
) -> None:
    document = add_scan(factory, alice_id, [None, "Page two."])
    step = add_transcribe_step(factory, alice_id, document, [0])
    view_then_store(tools_for(factory, step), 0, "Top\fBottom\r\nEnd\x00\x07")
    found = document_of(factory, document)
    assert found.text == "Top\nBottom\nEnd\fPage two."
    assert found.text.count("\f") == 1  # the pages of the document, and nothing else


# --- the assembly -------------------------------------------------------------------------------


def test_a_mixed_pdf_ends_in_page_order_and_its_citations_keep_their_place(
    factory: sessionmaker[Session], alice_id: uuid.UUID
) -> None:
    third = "The third page was read as text by the parser."
    pages = ["First page, as text.", None, third, "Fourth page.", None]
    document = add_scan(factory, alice_id, pages)
    step = add_transcribe_step(factory, alice_id, document, [1, 4])
    tools = tools_for(factory, step)
    view_then_store(tools, 4, "Fifth page, from a scan.")  # out of order on purpose
    assert document_of(factory, document).text_status is TextStatus.awaiting_transcription
    view_then_store(tools, 1, "Second page, from a scan.")
    found = document_of(factory, document)
    assert found.text.split("\f") == [
        "First page, as text.",
        "Second page, from a scan.",
        third,
        "Fourth page.",
        "Fifth page, from a scan.",
    ]
    assert found.text_status is TextStatus.done
    assert found.text_source is TextSource.parsed
    assert found.transcribed_pages == [1, 4]
    assert images_of(factory, document) == {}  # deleted with the last text
    # A citation is offsets in the text as stored now: one into page 3 opens on page 3 and is
    # not shown as transcribed, one into page 2 is.
    start = found.text.index(third)
    second = found.text.index("Second page")
    with factory() as db:
        marks = merge._transcribed_pages(db, [document])
    cited = {"document_id": str(document), "start": start, "end": start + len(third)}
    assert found.text[cited["start"] : cited["end"]] == third
    assert merge._is_transcribed(marks, cited) is False
    assert merge._is_transcribed(marks, {**cited, "start": second, "end": second + 6}) is True


def test_a_screenshot_with_no_text_is_done_with_an_empty_text(
    factory: sessionmaker[Session], alice_id: uuid.UUID
) -> None:
    document = add_scan(factory, alice_id, [None])
    step = add_transcribe_step(factory, alice_id, document, [0])
    assert view_then_store(tools_for(factory, step), 0, "") == (
        {"stored": 0, "next_position": None},
        False,
    )
    found = document_of(factory, document)
    assert (found.text, found.text_status) == ("", TextStatus.done)
    assert found.text_source is TextSource.transcribed
    assert images_of(factory, document) == {}
    with factory() as db:
        assert merge._transcribed_pages(db, [document]) == {str(document): True}


def test_a_31_page_scan_keeps_the_slot_of_the_page_that_got_no_image(
    factory: sessionmaker[Session], alice_id: uuid.UUID
) -> None:
    """G8 stores 30 images at most: the 31st page has an empty slot and no image. Four groups of
    at most 8, and the document is done when the last group stores its last image."""
    document = add_scan(factory, alice_id, [None] * 30 + [""])
    described = plan.Document(document, "ab" * 32, 30, tuple(range(30)))
    groups = [
        one.images
        for one in plan.build([described], model=plan.MODEL).steps
        if one.kind is plan.StepKind.transcribe
    ]
    assert [len(group) for group in groups] == [8, 8, 8, 6]
    step = None
    for group in groups:
        step = add_transcribe_step(factory, alice_id, document, group, after=step)
        tools = tools_for(factory, step)
        for position in group:
            assert document_of(factory, document).text_status is not TextStatus.done
            view_then_store(tools, position, f"Scanned page {position + 1}.")
        with factory() as db, db.begin():  # the engine ends a step before it starts the next
            db.execute(
                sa.update(IngestionRunStep)
                .where(IngestionRunStep.id == step.step_id)
                .values(status=StepStatus.done)
            )
    found = document_of(factory, document)
    slots = found.text.split("\f")
    assert slots == [f"Scanned page {number}." for number in range(1, 31)] + [""]
    assert found.transcribed_pages == list(range(30))
    assert found.text_status is TextStatus.done


def test_a_document_deleted_mid_transcription(
    factory: sessionmaker[Session], alice_id: uuid.UUID
) -> None:
    document = add_scan(factory, alice_id, [None, None])
    step = add_transcribe_step(factory, alice_id, document, [0, 1])
    tools = tools_for(factory, step)
    view_then_store(tools, 0, "Stored before the deletion.")
    view(tools, 1)
    delete_document(factory, document)
    assert store(tools, 1, "Stored after it.") == (transcribe.NOT_FOUND, True)
    assert view(tools, 1)["content"] == [{"type": "text", "text": transcribe.NOT_FOUND}]
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(IngestionImage)) == 0
        assert db.scalar(sa.select(sa.func.count()).select_from(IngestionDocument)) == 0


def test_a_transcription_that_shows_a_secret_warns_like_any_text(
    factory: sessionmaker[Session], alice_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = add_scan(factory, alice_id, [None])
    step = add_transcribe_step(factory, alice_id, document, [0])
    assert document_of(factory, document).secret_warning is False
    monkeypatch.setattr(transcribe.secrets, "scan", lambda text: ["made_up_rule"])
    view_then_store(tools_for(factory, step), 0, "A page.")
    assert document_of(factory, document).secret_warning is True


def test_the_viewed_positions_of_old_runs_leave_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transcribe, "VIEWED_RUNS_MAX", 2)
    monkeypatch.setattr(transcribe, "_viewed", type(transcribe._viewed)())
    first, second, third = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    transcribe._viewed_by(first).add(0)
    transcribe._viewed_by(second).add(0)
    transcribe._viewed_by(third)
    assert transcribe._viewed_by(second) == {0}
    assert transcribe._viewed_by(first) == set()  # forgotten: that run views again


# --- privacy ------------------------------------------------------------------------------------


def test_no_answer_and_no_log_holds_a_word_of_the_page(
    factory: sessionmaker[Session],
    alice_id: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = token()
    document = add_scan(factory, alice_id, [None, None])
    step = add_transcribe_step(factory, alice_id, document, [0, 1])
    tools = tools_for(factory, step)
    answers = []
    with caplog.at_level(logging.DEBUG):
        view(tools, 0)
        answers.append(store(tools, 0, f"A line with {marker}."))
        answers.append(store(tools, 0, f"Again {marker}."))
        answers.append(store(tools, 1, f"Unviewed {marker}."))
        answers.append(store(tools, 5, f"Outside {marker}."))

        def broken(*args: object, **kwargs: object) -> None:
            raise RuntimeError(f"a failure that quotes {marker}")

        monkeypatch.setattr(transcribe, "_left", broken)
        failed = view(tools, 1)
        view_error = failed["content"][0]["text"]
        monkeypatch.setattr(transcribe, "_waits", broken)
        answers.append(store(tools, 1, f"Failing {marker}."))
    assert (view_error, failed["is_error"]) == (transcribe.FAILED, True)
    assert answers[-1] == (transcribe.FAILED, True)
    assert marker not in repr(answers)
    logged = "\n".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)
    assert "RuntimeError" in logged  # the type of the failure, and nothing of its message
    assert marker not in logged
    assert "scan.pdf" not in logged
