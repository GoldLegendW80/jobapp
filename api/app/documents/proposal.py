"""The outreach proposal of a posting (issue #485), the documents side of opportunities O9a.

A posting whose only way to apply is an e-mail address gets a short proposal instead of a CV and
a letter: a document of kind `proposal`, one per posting (the partial unique index of 0015), with
a subject and a body of at most about 150 words (`content.ProposalContent`) and `to`, the
address it goes to. It is text only: no template prints it, the renderer never draws it, and the
app never sends it (the person copies it or opens it in their own mail program).

`create(db, user_id, posting_id, contact=..., at=...)` is what O9a calls, in its own
transaction, under the posting it was given: the proposal of that posting, made the first time
with `to` set to `contact`, and its writing started (`generate.create` and `generate.start`, the
writer of `writer_proposal`, the fact check and its corrections as for a letter). A second call
returns the same document and starts nothing while it has a version or a writing open; one
whose writing failed or was stopped is written again. The writing waits for the consent and the
credential as a click does (`writer_base.wait_reason`), and the tick's `start_clicks` starts it
once they are there; a person whose runs are all busy keeps it `queued`, which the tick starts
too. The caller commits, then queues `run_id` with `generate.enqueue_run`.

Versions and edits are those of every document: the routes of router.py save its draft (a
version each time, since no render snapshots it), restore a version and approve one; the
template switch and "Render" refuse it.

Logs carry ids and codes, never a text or an address.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.agents import runs as agent_runs
from app.config import Settings
from app.documents import content as c
from app.documents import estimate, generate, store, writer_base
from app.documents.models import (
    Author,
    Document,
    DocumentGeneration,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    Kind,
)

logger = logging.getLogger("app.documents.proposal")


@dataclass(frozen=True)
class Created:
    """What `create` did: the posting's proposal, and the writing it started or left waiting
    (None when the proposal was already written or being written)."""

    document: Document
    generation: DocumentGeneration | None
    run_id: uuid.UUID | None  # to queue after the commit (`generate.enqueue_run`)
    created: bool  # the document is new


def _address(contact: str) -> str:
    """The contact as `to` holds it, or ContentInvalidError (empty, or not one address)."""
    model = c.parse("proposal", {"to": contact})
    if not isinstance(model, c.ProposalContent) or not model.to:
        raise c.ContentInvalidError((c.FieldError("to", "required", "an address is required"),))
    return model.to


def _existing(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID) -> Document | None:
    return next(
        (
            found
            for found in store.posting_documents(db, user_id, posting_id)
            if found.kind == Kind.proposal
        ),
        None,
    )


def create(
    db: Session,
    user_id: uuid.UUID,
    posting_id: uuid.UUID,
    *,
    contact: str,
    at: datetime,
    script: str | None = None,
    settings: Settings | None = None,
) -> Created:
    """The posting's proposal, written (module docstring). Raises store.NotFoundError for a
    posting that is not the person's, content.ContentInvalidError for a contact that is not
    one address, store.DocumentBusyError when a lock waits too long."""
    to = _address(contact)
    if store.lock_posting(db, user_id, posting_id) is None:
        raise store.NotFoundError(posting_id)
    document = _existing(db, user_id, posting_id)
    created = document is None
    if document is None:
        document = store.get_or_create(db, user_id, posting_id, Kind.proposal, at=at)
        if not document.draft.get("to"):
            document = store.save_draft(
                db,
                user_id,
                document.id,
                {**document.draft, "to": to},
                document.draft_revision,
                Author.writer,
                at=at,
            )
    # Written, or a writing open (a click's, waiting or running): nothing more to start.
    if store.versions(db, user_id, document.id) or not generate.give_way(db, document, at):
        return Created(document, None, None, created)
    default = store.default_base(db, user_id)
    base_id = default.id if default is not None else None
    figures = estimate.for_posting(db, user_id, posting_id, base_id)
    reason = writer_base.wait_reason(db, user_id)
    generation = generate.create(
        db,
        document,
        GenerationMode.posting,
        GenerationTrigger.click,
        base_id,
        dict(estimate.as_json(figures)),
        figures.ceiling,
        at=at,
        status=GenerationStatus.waiting if reason else GenerationStatus.queued,
        wait_reason=reason,
    )
    run_id = None
    if reason is None:
        # A person whose runs are all busy: it stays queued, the tick's `start_clicks` starts it.
        with contextlib.suppress(agent_runs.ActiveRunsError):
            run_id = generate.start(db, generation, at=at, script=script, settings=settings).id
    logger.info(
        "documents.proposal.created",
        extra={
            "document_id": str(document.id),
            "generation_id": str(generation.id),
            "status": generation.status.value,
            "created": created,
        },
    )
    return Created(document, generation, run_id, created)
