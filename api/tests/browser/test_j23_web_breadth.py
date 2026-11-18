"""Jobs J23 (#813): "Anywhere on the web" searches as widely as Claude Code before it gives up.

The plan (app/browser/web_plan.py: per place, the saved titles, at least three roles next to the
profile, the national boards by site: query) is shown in the first message and counted from the
recorded WebSearch queries; report_no_postings is refused until it is searched, or WebSearch is
not offered or keeps failing. Approximate or missing dates are kept and flagged. Fakes only:
WebSearch is never run (its gate hook is called as the runner calls it), invented posting hosts.
"""

import asyncio
import json
import random
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa

from app.agents import fake_backend
from app.agents.models import RunStatus
from app.browser import finder, sources, web_plan
from app.jobs import sweep
from app.jobs.models import Job, JobEvent, Sweep, SweepTrigger
from tests.browser.runtime_support import (  # noqa: F401  (fixtures)
    Runtime,
    alice,
    clock,
    db,
    engine,
    factory,
    migrated_url,
    one_connection,
    runs_of,
    runtime,
)
from tests.browser.test_b20_agent import text_of, tool

PROFILE = json.dumps(
    {
        "recent_roles": ["GenAI Engineer"],
        "skills": [{"name": "LLM agents", "terms": ["Python", "FastAPI", "React"]}],
    }
)
TITLE = "ingénieur IA"
NICHE = "https://jobs.example-ai-board.test/p/1"
FRANCE_TRAVAIL = "https://candidat.francetravail.test/offres/recherche/detail/123ABC"


def plan(*places: str, titles: tuple[str, ...] = (TITLE,), criteria: str = "{}"):
    return web_plan.plan_of(titles, places, criteria, PROFILE)


# --- the roles next to the profile (the table written first) -------------------------------


def test_the_roles_follow_the_profile_then_the_broad_ones_of_claude_code() -> None:
    roles = [r.fr for r in plan("La Réunion")[0].roles]
    assert roles == [
        "data scientist",
        "ingénieur machine learning",
        "développeur Python",
        "développeur full stack",
        "développeur",
        "data",
        "intelligence artificielle",
    ]


@pytest.mark.parametrize(
    ("profile", "titles", "excluded", "expected"),
    [
        # An empty or unreadable profile: the three broad roles only, never fewer than three.
        ({}, (), (), ["développeur", "data", "intelligence artificielle"]),
        ({"skills": "not a list"}, (), (), ["développeur", "data", "intelligence artificielle"]),
        # A trigger is a whole word: "Rust" calls the software engineer, "Trust" nothing.
        (
            {"skills": [{"name": "Rust", "terms": []}]},
            (),
            (),
            ["ingénieur logiciel", "développeur", "data", "intelligence artificielle"],
        ),
        (
            {"skills": [{"name": "Trust and safety", "terms": []}]},
            (),
            (),
            ["développeur", "data", "intelligence artificielle"],
        ),
        # A saved title already searches a role: it is not listed twice.
        (
            {"skills": [{"name": "SQL", "terms": ["Airflow"]}]},
            ("Data engineer",),
            (),
            ["développeur", "intelligence artificielle"],
        ),
        # An unwanted title term leaves the roles it is in.
        (
            {"recent_roles": ["Consultant"], "skills": [{"name": "Python", "terms": []}]},
            (),
            ("consultant",),
            ["développeur Python", "développeur", "data", "intelligence artificielle"],
        ),
        # Accents and case aside.
        (
            {"adjacent_skills": [{"name": "DÉPLOIEMENT", "terms": []}]},
            (),
            (),
            ["MLOps", "développeur", "data", "intelligence artificielle"],
        ),
    ],
)
def test_the_role_table(profile, titles, excluded, expected) -> None:
    assert [r.fr for r in web_plan.adjacent_roles(profile, titles, excluded)] == expected


def test_an_unwanted_title_from_the_criteria_is_never_planned() -> None:
    criteria = json.dumps({"title_exclude": {"terms": ["full stack"]}})
    roles = [r.fr for r in plan("Paris", criteria=criteria)[0].roles]
    assert "développeur full stack" not in roles
    assert "data scientist" in roles


# --- the boards of a place's country ------------------------------------------------------------


