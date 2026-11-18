"""Jobs J20 (#793): LinkedIn's public job search finds what a person would find, signed in or not
(owner, 2026-11-17: "It must work, even if i'm not logged in"). The title rule moved after
reading, places sent as LinkedIn names them and the answers judged against the saved places, a
budget that covers 12 titles in 3 places, and the fallback of the browser search.

Fakes only: the LinkedIn guest fake of test_linkedin_guest (an httpx.MockTransport), here taught
to answer as LinkedIn did on 2026-11-17: a location it does not know is read as none, and the
postings it answers are of elsewhere ("Miami, FL"). No test reaches LinkedIn."""

import asyncio
import zlib
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest

from app.agents import activity_steps
from app.browser import finder
from app.browser.sources import linkedin_guest as guest
from app.browser.sources import linkedin_places as places
from app.jobs import sweep
from tests.browser.test_linkedin_guest import (
    CUSTOM,
    LINKEDIN,
    LinkedInFake,
    answer,
    card,
    execute,
    section,
)

# --- the alias table -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("saved", "sent"),
    [
        # Overseas France: the territory LinkedIn knows.
        ("Tahiti", "French Polynesia"),
        ("tahiti", "French Polynesia"),
        ("  TAHITI ", "French Polynesia"),
        ("Papeete", "French Polynesia"),
        ("Moorea", "French Polynesia"),
        ("Bora-Bora", "French Polynesia"),
        ("Polynésie française", "French Polynesia"),
        ("polynesie francaise", "French Polynesia"),
        ("French Polynesia", "French Polynesia"),
        ("La Réunion", "Reunion"),
        ("la reunion", "Reunion"),
        ("Réunion", "Reunion"),
        ("Île de la Réunion", "Reunion"),
        ("La Réunion (974)", "Reunion"),
        ("974", "Reunion"),
        ("Guyane", "French Guiana"),
        ("Nouvelle-Calédonie", "New Caledonia"),
        # The Paris region.
        ("Région parisienne", "Greater Paris Metropolitan Region"),
        ("Paris et sa région", "Greater Paris Metropolitan Region"),
        ("Grand Paris", "Greater Paris Metropolitan Region"),
        ("Paris area", "Greater Paris Metropolitan Region"),
        ("Île-de-France", "Île-de-France, France"),
        ("ILE DE FRANCE", "Île-de-France, France"),
        ("IDF", "Île-de-France, France"),
        # French names of countries and cities.
        ("Allemagne", "Germany"),
        ("Royaume-Uni", "United Kingdom"),
        ("États-Unis", "United States"),
        ("Suisse", "Switzerland"),
        ("Pays-Bas", "Netherlands"),
        ("Singapour", "Singapore"),
        ("Londres", "London"),
        ("Genève", "Geneva"),
        ("Bruxelles", "Brussels"),
    ],
)
def test_a_saved_place_goes_to_linkedin_as_the_name_linkedin_knows(saved: str, sent: str) -> None:
    assert places.linkedin_name(saved) == sent


@pytest.mark.parametrize(
    "saved",
    [
        # Places LinkedIn knows as written, and every unknown one: sent as written.
        "Paris",
        "Bordeaux",
        "Guadeloupe",
        "Martinique",
        "Mayotte",
        "Lyon, France",
        "New York",
        "remote",
        "Télétravail",
        # The whole text must be the alias: never a part of a longer place, never a near miss.
        "Tahiti, Papeete",
        "Tahitian Village",
        "Reunion Tower, Dallas",
        "Paris, Île-de-France",
        "Grand Paris Sud",
        "9740",
        "Suisse romande",
        "Saint-Denis",
        "",
        "x" * 500,
    ],
)
def test_any_other_place_is_sent_as_written(saved: str) -> None:
    assert places.linkedin_name(saved) == saved


def test_two_saved_places_linkedin_names_the_same_are_one_search() -> None:
    queries = guest.queries_for(section(location={"terms": ["Tahiti", "Polynésie française"]}))
    assert [(q.location, q.place) for q in queries] == [("French Polynesia", "Tahiti")]


# --- the saved places judge what LinkedIn answers ------------------------------------------------


def wanted(saved: list[str], stance: str | None = None) -> places.WantedPlaces:
    return places.WantedPlaces(saved, stance, finder.worldwide)


