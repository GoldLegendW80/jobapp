"""Jobs J26 (#819): the web plan's `ai_boards` family is met only by a `site:` query of a niche
AI and tech job board naming the place or its main town.

The owner's sweep of 2026-11-19 (f769661a) met the family with "offre emploi genai engineer
Saint-Denis La Réunion", and the search engine returned none of the pages of the niche board
the-intelligence-academy.com, where four Saint-Denis AI and data CDIs were online. The table is
written from the issue first; the queries are a person's, as typed."""

import pytest

from app.browser import web_plan
from tests.browser.test_j23_web_breadth import COVERING, plan

REUNION = ("La Réunion",)
THREE = ("La Réunion", "Guadeloupe", "Tahiti")
LYON = ("Lyon, France",)
EXAMPLE = "site:the-intelligence-academy.com OR site:hellowork.com data Saint-Denis La Réunion"


def aimed(places: tuple[str, ...], query: str, at: int = 0) -> bool:
    rows = plan(*places)
    return bool(web_plan.coverage(rows[at], [query], rows)["ai_queries"])


AI = [
    # Accepted: a niche board by site:, alone or joined by OR, the place or its main town named.
    (REUNION, 0, EXAMPLE, True),
    (REUNION, 0, "site:the-intelligence-academy.com data Saint-Denis La Réunion", True),
    (REUNION, 0, "site:the-intelligence-academy.com La Réunion", True),
    (REUNION, 0, "site:the-intelligence-academy.com data 974", True),
    (REUNION, 0, "site:the-intelligence-academy.com data Saint-Denis 97400", True),
    (REUNION, 0, "SITE:The-Intelligence-Academy.com DATA SAINT-DENIS LA RÉUNION", True),
    (REUNION, 0, "site:www.the-intelligence-academy.com/job-board data La Réunion", True),
    (REUNION, 0, "site:welcometothejungle.com OR site:free-work.com data La Réunion", True),
    (REUNION, 0, "site:hellowork.com OR site:aijobs.net data La Réunion", True),
    (REUNION, 0, "site:candidat.francetravail.fr OR site:lesjeudis.com data 974", True),
    (REUNION, 0, "site:chooseyourboss.com data scientist OR MLOps La Réunion", True),
    (REUNION, 0, "site:hellowork.com data Saint-Denis La Réunion", True),
    (THREE, 1, "site:the-intelligence-academy.com data Pointe-à-Pitre", True),
    (THREE, 2, "site:the-intelligence-academy.com data Papeete", True),
    (THREE, 1, "site:the-intelligence-academy.com data Basse-Terre", True),  # names Guadeloupe
    (LYON, 0, "site:the-intelligence-academy.com data Lyon", True),
    # Refused: the J25 offer phrase form, the production query of f769661a first.
    (REUNION, 0, "offre emploi genai engineer Saint-Denis La Réunion", False),
    (REUNION, 0, "offre emploi data scientist Saint-Denis La Réunion", False),
    (REUNION, 0, "offre d'emploi ingénieur IA Saint-Denis 974", False),
    (REUNION, 0, "job offer data scientist Saint-Denis La Réunion", False),
    (REUNION, 0, "the-intelligence-academy.com data Saint-Denis La Réunion", False),
    (REUNION, 0, "the intelligence academy data Saint-Denis La Réunion", False),
    (LYON, 0, "offre emploi data scientist Lyon", False),
    # Refused: a board outside the niche table, or not by site:.
    (REUNION, 0, "site:candidat.francetravail.fr data Saint-Denis La Réunion", False),
    (REUNION, 0, "site:apec.fr data Saint-Denis La Réunion", False),
    (REUNION, 0, "site:intelligence-academy.example.test data La Réunion", False),
    (REUNION, 0, "site:the-intelligence-academy.com.example.test data La Réunion", False),
    (REUNION, 0, "site:com data La Réunion", False),
    (REUNION, 0, "-site:the-intelligence-academy.com data La Réunion", False),
    # Refused: no place named, or the shared town alone (Seine-Saint-Denis for a search engine).
    (REUNION, 0, "site:the-intelligence-academy.com data", False),
    (REUNION, 0, "site:the-intelligence-academy.com data Saint-Denis", False),
    (REUNION, 0, "site:the-intelligence-academy.com data Saint-Denis 93200", False),
    (REUNION, 0, "site:the-intelligence-academy.com OR site:hellowork.com data Saint-Denis", False),
    # Refused: Hellowork, a national board too, without the main town (its J23 boards query).
    (REUNION, 0, "site:hellowork.com data La Réunion", False),
    (REUNION, 0, "site:hellowork.com data 974", False),
    # Refused: two sites with no OR are ANDed, no page is on both.
    (REUNION, 0, "site:the-intelligence-academy.com site:hellowork.com data La Réunion", False),
    # Refused: not a query of its own.
    (THREE, 0, "site:the-intelligence-academy.com data La Réunion Guadeloupe", False),
    (THREE, 0, "site:the-intelligence-academy.com data La Réunion OR Tahiti", False),
]


