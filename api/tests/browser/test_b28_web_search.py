"""B28 (owner, 2026-11-17, #792): the website search agent reads public pages with web_fetch and
searches the web with Claude Code's WebSearch, the website search's alone, at most 20 a run. A
fetched page counts as opened for report_postings; a fetched security check or sign in page is
blocked, counted, and its host never fetched more than twice; `web_search` is a route of the
search once the run has it. The accept and refuse table of `names_site`.

The harness is test_b20_agent's (browserd's answers from `Site`, the registered tools called the
way the SDK calls them). The search engine is stubbed (the fake backend's WebSearch), never a
real one; invented hosts only.
"""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.agents import definitions, fake_backend, runner, sdk_options
from app.agents import tools as app_tools
from app.browser import finder, finder_tools
from tests.browser.test_b20_agent import HOME, START, Site, run, selected, text_of, tool

POSTING = "https://jobs.example.test/jobs/7001"
CAREERS = "https://careers.example-robotics.test/jobs/7001"
SNAPSHOT = (
    '- heading "Robot Arm Engineer" [level=1]\n'
    "- paragraph: Invented Robotics builds invented robot arms.\n"
    '- link "Apply":\n  - /url: https://jobs.example.test/jobs/7001/apply'
)
CHECK = "https://walled.example-board.test/jobs?q=robot"
CHECK_2 = "https://walled.example-board.test/jobs/1"
CHECK_3 = "https://walled.example-board.test/jobs/2"


class FetchSite(Site):
    """`Site` with browserd's web_fetch: `fetched` maps an address to its answer."""

    def __init__(self, pages: dict[str, str], fetched: dict[str, dict[str, Any]]) -> None:
        super().__init__(pages)
        self.fetched = fetched

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name != "web_fetch":
            return await super().call(name, args)
        assert args.pop("_discovery") == "native_get"
        args.pop("_discovery_scope")
        self.calls.append((name, dict(args)))
        return self.fetched[args["url"]]


def page(url: str, snapshot: str = SNAPSHOT, status: int = 200) -> dict[str, Any]:
    title = "Robot Arm Engineer"
    return {"ok": True, "url": url, "status": status, "title": title, "snapshot": snapshot}


def blocked(url: str, reason: str = "security_check") -> dict[str, Any]:
    return {"ok": False, "error": "blocked", "reason": reason, "url": url, "status": 403}


def site(**fetched: dict[str, Any]) -> FetchSite:
    answers = {POSTING: page(POSTING), CAREERS: page(CAREERS)}
    answers |= {CHECK: blocked(CHECK), CHECK_2: blocked(CHECK_2, "signin_wall")}
    answers |= {CHECK_3: blocked(CHECK_3)}
    return FetchSite({START: HOME}, answers | fetched)