OVERSEAS = ["Bora Bora", "Guadeloupe", "La Réunion"]


@pytest.mark.parametrize(
    ("saved", "location", "kept"),
    [
        # The sweep of 2026-11-17: three overseas places, postings of Miami.
        (OVERSEAS, "Miami, FL", False),
        (OVERSEAS, "Miami-Fort Lauderdale Area", False),
        (OVERSEAS, "Papeete, Windward Islands, French Polynesia", True),
        (OVERSEAS, "French Polynesia", True),
        (OVERSEAS, "Saint-Denis, Réunion", True),
        (OVERSEAS, "Reunion", True),
        (OVERSEAS, "Pointe-à-Pitre, Guadeloupe", True),
        (OVERSEAS, "Paris, Île-de-France, France", False),
        (OVERSEAS, "Remote", False),  # remote is not wanted
        # The gazetteer's places: Paris and its suburbs, a country and its towns that name it.
        (["Paris"], "Paris, Île-de-France, France", True),
        (["Paris"], "Courbevoie, Île-de-France, France", True),
        (["Paris"], "La Défense, Île-de-France, France", True),
        (["Paris"], "Lyon, Auvergne-Rhône-Alpes, France", False),
        (["Paris"], "Parisot, Occitanie, France", False),  # a name inside a longer one
        (["France"], "Lyon, Auvergne-Rhône-Alpes, France", True),
        (["France"], "Geneva, Switzerland", False),
        (["Île-de-France"], "Courbevoie, Île-de-France, France", True),
        (["Île-de-France"], "Lyon, Auvergne-Rhône-Alpes, France", False),  # not France itself
        (["Région parisienne"], "Greater Paris Metropolitan Region", True),
        # A Paris region alias covers Paris with its suburbs and the other name of the region
        # (review 1 of #796), never the rest of France.
        (["Région parisienne"], "Paris, Île-de-France, France", True),
        (["Région parisienne"], "Courbevoie, Île-de-France, France", True),
        (["Région parisienne"], "Nanterre, Île-de-France, France", True),
        (["Région parisienne"], "Boulogne-Billancourt, Île-de-France, France", True),
        (["Région parisienne"], "Île-de-France, France", True),
        (["Région parisienne"], "Paris", True),
        (["Grand Paris"], "Paris, Île-de-France, France", True),
        (["Grand Paris"], "Greater Paris Metropolitan Region", True),
        (["Paris area"], "La Défense, Île-de-France, France", True),
        (["agglomération parisienne"], "Île-de-France, France", True),
        (["Île-de-France"], "Greater Paris Metropolitan Region", True),
        (["Île-de-France"], "Paris, Île-de-France, France", True),
        (["IDF"], "La Défense, Île-de-France, France", True),
        (["IDF"], "Greater Paris Metropolitan Region", True),
        (["ile de france"], "Puteaux, Île-de-France, France", True),
        (["Région parisienne"], "Lyon, Auvergne-Rhône-Alpes, France", False),
        (["Région parisienne"], "France", False),
        (["Région parisienne"], "Miami, FL", False),
        (["Région parisienne"], "Parisot, Occitanie, France", False),
        (["Région parisienne"], "Paris, TX", False),
        (["Grand Paris"], "Bordeaux, Nouvelle-Aquitaine, France", False),
        (["IDF"], "Lyon, Auvergne-Rhône-Alpes, France", False),
        (["Île-de-France"], "Marseille, Provence-Alpes-Côte d'Azur, France", False),
        # The last part of a location names its country: a known saved place of another
        # country is not it (review 1 of #796), whatever words come before.
        (["La Réunion"], "Reunion Tower, Dallas, TX", False),
        (["La Réunion"], "Reunion, Dallas, Texas", False),
        (["La Réunion"], "Reunion Park, Ontario, Canada", False),
        (["La Réunion"], "Saint-Pierre, Réunion", True),
        (["La Réunion"], "Saint-Denis, Réunion, France", True),
        (["La Réunion"], "Saint-Denis, Île-de-France, France", False),
        (["Tahiti"], "Papeete, Windward Islands, French Polynesia", True),
        (["Tahiti"], "French Polynesia", True),
        (["La Réunion"], "Reunion", True),
        (["Guadeloupe"], "Pointe-à-Pitre, Guadeloupe", True),
        (["Guadeloupe"], "Les Abymes, Pointe-à-Pitre, Guadeloupe", True),
        (["Guadeloupe"], "Miami, FL", False),
        (["Tahiti"], "Tahiti Village, Las Vegas, NV", False),
        (["Paris"], "Paris, TX", False),
        (["Paris"], "Paris, Texas, United States", False),
        (["Paris"], "Paris, Ontario, Canada", False),
        (["Paris"], "Paris, ON", False),
        (["Paris"], "Paris, Île-de-France, France (Hybrid)", True),
        (["Paris"], "Greater Paris Metropolitan Region", True),
        (["Paris"], "Paris", True),
        (["Londres"], "London, Ontario, Canada", False),
        (["Londres"], "London, England, United Kingdom", True),
        (["London"], "London, ON", False),
        (["London"], "London, England", True),
        (["Bruxelles"], "Brussels, Brussels Region, Belgium", True),
        (["Bruxelles"], "Brussels, WI", False),
        (["Barcelone"], "Barcelona, Catalonia, Spain", True),
        (["Barcelone"], "Barcelona, Anzoátegui, Venezuela", True),  # unknown country: words
        (["Dallas"], "Dallas, TX", True),
        (["Dallas"], "Dallas, Texas, United States", True),
        (["Dallas"], "Dallas, Scotland, United Kingdom", False),
        (
            ["Allemagne"],
            "Berlin, DE",
            False,
        ),  # capitals are a US state: LinkedIn writes no ISO code
        # A saved country matches every location whose last part names it.
        (["USA"], "New York, NY", True),
        (["États-Unis"], "Austin, Texas", True),
        (["United States"], "Toronto, ON", False),
        (["France"], "Paris, TX", False),
        (["Canada"], "Montréal, QC", True),
        (["Allemagne"], "Berlin, Germany", True),
        (["Allemagne"], "Allemagne", True),
        (["Londres"], "London Area, United Kingdom", True),
        # Nothing saved, or a place that says no limit: every place is kept.
        ([], "Miami, FL", True),
        (["everywhere"], "Miami, FL", True),
        (["Paris", "monde entier"], "Miami, FL", True),
        # An empty place says nothing: the posting reader and scoring judge it.
        (OVERSEAS, "", True),
        (OVERSEAS, "   ", True),
    ],
)
def test_a_posting_of_a_place_none_of_the_saved_ones_is_not_kept(
    saved: list[str], location: str, kept: bool
) -> None:
    assert wanted(saved).matches(location) is kept