@pytest.mark.parametrize(
    ("place", "country", "first_board"),
    [
        ("La Réunion", "France", "candidat.francetravail.fr"),
        ("Tahiti", "France", "candidat.francetravail.fr"),
        ("Guadeloupe", "France", "candidat.francetravail.fr"),
        ("Paris", "France", "candidat.francetravail.fr"),
        ("Bordeaux, France", "France", "candidat.francetravail.fr"),
        ("Genève", "Switzerland", "job-room.ch"),
        ("Bruxelles", "Belgium", "leforem.be"),
        ("London", "United Kingdom", "findajob.dwp.gov.uk"),
        ("Berlin", "Germany", "arbeitsagentur.de"),
        ("Montréal", "Canada", "jobbank.gc.ca"),
        ("Remote", None, None),
        ("Atlantis", None, None),
    ],
)
def test_the_country_table(place: str, country: str | None, first_board: str | None) -> None:
    [row] = plan(place)
    assert row.country == country
    assert (row.boards[0].hosts[0] if row.boards else None) == first_board


def test_no_place_saved_or_no_limit_plans_each_search_language() -> None:
    for places in ((), ("partout",), ("Worldwide",), ("  ",)):
        french, english = plan(*places)
        assert (french.place, french.language, french.country) == (None, "fr", "France")
        assert french.boards[0].hosts == ("candidat.francetravail.fr",)
        assert (english.place, english.language, english.boards) == (None, "en", ())
    broad_fr = "ingénieur IA OR développeur OR data OR intelligence artificielle CDI"
    broad_en = "ingénieur IA OR developer OR data OR artificial intelligence jobs"
    boards = ("site:candidat.francetravail.fr data", "site:apec.fr IA", "site:hellowork.com IA")
    rows = plan()
    # French words count for the French row only, and its boards are France's.
    assert web_plan.left(rows, [broad_fr, *boards]) == [
        "with no place, in English: 2 more roles next to the profile (of: data scientist, "
        "machine learning engineer, Python developer, full stack developer, developer, "
        "artificial intelligence)"
    ]
    assert web_plan.left(rows, [broad_fr, broad_en, *boards]) == []
    left = web_plan.left(rows, [broad_fr, broad_en])
    assert left == [
        "with no place, in French: the national job boards by site: query "
        "(site:candidat.francetravail.fr, site:apec.fr, site:hellowork.com)"
    ]


@pytest.mark.parametrize(
    ("languages", "expected"),
    [
        (None, ("fr", "en")),
        ([], ("fr", "en")),
        ("French", ("fr", "en")),  # not a list: the default
        ([{"name": "English", "level": "C1"}], ("en",)),
        ([{"name": "Français"}, {"name": "Anglais"}], ("fr", "en")),
        ([{"name": "english"}, {"name": "FRENCH"}, {"name": "French"}], ("en", "fr")),
        ([{"name": "Spanish"}], ("fr", "en")),  # a language the table does not name
        ([{"name": "Créole réunionnais"}, {"name": "French"}], ("fr",)),
        ([{"name": "French Creole"}], ("fr", "en")),  # a longer name is not French
        ([{"name": 7}, {"level": "B2"}, "English"], ("en",)),
    ],
)
def test_the_search_language_table(languages, expected) -> None:
    profile = {} if languages is None else {"languages": languages}
    assert web_plan.search_languages(profile) == expected


# --- the family matching table ------------------------------------------------------------------

REUNION = plan("La Réunion")[0]


