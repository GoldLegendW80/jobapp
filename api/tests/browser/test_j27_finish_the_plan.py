"""Jobs J27 (#821): the web source finishes its plan even after it found something.

Production acceptance of J26 (2026-11-19, sweep a6c1fe73): the web agent ran four WebSearch,
reported one posting and ended, its plan (saved titles, adjacent roles, the national boards of
two places, the niche AI boards by site:) never searched. Now every report_postings answer says
what is left and to go on, report_no_postings is refused while the plan is left even after a
posting, and a turn ended without it while the plan is left is continued in the same session by
the runner, at most FOLLOW_UPS_MAX times, then the source ends `plan_unfinished` (not retried).
Every stream here is replayed by the fake Claude Code through the runner, on fake pages."""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from claude_agent_sdk import ResultMessage

from app.agents import backend, definitions, fake_backend, runner
from app.agents.models import AgentEvent, RunStatus
from app.browser import finder
from app.browser.sources import interactive
from app.jobs import sweep
from tests.browser.runtime_support import (  # noqa: F401  (fixtures)
    Runtime,
    alice,
    clock,
    db,
    engine,
    factory,
    migrated_url,
    one_connection,
    runs_of,
    runtime,
)
from tests.browser.test_j23_web_breadth import (
    AI_BOARDS,
    COVERING,
    NICHE,
    NICHE_PAGE,
    PLAN,
    Browser,
    fetched,
    finder_tools_names,
    said,
    search,
    web_session,
)

APP = "mcp__app__"
POSTING = {
    "title": "Ingénieur IA générative (CDI)",
    "url": NICHE,
    "text": "Agents, RAG and Python.",
    "location": "Saint-Denis, La Réunion",
}
INIT = {
    "message": {
        "type": "system",
        "subtype": "init",
        "session_id": "{session_id}",
        "uuid": "00000000-0000-4000-8000-000000000001",
        "model": "{model}",
        "tools": ["WebSearch", *(APP + n for n in finder_tools_names())],
        "mcp_servers": [{"name": "app", "status": "connected"}],
    }
}


def result(n: int) -> dict[str, Any]:
    return {
        "message": {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": 3000,
            "duration_api_ms": 2500,
            "num_turns": 3,
            "session_id": "{session_id}",
            "uuid": f"00000000-0000-4000-8000-0000000009{n:02d}",
            "total_cost_usd": 0.0,
            "usage": {"input_tokens": 9000, "output_tokens": 120},
            "result": "One posting reported.",
        }
    }


# The production run of 2026-11-19: a narrow search, one posting fetched and reported, then the
# turn ends with no report_no_postings.
FOUND_ONE_THEN_ENDED = [
    INIT,
    said(1, ("WebSearch", {"query": "ingénieur IA générative CDI La Réunion"})),
    said(2, (APP + "web_fetch", {"url": NICHE})),
    said(3, (APP + "report_postings", {"postings": [POSTING]})),
    said(4),
    result(1),
]


def stream(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "description": "J27 test stream.",
        "web_search": [{"title": "Ingénieur IA générative", "url": NICHE}],
        "steps": steps,
    }


def run_web(
    runtime: Runtime,  # noqa: F811
    alice,  # noqa: F811
    monkeypatch,
    tmp_path: Path,
    steps: list[dict[str, Any]],
) -> tuple[finder.FinderSession, list[finder.VerifiedFinding], list[tuple[str, Any]]]:
    """The stream replayed through the runner for a La Réunion web search: the session, the
    postings it stored, and the run's events (kind, data)."""
    (tmp_path / "web_discovery.json").write_text(json.dumps(stream(steps)), encoding="utf-8")
    monkeypatch.setattr(fake_backend, "STREAMS", tmp_path)
    runtime.give_key(alice)
    session = web_session(browser=Browser({NICHE: fetched(NICHE, NICHE_PAGE)}))
    session.user_id = str(alice)
    stored: list[finder.VerifiedFinding] = []

    async def store(batch: finder.VerifiedBatch) -> None:
        stored.extend(batch.findings)

    session.on_verified = store
    asyncio.run(finder.RuntimeFinder(lambda: runtime.context())(session))
    [run] = runs_of(runtime.factory, alice)
    assert (run.agent, run.status) == (finder.WEB_NAME, RunStatus.done), run.error
    with runtime.factory() as database:
        events = [
            (event.kind, event.data)
            for event in database.scalars(
                sa.select(AgentEvent).where(AgentEvent.run_id == run.id).order_by(AgentEvent.seq)
            )
        ]
    return session, stored, events


