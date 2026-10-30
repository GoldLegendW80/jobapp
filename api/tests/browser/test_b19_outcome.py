"""B19, the truthful outcome (2026-10-29): the session counts the app's refusals that make
`search_refused` win over the agent's reason (interactive.outcome), as the production traces met
them: the api's refusal of an address that is no observed link (LinkedIn's search address) and
browserd's refusal of a navigation (Indeed's `host_not_allowed`); a page that did not load is
no refusal. Enter in the box (browser_type's `submit`) is a search the session saw, so "no
match" said after it is a searched website."""

from typing import Any

import pytest

from app.browser import finder
from tests.browser.fakes import ScriptedFinder
from tests.browser.test_j18_outcome import SEARCH, Jungle
from tests.browser.test_multi_search import run
from tests.browser.test_selected_interactive import selected


def test_an_address_the_api_refuses_makes_the_refusal_win():
    """LinkedIn, 2026-10-29: the agent's search address was refused before browserd (it was no
    observed link), then it said there was no search control."""
    answers: list = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.navigate("/fr/jobs")
        answers.append(await session.navigate("/fr/jobs?query=genai+engineer"))  # never observed
        session.report_none("no_search_control")
        return finder.Ran()

    entry, _, _ = run(selected(**SEARCH), Jungle(), ScriptedFinder(script))
    [(text, error)] = answers
    assert error is True
    assert text.startswith("Refused: open only an observed link"), text
    assert entry["refused"] == {"unobserved_address": 1}
    assert entry["error"] == "search_refused"


@pytest.mark.parametrize(
    ("code", "counted"),
    [
        ("host_not_allowed", True),
        ("unsupported_discovery", True),
        ("refused_scheme", True),
        ("bad_url", True),
        ("submit", True),
        ("danger", True),
        ("navigation_failed", False),  # the page's, not the app's
    ],
)
def test_a_navigation_browserd_refuses_is_the_app_s_refusal(code, counted):
    """Indeed, 2026-10-29: browserd stopped the navigation (`host_not_allowed`)."""

    class Refusing(Jungle):
        async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name == "browser_navigate" and args.get("url", "").endswith("/fr/jobs"):
                return {"ok": False, "error": code}
            return await super().call(name, args)

    answers: list = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        answers.append(await session.navigate("/fr/jobs"))
        session.report_none("no_list")
        return finder.Ran()

    entry, _, _ = run(selected(**SEARCH), Refusing(), ScriptedFinder(script))
    if counted:
        assert answers == [(finder.NAVIGATION_REFUSED, True)]
        assert entry["refused"] == {"unsupported_interaction": 1}
        assert entry["error"] == "search_refused"
    else:
        assert answers == [("The page did not load.", True)]
        assert entry["refused"] == {}
        assert entry["error"] == "no_job_list"


def test_enter_in_the_box_is_a_search_the_session_saw():
    """A script box (Welcome to the Jungle's) searches on Enter and lists in place: "no match"
    said after it is a searched website."""

    class Entering(Jungle):
        async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            submit = args.get("submit") is True
            answer = await super().call(name, args)
            if name == "browser_type" and submit and answer["ok"]:
                self.searched = self.typed
            return answer

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.navigate("/fr/jobs")
        await session.interact("browser_click", {"ref": "e20"})  # No, thanks
        typed = {"ref": "e1", "text": "GenAI Engineer", "submit": True}
        assert (await session.interact("browser_type", typed))[1] is False
        session.report_none("no_match")
        return finder.Ran()

    entry, _, _ = run(selected(**SEARCH), Entering(results={}), ScriptedFinder(script))
    assert (entry["error"], entry["searches"]) == (None, 1)
    assert entry["searched"] == [{"title": "genai engineer", "place": None}]
