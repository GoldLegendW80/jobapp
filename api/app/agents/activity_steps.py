"""The app's own steps of a workflow run (Activity, agents A10), read from their own rows: the
sweep and its sources, the ingestion run and the steps it has not started, the hiring panel and
the generations still waiting, the application's state. Each query is bound to the caller's id.
A step to come is `waiting`, with no time; its agent run, once made, is an agent step instead.
"""

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.agents.catalog import NAMES
from app.apply.models import Application, State
from app.documents.models import (
    Document,
    DocumentGeneration,
    DocumentPanel,
    GenerationStatus,
    Kind,
)
from app.ingestion.runs_models import IngestionRun, IngestionRunStep, StepStatus
from app.ingestion.runs_models import RunStatus as IngestionStatus
from app.jobs.models import Sweep, SweepStatus

_SWEEP = {
    SweepStatus.running: "running",
    SweepStatus.done: "success",
    SweepStatus.failed: "failure",
}
_SOURCE = {"complete": "success", "no_matches": "success", "partial": "failure"}
_INGESTION = {
    IngestionStatus.running: "running",
    IngestionStatus.paused: "waiting",
    IngestionStatus.done: "success",
    IngestionStatus.failed: "failure",
    IngestionStatus.cancelled: "cancelled",
}
_PANEL = {"running": "running", "done": "success", "failed": "failure"}
_APPLICATION_RUNNING = {State.queued, State.filling, State.submitting, State.approved, State.held}
_APPLICATION_WAITING = {State.needs_person, State.ready}
_APPLICATION_DONE = {State.submitted, State.rehearsed}
_APPLICATION_STATE = {
    State.queued: "Queued",
    State.filling: "Filling the form",
    State.needs_person: "Waiting for your answer",
    State.ready: "Waiting for your approval",
    State.approved: "Approved",
    State.held: "Held before it is sent",
    State.submitting: "Being sent",
    State.submitted: "Sent",
    State.submit_unknown: "Sent, the site's answer unknown",
    State.refused_by_site: "Refused by the site",
    State.failed: "Failed",
    State.rehearsed: "Rehearsed, not sent",
    State.cancelled: "Cancelled",
    State.blocked: "Blocked",
}
# The agent each ingestion step and each waiting generation runs, by kind.
_INGESTION_AGENT = {"transcribe": "document_transcribe", "extract": "profile_extract"}
_WRITER = {Kind.cv: "documents_cv", Kind.letter: "documents_letter"}


