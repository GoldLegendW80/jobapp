"""The app's own steps of a workflow run (Activity, agents A10), read from their own rows: the
sweep and its sources, the ingestion run and the steps it has not started, the hiring panel and
the generations still waiting, the application's state. Each query is bound to the caller's id.
A step to come is `waiting`, with no time; its agent run, once made, is an agent step instead.
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
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
from app.jobs.models import Job, Sweep, SweepStatus

_SWEEP = {
    SweepStatus.running: "running",
    SweepStatus.done: "success",
    SweepStatus.failed: "failure",
}
_SOURCE = {"complete": "success", "no_matches": "success", "partial": "failure"}
# Why a source that failed was not tried again (sweep.not_retried).
_NOT_RETRIED = {
    "linkedin_cooldown": "not tried again, LinkedIn asked the app to wait",
    "no_time": "not tried again, no time left",
    "not_started": "not tried again",
}
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
    State.posting_closed: "The posting closed",
}
# The agent each ingestion step and each waiting generation runs, by kind.
_INGESTION_AGENT = {"transcribe": "document_transcribe", "extract": "profile_extract"}
_WRITER = {Kind.cv: "documents_cv", Kind.letter: "documents_letter"}
# The titles and places a sweep's subject names before it says how many more.
_SEARCH_TERMS = 3


@dataclass
class Found:
    """What the app's own rows say of one workflow run."""

    steps: list[dict[str, Any]] = field(default_factory=list)
    subject: str | None = None
    job_id: uuid.UUID | None = None
    # The words of a failed step of the app's own, by its step id: why it failed.
    reasons: dict[str, str] = field(default_factory=dict)
    # A sweep's count of the postings it added.
    added: int | None = None


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


def _terms(search: Mapping[str, Any], name: str) -> list[str]:
    section = search.get(name)
    terms = section.get("terms") if isinstance(section, dict) else None
    return [t for t in terms if isinstance(t, str) and t] if isinstance(terms, list) else []


def _listed(terms: list[str]) -> str:
    shown = ", ".join(terms[:_SEARCH_TERMS])
    more = len(terms) - _SEARCH_TERMS
    return f"{shown} and {more} more" if more > 0 else shown


def search_words(stats: Mapping[str, Any]) -> str | None:
    """A sweep's search in a few words: the titles and places of its frozen scope."""
    scope = stats.get("search_scope")
    search = scope.get("search") if isinstance(scope, dict) else None
    if not isinstance(search, dict):
        return None
    titles, places = _terms(search, "title_include"), _terms(search, "location")
    if titles and places:
        return f"{_listed(titles)} in {_listed(places)}"
    if titles or places:
        return _listed(titles or places)
    return None


def _sweep_steps(key: uuid.UUID, row: Any) -> list[dict[str, Any]]:
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
        steps.append(_step(f"source:{key}:{index}", node, name, status, note=_source_note(source)))
    return steps


def _source_note(source: Mapping[str, Any]) -> str | None:
    """What a source found, and its one retry (browser B24, sweep.RETRIED): a website's retry is
    its second agent step."""
    found = source.get("found")
    parts = [f"{found} found"] if isinstance(found, int) else []
    if source.get("attempt") == 2:
        parts.append("tried twice")
    why = _NOT_RETRIED.get(str(source.get("not_retried")))
    if why is not None:
        parts.append(why)
    return ", ".join(parts) or None


def _sweeps(db: Session, user_id: uuid.UUID, keys: Sequence[uuid.UUID]) -> dict[uuid.UUID, Found]:
    rows = db.execute(
        sa.select(
            Sweep.id, Sweep.status, Sweep.started_at, Sweep.finished_at, Sweep.stats, Sweep.error
        ).where(Sweep.id.in_(keys), Sweep.user_id == user_id)
    ).all()
    found: dict[uuid.UUID, Found] = {}
    for row in rows:
        stats = row.stats or {}
        added = stats.get("new")
        found[row.id] = Found(
            _sweep_steps(row.id, row),
            search_words(stats),
            reasons={f"sweep:{row.id}": row.error}
            if row.status is SweepStatus.failed and row.error
            else {},
            added=added if isinstance(added, int) and not isinstance(added, bool) else None,
        )
    return found


def _ingestions(
    db: Session, user_id: uuid.UUID, keys: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Found]:
    found: dict[uuid.UUID, Found] = {}
    waiting: dict[uuid.UUID, str] = {}
    for run in db.execute(
        sa.select(
            IngestionRun.id, IngestionRun.status, IngestionRun.created_at, IngestionRun.finished_at
        ).where(IngestionRun.id.in_(keys), IngestionRun.user_id == user_id)
    ).all():
        found[run.id] = Found(
            [
                _step(
                    f"ingestion:{run.id}",
                    "run",
                    "Ingestion run",
                    _INGESTION[run.status],
                    run.created_at,
                    run.finished_at,
                )
            ]
        )
        going = run.status in (IngestionStatus.running, IngestionStatus.paused)
        waiting[run.id] = "waiting" if going else "cancelled"
    if not found:
        return found
    planned = db.execute(
        sa.select(IngestionRunStep.id, IngestionRunStep.ingestion_run_id, IngestionRunStep.kind)
        .where(
            IngestionRunStep.ingestion_run_id.in_(list(found)),
            IngestionRunStep.status == StepStatus.planned,
            IngestionRunStep.agent_run_id.is_(None),
        )
        .order_by(IngestionRunStep.position)
    ).all()
    for step in planned:
        agent = _INGESTION_AGENT[str(getattr(step.kind, "value", step.kind))]
        found[step.ingestion_run_id].steps.append(
            _step(f"ingestion-step:{step.id}", agent, NAMES[agent], waiting[step.ingestion_run_id])
        )
    return found


