"""Browser B12b: the sign in hand off as an agent's tool (app/browser/signin_tool.py), and its
registration in the one agent that drives a browser lease today, apply's form agent. The lease is
a fake LeaseTools: browserd's answers are given, the poll of B12a is replaced where a test is not
about it (B12a's own tests cover it), and every call is recorded."""

from __future__ import annotations

import asyncio
import functools
import uuid
from collections.abc import Callable, Coroutine, Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from app.agents import tools as app_tools
from app.apply import form_agent, form_tools  # noqa: F401  (registers the tools and the hold)
from app.browser import handoff, signin_tool
from app.browser.client import TAKEOVER_WAIT_S, BrowserError
from app.browser.handoff import Outcome

SITE = "Example Jobs"
REASON = "The form asks to sign in before it shows."


def sync(test: Callable[..., Coroutine[Any, Any, None]]) -> Callable[..., None]:
    @functools.wraps(test)
    def run_it(*args: Any, **kwargs: Any) -> None:
        asyncio.run(test(*args, **kwargs))

    return run_it


@dataclass
class FakeLeases:
    session_max_s: int = 1800


@dataclass
class FakeTools:
    """browserd's answer to the sign in tool, and nothing else reachable."""

    answer: dict[str, Any] = field(
        default_factory=lambda: {"ok": False, "error": "nobody_watching"}
    )
    raises: BrowserError | None = None
    asked: list[tuple[str, str]] = field(default_factory=list)
    leases: FakeLeases = field(default_factory=FakeLeases)

    async def ask_signin(self, site: str, reason: str) -> dict[str, Any]:
        self.asked.append((site, reason))
        if self.raises is not None:
            raise self.raises
        return self.answer

    @property
    def client(self) -> Any:
        raise AssertionError("nobody_watching answers before any poll of browserd")


@dataclass
class Found:
    pending: str = ""
    root: Any = None
    fields: list[Any] = field(default_factory=list)
    skipped: list[Any] = field(default_factory=list)


class WallFirst:
    """The generic adapter's reads: a sign in wall, then (once signed in) a page with no form."""

    def __init__(self, walls: int = 1) -> None:
        self.walls, self.reads, self.step = walls, 0, 0

    async def read(self, tools: Any) -> tuple[Found, str]:
        self.reads += 1
        return Found(pending="signin" if self.reads <= self.walls else ""), ""


def _session(tools: Any, walls: int = 1) -> form_agent.FormSession:
    return form_agent.FormSession(WallFirst(walls), tools, None, str(uuid.uuid4()))  # type: ignore[arg-type]


@pytest.fixture
def run() -> Iterator[Any]:
    """Registers a session under a run id, as RuntimeFormAgent does, and answers the tool."""
    made: list[uuid.UUID] = []

    def start(session: form_agent.FormSession) -> Any:
        run_id = uuid.uuid4()
        made.append(run_id)
        with form_agent._LOCK:
            form_agent._SESSIONS[run_id] = session
        scope = app_tools.Scope(sessions=None, user_id=uuid.uuid4(), run_id=run_id)  # type: ignore[arg-type]
        return app_tools.FACTORIES[signin_tool.TOOL](scope)

    yield start
    with form_agent._LOCK:
        for run_id in made:
            form_agent._SESSIONS.pop(run_id, None)


async def _call(made: Any, **args: Any) -> tuple[str, bool]:
    result = await made.handler({"site": SITE, "reason": REASON, **args})
    return result["content"][0]["text"], bool(result.get("is_error"))


def test_the_form_agent_names_the_tool_and_its_run_outlives_the_wait() -> None:
    assert signin_tool.TOOL == form_agent.SIGNIN == "browser_request_signin"
    assert form_agent.SIGNIN in form_agent.APPLY_FORM.tools
    assert app_tools.FACTORIES[form_agent.SIGNIN] is signin_tool._signin
    assert form_agent.SIGNIN_WAIT_S == 1800
    assert TAKEOVER_WAIT_S == 900
    assert form_agent.WALL_TIME_S == 900
    assert form_agent.APPLY_FORM.wall_time_s == 900 + 1800 + TAKEOVER_WAIT_S
    assert form_agent.APPLY_FORM.max_turns == form_agent.MAX_TURNS == 30
    assert form_agent.TOKEN_BUDGET == 820_000
    assert app_tools.TOOL_NAME.fullmatch(signin_tool.TOOL)


