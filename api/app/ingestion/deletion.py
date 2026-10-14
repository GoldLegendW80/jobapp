"""Deleting a document and what was drawn from it (ingestion G15), in one transaction.

What goes, by SPEC-ingestion.md ("Deletion is a hard delete") and the plan's "Failure rules":
- the document's row, so its file, its text and its transcriptions, and through the foreign keys
  its page images and its items (`ingestion_items`);
- its citations in proposals of every status. A pending item keeps the citations of its other
  documents; a value of it left without one is dropped (the next value of a conflict takes its
  place), and an item left with none is deleted, as is a pending derived item that counted it
  (its value was computed from what the document said). An accepted item keeps its value, which
  is the user's own profile now, and each of its citations of the document becomes
  `{document_id, source_deleted: true}` with the excerpt gone; but a value of it that the user
  turned down (they accepted with their own value, by an edit or by picking another value of a
  conflict) and that only this document gave keeps its hash alone, no text. Every other item
  (rejected, superseded, invalid, undone) keeps only its section, key, action and value hash,
  with the value hashes of a conflict: what a rerun needs so as not to propose a refused value again
  (`merge.py`, rule 4), and nothing a document said. Such an item has no run any more, so no
  run's page lists it;
- every `agent_events` row of the steps that read the document. A step still at work has its
  agent run cancelled and `purge_events` set: `engine.advance` deletes its events again when the
  run ends, and the cron until five minutes after (`engine.purge`). A step that was only planned
  is failed by the engine, which finds no document, without an agent run.

What stays: the run and step rows, with their ids, statuses, tokens and cost (a step's
`document_id` is no foreign key; the pages show it as "Deleted document"), and the profile as it
is: nothing accepted is taken back.

Locks, in the plan's order: the user's active run row, if any (3), the document row (4), the
rows of its steps at work (5), their agent runs (7), then the proposals that cite it in id order
(8). So a delete waits for a merge in flight (which holds 3 and writes 8), for a tool call in
flight (which holds 4 `FOR KEY SHARE`), and for an accept of one of its items (2 then 8); the
tool call after it finds no document, and a start that comes after it finds the document gone
(`engine.create` reads the documents `FOR KEY SHARE` after inserting its run). A start that
committed while the delete waited for the document makes a run active that the delete did not
hold: its row is taken then, after the document row. That is safe, because no path holds a run
row and waits for a document row (`advance` and the merge only read documents). A wait longer
than `LOCK_WAIT_MS` raises `DeletionBusyError` and deletes nothing.

Every query filters on the caller's user id: another user's document is not found. Nothing here
logs a name, a text, a value or an excerpt: ids and counts only.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.agents import runs as agent_runs
from app.agents.models import AgentEvent, AgentRun, Proposal, ProposalStatus
from app.agents.models import RunStatus as AgentStatus
from app.ingestion import documents, items
from app.ingestion.models import IngestionDocument
from app.ingestion.runs_models import IngestionRun, IngestionRunStep, RunStatus, StepStatus
from app.jobs import service as jobs_service

logger = logging.getLogger("app.ingestion")  # the logger of the documents: "document deleted"

LOCK_WAIT_MS = jobs_service.LOCK_TIMEOUT_MS
ACTIVE = (RunStatus.running, RunStatus.paused)
AT_WORK = (AgentStatus.queued, AgentStatus.running)
SOURCE_DELETED = "source_deleted"
DERIVED = "derived"
CONFLICT = "conflict"

REPLACEMENT_REFUSED = (
    "This file is already among your documents and a run has read it, so this upload did not "
    "replace it. Delete that document first, then upload the file again."
)


class DeletionBusyError(Exception):
    """A run, a tool call or a decision holds what the delete needs for longer than the lock
    timeout. Nothing was deleted: the caller rolls back and answers 409."""


def delete(db: Session, user_id: uuid.UUID, document_id: uuid.UUID, now: datetime) -> bool:
    """Delete the user's document and what was drawn from it, in the caller's transaction,
    which the caller commits. False when the user has no such document (another user's
    included). Raises DeletionBusyError when a lock is not free in time."""
    db.execute(sa.text("select set_config('lock_timeout', :ms, true)"), {"ms": f"{LOCK_WAIT_MS}ms"})
    try:
        return _delete(db, user_id, document_id, now)
    except DBAPIError as exc:
        if jobs_service.is_lock_timeout(exc):
            raise DeletionBusyError from exc
        raise


def _delete(db: Session, user_id: uuid.UUID, document_id: uuid.UUID, now: datetime) -> bool:
    if not _exists(db, user_id, document_id):
        return False  # nothing locked for a document that is not there, or not the user's
    held = _lock_active_run(db, user_id, None)
    document = documents.lock_document(db, user_id, document_id)
    if document is None:
        return False  # deleted meanwhile by another tab
    _lock_active_run(db, user_id, held)  # a run that became active while the delete waited
    stopped, purged = _stop_steps(db, user_id, document_id, now)
    decided = _strip_proposals(db, user_id, document_id)
    db.delete(document)
    db.flush()
    logger.info(
        "document deleted",
        extra={
            "document_id": str(document_id),
            "user_id": str(user_id),
            "steps_cancelled": stopped,
            "events_deleted": purged,
            **decided,
        },
    )
    return True


def _exists(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            sa.select(IngestionDocument.id).where(
                IngestionDocument.id == document_id, IngestionDocument.user_id == user_id
            )
        )
        is not None
    )


def _lock_active_run(db: Session, user_id: uuid.UUID, held: uuid.UUID | None) -> uuid.UUID | None:
    """The row of the user's active run, locked (3), unless it is `held` already. Answers the
    id of the run held."""
    found = db.scalar(
        sa.select(IngestionRun.id).where(
            IngestionRun.user_id == user_id, IngestionRun.status.in_(ACTIVE)
        )
    )
    if found is None or found == held:
        return held
    db.execute(
        sa.select(IngestionRun.id)
        .where(IngestionRun.id == found, IngestionRun.user_id == user_id)
        .with_for_update()
    )
    return found


def _stop_steps(
    db: Session, user_id: uuid.UUID, document_id: uuid.UUID, now: datetime
) -> tuple[int, int]:
    """Mark the document's steps still `running` for the purge and cancel their agent runs,
    then delete the events of every step that read the document. Answers how many agent runs
    were asked to stop and how many events were deleted."""
    step = IngestionRunStep
    rows = db.execute(
        sa.select(step.id, step.status, step.agent_run_id, AgentRun.status.label("agent"))
        .join(IngestionRun, IngestionRun.id == step.ingestion_run_id)
        .outerjoin(AgentRun, AgentRun.id == step.agent_run_id)
        .where(step.document_id == document_id, IngestionRun.user_id == user_id)
        .order_by(step.id)
    ).all()
    running = [row for row in rows if row.status is StepStatus.running]
    if running:
        # The step rows (5), then their agent runs (7): `purge_events` turns the cancel into a
        # failed step whose chain goes on (`engine._after`). A step whose agent run has ended
        # already is recorded by the next `advance`, which deletes its events once more.
        db.execute(
            sa.update(step)
            .where(step.id.in_([row.id for row in running]))
            .values(purge_events=True)
            .execution_options(synchronize_session=False)
        )
    stopped = 0
    for row in running:
        if row.agent_run_id is not None and row.agent in AT_WORK:
            agent_runs.request_cancel(db, user_id, row.agent_run_id, now)
            stopped += 1
    read = [row.agent_run_id for row in rows if row.agent_run_id is not None]
    purged = 0
    if read:
        purged = db.execute(
            sa.delete(AgentEvent)
            .where(AgentEvent.run_id.in_(read))
            .execution_options(synchronize_session=False)
        ).rowcount
    return stopped, int(purged or 0)


# --- proposals ------------------------------------------------------------------------------------


def _cites(document_id: uuid.UUID) -> sa.ColumnElement[bool]:
    """A profile item that cites the document, for the item or for a value of its conflict."""
    one = [{"document_id": str(document_id)}]
    return sa.or_(
        Proposal.payload["citations"].contains(one),
        Proposal.payload[CONFLICT].contains([{"citations": one}]),
    )


def _strip_proposals(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> dict[str, int]:
    """Apply the rules of the module docstring to every item that cites the document, locked in
    id order (8). Answers counts by what was done."""
    found = db.scalars(
        sa.select(Proposal)
        .where(
            Proposal.user_id == user_id,
            Proposal.target == items.TARGET,
            Proposal.kind == items.KIND,
            _cites(document_id),
        )
        .order_by(Proposal.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    counts = {"proposals_deleted": 0, "proposals_kept": 0, "proposals_reduced": 0}
    gone = str(document_id)
    for proposal in found:
        payload = proposal.payload or {}
        if proposal.status is ProposalStatus.pending:
            kept = _pending(payload, gone)
            if kept is None:
                db.delete(proposal)
                counts["proposals_deleted"] += 1
                continue
            proposal.payload = kept
            counts["proposals_kept"] += 1
        elif proposal.status is ProposalStatus.accepted:
            proposal.payload = _accepted(payload, gone, proposal.edited_value)
            counts["proposals_kept"] += 1
        else:
            proposal.payload = remembered(payload)
            counts["proposals_reduced"] += 1
    db.flush()
    return counts


def _of(raw: Any) -> list[Mapping[str, Any]]:
    return [one for one in raw if isinstance(one, Mapping)] if isinstance(raw, list) else []


def _without(citations: Any, gone: str) -> list[Mapping[str, Any]]:
    return [one for one in _of(citations) if str(one.get("document_id")) != gone]


def _pending(payload: Mapping[str, Any], gone: str) -> dict[str, Any] | None:
    """A pending item without the document, or None when nothing of it is left to propose."""
    flags = [one for one in payload.get("flags") or () if isinstance(one, str)]
    if DERIVED in flags or payload.get("depends_on"):
        return None  # computed from what the document said: the next merge computes it again
    values = [
        {
            "value": payload.get("value"),
            "value_hash": payload.get("value_hash"),
            "citations": _without(payload.get("citations"), gone),
        }
    ]
    values += [
        {**other, "citations": _without(other.get("citations"), gone)}
        for other in _of(payload.get(CONFLICT))
    ]
    left = [one for one in values if one["citations"]]
    if not left:
        return None
    first, others = left[0], left[1:]
    kept = {
        **payload,
        "value": first["value"],
        "value_hash": first["value_hash"],
        "citations": first["citations"],
    }
    if others:
        kept[CONFLICT] = others
    else:
        kept.pop(CONFLICT, None)
        kept["flags"] = [one for one in flags if one != CONFLICT]
    return kept


def _marked(citations: Any, gone: str) -> list[dict[str, Any]]:
    return [
        {"document_id": gone, SOURCE_DELETED: True}
        if str(one.get("document_id")) == gone
        else dict(one)
        for one in _of(citations)
    ]


def _turned_down(entry: dict[str, Any], gone: str, kept: str | None) -> dict[str, Any]:
    """A value an accepted item showed, with its citations marked. When the user kept a value of
    their own (`kept` is its hash) and the document was the only source of this one, it is a
    value the user turned down and its text goes: the hash stays, for the rerun to refuse it."""
    cited = _of(entry.get("citations"))
    # A citation an earlier delete marked is a source that is gone already.
    only = bool(cited) and all(
        one.get(SOURCE_DELETED) is True or str(one.get("document_id")) == gone for one in cited
    )
    entry = {**entry, "citations": _marked(entry.get("citations"), gone)}
    if only and kept is not None and entry.get("value_hash") != kept:
        entry.pop("value", None)
    return entry


def _accepted(payload: Mapping[str, Any], gone: str, edited: Any = None) -> dict[str, Any]:
    """An accepted item keeps its value, its citation of the document turned into "source
    deleted". An item accepted with the user's own value (an edit, or the pick of another value
    of a conflict, `edited`) turned down the values the documents gave; of those, the ones this
    document alone gave lose their text."""
    kept = items.value_hash(edited) if edited is not None else None
    result = _turned_down(dict(payload), gone, kept)
    if CONFLICT in payload:
        result[CONFLICT] = [
            _turned_down(dict(other), gone, kept) for other in _of(payload[CONFLICT])
        ]
    return result


def remembered(payload: Mapping[str, Any]) -> dict[str, Any]:
    """What a decided item keeps once a document it cites is deleted: where it was and the hash
    of each value it showed, so that a rerun does not propose a refused value again."""
    kept = {name: payload.get(name) for name in ("section", "key", "action", "value_hash")}
    others = [{"value_hash": other.get("value_hash")} for other in _of(payload.get(CONFLICT))]
    if others:
        kept[CONFLICT] = others
    return kept


# --- the replacement of a document a run has read ----------------------------------------------


def read_by_a_run(db: Session, document: IngestionDocument) -> str | None:
    """The sentence that refuses to replace the document's text by a new upload of the same
    file, or None when no step has read it. A replacement keeps the row and its id, so it
    cannot do what a delete does to what was drawn from the text; the user deletes the document
    and uploads the file again. A run that ended `failed` or `cancelled` without one token
    counted read nothing (review of PR #148)."""
    step, run, agent = IngestionRunStep, IngestionRun, AgentRun
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
            run.user_id == document.user_id,
            sa.or_(
                step.agent_run_id.is_not(None),
                step.status.in_((StepStatus.running, StepStatus.done, StepStatus.reused)),
            ),
            sa.not_(nothing_read),
        )
        .limit(1)
    ).first()
    return REPLACEMENT_REFUSED if found is not None else None


documents.register_replacement_check(read_by_a_run)