@pytest.mark.parametrize(
    ("query", "titles", "roles", "boards"),
    [
        # Accepted.
        ("ingénieur IA La Réunion", {TITLE}, set(), set()),
        ("INGENIEUR ia cdi reunion", {TITLE}, set(), set()),
        ("ingénieur IA 974", {TITLE}, set(), set()),
        ("ingénieur-IA île de la Réunion", {TITLE}, set(), set()),
        (
            '"ingénieur IA" OR "data scientist" Saint-Denis La Réunion',
            {TITLE},
            {"data scientist", "data"},
            set(),
        ),
        (
            "développeur OR data OR intelligence artificielle La Réunion Saint-Denis CDI",
            set(),
            {"developer", "data", "artificial intelligence"},
            set(),
        ),
        (
            "site:candidat.francetravail.fr data scientist 974",
            set(),
            {"data scientist", "data"},
            {"France Travail"},
        ),
        ("site:francetravail.fr développeur Réunion", set(), {"developer"}, {"France Travail"}),
        ("site:www.apec.fr La Réunion", set(), set(), {"APEC"}),
        ("SITE:apec.fr reunion", set(), set(), {"APEC"}),
        ("site:https://www.hellowork.com/fr-fr/emploi La Reunion", set(), set(), {"Hellowork"}),
        ("python developer Reunion Island", set(), {"Python developer", "developer"}, set()),
        # Refused: no place, another place, a longer word, another order, an exclusion.
        ("ingénieur IA CDI", set(), set(), set()),
        ("ingénieur IA Saint-Denis", set(), set(), set()),
        ("ingénieur IA Guadeloupe", set(), set(), set()),
        ("ingénieur IA réunionnais", set(), set(), set()),
        ("ingénieur IA 9745", set(), set(), set()),
        ("ingénieurs IAS La Réunion", set(), set(), set()),
        ("IA ingénieur La Réunion", set(), set(), set()),
        ('La Réunion -"ingénieur IA"', set(), set(), set()),
        ("ingénieur IA -Réunion", set(), set(), set()),
        # Refused boards: no site: operator, an excluded one, a suffix, a look alike host.
        ("apec.fr La Réunion", set(), set(), set()),
        ("inurl:apec.fr La Réunion", set(), set(), set()),
        ("-site:apec.fr La Réunion", set(), set(), set()),
        ("site:fr La Réunion", set(), set(), set()),
        ("site:apec.fr.example.test La Réunion", set(), set(), set()),
        ("site:notapec.fr La Réunion", set(), set(), set()),
        ("site:apec.fr", set(), set(), set()),
        ("", set(), set(), set()),
    ],
)
def test_the_family_matching_table(query: str, titles, roles, boards) -> None:
    done = web_plan.coverage(REUNION, [query])
    assert (done["titles"], done["roles"], done["boards"]) == (titles, roles, boards)


def test_a_place_without_boards_needs_a_jobs_query() -> None:
    [row] = plan("Remote")
    assert web_plan.coverage(row, ["ingénieur IA remote"])["jobs"] is False
    assert web_plan.coverage(row, ["ingénieur IA remote jobs"])["jobs"] is True
    assert web_plan.coverage(row, ["ingénieur IA jobs"])["jobs"] is False  # no place


def test_the_plan_is_shown_in_the_first_message() -> None:
    session = web_session(places=("La Réunion", "Tahiti"))
    request = "a" * 32
    with finder._LOCK:
        finder._SCOPES[request] = session
    try:
        first = json.loads(finder.WEB_FINDER.prompt({"request": request}, None))
    finally:
        with finder._LOCK:
            finder._SCOPES.pop(request, None)
    shown = first["search_plan"]
    assert "report_no_postings is refused" in shown["rule"]
    reunion, tahiti = shown["places"]
    assert reunion["place"] == "La Réunion"
    assert "974" in reunion["also_named"]
    assert "papeete" in tahiti["also_named"]
    assert reunion["titles"] == [TITLE]
    assert reunion["boards"] == [
        "site:candidat.francetravail.fr",
        "site:apec.fr",
        "site:hellowork.com",
    ]
    assert len(reunion["adjacent_roles"]) >= web_plan.ADJACENT_NEEDED
    for dash in ("—", "–", " - "):
        assert dash not in json.dumps(shown, ensure_ascii=False)
        assert dash not in finder.WEB_PROMPT


# --- giving up ---------------------------------------------------------------------------------


class Browser:
    """browserd: web_fetch answers `fetched`; no other page opens."""

    def __init__(self, fetched: dict[str, dict[str, Any]] | None = None) -> None:
        self.fetched = fetched or {}
        self.calls: list[str] = []

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(name)
        if name == "web_fetch" and args["url"] in self.fetched:
            return self.fetched[args["url"]]
        return {"ok": False, "error": "navigation_failed"}

    async def snapshot(self) -> dict[str, Any]:
        return {"ok": False, "error": "no_page"}

    async def wait_for(self, **_: Any) -> dict[str, Any]:
        return {"ok": True}


def web_session(
    places: tuple[str, ...] = ("La Réunion",), browser: Browser | None = None
) -> finder.FinderSession:
    pages = sources.Pages(
        browser or Browser(),
        1000,
        sleep=lambda _: asyncio.sleep(0),
        rng=random.Random(5),  # noqa: S311  a seeded pacing, not a secret
        monotonic=lambda: 0,
    )
    scope = finder.InteractiveScope(
        finder.WEB_START, (TITLE,), places, (), profile=PROFILE, web=True
    )
    session = finder.FinderSession(pages, finder.WEB_START, "", "u1", interactive=scope)
    session.web_search_on = True
    return session


