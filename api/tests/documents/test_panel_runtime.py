"""D29: the hiring panel end to end on the agents runtime with the fake backend (recorded
streams, no model, no key), on a real Postgres at head. No test sleeps or reads the clock.

A posting's CV written on a click opens its panel and queues its job instead of the letter; the
job runs the seven reviewers and the merge through `panel.RuntimeCall` (each its own agent run of
the person, on its recorded stream), the rebuild through the CV writer and `submit.handle` (an
invented percent the rebuild keeps is removed by the fact check's code and recorded), and the
screen again (REJECT before, PASS after); the letter then starts. The conversion of an uploaded
CV opens a panel of kind `base` against the target roles: its rebuild is a version of origin
`panel` whose fixed facts are the base CV's own, whatever the writer sent."""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents.definitions import FinalRun
from app.agents.models import AgentRun, RunStatus
from app.documents import (  # noqa: F401 (writers registers the writers and the panel)
    consent,
    design_capture,
    panel,
    writers,
)
from app.documents import content as c
from app.documents.models import (
    DocumentGeneration,
    DocumentPanel,
    DocumentVersion,
    GenerationMode,
    Kind,
    Origin,
)
from tests.documents.store_support import AT, USER_A, engine, factory, migrated_url
from tests.documents.test_writer_base_upload import MORGAN, add_upload, convert
from tests.documents.writer_support import Person, execute, person, start

__all__ = ["engine", "factory", "migrated_url"]

SCREEN_TWICE = {
    "documents_panel_screen": ["documents_panel_screen_reject", "documents_panel_screen"]
}


def with_panel(who: Person) -> Person:
    settings = who.settings.model_copy(update={"documents_panel_enabled": True})
    return dataclasses.replace(who, settings=settings)


def panel_of(factory: sessionmaker[Session], document_id: uuid.UUID) -> DocumentPanel:
    with factory() as db:
        found = db.scalar(sa.select(DocumentPanel).where(DocumentPanel.document_id == document_id))
        assert found is not None
        db.expunge(found)
        return found


def versions_of(factory: sessionmaker[Session], document_id: uuid.UUID) -> list[DocumentVersion]:
    with factory() as db:
        found = list(
            db.scalars(
                sa.select(DocumentVersion)
                .where(DocumentVersion.document_id == document_id)
                .order_by(DocumentVersion.number)
                .options(sa.orm.undefer(DocumentVersion.content))
            )
        )
        for one in found:
            db.expunge(one)
        return found


def printed(kind: str, content: dict[str, Any]) -> str:
    return "\n".join(text for _, text in c.text_fields(c.parse(kind, content)))


