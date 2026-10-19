"""D17: the chat of a document, through the routes the panel calls and the chat writers on the
fake backend (`documents_chat_*` streams). Two e2e people; real Postgres at head.

A message makes a generation of mode chat whose result goes through D7's pipeline: "shorter
summary" changes the summary only; "say I managed 20 people" is written, then corrected away, and
the answer says so and suggests the profile; "use the sidebar template" switches with the content
unchanged; an unknown template lists the gallery; a message while the person edits is kept as a
version; a frozen version stays as it was; a base CV keeps its fixed block. Refused: an empty or
too long message, the design channel, a second message while one runs, a message without the
consent or a credential (nothing stored), another person's chat (404). The 201st message prunes
the oldest; the canary of a message reaches no log, no arq argument and no run event; a tool the
chat writer does not have is refused; a run whose hook was missed is settled by the next read."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.agents import credentials, definitions
from app.agents.crypto import Keyring
from app.agents.models import AgentEvent, AgentRun, CredentialKind, RunStatus
from app.agents.runner import RunContext
from app.documents import chat, consent, generate, store, templates, writers  # noqa: F401
from app.documents import content as c
from app.documents.corrections import Correction
from app.documents.models import (
    Author,
    BaseSource,
    Document,
    DocumentGeneration,
    DocumentMessage,
    DocumentVersion,
    FreezeReason,
    GenerationOutcome,
    GenerationStatus,
    Kind,
    Origin,
    Role,
)
from app.main import create_app
from tests.agents.conftest import API_KEY, new_key
from tests.agents.g6_helpers import everything_stored
from tests.agents.run_helpers import Backends
from tests.conftest import make_settings
from tests.documents.canary import find, marker
from tests.documents.conftest import load
from tests.documents.render_support import RecordingQueue, give_profile
from tests.identity.api_helpers import Api, make_user
from tests.identity.conftest import api, clock, engine, factory, migrated_url

__all__ = ["api", "clock", "engine", "factory", "migrated_url"]

ALEX = "e2e-docs-d17-alex@example.org"
BLAKE = "e2e-docs-d17-blake@example.org"
KEY = new_key()
STREAMS = Path(chat.__file__).parent.parent / "agents" / "fake_streams"


def _submitted(name: str) -> dict[str, Any]:
    steps = json.loads((STREAMS / f"{name}.json").read_text(encoding="utf-8"))["steps"]
    return next(
        block["input"]
        for step in steps
        for block in step["message"].get("message", {}).get("content", [])
        if block.get("type") == "tool_use" and "submit" in block["name"]
    )


VALID = _submitted("documents_cv_valid")
LETTER = _submitted("documents_letter_valid")


@pytest.fixture
def app(migrated_url: str, clock: Any) -> FastAPI:
    settings = make_settings(migrated_url, agents_fake_backend=True, credentials_key=KEY)
    return create_app(settings, clock=clock)


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:
    recording = RecordingQueue()
    app.state.queue = recording
    return recording


class Who:
    """A person with the invented profile, a base CV, a posting and its CV written."""

    def __init__(
        self,
        factory: sessionmaker[Session],
        api: Api,
        email: str,
        clock: Any,
        *,
        consented: bool = True,
        credential: bool = True,
    ) -> None:
        self.factory = factory
        self.user = make_user(factory, email=email, name=email.split("@")[0]).id
        give_profile(factory, self.user, clock())
        self.token = api.login_token(email)
        with factory() as db:
            if consented:
                consent.record(db, self.user, clock())
            if credential:
                credentials.save(
                    db,
                    Keyring.from_text(KEY),
                    self.user,
                    CredentialKind.anthropic_api_key,
                    API_KEY,
                    clock(),
                )
            base = store.create_base(
                db,
                self.user,
                "My CV",
                BaseSource.profile,
                at=clock(),
                content=load("base_cv_alex.json"),
            )
            self.base = base.id
            self.posting = db.execute(
                sa.text(
                    "INSERT INTO jobs (user_id, slug, company, title, posting_text) VALUES "
                    "(:u, :s, 'Lumen Example', 'Platform engineer', :t) RETURNING id"
                ),
                {
                    "u": self.user,
                    "s": f"lumen-{uuid.uuid4().hex[:8]}",
                    "t": load("posting_lumen.json")["text"],
                },
            ).scalar_one()
            cv = store.get_or_create(
                db, self.user, self.posting, Kind.cv, at=clock(), base_document_id=self.base
            )
            store.save_draft(
                db, self.user, cv.id, VALID, cv.draft_revision, Author.writer, at=clock()
            )
            store.snapshot(db, self.user, cv.id, Origin.generated, at=clock())
            self.cv = cv.id
            db.commit()

    def document(self, document_id: uuid.UUID | None = None) -> Document:
        with self.factory() as db:
            found = db.get(Document, document_id or self.cv)
            assert found is not None
            return found

    def versions(self, document_id: uuid.UUID | None = None) -> list[DocumentVersion]:
        with self.factory() as db:
            return list(
                db.scalars(
                    sa.select(DocumentVersion)
                    .where(DocumentVersion.document_id == (document_id or self.cv))
                    .order_by(DocumentVersion.number)
                    .options(undefer(DocumentVersion.content))
                )
            )

    def generation(self, generation_id: str) -> DocumentGeneration:
        with self.factory() as db:
            found = db.get(DocumentGeneration, uuid.UUID(generation_id))
            assert found is not None
            return found


def _send(api: Api, who: Who, text: str, script: str | None = None, **extra: Any) -> Any:
    document = extra.pop("document", None) or who.cv
    body: dict[str, Any] = {"text": text, **extra}
    if script is not None:
        body["script"] = script
    return api.post(f"/api/documents/{document}/messages", token=who.token, json=body)


def _messages(api: Api, who: Who, document: uuid.UUID | None = None) -> list[dict[str, Any]]:
    answer = api.get(f"/api/documents/{document or who.cv}/messages", token=who.token)
    assert answer.status_code == 200, answer.text
    return answer.json()["messages"]


def _run(
    app: FastAPI, factory: sessionmaker[Session], queue: RecordingQueue, clock: Any
) -> list[uuid.UUID]:
    """Every queued chat run, executed on the fake backend; the render jobs stay queued."""
    from app.agents import runner

    context = RunContext(
        settings=app.state.settings,
        sessions=factory,
        clock=lambda: datetime.now(UTC),
        keyring=Keyring.from_text(KEY),
        backend_factory=Backends(speed=0.0),
        cancel_poll_s=0.02,
        interrupt_grace_s=1.0,
        services={generate.QUEUE_SERVICE: queue},
    )
    ran = [uuid.UUID(job["args"][0]) for job in queue.jobs if job["function"] == "run_agent"]
    queue.jobs[:] = [job for job in queue.jobs if job["function"] != "run_agent"]
    for run_id in ran:
        asyncio.run(runner.execute(context, run_id))
    return ran


@pytest.fixture
def alex(factory: sessionmaker[Session], api: Api, clock: Any, queue: RecordingQueue) -> Who:
    return Who(factory, api, ALEX, clock)


def _talk(
    api: Api,
    app: FastAPI,
    factory: Any,
    queue: RecordingQueue,
    clock: Any,
    who: Who,
    text: str,
    script: str,
    **extra: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    sent = _send(api, who, text, script, **extra)
    assert sent.status_code == 202, sent.text
    _run(app, factory, queue, clock)
    return sent.json(), _messages(api, who, extra.get("document"))[-1]


# --- what a message changes -----------------------------------------------------------------------


def test_shorter_summary_changes_the_summary_only_and_names_the_version(
    api: Api, app: FastAPI, factory: Any, queue: RecordingQueue, clock: Any, alex: Who
) -> None:
    sent = _send(api, alex, "shorter summary", "valid")
    assert sent.status_code == 202, sent.text
    assert sent.headers["cache-control"] == "private, no-store"
    body = sent.json()
    assert body["message"]["role"] == "user"
    assert body["message"]["text"] == "shorter summary"
    assert body["message"]["generation_id"] == body["generation"]["id"]
    assert body["generation"]["status"] == "running"
    followed = api.get(f"/api/document-generations/{body['generation']['id']}", token=alex.token)
    assert followed.json()["mode"] == "chat"
    assert followed.json()["trigger"] == "chat"

    _run(app, factory, queue, clock)
    generation = alex.generation(body["generation"]["id"])
    assert generation.status is GenerationStatus.done
    assert generation.outcome is GenerationOutcome.draft_replaced
    document = alex.document()
    assert document.draft_written_by is Author.chat
    before, after = c.parse("cv", VALID), c.parse("cv", document.draft)
    assert after.summary != before.summary
    assert after.model_copy(update={"summary": before.summary}) == before
    latest = alex.versions()[-1]
    assert latest.origin is Origin.chat
    assert latest.number == generation.version_number == 2
    reply = _messages(api, alex)[-1]
    assert reply["role"] == "assistant"
    assert reply["version_number"] == 2
    assert reply["text"] == "Changed the summary.\nThis is version 2."
    assert [job["function"] for job in queue.jobs] == ["render_document"]
    detail = api.get(f"/api/document-generations/{generation.id}", token=alex.token).json()
    assert (detail["status"], detail["outcome"], detail["version_number"]) == (
        "done",
        "draft_replaced",
        2,
    )


def test_say_i_managed_20_people_is_written_then_corrected_and_the_answer_suggests_the_profile(
    api: Api, app: FastAPI, factory: Any, queue: RecordingQueue, clock: Any, alex: Who
) -> None:
    _, reply = _talk(api, app, factory, queue, clock, alex, "say I managed 20 people", "managed")
    summary = c.parse("cv", alex.document().draft).summary
    assert "20" not in summary
    assert "managed people" in summary
    latest = alex.versions()[-1]
    assert [(r["code"], r["by"]) for r in latest.corrections] == [("number_not_in_sources", "code")]
    assert (
        '"20" was removed from the summary: a number your profile does not state.' in reply["text"]
    )
    assert chat.SUGGEST_PROFILE in reply["text"]
    assert "The fact check corrected 1 claim:" in reply["text"]
    assert reply["text"].endswith("This is version 2.")


def test_use_the_sidebar_template_switches_with_the_content_unchanged(
    api: Api, app: FastAPI, factory: Any, queue: RecordingQueue, clock: Any, alex: Who
) -> None:
    before = alex.versions()[-1]
    _, reply = _talk(api, app, factory, queue, clock, alex, "use the sidebar template", "sidebar")
    document = alex.document()
    assert document.template_slug == "sidebar"
    assert c.parse("cv", document.draft) == c.parse("cv", VALID)
    latest = alex.versions()[-1]
    assert latest.origin is Origin.template
    assert latest.content_hash == before.content_hash
    assert latest.template_slug == "sidebar"
    assert reply["text"] == f"Switched the template to Sidebar.\nThis is version {latest.number}."
    assert [job["function"] for job in queue.jobs] == ["render_document"]


def test_an_unknown_template_lists_the_gallery_and_changes_nothing(
    api: Api, app: FastAPI, factory: Any, queue: RecordingQueue, clock: Any, alex: Who
) -> None:
    sent, reply = _talk(api, app, factory, queue, clock, alex, "use the neon template", "unknown")
    assert alex.document().template_slug == "classic"
    assert len(alex.versions()) == 1
    names = [t.name["en"] for t in templates.registry().values()]
    assert reply["text"].startswith("No template has that name. The gallery has ")
    assert all(name in reply["text"] for name in names)
    assert reply["text"].endswith(chat.NOTHING)
    generation = alex.generation(sent["generation"]["id"])
    assert (generation.status, generation.version_number) == (GenerationStatus.done, None)


def test_a_message_while_the_person_edits_is_kept_as_a_version(
    api: Api, app: FastAPI, factory: Any, queue: RecordingQueue, clock: Any, alex: Who
) -> None:
    sent = _send(api, alex, "shorter summary", "valid")
    typed = dict(VALID, headline="Typed by hand while the chat writes")
    with factory() as db:
        revision = db.get(Document, alex.cv).draft_revision
        store.save_draft(db, alex.user, alex.cv, typed, revision, Author.user, at=clock())
        db.commit()
    _run(app, factory, queue, clock)
    document = alex.document()
    assert document.draft_written_by is Author.user
    assert c.parse("cv", document.draft).headline == "Typed by hand while the chat writes"
    generation = alex.generation(sent.json()["generation"]["id"])
    assert generation.outcome is GenerationOutcome.kept_as_version
    kept = alex.versions()[-1]
    assert kept.origin is Origin.chat
    assert kept.number == generation.version_number
    assert c.parse("cv", kept.content).headline == VALID["headline"]
    reply = _messages(api, alex)[-1]["text"]
    assert f"kept as version {kept.number}" in reply
    assert "your draft stays as you left it" in reply


def test_a_chat_on_a_document_with_a_frozen_version_leaves_it_as_it_was(
    api: Api, app: FastAPI, factory: Any, queue: RecordingQueue, clock: Any, alex: Who
) -> None:
    with factory() as db:
        first = alex.versions()[0]
        store.freeze(db, alex.user, first.id, FreezeReason.marked_sent, clock())
        db.commit()
    frozen = alex.versions()[0]
    _talk(api, app, factory, queue, clock, alex, "shorter summary", "valid")
    _talk(api, app, factory, queue, clock, alex, "use the sidebar template", "sidebar")
    again = alex.versions()[0]
    assert (again.content_hash, again.template_slug, again.frozen_at) == (
        frozen.content_hash,
        frozen.template_slug,
        frozen.frozen_at,
    )
    assert [v.origin for v in alex.versions()[1:]] == [Origin.chat, Origin.template]


def test_a_base_cv_keeps_its_fixed_block_whatever_the_writer_sends(
    api: Api, app: FastAPI, factory: Any, queue: RecordingQueue, clock: Any, alex: Who
) -> None:
    fixed = c.parse("base", alex.document(alex.base).draft).fixed
    _, reply = _talk(
        api, app, factory, queue, clock, alex, "shorter summary", "valid", document=alex.base
    )
    content = c.parse("base", alex.document(alex.base).draft)
    assert content.fixed == fixed
    assert "Initech" not in json.dumps(alex.document(alex.base).draft)
    assert content.summary.startswith("Platform engineer with about 3.5 years")
    assert reply["text"].startswith("Changed the summary")
    assert reply["text"].endswith("This is version 1.")


def test_a_letter_chat_changes_its_paragraph(
    api: Api, app: FastAPI, factory: Any, queue: RecordingQueue, clock: Any, alex: Who
) -> None:
    with factory() as db:
        letter = store.get_or_create(db, alex.user, alex.posting, Kind.letter, at=clock())
        store.save_draft(
            db, alex.user, letter.id, LETTER, letter.draft_revision, Author.writer, at=clock()
        )
        db.commit()
        letter_id = letter.id
    sent, reply = _talk(
        api, app, factory, queue, clock, alex, "shorter last paragraph", "valid", document=letter_id
    )
    assert alex.generation(sent["generation"]["id"]).base_document_id == alex.base
    paragraphs = c.parse("letter", alex.document(letter_id).draft).paragraphs
    assert paragraphs[3] == "I live in Lyon and speak English and French."
    assert reply["text"] == "Changed paragraph 4.\nThis is version 1."


# --- what is refused ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["", "   ", "\n\t\n", "\x00\x01\x02", "\\ud800", "a" * 2001, "a" * 1999 + "\x00" + "bc"],
)
def test_an_empty_or_too_long_message_is_422_and_stores_nothing(
    api: Api, alex: Who, queue: RecordingQueue, text: str
) -> None:
    # Sent as JSON text, so a lone surrogate escape reaches the api as the client wrote it.
    body = f'{{"text": "{text}"}}' if text == "\\ud800" else json.dumps({"text": text})
    sent = api.post(
        f"/api/documents/{alex.cv}/messages",
        token=alex.token,
        content=body.encode(),
        headers={"content-type": "application/json"},
    )
    assert sent.status_code == 422, sent.text
    assert _messages(api, alex) == []
    assert queue.jobs == []


@pytest.mark.parametrize(
    ("text", "stored"),
    [
        ("a" * 2000, "a" * 2000),
        ("  shorter\r\nsummary\r ", "shorter\nsummary"),
        ("a\x00" * 2000, "a" * 2000),
        ("Résumé plus court, s'il te plaît 🙂", "Résumé plus court, s'il te plaît 🙂"),
    ],
)
def test_a_message_at_the_limit_is_stored_as_cleaned(
    api: Api, alex: Who, queue: RecordingQueue, text: str, stored: str
) -> None:
    sent = api.post(f"/api/documents/{alex.cv}/messages", token=alex.token, json={"text": text})
    assert sent.status_code == 202, sent.text
    assert sent.json()["message"]["text"] == stored


def test_the_design_channel_is_not_open_yet(api: Api, alex: Who) -> None:
    sent = _send(api, alex, "fix the colours", channel="design")
    assert sent.status_code == 422


def test_a_second_message_while_one_runs_is_409(api: Api, alex: Who, queue: RecordingQueue) -> None:
    assert _send(api, alex, "shorter summary", "valid").status_code == 202
    second = _send(api, alex, "and the headline", "valid")
    assert second.status_code == 409
    assert (
        second.json()["detail"]
        == "The assistant is still answering the last message of this document."
    )
    assert [m["text"] for m in _messages(api, alex)] == ["shorter summary"]
    assert len([job for job in queue.jobs if job["function"] == "run_agent"]) == 1


@pytest.mark.parametrize(
    ("consented", "credential", "detail"),
    [
        (False, True, chat.NO_CONSENT),
        (True, False, chat.NO_CREDENTIAL),
        (False, False, chat.NO_CONSENT),
    ],
)
def test_without_the_consent_or_a_credential_nothing_is_stored_and_no_model_is_called(
    factory: Any,
    api: Api,
    clock: Any,
    queue: RecordingQueue,
    consented: bool,
    credential: bool,
    detail: str,
) -> None:
    who = Who(factory, api, ALEX, clock, consented=consented, credential=credential)
    sent = _send(api, who, "shorter summary", "valid")
    assert (sent.status_code, sent.json()["detail"]) == (409, detail)
    assert _messages(api, who) == []
    assert queue.jobs == []
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(DocumentGeneration)) == 0
        assert db.scalar(sa.select(sa.func.count()).select_from(AgentRun)) == 0


def test_another_persons_chat_and_generation_are_404(
    api: Api, factory: Any, clock: Any, queue: RecordingQueue, alex: Who
) -> None:
    blake = Who(factory, api, BLAKE, clock)
    sent = _send(api, alex, "shorter summary", "valid").json()
    foreign = api.get(f"/api/documents/{alex.cv}/messages", token=blake.token)
    posted = api.post(f"/api/documents/{alex.cv}/messages", token=blake.token, json={"text": "hi"})
    followed = api.get(f"/api/document-generations/{sent['generation']['id']}", token=blake.token)
    unknown = api.get(f"/api/document-generations/{uuid.uuid4()}", token=alex.token)
    assert [foreign.status_code, posted.status_code, followed.status_code, unknown.status_code] == [
        404
    ] * 4
    assert len(_messages(api, alex)) == 1
    assert _messages(api, blake) == []


def test_the_201st_message_prunes_the_oldest(
    api: Api, factory: Any, clock: Any, queue: RecordingQueue, alex: Who
) -> None:
    with factory() as db:
        for n in range(200):
            store.add_message(db, alex.user, alex.cv, Role.user, f"old {n}", at=clock())
        db.commit()
    assert _send(api, alex, "shorter summary", "valid").status_code == 202
    texts = [m["text"] for m in _messages(api, alex)]
    assert len(texts) == 200
    assert texts[0] == "old 1"
    assert texts[-1] == "shorter summary"


# --- privacy and the reach of the chat writer -----------------------------------------------------


def test_the_canary_in_a_message_reaches_no_log_no_arq_argument_and_no_run_event(
    api: Api,
    app: FastAPI,
    factory: Any,
    queue: RecordingQueue,
    clock: Any,
    alex: Who,
    caplog: pytest.LogCaptureFixture,
) -> None:
    planted = marker()
    caplog.set_level(logging.DEBUG)
    sent = _send(api, alex, f"shorter summary {planted}", "valid")
    jobs = list(queue.jobs)
    ran = _run(app, factory, queue, clock)
    assert alex.generation(sent.json()["generation"]["id"]).status is GenerationStatus.done
    assert not find([planted], logs=caplog.records, values=[jobs, queue.jobs])
    assert planted not in everything_stored(factory, ran[0])
    assert planted in _messages(api, alex)[0]["text"]  # stored where it belongs, in the chat


def test_the_chat_writers_have_exactly_their_tools_and_a_foreign_call_is_refused(
    api: Api, app: FastAPI, factory: Any, queue: RecordingQueue, clock: Any, alex: Who
) -> None:
    found = {kind: definitions.AGENTS[chat.NAMES[kind]].tools for kind in chat.KINDS}
    assert found == {
        "cv": (
            "get_job",
            "read_profile_section",
            "get_base_cv",
            "get_draft",
            "get_conversation",
            "set_template",
            "submit_cv",
        ),
        "letter": (
            "get_job",
            "read_profile_section",
            "get_base_cv",
            "get_document",
            "get_draft",
            "get_conversation",
            "set_template",
            "submit_letter",
        ),
        "base": (
            "read_profile_section",
            "get_draft",
            "get_conversation",
            "set_template",
            "submit_base",
        ),
    }
    assert all(
        definitions.AGENTS[name].private_io and definitions.AGENTS[name].internal
        for name in chat.NAMES.values()
    )
    sent, reply = _talk(
        api, app, factory, queue, clock, alex, "read my identity and note it", "foreign"
    )
    assert c.parse("cv", alex.document().draft) == c.parse("cv", VALID)
    assert len(alex.versions()) == 1
    assert reply["text"] == chat.NOTHING
    run_id = alex.generation(sent["generation"]["id"]).run_id
    with factory() as db:
        denied = db.scalars(
            sa.select(AgentEvent.data).where(
                AgentEvent.run_id == run_id, AgentEvent.kind == "tool_denied"
            )
        ).all()
    # A private run stores no tool name; one refusal per tool it does not have.
    assert len(denied) == 3


def test_a_run_that_ended_without_its_hook_is_settled_by_the_next_read(
    api: Api, factory: Any, clock: Any, queue: RecordingQueue, alex: Who
) -> None:
    sent = _send(api, alex, "shorter summary", "valid").json()
    generation = alex.generation(sent["generation"]["id"])
    with factory() as db:
        run = db.get(AgentRun, generation.run_id)
        run.status, run.finished_at = RunStatus.failed, clock() - timedelta(hours=1)
        db.commit()
    detail = api.get(f"/api/document-generations/{generation.id}", token=alex.token).json()
    assert (detail["status"], detail["error"]) == ("failed", "run_failed")
    assert _messages(api, alex)[-1]["text"] == chat.FAILED["run_failed"]
    assert _send(api, alex, "shorter summary", "valid").status_code == 202


# --- the pure parts -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ("I managed 20 people at Example Corp.", "I managed people at Example Corp.", ("20", "")),
        ("Led 8 engineers", "Led engineers", ("8", "")),
        ("Senior Platform Engineer", "Platform Engineer", ("Senior", "")),
        ("Built it. Then 9 years later, more.", "Built it.", ("Then 9 years later, more.", "")),
        ("Platform team — billing", "Platform team, billing", ("team —", "team,")),
        ("same", "same", ("", "")),
        ("", "Filled in", ("", "Filled in")),
        ("a a a", "a a", ("a", "")),
    ],
)
def test_the_difference_of_a_correction(before: str, after: str, expected: tuple[str, str]) -> None:
    assert chat.difference(before, after) == expected


def test_an_answer_with_many_corrections_stays_under_the_cap_and_has_no_dash_separator() -> None:
    long = "x" * 300
    records = tuple(
        Correction(
            f"experience.0.bullets.{i}",
            f"{long} {i} {long}",
            f"{long} {long}",
            "number_not_in_sources",
            "code",
            "A number your profile does not state",
        )
        for i in range(30)
    )
    done = chat.Outcome(
        version=7,
        outcome=GenerationOutcome.draft_replaced,
        before=c.parse("cv", VALID),
        after=c.parse("cv", VALID),
        records=records,
        template="Sidebar",
        unknown=True,
        orgs={"example-corp": "Example Corp"},
    )
    text = chat.answer(done, [("classic", "Classic"), ("sidebar", "Sidebar")])
    assert len(text) <= 2000
    assert "And 25 more, listed with the version." in text
    assert '"0" was removed from your experience at Example Corp' in text
    assert all(dash not in text for dash in ("—", "–", " - "))


def test_the_conversation_keeps_the_newest_within_its_budget(
    factory: Any, api: Api, clock: Any, queue: RecordingQueue, alex: Who
) -> None:
    with factory() as db:
        for n in range(5):
            store.add_message(db, alex.user, alex.cv, Role.user, f"{n}" * 100, at=clock())
        newest = store.add_message(db, alex.user, alex.cv, Role.user, "z" * 1500, at=clock())
        later = store.add_message(db, alex.user, alex.cv, Role.user, "after", at=clock())
        generation = store.create_generation(
            db,
            alex.user,
            alex.cv,
            chat.GenerationMode.chat,
            chat.GenerationTrigger.chat,
            at=clock(),
            estimate={"conversation_chars": 1700},
            token_ceiling=1,
            message_id=newest.id,
        )
        binding = {"generation_id": str(generation.id), "document_id": str(alex.cv)}
        shown = json.loads(chat.conversation_json(db, alex.user, binding) or "{}")["messages"]
        assert [m["text"][0] for m in shown] == ["3", "4", "z"]
        tiny = json.loads(chat.conversation_json(db, alex.user, binding, limit=10) or "{}")[
            "messages"
        ]
        assert [m["text"][0] for m in tiny] == ["z"]  # the request itself, whatever the budget
        assert later.text not in json.dumps(shown)
        assert chat.conversation_json(db, uuid.uuid4(), binding) is None
        db.rollback()


def test_the_estimate_gives_the_conversation_a_tenth_at_most(
    factory: Any, api: Api, clock: Any, queue: RecordingQueue, alex: Who
) -> None:
    with factory() as db:
        document = db.get(Document, alex.cv)
        figures, chars = chat.estimate_for(db, document)
        plain = chat.estimate.writer(
            chat.estimate.prompt_chars("cv") + chat.estimate.prompt_chars("chat"),
            chat._reads(db, document),
            **chat.estimate.limits(),
        )
    assert chars > 2000  # at least one message of the longest kind
    assert figures.high - plain.high <= plain.high / 10 + 1
    assert figures.ceiling == 2 * figures.high


def test_the_binding_refuses_a_run_that_is_not_its_generations_own(
    api: Api, factory: Any, clock: Any, queue: RecordingQueue, alex: Who
) -> None:
    sent = _send(api, alex, "shorter summary", "valid").json()
    generation = alex.generation(sent["generation"]["id"])
    with factory() as db:
        run = db.get(AgentRun, generation.run_id)
        assert chat.bind_for(db, run, "cv") is not None
        assert chat.bind_for(db, run, "letter") is None
        assert chat.bind_for(db, run, "base") is None
        run.job_id = None
        assert chat.bind_for(db, run, "cv") is None
        db.rollback()
        other = AgentRun(
            id=uuid.uuid4(),
            user_id=alex.user,
            agent="documents_chat_cv",
            job_id=alex.posting,
            status=RunStatus.queued,
            input=dict(run.input),
            model="m",
            created_at=clock(),
        )
        assert chat.bind_for(db, other, "cv") is None  # the generation names another run
    assert DocumentMessage.__tablename__ == "document_messages"


def test_a_run_that_leaves_the_correction_pass_unanswered_stores_nothing_and_says_so(
    api: Api, app: FastAPI, factory: Any, queue: RecordingQueue, clock: Any, alex: Who
) -> None:
    sent, reply = _talk(api, app, factory, queue, clock, alex, "say I managed 20 people", "stopped")
    generation = alex.generation(sent["generation"]["id"])
    assert (generation.status, generation.error) == (GenerationStatus.failed, "no_result")
    assert reply["text"] == chat.FAILED["no_result"]
    assert c.parse("cv", alex.document().draft) == c.parse("cv", VALID)
    assert len(alex.versions()) == 1