def _step(
    key: str,
    node: str | None,
    name: str,
    status: str,
    started: datetime | None = None,
    finished: datetime | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    return {
        "id": key,
        "kind": "system",
        "node": node,
        "name": name,
        "agent": None,
        "status": status,
        "started_at": started,
        "finished_at": finished,
        "duration_s": (finished - started).total_seconds() if started and finished else None,
        "private": False,
        "note": note,
    }


def _uuid(text: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(text)
    except ValueError:
        return None


def _sweep(db: Session, user_id: uuid.UUID, key: uuid.UUID) -> list[dict[str, Any]]:
    row = db.execute(
        sa.select(Sweep.status, Sweep.started_at, Sweep.finished_at, Sweep.stats).where(
            Sweep.id == key, Sweep.user_id == user_id
        )
    ).one_or_none()
    if row is None:
        return []
    stats = row.stats or {}
    reading = stats.get("reading") or {}
    note = (
        f"{reading.get('read', 0)} postings read, {reading.get('left', 0)} left"
        if reading
        else None
    )
    steps = [
        _step(
            f"sweep:{key}",
            "sweep",
            "Sweep",
            _SWEEP[row.status],
            row.started_at,
            row.finished_at,
            note,
        )
    ]
    for index, source in enumerate(stats.get("sources") or []):
        if not isinstance(source, dict):
            continue
        name = str(source.get("host") or source.get("kind") or "A source")
        if source.get("phase") == "pending":
            status = "waiting" if row.status is SweepStatus.running else "cancelled"
        elif source.get("phase") == "running":
            status = "running"
        else:
            status = _SOURCE.get(str(source.get("outcome")), "failure")
            if source.get("outcome") == "not_attempted":
                status = "cancelled"
        # A website searched in the browser shows as its agent's step once that run exists.
        node = "browser_discovery" if source.get("mode") == "llm_browser" else "sweep"
        if node == "browser_discovery" and status != "waiting":
            continue
        found = source.get("found")
        steps.append(
            _step(
                f"source:{key}:{index}",
                node,
                name,
                status,
                note=f"{found} found" if isinstance(found, int) else None,
            )
        )
    return steps


def _ingestion(db: Session, user_id: uuid.UUID, key: uuid.UUID) -> list[dict[str, Any]]:
    run = db.execute(
        sa.select(IngestionRun.status, IngestionRun.created_at, IngestionRun.finished_at).where(
            IngestionRun.id == key, IngestionRun.user_id == user_id
        )
    ).one_or_none()
    if run is None:
        return []
    steps = [
        _step(
            f"ingestion:{key}",
            "run",
            "Ingestion run",
            _INGESTION[run.status],
            run.created_at,
            run.finished_at,
        )
    ]
    planned = db.execute(
        sa.select(IngestionRunStep.id, IngestionRunStep.kind)
        .where(
            IngestionRunStep.ingestion_run_id == key,
            IngestionRunStep.status == StepStatus.planned,
            IngestionRunStep.agent_run_id.is_(None),
        )
        .order_by(IngestionRunStep.position)
    ).all()
    waiting = (
        "waiting"
        if run.status in (IngestionStatus.running, IngestionStatus.paused)
        else ("cancelled")
    )
    for step in planned:
        agent = _INGESTION_AGENT[str(getattr(step.kind, "value", step.kind))]
        steps.append(_step(f"ingestion-step:{step.id}", agent, NAMES[agent], waiting))
    return steps


def _documents(
    db: Session, user_id: uuid.UUID, key: uuid.UUID, run_ids: Sequence[uuid.UUID]
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    panels = db.execute(
        sa.select(
            DocumentPanel.id,
            DocumentPanel.kind,
            DocumentPanel.status,
            DocumentPanel.started_at,
            DocumentPanel.created_at,
            DocumentPanel.finished_at,
        )
        .where(DocumentPanel.user_id == user_id, DocumentPanel.source_run_id.in_(run_ids))
        .order_by(DocumentPanel.created_at)
    ).all()
    for panel in panels:
        status = _PANEL.get(panel.status, "failure")
        if status == "running" and panel.started_at is None:
            status = "waiting"
        steps.append(
            _step(
                f"panel:{panel.id}",
                "panel",
                f"Hiring panel of the {panel.kind}",
                status,
                panel.started_at,
                panel.finished_at,
            )
        )
    first = db.execute(
        sa.select(DocumentGeneration.created_at, Document.posting_id)
        .join(Document, Document.id == DocumentGeneration.document_id)
        .where(DocumentGeneration.id == key, DocumentGeneration.user_id == user_id)
    ).one_or_none()
    if first is None or first.posting_id is None:
        return steps
    # The generations of the same click (`documents.flows.posting_flow`) no run started yet.
    waiting = db.execute(
        sa.select(DocumentGeneration.id, Document.kind)
        .join(Document, Document.id == DocumentGeneration.document_id)
        .where(
            DocumentGeneration.user_id == user_id,
            DocumentGeneration.created_at == first.created_at,
            Document.posting_id == first.posting_id,
            DocumentGeneration.run_id.is_(None),
            DocumentGeneration.status.in_((GenerationStatus.queued, GenerationStatus.waiting)),
        )
        .order_by(DocumentGeneration.id)
    ).all()
    for generation in waiting:
        agent = _WRITER.get(generation.kind)
        if agent is not None:
            steps.append(_step(f"generation:{generation.id}", agent, NAMES[agent], "waiting"))
    return steps


def _application(db: Session, user_id: uuid.UUID, key: uuid.UUID) -> list[dict[str, Any]]:
    row = db.execute(
        sa.select(Application.state, Application.created_at, Application.updated_at).where(
            Application.id == key, Application.user_id == user_id
        )
    ).one_or_none()
    if row is None:
        return []
    if row.state in _APPLICATION_RUNNING:
        status, finished = "running", None
    elif row.state in _APPLICATION_WAITING:
        status, finished = "waiting", None
    elif row.state in _APPLICATION_DONE:
        status, finished = "success", row.updated_at
    elif row.state is State.cancelled:
        status, finished = "cancelled", row.updated_at
    else:
        status, finished = "failure", row.updated_at
    return [
        _step(
            f"application:{key}",
            "fill",
            "Application",
            status,
            row.created_at,
            finished,
            _APPLICATION_STATE[row.state],
        )
    ]


def system_steps(
    db: Session, user_id: uuid.UUID, workflow: str, key: str, run_ids: Iterable[uuid.UUID]
) -> list[dict[str, Any]]:
    """The app's own steps of one workflow run of the caller, by its workflow and its id."""
    found = _uuid(key)
    if found is None:
        return []
    if workflow == "job_search":
        return _sweep(db, user_id, found)
    if workflow == "fill_profile":
        return _ingestion(db, user_id, found)
    if workflow in ("posting_documents", "base_cv"):
        return _documents(db, user_id, found, list(run_ids))
    if workflow == "application":
        return _application(db, user_id, found)
    return []