@pytest.mark.parametrize("takeover_first", [False, True])
def test_signin_and_takeover_share_the_aggregate_deadline_without_using_active_time(
    takeover_first: bool,
) -> None:
    now = [1_000.0]
    session = _session(FakeTools())
    session.clock = lambda: now[0]
    session.deadline = now[0] + form_agent.APPLY_FORM.wall_time_s
    # The order of the two human pauses does not consume the remaining active allowance.
    if takeover_first:
        now[0] += TAKEOVER_WAIT_S
    assert session.signin_budget() >= form_agent.SIGNIN_WAIT_S
    now[0] += form_agent.SIGNIN_WAIT_S
    if not takeover_first:
        now[0] += TAKEOVER_WAIT_S
    assert session.deadline - now[0] == form_agent.WALL_TIME_S == 900


@pytest.mark.parametrize("cancel", [False, True])
@sync
async def test_runtime_uses_the_aggregate_deadline_and_keeps_cancellation_cleanup(
    monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    from app.agents import runner
    from app.agents.models import RunStatus

    now = [1_000.0]
    session = _session(FakeTools())
    session.clock = lambda: now[0]
    run_id = uuid.uuid4()
    released = []
    keys_before = set(form_agent._KEYS)
    context = SimpleNamespace(sessions=None, clock=lambda: 0)
    db = SimpleNamespace(get=lambda *_: SimpleNamespace(output={"turns": 2}))
    monkeypatch.setattr(form_agent.credentials, "get", lambda *_: object())
    monkeypatch.setattr(form_agent.runs, "create_run", lambda *_: SimpleNamespace(id=run_id))
    monkeypatch.setattr(form_agent.answer_agents, "_in_db", lambda _, operation: operation(db))
    monkeypatch.setattr(
        form_agent.answer_agents,
        "_ended",
        lambda *_: SimpleNamespace(status=RunStatus.done, input_tokens=0, output_tokens=0),
    )

    async def execute(given_context, given_id):
        assert given_context is context
        assert given_id == run_id
        assert form_agent.session_of(run_id) is session
        assert session.deadline == 1_000 + 900 + 1800 + TAKEOVER_WAIT_S
        now[0] += TAKEOVER_WAIT_S + form_agent.SIGNIN_WAIT_S
        assert session.deadline - now[0] == 900
        if cancel:
            raise asyncio.CancelledError

    async def release(given_context, given_id):
        released.append(given_id)

    monkeypatch.setattr(runner, "execute", execute)
    monkeypatch.setattr(form_agent.answer_agents, "_release", release)
    agent = form_agent.RuntimeFormAgent(lambda: context)
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await agent(session)
        assert released == [run_id]
    else:
        assert (await agent(session)).turns == 2
        assert released == []
    assert form_agent.session_of(run_id) is None
    assert keys_before == form_agent._KEYS


def test_the_tool_takes_a_site_and_a_reason_only() -> None:
    assert signin_tool.SCHEMA["required"] == ["site", "reason"]
    assert set(signin_tool.SCHEMA["properties"]) == {"site", "reason"}
    assert signin_tool.SCHEMA["additionalProperties"] is False


@sync
async def test_a_wall_read_then_nobody_watching_answers_at_once_and_the_fill_waits(
    run: Any,
) -> None:
    tools = FakeTools()
    session = _session(tools)
    made = run(session)

    view, error = await session.read_form()
    assert (view, error) == (form_agent.WALL, True)
    text, error = await _call(made)

    assert (text, error) == (handoff.TEXTS["nobody_watching"], True)
    assert tools.asked == [(SITE, REASON)]
    assert session.signin_over
    reads = session.adapter.reads
    assert await session.read_form() == (form_agent.SIGNIN_OVER, True)
    assert await session.fill_field("e1") == (form_agent.SIGNIN_OVER, True)
    report = await session.finish()
    assert session.adapter.reads == reads  # nothing reads the page once the hand off is over
    assert (report.stopped, report.needs_person) == ("needs_person", "signin")


@pytest.mark.parametrize(
    "code",
    [
        "signin_cancelled",
        "signin_timeout",
        "turn_ended",
        "ended",
        "lease_ended",
        "nobody_watching",
    ],
)
@sync
async def test_every_end_but_signed_in_stops_the_fill_as_needs_person_signin(
    run: Any, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    async def ends(*args: Any, **kwargs: Any) -> Outcome:
        return Outcome(code)

    monkeypatch.setattr(signin_tool, "request_signin", ends)
    session = _session(FakeTools())
    made = run(session)
    await session.read_form()

    text, error = await _call(made)

    assert (text, error) == (Outcome(code).text(), True)
    report = await session.finish()
    assert (report.stopped, report.needs_person) == ("needs_person", "signin")


@sync
async def test_signed_in_reads_the_form_again_and_the_fill_goes_on(
    run: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def signed_in(*args: Any, **kwargs: Any) -> Outcome:
        return Outcome("signed_in", title="Example Jobs: signed in as e2e-b12")

    monkeypatch.setattr(signin_tool, "request_signin", signed_in)
    session = _session(FakeTools())
    made = run(session)
    await session.read_form()

    text, error = await _call(made)

    assert not error
    assert text == 'The person signed in; the page is now "Example Jobs: signed in as e2e-b12".'
    assert (session.signin_over, session.pending) == (False, "")
    await session.read_form()
    assert session.adapter.reads == 2
    report = await session.finish()
    assert (report.needs_person, report.stopped) == ("", "")


@sync
async def test_a_wall_seen_again_after_signed_in_may_ask_again(
    run: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def signed_in(*args: Any, **kwargs: Any) -> Outcome:
        calls.append("asked")
        return Outcome("signed_in")

    monkeypatch.setattr(signin_tool, "request_signin", signed_in)
    session = _session(FakeTools(), walls=2)
    made = run(session)
    await session.read_form()
    await _call(made)
    assert await session.read_form() == (form_agent.WALL, True)
    _, error = await _call(made)
    assert (error, calls) == (False, ["asked", "asked"])


@sync
async def test_a_page_that_asks_for_the_tool_without_a_wall_gets_nothing_asked(run: Any) -> None:
    tools = FakeTools()
    session = _session(tools, walls=0)
    made = run(session)

    assert await _call(made) == (signin_tool.NO_WALL, True)
    await session.read_form()
    assert await _call(made) == (signin_tool.NO_WALL, True)
    assert tools.asked == []
    assert not session.signin_over


@pytest.mark.parametrize(
    ("site", "reason"),
    [
        ("", REASON),
        (SITE, ""),
        ("   ", REASON),
        ("\u200b\u0000", REASON),
        (7, REASON),
        (None, None),
    ],
)
@sync
async def test_an_empty_or_odd_site_or_reason_asks_nothing(
    run: Any, site: Any, reason: Any
) -> None:
    tools = FakeTools()
    session = _session(tools)
    made = run(session)
    await session.read_form()

    assert await _call(made, site=site, reason=reason) == (signin_tool.BAD_ARGS, True)
    assert tools.asked == []


@sync
async def test_the_site_and_reason_reach_browserd_as_short_plain_text(run: Any) -> None:
    tools = FakeTools()
    session = _session(tools)
    made = run(session)
    await session.read_form()

    await _call(made, site="Example\nJobs\u202e" + "x" * 200, reason="Sign\tin " * 100)

    site, reason = tools.asked[0]
    assert site.startswith("Example Jobs")
    assert len(site) == signin_tool.SITE_MAX
    assert "\n" not in site
    assert "\u202e" not in site
    assert len(reason) == signin_tool.REASON_MAX
    assert "\t" not in reason


@sync
async def test_browserd_unreachable_at_the_call_ends_the_hand_off(run: Any) -> None:
    tools = FakeTools(raises=BrowserError("unavailable"))
    session = _session(tools)
    made = run(session)
    await session.read_form()

    assert await _call(made) == (handoff.TEXTS["ended"], True)
    assert session.signin_over


@sync
async def test_browserd_lease_ended_ends_the_fill_without_reading(run: Any) -> None:
    tools = FakeTools(answer={"ok": False, "error": "lease_ended", "message": "The lease ended."})
    session = _session(tools)
    made = run(session)
    await session.read_form()

    assert await _call(made) == (handoff.TEXTS["ended"], True)
    assert session.signin_over
    reads = session.adapter.reads
    report = await session.finish()
    assert session.adapter.reads == reads
    assert (report.stopped, report.needs_person) == ("needs_person", "signin")


@sync
async def test_a_module_that_says_nothing_of_a_wall_asks_nothing() -> None:
    tools = FakeTools()
    assert await signin_tool.ask(signin_tool.Hold(tools), {"site": SITE, "reason": REASON}) == (
        signin_tool.NO_WALL
    )
    assert tools.asked == []


@sync
async def test_the_wait_is_bounded_by_what_the_run_has_left(
    run: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    budgets: list[float | None] = []

    async def capture(*args: Any, budget_s: float | None = None, **kwargs: Any) -> Outcome:
        budgets.append(budget_s)
        return Outcome("turn_ended")

    monkeypatch.setattr(signin_tool, "request_signin", capture)
    now = [1_000.0]
    session = _session(FakeTools())
    session.clock = lambda: now[0]
    session.deadline = now[0] + 500
    made = run(session)
    await session.read_form()

    await _call(made)

    margin = form_agent.SIGNIN_MARGIN_S
    assert budgets == [500 - margin]
    now[0] = session.deadline - margin - 1
    assert session.signin_budget() == 1.0
    now[0] = session.deadline - margin  # at the margin: no wait
    assert session.signin_budget() == 0.0
    now[0] = session.deadline + 1  # past the deadline: never a negative wait
    assert session.signin_budget() == 0.0
    session.deadline = None
    assert session.signin_budget() is None


@pytest.mark.parametrize(
    "code",
    ["bad_address", "host_not_allowed", "refused", "bad_args", "unknown_code", None],
)
@sync
async def test_a_refusal_of_browserd_stops_the_fill_and_asks_no_more(
    run: Any, code: str | None
) -> None:
    tools = FakeTools(answer={"ok": False, "error": code, "message": "Refused by browserd."})
    session = _session(tools)
    made = run(session)
    await session.read_form()

    text, error = await _call(made)

    assert error
    assert text.startswith("The sign in could not be asked: ")
    assert session.signin_over
    assert await _call(made) == (signin_tool.NO_WALL, True)
    assert len(tools.asked) == 1
    reads = session.adapter.reads
    assert await session.read_form() == (form_agent.SIGNIN_OVER, True)
    report = await session.finish()
    assert session.adapter.reads == reads
    assert (report.stopped, report.needs_person) == ("needs_person", "signin")


@sync
async def test_a_run_without_a_session_has_no_lease_and_each_run_reaches_its_own(
    run: Any,
) -> None:
    scope = app_tools.Scope(sessions=None, user_id=uuid.uuid4(), run_id=uuid.uuid4())  # type: ignore[arg-type]
    lone = app_tools.FACTORIES[signin_tool.TOOL](scope)
    assert await _call(lone) == (signin_tool.NO_LEASE, True)

    tools_a, tools_b = FakeTools(), FakeTools()
    session_a, session_b = _session(tools_a), _session(tools_b)
    made_a, made_b = run(session_a), run(session_b)
    await session_a.read_form()
    await session_b.read_form()
    await _call(made_a, site="Site A")
    assert ([s for s, _ in tools_a.asked], tools_b.asked) == (["Site A"], [])
    assert (session_a.signin_over, session_b.signin_over) == (True, False)
    await _call(made_b, site="Site B")
    assert [s for s, _ in tools_b.asked] == ["Site B"]