@pytest.mark.parametrize(
    ("saved", "stance", "location", "title", "kept"),
    [
        (["Paris", "remote"], None, "Remote", "AI Engineer", True),
        (["Paris", "Télétravail"], None, "France (Remote)", "AI Engineer", True),
        (["Paris", "Full remote"], None, "Miami, FL", "AI Engineer (Remote)", True),
        (["Paris"], "remote", "United States (Remote)", "AI Engineer", True),
        (["Paris"], "hybrid", "United States (Remote)", "AI Engineer", False),
        (["Paris"], None, "Remote", "AI Engineer", False),
        (["Paris", "remote"], None, "Miami, FL", "AI Engineer", False),
        (["Paris", "remote"], None, "Remoteville, Texas", "AI Engineer", False),
        (["remote"], None, "Miami, FL", "AI Engineer", False),
        # Only a part that is a work mode as a whole says remote (review 1 of #796): never a
        # word of a longer title.
        (["Paris", "remote"], None, "Miami, FL", "Remote Sensing Scientist", False),
        (["Paris", "remote"], None, "Miami, FL", "Remote Pilot", False),
        (["Paris"], "remote", "Miami, FL", "Remote Sensing Scientist", False),
        (["Paris", "remote"], None, "Miami, FL", "Remote Systems Engineer (Hybrid)", False),
        (["Paris", "remote"], None, "Miami, FL", "Senior Remoteness Analyst", False),
        (["Paris", "remote"], None, "Remote Sensing Lab, Austin, TX", "AI Engineer", False),
        (["Paris", "remote"], None, "Miami, FL", "AI Engineer - Remote", True),
        (["Paris", "remote"], None, "Miami, FL", "AI Engineer, Full remote", True),
        (["Paris", "remote"], None, "Miami, FL", "AI Engineer [100% remote]", True),
        (["Paris", "remote"], None, "Miami, FL", "Ingénieur IA (télétravail)", True),
        (["Paris", "remote"], None, "Miami, FL", "Ingénieur IA | À distance", True),
        (["Paris", "remote"], None, "Remote, US", "AI Engineer", True),
        (["Paris", "remote"], None, "Télétravail", "AI Engineer", True),
        (["Paris", "remote"], None, "Full remote", "AI Engineer", True),
        (["Paris", "remote"], None, "100% remote", "AI Engineer", True),
    ],
)
def test_a_remote_posting_is_kept_when_remote_is_wanted(
    saved: list[str], stance: str | None, location: str, title: str, kept: bool
) -> None:
    assert wanted(saved, stance).matches(location, title) is kept


