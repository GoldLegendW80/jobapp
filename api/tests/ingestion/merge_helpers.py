"""Helpers of the merge and review tests (G12): a run driven by the real engine whose steps
store the items a test names, each cited at its real place in the text of its document."""

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents.models import Proposal, ProposalStatus
from app.agents.models import RunStatus as AgentStatus
from app.config import Settings
from app.ingestion import items
from app.ingestion.models import IngestionDocument
from app.ingestion.runs_models import IngestionItem, IngestionRun, IngestionRunStep
from app.profile import schemas
from app.profile import service as profile_service
from tests.agents.run_helpers import now
from tests.ingestion import engine_helpers as eh
from tests.ingestion.extract_helpers import add_document

# One fact a step stores: (section, key or slot, value, the excerpt cited), then optionally the
# action and the flags of the stored row.
Fact = tuple[Any, ...]

ACME = {"organisation": "Acme Group", "title": "Engineer"}
CV = (
    "Alice Martin, alice@example.org.\n"
    "Engineer at Acme Group from March 2021 to June 2023.\n"
    "Speaks English at level C1.\n"
)
LETTER = (
    "My name is Alice Martin.\n"
    "I joined Acme Group as an Engineer in May 2021 and left in June 2023.\n"
    "English: C1.\n"
)


def employer(start: str, end: str | None = None, **more: Any) -> dict[str, Any]:
    return {**ACME, "start": start, "end": end, **more}


def save(factory: sessionmaker[Session], user: uuid.UUID, section: str, data: Any) -> int:
    """Save one section as the profile page does. Returns the new revision."""
    with factory() as db, db.begin():
        profile = profile_service.get_profile(db, user, now())
        valid = schemas.validate_section(section, data)
        saved = profile_service.save_section(db, user, section, valid, profile.revision, now())
        return int(saved.revision)


def revision(factory: sessionmaker[Session], user: uuid.UUID) -> int:
    with factory() as db:
        profile = profile_service.get_profile(db, user, now())
        db.commit()
        return int(profile.revision)


def store(
    factory: sessionmaker[Session],
    user: uuid.UUID,
    step: IngestionRunStep,
    section: str,
    key: str,
    value: Any,
    excerpt: str,
    action: str = "add",
    flags: Sequence[str] = (),
) -> None:
    """One row as the write tool stores it: the cleaned value, its key and hash, and a citation
    at the place of `excerpt` in the text of the step's document."""
    item = items.build(section, key, action, value)
    with factory() as db, db.begin():
        text = db.scalar(
            sa.select(IngestionDocument.text).where(IngestionDocument.id == step.document_id)
        )
        start = (text or "").index(excerpt)
        db.add(
            IngestionItem(
                user_id=user,
                document_id=step.document_id,
                step_id=step.id,
                section=item.section,
                key=item.key,
                action=item.action.value,
                value=item.value,
                value_hash=item.value_hash,
                citations=[{"start": start, "end": start + len(excerpt), "excerpt": excerpt}],
                flags=list(flags),
            )
        )


def begin(
    factory: sessionmaker[Session], user: uuid.UUID, settings: Settings, texts: Sequence[str]
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """A run over one small document per text, its first step running."""
    documents = [add_document(factory, user, text) for text in texts]
    run_id = eh.start(factory, user, documents)
    eh.advance(factory, run_id, settings)
    return run_id, documents


def finish(
    factory: sessionmaker[Session],
    user: uuid.UUID,
    settings: Settings,
    run_id: uuid.UUID,
    documents: Sequence[uuid.UUID],
    facts: Sequence[Sequence[Fact]],
    failing: Sequence[int] = (),
) -> IngestionRun:
    """Every step stores the facts of its document (by the document's index) and ends, `failed`
    with a failure of its own content for the indexes in `failing`. The last `advance` merges."""
    while eh.run_of(factory, run_id).status.value == "running":
        step = eh.running(factory, run_id)
        index = list(documents).index(step.document_id)
        for fact in facts[index] if index < len(facts) else ():
            store(factory, user, step, *fact)
        if index in failing:
            eh.finish_step(factory, run_id, settings, AgentStatus.failed, "TURN_LIMIT")
        else:
            eh.finish_step(factory, run_id, settings)
    return eh.run_of(factory, run_id)


def merged(
    factory: sessionmaker[Session],
    user: uuid.UUID,
    settings: Settings,
    texts: Sequence[str],
    facts: Sequence[Sequence[Fact]],
    failing: Sequence[int] = (),
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """A whole run: started, read, merged. Answers its id and its documents."""
    run_id, documents = begin(factory, user, settings, texts)
    finish(factory, user, settings, run_id, documents, facts, failing)
    return run_id, documents


def proposals_of(
    factory: sessionmaker[Session],
    user: uuid.UUID,
    run_id: uuid.UUID | None = None,
    status: ProposalStatus | None = ProposalStatus.pending,
) -> list[Proposal]:
    """The user's profile items in id order, of one run and one status when given."""
    with factory() as db:
        query = sa.select(Proposal).where(
            Proposal.user_id == user, Proposal.kind == items.KIND, Proposal.target == items.TARGET
        )
        if run_id is not None:
            query = query.where(Proposal.payload["ingestion_run_id"].astext == str(run_id))
        if status is not None:
            query = query.where(Proposal.status == status)
        return list(db.scalars(query.order_by(Proposal.id)).all())


def at(found: Sequence[Proposal], key: str) -> Proposal:
    """The one proposal at this key."""
    (one,) = [proposal for proposal in found if proposal.payload["key"] == key]
    return one
