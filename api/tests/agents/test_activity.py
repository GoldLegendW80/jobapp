"""Activity as workflow runs (agents A10): the workflows and their graphs, the grouping of a
person's agent runs into workflow runs, and that each person reads only their own."""

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import catalog, runs, workflows
from app.agents.models import AgentRun, RunStatus
from app.identity.models import User
from tests.agents.conftest import ALICE, BOB
from tests.identity.api_helpers import Api

T0 = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)


def _user(factory: sessionmaker[Session], email: str) -> uuid.UUID:
    with factory() as db:
        return db.scalars(sa.select(User.id).where(User.email == email)).one()


def _run(
    factory: sessionmaker[Session],
    user_id: uuid.UUID,
    agent: str,
    minute: int,
    data: dict[str, Any] | None = None,
    flow: runs.WorkflowRef | None = None,
    status: RunStatus = RunStatus.done,
) -> uuid.UUID:
    with factory() as db:
        row = runs.create_run(
            db, user_id, agent, None, data or {}, "claude-opus-5-5", T0, flow=flow
        )
        db.execute(
            sa.update(AgentRun)
            .where(AgentRun.id == row.id)
            .values(
                status=status,
                created_at=T0 + timedelta(minutes=minute),
                started_at=T0 + timedelta(minutes=minute),
                finished_at=T0 + timedelta(minutes=minute, seconds=30)
                if status == RunStatus.done
                else None,
            )
        )
        db.commit()
        return row.id


def test_each_workflow_draws_a_part_of_its_categorys_flow_and_every_agent_has_one() -> None:
    for workflow in workflows.WORKFLOWS:
        if not workflow.category:
            continue
        drawn = {node.id for node in catalog.FLOWS[workflow.category].nodes}
        assert set(workflow.nodes) <= drawn, workflow.key
        flow = workflows.flow_of(workflow)
        assert flow is not None
        assert {node.id for node in flow.nodes} == set(workflow.nodes)
        # The columns and rows are packed: none is left empty.
        assert {node.column for node in flow.nodes} == set(
            range(max(node.column for node in flow.nodes) + 1)
        )
        assert all(e.source in workflow.nodes and e.target in workflow.nodes for e in flow.edges)
    started = set(catalog.CATEGORY_OF) - {"echo", "documents_proposal"}
    assert started == set(workflows.AGENT_WORKFLOW)
    for agent, key in workflows.AGENT_WORKFLOW.items():
        assert agent in workflows.BY_KEY[key].nodes, agent
    for trigger in workflows.TRIGGERS.values():
        assert trigger.node is None or any(trigger.node in w.nodes for w in workflows.WORKFLOWS)
        assert " - " not in trigger.words
        assert "\u2014" not in trigger.words


def test_the_workflows_route_serves_each_graph(api: Api, alice: str) -> None:
    answer = api.get("/api/activity/workflows", token=alice)
    assert answer.status_code == 200
    items = answer.json()["items"]
    assert [item["key"] for item in items] == [w.key for w in workflows.WORKFLOWS]
    search = items[0]["flow"]
    assert {"from": "sweep", "to": "browser_discovery", "label": "one run per website"} in search[
        "edges"
    ]
    assert items[-1]["flow"] is None


def test_runs_group_by_what_their_starter_recorded_else_their_input_else_alone(
    factory: sessionmaker[Session], api: Api, alice: str
) -> None:
    me = _user(factory, ALICE)
    sweep = uuid.uuid4()
    application = str(uuid.uuid4())
    recorded = runs.WorkflowRef("job_search", sweep, "schedule")
    first = _run(factory, me, "browser_discovery", 0, flow=recorded)
    _run(factory, me, "browser_discovery", 1, flow=recorded)
    _run(factory, me, "posting_reader", 5, flow=recorded, status=RunStatus.running)
    answer_run = _run(factory, me, "apply_answer", 10, {"application_id": application})
    _run(
        factory,
        me,
        "apply_check",
        11,
        {"application_id": application},
        status=RunStatus.failed,
    )
    chat = _run(factory, me, "job_chat", 20)

    items = api.get("/api/activity/runs", token=alice).json()["items"]
    assert [(i["workflow"], i["agent_runs"], i["status"]) for i in items] == [
        ("job_chat", 1, "success"),
        ("application", 2, "failure"),
        ("job_search", 3, "running"),
    ]
    assert items[0]["id"] == str(chat)
    assert items[0]["trigger"] == {
        "kind": "recorded",
        "words": "Your message in the chat",
        "node": "message",
    }
    assert items[1]["id"] == application
    assert items[1]["trigger"]["words"] == "Not recorded for this run"
    assert items[2]["id"] == str(sweep)
    assert items[2]["trigger"]["node"] == "schedule"
    assert items[2]["finished_at"] is None

    only = api.get("/api/activity/runs?workflow=application", token=alice).json()["items"]
    assert [i["id"] for i in only] == [application]

    detail = api.get(f"/api/activity/runs/{sweep}", token=alice).json()
    assert [s["node"] for s in detail["steps"]] == [
        "browser_discovery",
        "browser_discovery",
        "posting_reader",
    ]
    assert detail["steps"][0]["id"] == str(first)
    assert detail["flow"]["nodes"]
    # An old /runs/:id link: the id of one agent run finds its workflow run.
    by_step = api.get(f"/api/activity/runs/{answer_run}", token=alice).json()
    assert by_step["id"] == application
    assert by_step["agent_runs"] == 2


