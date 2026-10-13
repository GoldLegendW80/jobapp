"""The guard on deleting a document a run has read (ingestion G10, removed by G15).

Until G15 deletes a document together with what was drawn from it (its proposals, the events of
its steps), a document that a step has read cannot be deleted: `main` never deletes a document
while leaving its excerpts behind. A step has read a document when an agent run was created for
it, or when it stands for an earlier reading (`reused`). A step that is only `planned` has read
nothing: the document is deleted, and the step then finds no document and fails without a model
call.

Importing this module registers the guard in the list `documents.delete` consults. The api
imports it through `runs_router`.
"""

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.ingestion import documents
from app.ingestion.models import IngestionDocument
from app.ingestion.runs_models import IngestionRunStep, StepStatus

READ_BY_A_RUN = "This document was read by a run and cannot be deleted yet."


def read_by_a_run(db: Session, document: IngestionDocument) -> str | None:
    """The sentence that refuses the deletion, or None when no step has read the document."""
    step = IngestionRunStep
    found = db.execute(
        sa.select(step.id)
        .where(
            step.document_id == document.id,
            sa.or_(
                step.agent_run_id.is_not(None),
                step.status.in_((StepStatus.running, StepStatus.done, StepStatus.reused)),
            ),
        )
        .limit(1)
    ).first()
    return READ_BY_A_RUN if found is not None else None


documents.register_delete_guard(read_by_a_run)
