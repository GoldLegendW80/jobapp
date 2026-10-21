"""D17 follow ups (issue #423): a message with no drawn character is empty; the chat's lock order
(the posting before the document, as the Like, the freeze and the render take them) with two
sessions; the cancel route of a chat generation; the answer names the parts only the fact check
changed; a template deleted during a run falls back with a note; a template switch made by the
chat records what the template cannot print. Real Postgres at head, no sleep."""

from __future__ import annotations

import dataclasses
import threading
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.agents import runs as agent_runs
from app.agents.models import AgentRun, RunStatus
from app.documents import chat, store, submit, templates
from app.documents import content as c
from app.documents.models import (
    Author,
    DocumentGeneration,
    DocumentMessage,
    GenerationMode,
    GenerationOutcome,
    GenerationStatus,
    GenerationTrigger,
    Origin,
)
from app.jobs import service as jobs_service
from tests.documents.render_support import RecordingQueue
from tests.documents.test_chat import (
    BLAKE,
    VALID,
    Who,
    _messages,
    _run,
    _send,
    alex,
    app,
    queue,
)
from tests.documents.two_sessions import in_thread, wait_until_blocked
from tests.identity.api_helpers import Api
from tests.identity.conftest import api, clock, engine, factory, migrated_url

__all__ = ["alex", "api", "app", "clock", "engine", "factory", "migrated_url", "queue"]


# --- a message with nothing drawn is empty --------------------------------------------------------

REFUSED = [
    "​",
    "​​​",
    "‌",
    "‍",
    "​‌‍",
    "⁠",
    "﻿",
    "­",
    "͏",
    "؜",
    "ᅟ",
    "ᅠ",
    "឴",
    "᠎",
    "‎‏",
    "‪‬",
    "‮",
    "⁦⁩",
    "⁤",
    "ㅤ",
    "️",
    "︀",
    "ﾠ",
    "\U0001d173",
    "\U000e0001\U000e0020",
    "\U000e0100",
    " ​ ",
    "\n​\t",
    " ​",
    "　⁠",
    " ﻿ ",
]
ACCEPTED = [
    "a",
    "a​",
    "​shorter summary",
    "shorter‍summary",
    "1",
    ".",
    "?",
    "ß",
    "Résumé plus court",
    "短く",
    "الملخص",  # Arabic
    "ـ",  # Arabic tatweel: drawn
    "؀",  # a format character with a glyph
    "́",  # a combining mark alone: drawn on a dotted circle
    "⠀",  # braille blank: a symbol, kept (declared open point)
    "👍",
    "❤️",
    "👨‍👩‍👧",
    "🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",
    " x",
    "x​​",
]


@pytest.mark.parametrize("text", REFUSED)
def test_a_message_with_nothing_drawn_is_not_visible(text: str) -> None:
    assert not chat.visible(chat.clean(text))


@pytest.mark.parametrize("text", ACCEPTED)
def test_a_message_with_a_drawn_character_is_visible(text: str) -> None:
    assert chat.visible(chat.clean(text))


def test_visible_is_linear_on_a_message_at_the_cap() -> None:
    assert not chat.visible("​" * 20_000)
    assert chat.visible("​" * 19_999 + "a")


@pytest.mark.parametrize("text", ["​", "​‌‍﻿", " ⁠\n"])
def test_a_zero_width_message_is_422_and_stores_nothing(
    api: Api, alex: Who, queue: RecordingQueue, text: str
) -> None:
    sent = _send(api, alex, text)
    assert sent.status_code == 422, sent.text
    assert _messages(api, alex) == []
    assert queue.jobs == []


# --- the lock order, two sessions -----------------------------------------------------------------


def _post(factory: sessionmaker[Session], who: Who, clock: Any, settings: Any) -> Any:
    with factory() as db:
        try:
            posted = chat.post(
                db, who.user, who.cv, "shorter summary", at=clock(), settings=settings
            )
            store.commit(db)
            return posted.generation.id
        except Exception:
            db.rollback()
            raise