@pytest.mark.parametrize(
    ("saved", "location", "kept"),
    [
        # A saved place longer than PLACE_MAX characters (review 1 of #796): judged by the places
        # its first PLACE_MAX characters name; naming none, alone it keeps every place.
        (["x" * 150], "Miami, FL", True),
        (["x" * 150], "Paris, Île-de-France, France", True),
        (["Paris, " + "x" * 150], "Paris, Île-de-France, France", True),
        (["Paris, " + "x" * 150], "Lyon, Auvergne-Rhône-Alpes, France", False),
        (["x" * 95 + " Paris"], "Paris, Île-de-France, France", True),  # Paris is cut off
        (["x" * 150, "Bordeaux"], "Bordeaux, Nouvelle-Aquitaine, France", True),
        (["x" * 150, "Bordeaux"], "Miami, FL", False),
        (["x" * 150, "remote"], "Miami, FL", True),
        (["x" * 100], "Miami, FL", False),  # PLACE_MAX itself is read as written
    ],
)
def test_a_saved_place_too_long_to_read_never_drops_every_card(
    saved: list[str], location: str, kept: bool
) -> None:
    assert wanted(saved).matches(location) is kept


def test_a_saved_place_too_long_is_counted_unread() -> None:
    assert wanted(["x" * 150, "Bordeaux"]).unread == 1
    assert wanted(["Paris, " + "x" * 150]).unread == 0


def test_every_gazetteer_city_and_every_covered_place_has_its_country() -> None:
    from app.jobs import gazetteer

    assert {c.name for c in gazetteer.gazetteer().cities} == set(places.CITY_HOME)
    countries = set(places.COUNTRIES.values())
    assert set(places.CITY_HOME.values()) <= countries
    assert set(places.HOME.values()) <= countries
    assert set(places.HOME) <= set(places.ALIASES)
    cities = {c.name for c in gazetteer.gazetteer().cities}
    for name, covered in places.COVERS.items():
        assert name in places.ALIASES
        assert all(c in places.ALIASES or c in cities for c in covered)


@pytest.mark.parametrize(
    ("location", "country"),
    [
        ("Dallas, TX", "United States"),
        ("Dallas, tx", None),  # a code is LinkedIn's capitals only
        ("Paris, Texas, United States", "United States"),
        ("United States (Remote)", "United States"),
        ("Toronto, ON", "Canada"),
        ("Paris, Île-de-France, France", "France"),
        ("London Area, United Kingdom", "United Kingdom"),
        ("London, England", "United Kingdom"),
        ("Saint-Denis, Réunion", "Reunion"),
        ("Papeete, Windward Islands, French Polynesia", "French Polynesia"),
        ("Berlin, Allemagne", "Germany"),
        ("Greater Paris Metropolitan Region", None),
        ("Île-de-France", None),
        ("Miami-Fort Lauderdale Area", None),
        ("EMEA", None),
        ("", None),
    ],
)
def test_the_last_part_of_a_location_names_its_country(location: str, country: str | None) -> None:
    assert places.country_of(location) == country


class PlaceAware(LinkedInFake):
    """LinkedIn as it answered on 2026-11-17: a location of its own list gives postings there, any
    other location is read as none and gives postings of Miami."""

    KNOWN: ClassVar[dict[str, str]] = {
        "French Polynesia": "Papeete, Windward Islands, French Polynesia",
        "Reunion": "Saint-Denis, Réunion",
        "Guadeloupe": "Pointe-à-Pitre, Guadeloupe",
        "Paris": "Paris, Île-de-France, France",
    }

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/seeMoreJobPostings/search"):
            return super().handler(request)
        self.requests.append(request)
        self.times.append(self.now[0])
        params = request.url.params
        where = self.KNOWN.get(params["location"], "Miami, FL")
        first = 4300000000 + (zlib.crc32(where.encode()) % 10_000_000) * 10
        page = "".join(
            card(str(first + n), f"{params['keywords']} {n}", place=where) for n in range(3)
        )
        return answer(200, page)


