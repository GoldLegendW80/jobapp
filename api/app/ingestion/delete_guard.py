"""The guard on deleting a document a run has read (ingestion G10, removed by G15).

Until G15 deletes a document together with what was drawn from it (its proposals, the events of
its steps), a document that a step has read cannot be deleted: `main` never deletes a document
while leaving its excerpts behind. A step has read a document when an agent run was created for
it, or when it stands for an earlier reading (`reused`), unless its run ended `failed` or
`cancelled` without one token counted (review of PR #148: nothing was read, nothing is held).
A step that is only `planned` has read nothing: the document is deleted, and the step then finds
no document and fails without a model call.

Importing this module registers the guard in the list `documents.delete` consults. The api
imports it through `runs_router`.
"""

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.agents.models import AgentRun
from app.ingestion import documents
from app.ingestion.models import IngestionDocument
from app.ingestion.runs_models import IngestionRun, IngestionRunStep, RunStatus, StepStatus

READ_BY_A_RUN = (
    "This document was read by a run, so it cannot be deleted yet. Deleting a document that a "
    "run has read arrives with a coming update, which also removes what was drawn from it."
)


def read_by_a_run(db: Session, document: IngestionDocument) -> str | None:
    """The sentence that refuses the deletion, or None when no step has read the document."""
    step, run, agent = IngestionRunStep, IngestionRun, AgentRun
    # A run that ended `failed` or `cancelled` without one token counted never had an answer
    # from the model: no tool was called, so no excerpt of its documents is in an event and no
    # item was stored (a step that failed at once on a missing credential, a run cancelled
    # before its first answer). Its steps hold nothing back. A run that spent tokens does, even
    # with no item kept: the events of its steps quote the document until G15 deletes them. A
    # run the engine closed itself (`closed_reason`) may hold tokens it never recorded.
    nothing_read = sa.and_(
        run.status.in_((RunStatus.failed, RunStatus.cancelled)),
        run.tokens_spent == 0,
        run.closed_reason.is_(None),
        sa.func.coalesce(agent.input_tokens, 0) + sa.func.coalesce(agent.output_tokens, 0) == 0,
        step.items_count == 0,
    )
    found = db.execute(
        sa.select(step.id)
        .join(run, run.id == step.ingestion_run_id)
        .outerjoin(agent, agent.id == step.agent_run_id)
        .where(
            step.document_id == document.id,
            sa.or_(
                step.agent_run_id.is_not(None),
                step.status.in_((StepStatus.running, StepStatus.done, StepStatus.reused)),
            ),
            sa.not_(nothing_read),
        )
        .limit(1)
    ).first()
    return READ_BY_A_RUN if found is not None else None


documents.register_delete_guard(read_by_a_run)