def answer_of(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(text_of(result))


def test_a_page_read_by_web_fetch_is_kept_shown_and_reportable():
    """Acceptance 1, 2 and 6: the fetched posting's start is answered as untrusted text, the
    page is kept (source "fetch" reads it), and report_postings keeps it as an opened page;
    the employer's careers page of the same job may be reported with its own address."""
    browser = site()
    seen: dict[str, Any] = {}

    async def script(session):
        stored: list[finder.VerifiedBatch] = []

        async def keep(batch):
            stored.append(batch)

        session.on_verified = keep
        seen["fetch"] = answer_of(await tool(session, "web_fetch", url=POSTING))
        seen["find"] = text_of(
            await tool(session, "browser_find", pattern="robot arms", source="fetch")
        )
        seen["window"] = answer_of(await tool(session, "browser_snapshot", source="fetch"))
        await tool(session, "web_fetch", url=CAREERS)
        postings = [
            {"title": "Robot Arm Engineer", "url": POSTING, "text": "Build robot arms."},
            {"title": "Robot Arm Engineer", "url": CAREERS, "text": "Build robot arms."},
        ]
        seen["report"] = answer_of(await tool(session, "report_postings", postings=postings))
        seen["stored"] = [f.url for batch in stored for f in batch.findings]
        seen["tab"] = session.kept[0].url
        return finder.Ran()

    entry, _, _ = run(selected(), browser, script)
    fetched = seen["fetch"]
    assert fetched["url"] == POSTING
    assert fetched["untrusted_page_title"] == "Robot Arm Engineer"
    assert "Invented Robotics builds invented robot arms." in fetched["untrusted_page_text"]
    assert "invented robot arms" in seen["find"]
    assert seen["window"]["untrusted_page_text"].startswith('- heading "Robot Arm Engineer"')
    assert seen["report"]["kept"] == 2
    assert seen["stored"] == [POSTING, CAREERS]
    assert seen["tab"] == START  # the tab never moved
    assert ("browser_navigate", {"url": POSTING}) not in browser.calls
    assert entry["fetch_blocked"] == {}


def test_a_long_fetched_page_answers_its_start_only():
    long = "\n".join(f"- paragraph: line {n} of an invented posting" for n in range(2000))
    browser = site(**{POSTING: page(POSTING, long)})
    seen: dict[str, Any] = {}

    async def script(session):
        seen["fetch"] = answer_of(await tool(session, "web_fetch", url=POSTING))
        return finder.Ran()

    run(selected(), browser, script)
    assert len(seen["fetch"]["untrusted_page_text"]) <= finder.FETCH_SHOWN
    assert 'source="fetch"' in seen["fetch"]["fetched"]


def test_a_blocked_fetch_is_counted_and_its_host_fetched_twice_at_most():
    """Acceptance 4: a security check or a sign in page answers blocked with its reason, is
    counted in the website's stats by reason, and a third fetch of that host is refused by the
    app without a call (never a retry loop)."""
    browser = site()
    seen: dict[str, Any] = {}

    async def script(session):
        seen["first"] = await tool(session, "web_fetch", url=CHECK)
        seen["second"] = await tool(session, "web_fetch", url=CHECK_2)
        seen["third"] = await tool(session, "web_fetch", url=CHECK_3)
        seen["other"] = await tool(session, "web_fetch", url=POSTING)
        return finder.Ran()

    entry, _, _ = run(selected(), browser, script)
    first = answer_of(seen["first"])
    assert seen["first"]["is_error"] is True
    assert first["blocked"] == "security_check"
    assert "never solved" in first["message"]
    assert answer_of(seen["second"])["blocked"] == "signin_wall"
    assert "Do not fetch it again" in text_of(seen["third"])
    assert [args["url"] for name, args in browser.calls if name == "web_fetch"] == [
        CHECK,
        CHECK_2,
        POSTING,
    ]
    assert entry["fetch_blocked"] == {"security_check": 1, "signin_wall": 1}


def test_a_blocked_page_is_never_reportable():
    browser = site()
    seen: dict[str, Any] = {}

    async def script(session):
        await tool(session, "web_fetch", url=CHECK_2)
        posting = {"title": "Robot Arm Engineer", "url": CHECK_2, "text": "A card."}
        seen["report"] = answer_of(await tool(session, "report_postings", postings=[posting]))
        return finder.Ran()

    run(selected(), browser, script)
    assert seen["report"]["kept"] == 0


@pytest.mark.parametrize("bad", ["", "mailto:x@example.org", "javascript:alert(1)"])
def test_an_address_that_is_none_is_refused_by_the_app(bad: str):
    browser = site()
    seen: dict[str, Any] = {}

    async def script(session):
        seen["answer"] = await tool(session, "web_fetch", url=bad)
        return finder.Ran()

    run(selected(), browser, script)
    assert seen["answer"]["is_error"] is True
    assert not [call for call in browser.calls if call[0] == "web_fetch"]


def test_browserd_refusing_a_fetch_answers_why_and_reads_nothing():
    refused = {"ok": False, "error": "private_address", "message": "Refused."}
    browser = site(**{POSTING: refused})
    seen: dict[str, Any] = {}

    async def script(session):
        seen["answer"] = await tool(session, "web_fetch", url=POSTING)
        seen["read"] = finder.canonical(POSTING) in session.pages_read
        return finder.Ran()

    run(selected(), browser, script)
    assert answer_of(seen["answer"])["error"] == "private_address"
    assert seen["read"] is False


@pytest.mark.parametrize(
    ("query", "named"),
    [
        ("site:jobs.example.test robot engineer", True),
        ("robot engineer jobs.example.test", True),
        ("site:www.jobs.example.test robot", True),
        ("robot engineer site:eu.jobs.example.test", True),
        ("https://jobs.example.test/jobs robot", True),
        ("robot engineer SITE:JOBS.EXAMPLE.TEST", True),
        ("robot engineer example", True),  # the host's name before its suffix
        ("robot engineer", False),
        ("site:other.test robot", False),
        ("site:example.test robot", False),  # the parent host is another website
        ("site:notjobs.example.test.evil.test robot", False),
        ("robot examples", False),
        ("", False),
    ],
)
def test_a_web_search_names_the_website(query: str, named: bool):
    assert finder.names_site(query, {START}) is named


@pytest.mark.parametrize(
    ("host", "name"),
    [
        ("www.example.co.uk", "example"),
        ("jobs.example.com", "example"),
        ("example.com", "example"),
        ("localhost", ""),
        ("10.0.0.1", ""),
    ],
)
def test_the_site_name_of_a_host(host: str, name: str):
    assert finder.site_name(host) == name


def test_a_common_word_is_no_site_name():
    """jobs.ch's name is `jobs`: a query of robot jobs does not name it; its host does."""
    assert not finder.names_site("robot jobs", {"https://www.jobs.ch/"})
    assert finder.names_site("site:jobs.ch robot", {"https://www.jobs.ch/"})


def test_web_search_is_a_route_once_the_run_has_it():
    """B27's routes rule with B28: report_no_postings needs a web search of the website when the
    run has WebSearch, unless a block or a refusal spared the routes; a search of another
    website, or none, does not count."""
    seen: dict[str, Any] = {}

    async def script(session):
        session.routes.update(dict.fromkeys(("search_box", "search_address", "listing")))
        seen["off"] = session.routes_left("no_list")
        session.web_search_on = True
        seen["on"] = session.routes_left("no_list")
        seen["refusal"] = text_of(await tool(session, "report_no_postings", reason="no_list"))
        assert finder.web_search_hook(seen["run"], {"query": "robot"}) is None
        seen["other"] = session.routes_left("no_list")
        session.asked = 1
        seen["blocked"] = session.routes_left("security_check")
        session.asked = 0
        assert (
            finder.web_search_hook(seen["run"], {"query": "site:jobs.example.test robot"}) is None
        )
        seen["after"] = session.routes_left("no_list")
        seen["searches"] = session.web_searches
        return finder.Ran()

    async def bound(session):
        import uuid

        seen["run"] = run_id = uuid.uuid4()
        with finder._LOCK:
            finder._SESSIONS[run_id] = session
        try:
            return await script(session)
        finally:
            with finder._LOCK:
                finder._SESSIONS.pop(run_id, None)

    entry, _, _ = run(selected(), Site({START: HOME}), bound)
    assert seen["off"] == []
    assert seen["on"] == ["web_search"]
    assert "a web search of this website's postings" in seen["refusal"]
    assert seen["other"] == ["web_search"]
    assert seen["blocked"] == []
    assert seen["after"] == []
    assert seen["searches"] == 2
    assert entry["web_searches"] == 2
    assert "web_search" in entry["routes"]


def test_a_web_search_without_a_session_is_refused():
    import uuid

    assert finder.web_search_hook(uuid.uuid4(), {"query": "x"}) == "This run has no site. Stop now."


def test_the_tools_of_the_website_search():
    """Acceptance 6: web_fetch is the search's own tool (not an action on the tab), with its
    schema; WebSearch is the website search's alone, through its definition's hook."""
    assert "web_fetch" in finder.INTERACTIVE_TOOLS
    assert "web_fetch" not in finder.ACTIONS
    assert finder_tools.SCHEMAS["web_fetch"]["required"] == ["url"]
    assert finder_tools.SOURCE["enum"] == ["page", "script", "fetch"]
    assert finder.INTERACTIVE_FINDER.web_search is finder.web_search_hook
    searching = (finder.INTERACTIVE_NAME, finder.WEB_NAME)  # J21: the web search has it too
    others = [d for name, d in definitions.AGENTS.items() if name not in searching]
    assert others
    assert all(d.web_search is None for d in others)
    assert "WebSearch" not in finder.INTERACTIVE_TOOLS
    assert "untrusted" in finder.INTERACTIVE_PROMPT.split("6. When the website's search")[1]


def test_the_options_offer_web_search_only_when_asked(tmp_path):
    """No built-in tool by default; with web search, WebSearch alone is offered and allowed,
    WebFetch and the rest stay denied, and Claude Code's own cap is the gate's."""
    assert sdk_options.fixed()["tools"] == []
    assert "WebSearch" in sdk_options.fixed()["disallowed_tools"]
    on = sdk_options.fixed(True)
    assert on["tools"] == ["WebSearch"]
    assert "WebSearch" not in on["disallowed_tools"]
    assert "WebFetch" in on["disallowed_tools"]
    tools = app_tools.build(None, finder.uuid.uuid4(), ("get_job",))  # type: ignore[arg-type]

    async def gate(*_: Any) -> dict[str, Any]:
        return {}

    options = runner.build_options(
        definitions.ECHO, tools, {"ANTHROPIC_API_KEY": "sk-test"}, tmp_path, gate, web_search=True
    )
    assert options.tools == ["WebSearch"]
    assert options.allowed_tools == ["mcp__app__get_job", "WebSearch"]
    assert options.env[sdk_options.WEB_SEARCHES_ENV] == str(sdk_options.WEB_SEARCHES_MAX)
    plain = runner.build_options(definitions.ECHO, tools, {}, tmp_path, gate)
    assert plain.tools == []
    assert "WebSearch" not in plain.allowed_tools
    assert sdk_options.WEB_SEARCHES_ENV not in plain.env


def hook_input(name: str, query: str = "site:jobs.example.test robot") -> dict[str, Any]:
    return {"tool_name": name, "tool_input": {"query": query}}


def test_the_gate_allows_at_most_20_web_searches_a_run():
    asked: list[dict[str, Any]] = []
    denied: list[str] = []

    async def on_denied(name: str) -> None:
        denied.append(name)

    def hook(found: dict[str, Any]) -> str | None:
        asked.append(found)
        return None

    async def go() -> list[dict[str, Any]]:
        gate = runner.make_gate(frozenset(), on_denied, None, hook)
        return [await gate(hook_input("WebSearch"), None, {}) for _ in range(21)]

    verdicts = asyncio.run(go())
    assert verdicts[:20] == [{}] * 20
    last = verdicts[20]["hookSpecificOutput"]
    assert last["permissionDecision"] == "deny"
    assert last["permissionDecisionReason"] == runner.WEB_SEARCHES_SPENT
    assert len(asked) == 20
    assert denied == []


def test_the_gate_denies_web_search_without_the_hook_and_passes_its_refusal():
    denied: list[str] = []

    async def on_denied(name: str) -> None:
        denied.append(name)

    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        closed = runner.make_gate(frozenset(), on_denied)
        refusing = runner.make_gate(frozenset(), on_denied, None, lambda _: "Stopped: x.")
        return (
            await closed(hook_input("WebSearch"), None, {}),
            await refusing(hook_input("WebSearch"), None, {}),
        )

    closed, refusing = asyncio.run(go())
    assert closed["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert denied == ["WebSearch"]
    assert refusing["hookSpecificOutput"]["permissionDecisionReason"] == "Stopped: x."


def stub_backend(results: list[dict[str, str]]) -> fake_backend.FakeBackend:
    backend = fake_backend.FakeBackend.__new__(fake_backend.FakeBackend)
    backend._gate_all = False
    backend._web_results = results
    return backend


def test_the_fake_backend_stubs_web_search_and_never_offers_it_unasked():
    """Fake runs never reach a search engine: WebSearch answers the stream's invented results
    when the run offers it, and the CLI's no such tool otherwise."""
    results = [{"title": "Robot Arm Engineer", "url": POSTING}]

    async def gate(*_: Any) -> dict[str, Any]:
        return {}

    def request(offered: list[str]) -> Any:
        return SimpleNamespace(
            tools=SimpleNamespace(by_name={}),
            options=SimpleNamespace(tools=offered, cwd=None),
            gate=gate,
        )

    block = {"name": "WebSearch", "id": "toolu_1", "input": {"query": "site:jobs.example.test"}}
    on = asyncio.run(stub_backend(results)._call(request(["WebSearch"]), block))
    off = asyncio.run(stub_backend(results)._call(request([]), block))
    assert on["is_error"] is False
    assert POSTING in on["content"]
    assert off["is_error"] is True
    assert "No such tool" in off["content"]


SIGN_OUT = "https://jobs.example.test/logout"
ALL_ROUTES = ["search_box", "search_address", "listing", "web_search"]


@pytest.mark.parametrize("code", ["bad_url", "refused_scheme", "private_address", "line"])
def test_a_fetch_refused_for_its_address_is_the_agents_slip(code: str):
    """Round 1 of #805: browserd refusing the address the agent chose to fetch (a sign out
    address, a user part, a scheme, a private host) is never the app's refusal, so it spares no
    route: report_no_postings("app_refused") still needs all four."""
    browser = site(**{SIGN_OUT: {"ok": False, "error": code, "message": "Refused."}})
    seen: dict[str, Any] = {}

    async def script(session):
        session.web_search_on = True
        seen["before"] = session.routes_left("app_refused")
        seen["answer"] = answer_of(await tool(session, "web_fetch", url=SIGN_OUT))
        seen["after"] = session.routes_left("app_refused")
        seen["refused"] = dict(session.refused)
        return finder.Ran()

    run(selected(), browser, script)
    assert seen["before"] == ALL_ROUTES
    assert seen["after"] == ALL_ROUTES
    assert seen["answer"]["error"] == code
    assert seen["refused"].get("unsupported_interaction", 0) == 0
    assert seen["refused"]["not_an_address"] == 1


def test_browserd_refusing_the_lease_is_still_the_apps_refusal():
    browser = site(**{POSTING: {"ok": False, "error": "unsupported_discovery"}})
    seen: dict[str, Any] = {}

    async def script(session):
        session.web_search_on = True
        await tool(session, "web_fetch", url=POSTING)
        seen["after"] = session.routes_left("app_refused")
        return finder.Ran()

    run(selected(), browser, script)
    assert seen["after"] == ["search_address", "listing"]


def test_the_web_search_route_follows_the_runners_word_not_the_credential():
    """Round 1 of #805: `web_search_on` is the runner's word (`web_search_offered`), never set
    when the run is created: a session the runner said nothing about, or said False about
    (Claude Code did not list WebSearch), needs no web search before report_no_postings."""
    seen: dict[str, Any] = {}

    async def script(session):
        import uuid

        run_id = uuid.uuid4()
        with finder._LOCK:
            finder._SESSIONS[run_id] = session
        try:
            seen["unsaid"] = session.routes_left("no_list")
            finder.INTERACTIVE_FINDER.web_search_offered(run_id, True)
            seen["offered"] = session.routes_left("no_list")
            finder.INTERACTIVE_FINDER.web_search_offered(run_id, False)
            seen["unlisted"] = session.routes_left("no_list")
            seen["refusal"] = text_of(await tool(session, "report_no_postings", reason="no_list"))
        finally:
            with finder._LOCK:
                finder._SESSIONS.pop(run_id, None)
        finder.INTERACTIVE_FINDER.web_search_offered(run_id, True)  # a run gone: nothing
        return finder.Ran()

    run(selected(), Site({START: HOME}), script)
    assert seen["unsaid"] == ["search_box", "search_address", "listing"]
    assert seen["offered"] == ALL_ROUTES
    assert seen["unlisted"] == ["search_box", "search_address", "listing"]
    assert "a web search of this website's postings" not in seen["refusal"]


def test_the_first_message_no_longer_says_whether_web_search_is_on():
    """The model reads its own tool list (the prompt's Method: "when WebSearch is among your
    tools"); the first message says nothing a decision taken before the run could get wrong."""
    seen: dict[str, Any] = {}

    async def script(session):
        with finder._LOCK:
            finder._SCOPES["request-key"] = session
        try:
            seen["first"] = json.loads(
                finder.INTERACTIVE_FINDER.prompt({"request": "request-key"}, None)
            )
        finally:
            with finder._LOCK:
                finder._SCOPES.pop("request-key", None)
        return finder.Ran()

    run(selected(), Site({START: HOME}), script)
    assert "web_search" not in seen["first"]["search_scope"]
    assert "start" in seen["first"]["search_scope"]
    assert "when WebSearch is among your tools" in finder.INTERACTIVE_PROMPT