def search(session: finder.FinderSession, *queries: str) -> None:
    """WebSearch as the runner's gate asks the hook, then its answer (never an engine)."""
    run_id = uuid.uuid4()
    with finder._LOCK:
        finder._SESSIONS[run_id] = session
    try:
        for query in queries:
            assert finder.web_search_hook(run_id, {"query": query}) is None
            finder.WEB_FINDER.web_search_answered(run_id, False)
    finally:
        with finder._LOCK:
            finder._SESSIONS.pop(run_id, None)


COVERING = (
    "développeur OR data OR intelligence artificielle La Réunion Saint-Denis CDI",
    "ingénieur IA OR data scientist La Réunion CDI",
    "site:candidat.francetravail.fr data scientist 974",
    "site:apec.fr développeur La Réunion",
    "site:hellowork.com intelligence artificielle La Réunion",
)


def test_production_give_up_is_refused_with_what_is_left_then_accepted() -> None:
    session = web_session()
    # The sweep of 2026-11-18: one narrow query, then no_match.
    search(session, "ingénieur IA générative CDI La Réunion")
    said, error = session.report_none("no_match")
    assert error
    assert session.outcome == ""
    assert said.startswith("Refused: do not give up yet")
    assert "site:candidat.francetravail.fr, site:apec.fr, site:hellowork.com" in said
    assert "3 more roles next to the profile" in said
    assert '"ingénieur IA"' not in said  # the title is searched
    search(session, *COVERING[:-1])
    said, error = session.report_none("no_match")
    assert error
    assert "site:hellowork.com" in said
    assert "site:apec.fr" not in said
    search(session, COVERING[-1])
    assert session.report_none("no_match") == (
        "Noted. Report any posting you opened, then stop.",
        False,
    )
    assert session.outcome == "no_match"


def test_every_place_needs_its_own_searches() -> None:
    session = web_session(places=("La Réunion", "Tahiti"))
    search(session, *COVERING)
    said, error = session.report_none("no_match")
    assert error
    assert "for Tahiti:" in said
    assert "for La Réunion" not in said
    search(session, *(q.replace("La Réunion", "Tahiti").replace("974", "Papeete")
                      for q in COVERING))  # fmt: skip
    assert session.report_none("no_match")[1] is False


@pytest.mark.parametrize("reason", ["security_check", "app_refused", "no_search_control"])
def test_a_block_or_a_refusal_never_spares_the_plan(reason: str) -> None:
    session = web_session()
    session.asked = 1
    session.refused["unsupported_interaction"] = 1
    search(session, "ingénieur IA La Réunion")
    assert session.report_none(reason)[1] is True


def test_web_search_not_offered_or_failing_lifts_the_plan() -> None:
    session = web_session()
    session.web_search_on = False
    assert session.report_none("no_match")[1] is False
    session = web_session()
    session.web_search_answered(True)
    assert session.report_none("no_match")[1] is True  # one error is not a failing tool
    session.web_search_answered(False)
    session.web_search_answered(True)
    assert session.report_none("no_match")[1] is True  # not in a row
    session.web_search_answered(True)
    assert session.report_none("no_match")[1] is False


def test_routes_left_names_the_web_search_until_the_plan_is_searched() -> None:
    session = web_session()
    assert session.routes_left("no_match") == ["web_search"]
    search(session, *COVERING)
    assert session.routes_left("no_match") == []


# --- dates, and the recorded La Réunion session --------------------------------------------------


def fetched(url: str, snapshot: str) -> dict[str, Any]:
    return {"ok": True, "url": url, "status": 200, "title": "Offre", "snapshot": snapshot}


NICHE_PAGE = (
    '- heading "Ingénieur IA générative (CDI)" [level=1]\n'
    "- paragraph: Example AI Lab, Saint-Denis, La Réunion\n"
    "- paragraph: Publiée il y a un mois\n"
    "- paragraph: Agents, RAG and Python for an invented lab."
)
FRANCE_TRAVAIL_PAGE = (
    '- heading "Data scientist (H/F)" [level=1]\n'
    "- paragraph: 974, Saint-Denis\n"
    "- paragraph: Contrat à durée indéterminée. Invented Example Data builds models."
)


