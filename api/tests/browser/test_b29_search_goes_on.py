"""Browser B29 in the api: the website search's agent is told when its tab was closed and opened
again blank, and the close is counted by reason in the source's stats, served by `SourceOut`.

The answers are browserd's own: `closed_answer` runs browserd's `actions.run` after its memory
guard closed the search's tab (test_b29_browserd's fakes), and the website (`Site` of
test_b20_agent) answers it while its tab is blank, as browserd does until a navigation. Invented
hosts only.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.browser import finder
from app.jobs import sweep_router
from app.jobs.models import Sweep, SweepStatus, SweepTrigger
from tests.browser.test_b20_agent import RESULTS, Site, report, run, selected, text_of, tool
from tests.browser.test_b29_browserd import call, guard, started

MEMORY_SAID = (
    "The tab was closed because the browser ran short of memory, and it was opened again blank: "
    "open the page again with browser_navigate. Images, videos and fonts no longer load in this "
    "search, so the page fits."
)


def closed_answer() -> dict[str, Any]:
    """browserd's answer to a click after its memory guard closed the search's tab."""
    tab, _, browser = started()
    guard(browser, used=1850)
    answer = call(tab, "browser_click", {"ref": "e7"})
    assert answer["error"] == "tab_closed"
    return answer


class ClosingSite(Site):
    """`Site` whose tab is closed when the script says so (`blank`), as browserd answers then:
    the first call answers the close whatever it is; every later one too until a navigation
    loads a page, whose answer still names the close."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.closed = closed_answer()
        self.blank = self.told = False

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        answer = await super().call(name, args)
        if not self.blank:
            return answer
        if name == "browser_navigate" and answer.get("ok") and self.told:
            self.blank = False
            return {**answer, "tab_closed": self.closed["tab_closed"]}
        self.told = True
        return self.closed

    async def snapshot(self) -> dict[str, Any]:
        if self.blank:
            self.told = True
            return self.closed
        return await super().snapshot()


@pytest.mark.parametrize("first", ["browser_click", "browser_navigate"])
def test_the_agent_is_told_the_tab_closed_and_the_search_goes_on(first: str) -> None:
    site = ClosingSite()
    seen: dict[str, dict] = {}

    async def script(session: finder.FinderSession):
        site.blank = True  # the memory guard closed the tab
        args = {"ref": "e1"} if first == "browser_click" else {"url": RESULTS}
        seen[first] = await tool(session, first, **args)
        seen["snapshot"] = await tool(session, "browser_snapshot")
        seen["find"] = await tool(session, "browser_find", pattern="Robot")
        seen["window"] = await tool(session, "browser_snapshot", offset=10)
        seen["navigate"] = await tool(session, "browser_navigate", url=RESULTS)
        seen["find after"] = await tool(session, "browser_find", pattern="Robot")
        await report(session)
        return finder.Ran()

    entry, _, _ = run(selected(), site, script)
    for name in (first, "snapshot", "find", "window"):
        assert seen[name]["is_error"] is True, name
        assert text_of(seen[name]) == MEMORY_SAID, name
    assert seen["navigate"].get("is_error") is not True
    assert json.loads(text_of(seen["navigate"]))["url"] == RESULTS
    assert "Robot" in text_of(seen["find after"])
    # Counted once by browserd's id, under its own key: never a refusal of the app.
    assert entry["tab_closed"] == 1
    assert entry["tab_closed_reasons"] == {"memory": 1}
    assert entry["tab_closed_mib"] == [1850]
    assert entry["media_blocked"] is True
    assert "refusals" not in entry
    assert "unsupported_interaction" not in entry["refused"]
    assert entry["found"] == 1  # the search went on and kept its posting


def test_a_close_with_no_known_reason_says_none() -> None:
    site = ClosingSite()
    site.closed = {**site.closed, "tab_closed": {"id": "0b290b29", "reason": "other"}}
    seen: list[dict] = []

    async def script(session: finder.FinderSession):
        site.blank = True
        seen.append(await tool(session, "browser_click", ref="e1"))
        await tool(session, "browser_navigate", url=RESULTS)
        await report(session)
        return finder.Ran()

    entry, _, _ = run(selected(), site, script)
    said = text_of(seen[0])
    assert said == (
        "The tab was closed, and it was opened again blank: open the page again with "
        "browser_navigate."
    )
    assert "memory" not in said
    assert "crash" not in said
    assert entry["tab_closed_reasons"] == {"other": 1}
    assert entry["tab_closed_mib"] == []
    assert entry["media_blocked"] is False


def test_a_site_whose_tab_never_closed_writes_no_close() -> None:
    async def script(session: finder.FinderSession):
        await report(session)
        return finder.Ran()

    entry, _, _ = run(selected(), Site(), script)
    assert "tab_closed" not in entry
    assert "media_blocked" not in entry


def row(sources: list[Any]) -> Sweep:
    return Sweep(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        trigger=SweepTrigger.manual,
        status=SweepStatus.done,
        started_at=datetime(2026, 11, 26, 8, 30, tzinfo=UTC),
        finished_at=datetime(2026, 11, 26, 8, 40, tzinfo=UTC),
        error=None,
        stats={"sources": sources},
    )


def website(**fields: Any) -> dict[str, Any]:
    return {
        "kind": "linkedin",
        "query": "",
        "location": None,
        "mode": "llm_browser",
        "found": 2,
        "postings": 2,
        "error": None,
        **fields,
    }


def test_source_out_serves_the_closes_read_defensively() -> None:
    [source] = sweep_router.sweep_out(
        row(
            [
                website(
                    tab_closed=3,
                    tab_closed_reasons={"memory": 2, "other": 1, "guessed": 4, "crash": True},
                    tab_closed_mib=[1850, "1900", 1902, -1, True],
                    media_blocked=True,
                )
            ]
        )
    ).sources
    assert source.tab_closed == 3
    assert source.tab_closed_reasons == {"memory": 2, "other": 1}
    assert source.tab_closed_mib == [1850, 1902]
    assert source.media_blocked is True
    assert source.refusals == {}


@pytest.mark.parametrize(
    "fields",
    [
        {},  # an older sweep
        {"tab_closed": "3", "tab_closed_reasons": [], "tab_closed_mib": {}, "media_blocked": 1},
    ],
)
def test_an_older_or_malformed_row_reads_no_close(fields: dict[str, Any]) -> None:
    [source] = sweep_router.sweep_out(row([website(**fields)])).sources
    body = source.model_dump(mode="json")
    assert (body["tab_closed"], body["tab_closed_reasons"]) == (0, {})
    assert (body["tab_closed_mib"], body["media_blocked"]) == ([], False)


def test_the_answers_shape_is_browserds() -> None:
    """The closed answer the fake site gives is the one browserd's run made."""
    answer = asyncio.run(asyncio.to_thread(closed_answer))
    assert set(answer) == {"ok", "error", "message", "url", "tab_closed"}
    assert set(answer["tab_closed"]) == {"id", "reason", "used_mib", "media_blocked"}
    assert finder.CLOSE_ID.fullmatch(answer["tab_closed"]["id"])
