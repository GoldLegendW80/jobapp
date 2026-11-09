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
from app.apply import agent as apply_agent
from app.apply.models import Mode
from app.identity.models import User
from app.ingestion.plan import StepKind
from app.ingestion.runs_models import IngestionRun, IngestionRunStep, StepStatus
from app.ingestion.runs_models import RunStatus as IngestionStatus
from app.jobs import sweep
from app.jobs.models import SweepTrigger
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
    "app/apply/agent.py": {("application", "apply"), ("application", "like")},
    "app/apply/applier.py": {("application", "apply")},
    "app/apply/followup_service.py": {("follow_up", "draft")},
    "app/apply/site_profile.py": {("site_profile", "site")},
    "app/documents/chat.py": {("document_chat", "chat")},
    "app/documents/flows.py": {
        ("base_cv", ""),
        ("base_cv", "capture"),
        ("base_cv", "new_base"),
        ("posting_documents", "write"),
    },
    "app/documents/interview_prep.py": {
        ("interview_prep", "prepare"),
        ("interview_prep", "practice"),
    },
    "app/documents/offer_notes.py": {("offer_letter", "notes_chat"), ("offer_letter", "offer")},
    "app/ingestion/engine.py": {("fill_profile", "read"), ("fill_profile", "onboarding_read")},
    "app/jobs/assess_router.py": {("assess", "assess")},
    "app/jobs/reader.py": {("posting_reading", "backlog")},
    "app/jobs/sweep.py": {
        ("job_search", "onboarding"),
        ("job_search", "schedule"),
        ("job_search", "search"),
    },
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
            # Each trigger has its box in the workflow's graph, where the run page marks it; a
            # step that joins a run started by another records none.
            if trigger:
                assert workflows.TRIGGERS[trigger].node in workflows.BY_KEY[workflow].nodes


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
        "words": "Your click on Write now",
        "node": "click",
    }


def test_a_step_joins_the_workflow_run_of_the_run_it_follows_without_its_trigger(
    factory: sessionmaker[Session], alice: str
) -> None:
    me = _user(factory, ALICE)
    click = uuid.uuid4()
    writer = _run(
        factory, me, "documents_cv", 0, flow=runs.WorkflowRef("posting_documents", click, "write")
    )
    alone = _run(
        factory,
        me,
        "posting_reader",
        1,
        flow=runs.WorkflowRef("posting_reading", trigger="backlog"),
    )
    older = _run(factory, me, "documents_cv", 2)
    with factory() as db:
        assert runs.joining(db, writer) == runs.WorkflowRef("posting_documents", click)
        # A run recorded with no id of its own is its workflow run.
        assert runs.joining(db, alone) == runs.WorkflowRef("posting_reading", alone)
        assert runs.joining(db, older) is None
        assert runs.joining(db, None) is None


def _plan(scope: dict[str, Any] | None) -> sweep.Plan:
    return sweep.Plan(uuid.uuid4(), uuid.uuid4(), [], None, None, search_scope=scope)  # type: ignore[arg-type]


def test_a_sweeps_agents_record_its_run_with_its_trigger(
    factory: sessionmaker[Session], alice: str
) -> None:
    by_hand = _plan({"trigger": "manual"})
    assert by_hand.flow == runs.WorkflowRef("job_search", by_hand.sweep_id, "search")
    assert _plan({"trigger": "schedule"}).flow.trigger == "schedule"
    assert _plan({"trigger": "manual", "started_by": sweep.ONBOARDING}).flow.trigger == "onboarding"
    assert _plan(None).flow.trigger == "search"
    me = _user(factory, ALICE)
    with factory() as db:
        first = sweep.start(db, me, SweepTrigger.manual, T0, sweep.ONBOARDING)
        assert first.stats["search_scope"]["started_by"] == "onboarding"
        db.rollback()
        other = sweep.start(db, me, SweepTrigger.manual, T0)
        assert "started_by" not in other.stats["search_scope"]
        db.rollback()


def test_an_applications_agents_record_its_run_a_like_in_full_auto_else_apply() -> None:
    application = uuid.uuid4()
    assert apply_agent.application_flow(application, Mode.full_auto) == runs.WorkflowRef(
        "application", application, "like"
    )
    for mode in (Mode.direct, Mode.review, Mode.manual):
        assert apply_agent.application_flow(application, mode).trigger == "apply"


