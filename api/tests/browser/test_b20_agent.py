"""B20 through the website Runner and a scripted model (owner, 2026-11-04: the search on the
Jetson works as Claude Code with Playwright CLI on the Mac): the screenshot reaches the model as
an image block, bounded; a script's result comes back as bounded text; the person is asked
(nobody watching: at once; watched: until the browser is given back, then the agent goes on); a
security check or a sign in wall no longer ends the website; no time cap ends a long search;
both agents run Sonnet for every credential kind; the line's refusals are told and counted.

The harness is this file's own (the J18 files are being rewritten beside it): browserd's answers
come from `Site`, the model is `ScriptedFinder`, the registered tools (finder_tools) are called
the way the SDK calls them. Invented hosts only.
"""

import asyncio
import base64
import io
import json
import random
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from app.agents import tools as app_tools
from app.agents.models import CredentialKind
from app.browser import finder, finder_tools, platform_policy, screenshot, sources
from app.jobs import sweep
from app.profile.schemas import SearchSection
from tests.browser.fakes import FakeLeases, ScriptedFinder

START = "https://jobs.example.test/"
HOME = '- searchbox "Search jobs" [ref=e1]\n- button "Search" [ref=e2]'
RESULTS = START + "search?q=robot"
POSTING = "https://ats.example-hire.test/example-robotics/jobs/42"  # an ATS, another host
LIST = f'- link "Robot Engineer" [ref=e9]\n  - /url: {POSTING}'
JOB = '- heading "Robot Engineer" [level=1]\n- paragraph: Build robots with customers.'
LINKEDIN = "https://www.linkedin.com/jobs/"
CHECK = "https://www.linkedin.com/checkpoint/challenge/AgF3e2e"
CHECK_PAGE = '- heading "Let\'s do a quick security check" [level=1]\n- button "Verify" [ref=e5]'
AUTHWALL = "https://www.linkedin.com/authwall?trk=e2e"
AUTH_PAGE = '- heading "Sign in to view more jobs" [level=1]\n- button "Sign in" [ref=e6]'


class Client:
    """browserd's GET /leases/current as the api reads it (`client.current()`): the states the
    lease goes through, one per poll, the last one kept."""

    def __init__(self, site: "Site", states: list[str | None]) -> None:
        self.site, self.states, self.polls = site, states, 0

    async def current(self) -> dict[str, Any]:
        state = self.states[min(self.polls, len(self.states) - 1)] if self.states else "active"
        self.polls += 1
        if state == "active" and self.site.after_person:
            self.site.current, self.site.after_person = self.site.after_person, ""
        if state is None:
            return {"lease": None, "last_end": None}
        lease = {"lease_id": self.site.lease_id, "state": state, "wait_host": "jobs.example.test"}
        if state == "waiting_person":
            lease["wait_reason"] = "ask_person"
        return {"lease": lease, "last_end": None}