def test_a_recorded_la_reunion_session_stores_the_niche_board_and_france_travail_postings():
    """The session Claude Code ran on 2026-11-18 (scrubbed, invented hosts), replayed as the
    agent's tool calls: the early no_match is refused, the broad and site: searches follow, and
    both postings are kept, the niche board's approximate date flagged, France Travail's
    missing date flagged unknown."""
    browser = Browser(
        {NICHE: fetched(NICHE, NICHE_PAGE), FRANCE_TRAVAIL: fetched(FRANCE_TRAVAIL,
                                                                     FRANCE_TRAVAIL_PAGE)}
    )  # fmt: skip
    session = web_session(browser=browser)
    stored: list[finder.VerifiedFinding] = []

    async def keep(batch: finder.VerifiedBatch) -> None:
        stored.extend(batch.findings)

    session.on_verified = keep

    async def replay() -> dict[str, Any]:
        search(session, "ingénieur IA générative CDI La Réunion")
        early = text_of(await tool(session, "report_no_postings", reason="no_match"))
        search(session, *COVERING, "site:welcometothejungle.com data La Réunion")
        await tool(session, "web_fetch", url=NICHE)
        await tool(session, "web_fetch", url=FRANCE_TRAVAIL)
        postings = [
            {
                "title": "Ingénieur IA générative (CDI)",
                "url": NICHE,
                "text": "Agents, RAG and Python.",
                "location": "Saint-Denis, La Réunion",
                "date_shown": "il y a un mois",
            },
            {
                "title": "Data scientist (H/F)",
                "url": FRANCE_TRAVAIL,
                "text": "Invented Example Data builds models.",
                "date_shown": "il y a 2 jours",  # not on the page: dropped, posting kept
            },
        ]
        report = json.loads(text_of(await tool(session, "report_postings", postings=postings)))
        return {"early": early, "report": report}

    seen = asyncio.run(replay())
    assert seen["early"].startswith("Refused: do not give up yet")
    assert seen["report"]["kept"] == 2
    assert [f.url for f in stored] == [NICHE, FRANCE_TRAVAIL]
    niche, france_travail = stored
    assert niche.published_on is None
    assert niche.evidence["date_shown"] == "il y a un mois"
    assert sweep.discovery_date(niche) == "approximate"
    assert "date_shown" not in france_travail.evidence
    assert sweep.discovery_date(france_travail) == "unknown"
    assert browser.calls == ["web_fetch", "web_fetch"]


@pytest.mark.parametrize(
    ("value", "kept"),
    [
        ("il y a un mois", "il y a un mois"),
        ("  il  y a un   mois ", "il y a un mois"),
        ("IL Y A UN MOIS", "IL Y A UN MOIS"),
        ("il y a deux mois", None),  # not on the page
        ("", None),
        (None, None),
        (42, None),
        ("x" * 101, None),
        ("il y a <b>un</b> mois", None),
        ("il y a un mois\x00", None),
    ],
)
def test_the_date_shown_is_kept_only_as_the_page_words_it(value: object, kept) -> None:
    session = web_session(browser=Browser({NICHE: fetched(NICHE, NICHE_PAGE)}))
    asyncio.run(session.web_fetch(NICHE))
    page = session.pages_read[NICHE]
    assert finder.date_shown(value, page) == kept


def test_an_exact_date_is_exact() -> None:
    finding = SimpleNamespace(published_on="2026-11-02", evidence={"date_shown": "x"})
    assert sweep.discovery_date(finding) == "exact"


# --- the recorded La Réunion session, replayed by the fake Claude Code ---------------------------


