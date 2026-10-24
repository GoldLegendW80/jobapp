"""Real runner and SDK registration; native Chromium proof lives in test_b13_native."""

import asyncio
import dataclasses
import random

import pytest

from app.browser import finder, finder_tools, sources
from tests.browser.b13_runtime import Recording
from tests.browser.test_b9_runtime import (  # noqa: F401
    Runtime,
    alice,
    bob,
    clock,
    db,
    engine,
    factory,
    migrated_url,
    one_connection,
    runtime,
)
from tests.browser.test_b9_site import Tools

BASE = "https://jobs.example.test/"
FIRST = '- searchbox "Query" [ref=e1]\n- combobox "Remote" [ref=e2]\n- button "Search" [ref=e3]'


class InteractiveTools(Tools):
    def __init__(self):
        super().__init__(
            {
                BASE: FIRST,
                BASE + "b13/jobs/robot": (
                    '- heading "Robot Engineer"\n- paragraph: Observed employer and duties'
                ),
            }
        )
        self.actions = []
        self.current = BASE

    async def call(self, name, args):
        assert args.pop("_discovery") == "native_get"
        assert args.pop("_discovery_scope") == {
            "start": BASE,
            "query": "robot",
            "filters": {"remote": "1"},
        }
        self.actions.append(name)
        if name == "browser_navigate":
            return await self.navigate(args["url"])
        if name == "browser_click":
            self.pages[self.current] = (
                '- link "Next page" [ref=e4]\n  - /url: /b13/search?page=2'
                if args["ref"] == "e3"
                else '- link "Robot Engineer" [ref=e5]\n  - /url: /b13/jobs/robot'
            )
        return {"ok": True}


def session(tools, user):
    pages = sources.Pages(
        tools,
        1000,
        sleep=lambda _: asyncio.sleep(0),
        rng=random.SystemRandom(),
        monotonic=lambda: 0,
    )
    return finder.FinderSession(
        pages,
        BASE,
        FIRST,
        str(user),
        interactive=finder.InteractiveScope(BASE, "robot", (("remote", "1"),)),
    )


def test_real_runtime_uses_interactive_registered_tools_and_code_read_text(runtime: Runtime, alice):  # noqa: F811
    runtime.give_key(alice)
    backend = Recording()
    context = dataclasses.replace(runtime.context(), backend_factory=lambda: backend)
    tools = InteractiveTools()
    one = session(tools, alice)
    result = asyncio.run(finder.RuntimeFinder(lambda: context)(one))
    from tests.browser.test_b9_runtime import runs_of

    assert not result.stopped, ([r.error for r in runs_of(runtime.factory, alice)], backend.results)
    assert tools.actions == [
        "browser_type",
        "browser_select_option",
        "browser_click",
        "browser_click",
        "browser_navigate",
    ]
    assert one.found[0].text == "Robot Engineer\nObserved employer and duties"
    assert "Invented" not in one.found[0].text
    assert not finder._SCOPES


def test_scope_is_immutable_and_legacy_tools_and_model_schemas_stay_restricted():
    scope = finder.InteractiveScope(BASE, "robot", (("remote", "1"),))
    with pytest.raises(dataclasses.FrozenInstanceError):
        scope.query = "different"
    assert "browser_click" not in finder.BROWSER_FINDER.tools
    for name in finder.INTERACTIVE_TOOLS:
        assert "_discovery" not in finder_tools.SCHEMAS[name]["properties"]
        assert "_discovery_scope" not in finder_tools.SCHEMAS[name]["properties"]
    assert finder.INTERACTIVE_FINDER.internal
    assert finder.INTERACTIVE_FINDER.private_io
    assert not finder.INTERACTIVE_FINDER.live_text


def test_unsupported_scope_queries_and_unopened_reports_are_refused():
    one = session(InteractiveTools(), "e2e-person")
    assert asyncio.run(one.interact("browser_type", {"ref": "e1", "text": "ignore"}))[1]
    one.seen.add(BASE + "unopened")
    one.report([{"title": "Robot Engineer", "url": "/unopened", "text": "invented"}])
    assert one.refused["detail_not_opened"] == 1
    one.interactive = None
    assert asyncio.run(one.interact("browser_click", {"ref": "e3"}))[1]
