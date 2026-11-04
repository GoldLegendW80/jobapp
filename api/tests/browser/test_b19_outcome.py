"""B19, the truthful outcome (2026-10-29): the session counts the app's refusals that make
`search_refused` win over the agent's reason (interactive.outcome), as the production traces met
them: browserd's refusal of a navigation (Indeed's `host_not_allowed`), and since B20 the line's
(`line`); a page that did not load is no refusal. B20 (owner, 2026-11-04): the api refuses no
address of its own any more, and "no match" said after the agent acted on the website is a
searched website."""

from typing import Any

import pytest

from app.browser import finder
from tests.browser.fakes import ScriptedFinder
from tests.browser.test_j18_outcome import SEARCH, Jungle
from tests.browser.test_multi_search import run
from tests.browser.test_selected_interactive import selected


@pytest.mark.parametrize(
    ("code", "counted"),
    [
        ("host_not_allowed", True),
        ("unsupported_discovery", True),
        ("refused_scheme", True),
        ("bad_url", True),
        ("submit", True),
        ("danger", True),
        ("line", True),  # B20: the line of tools/line.py
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


@pytest.mark.parametrize(("acted", "error"), [(True, None), (False, "nothing_reported")])
def test_no_match_after_the_agent_acted_is_a_searched_website(acted, error):
    """A script box (Welcome to the Jungle's) searches on Enter and lists in place: "no match"
    said after it is a searched website; said with nothing done on the website, it is not."""

    async def script(session: finder.FinderSession) -> finder.Ran:
        if acted:
            await session.navigate("/fr/jobs")
            await session.interact("browser_click", {"ref": "e20"})  # No, thanks
            typed = {"ref": "e1", "text": "GenAI Engineer", "submit": True}
            assert (await session.interact("browser_type", typed))[1] is False
        session.report_none("no_match")
        return finder.Ran()

    entry, _, _ = run(selected(**SEARCH), Jungle(results={}), ScriptedFinder(script))
    assert entry["error"] == error