def said(n: int, *calls: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    """One assistant message of the recorded session: its tool calls."""
    return {
        "message": {
            "type": "assistant",
            "session_id": "{session_id}",
            "uuid": f"00000000-0000-4000-8000-{n:012d}",
            "parent_tool_use_id": None,
            "message": {
                "id": f"msg_j23_{n}",
                "type": "message",
                "role": "assistant",
                "model": "{model}",
                "content": [
                    {"type": "tool_use", "id": f"toolu_j23_{n}_{i}", "name": name, "input": args}
                    for i, (name, args) in enumerate(calls)
                ]
                or [{"type": "text", "text": "Two postings reported."}],
                "stop_reason": "tool_use" if calls else "end_turn",
                "usage": {"input_tokens": 3000, "output_tokens": 40},
            },
        }
    }


def reunion_stream() -> dict[str, Any]:
    """Claude Code's session of 2026-11-18 for La Réunion (scrubbed: invented hosts and
    companies), opened by the production run's own give up after one narrow query."""
    app = "mcp__app__"
    postings = [
        {
            "title": "Ingénieur IA générative (CDI)",
            "url": NICHE,
            "text": "Agents, RAG and Python.",
            "location": "Saint-Denis, La Réunion",
            "date_shown": "il y a un mois",
        },
        {"title": "Data scientist (H/F)", "url": FRANCE_TRAVAIL, "text": "Builds models."},
    ]
    steps = [
        {
            "message": {
                "type": "system",
                "subtype": "init",
                "session_id": "{session_id}",
                "uuid": "00000000-0000-4000-8000-000000000001",
                "model": "{model}",
                "tools": ["WebSearch", *(app + n for n in finder_tools_names())],
                "mcp_servers": [{"name": "app", "status": "connected"}],
            }
        },
        said(1, ("WebSearch", {"query": "ingénieur IA générative CDI La Réunion"})),
        said(2, (app + "report_no_postings", {"reason": "no_match"})),
        said(3, *(("WebSearch", {"query": q}) for q in COVERING)),
        said(4, (app + "web_fetch", {"url": NICHE}), (app + "web_fetch", {"url": FRANCE_TRAVAIL})),
        said(5, (app + "report_postings", {"postings": postings})),
        said(6),
        {
            "message": {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "duration_ms": 3000,
                "duration_api_ms": 2500,
                "num_turns": 6,
                "session_id": "{session_id}",
                "uuid": "00000000-0000-4000-8000-0000000009ff",
                "total_cost_usd": 0.0,
                "usage": {"input_tokens": 9000, "output_tokens": 120},
                "result": "Two postings reported.",
            }
        },
    ]
    return {
        "description": "J23 test: the recorded La Réunion session.",
        "web_search": [
            {"title": "Ingénieur IA générative", "url": NICHE},
            {"title": "Data scientist (H/F), 974", "url": FRANCE_TRAVAIL},
        ],
        "steps": steps,
    }


def finder_tools_names() -> list[str]:
    return ["web_fetch", "browser_navigate", "browser_snapshot", "report_postings",
            "report_no_postings"]  # fmt: skip


def test_the_recorded_la_reunion_session_is_stored_through_the_runner(
    runtime: Runtime,  # noqa: F811
    alice,  # noqa: F811
    monkeypatch,
    tmp_path,
) -> None:
    (tmp_path / "web_discovery.json").write_text(json.dumps(reunion_stream()), encoding="utf-8")
    monkeypatch.setattr(fake_backend, "STREAMS", tmp_path)
    runtime.give_key(alice)
    with runtime.factory() as database:
        row = Sweep(user_id=alice, trigger=SweepTrigger.manual, stats={})
        database.add(row)
        database.commit()
        sweep_id = row.id
    with runtime.factory() as database:
        known = sweep.Known(database, alice)
    stats = sweep.Stats()
    plan_ = sweep.Plan(sweep_id=sweep_id, user_id=alice, rules=[], skills=None, known=known)
    browser = Browser(
        {NICHE: fetched(NICHE, NICHE_PAGE), FRANCE_TRAVAIL: fetched(FRANCE_TRAVAIL,
                                                                     FRANCE_TRAVAIL_PAGE)}
    )  # fmt: skip
    session = web_session(browser=browser)
    session.user_id = str(alice)

    async def run() -> None:
        commits = sweep.Commits(runtime.factory, plan_, stats, lambda: datetime.now(UTC))
        commits.admit("web")

        async def store(batch: finder.VerifiedBatch) -> None:
            await commits.save("web", finder.WEB_NAME, batch)

        session.on_verified = store
        await finder.RuntimeFinder(lambda: runtime.context())(session)

    asyncio.run(run())
    [run_row] = runs_of(runtime.factory, alice)
    assert run_row.status == RunStatus.done, run_row.error
    assert session.web_searches == 1 + len(COVERING)
    assert session.outcome == ""  # the early give up was refused, then postings came
    with runtime.factory() as database:
        jobs = {
            job.url: job
            for job in database.scalars(sa.select(Job).where(Job.user_id == alice)).all()
        }
        dates = {
            url: [
                data["discovery"]["date"]
                for data in database.scalars(
                    sa.select(JobEvent.data).where(JobEvent.job_id == job.id)
                )
                if isinstance(data, dict) and "discovery" in data
            ]
            for url, job in jobs.items()
        }
    assert sorted(jobs) == sorted([NICHE, FRANCE_TRAVAIL])
    assert dates == {NICHE: ["approximate"], FRANCE_TRAVAIL: ["unknown"]}
