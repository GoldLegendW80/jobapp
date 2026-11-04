"""B20: a job website's summary names and counts the searches its agent ran again.

The agent types what it judges right (owner, 2026-11-04), so a search is told by what it did,
never by matching the saved values: what it typed into boxes counts once it submitted them
(browser_type with submit, or a click or Enter as its next action, a choice, a tick, a hover, a
scroll or another key in between keeping it), named by the texts typed and the options chosen
with them; and an address it opened whose query holds one of the saved titles, as whole words,
case and accents aside, counts as a search by that title (and the first saved place it holds),
unless a search of that title was counted already (its next page).
"""

import asyncio
import random
from typing import Any

import pytest

from app.browser import finder, sources
from tests.browser import test_selected_interactive as selected_module
from tests.browser.fakes import ScriptedFinder
from tests.browser.test_b13_runtime import BASE

PAGE = '- searchbox "What" [ref=e1]\n- combobox "Where" [ref=e2]\n- button "Search" [ref=e3]'
TITLES = ("Data engineer", "Ingénieur robotique")
PLACES = ("Paris", "Lyon")


class Site:
    """browserd's answers on a website that takes every action; `refuse` names the tools the
    line refuses (browserd's `line` code)."""

    def __init__(self, refuse: frozenset[str] = frozenset()) -> None:
        self.current, self.refuse = BASE, refuse

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name in self.refuse:
            return {"ok": False, "error": "line", "message": "Refused."}
        if name == "browser_navigate":
            self.current = args["url"]
        return {"ok": True}

    async def snapshot(self) -> dict[str, Any]:
        return {"ok": True, "url": self.current, "snapshot": PAGE}


def session(site: Site | None = None) -> finder.FinderSession:
    pages = sources.Pages(
        site or Site(),
        float("inf"),
        sleep=lambda _: asyncio.sleep(0),
        rng=random.Random(7),  # noqa: S311  deterministic test pacing
        monotonic=lambda: 0.0,
    )
    return finder.FinderSession(
        pages,
        BASE,
        PAGE,
        "e2e-user",
        interactive=finder.InteractiveScope(BASE, TITLES, PLACES),
    )


Step = tuple[str, dict[str, Any]]
TYPE: Step = ("browser_type", {"ref": "e1", "text": "data engineer"})
CLICK: Step = ("browser_click", {"ref": "e3"})


def play(steps: list[Step], site: Site | None = None) -> finder.FinderSession:
    one = session(site)

    async def go() -> None:
        for name, args in steps:
            if name == "browser_navigate":
                await one.navigate(args["url"])
            else:
                await one.interact(name, args)

    asyncio.run(go())
    return one


