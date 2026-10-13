"""Reuse of finished steps (ingestion G10): what an earlier run already read is not read again.

A step is identified by `plan.Identity` (the hash of the file, the kind, the part or the image
positions, the extractor version and the model), stored on its row as `identity_hash`. A step
counts as finished for a later run when it ended `done` in any run of the same user, whatever
became of that run (a cancelled run keeps the items of its finished steps), **and** it was
planned on the text its document stores now (`text_sha256`: a replaced text keeps the row and
the hash of its file, so the identity alone cannot tell).

`finished` is registered as the provider of the estimate (`estimate_router.quote`), so the
dialog, the start of a run and the engine all plan from the same answer. `source_steps` is the
other direction, for the readers of `ingestion_items` (the merge of G12, the summaries of G11):
the step rows whose items belong to a run, its own finished steps and, for each reused one, the
earlier step it stands for.

No lock is taken here: reads only.
"""

import uuid
from collections.abc import Collection, Sequence

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from app.ingestion import estimate_router, plan
from app.ingestion.models import IngestionDocument
from app.ingestion.runs_models import (
    IngestionRun,
    IngestionRunStep,
    StepStatus,
    identity_hash,
    stored_text_sha256,
)


def _of_stored_text(step: type[IngestionRunStep]) -> sa.ColumnElement[bool]:
    """True for a step planned on the text its document stores now (the document still there)."""
    return (
        sa.exists()
        .where(
            IngestionDocument.id == step.document_id,
            IngestionDocument.sha256 == step.sha256,
            stored_text_sha256() == step.text_sha256,
        )
        .correlate(step)
    )


def finished(
    db: Session, user_id: uuid.UUID, selected: Sequence[plan.Document], model: str
) -> Collection[plan.Identity]:
    """The identities, among the steps a run of `selected` with `model` would have, of the
    steps that finished in earlier runs of this user."""
    if not selected:
        return frozenset()
    # Every identity the selection can have, reused or not: the plan is the one place that
    # knows how a document becomes steps.
    candidates = {
        identity_hash(step.identity): step.identity
        for step in plan.build(selected, model=model).steps
    }
    if not candidates:
        return frozenset()
    step = IngestionRunStep
    found = db.scalars(
        sa.select(step.identity_hash)
        .join(IngestionRun, IngestionRun.id == step.ingestion_run_id)
        .where(
            IngestionRun.user_id == user_id,
            step.status == StepStatus.done,
            step.document_id.in_([document.id for document in selected]),
            step.identity_hash.in_(list(candidates)),
            _of_stored_text(step),
        )
        .distinct()
    )
    return frozenset(candidates[one] for one in found)


def source_steps(db: Session, ingestion_run_id: uuid.UUID) -> list[uuid.UUID]:
    """The ids of the steps whose items are this run's: its own steps that ended `done` or were
    interrupted with items stored, and for each `reused` step the latest earlier `done` step of
    the same user with the same identity and the same text. A reader of `ingestion_items`
    selects `step_id IN (these)` together with `tools.of_stored_text()`."""
    step = IngestionRunStep
    own = list(
        db.scalars(
            sa.select(step.id).where(
                step.ingestion_run_id == ingestion_run_id, step.status != StepStatus.reused
            )
        )
    )
    earlier = aliased(IngestionRunStep)
    earlier_run = aliased(IngestionRun)
    run = aliased(IngestionRun)
    stood_for = db.execute(
        sa.select(step.id, earlier.id)
        .join(run, run.id == step.ingestion_run_id)
        .join(
            earlier,
            sa.and_(
                earlier.identity_hash == step.identity_hash,
                earlier.document_id == step.document_id,
                earlier.text_sha256 == step.text_sha256,
                earlier.status == StepStatus.done,
            ),
        )
        .join(
            earlier_run,
            sa.and_(earlier_run.id == earlier.ingestion_run_id, earlier_run.user_id == run.user_id),
        )
        .where(step.ingestion_run_id == ingestion_run_id, step.status == StepStatus.reused)
        .order_by(step.position, earlier_run.created_at.desc(), earlier.id)
    )
    seen: dict[uuid.UUID, uuid.UUID] = {}
    for reused_id, source_id in stood_for:
        seen.setdefault(reused_id, source_id)
    return own + list(seen.values())


estimate_router.register_finished_provider(finished)