def test_a_chat_waits_for_the_likes_posting_and_holds_no_document_meanwhile(
    app: FastAPI, engine: sa.Engine, factory: sessionmaker[Session], clock: Any, alex: Who
) -> None:
    """The Like locks the posting, then the CV (apply.likes): a chat sent meanwhile waits at the
    posting, so the Like's lock of the CV never waits for it, and both go through."""
    outcome: dict[str, Any] = {}
    with factory() as like:
        assert jobs_service.lock_job(like, alex.user, alex.posting) is not None
        store.lock_posting(like, alex.user, alex.posting)
        thread = in_thread(lambda: _post(factory, alex, clock, app.state.settings), outcome)
        wait_until_blocked(engine, [thread], 1)
        assert thread.is_alive(), "the chat did not wait for the posting"
        store.lock_document(like, alex.user, alex.cv)
        like.commit()
    thread.join()
    assert "error" not in outcome, outcome.get("error")
    assert alex.generation(str(outcome["result"])).status is GenerationStatus.running


def test_a_like_waits_for_a_chat_that_holds_the_posting_then_goes_through(
    app: FastAPI, engine: sa.Engine, factory: sessionmaker[Session], clock: Any, alex: Who
) -> None:
    outcome: dict[str, Any] = {}

    def like() -> None:
        with factory() as db:
            assert jobs_service.lock_job(db, alex.user, alex.posting) is not None
            store.lock_posting(db, alex.user, alex.posting)
            for document in store.posting_documents(db, alex.user, alex.posting):
                store.lock_document(db, alex.user, document.id)
            db.commit()

    with factory() as db:
        chat.post(
            db, alex.user, alex.cv, "shorter summary", at=clock(), settings=app.state.settings
        )
        thread = in_thread(like, outcome)
        wait_until_blocked(engine, [thread], 1)
        assert thread.is_alive(), "the Like did not wait for the chat's posting lock"
        store.commit(db)
    thread.join()
    assert "error" not in outcome, outcome.get("error")


def test_two_messages_at_once_make_one_run_and_the_other_is_told_one_runs(
    app: FastAPI, engine: sa.Engine, factory: sessionmaker[Session], clock: Any, alex: Who
) -> None:
    outcome: dict[str, Any] = {}
    with factory() as first:
        chat.post(
            first, alex.user, alex.cv, "shorter summary", at=clock(), settings=app.state.settings
        )
        thread = in_thread(lambda: _post(factory, alex, clock, app.state.settings), outcome)
        wait_until_blocked(engine, [thread], 1)
        store.commit(first)
    thread.join()
    assert isinstance(outcome.get("error"), store.GenerationOpenError)
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(DocumentGeneration)) == 1
        assert db.scalar(sa.select(sa.func.count()).select_from(DocumentMessage)) == 1
        assert db.scalar(sa.select(sa.func.count()).select_from(AgentRun)) == 1


# --- the cancel route -----------------------------------------------------------------------------


def _cancel(api: Api, who: Who, generation_id: str) -> Any:
    return api.post(f"/api/document-generations/{generation_id}/cancel", token=who.token)


def test_a_queued_chat_run_is_cancelled_at_once_and_a_second_click_changes_nothing(
    api: Api, alex: Who, queue: RecordingQueue
) -> None:
    sent = _send(api, alex, "shorter summary", "valid").json()
    generation_id = sent["generation"]["id"]
    stopped = _cancel(api, alex, generation_id)
    assert stopped.status_code == 202, stopped.text
    assert stopped.headers["cache-control"] == "private, no-store"
    assert (stopped.json()["status"], stopped.json()["error"]) == ("cancelled", None)
    generation = alex.generation(generation_id)
    with alex.factory() as db:
        run = db.get(AgentRun, generation.run_id)
        assert run is not None
        assert run.status is RunStatus.cancelled
    assert [m["text"] for m in _messages(api, alex)] == ["shorter summary", chat.CANCELLED]
    again = _cancel(api, alex, generation_id)
    assert (again.status_code, again.json()["status"]) == (200, "cancelled")
    assert len(_messages(api, alex)) == 2
    assert c.parse("cv", alex.document().draft) == c.parse("cv", VALID)
    assert len(alex.versions()) == 1
    assert _send(api, alex, "shorter summary", "valid").status_code == 202