# (steps, the searches the summary names, in order)
CASES: list[tuple[str, list[Step], list[tuple[str, str | None]]]] = [
    ("typed and submitted", [("browser_type", {**TYPE[1], "submit": True})],
     [("data engineer", None)]),
    ("typed then a click", [TYPE, CLICK], [("data engineer", None)]),
    ("typed then Enter", [TYPE, ("browser_press_key", {"key": "Enter"})],
     [("data engineer", None)]),
    ("a double click", [TYPE, ("browser_click", {"ref": "e3", "doubleClick": True})],
     [("data engineer", None)]),
    ("typed, a place chosen, then a click",
     [TYPE, ("browser_select_option", {"ref": "e2", "values": ["Paris"]}), CLICK],
     [("data engineer", "Paris")]),
    ("a place chosen first, then typed",
     [("browser_select_option", {"ref": "e2", "values": ["Lyon"]}), TYPE, CLICK],
     [("data engineer", "Lyon")]),
    ("two boxes typed",
     [TYPE, ("browser_type", {"ref": "e2", "text": "Paris"}), CLICK],
     [("data engineer, Paris", None)]),
    ("a box typed twice keeps its last text",
     [TYPE, ("browser_type", {"ref": "e1", "text": "robotics  engineer "}), CLICK],
     [("robotics engineer", None)]),
    ("a hover, a scroll, a tick and Tab keep what was typed",
     [TYPE, ("browser_hover", {"ref": "e3"}), ("browser_scroll", {"dy": 400}),
      ("browser_check", {"ref": "e2"}), ("browser_press_key", {"key": "Tab"}), CLICK],
     [("data engineer", None)]),
    ("typed in another language, as the agent judges right",
     [("browser_type", {"ref": "e1", "text": "Dateningenieur", "submit": True})],
     [("Dateningenieur", None)]),
    ("the same search twice, case and spacing aside, counts once",
     [("browser_type", {"ref": "e1", "text": "Data Engineer", "submit": True}),
      ("browser_type", {"ref": "e1", "text": "data  engineer", "submit": True})],
     [("Data Engineer", None)]),
    ("two searches", [TYPE, CLICK, ("browser_type", {"ref": "e1", "text": "ML engineer"}), CLICK],
     [("data engineer", None), ("ML engineer", None)]),
    ("a click with nothing typed", [CLICK], []),
    ("a choice alone is a filter", [("browser_select_option", {"ref": "e2", "values": ["Paris"]}),
                                    CLICK], []),
    ("typed, never submitted", [TYPE, ("browser_snapshot", {})], []),
    ("a box cleared", [TYPE, ("browser_type", {"ref": "e1", "text": "  "}), CLICK], []),
    ("typed, then back", [TYPE, ("browser_navigate_back", {}), CLICK], []),
    ("typed, then a script", [TYPE, ("browser_evaluate", {"function": "() => 1"}), CLICK], []),
    ("typed, then another tab", [TYPE, ("browser_tabs", {"action": "new"}), CLICK], []),
    ("typed, then another address", [TYPE, ("browser_navigate", {"url": "/about"}), CLICK], []),
    ("typed, then Escape and a click",
     [TYPE, ("browser_press_key", {"key": "Escape"}), CLICK], [("data engineer", None)]),
    # Opening an address: a saved title held by its query.
    ("an address with a saved title and place",
     [("browser_navigate", {"url": "/jobs?q=data+engineer&l=Paris"})],
     [("Data engineer", "Paris")]),
    ("the title percent encoded, another case",
     [("browser_navigate", {"url": "/jobs?keywords=Data%20Engineer"})],
     [("Data engineer", None)]),
    ("accents aside", [("browser_navigate", {"url": "/jobs?q=ingenieur-robotique"})],
     [("Ingénieur robotique", None)]),
    ("the title inside a longer query",
     [("browser_navigate", {"url": "/jobs?q=senior+data+engineer+python&where=Lyon"})],
     [("Data engineer", "Lyon")]),
    ("its next page counts no new search",
     [("browser_navigate", {"url": "/jobs?q=data+engineer"}),
      ("browser_navigate", {"url": "/jobs?q=data+engineer&page=2"})],
     [("Data engineer", None)]),
    ("the next page of a typed search", [TYPE, CLICK,
     ("browser_navigate", {"url": "/jobs?q=data+engineer&l=Paris&page=2"})],
     [("data engineer", None)]),
    ("glued words", [("browser_navigate", {"url": "/jobs?q=dataengineer"})], []),
    ("another word order", [("browser_navigate", {"url": "/jobs?q=engineer+data"})], []),
    ("one word of a title", [("browser_navigate", {"url": "/jobs?q=data"})], []),
    ("a longer word", [("browser_navigate", {"url": "/jobs?q=big+data+engineering"})], []),
    ("in the path, not the query", [("browser_navigate", {"url": "/jobs/data-engineer"})], []),
    ("a place alone", [("browser_navigate", {"url": "/jobs?l=Paris"})], []),
    ("in the fragment", [("browser_navigate", {"url": "/jobs#q=data+engineer"})], []),
]  # fmt: skip


@pytest.mark.parametrize(("steps", "named"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_what_counts_as_a_search(steps: list[Step], named: list[tuple[str, str | None]]) -> None:
    assert list(play(steps).searches) == named


def test_a_refused_action_counts_nothing_and_keeps_what_was_typed() -> None:
    """The line refused the click (browserd's `line`): no search; the next click that goes
    through submits what was typed."""
    site = Site(refuse=frozenset({"browser_click"}))
    one = play([TYPE, CLICK], site)
    assert list(one.searches) == []
    site.refuse = frozenset()

    async def click() -> None:
        await one.interact(*CLICK)

    asyncio.run(click())
    assert list(one.searches) == [("data engineer", None)]


def test_a_refused_address_counts_nothing() -> None:
    site = Site(refuse=frozenset({"browser_navigate"}))
    assert list(play([("browser_navigate", {"url": "/jobs?q=data+engineer"})], site).searches) == []


def test_names_and_counts_are_bounded() -> None:
    long = "x" * 2000
    one = play([("browser_type", {"ref": "e1", "text": long, "submit": True})])
    [(title, place)] = one.searches
    assert (title, place) == ("x" * finder.SEARCH_NAME_MAX, None)
    many = [
        ("browser_type", {"ref": "e1", "text": f"engineer {n}", "submit": True})
        for n in range(finder.SEARCHES_MAX + 5)
    ]
    assert len(play(many).searches) == finder.SEARCHES_MAX


def test_the_summary_names_and_counts_the_searches(monkeypatch) -> None:
    """Through the website's run (interactive.run_selected): the source's entry holds the count
    and the names, as GET /api/jobs/sweeps/{id} reads them."""

    async def script(one: finder.FinderSession) -> finder.Ran:
        await one.interact("browser_type", {"ref": "e1", "text": "robot", "submit": True})
        await one.navigate("/b13/search?q=robot&page=2")
        await one.interact("browser_type", {"ref": "e1", "text": "Robotiker", "submit": True})
        return finder.Ran(input_tokens=0, output_tokens=0)

    class Tools(selected_module.Tools):
        async def call(self, name, args):
            if name == "browser_navigate" and "?" in args["url"]:
                self.pages.setdefault(args["url"], self.pages[selected_module.BASE])
            return await super().call(name, args)

    entries, *_ = asyncio.run(
        selected_module.execute(
            selected_module.selected(), "e2e-user", ScriptedFinder(script), tools=Tools()
        )
    )
    [entry] = entries
    assert entry["searches"] == 2
    assert entry["searched"] == [
        {"title": "robot", "place": None},
        {"title": "Robotiker", "place": None},
    ]
