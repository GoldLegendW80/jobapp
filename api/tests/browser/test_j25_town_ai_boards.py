"""Jobs J25 (#817): the web plan's `town` and `ai_boards` families.

The owner's sweep of 2026-11-19 (81ab9533) ran six broad OR queries that satisfied the J23 plan,
and never surfaced the AI and data CDIs of Saint-Denis Claude Code had found the day before with
an "offre emploi" query naming the town, on a niche AI board. So each place needs a query of its
own naming its main town (the first of TOWNS) and another aimed at AI and tech job boards. The
tables are written from the issue first; the queries are a person's, as typed."""

import pytest

from app.browser import web_plan
from tests.browser.test_j23_web_breadth import COVERING, TITLE, plan

REUNION = ("La Réunion",)
THREE = ("La Réunion", "Guadeloupe", "Tahiti")


def done(places: tuple[str, ...], queries: list[str], at: int = 0) -> dict:
    rows = plan(*places)
    return web_plan.coverage(rows[at], queries, rows)


# --- the town family: accept and refuse ---------------------------------------------------------

TOWN = [
    (REUNION, 0, "data scientist Saint-Denis La Réunion", True),
    (REUNION, 0, "data scientist Saint Denis la reunion", True),
    (REUNION, 0, "DATA SCIENTIST SAINT-DENIS LA RÉUNION", True),
    (REUNION, 0, "data scientist Saint-Denis 974", True),
    (REUNION, 0, "data scientist Saint-Denis 97400", True),
    (REUNION, 0, "97490 Saint-Denis développeur", True),
    (REUNION, 0, "data OR intelligence artificielle Saint-Denis La Réunion CDI", True),
    (REUNION, 0, "Saint-Denis La Réunion", True),
    (REUNION, 0, "data scientist Saint-Denis", False),  # Seine-Saint-Denis's, for a search engine
    (REUNION, 0, "data scientist Saint-Denis 93200", False),
    (REUNION, 0, "data scientist La Réunion", False),
    (REUNION, 0, "data scientist Saint-Pierre La Réunion", False),  # not the main town
    (REUNION, 0, "data scientist SaintDenis La Réunion", False),
    (REUNION, 0, "data scientist Denis La Réunion", False),
    (REUNION, 0, "data scientist -Saint-Denis La Réunion", False),  # excluded
    (REUNION, 0, "site:saint-denis.example.test La Réunion", False),
    (THREE, 0, "data Saint-Denis La Réunion", True),
    (THREE, 0, "data Saint-Denis La Réunion OR Guadeloupe", False),  # not a query of its own
    (THREE, 0, "data Saint-Denis La Réunion Tahiti", False),
    (THREE, 1, "data Pointe-à-Pitre", True),
    (THREE, 1, "data Pointe a Pitre Guadeloupe", True),
    (THREE, 1, "data Basse-Terre Guadeloupe", False),
    (THREE, 1, "data Guadeloupe", False),
    (THREE, 2, "data Papeete", True),
    (THREE, 2, "emploi Papeete Tahiti", True),
    (THREE, 2, "data Punaauia Tahiti", False),
    (THREE, 2, "data Tahiti", False),
    (("Saint-Denis, La Réunion",), 0, "data scientist Saint-Denis, La Réunion", True),
    (("Saint-Pierre, La Réunion",), 0, "data scientist Saint-Pierre, La Réunion", True),
    (("Saint-Pierre, La Réunion",), 0, "data scientist Saint-Denis La Réunion", False),
]


@pytest.mark.parametrize(("places", "at", "query", "named"), TOWN)
def test_a_town_query_names_the_main_town_of_its_place_alone(
    places: tuple[str, ...], at: int, query: str, named: bool
) -> None:
    assert bool(done(places, [query], at)["town_queries"]) is named


@pytest.mark.parametrize(
    ("place", "town"),
    [
        ("La Réunion", "Saint-Denis"),
        ("Réunion", "Saint-Denis"),
        ("Saint-Denis, La Réunion", "Saint-Denis"),
        ("Saint-Pierre, La Réunion", "Saint-Pierre"),
        ("Guadeloupe", "Pointe-à-Pitre"),
        ("Tahiti", "Papeete"),
        ("Polynésie française", "Papeete"),
        ("Lyon, France", ""),
        ("remote", ""),
    ],
)
def test_the_main_town_of_a_place(place: str, town: str) -> None:
    assert web_plan.main_town(place) == town