def _documents(
    db: Session, user_id: uuid.UUID, runs: Mapping[uuid.UUID, Sequence[uuid.UUID]]
) -> dict[uuid.UUID, Found]:
    found = {key: Found() for key in runs}
    owner = {run_id: key for key, run_ids in runs.items() for run_id in run_ids}
    panels = db.execute(
        sa.select(
            DocumentPanel.id,
            DocumentPanel.source_run_id,
            DocumentPanel.kind,
            DocumentPanel.status,
            DocumentPanel.started_at,
            DocumentPanel.finished_at,
        )
        .where(DocumentPanel.user_id == user_id, DocumentPanel.source_run_id.in_(list(owner)))
        .order_by(DocumentPanel.created_at)
    ).all()
    for panel in panels:
        status = _PANEL.get(panel.status, "failure")
        if status == "running" and panel.started_at is None:
            status = "waiting"
        found[owner[panel.source_run_id]].steps.append(
            _step(
                f"panel:{panel.id}",
                "panel",
                f"Hiring panel of the {panel.kind}",
                status,
                panel.started_at,
                panel.finished_at,
            )
        )
    # The generations of the same click (`documents.flows.posting_flow`) no run started yet.
    clicks = {
        (first.created_at, first.posting_id): first.id
        for first in db.execute(
            sa.select(DocumentGeneration.id, DocumentGeneration.created_at, Document.posting_id)
            .join(Document, Document.id == DocumentGeneration.document_id)
            .where(DocumentGeneration.id.in_(list(runs)), DocumentGeneration.user_id == user_id)
        ).all()
        if first.posting_id is not None
    }
    if not clicks:
        return found
    waiting = db.execute(
        sa.select(
            DocumentGeneration.id,
            DocumentGeneration.created_at,
            Document.posting_id,
            Document.kind,
        )
        .join(Document, Document.id == DocumentGeneration.document_id)
        .where(
            DocumentGeneration.user_id == user_id,
            sa.tuple_(DocumentGeneration.created_at, Document.posting_id).in_(list(clicks)),
            DocumentGeneration.run_id.is_(None),
            DocumentGeneration.status.in_((GenerationStatus.queued, GenerationStatus.waiting)),
        )
        .order_by(DocumentGeneration.id)
    ).all()
    for generation in waiting:
        agent = _WRITER.get(generation.kind)
        if agent is not None:
            found[clicks[(generation.created_at, generation.posting_id)]].steps.append(
                _step(f"generation:{generation.id}", agent, NAMES[agent], "waiting")
            )
    return found


def _application_status(state: State) -> str:
    if state in _APPLICATION_RUNNING:
        return "running"
    if state in _APPLICATION_WAITING:
        return "waiting"
    if state in _APPLICATION_DONE:
        return "success"
    return "cancelled" if state is State.cancelled else "failure"


def _applications(
    db: Session, user_id: uuid.UUID, keys: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Found]:
    rows = db.execute(
        sa.select(
            Application.id,
            Application.state,
            Application.created_at,
            Application.updated_at,
            Job.id.label("job_id"),
            Job.title,
            Job.company,
        )
        .outerjoin(Job, sa.and_(Job.id == Application.posting_id, Job.user_id == user_id))
        .where(Application.id.in_(keys), Application.user_id == user_id)
    ).all()
    found: dict[uuid.UUID, Found] = {}
    for row in rows:
        status = _application_status(row.state)
        finished = None if status in ("running", "waiting") else row.updated_at
        step = _step(
            f"application:{row.id}",
            "fill",
            "Application",
            status,
            row.created_at,
            finished,
            _APPLICATION_STATE[row.state],
        )
        subject = f"{row.title} at {row.company}" if row.job_id else None
        said = {step["id"]: step["note"]} if status == "failure" else {}
        found[row.id] = Found([step], subject, row.job_id, reasons=said)
    return found


_READERS = {
    "job_search": _sweeps,
    "fill_profile": _ingestions,
    "application": _applications,
}


def read(
    db: Session, user_id: uuid.UUID, runs: Iterable[tuple[str, str, Sequence[uuid.UUID]]]
) -> dict[str, Found]:
    """The app's own steps and subject of the caller's workflow runs, each given as its
    workflow, its id and its agent runs' ids, by its id; a run no row speaks of is left out."""
    asked: dict[str, dict[uuid.UUID, Sequence[uuid.UUID]]] = {}
    named: dict[uuid.UUID, str] = {}
    for workflow, key, run_ids in runs:
        found = _uuid(key)
        if found is not None:
            asked.setdefault(workflow, {})[found] = run_ids
            named[found] = key
    out: dict[uuid.UUID, Found] = {}
    for workflow, reader in _READERS.items():
        if asked.get(workflow):
            out.update(reader(db, user_id, list(asked[workflow])))
    documents = {**asked.get("posting_documents", {}), **asked.get("base_cv", {})}
    if documents:
        out.update(_documents(db, user_id, documents))
    return {named[key]: found for key, found in out.items()}