@pytest.fixture
def no_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    async def skip(final: FinalRun, generation_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(design_capture, "enqueue", skip)


def test_a_cv_goes_through_the_whole_panel_on_the_runtime_then_its_letter_starts(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = with_panel(person(factory, migrated_url, USER_A))
    with factory() as db:
        consent.record(db, who.user, AT)  # so the letter that follows can start
        db.commit()
    document, generation, run = start(who, "documents_cv_valid")
    execute(who, run)
    queued = [job for job in who.queue.jobs if job["function"] == panel.FUNCTION]
    assert [job["function"] for job in who.queue.jobs if job["function"] == "run_agent"] == []
    [job] = queued
    panel_id = uuid.UUID(job["args"][0])
    assert panel_of(factory, document).status == "running"

    status = asyncio.run(panel.job(who.context(), panel_id, scripts=SCREEN_TWICE))
    assert status == "done"
    found = panel_of(factory, document)
    assert {key for key, value in found.reviews.items() if "missing" not in value} == {
        role.key for role in panel.ROLES
    }
    assert (found.screen_before, found.screen_after) == ("REJECT", "PASS")
    assert [m["number"] for m in found.mistakes] == [1, 2]
    assert found.questions == [
        {"question": "How many services did you move to Kubernetes?", "section": "experience"}
    ]
    assert (found.reviewed_version, found.rebuilt_version) == (1, 2)
    assert len(found.run_ids) == len(panel.ROLES) + 3  # the merge, the rebuild, the screen again

    first, rebuilt = versions_of(factory, document)
    assert (first.origin, rebuilt.origin) == (Origin.generated, Origin.panel)
    # The rebuild went through submit.handle: the invented percent it kept is cut by code.
    [record] = rebuilt.corrections
    assert (record["by"], record["code"]) == ("code", "percent_not_in_sources")
    assert "[ADD" not in printed("cv", rebuilt.content).upper()

    with factory() as db:
        rebuild = db.scalar(
            sa.select(DocumentGeneration).where(DocumentGeneration.mode == GenerationMode.panel)
        )
        assert rebuild is not None
        assert rebuild.run_id in found.run_ids
        spent = panel.tokens(db, found)
        assert spent[0] > 0
        assert spent[1] > 0
        runs = db.scalars(sa.select(AgentRun).where(AgentRun.id.in_(found.run_ids))).all()
        assert {r.status for r in runs} == {RunStatus.done}
        assert {r.agent for r in runs} >= set(panel.AGENT_NAMES)
        assert all(r.user_id == who.user for r in runs)
        assert all(
            set(r.input) <= {"request", "token_budget", "script"}
            for r in runs
            if r.agent in panel.AGENT_NAMES
        )
        letter = db.scalar(
            sa.select(DocumentGeneration).where(
                DocumentGeneration.id != generation, DocumentGeneration.id != rebuild.id
            )
        )
        assert letter is not None  # the letter followed the CV's panel
    assert any(job["function"] == "run_agent" for job in who.queue.jobs)


def test_an_uploaded_cv_is_rebuilt_by_its_panel_with_its_fixed_facts_kept(
    factory: sessionmaker[Session], migrated_url: str, no_capture: None
) -> None:
    who = with_panel(person(factory, migrated_url, USER_A))
    upload = add_upload(factory, who.user, MORGAN)
    document, _ = convert(who, upload, "documents_base_upload_valid")
    [job] = [job for job in who.queue.jobs if job["function"] == panel.FUNCTION]
    found = panel_of(factory, document)
    assert found.kind == "base"
    status = asyncio.run(
        panel.job(
            who.context(),
            uuid.UUID(job["args"][0]),
            scripts={**SCREEN_TWICE, "rebuild": "documents_base_panel"},
        )
    )
    assert status == "done"
    first, rebuilt = versions_of(factory, document)
    assert (first.origin, rebuilt.origin) == (Origin.generated, Origin.panel)
    assert rebuilt.content["headline"] == "Platform Engineer for billing teams"
    # The writer sent "Head of Platform": the base CV's own fixed facts are kept by code.
    assert rebuilt.content["fixed"] == first.content["fixed"]
    assert rebuilt.content["fixed"]["employers"][0]["title"] == "Platform Engineer"
    assert panel_of(factory, document).rebuilt_version == 2
    with factory() as db:
        assert (
            db.scalar(
                sa.select(sa.func.count())
                .select_from(DocumentGeneration)
                .where(
                    DocumentGeneration.document_id == document,
                    DocumentGeneration.mode == GenerationMode.panel,
                )
            )
            == 1
        )


def test_the_reviewers_brief_names_the_job_and_the_company(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    """The first message a reviewer run reads is built in process from the brief: it names the
    posting's company and its role, never another reviewer's answer."""
    who = with_panel(person(factory, migrated_url, USER_A))
    document, _, run = start(who, "documents_cv_valid")
    execute(who, run)
    with factory() as db:
        loaded = panel.load(db, panel_of(factory, document).id)
        assert loaded is not None
    reader = panel.review_prompt(panel.ROLE_OF["reader_ceo"], loaded.subject)
    company = loaded.subject.brief["company"]["name"]
    assert f"the CEO of {company}" in reader
    assert loaded.subject.brief["text"]
    assert Kind.cv.value == loaded.kind
