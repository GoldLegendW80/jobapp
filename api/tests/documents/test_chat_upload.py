"""D17: the chat of every document whose sources include an uploaded CV is fact checked against
the file, as the document's writer was (SPEC-documents.md, "Being careful", step 3): a base CV
converted from an upload, whose chat writer also reads the file (`get_upload_text`, bound in code
to the base CV's own file), and a posting's CV and letter written from that base CV.

The draft sent back unchanged by a chat, or with its summary shortened, stores no correction and
keeps every claim of the file (the review's reproduction: "Moved 30 services" became "Moved
services"); with the file deleted in ingestion, before the message or during its run, the same
draft goes to the correction pass, and the base CV stays; a claim neither the file nor the
profile states is still removed by code. Real Postgres at head."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents.models import AgentRun, RunStatus
from app.agents.tools import Scope
from app.documents import chat, estimate, store, submit, writer_base
from app.documents import content as c
from app.documents.models import Author, Document, Kind, Origin
from app.ingestion.models import IngestionDocument
from tests.documents.store_support import AT, USER_A, USER_B, engine, factory, migrated_url
from tests.documents.test_writer_base_upload import MORGAN, add_upload, convert, hooks
from tests.documents.writer_support import Person, document_row, person, versions

__all__ = ["engine", "factory", "hooks", "migrated_url"]

KINDS = ("base", "cv", "letter")
CLAIM = "Moved 30 services to Kubernetes with Terraform."
INVENTED = "Cut the support team's response time by 83%."
# A letter of the posting written from the uploaded CV: its claims are the file's.
LETTER = {
    "schema": 1,
    "target": "Lumen Example, Senior Platform Engineer",
    "headline": "Platform engineering for a team that ships every day",
    "recipient": "Hiring team, Lumen Example",
    "subject": "A platform that other engineers rely on",
    "salutation": "Dear hiring team,",
    "paragraphs": [
        "I run the deployment platform of the billing team at Quillmere Example Works, from "
        "the first commit to production.",
        "You ask for Python and Kubernetes. At Quillmere Example Works I moved 30 services to "
        "Kubernetes with Terraform.",
        "I would enjoy talking with your team about your platform.",
    ],
    "closing": "Kind regards,",
}


@dataclass(frozen=True)
class World:
    who: Person
    upload: uuid.UUID
    base: uuid.UUID  # the base CV converted from the upload
    document: uuid.UUID  # the document the chat is on
    draft: dict[str, Any]  # its draft, as get_draft gives it


def _world(factory: sessionmaker[Session], migrated_url: str, kind: str) -> World:
    """Morgan's CV uploaded and converted without a correction; for a CV or a letter, the
    posting's CV written from it, and the base CV's own "30 services" edited away by hand, so the
    file alone states the claim."""
    who = person(factory, migrated_url, USER_A)
    upload = add_upload(factory, who.user, MORGAN)
    base, _ = convert(who, upload, "documents_base_upload_valid")
    (converted,) = versions(factory, base)
    assert converted.corrections == []
    assert CLAIM in printed("base", converted.content)
    if kind == "base":
        return World(who, upload, base, base, document_row(factory, base).draft)
    trimmed = json.loads(json.dumps(converted.content).replace(" 30 services", " services"))
    with factory() as db:
        own = db.get(Document, base)
        assert own is not None
        store.save_draft(db, who.user, base, trimmed, own.draft_revision, Author.user, at=AT)
        cv = store.get_or_create(db, who.user, who.posting, Kind.cv, at=AT, base_document_id=base)
        written = {k: v for k, v in converted.content.items() if k != "fixed"}
        store.save_draft(db, who.user, cv.id, written, cv.draft_revision, Author.writer, at=AT)
        document = cv
        if kind == "letter":
            document = store.get_or_create(db, who.user, who.posting, Kind.letter, at=AT)
            store.save_draft(
                db, who.user, document.id, LETTER, document.draft_revision, Author.writer, at=AT
            )
        db.commit()
        document_id = document.id
    assert "30" not in printed("base", document_row(factory, base).draft)
    return World(who, upload, base, document_id, document_row(factory, document_id).draft)


def printed(kind: str, content: Mapping[str, Any]) -> str:
    return "\n".join(text for _, text in c.text_fields(c.parse(kind, content)))  # type: ignore[arg-type]


def _chat(world: World, kind: str, document: uuid.UUID | None = None) -> tuple[uuid.UUID, Any]:
    """A message in the chat of `document`, its run started (not executed) and bound."""
    who = world.who
    with who.factory() as db:
        posted = chat.post(
            db,
            who.user,
            document or world.document,
            "shorter summary",
            at=AT,
            settings=who.settings,
        )
        db.commit()
        run = db.get(AgentRun, posted.run_id)
        assert run is not None
        binding = chat.bind_for(db, run, kind)  # type: ignore[arg-type]
    assert binding is not None
    return posted.run_id, binding


def _submit(world: World, kind: str, run_id: uuid.UUID, binding: Any, data: object) -> Any:
    """One submit of the chat writer of `kind`, as its tool runs it."""
    pytest.importorskip("claude_agent_sdk")
    from app.documents import writer_base_tools, writer_tools

    scope = Scope(world.who.factory, world.who.user, run_id=run_id, binding=binding)
    if kind == "base":
        return writer_base_tools._submit(scope, data)
    return writer_tools._submit(scope, kind, data)  # type: ignore[arg-type]


def _with_invented(kind: str, draft: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if kind == "letter":
        paragraphs = list(draft["paragraphs"])
        paragraphs[2] = f"{INVENTED} {paragraphs[2]}"
        return {**draft, "paragraphs": paragraphs}, "paragraphs.2"
    experience = [dict(entry) for entry in draft["experience"]]
    experience[1]["bullets"] = [*experience[1]["bullets"], INVENTED]
    return {**draft, "experience": experience}, "experience.1.bullets.1"


def _settle(world: World, run_id: uuid.UUID) -> Any:
    chat.settle(
        world.who.factory, run_id, RunStatus.done, submit.take(run_id), chat.take(run_id), AT
    )
    return versions(world.who.factory, world.document)[-1]


def _shorter(kind: str, draft: dict[str, Any]) -> dict[str, Any]:
    """The change "shorter summary" asks for, every other field as the draft has it."""
    if kind == "letter":
        paragraphs = [*draft["paragraphs"][:2], "I would enjoy talking with your team."]
        return {**draft, "paragraphs": paragraphs}
    return {**draft, "summary": "Platform engineer at Quillmere Example Works."}


@pytest.mark.parametrize("shorter", [False, True], ids=["unchanged", "shorter"])
@pytest.mark.parametrize("kind", KINDS)
def test_a_chat_sends_the_draft_back_and_no_claim_of_the_file_is_cut(
    kind: str,
    shorter: bool,
    factory: sessionmaker[Session],
    migrated_url: str,
    hooks: list[uuid.UUID],
) -> None:
    world = _world(factory, migrated_url, kind)
    sent = _shorter(kind, world.draft) if shorter else world.draft
    run_id, binding = _chat(world, kind)
    assert _submit(world, kind, run_id, binding, sent) == (submit.STORED, False)
    assert _submit(world, kind, run_id, binding, sent) == (submit.ALREADY, False)
    latest = _settle(world, run_id)
    assert (latest.origin, latest.corrections) == (Origin.chat, [])
    draft = document_row(factory, world.document).draft
    assert c.parse(kind, draft) == c.parse(kind, sent)  # type: ignore[arg-type]
    assert "moved 30 services to" in printed(kind, draft).lower()


@pytest.mark.parametrize("during", [False, True], ids=["before", "during"])
@pytest.mark.parametrize("kind", KINDS)
def test_with_the_file_deleted_the_same_draft_goes_to_the_correction_pass(
    kind: str,
    during: bool,
    factory: sessionmaker[Session],
    migrated_url: str,
    hooks: list[uuid.UUID],
) -> None:
    # Deleted in ingestion before the message, or while its run works: the profile alone.
    world = _world(factory, migrated_url, kind)

    def delete() -> None:
        with factory() as db:
            db.execute(sa.delete(IngestionDocument).where(IngestionDocument.id == world.upload))
            db.commit()

    if not during:
        delete()
    run_id, binding = _chat(world, kind)
    if during:
        delete()
    answer, error = _submit(world, kind, run_id, binding, world.draft)
    assert error
    assert answer.startswith("Not stored yet: the fact check found claims")
    assert "30" in answer
    assert "(number_not_in_sources)" in answer
    assert document_row(factory, world.base).source_document_id is None


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_neither_the_file_nor_the_profile_states_is_still_corrected(
    kind: str, factory: sessionmaker[Session], migrated_url: str, hooks: list[uuid.UUID]
) -> None:
    world = _world(factory, migrated_url, kind)
    with factory() as db:
        profile = db.execute(
            sa.text("SELECT sections::text FROM profiles WHERE user_id = :u"),
            {"u": world.who.user},
        ).scalar_one()
    assert "83" not in MORGAN
    assert "83" not in profile
    sent, path = _with_invented(kind, world.draft)
    run_id, binding = _chat(world, kind)
    answer, error = _submit(world, kind, run_id, binding, sent)
    assert error
    assert f"- {path}: " in answer
    assert "83" in answer
    assert "30 services" not in answer  # the file's claim is not asked about
    assert _submit(world, kind, run_id, binding, sent) == (submit.STORED, False)
    latest = _settle(world, run_id)
    assert [(r["field_path"], r["by"]) for r in latest.corrections] == [(path, "code")]
    assert "83" not in latest.corrections[0]["after"]
    draft = document_row(factory, world.document).draft
    assert "83" not in printed(kind, draft)
    assert "moved 30 services to" in printed(kind, draft).lower()


def test_the_base_chat_reads_its_own_file_only_and_its_estimate_counts_it(
    factory: sessionmaker[Session], migrated_url: str, hooks: list[uuid.UUID]
) -> None:
    world = _world(factory, migrated_url, "base")
    who = world.who
    run_id, binding = _chat(world, "base")
    assert binding["upload_id"] == str(world.upload)
    _, from_profile = _chat(world, "base", who.base)
    assert from_profile["upload_id"] is None
    blake = person(factory, migrated_url, USER_B)
    with factory() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        run.user_id = blake.user
        assert chat.bind_for(db, run, "base") is None
        db.rollback()
    with factory() as db:
        converted, built = db.get(Document, world.base), db.get(Document, who.base)
        assert converted is not None
        assert built is not None
        profile = estimate.profile_chars(db, who.user)
        assert chat._reads(db, converted) == (
            profile + len(json.dumps(converted.draft)) + len(MORGAN)
        )
        assert chat._reads(db, built) == profile + len(json.dumps(built.draft))
        assert writer_base.upload_text(db, who.user, world.upload) == MORGAN
    pytest.importorskip("claude_agent_sdk")
    from app.documents import writer_base_tools as tools

    text, error = tools._read_upload(Scope(factory, who.user, binding=binding))
    assert not error
    assert "Moved 30 services to Kubernetes" in text
    assert tools._read_upload(Scope(factory, who.user, binding=from_profile)) == (
        tools.NO_UPLOAD,
        True,
    )


@pytest.mark.parametrize("state", ["secret", "reading", "failed", "clipped"])
@pytest.mark.parametrize("kind", KINDS)
def test_a_file_the_base_chat_cannot_read_is_no_source_of_any_chat(
    kind: str,
    state: str,
    factory: sessionmaker[Session],
    migrated_url: str,
    hooks: list[uuid.UUID],
) -> None:
    # The posting's CV and letter read the file as the base CV's chat does: the clipped head of
    # a file still readable (`writer_base.upload_text`), never the whole text of any file.
    world = _world(factory, migrated_url, kind)
    changes = {
        "secret": "secret_warning = true, secret_confirmed_at = NULL",
        "reading": "text_status = 'pending'",
        "failed": "text_status = 'failed'",
        "clipped": "text = repeat('x ', :half) || text",
    }
    with factory() as db:
        db.execute(
            sa.text(f"UPDATE ingestion_documents SET {changes[state]} WHERE id = :d"),
            {"d": world.upload, "half": writer_base.UPLOAD_MAX_CHARS // 2},
        )
        db.commit()
    run_id, binding = _chat(world, kind)
    answer, error = _submit(world, kind, run_id, binding, world.draft)
    assert error
    assert answer.startswith("Not stored yet: the fact check found claims")
    assert "(number_not_in_sources)" in answer


@pytest.mark.parametrize("uploaded", [True, False], ids=["from an upload", "from the profile"])
def test_a_base_chat_run_leaves_nothing_in_memory(
    uploaded: bool, factory: sessionmaker[Session], migrated_url: str, hooks: list[uuid.UUID]
) -> None:
    world = _world(factory, migrated_url, "base")
    document = world.base if uploaded else world.who.base
    run_id, binding = _chat(world, "base", document)
    draft = document_row(factory, document).draft
    answer, error = _submit(world, "base", run_id, binding, draft)
    if error:  # the correction pass, answered with the same draft: code removes the rest
        answer, error = _submit(world, "base", run_id, binding, draft)
    assert (answer, error) == (submit.STORED, False)
    assert run_id not in writer_base._FIXED
    chat.settle(factory, run_id, RunStatus.done, submit.take(run_id), chat.take(run_id), AT)
    assert run_id not in writer_base._FIXED
    assert run_id not in submit._RUNS
    assert run_id not in chat._ASKED