def test_overseas_places_are_searched_as_linkedin_names_them_and_miami_is_never_read() -> None:
    search = section(location={"terms": ["Tahiti", "Guadeloupe", "La Réunion", "Atlantis"]})
    entries, fake, _, _ = execute(search, PlaceAware)
    [entry] = entries
    sent = [r.url.params["location"] for r in fake.requests if "location" in r.url.params]
    assert sent == ["French Polynesia", "Guadeloupe", "Reunion", "Atlantis"]
    read = [r.url.path.rsplit("/", 1)[1] for r in fake.requests if "location" not in r.url.params]
    assert len(read) == 9  # the three places LinkedIn knows, three postings each
    assert entry["other_place"] == 3  # "Atlantis" answered Miami: never read, never stored
    assert entry["found"] == 9


def test_a_card_without_its_place_is_judged_on_its_posting_page() -> None:
    page = """<li><div class="base-card base-search-card"
      data-entity-urn="urn:li:jobPosting:4300000099">
      <h3 class="base-search-card__title">AI Engineer</h3></div></li>"""
    posting = (
        '<section class="top-card-layout"><h1 class="top-card-layout__title">AI Engineer</h1>'
        '<span class="topcard__flavor topcard__flavor--bullet">Miami, FL</span></section>'
        '<div class="show-more-less-html__markup">About the role</div>'
    )
    search = section(location={"terms": ["Tahiti"]})
    entries, fake, _, _ = execute(
        search, lambda now: LinkedInFake(now, pages={0: page}, postings={"4300000099": posting})
    )
    assert fake.paths() == ["search?start=0", "4300000099"]
    assert guest.parse_posting(posting).location == "Miami, FL"  # type: ignore[union-attr]
    assert (entries[0]["other_place"], entries[0]["found"]) == (1, 0)


# --- the budget ----------------------------------------------------------------------------------

TITLES = [f"Example Role {n}" for n in range(12)]
PLACES = ["Paris", "Lyon", "Bordeaux"]


class FullPages(LinkedInFake):
    """A full page of new cards in Paris for every search."""

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/seeMoreJobPostings/search"):
            return super().handler(request)
        self.requests.append(request)
        self.times.append(self.now[0])
        params = request.url.params
        key = f"{params['keywords']}|{params['location']}|{params['start']}"
        first = 4400000000 + (zlib.crc32(key.encode()) % 10_000_000) * 100
        return answer(
            200, "".join(card(str(first + n), f"Role {n}") for n in range(guest.PAGE_SIZE))
        )


@pytest.mark.parametrize("websites", [0, 2])
def test_twelve_titles_in_three_places_are_all_searched_then_their_new_cards_read(
    websites: int,
) -> None:
    """A sweep at the 900 s of the sources that make no browser call, with websites read in
    the browser ticked or not: every one of the 36 pairs is searched once, then details."""
    others = [{**CUSTOM, "url": f"https://jobs-{n}.example.test/"} for n in range(websites)]
    search = section(
        title_include={"terms": TITLES},
        location={"terms": PLACES},
        location_exclude={"terms": []},
        interactive_sites=[LINKEDIN, *others],
    )
    entries, fake, _, _ = execute(search, FullPages, deadline=900.0)
    entry = next(e for e in entries if e["mode"] == "api")
    firsts = {
        (r.url.params["keywords"], r.url.params["location"])
        for r in fake.requests
        if r.url.params.get("start") == "0"
    }
    assert len(firsts) == 36
    assert (entry["searches"], entry["planned"], entry["not_searched"]) == (36, 36, [])
    details = [r for r in fake.requests if "start" not in r.url.params]
    assert len(details) >= 15
    assert entry["details"] == len(details)
    assert max(fake.times) + guest.REQUEST_S <= 900.0