def answers(events: list[tuple[str, Any]], name: str) -> list[str]:
    """The answers of the app tool `name`, in order (the web run's events are private: the
    error text of an app tool is kept, a success only by size; so the refusals are read)."""
    calls = {
        data["id"]: data.get("name")
        for kind, data in events
        if kind == "tool_call" and isinstance(data, dict)
    }
    return [
        data.get("error") or data.get("content") or ""
        for kind, data in events
        if kind == "tool_result" and calls.get(data.get("id")) == APP + name
    ]


def test_a_stream_that_reports_one_posting_then_ends_is_continued_until_the_plan_is_searched(
    runtime: Runtime,  # noqa: F811
    alice,  # noqa: F811
    monkeypatch,
    tmp_path: Path,
) -> None:
    follow_up = [
        # The give up right after the follow up is refused: the posting spares no family.
        said(5, (APP + "report_no_postings", {"reason": "no_match"})),
        said(6, *(("WebSearch", {"query": q}) for q in PLAN)),
        said(7, (APP + "report_no_postings", {"reason": "no_match"})),
        said(8),
        result(2),
    ]
    session, stored, events = run_web(
        runtime, alice, monkeypatch, tmp_path, FOUND_ONE_THEN_ENDED + follow_up
    )
    assert [f.url for f in stored] == [NICHE]
    kinds = [kind for kind, _ in events]
    assert [data for kind, data in events if kind == "follow_up"] == [{"attempt": 1, "of": 2}]
    assert "unfinished" not in kinds
    # The first give up of the follow up's turn was refused with what is left, the second held.
    refused, held = answers(events, "report_no_postings")
    assert refused.startswith("Refused: do not give up yet")
    assert "site:" in refused
    assert held == ""  # no error: a private run keeps a success by its size only
    assert session.web_searches == 1 + len(PLAN)
    assert (session.finished, session.outcome, session.web_left()) == (True, "no_match", "")
    assert interactive.outcome(session) is None  # searched, one posting kept


def test_every_report_postings_answer_says_what_is_left_and_to_go_on() -> None:
    session = web_session(browser=Browser({NICHE: fetched(NICHE, NICHE_PAGE)}))
    search(session, "ingénieur IA générative CDI La Réunion")
    assert asyncio.run(session.web_fetch(NICHE))[1] is False
    said_, error = session.report([POSTING])
    answer = json.loads(said_)
    assert (error, answer["kept"]) == (False, 1)
    assert answer["plan_left"] == session.web_left() != ""
    assert answer["next"].startswith("Go on searching")
    # A posting kept spares nothing of the plan (J27): the give up is still refused.
    refused, error = session.report_none("no_match")
    assert error
    assert refused.startswith("Refused: do not give up yet")
    assert session.plan_unfinished()
    assert interactive.outcome(session) == "plan_unfinished"
    search(session, *PLAN)
    answer = json.loads(session.report([])[0])
    assert "plan_left" not in answer
    assert "next" not in answer
    assert session.go_on() is None  # covered: a turn ended now ends the run
    assert not session.plan_unfinished()
    assert interactive.outcome(session) is None


def test_a_stream_that_covers_the_plan_finishes_without_a_follow_up(
    runtime: Runtime,  # noqa: F811
    alice,  # noqa: F811
    monkeypatch,
    tmp_path: Path,
) -> None:
    steps = [
        INIT,
        said(1, *(("WebSearch", {"query": q}) for q in PLAN)),
        said(2, (APP + "web_fetch", {"url": NICHE})),
        said(3, (APP + "report_postings", {"postings": [POSTING]})),
        said(4),  # ends its turn without report_no_postings: the plan is searched, so it ends
        result(1),
    ]
    session, stored, events = run_web(runtime, alice, monkeypatch, tmp_path, steps)
    assert [f.url for f in stored] == [NICHE]
    kinds = [kind for kind, _ in events]
    assert "follow_up" not in kinds
    assert "unfinished" not in kinds
    assert session.web_left() == ""
    assert interactive.outcome(session) is None