def test_a_sweeps_run_shows_the_sweep_and_its_sources_from_their_rows_and_waiting_websites(
    factory: sessionmaker[Session], api: Api, alice: str, bob: str
) -> None:
    me = _user(factory, ALICE)
    with factory() as db:
        row = sweep.start(db, me, SweepTrigger.manual, T0)
        row.stats = {
            **row.stats,
            "sources": [
                {
                    "source_id": "a",
                    "kind": "arbeitnow",
                    "phase": "done",
                    "outcome": "complete",
                    "found": 4,
                },
                {
                    "source_id": "b",
                    "mode": "llm_browser",
                    "host": "jobs.example.org",
                    "phase": "running",
                    "outcome": None,
                },
                {
                    "source_id": "c",
                    "mode": "llm_browser",
                    "host": "careers.example.org",
                    "phase": "pending",
                    "outcome": "not_attempted",
                },
            ],
            "reading": {"read": 2, "refused": 0, "failed": 0, "left": 1},
        }
        db.commit()
        sweep_id = row.id
    agent_run = _run(
        factory,
        me,
        "browser_discovery",
        1,
        flow=runs.WorkflowRef("job_search", sweep_id, "search"),
        status=RunStatus.running,
    )
    detail = api.get(f"/api/activity/runs/{sweep_id}", token=alice).json()
    steps = [(s["kind"], s["node"], s["name"], s["status"], s["note"]) for s in detail["steps"]]
    assert steps == [
        ("system", "sweep", "Sweep", "running", "2 postings read, 1 left"),
        ("agent", "browser_discovery", "Website search", "running", None),
        ("system", "sweep", "arbeitnow", "success", "4 found"),
        ("system", "browser_discovery", "careers.example.org", "waiting", None),
    ]
    assert detail["steps"][1]["id"] == str(agent_run)
    assert detail["steps"][-1]["started_at"] is None
    # Another person's sweep id is not found, and its rows are never read.
    assert api.get(f"/api/activity/runs/{sweep_id}", token=bob).status_code == 404


def test_an_ingestion_runs_steps_not_started_show_as_waiting(
    factory: sessionmaker[Session], api: Api, alice: str
) -> None:
    me = _user(factory, ALICE)
    run_id = uuid.uuid4()
    with factory() as db:
        db.add(
            IngestionRun(
                id=run_id,
                user_id=me,
                document_ids=[uuid.uuid4()],
                base_revision=0,
                status=IngestionStatus.running,
                deadline_at=T0 + timedelta(hours=1),
                estimate_low=1,
                estimate_high=2,
                token_ceiling=4,
                tokens_spent=0,
                ceiling_history=[],
                created_at=T0,
            )
        )
        for position, kind in enumerate((StepKind.transcribe, StepKind.extract)):
            db.add(
                IngestionRunStep(
                    ingestion_run_id=run_id,
                    position=position,
                    kind=kind,
                    document_id=uuid.uuid4(),
                    sha256=b"\0" * 32,
                    part=None if kind is StepKind.transcribe else 0,
                    image_positions=[0] if kind is StepKind.transcribe else None,
                    identity_hash="0" * 64,
                )
            )
        db.commit()
    _run(
        factory,
        me,
        "document_transcribe",
        1,
        flow=runs.WorkflowRef("fill_profile", run_id, "onboarding_read"),
        status=RunStatus.running,
    )
    with factory() as db:
        db.execute(
            sa.update(IngestionRunStep)
            .where(IngestionRunStep.ingestion_run_id == run_id, IngestionRunStep.position == 0)
            .values(status=StepStatus.running)
        )
        db.commit()
    detail = api.get(f"/api/activity/runs/{run_id}", token=alice).json()
    assert detail["trigger"]["words"] == "Your click on Build my profile"
    assert [(s["kind"], s["node"], s["status"]) for s in detail["steps"]] == [
        ("system", "run", "running"),
        ("agent", "document_transcribe", "running"),
        ("system", "profile_extract", "waiting"),
    ]