@pytest.mark.parametrize(("places", "at", "query", "counted"), AI)
def test_only_a_site_query_of_a_niche_board_meets_ai_boards(
    places: tuple[str, ...], at: int, query: str, counted: bool
) -> None:
    assert aimed(places, query, at) is counted


def test_the_production_queries_of_f769661a_leave_the_ai_boards_family() -> None:
    """The run of f769661a (invented copy): the covering searches and the offer phrase one."""
    [row] = plan(*REUNION)
    queries = [*COVERING, "offre emploi genai engineer Saint-Denis La Réunion"]
    assert web_plan.own_families(row, web_plan.coverage(row, queries, (row,))) == (True, False)
    [line] = web_plan.left((row,), queries)
    assert "site: of one or more AI and tech job boards" in line
    assert f'("{EXAMPLE}")' in line
    assert web_plan.left((row,), [*queries, EXAMPLE]) == []


def test_the_refusal_and_the_shown_plan_name_the_niche_boards() -> None:
    rows = plan(*THREE)
    said = web_plan.left_text(rows, [])
    for board in web_plan.NICHE:
        assert f"site:{board.hosts[0]}" in said
    assert EXAMPLE in said
    assert "offre emploi" not in said
    shown = web_plan.shown(rows)
    assert 'a search without site: ("offre emploi ...") is not one' in shown["rule"]
    reunion = shown["places"][0]
    assert reunion["ai_boards"] == [f"site:{b.hosts[0]}" for b in web_plan.NICHE]
    assert reunion["ai_boards_query"] == EXAMPLE
    assert shown["places"][1]["ai_boards_query"] == (
        "site:the-intelligence-academy.com OR site:hellowork.com data Pointe-à-Pitre Guadeloupe"
    )
    assert "ai_boards_query" not in web_plan.shown(plan())["places"][0]


def test_the_example_query_uses_the_first_term_without_the_broad_data_role() -> None:
    """A plan whose title exclusions leave out the broad "data" role names its first title."""
    [row] = plan(*REUNION)
    narrow = web_plan.Row(**{**row.__dict__, "roles": ()})
    query = web_plan.ai_boards_query(narrow)
    assert query == (
        f"site:the-intelligence-academy.com OR site:hellowork.com {row.titles[0]} "
        "Saint-Denis La Réunion"
    )
    assert web_plan.coverage(narrow, [query], (narrow,))["ai_queries"] == {query}


def test_the_covering_queries_search_ai_boards_by_site() -> None:
    rows = plan(*THREE)
    queries = web_plan.covering_queries(rows)
    ai = [q for q in queries[1:] if q.startswith("site:")]
    assert len(ai) == len(rows)
    assert all("site:the-intelligence-academy.com OR site:hellowork.com" in q for q in ai)
    assert not any("offre emploi" in q for q in queries)
    assert web_plan.left(rows, queries) == []