def test_a_budget_that_ends_early_names_the_pairs_not_searched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(guest, "LINKEDIN_TIME_S", 150.0)  # about 12 requests
    search = section(title_include={"terms": TITLES[:6]}, location={"terms": PLACES})
    entries, _, _, _ = execute(search, FullPages, deadline=900.0)
    [entry] = entries
    assert entry["error"] == "time_budget"
    ran = {(s["title"], s["place"]) for s in entry["searched"]}
    left = [(s["title"], s["place"]) for s in entry["not_searched"]]
    assert entry["planned"] == 18
    assert len(ran) + len(left) == 18
    assert not ran & set(left)
    assert left[-1] == ("Example Role 5", "Bordeaux")  # in saved order, the last pair last
    assert entry["searches"] == len(ran) < 18


def test_pairs_past_the_search_cap_are_named_too() -> None:
    titles = [f"Example Role {n}" for n in range(30)]
    search = section(title_include={"terms": titles}, location={"terms": PLACES})
    assert len(guest.every_query(search)) == 90
    assert len(guest.queries_for(search)) == guest.SEARCHES_MAX
    entries, _, _, _ = execute(search, lambda now: LinkedInFake(now, pages={0: ""}), deadline=1e6)
    [entry] = entries
    assert (entry["searches"], entry["planned"]) == (guest.SEARCHES_MAX, 90)
    assert entry["not_searched"][0] == {"title": "Example Role 24", "place": "Paris"}
    assert len(entry["not_searched"]) == 90 - guest.SEARCHES_MAX


# --- the fallback of the browser search ----------------------------------------------------------


class Logins:
    """The person's sign ins (B17, site_logins): `hosts` signed in; `fails` cannot say."""

    def __init__(self, *hosts: str, fails: bool = False) -> None:
        self.hosts, self.fails, self.asked = set(hosts), fails, []

    async def signed_in(self, user_id: str, host: str) -> bool:
        self.asked.append(host)
        if self.fails:
            raise RuntimeError("store down")
        return host in self.hosts


def browser_search(**fields: Any) -> Any:
    return section(linkedin_search="browser", **fields)


def test_with_no_linkedin_sign_in_the_public_search_runs_in_place_of_the_browser() -> None:
    logins = Logins()
    entries, fake, leases, _ = execute(browser_search(), logins=logins)
    [browser, public] = entries
    assert logins.asked == ["www.linkedin.com"]
    assert leases.acquired == []  # the person's browser is not opened for LinkedIn
    assert (browser["mode"], browser["outcome"], browser["reason"], browser["fallback"]) == (
        "llm_browser",
        "not_attempted",
        "linkedin_signin",
        "guest",
    )
    assert (public["mode"], public["fallback"], public["fallback_after"]) == (
        "api",
        "guest",
        "linkedin_signin",
    )
    assert public["source_id"] == sweep._source_id("api", LINKEDIN["url"])
    assert (public["found"], len(fake.requests)) == (3, 4)


def test_signed_in_the_browser_search_runs_and_its_failure_falls_back_in_the_same_sweep() -> None:
    entries, _, _, _ = execute(browser_search(), logins=Logins("www.linkedin.com"))
    [browser, public] = entries
    # No model credential here: the browser search ends failed (`no_credential`).
    assert (browser["reason"], browser["outcome"], browser["fallback"]) == (
        "no_credential",
        "failed",
        "guest",
    )
    assert (public["fallback_after"], public["found"]) == ("no_credential", 3)
    # A sign in store that cannot say reads as signed in: the browser search runs first.
    entries, _, _, _ = execute(browser_search(), logins=Logins(fails=True))
    assert [e["mode"] for e in entries] == ["llm_browser", "api"]
    assert entries[0]["reason"] == "no_credential"


def test_the_fallback_runs_once_and_takes_the_retry_slot() -> None:
    entries, _, _, _ = execute(browser_search(), logins=Logins("www.linkedin.com"))
    bound = {e["source_id"] for e in entries}
    # Both failed ones are out of B24's retry: the public search took it.
    for entry in entries:
        entry.update(outcome="failed", reason="page_failed", phase="done")
    assert sweep._failed(sweep.Stats(sources=entries), bound) == []
    # A second pass (B24 calls the websites again) does not search LinkedIn a second time.
    stats = sweep.Stats(sources=[dict(e) for e in entries])

    async def progress() -> None:
        return None

    runner = SimpleNamespace(base_url=None)
    plan = SimpleNamespace(search=browser_search(), search_scope=None, commits=None, sweep_id=None)
    asyncio.run(guest.fall_back(runner, plan, stats, progress, 1000.0))  # type: ignore[arg-type]
    assert len(stats.sources) == 2


