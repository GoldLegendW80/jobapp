"""D12: the recorded streams the Playwright journeys name (`documents_base_journey`,
`documents_cv_journey`, `documents_letter_journey`) replayed for the invented person of the
journeys (`invented()` of web/e2e/documents-helpers.ts, "Morgan Exampleton"; its profile export is
`fixtures/profile_morgan_export.json`) and the posting the journeys add. The page's one click asks
for both kinds: the CV ends `done` with one version, then its letter follows on the letter stream
and ends `done` too. A stream that drifts from that profile ends `no_result` on the stack, where
only a timeout shows it; here the submit tool's refusal is printed. Real Postgres at head."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import credentials, fake_backend
from app.agents.crypto import Keyring
from app.agents.models import AgentRun, CredentialKind
from app.documents import (
    consent,
    estimate,
    generate,
    store,
    submit,
    writer_base,
    writer_cv,
    writer_letter,
    writers,  # noqa: F401 (registers the writers)
)
from app.documents.models import (
    DocumentGeneration,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    Kind,
    Origin,
)
from app.profile import service as profile_service
from app.profile.schemas import parse_document
from tests.agents.conftest import API_KEY, new_key
from tests.conftest import make_settings
from tests.documents.conftest import FIXTURES
from tests.documents.store_support import AT, db, engine, factory, migrated_url
from tests.documents.writer_support import (
    Person,
    execute,
    generation_row,
    run_row,
    versions,
)

__all__ = ["db", "engine", "factory", "migrated_url"]

# The posting of `invented()`, as `addPosting` sends it.
COMPANY = "Brindle Example Labs"
TITLE = "Senior Platform Engineer"
POSTING = (
    "Brindle Example Labs is hiring a Senior Platform Engineer.\n\n"
    "You will run our deployment platform on Kubernetes and write backend services in Python."
)


@pytest.fixture
def morgan(factory: sessionmaker[Session], migrated_url: str) -> Person:
    """The journey's person: the imported profile, a credential, the consent, the base CV built
    from the profile on `documents_base_journey`, and the posting."""
    key = new_key()
    settings = make_settings(migrated_url, credentials_key=key, agents_fake_backend=True)
    keyring = Keyring.from_text(key)
    export = parse_document((FIXTURES / "profile_morgan_export.json").read_bytes())
    with factory() as db:
        user = db.execute(
            sa.text(
                "INSERT INTO users (email, name, password_hash) VALUES (:e, :n, 'h') RETURNING id"
            ),
            {"e": "e2e-documents-morgan@example.org", "n": "Morgan Exampleton"},
        ).scalar_one()
        posting = db.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title, location, posting_text) "
                "VALUES (:u, :s, :c, :t, 'Nantes, France', :p) RETURNING id"
            ),
            {
                "u": user,
                "s": f"brindle-{uuid.uuid4().hex[:8]}",
                "c": COMPANY,
                "t": TITLE,
                "p": POSTING,
            },
        ).scalar_one()
        profile_service.import_profile(db, user, export.stored_sections(), None, None, AT)
        credentials.save(db, keyring, user, CredentialKind.anthropic_api_key, API_KEY, AT)
        consent.record(db, user, AT)
        built = writer_base.build_from_profile(db, user, at=AT)
        assert built.generation is not None
        started = writer_base.start_ready(
            db, user, at=AT, script="documents_base_journey", settings=settings
        )
        db.commit()
        base = built.document.id
    who = Person(factory, settings, keyring, user, posting, base)
    for run_id in started:
        execute(who, run_id)
    assert [v.origin for v in versions(factory, base)] == [Origin.generated]
    return who


def _click(who: Person) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """What the page's one click does through the write route with `script: "journey"`: the CV's
    generation, which the letter follows, and its run. (document id, generation id, run id)"""
    with who.factory() as db:
        document = store.get_or_create(db, who.user, who.posting, Kind.cv, at=AT)
        figures = estimate.for_posting(db, who.user, who.posting, None)
        stored: dict[str, Any] = dict(estimate.as_json(figures))
        stored[generate.LETTER_FOLLOWS] = True
        stored[generate.LETTER_SCRIPT] = "documents_letter_journey"
        generation = generate.create(
            db,
            document,
            GenerationMode.posting,
            GenerationTrigger.click,
            None,
            stored,
            figures.ceiling,
            at=AT,
        )
        run = generate.start(
            db, generation, at=AT, script="documents_cv_journey", settings=who.settings
        )
        db.commit()
        return document.id, generation.id, run.id


def _submitted(script: str, tool: str) -> list[dict[str, Any]]:
    return [
        block["input"]
        for step in fake_backend.load(script)["steps"]
        for block in step.get("message", {}).get("message", {}).get("content", [])
        if block.get("name") == f"mcp__app__{tool}"
    ]


def _answer(who: Person, run: uuid.UUID, kind: str) -> tuple[str, bool]:
    """The submit tool's answer to the one document the journey stream of `kind` submits, in the
    context of its bound run: the refusal names each path and rule the stream breaks."""
    writer = writer_cv if kind == "cv" else writer_letter
    with who.factory() as db:
        binding = writer.bind_generation(db, db.get(AgentRun, run))
        assert binding is not None
        context_ = submit.context_for(db, who.user, binding, kind)
    [sent] = _submitted(f"documents_{kind}_journey", f"submit_{kind}")
    return submit.handle(context_, run, sent)


def _letter_run(who: Person) -> uuid.UUID:
    """The run of the letter that followed the CV (queued by the CV's end)."""
    [run] = [uuid.UUID(job["args"][0]) for job in who.queue.jobs if job["function"] == "run_agent"]
    return run


def test_each_journey_stream_is_stored_at_its_first_submit(morgan: Person) -> None:
    _, _, run = _click(morgan)
    text, error = _answer(morgan, run, "cv")
    assert (text, error) == (submit.STORED, False), text
    execute(morgan, run)
    text, error = _answer(morgan, _letter_run(morgan), "letter")
    assert (text, error) == (submit.STORED, False), text


def test_one_click_writes_the_cv_then_the_letter_on_the_journey_streams(morgan: Person) -> None:
    document, generation, run = _click(morgan)
    execute(morgan, run)
    row = generation_row(morgan.factory, generation)
    assert row.status is GenerationStatus.done, (row.status, row.error)
    [version] = versions(morgan.factory, document)
    assert version.generation_id == generation
    assert [r["employer_key"] for r in version.content["experience"]] == ["quillmere", "tarnwick"]
    assert run_row(morgan.factory, run).input["script"] == "documents_cv_journey"

    # The CV's end queued the letter's run, on the letter stream of the journey.
    letter_run = _letter_run(morgan)
    assert run_row(morgan.factory, letter_run).input["script"] == "documents_letter_journey"
    execute(morgan, letter_run)
    with morgan.factory() as db:
        [letter_generation] = db.scalars(
            sa.select(DocumentGeneration).where(
                DocumentGeneration.user_id == morgan.user,
                DocumentGeneration.document_id.not_in([document, morgan.base]),
            )
        ).all()
        assert letter_generation.status is GenerationStatus.done, letter_generation.error
        letter, followed = letter_generation.document_id, letter_generation.base_document_id
    assert followed == morgan.base  # the letter names the base CV the new CV was written from
    [written] = versions(morgan.factory, letter)
    assert written.origin is Origin.generated
    assert written.generation_id == letter_generation.id
