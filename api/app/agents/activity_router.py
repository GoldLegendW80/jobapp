"""GET /api/activity: the caller's workflow runs, as Activity shows them (agents A10).

A workflow run groups the caller's agent runs of one thing (`workflows.group_of`). Each person
reads only their own runs: every query is bound to the caller's id. An agent step's log is the
agent run's own, read through /api/agents/runs/{id} and its stream.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app.agents import catalog, workflows
from app.agents.models import AgentRun, RunStatus
from app.agents.runs_router import CurrentUser, _no_store, sessions_of
from app.jobs.models import Job

router = APIRouter(prefix="/api/activity", tags=["activity"])

# The newest agent runs a list groups: enough for 50 workflow runs of many steps each.
SCAN_MAX = 1000
LIST_MAX = 100
NOT_FOUND = "Run not found."

_STATUS = {
    RunStatus.queued: "queued",
    RunStatus.running: "running",
    RunStatus.done: "success",
    RunStatus.failed: "failure",
    RunStatus.cancelled: "cancelled",
}


class NodeOut(BaseModel):
    id: str
    kind: str
    label: str
    column: int
    row: int
    note: str
    group: str


class FlowOut(BaseModel):
    nodes: list[NodeOut]
    edges: list[dict[str, str]]


class WorkflowOut(BaseModel):
    key: str
    title: str
    sentence: str
    flow: FlowOut | None


class WorkflowList(BaseModel):
    items: list[WorkflowOut]


class TriggerOut(BaseModel):
    kind: str
    words: str
    node: str | None


class SubjectOut(BaseModel):
    words: str
    job_id: uuid.UUID | None


class WorkflowRunOut(BaseModel):
    id: str
    workflow: str
    workflow_title: str
    trigger: TriggerOut
    subject: SubjectOut | None
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_s: float | None
    agent_runs: int


class WorkflowRunList(BaseModel):
    items: list[WorkflowRunOut]


class StepOut(BaseModel):
    id: str
    kind: str
    node: str | None
    name: str
    agent: str | None
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    duration_s: float | None
    private: bool
    note: str | None


class WorkflowRunDetail(WorkflowRunOut):
    flow: FlowOut | None
    steps: list[StepOut]
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    credential_kind: str | None


def _flow_out(workflow: workflows.Workflow) -> FlowOut | None:
    flow = workflows.flow_of(workflow)
    if flow is None:
        return None
    return FlowOut(
        nodes=[NodeOut(**node.__dict__) for node in flow.nodes],
        edges=[{"from": e.source, "to": e.target, "label": e.label} for e in flow.edges],
    )


@router.get("/workflows", response_model=WorkflowList, dependencies=[Depends(_no_store)])
def list_workflows(user: CurrentUser) -> WorkflowList:
    """Every workflow, in the order of the side list, each with its graph."""
    return WorkflowList(
        items=[
            WorkflowOut(key=w.key, title=w.title, sentence=w.sentence, flow=_flow_out(w))
            for w in workflows.WORKFLOWS
        ]
    )


_COLUMNS = (
    AgentRun.id,
    AgentRun.agent,
    AgentRun.job_id,
    AgentRun.status,
    AgentRun.created_at,
    AgentRun.started_at,
    AgentRun.finished_at,
    AgentRun.input_tokens,
    AgentRun.output_tokens,
    AgentRun.cost_usd,
    AgentRun.credential_kind,
    AgentRun.workflow,
    AgentRun.workflow_run_id,
    AgentRun.workflow_trigger,
    # Only the ids an older run is grouped by, never the rest of its input.
    AgentRun.input["ingestion_run_id"].astext.label("ingestion_run_id"),
    AgentRun.input["application_id"].astext.label("application_id"),
    AgentRun.input["generation_id"].astext.label("generation_id"),
)


class _Group:
    def __init__(self, key: str, workflow: str, trigger: workflows.Trigger) -> None:
        self.key = key
        self.workflow = workflow
        self.trigger = trigger
        self.rows: list[sa.Row[Any]] = []


def _group(rows: Sequence[sa.Row[Any]]) -> list[_Group]:
    groups: dict[str, _Group] = {}
    for row in rows:
        held = {
            name: getattr(row, name)
            for name in ("ingestion_run_id", "application_id", "generation_id")
            if getattr(row, name)
        }
        workflow, key, trigger = workflows.group_of(
            row.agent,
            str(row.id),
            row.workflow,
            str(row.workflow_run_id) if row.workflow_run_id else None,
            row.workflow_trigger,
            held,
        )
        group = groups.setdefault(key, _Group(key, workflow, trigger))
        group.rows.append(row)
    for group in groups.values():
        group.rows.sort(key=lambda row: (row.created_at, str(row.id)))
    return sorted(groups.values(), key=lambda g: g.rows[0].created_at, reverse=True)


def _status(rows: Sequence[sa.Row[Any]]) -> str:
    statuses = {_STATUS[row.status] for row in rows}
    for status_ in ("running", "queued", "failure"):
        if status_ in statuses:
            return status_
    return "cancelled" if statuses == {"cancelled"} else "success"


def _subjects(db: Session, user_id: uuid.UUID, groups: list[_Group]) -> dict[uuid.UUID, str]:
    ids = {row.job_id for group in groups for row in group.rows if row.job_id}
    if not ids:
        return {}
    found = db.execute(
        sa.select(Job.id, Job.company, Job.title).where(Job.user_id == user_id, Job.id.in_(ids))
    ).all()
    return {job_id: f"{title} at {company}" for job_id, company, title in found}


def _summary(group: _Group, subjects: dict[uuid.UUID, str]) -> dict[str, Any]:
    rows = group.rows
    workflow = workflows.BY_KEY[group.workflow]
    job_id = next((row.job_id for row in rows if row.job_id in subjects), None)
    started = [row.started_at for row in rows if row.started_at]
    state = _status(rows)
    finished = (
        max((row.finished_at for row in rows if row.finished_at), default=None)
        if state not in ("running", "queued")
        else None
    )
    return {
        "id": group.key,
        "workflow": workflow.key,
        "workflow_title": workflow.title,
        "trigger": TriggerOut(
            kind="recorded" if group.trigger.node else "unknown",
            words=group.trigger.words,
            node=group.trigger.node if group.trigger.node in workflow.nodes else None,
        ),
        "subject": SubjectOut(words=subjects[job_id], job_id=job_id) if job_id else None,
        "status": state,
        "created_at": rows[0].created_at,
        "started_at": min(started) if started else None,
        "finished_at": finished,
        "duration_s": (finished - min(started)).total_seconds() if finished and started else None,
        "agent_runs": len(rows),
    }


def _scan(db: Session, user_id: uuid.UUID) -> Sequence[sa.Row[Any]]:
    return db.execute(
        sa.select(*_COLUMNS)
        .where(AgentRun.user_id == user_id)
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(SCAN_MAX)
    ).all()


def _list(
    sessions: sessionmaker[Session], user_id: uuid.UUID, workflow: str | None, limit: int
) -> WorkflowRunList:
    with sessions() as db:
        groups = [g for g in _group(_scan(db, user_id)) if workflow in (None, g.workflow)]
        groups = groups[:limit]
        subjects = _subjects(db, user_id, groups)
        return WorkflowRunList(items=[WorkflowRunOut(**_summary(g, subjects)) for g in groups])


@router.get("/runs", response_model=WorkflowRunList, dependencies=[Depends(_no_store)])
async def list_workflow_runs(
    user: CurrentUser,
    request: Request,
    workflow: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=LIST_MAX)] = 50,
) -> WorkflowRunList:
    """The caller's workflow runs, newest first: all of them, or one workflow's."""
    return await run_in_threadpool(_list, sessions_of(request), user.id, workflow, limit)


def _detail(
    sessions: sessionmaker[Session], user_id: uuid.UUID, wanted: uuid.UUID
) -> WorkflowRunDetail | None:
    text = str(wanted)
    with sessions() as db:
        rows = db.execute(
            sa.select(*_COLUMNS).where(
                AgentRun.user_id == user_id,
                sa.or_(
                    AgentRun.id == wanted,
                    AgentRun.workflow_run_id == wanted,
                    AgentRun.input["ingestion_run_id"].astext == text,
                    AgentRun.input["application_id"].astext == text,
                    AgentRun.input["generation_id"].astext == text,
                ),
            )
        ).all()
        groups = _group(rows)
        # An agent run's own id finds its workflow run (an old /runs/:id link).
        group = next((g for g in groups if g.key == text), None) or next(
            (g for g in groups if any(row.id == wanted for row in g.rows)), None
        )
        if group is None:
            return None
        if group.key != text:
            try:
                return _detail(sessions, user_id, uuid.UUID(group.key))
            except ValueError:
                return None
        subjects = _subjects(db, user_id, [group])
        definitions = catalog.load()
        workflow = workflows.BY_KEY[group.workflow]
        tokens_in = [row.input_tokens for row in group.rows if row.input_tokens is not None]
        tokens_out = [row.output_tokens for row in group.rows if row.output_tokens is not None]
        costs = [float(row.cost_usd) for row in group.rows if row.cost_usd is not None]
        kinds = {row.credential_kind.value for row in group.rows if row.credential_kind}
        return WorkflowRunDetail(
            **_summary(group, subjects),
            flow=_flow_out(workflow),
            steps=[
                StepOut(
                    id=str(row.id),
                    kind="agent",
                    node=row.agent if row.agent in workflow.nodes else None,
                    name=catalog.NAMES.get(row.agent, row.agent),
                    agent=row.agent,
                    status=_STATUS[row.status],
                    started_at=row.started_at,
                    finished_at=row.finished_at,
                    duration_s=(row.finished_at - row.started_at).total_seconds()
                    if row.started_at and row.finished_at
                    else None,
                    private=bool(definitions.get(row.agent) and definitions[row.agent].private_io),
                    note=None,
                )
                for row in group.rows
            ],
            input_tokens=sum(tokens_in) if tokens_in else None,
            output_tokens=sum(tokens_out) if tokens_out else None,
            cost_usd=round(sum(costs), 6) if costs else None,
            credential_kind=kinds.pop() if len(kinds) == 1 else None,
        )


@router.get("/runs/{run_id}", response_model=WorkflowRunDetail, dependencies=[Depends(_no_store)])
async def read_workflow_run(run_id: str, user: CurrentUser, request: Request) -> WorkflowRunDetail:
    """One workflow run of the caller, by its id or the id of one of its agent runs."""
    try:
        wanted = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND) from None
    found = await run_in_threadpool(_detail, sessions_of(request), user.id, wanted)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return found