def test_a_private_agents_step_says_so(
    factory: sessionmaker[Session], api: Api, alice: str
) -> None:
    me = _user(factory, ALICE)
    private = next(name for name, d in catalog.load().items() if d.private_io)
    run_id = _run(factory, me, private, 0)
    detail = api.get(f"/api/activity/runs/{run_id}", token=alice).json()
    assert detail["steps"][0]["private"] is True


def test_each_person_reads_only_their_own_runs(
    factory: sessionmaker[Session], api: Api, alice: str, bob: str
) -> None:
    alices = _run(factory, _user(factory, ALICE), "job_chat", 0)
    shared = uuid.uuid4()
    _run(factory, _user(factory, BOB), "job_chat", 1, flow=runs.WorkflowRef("job_chat", shared))
    _run(factory, _user(factory, ALICE), "job_chat", 2, flow=runs.WorkflowRef("job_chat", shared))
    assert [i["agent_runs"] for i in api.get("/api/activity/runs", token=bob).json()["items"]] == [
        1
    ]
    assert api.get(f"/api/activity/runs/{alices}", token=bob).status_code == 404
    seen = api.get(f"/api/activity/runs/{shared}", token=alice).json()
    assert seen["agent_runs"] == 1
    assert api.get("/api/activity/runs/not-an-id", token=alice).status_code == 404
    assert api.get("/api/activity/runs").status_code == 401


# The starters that record their workflow run today, by file; the rest are grouped from their
# input or shown alone until they pass one (tasks/agents/todo.md, A10).
RECORDING = {
    "app/agents/chat.py": {("job_chat", "message")},
    "app/agents/operator.py": {("assess", "with_agent")},
    "app/apply/followup_service.py": {("follow_up", "draft")},
    "app/documents/chat.py": {("document_chat", "chat")},
    "app/documents/flows.py": {("posting_documents", "write")},
    "app/documents/interview_prep.py": {
        ("interview_prep", "prepare"),
        ("interview_prep", "practice"),
    },
    "app/documents/offer_notes.py": {("offer_letter", "notes_chat"), ("offer_letter", "offer")},
    "app/documents/writer_base.py": {("base_cv", "new_base")},
    "app/ingestion/engine.py": {("fill_profile", "read")},
    "app/jobs/assess_router.py": {("assess", "assess")},
    "app/profile/about.py": {("about_you", "about")},
    "app/profile/titles.py": {("job_titles", "titles_tick"), ("job_titles", "ideas")},
}


def _recorded(path: Path) -> set[tuple[str, str]]:
    """The (workflow, trigger) pairs the WorkflowRef calls of one file can record."""
    found: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if not (isinstance(node, ast.Call) and ast.unparse(node.func).endswith("WorkflowRef")):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            found.add(("", ""))
            continue
        said = node.args[2] if len(node.args) > 2 else None
        said = next((k.value for k in node.keywords if k.arg == "trigger"), said)
        words = (
            [
                n.value
                for n in ast.walk(said)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
            if said is not None
            else [""]
        )
        found |= {(node.args[0].value, word) for word in words}
    return found


def test_every_starter_records_a_workflow_and_a_trigger_activity_draws() -> None:
    app = Path(__file__).resolve().parents[2]
    seen: dict[str, set[tuple[str, str]]] = {}
    for path in sorted((app / "app").rglob("*.py")):
        pairs = _recorded(path)
        if pairs and path.name != "runs.py":
            seen[str(path.relative_to(app))] = pairs
    assert seen == RECORDING
    for pairs in seen.values():
        for workflow, trigger in pairs:
            assert workflow in workflows.BY_KEY, workflow
            # Each trigger has its box in the workflow's graph, where the run page marks it.
            assert workflows.TRIGGERS[trigger].node in workflows.BY_KEY[workflow].nodes, trigger


def test_a_runs_trigger_is_its_first_steps_when_a_later_step_records_none(
    factory: sessionmaker[Session], api: Api, alice: str
) -> None:
    me = _user(factory, ALICE)
    click = uuid.uuid4()
    _run(factory, me, "documents_cv", 0, flow=runs.WorkflowRef("posting_documents", click, "write"))
    _run(factory, me, "documents_cv", 4, flow=runs.WorkflowRef("posting_documents", click))

    items = api.get("/api/activity/runs", token=alice).json()["items"]
    assert [(i["id"], i["agent_runs"]) for i in items] == [(str(click), 2)]
    assert items[0]["trigger"] == {
        "kind": "recorded",
        "words": "Your click on Write",
        "node": "click",
    }
