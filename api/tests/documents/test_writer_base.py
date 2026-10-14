"""D9: the base CV writer end to end on the fake backend (no model, no key): the definition, a
base CV built from the profile whose claims pass the fact check (no correction) and whose fixed
block is the profile's (copied by code, whatever the writer sent), an invented team size the
writer keeps after the correction pass (removed by code, recorded), the bound run (another
person's, a run with a posting), and the canary planted in the facts. Real Postgres at head."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import definitions
from app.agents.models import AgentRun
from app.documents import consent, store, writer_base, writers  # noqa: F401 (registers)
from app.documents import content as c
from app.documents.models import (
    BaseSource,
    Document,
    DocumentGeneration,
    GenerationOutcome,
    GenerationStatus,
    GenerationWait,
    Origin,
)
from tests.agents.g6_helpers import everything_stored
from tests.documents.canary import find, marker
from tests.documents.store_support import AT, USER_A, USER_B, db, engine, factory, migrated_url
from tests.documents.writer_support import (
    Person,
    document_row,
    execute,
    generation_row,
    person,
    versions,
)

__all__ = ["db", "engine", "factory", "migrated_url"]


def printed(content: dict[str, Any]) -> str:
    """Every printed text of a base CV, joined."""
    return "\n".join(text for _, text in c.text_fields(c.parse("base", content)))


def _build(who: Person, script: str, *, consented: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
    """A base CV built from the profile and its run started: (document id, generation id)."""
    with who.factory() as db:
        if consented:
            consent.record(db, who.user, AT)
        built = writer_base.build_from_profile(db, who.user, at=AT)
        assert built.generation is not None
        runs = writer_base.start_ready(db, who.user, at=AT, script=script, settings=who.settings)
        db.commit()
        document_id, generation_id = built.document.id, built.generation.id
    for run_id in runs:
        execute(who, run_id)
    return document_id, generation_id


def test_the_definition_is_internal_bound_private_and_priced_by_credential() -> None:
    agent = writer_base.BASE_WRITER
    assert definitions.get(writer_base.NAME, "test") is agent
    assert agent.internal
    assert agent.private_io
    assert not agent.live_text
    assert agent.bind is writer_base.bind_generation
    assert agent.tools == ("read_profile_section", "submit_base")
    assert agent.model_by_credential == {"anthropic_api_key": "claude-sonnet-5-5"}
    assert agent.max_turns == 10
    assert agent.wall_time_s == 300


def test_a_base_cv_from_the_profile_passes_the_fact_check_and_keeps_the_profiles_fixed_block(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = person(factory, migrated_url, USER_A)
    document_id, generation_id = _build(who, "documents_base_valid")
    generation = generation_row(factory, generation_id)
    assert generation.status is GenerationStatus.done, generation.error
    assert generation.outcome is GenerationOutcome.draft_replaced
    assert generation.corrections == 0  # every claim is the profile's
    (version,) = versions(factory, document_id)
    assert version.origin is Origin.generated
    assert version.corrections == []
    document = document_row(factory, document_id)
    assert document.draft == version.content
    fixed = document.draft["fixed"]
    assert [e["organisation"] for e in fixed["employers"]] == ["Example Corp", "Sample Labs"]
    assert [s["school"] for s in fixed["schools"]] == ["Example Institute of Technology"]
    assert [x["name"] for x in fixed["languages"]] == ["English", "French"]
    assert "Moved 40 services to Kubernetes" in printed(document.draft)
    assert [job["function"] for job in who.queue.jobs] == ["render_document"]


def test_an_invented_team_size_kept_by_the_writer_is_removed_by_code(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = person(factory, migrated_url, USER_A)
    document_id, generation_id = _build(who, "documents_base_invented")
    generation = generation_row(factory, generation_id)
    assert generation.status is GenerationStatus.done, generation.error
    assert generation.corrections >= 1
    (version,) = versions(factory, document_id)
    assert "12 engineers" not in printed(version.content)
    assert any(record.get("by") == "code" for record in version.corrections)


def test_without_consent_nothing_starts_and_nothing_is_sent(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = person(factory, migrated_url, USER_A)
    with factory() as db:
        built = writer_base.build_from_profile(db, who.user, at=AT)
        assert writer_base.start_ready(db, who.user, at=AT, settings=who.settings) == []
        db.commit()
        assert built.generation is not None
        generation_id = built.generation.id
    generation = generation_row(factory, generation_id)
    assert generation.status is GenerationStatus.waiting
    assert generation.wait_reason is GenerationWait.consent
    assert generation.run_id is None
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(AgentRun)) == 0


def test_the_run_is_bound_to_its_own_base_cv_only(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    alex = person(factory, migrated_url, USER_A)
    blake = person(factory, migrated_url, USER_B)
    with factory() as db:
        consent.record(db, alex.user, AT)
        built = writer_base.build_from_profile(db, alex.user, at=AT)
        (run_id,) = writer_base.start_ready(db, alex.user, at=AT, settings=alex.settings)
        db.commit()
        assert built.generation is not None
    with factory() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        binding = writer_base.bind_generation(db, run)
        assert binding is not None
        assert binding["document_id"] == str(built.document.id)
        run.user_id = blake.user  # another person's run
        assert writer_base.bind_generation(db, run) is None
        db.rollback()
    with factory() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        run.job_id = blake.posting  # a run that names a posting
        assert writer_base.bind_generation(db, run) is None
        db.rollback()
    with factory() as db:
        generation = db.get(DocumentGeneration, built.generation.id)
        assert generation is not None
        generation.status = GenerationStatus.cancelled
        db.flush()
        run = db.get(AgentRun, run_id)
        assert run is not None
        assert writer_base.bind_generation(db, run) is None
        db.rollback()


def test_a_second_start_of_the_same_generation_starts_nothing(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = person(factory, migrated_url, USER_A)
    with factory() as db:
        consent.record(db, who.user, AT)
        writer_base.build_from_profile(db, who.user, at=AT)
        first = writer_base.start_ready(db, who.user, at=AT, settings=who.settings)
        db.commit()
    with factory() as db:
        assert writer_base.start_ready(db, who.user, at=AT, settings=who.settings) == []
        db.commit()
    assert len(first) == 1


def test_the_canary_in_the_facts_reaches_no_log_and_no_event(
    factory: sessionmaker[Session], migrated_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    planted = marker()
    who = person(factory, migrated_url, USER_A, facts_extra=f"Planted fact {planted}.")
    caplog.set_level(logging.DEBUG)
    with factory() as db:
        consent.record(db, who.user, AT)
        built = writer_base.build_from_profile(db, who.user, at=AT)
        (run_id,) = writer_base.start_ready(
            db, who.user, at=AT, script="documents_base_valid", settings=who.settings
        )
        db.commit()
        assert built.generation is not None
    execute(who, run_id)
    assert generation_row(factory, built.generation.id).status is GenerationStatus.done
    stored = everything_stored(factory, run_id)
    assert planted not in stored
    assert "payments team" not in stored
    assert not find([planted], logs=caplog.records, values=[who.queue.jobs])
    assert "payments team" not in caplog.text


def test_the_skeleton_of_an_empty_profile_is_a_base_cv_that_renders(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = person(factory, migrated_url, USER_A)
    with factory() as db:
        db.execute(
            sa.text("UPDATE profiles SET sections = '{}'::jsonb WHERE user_id = :u"),
            {"u": who.user},
        )
        built = writer_base.build_from_profile(db, who.user, at=AT, source=BaseSource.profile)
        db.commit()
        assert built.generation is None
        found = store.get(db, who.user, built.document.id)
        assert isinstance(found, Document)
        assert found.draft["notes"] == writer_base.FILL_PROFILE
        assert found.base_source is BaseSource.profile