class Mirror:
    """The api's copy of the lease (app.browser.leases.Leases): what the ask set on it."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def wait_person(self, user: str, lease_id: str, reason: str, host: str, left: int):
        self.calls.append(("wait_person", lease_id, reason, host, left))

    async def resume(self, user: str, lease_id: str):
        self.calls.append(("resume", lease_id))


class Site:
    """A job website as browserd answers a sweep lease's tools (actions.run): its pages by
    address, the tool calls it got, a screenshot, a script's result, the ask's answer and the
    lease states the api polls after it (`Client`). `refuse` maps a ref to the line's code
    browserd answers for any action on it."""

    def __init__(self, pages: dict[str, str] | None = None, **kw: Any) -> None:
        self.pages = {**(pages or {START: HOME}), RESULTS: LIST, POSTING: JOB}
        self.current = ""
        self.calls: list[tuple[str, dict]] = []
        self.shot: dict[str, Any] = kw.get("shot", {"ok": True, "type": "png", "data": "iVBOR"})
        self.evaluated: dict[str, Any] = kw.get("evaluated", {"ok": True, "result": "42"})
        self.asked: dict[str, Any] = kw.get("asked", {"ok": False, "error": "nobody_watching"})
        self.refuse: dict[str, str] = kw.get("refuse", {})
        self.after_person = kw.get("after_person", "")  # the page the person leaves
        self.lease_id, self.user_id = "lease-1", "e2e-person"
        self.client = Client(self, kw.get("states", []))
        self.leases = Mirror()
        self.clock: list[float] | None = None
        self.step_s = kw.get("step_s", 0.0)  # the time each call takes (a long search)

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        assert args.pop("_discovery") == "native_get"
        assert args.pop("_discovery_scope") == {"start": self.start}  # no saved values (B20)
        self.calls.append((name, dict(args)))
        if self.clock is not None:
            self.clock[0] += self.step_s
        if name == "browser_navigate":
            if args["url"] not in self.pages:
                return {"ok": False, "error": "navigation_failed"}
            self.current = args["url"]
            return {"ok": True, "url": self.current}
        if (args.get("ref") or args.get("key")) in self.refuse:
            code = self.refuse[args.get("ref") or args["key"]]
            return {"ok": False, "error": code, "message": "Refused: the line (invented)."}
        if name == "browser_take_screenshot":
            return self.shot
        if name == "browser_evaluate":
            return self.evaluated
        if name == "browser_ask_person":
            return self.asked
        if name == "browser_type" and args.get("submit"):
            self.current = RESULTS
        if name == "browser_navigate_back":
            self.current = RESULTS
        return {"ok": True}

    @property
    def start(self) -> str:
        return next(iter(self.pages))

    async def snapshot(self) -> dict[str, Any]:
        return {"ok": True, "url": self.current, "snapshot": self.pages[self.current]}

    async def wait_for(self, **_: Any) -> dict[str, Any]:
        return {"ok": True}


def selected(url: str = START, **search: Any) -> SearchSection:
    return SearchSection.model_validate(
        {
            "title_include": {"terms": ["robot"]},
            "interactive_sites": [
                {"platform": "custom", "url": url, "enabled": True, "mode": "llm_browser"}
            ],
            **search,
        }
    )


class NoDatabasePolicy(platform_policy.PlatformPolicy):
    """The real policy with consent granted and nothing stored (a LinkedIn browser search)."""

    def __init__(self) -> None:
        super().__init__(sessions=None)  # type: ignore[arg-type]
        self.observed: list[str] = []

    def _refusal(self, context, platform):
        return None

    def _reserve(self, context, platform):
        return None

    async def observe(self, context, url, snapshot):
        self.observed.append(url)
        return await super().observe(context, url, snapshot)


async def execute(search, site: Site, agent, *, deadline=100_000.0, **kw: Any):
    """The sweep's website Runner on `site` (test_selected_interactive's `execute`, copied)."""
    now = [0.0]
    site.clock = now

    async def sleep(seconds):
        now[0] += seconds

    async def progress():
        return None

    scope = sweep.freeze_scope(
        SimpleNamespace(sections={"search": search.model_dump(mode="json")}, revision=3),
        sweep.SweepTrigger.manual,
        datetime.fromisoformat("2026-11-04T00:00:00+00:00"),
    )
    stats = sweep.Stats(sources=sweep.initial_sources(scope))
    plan = SimpleNamespace(
        search=search, user_id="e2e-person", search_scope=scope, sweep_id=kw.get("sweep_id")
    )
    leases = kw.get("leases") or FakeLeases()
    runner = sources.Runner(
        leases,
        lambda *_: site,
        finder=agent,
        sleep=sleep,
        monotonic=lambda: now[0],
        rng=random.Random(7),  # noqa: S311  deterministic test pacing
        pacer=sources.MemoryPacer(lambda: now[0]),
        interactive_policy=kw.get("policy"),
    )
    await runner(plan, stats, progress, deadline)
    [entry] = stats.sources
    return entry, now[0], leases


def run(search, site: Site, script, **kw: Any):
    return asyncio.run(execute(search, site, ScriptedFinder(script), **kw))


async def tool(session: finder.FinderSession, name: str, **args: Any) -> dict[str, Any]:
    """The registered tool `name` (finder_tools) called as the SDK calls it, on that session:
    what the model reads."""
    run_id = uuid.uuid4()
    with finder._LOCK:
        finder._SESSIONS[run_id] = session
    try:
        scope = app_tools.Scope(sessions=None, user_id=uuid.uuid4(), run_id=run_id)  # type: ignore[arg-type]
        return await finder_tools._factory(name)(scope).handler(args)
    finally:
        with finder._LOCK:
            finder._SESSIONS.pop(run_id, None)


def text_of(result: dict[str, Any]) -> str:
    [block] = result["content"]
    assert block["type"] == "text", block
    return block["text"]


async def report(session: finder.FinderSession) -> None:
    """Open the ATS posting from the list and report it (what makes a website searched)."""
    _, error = await session.navigate(POSTING)
    assert not error
    _, error = await session.report_verified(
        [{"title": "Robot Engineer", "url": POSTING, "text": "Build robots."}]
    )
    assert not error


# --- the screenshot ------------------------------------------------------------------------------


def _png(width: int, height: int) -> str:
    pixels = random.Random(7).randbytes(width * height * 3)  # noqa: S311  a fixed page
    out = io.BytesIO()
    Image.frombytes("RGB", (width, height), pixels).save(out, "PNG")
    return base64.b64encode(out.getvalue()).decode("ascii")


@pytest.mark.parametrize(
    ("data", "image"),
    [
        (_png(160, 100), True),
        (_png(1440, 900), True),  # random pixels: over 1 MiB as base64, the bound of B23
        ("iVBOR" + "A" * 100, False),  # no image: text, never a cut image
    ],
)
def test_the_screenshot_reaches_the_model_as_an_image_block_bounded(data: str, image: bool):
    site = Site(shot={"ok": True, "type": "png", "url": START, "data": data})
    seen: list[dict] = []

    async def script(session):
        seen.append(await tool(session, "browser_take_screenshot"))
        await report(session)
        return finder.Ran()

    entry, _, _ = run(selected(), site, script)
    [result] = seen
    if image:
        [block] = result["content"]
        assert (block["type"], block["mimeType"]) == ("image", "image/jpeg")
        assert len(base64.b64decode(block["data"])) <= screenshot.SHOT_BYTES
        assert block["data"] == screenshot.for_model(data)
    else:
        assert result.get("is_error") is True
        assert text_of(result) == screenshot.UNREAD
    assert ("browser_take_screenshot", {"type": "png"}) in site.calls  # no model argument
    assert entry["error"] == "finding_persistence_pending"  # the agent went on


@pytest.mark.parametrize(
    "shot",
    [{"ok": False, "error": "page_error"}, {"ok": True, "data": 7}, {"ok": True}],
)
def test_a_screenshot_that_failed_is_a_sentence_and_the_agent_goes_on(shot):
    site = Site(shot=shot)
    seen: list[dict] = []

    async def script(session):
        seen.append(await tool(session, "browser_take_screenshot"))
        await report(session)
        return finder.Ran()

    entry, _, _ = run(selected(), site, script)
    assert seen[0]["is_error"] is True
    assert text_of(seen[0]) == "The screenshot could not be taken."
    assert entry["error"] == "finding_persistence_pending"


# --- a script on the page ----------------------------------------------------------------------


def test_a_script_s_result_returns_as_bounded_text_with_the_page():
    long = json.dumps("x" * 50_000)
    site = Site(evaluated={"ok": True, "result": long, "cut": True})
    seen: list[dict] = []

    async def script(session):
        seen.append(await tool(session, "browser_evaluate", function="() => document.title"))
        await report(session)
        return finder.Ran()

    entry, _, _ = run(selected(), site, script)
    view = json.loads(text_of(seen[0]))
    assert view["script_result"] == long[:20_000]
    assert view["untrusted_page_snapshot"] == HOME  # the page as it is after the script
    assert ("browser_evaluate", {"function": "() => document.title"}) in site.calls
    assert entry["error"] == "finding_persistence_pending"


def test_a_model_argument_beyond_the_schema_never_reaches_browserd():
    site = Site()
    seen: list[dict] = []

    async def script(session):
        seen.append(
            await tool(
                session,
                "browser_evaluate",
                function="() => 1",
                _discovery="other",
                _discovery_scope={"start": "https://elsewhere.example.test/"},
            )
        )
        return finder.Ran()

    run(selected(), site, script)
    assert ("browser_evaluate", {"function": "() => 1"}) in site.calls  # Site asserts the marker


@pytest.mark.parametrize(
    ("answer", "counted"),
    [
        ({"ok": False, "error": "line", "message": "Refused: the line."}, True),
        ({"ok": False, "error": "script_error", "message": "The script failed: x"}, False),
        ({"ok": False, "error": "timeout", "message": "The script ran past 5 seconds."}, False),
    ],
)
def test_a_script_refused_or_failed_is_told_and_only_the_line_counts(answer, counted):
    site = Site(evaluated=answer)
    seen: list[dict] = []

    async def script(session):
        seen.append(await tool(session, "browser_evaluate", function="() => 1"))
        await report(session)
        return finder.Ran()

    entry, _, _ = run(selected(), site, script)
    told = json.loads(text_of(seen[0]))
    assert (told["error"], seen[0]["is_error"]) == (answer["error"], True)
    assert entry["refused"] == ({"unsupported_interaction": 1} if counted else {})
    assert entry["found"] == 1


# --- asking the person ---------------------------------------------------------------------------


def test_nobody_watching_answers_at_once_and_the_agent_goes_on():
    site = Site(states=["active"])
    seen: list[dict] = []

    async def script(session):
        seen.append(await tool(session, "browser_ask_person", reason="Please solve the check."))
        await report(session)
        return finder.Ran()

    entry, elapsed, _ = run(selected(), site, script)
    assert text_of(seen[0]).startswith("Nobody is watching")
    assert seen[0].get("is_error") is not True
    assert site.client.polls == 0  # nothing waited
    assert site.leases.calls == []
    assert ("browser_ask_person", {"reason": "Please solve the check."}) in site.calls
    assert elapsed == 0.0
    assert entry["error"] == "finding_persistence_pending"


def test_watched_the_agent_waits_until_given_back_then_reads_the_page_the_person_left():
    site = Site(
        asked={"ok": False, "error": "waiting_person", "left_s": 900},
        states=["waiting_person", "waiting_person", "waiting_person", "active"],
        after_person=RESULTS,  # the person solved the check: the results show
    )
    seen: list = []

    async def script(session):
        seen.append(await tool(session, "browser_ask_person", reason="Please help me."))
        seen.append(await session.snapshot())
        await report(session)
        return finder.Ran()

    entry, elapsed, _ = run(selected(), site, script)
    assert text_of(seen[0]).startswith("The person had the browser")
    assert json.loads(seen[1][0])["untrusted_page_snapshot"] == LIST
    assert site.client.polls == 4
    assert site.leases.calls == [
        ("wait_person", "lease-1", "ask_person", "jobs.example.test", 900),
        ("resume", "lease-1"),
    ]
    assert elapsed == 3 * 2.0  # ASK_POLL_S apart, no page pace
    assert entry["error"] == "finding_persistence_pending"


def test_an_ask_nobody_gives_back_ends_after_its_deadline_and_the_agent_goes_on():
    site = Site(
        asked={"ok": False, "error": "waiting_person", "left_s": 60}, states=["waiting_person"]
    )
    seen: list = []

    async def script(session):
        seen.append(await tool(session, "browser_ask_person", reason="Please help me."))
        await report(session)
        return finder.Ran()

    entry, elapsed, _ = run(selected(), site, script)
    assert text_of(seen[0]).startswith("The person did not answer")
    from app.browser.leases import MAX_GRACE_S

    assert 60 + MAX_GRACE_S <= elapsed <= 60 + MAX_GRACE_S + 2.0
    assert entry["error"] == "finding_persistence_pending"


@pytest.mark.parametrize("gone", [None, "other"])
def test_a_lease_that_ended_during_the_ask_stops_the_session_not_the_tool(gone):
    """A lease gone while the person was asked (ended, or another lease now): the tool answers
    the stop as every other tool does, and the session is stopped `lease_ended`."""
    site = Site(asked={"ok": False, "error": "waiting_person", "left_s": 900}, states=[gone])
    if gone == "other":
        site.client.site = SimpleNamespace(lease_id="lease-9", after_person="")
    seen: list = []

    async def script(session):
        seen.append(await tool(session, "browser_ask_person", reason="Please help me."))
        seen.append(session.stopped)
        return finder.Ran()

    entry, _, _ = run(selected(), site, script)
    assert seen[1] == "lease_ended"
    assert text_of(seen[0]).startswith("Stopped: lease_ended")
    assert entry["error"] == "lease_ended"


class Withdrawn:
    """The website's permission withdrawn after the first page (the policy's check refuses)."""

    async def check(self, context):
        return "permission_unavailable"


@pytest.mark.parametrize("name", ["browser_take_screenshot", "browser_ask_person"])
def test_a_permission_withdrawn_mid_search_stops_the_screenshot_and_the_ask(name):
    site = Site(asked={"ok": False, "error": "waiting_person", "left_s": 900})
    seen: list = []

    async def script(session):
        session.pages.tools.policy = Withdrawn()
        seen.append(await tool(session, name, reason="Please help me."))
        seen.append(session.stopped)
        return finder.Ran()

    entry, _, _ = run(selected(), site, script)
    assert seen[1] == "permission_unavailable"
    assert text_of(seen[0]).startswith("Stopped: permission_unavailable")
    assert all(called != name for called, _ in site.calls)  # checked before browserd
    assert entry["error"] == "permission_unavailable"


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_an_ask_with_no_sentence_is_refused_before_browserd(reason):
    site = Site()
    seen: list = []

    async def script(session):
        seen.append(await session.ask_person(reason))
        return finder.Ran()

    run(selected(), site, script)
    assert seen == [("Refused: give one sentence for the person.", True)]
    assert all(name != "browser_ask_person" for name, _ in site.calls)


@pytest.mark.parametrize(
    ("wall", "page"), [(CHECK, CHECK_PAGE), (AUTHWALL, AUTH_PAGE)], ids=["checkpoint", "signin"]
)
def test_a_security_check_or_a_sign_in_wall_makes_the_agent_ask_not_end_the_website(wall, page):
    """Before B20 the policy's wall check ended the website `linkedin_checkpoint` or
    `linkedin_signin` on this page; now the agent asks, the person solves it, the search goes
    on. The fixture is a wall as the platform policy tells one."""
    assert platform_policy.wall("linkedin", wall, page) is not None
    site = Site(
        {LINKEDIN: HOME, wall: page},
        asked={"ok": False, "error": "waiting_person", "left_s": 900},
        states=["waiting_person", "active"],
        after_person=RESULTS,
    )
    seen: list = []

    async def script(session):
        seen.append(await session.navigate(wall))
        seen.append(await session.snapshot())
        seen.append(await session.ask_person("Please solve the security check on LinkedIn."))
        seen.append(await session.snapshot())
        await report(session)
        return finder.Ran()

    policy = NoDatabasePolicy()
    entry, _, _ = run(
        selected(LINKEDIN, linkedin_search="browser"),
        site,
        script,
        policy=policy,
        sweep_id=str(uuid.uuid4()),
    )
    assert [error for _, error in seen] == [False] * 4, seen
    assert json.loads(seen[3][0])["untrusted_page_snapshot"] == LIST
    assert entry["error"] == "finding_persistence_pending", entry
    assert entry["found"] == 1


# --- no time cap ---------------------------------------------------------------------------------


def test_no_time_cap_ends_a_long_website_search():
    """Each browser call takes 300 s and the sweep's own deadline is 900 s (its API sources'):
    the website runs on for over an hour and is searched; nothing ends it `time_budget`."""
    site = Site(step_s=300.0)

    async def script(session):
        for _ in range(10):
            assert not (await session.interact("browser_scroll", {"dy": 600}))[1]
        assert not (await session.interact("browser_navigate_back", {}))[1]
        await report(session)
        return finder.Ran()

    entry, elapsed, leases = run(selected(), site, script, deadline=900.0)
    assert elapsed > 3600
    assert entry["error"] == "finding_persistence_pending", entry
    assert leases.released == ["lease-1"]


def test_a_lease_of_900_seconds_in_the_api_copy_does_not_cut_the_search():
    site = Site(step_s=200.0)

    async def script(session):
        for _ in range(8):
            assert not (await session.interact("browser_hover", {"ref": "e1"}))[1]
        await report(session)
        return finder.Ran()

    entry, elapsed, _ = run(selected(), site, script, leases=FakeLeases(max_s=900))
    assert elapsed > 1800
    assert entry["error"] == "finding_persistence_pending", entry


# --- the model -----------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", list(CredentialKind))
def test_the_search_agent_runs_sonnet_for_every_credential_kind(kind):
    definition = finder.INTERACTIVE_FINDER
    assert definition.model == "claude-sonnet-5-5"
    assert definition.model_for(kind) == "claude-sonnet-5-5"
    assert definition.model_for(None) == "claude-sonnet-5-5"


# --- the line, as the agent is told it --------------------------------------------------------


@pytest.mark.parametrize("code", ["line", "submit", "danger", "credential", "card_field"])
@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("browser_click", {"ref": "e2"}),
        ("browser_click", {"ref": "e2", "doubleClick": True}),
        ("browser_type", {"ref": "e2", "text": "robot", "submit": True}),
        ("browser_press_key", {"key": "Enter"}),
        ("browser_check", {"ref": "e2"}),
    ],
)
def test_a_refusal_of_the_line_is_told_counted_and_the_agent_goes_on(code, name, args):
    site = Site(refuse={"e2": code, "Enter": code})
    seen: list = []

    async def script(session):
        seen.append(await session.interact(name, args))
        await report(session)
        return finder.Ran()

    entry, _, _ = run(selected(), site, script)
    told, error = seen[0]
    assert error is True
    assert json.loads(told)["error"] == code
    assert entry["refused"] == {"unsupported_interaction": 1}
    assert entry["found"] == 1


def test_every_playwright_cli_tool_is_registered_with_a_schema_and_a_description():
    from app.agents import tools as registry

    for name in finder.INTERACTIVE_TOOLS:
        assert name in registry.FACTORIES, name
        assert name in finder_tools.SCHEMAS, name
        assert name in finder_tools.DESCRIPTIONS, name
    assert "browser_file_upload" not in finder.INTERACTIVE_TOOLS  # no upload for the search
    assert set(finder.ACTIONS) <= set(finder.INTERACTIVE_TOOLS)