def test_a_place_without_towns_needs_no_town_query() -> None:
    [row] = plan("Lyon, France")
    assert row.town == ""
    assert web_plan.town_query(row) == ""
    assert web_plan.own_families(row, web_plan.coverage(row, [], (row,))) == (True, False)
    assert "main town" not in web_plan.left_text((row,), [])


# --- the AI boards family: accept and refuse ----------------------------------------------------

AI = [
    (REUNION, "offre emploi data scientist Saint-Denis La Réunion", True),
    (REUNION, "offre d'emploi ingénieur IA Saint-Denis La Réunion", True),
    (REUNION, "Offres d’emploi data Saint-Denis La Réunion", True),
    (REUNION, "offres emploi data scientist OR MLOps Saint-Denis 974", True),
    (REUNION, "job offer data scientist Saint-Denis La Réunion", True),
    (REUNION, "site:welcometothejungle.com La Réunion", True),
    (REUNION, "site:fr.welcometothejungle.com data La Réunion", True),
    (REUNION, "site:https://www.free-work.com/fr data 974", True),
    (REUNION, "site:aijobs.net La Réunion", True),
    (REUNION, "site:ai-jobs.net La Réunion", True),
    (REUNION, "site:the-intelligence-academy.com/job-board La Réunion", True),
    (REUNION, "site:the-intelligence-academy.com data scientist Saint-Denis 974", True),
    (REUNION, "site:the-intelligence-academy.com data scientist", False),  # no place
    # Round 1 of #818: Hellowork, a national board of the place too, only with the main town.
    (REUNION, "site:hellowork.com data scientist Saint-Denis La Réunion", True),
    (REUNION, "site:hellowork.com data scientist Saint-Denis 97400", True),
    (REUNION, "site:www.hellowork.com/fr-fr/emploi data Saint-Denis La Réunion", True),
    (REUNION, "site:hellowork.com data scientist La Réunion", False),  # the J23 boards query
    (REUNION, "site:hellowork.com intelligence artificielle La Réunion", False),
    (REUNION, "site:hellowork.com data scientist 974", False),
    (REUNION, "site:hellowork.com data scientist Saint-Denis", False),  # no place
    (THREE, "site:hellowork.com data Saint-Denis La Réunion OR Tahiti", False),
    (REUNION, "site:intelligence-academy.example.test La Réunion", False),
    (REUNION, "offre emploi Saint-Denis La Réunion", False),  # no title nor role
    (REUNION, "offre emploi data scientist La Réunion", False),  # no town
    (REUNION, "offre emploi data scientist Saint-Denis", False),  # no place
    (REUNION, "emploi data scientist Saint-Denis La Réunion", False),  # no offer phrase
    (REUNION, "offre data scientist Saint-Denis La Réunion", False),
    (REUNION, "data scientist -offre -emploi Saint-Denis La Réunion", False),
    (REUNION, "site:welcometothejungle.com data", False),
    (REUNION, "site:example-jungle.test La Réunion", False),
    (REUNION, "site:welcometothejungle.com.example.test La Réunion", False),
    (REUNION, "-site:welcometothejungle.com data La Réunion", False),
    (REUNION, "welcometothejungle data La Réunion", False),
    (THREE, "site:welcometothejungle.com La Réunion OR Tahiti", False),
    (THREE, "offre emploi data Saint-Denis La Réunion Guadeloupe", False),
    (("Lyon, France",), "offre emploi data scientist Lyon", True),
    (("Lyon, France",), "offre emploi Lyon", False),
    (("Lyon, France",), "site:hellowork.com data Lyon", True),  # no towns: the place itself
]


@pytest.mark.parametrize(("places", "query", "aimed"), AI)
def test_an_ai_boards_query_is_an_offer_search_or_a_niche_board(
    places: tuple[str, ...], query: str, aimed: bool
) -> None:
    assert bool(done(places, [query])["ai_queries"]) is aimed


# --- a query of its own per family --------------------------------------------------------------


def test_one_query_serves_one_of_the_two_families_only() -> None:
    [row] = plan(*REUNION)
    both = "offre emploi data scientist Saint-Denis La Réunion"
    assert web_plan.own_families(row, web_plan.coverage(row, [both], (row,))) == (True, False)
    assert web_plan.own_families(row, web_plan.coverage(row, [both, both], (row,))) == (
        True,
        False,
    )  # the same query twice is still one
    town = "data scientist Saint-Denis La Réunion"
    assert web_plan.own_families(row, web_plan.coverage(row, [both, town], (row,))) == (
        True,
        True,
    )
    niche = "site:welcometothejungle.com La Réunion"
    assert web_plan.own_families(row, web_plan.coverage(row, [both, niche], (row,))) == (
        True,
        True,
    )