def test_the_follow_ups_are_bounded_then_the_source_ends_plan_unfinished(
    runtime: Runtime,  # noqa: F811
    alice,  # noqa: F811
    monkeypatch,
    tmp_path: Path,
) -> None:
    # The model ends each follow up's turn at once (no step left after the first result).
    session, stored, events = run_web(runtime, alice, monkeypatch, tmp_path, FOUND_ONE_THEN_ENDED)
    assert runner.FOLLOW_UPS_MAX == 2
    assert [data for kind, data in events if kind == "follow_up"] == [
        {"attempt": 1, "of": 2},
        {"attempt": 2, "of": 2},
    ]
    assert [data for kind, data in events if kind == "unfinished"] == [{"follow_ups": 2}]
    assert [kind for kind, _ in events].count("usage") == 3  # the first turn and two follow ups
    assert [f.url for f in stored] == [NICHE]  # what it found is kept
    assert not session.finished
    assert interactive.outcome(session) == "plan_unfinished"
    assert "plan_unfinished" in sweep.NOT_RETRIED
    entry = {"source_id": "web", "error": "plan_unfinished", "found": 1}
    sweep.finish_source(entry)
    assert (entry["outcome"], entry["reason"]) == ("partial", "plan_unfinished")


def test_the_follow_up_names_what_is_left() -> None:
    session = web_session()
    search(session, *COVERING)
    said_ = session.go_on()
    assert said_ is not None
    assert said_.startswith("You ended your turn, but the search plan")
    assert "site: of one or more AI and tech job boards" in said_  # AI_BOARDS is left
    search(session, AI_BOARDS)
    assert session.go_on() is None


@pytest.mark.parametrize("code", ["lease_ended", "permission_revoked", "agent_cancelled"])
def test_a_stopped_session_is_never_continued_and_may_still_end(code: str) -> None:
    session = web_session()
    search(session, "ingénieur IA générative CDI La Réunion")
    assert session.go_on() is not None
    session.stopped = code
    assert session.go_on() is None
    assert not session.plan_unfinished()
    said_, error = session.report_none("no_match")
    assert (error, session.finished) == (False, True)
    assert said_.startswith(f"Noted. Stopped: {code}.")


def test_without_web_search_the_plan_asks_for_nothing() -> None:
    session = web_session()
    session.web_search_on = False
    assert (session.go_on(), session.plan_unfinished()) == (None, False)


def test_a_run_without_its_session_and_other_agents_are_never_continued() -> None:
    assert finder.WEB_FINDER.follow_up(uuid.uuid4()) is None  # no session: the run ends
    assert finder.INTERACTIVE_FINDER.follow_up(uuid.uuid4()) is None
    # Every other agent: the base definition's answer.
    assert definitions.AgentDefinition.follow_up(finder.WEB_FINDER, uuid.uuid4()) is None


class Client:
    """A stand-in for the SDK's client: each response ends after its result, as the SDK's
    `receive_response` does; the prompts it is asked, in order."""

    prompts: list[str]

    def __init__(self, options: Any = None) -> None:
        self.options = options

    async def connect(self) -> None:
        pass

    async def query(self, prompt: str) -> None:
        self.prompts.append(prompt)

    async def receive_response(self) -> Any:
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
            result="ended",
        )

    async def interrupt(self) -> None:
        self.prompts.append("<interrupt>")

    async def disconnect(self) -> None:
        self.prompts.append("<disconnect>")


def test_the_real_backend_continues_the_same_session_with_each_follow_up(monkeypatch) -> None:
    class Fresh(Client):
        prompts: list[str] = []  # noqa: RUF012, one list per test

    monkeypatch.setattr(backend, "ClaudeSDKClient", Fresh)
    told = ["Still to search: the boards.", None]

    async def follow_up() -> str | None:
        return told.pop(0)

    request = backend.BackendRequest(
        prompt="first", options=None, tools=None, gate=None, follow_up=follow_up
    )  # type: ignore[arg-type]

    async def go() -> list[Any]:
        return [message async for message in backend.SdkBackend().stream(request)]

    assert len(asyncio.run(go())) == 2  # a result per turn
    # One client, one session: the follow up is a second query, never a new process.
    assert Fresh.prompts == ["first", "Still to search: the boards.", "<disconnect>"]
    assert told == []