def test_a_running_chat_run_stops_cleanly_and_changes_nothing(
    api: Api,
    app: FastAPI,
    factory: sessionmaker[Session],
    queue: RecordingQueue,
    clock: Any,
    alex: Who,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _send(api, alex, "shorter summary", "valid").json()
    generation_id = sent["generation"]["id"]
    seen = threading.Event()
    asked: list[Any] = []
    real_read, real_requested = chat.conversation_json, agent_runs.cancel_requested

    def requested(db: Session, run_id: uuid.UUID) -> bool:
        found = real_requested(db, run_id)
        if found:
            seen.set()
        return found

    def read_then_stop(*args: Any, **kw: Any) -> Any:
        # The run is in the middle of its first tool call: the person clicks Stop.
        asked.append(_cancel(api, alex, generation_id))
        assert seen.wait(timeout=60)
        return real_read(*args, **kw)

    monkeypatch.setattr(agent_runs, "cancel_requested", requested)
    monkeypatch.setattr(chat, "conversation_json", read_then_stop)
    _run(app, factory, queue, clock)
    assert [(a.status_code, a.json()["status"]) for a in asked] == [(202, "running")]
    generation = alex.generation(generation_id)
    assert (generation.status, generation.version_number) == (GenerationStatus.cancelled, None)
    assert _messages(api, alex)[-1]["text"] == chat.CANCELLED
    assert c.parse("cv", alex.document().draft) == c.parse("cv", VALID)
    assert len(alex.versions()) == 1
    assert [job["function"] for job in queue.jobs] == []
    again = _cancel(api, alex, generation_id)
    assert (again.status_code, again.json()["status"]) == (200, "cancelled")


def test_an_ended_chat_generation_is_left_as_it_is(
    api: Api, app: FastAPI, factory: Any, queue: RecordingQueue, clock: Any, alex: Who
) -> None:
    sent = _send(api, alex, "shorter summary", "valid").json()
    _run(app, factory, queue, clock)
    answer = _cancel(api, alex, sent["generation"]["id"])
    assert (answer.status_code, answer.json()["status"]) == (200, "done")
    assert answer.json()["version_number"] == 2
    assert len(_messages(api, alex)) == 2


def test_cancel_is_404_for_another_person_an_unknown_id_and_a_writer_generation(
    api: Api, factory: Any, clock: Any, queue: RecordingQueue, alex: Who
) -> None:
    blake = Who(factory, api, BLAKE, clock)
    sent = _send(api, alex, "shorter summary", "valid").json()
    with factory() as db:
        written = store.create_generation(
            db,
            blake.user,
            blake.cv,
            GenerationMode.posting,
            GenerationTrigger.click,
            at=clock(),
            estimate={},
            token_ceiling=1,
        )
        db.commit()
        written_id = str(written.id)
    answers = [
        _cancel(api, blake, sent["generation"]["id"]),
        _cancel(api, alex, str(uuid.uuid4())),
        _cancel(api, blake, written_id),
        _cancel(api, alex, written_id),
    ]
    assert [a.status_code for a in answers] == [404] * 4
    assert alex.generation(sent["generation"]["id"]).status is GenerationStatus.running
    assert alex.generation(written_id).status is not GenerationStatus.cancelled


# --- the answer names the fact check's cuts apart -------------------------------------------------


def _outcome(before: Any, written: Any, after: Any, **extra: Any) -> chat.Outcome:
    return chat.Outcome(
        version=3,
        outcome=GenerationOutcome.draft_replaced,
        before=before,
        written=written,
        after=after,
        orgs={"example-corp": "Example Corp"},
        **extra,
    )


def test_a_part_only_the_fact_check_changed_is_named_apart() -> None:
    before = c.parse("cv", VALID)
    hand = before.model_copy(update={"highlights": ["Led 40 engineers", *before.highlights[1:]]})
    shorter = hand.model_copy(update={"summary": "Platform engineer."})
    after = shorter.model_copy(update={"highlights": ["Led engineers", *before.highlights[1:]]})
    text = chat.answer(_outcome(hand, shorter, after))
    assert text.startswith("Changed the summary.\nThe fact check alone changed the highlights.\n")
    assert chat.split_changes(hand, shorter, after, {}) == (["the summary"], ["the highlights"])


def test_a_claim_the_writer_added_and_the_fact_check_cut_whole_is_no_change() -> None:
    before = c.parse("cv", VALID)
    written = before.model_copy(update={"summary": before.summary + " I managed 20 people."})
    text = chat.answer(_outcome(before, written, before))
    assert text.startswith("The text is the same as before.")
    assert "Changed" not in text


def test_without_the_writers_own_content_every_part_is_the_writers() -> None:
    before = c.parse("cv", VALID)
    after = before.model_copy(update={"summary": "Platform engineer."})
    assert chat.answer(_outcome(before, None, after)).startswith("Changed the summary.")


# --- a template deleted during the run, and what a template cannot print --------------------------

OWN = "upload-1a2b3c4d"


@pytest.fixture
def own_template() -> Any:
    """A person's own template, served while `alive[0]`, as D25b's loader would."""
    alive = [True]
    mine = dataclasses.replace(templates.registry()["classic"], slug=OWN, name={"en": "Mine"})
    templates.register_loader(lambda db, user, slug: mine if slug == OWN and alive[0] else None)
    yield alive
    templates.register_loader(None)


def _ask_and_settle(
    who: Who, generation_id: str, slug: str, accepted: Any, before: Any, at: Any
) -> None:
    generation = who.generation(generation_id)
    assert generation.run_id is not None
    with who.factory() as db:
        run = db.get(AgentRun, generation.run_id)
        binding = chat.bind_for(db, run, "cv")
    _, error = chat.ask_template(who.factory, who.user, generation.run_id, binding, slug)
    assert not error
    before()
    taken = submit.Taken(accepted=accepted, submits=1 if accepted else 0, exhausted=False)
    chat.settle(
        who.factory, generation.run_id, RunStatus.done, taken, chat.take(generation.run_id), at
    )


@pytest.mark.parametrize("with_text", [False, True], ids=["template only", "with a change"])
def test_a_persons_template_deleted_during_the_run_falls_back_with_a_note(
    api: Api, alex: Who, queue: RecordingQueue, clock: Any, own_template: Any, with_text: bool
) -> None:
    sent = _send(api, alex, "use my template", "valid").json()
    shorter = c.parse("cv", VALID).model_copy(update={"summary": "Platform engineer."})
    accepted = submit.Accepted(shorter, (), shorter) if with_text else None

    def delete() -> None:
        own_template[0] = False

    _ask_and_settle(alex, sent["generation"]["id"], OWN, accepted, delete, clock())
    generation = alex.generation(sent["generation"]["id"])
    assert generation.status is GenerationStatus.done
    assert chat.generate.WRITE_TEMPLATE not in (generation.estimate or {})
    assert alex.document().template_slug == "classic"
    reply = _messages(api, alex)[-1]["text"]
    note = chat.TEMPLATE_GONE.format(name="Mine")
    assert reply.startswith(note)
    if with_text:
        assert alex.versions()[-1].template_slug == "classic"
        assert "Changed the summary." in reply
    else:
        assert reply == f"{note}\n{chat.NOTHING}"
        assert len(alex.versions()) == 1


def test_a_chat_template_switch_records_what_the_template_cannot_print(
    api: Api, alex: Who, queue: RecordingQueue, clock: Any
) -> None:
    sent = _send(api, alex, "use the sidebar template and shorten the summary", "valid").json()
    content = c.parse("cv", VALID).model_copy(update={"summary": "Platform engineer 漢字."})
    accepted = submit.Accepted(content, (), content)
    _ask_and_settle(alex, sent["generation"]["id"], "sidebar", accepted, lambda: None, clock())
    generation = alex.generation(sent["generation"]["id"])
    latest = alex.versions()[-1]
    assert (latest.origin, latest.template_slug) == (Origin.chat, "sidebar")
    assert [(r["field_path"], r["code"]) for r in latest.corrections] == [
        ("summary", "not_printable")
    ]
    assert generation.corrections == 0  # the fact check corrected nothing
    reply = _messages(api, alex)[-1]["text"]
    assert chat.UNPRINTABLE in reply
    assert "The fact check corrected" not in reply
    assert alex.document().draft_written_by is Author.chat