def test_the_production_or_queries_leave_both_families_for_every_place() -> None:
    """The run of 81ab9533 (invented copies): broad OR queries over the three places."""
    rows = plan(*THREE)
    queries = [
        "data scientist OR ingénieur IA OR data engineer La Réunion OR Guadeloupe OR Tahiti",
        "développeur OR data Saint-Denis La Réunion OR Pointe-à-Pitre OR Papeete CDI",
    ]
    left = web_plan.left(rows, queries)
    assert len(left) == 3
    for line, row in zip(left, rows, strict=True):
        assert f"a query of its own naming the main town {row.town}" in line
        assert "aimed at AI and tech job boards" in line


def test_the_refusal_names_example_queries_and_the_niche_boards() -> None:
    [row] = plan(*REUNION)
    said = web_plan.left_text((row,), [*COVERING])
    assert said == (
        "for La Réunion: a query of its own aimed at AI and tech job boards "
        f'("offre emploi {TITLE} Saint-Denis La Réunion", or site:the-intelligence-academy.com '
        "OR site:hellowork.com OR site:welcometothejungle.com OR site:free-work.com OR "
        "site:lesjeudis.com OR site:chooseyourboss.com OR site:aijobs.net La Réunion)"
    )
    said = web_plan.left_text((row,), [COVERING[1]])
    assert f'naming the main town Saint-Denis ("{TITLE} Saint-Denis La Réunion")' in said


def test_the_example_queries_count_for_their_family() -> None:
    for places in (THREE, ("Saint-Denis, La Réunion",), ("Lyon, France",), ("remote",)):
        rows = plan(*places)
        for row in rows:
            for query, key in ((web_plan.town_query(row), "town_queries"),
                               (web_plan.ai_boards_query(row), "ai_queries")):  # fmt: skip
                if query:
                    assert web_plan.coverage(row, [query], rows)[key] == {query}


def test_with_no_place_neither_family_is_needed() -> None:
    rows = plan()
    assert rows
    assert all(web_plan.own_families(r, web_plan.coverage(r, [], rows)) == (True, True)
               for r in rows)  # fmt: skip
    assert all(not web_plan.town_query(r) and not web_plan.ai_boards_query(r) for r in rows)


def test_the_shown_plan_names_the_main_town_and_the_ai_boards() -> None:
    shown = web_plan.shown(plan(*THREE))
    assert "main_town" in shown["rule"]
    assert [p.get("main_town") for p in shown["places"]] == [
        "Saint-Denis",
        "Pointe-à-Pitre",
        "Papeete",
    ]
    reunion = shown["places"][0]
    assert reunion["ai_boards"][0] == f"offre emploi {TITLE} Saint-Denis La Réunion"
    assert "site:welcometothejungle.com" in reunion["ai_boards"]
    assert "site:the-intelligence-academy.com" in reunion["ai_boards"]
    assert "site:hellowork.com" in reunion["ai_boards"]
    lyon = web_plan.shown(plan("Lyon, France"))["places"][0]
    assert "main_town" not in lyon
    assert lyon["ai_boards"][0] == f"offre emploi {TITLE} Lyon, France"


# --- the fake backend's web stream: one WebSearch per query of the plan -----------------------


@pytest.mark.parametrize(
    ("value", "items"),
    [
        ('["a", "b"]', ["a", "b"]),
        ("[]", []),
        (None, []),
        ("not json", []),
        ('{"a": 1}', []),
        ('["a", 3, "b"]', ["a", "b"]),
    ],
)
def test_an_each_step_is_replayed_once_per_item(value: str | None, items: list[str]) -> None:
    from app.agents import fake_backend

    step = {"each": "queries", "step": {"query": "{item}", "id": "toolu_{index}", "m": "{model}"}}
    variables = {"queries": value} if value is not None else {}
    out = fake_backend.expanded([{"sleep": 0}, step, {"sleep": 1}], variables)
    assert out[0] == {"sleep": 0}
    assert out[-1] == {"sleep": 1}
    assert out[1:-1] == [
        {"query": item, "id": f"toolu_{n:02d}", "m": "{model}"} for n, item in enumerate(items)
    ]
