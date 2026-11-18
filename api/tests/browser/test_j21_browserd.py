"""Jobs J21 in browserd: the lease of "Anywhere on the web" (start `https://anywhere.invalid/`,
never opened) is a discovery like a website's, and opens or fetches any public page.

Only the Playwright boundary is fake (tests/browser/test_b17_browserd.py's)."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.browser import finder
from app.browser.sources import SourceError
from app.browser.sources.interactive import GuardedTools, SourceContext

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    not (ROOT / "browser/browserd").is_dir(), reason="the repository root is not mounted here"
)
sys.path.insert(0, str(ROOT / "browser"))

from browserd.tools import actions, discovery, fetch, policy  # noqa: E402

from tests.browser.test_b17_browserd import Context, Page, tab_on  # noqa: E402

POSTINGS = (
    "https://jobs.example-ai-board.test/p/1",
    "https://candidat.francetravail.test/offres/recherche/detail/123ABC",
)


def test_the_web_scope_parses_and_any_public_page_is_open_to_it() -> None:
    scope = discovery.Scope.parse(finder.InteractiveScope(start=finder.WEB_START).tool_scope())
    assert scope == discovery.Scope(finder.WEB_START)
    hosts = tuple(finder.lease_hosts(finder.WEB_START))
    assert hosts == ("anywhere.invalid",)
    assert policy.parse_hosts(list(hosts)) == hosts
    for url in POSTINGS:
        # Off the lease's list, yet a discovery opens and fetches any public address (B20).
        assert policy.url_verdict(url, hosts) == "host_not_allowed"
        assert discovery.public_url(url)
        assert fetch.allowed(url) is None
    assert fetch.allowed("http://127.0.0.1/") == "private_address"


def anonymous_tab() -> tuple[actions.Tab, Context]:
    made = Context([Page()])

    async def new_context(**_: Any) -> Context:
        return made

    tab = tab_on(Context([Page()]))
    tab.browser.browser = SimpleNamespace(contexts=tab.browser.browser.contexts)
    tab.browser.browser.new_context = new_context
    return tab, made


@pytest.mark.parametrize("first", ["web_fetch", "browser_navigate"])
def test_the_web_search_may_begin_with_a_fetch_or_a_navigation(first: str) -> None:
    tab, made = anonymous_tab()
    scope = discovery.Scope(finder.WEB_START)
    asyncio.run(tab.begin_discovery(scope, first, {"url": POSTINGS[1]}))
    assert tab.state.discovery
    assert tab.state.native_context is made
    assert made.routes  # the line's route holds every request
    # The next calls reuse it; another scope is refused.
    asyncio.run(tab.begin_discovery(scope, "browser_navigate", {"url": POSTINGS[0]}))
    with pytest.raises(actions.UnsupportedDiscovery):
        asyncio.run(tab.begin_discovery(discovery.Scope(POSTINGS[0]), "web_fetch", {}))


def test_no_other_tool_begins_a_discovery() -> None:
    tab, _ = anonymous_tab()
    with pytest.raises(actions.UnsupportedDiscovery):
        asyncio.run(tab.begin_discovery(discovery.Scope(finder.WEB_START), "browser_click", {}))
    assert not tab.state.discovery


class Tools:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(name)
        return {"ok": True}


def test_the_api_lets_a_fetch_be_the_first_call_and_nothing_else() -> None:
    context = SourceContext("u", None, "web:anywhere", "custom", finder.WEB_START)
    tools = Tools()
    guarded = GuardedTools(tools, None, context)
    asyncio.run(guarded.call("web_fetch", {"url": POSTINGS[0]}))
    assert guarded.reserved
    assert tools.calls == ["web_fetch"]
    other = GuardedTools(Tools(), None, context)
    with pytest.raises(SourceError, match="permission_unavailable"):
        asyncio.run(other.call("browser_click", {}))