@pytest.mark.parametrize("how", ["stop", "abandoned"])
def test_a_fallback_that_never_starts_is_never_claimed(
    how: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 1 of #796: `unsigned` sets the browser search aside, then the sweep stops (an API
    source took the browser away) or the run lets go of the row before the public search
    starts: the browser entry says not searched, and no fallback."""
    if how == "stop":

        async def stopped(*args: Any) -> str:
            return "lease_ended"

        monkeypatch.setattr(guest, "run", stopped)
    else:
        monkeypatch.setattr(sweep, "abandoned", lambda plan: True)
    entries, fake, _, _ = execute(browser_search(), logins=Logins())
    [browser] = entries
    assert (browser["outcome"], browser["reason"]) == ("not_attempted", "linkedin_signin")
    assert "fallback" not in browser
    assert fake.requests == []


def test_a_browser_entry_already_searched_here_is_not_searched_twice() -> None:
    """The guard of `fall_back`: a fresh failed browser entry beside an existing public search
    entry of the same address (review 1 of #796, follow up)."""
    entries, _, _, _ = execute(browser_search(), logins=Logins("www.linkedin.com"))
    [browser, public] = entries
    fresh = {k: v for k, v in browser.items() if k != "fallback"}
    stats = sweep.Stats(sources=[fresh, dict(public)])

    async def progress() -> None:
        return None

    runner = SimpleNamespace(base_url=None)
    plan = SimpleNamespace(search=browser_search(), search_scope=None, commits=None, sweep_id=None)
    asyncio.run(guest.fall_back(runner, plan, stats, progress, 1000.0))  # type: ignore[arg-type]
    assert len(stats.sources) == 2
    assert "fallback" not in stats.sources[0]


@pytest.mark.parametrize("reason", ["store_failed", "not_started", "agent_cancelled"])
def test_no_fallback_after_the_run_stopped_its_sources_or_the_person_stopped_the_search(
    reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ended(*args: Any) -> str:
        return reason

    from app.browser.sources import interactive

    monkeypatch.setattr(interactive, "_one", ended)
    entries, fake, _, _ = execute(browser_search(), logins=Logins("www.linkedin.com"))
    assert [e["mode"] for e in entries] == ["llm_browser"]
    assert "fallback" not in entries[0]
    assert fake.requests == []


def test_api_mode_has_no_fallback_entry() -> None:
    entries, _, _, _ = execute(section(), logins=Logins())
    [entry] = entries
    assert "fallback" not in entry


@pytest.mark.parametrize(
    ("after", "said"),
    [
        ("linkedin_signin", "LinkedIn's public job search, as you are not signed in to LinkedIn"),
        ("search_refused", "LinkedIn's public job search, as the browser search failed"),
    ],
)
def test_activity_shows_the_fallback(after: str, said: str) -> None:
    source = {"mode": "api", "found": 4, "fallback": "guest", "fallback_after": after}
    assert activity_steps._source_note(source) == f"4 found, {said}"
    assert activity_steps._source_note({"mode": "api", "found": 4}) == "4 found"


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        (
            {
                "not_searched": [{"title": "AI Engineer", "place": "Lyon"}, {"title": "ML"}],
                "other_place": 3,
                "fallback": "guest",
                "fallback_after": "linkedin_signin",
            },
            ([("AI Engineer", "Lyon"), ("ML", None)], 3, "guest", "linkedin_signin"),
        ),
        ({}, ([], 0, None, None)),  # an older row
        (
            {"not_searched": "Lyon", "other_place": -2, "fallback": 1, "fallback_after": True},
            ([], 0, None, None),
        ),
    ],
)
def test_the_summary_shows_the_pairs_not_searched_other_places_and_the_fallback(
    stored: dict[str, Any], shown: tuple[Any, ...]
) -> None:
    from app.jobs import sweep_router
    from tests.browser.test_multi_search import row

    entry = {"kind": "linkedin", "query": "", "mode": "api", "found": 1, **stored}
    [source] = sweep_router.sweep_out(row([entry])).sources
    left = [(one.title, one.place) for one in source.not_searched]
    assert (left, source.other_place, source.fallback, source.fallback_after) == shown
