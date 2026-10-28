"""The starter list of boards and sources (opportunities O5b): the catalog, the matching of the
person's words, the proposal and the routes.

Real Postgres for the routes. No test reads a real board: the routes use the stub catalog (the
fake ATS server's orgs, invented companies), and the live check runs on a fake transport.
Accounts are invented (e2e-*@example.org).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from app.profile.boards import BoardChecker
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.identity.models import Role, User
from app.opportunities import starter, starter_check
from app.profile.models import Profile
from app.profile.schemas import SearchSection, validate_section
from tests.identity.api_helpers import Api, make_user
from tests.jobs.sweep_helpers import board, set_profile

BASE = "/api/opportunities/starter"
STUB = starter.load(starter.STUB_CATALOG_PATH)
REAL = starter.load(starter.CATALOG_PATH)
CYBER_WORDS = ("cyber", "security", "pentest", "threat", "secure")
# The stub list of a person looking for AI engineer roles in Paris, in its order: the browser
# sources first (always proposed), then the boards.
A_LIST = [
    "source:vie",
    "source:google_careers",
    "lever:fake-404",
    "ashby:fake-ok",
    "lever:fake-unicode",
    "greenhouse:fake-ok2",
]


def section(**values: Any) -> SearchSection:
    data: dict[str, Any] = {}
    for name in ("title_include", "location", "industries"):
        if name in values:
            data[name] = {
                "terms": values.pop(name),
                "advanced_regex": values.pop(f"{name}_re", False),
            }
    data.update(values)
    return SearchSection.model_validate(data)


def keys(found: starter.Proposal) -> list[str]:
    return [entry.key for entry in found.entries]


def boards(found: starter.Proposal) -> list[str]:
    return [entry.key for entry in found.entries if entry.kind == "board"]


# --- the catalog --------------------------------------------------------------------------------


def test_the_shipped_catalog_loads_and_keeps_its_rules() -> None:
    assert REAL.version >= 1
    assert REAL.checked_on == "2026-10-20"
    assert len(REAL.boards) >= 30
    assert sum(item.general for item in REAL.boards) >= 6
    for item in (*REAL.boards, *REAL.sources):
        folded = f"{item.company} {' '.join(item.industries)}".casefold()
        assert not any(word in folded for word in CYBER_WORDS), item.company
    assert {source.kind for source in REAL.sources} == {"google_careers", "vie"}
    # never the owner's personal data: only public company slugs and names
    text = starter.CATALOG_PATH.read_text("utf-8")
    assert "@" not in text
    assert "http" not in text


def test_the_stub_catalog_names_only_the_fake_ats_orgs() -> None:
    assert all(item.org.startswith("fake-") for item in STUB.boards)
    assert {item.company for item in STUB.boards} >= {"Example Robotics", "Sample Analytics"}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda c: c["boards"].append({**c["boards"][0]}), "listed twice"),
        (
            lambda c: c["boards"].append(
                {"company": "Example Cyber Shield", "ats": "ashby", "org": "x1",
                 "industries": ["software"]}
            ),
            "never a cybersecurity company",
        ),
        (
            lambda c: c["boards"].append(
                {"company": "Example Pentest", "ats": "ashby", "org": "x2",
                 "industries": ["software"]}
            ),
            "never a cybersecurity company",
        ),
        (lambda c: c["boards"][0].update(industries=["security"]), "industries"),
        (lambda c: c["boards"][0].update(ats="workday"), "ats"),
        (lambda c: c["boards"][0].update(org="../x"), "org"),
        (lambda c: c["boards"][0].update(extra=True), "extra"),
        (lambda c: c["sources"].append({"company": "LinkedIn", "kind": "linkedin",
                                        "industries": ["software"]}), "kind"),
        (lambda c: c["sources"].append({"company": "A site", "kind": "site",
                                        "industries": ["software"]}), "kind"),
        (lambda c: c.update(checked_on="yesterday"), "checked_on"),
        (lambda c: c["boards"][0].update(industries=[]), "industries"),
    ],
)  # fmt: skip
def test_a_catalog_that_breaks_a_rule_is_refused(tmp_path: Path, change: Any, message: str) -> None:
    data = json.loads(starter.STUB_CATALOG_PATH.read_text("utf-8"))
    change(data)
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data), "utf-8")
    with pytest.raises(starter.CatalogError, match=message):
        starter.load(path)
    with pytest.raises(starter.CatalogError):
        starter.load(tmp_path / "missing.json")


@pytest.mark.parametrize(
    ("base_url", "prefix", "stub"),
    [
        (None, None, False),
        ("http://fakeats:8099", None, True),
        ("http://fakeats:8099", "fake-", False),
    ],
)
def test_the_stub_catalog_only_when_every_ats_call_goes_to_a_stub(
    base_url: str | None, prefix: str | None, stub: bool
) -> None:
    class Settings:
        ats_base_url = base_url
        ats_base_url_org_prefix = prefix

    assert starter.uses_stub(Settings()) is stub
    assert starter.catalog_for(Settings()) == (STUB if stub else REAL)
    assert starter.catalog_for(None) == REAL


# --- the person's words -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("term", "families"),
    [
        ("AI engineer", {"ai", "engineering"}),
        ("Applied AI Engineer", {"ai", "engineering"}),
        ("ai engineers", {"ai", "engineering"}),
        ("Forward Deployed Engineer", {"ai", "engineering"}),
        ("Software developer", {"engineering"}),
        ("Développeur backend", {"engineering"}),
        ("Ingénieur logiciel", {"engineering"}),
        ("Full-stack engineer", {"engineering"}),
        ("full stack", {"engineering"}),
        ("DevOps", {"engineering"}),
        ("Machine learning engineer", {"ai", "engineering"}),
        ("Ingénieur IA", {"ai", "engineering"}),
        ("LLM engineer", {"ai", "engineering"}),
        ("GenAI", {"ai"}),
        ("Data scientist", {"data"}),
        ("Data analyst", {"data"}),
        ("Product manager", {"product"}),
        ("Product designer", {"product", "design"}),
        ("UX designer", {"design"}),
        ("Solutions engineer", {"sales", "engineering"}),
        ("Account executive", {"sales"}),
        ("Customer success manager", {"sales"}),
        ("Growth marketing", {"marketing"}),
        ("Chef de projet", {"operations"}),
        ("Recruiter", {"people"}),
        ("Comptable", {"finance"}),
        ("Nurse", set()),
        ("Chef cuisinier", set()),
        ("Electrician", set()),
        ("", set()),
        ("maintainer", set()),  # "ai" inside a word is no match
        ("Plombier", set()),
        ("Said", set()),
    ],
)
def test_role_words_name_role_families(term: str, families: set[str]) -> None:
    found, unmatched = starter.role_families([term])
    assert found == families
    assert unmatched == ([] if families else [term])


@pytest.mark.parametrize(
    ("term", "names"),
    [
        ("AI", {"ai"}),
        ("Artificial intelligence", {"ai"}),
        ("Intelligence artificielle", {"ai"}),
        ("Fintech", {"fintech"}),
        ("Banking", {"fintech"}),
        ("Banque", {"fintech"}),
        ("Assurance", {"fintech"}),
        ("Healthcare", {"health"}),
        ("Santé", {"health"}),
        ("E-commerce", {"marketplace"}),
        ("ecommerce", {"marketplace"}),
        ("Legal tech", {"legal"}),
        ("LegalTech", {"legal"}),
        ("SaaS", {"software"}),
        ("Developer tools", {"developer_tools"}),
        ("Cloud infrastructure", {"developer_tools"}),
        ("Data", {"data"}),
        ("Music", {"media"}),
        ("Jeux vidéo", {"media"}),
        ("Mobility", {"mobility"}),
        ("EdTech", {"education"}),
        ("Agriculture", set()),
        ("Mining", set()),
        ("Construction", set()),
        ("Paint", set()),  # "ai" inside a word is no match
    ],
)
def test_industry_words_name_catalog_industries(term: str, names: set[str]) -> None:
    assert starter.industries_of([term])[0] == names


@pytest.mark.parametrize(
    ("terms", "countries"),
    [
        (["Paris"], {"FR"}),
        (["Bordeaux, France"], {"FR"}),
        (["Île-de-France"], {"FR"}),
        (["London"], {"GB"}),
        (["Remote, UK"], {"GB"}),
        (["Zürich"], {"CH"}),
        (["Genève"], {"CH"}),
        (["New York"], {"US"}),
        (["Remote, US"], {"US"}),
        (["Remote"], None),
        (["Full remote"], None),
        (["Austin"], {"US"}),
        (["Europe"], set(starter.EUROPE)),
        (["EMEA"], set(starter.EUROPE)),
        (["Anywhere"], None),
        (["Paris", "Remote"], None),
        (["Atlantis"], set()),
        (["Business district"], set()),  # "us" inside a word is no match
        (["Lisbonne"], {"PT"}),
        ([], set()),
    ],
)
def test_places_name_countries(terms: list[str], countries: set[str] | None) -> None:
    assert starter.countries_of(terms)[0] == countries


# --- the proposal -------------------------------------------------------------------------------


def test_no_target_gives_the_small_general_list() -> None:
    found = starter.propose(REAL, section(), [])
    assert found.targets is False
    assert 1 <= len(found.entries) <= starter.GENERAL_MAX
    general = {item.key for item in REAL.boards if item.general}
    assert {e.key for e in found.entries if e.kind == "board"} <= general
    sources = {e.key: e for e in found.entries if e.kind == "source"}
    assert set(sources) == {"source:google_careers", "source:vie"}  # always proposed
    google = sources["source:google_careers"]
    assert (google.query, google.location) == (starter.DEFAULT_QUERY, None)
    assert google.why == "Searches every job (no role given)"
    assert found.unmatched == []


def test_targets_rank_industries_then_roles_then_places() -> None:
    found = starter.propose(
        STUB, section(title_include=["AI engineer"], location=["Paris"], industries=["Data"]), []
    )
    # a tie on every count is broken by the name
    assert keys(found) == ["source:vie", "source:google_careers", "greenhouse:fake-ok2"]
    assert found.entries[2].why == "Data, France"
    google = found.entries[1]
    assert (google.source, google.query, google.location) == (
        "google_careers",
        "AI engineer",
        "Paris",
    )
    assert google.why == 'Searches "AI engineer" in Paris'
    assert google.ats is None
    assert google.org is None


def test_roles_and_places_without_industries() -> None:
    found = starter.propose(STUB, section(title_include=["AI engineer"], location=["Paris"]), [])
    assert keys(found) == A_LIST  # Invented Travel hires in Spain only
    assert {e.key: e.why for e in found.entries}["ashby:fake-ok"] == (
        "Artificial intelligence, France"
    )


def test_a_place_filters_and_anywhere_does_not() -> None:
    spain = starter.propose(STUB, section(industries=["Travel"], location=["Madrid"]), [])
    assert boards(spain) == ["greenhouse:fake-dup"]
    london = starter.propose(STUB, section(industries=["Travel"], location=["London"]), [])
    assert boards(london) == []
    anywhere = starter.propose(STUB, section(industries=["Travel"], location=["Anywhere"]), [])
    assert boards(anywhere) == ["greenhouse:fake-dup"]
    unknown = starter.propose(STUB, section(industries=["Travel"], location=["Atlantis"]), [])
    assert boards(unknown) == ["greenhouse:fake-dup"]


@pytest.mark.parametrize(
    "values",
    [
        {"title_include": ["Nurse"]},
        {"title_include": ["Nurse", "Midwife"], "location": ["Paris"]},
        {"industries": ["Agriculture"]},
        {"title_include": ["AI engineer"], "industries": ["Agriculture"]},
    ],
)
def test_targets_matching_nothing_propose_no_board_and_say_which(values: dict[str, Any]) -> None:
    found = starter.propose(REAL, section(**values), [])
    assert found.targets is True
    assert boards(found) == []
    assert keys(found) == ["source:vie", "source:google_careers"]  # still proposed to everyone
    expected = [t for t in values.get("title_include", []) if t != "AI engineer"]
    expected += values.get("industries", [])
    assert found.unmatched == expected


def test_a_known_word_beside_an_unknown_one_still_proposes() -> None:
    found = starter.propose(STUB, section(title_include=["Nurse", "AI engineer"]), [])
    assert "ashby:fake-ok" in keys(found)
    assert found.unmatched == []


def test_a_board_already_in_the_search_is_shown_as_added() -> None:
    search = section(title_include=["AI engineer"], boards=[board("fake-ok", company="Mine")])
    found = starter.propose(STUB, search, [])
    added = {entry.key: entry.added for entry in found.entries}
    assert added["ashby:fake-ok"] is True
    assert added["greenhouse:fake-ok2"] is False
    # the same org typed in capitals is the same board
    caps = section(title_include=["AI engineer"], boards=[board("FAKE-OK")])
    assert {e.key: e.added for e in starter.propose(STUB, caps, []).entries}["ashby:fake-ok"]


def test_an_entry_handled_and_gone_is_never_proposed_again() -> None:
    search = section(title_include=["AI engineer"], boards=[board("fake-ok")])
    found = starter.propose(STUB, search, ["ashby:fake-ok", "greenhouse:fake-ok2"])
    assert "greenhouse:fake-ok2" not in keys(found)  # removed from the list, or deleted later
    assert {e.key: e.added for e in found.entries}["ashby:fake-ok"] is True  # still there


def test_never_apply_rules_and_the_app_s_own_rule_leave_entries_out() -> None:
    search = section(title_include=["AI engineer"], never_apply=["Defence: defence, defense"])
    found = starter.propose(STUB, search, [])
    assert "lever:fake-404" not in keys(found)
    assert found.blocked == 1
    google = section(title_include=["AI engineer"], never_apply=["No Google: google"])
    assert "source:google_careers" not in keys(starter.propose(STUB, google, []))
    # a catalog entry that slipped past the loader is still never proposed
    shield = starter.Board(company="Example Security Labs", ats="ashby", org="fake-ok",
                           industries=("software",), general=True)  # fmt: skip
    leaky = STUB.model_copy(update={"boards": (shield,), "sources": ()})
    assert starter.propose(leaky, section(), []).entries == []
    assert starter.propose(leaky, section(), []).blocked == 1


def test_sources_are_always_proposed_whatever_the_person_saved() -> None:
    both = ["source:vie", "source:google_careers"]
    for values in (
        {},
        {"title_include": ["AI engineer"]},
        {"title_include": ["AI engineer"], "contract_types": ["CDI"]},
        {"industries": ["Agriculture"]},
        {"location": ["Atlantis"], "contract_types": ["VIE"]},
    ):
        assert keys(starter.propose(STUB, section(**values), []))[:2] == both, values
    entry = {
        e.key: e for e in starter.propose(STUB, section(title_include=["AI engineer"]), []).entries
    }["source:vie"]
    assert (entry.query, entry.location, entry.why) == (
        "AI engineer",
        None,
        'Searches "AI engineer"',
    )
    # role words kept as patterns are no search words: the default query, said so
    patterns = section(title_include=[r"ai\s+engineer"], title_include_re=True)
    entry = {e.key: e for e in starter.propose(STUB, patterns, []).entries}["source:vie"]
    assert (entry.query, entry.why) == (starter.DEFAULT_QUERY, "Searches every job (no role given)")
    # a place goes to Google Careers only, and the first place when several
    google = {
        e.key: e for e in starter.propose(STUB, section(location=["Paris", "Lyon"]), []).entries
    }["source:google_careers"]
    assert (google.location, google.why) == ("Paris", "Searches every job (no role given) in Paris")


def test_a_source_the_person_removed_is_not_proposed_again() -> None:
    found = starter.propose(STUB, section(), ["source:vie"])
    assert "source:vie" not in keys(found)
    assert "source:google_careers" in keys(found)


def test_the_list_is_capped() -> None:
    found = starter.propose(REAL, section(title_include=["Software engineer"]), [])
    assert len(found.entries) == starter.ENTRIES_MAX


def test_two_people_get_lists_from_their_own_targets() -> None:
    a = starter.propose(STUB, section(industries=["Health"]), [])
    b = starter.propose(STUB, section(industries=["Travel"]), ["greenhouse:fake-dup"])
    assert boards(a) == ["lever:fake-unicode"]
    assert boards(b) == []


# --- the live check, on a fake transport --------------------------------------------------------


def _fake_ats(request: httpx.Request) -> httpx.Response:
    if "fake-ok" in request.url.path:
        return httpx.Response(200, json={"jobs": [{"title": "AI Engineer"}]})
    return httpx.Response(404)


def test_the_check_asks_each_board_once_through_the_board_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return _fake_ats(request)

    def checker(**_: Any) -> BoardChecker:
        return BoardChecker(transport=httpx.MockTransport(transport))

    monkeypatch.setattr(starter_check, "BoardChecker", checker)
    assert starter_check.main(["--file", str(starter.STUB_CATALOG_PATH)]) == 1
    out = capsys.readouterr().out
    assert "ok    ashby:fake-ok  1 postings" in out
    assert "FAIL  lever:fake-404  not_found" in out
    assert "2 of 5 boards answered" in out
    assert len(seen) == len(STUB.boards)
    assert set(seen) <= {"api.ashbyhq.com", "boards-api.greenhouse.io", "api.lever.co"}


# --- the routes ---------------------------------------------------------------------------------


class Who:
    def __init__(self, token: str, user_id: uuid.UUID) -> None:
        self.token, self.user_id = token, user_id


def _signed(factory: sessionmaker[Session], api: Api, email: str) -> Who:
    make_user(factory, email=email, name=email.split("@")[0], role=Role.member)
    token = api.login_token(email)
    with factory() as db:
        user_id = db.execute(sa.select(User.id).where(User.email == email)).scalar_one()
    return Who(token, user_id)


@pytest.fixture(autouse=True)
def stub_catalog(app: FastAPI) -> None:
    app.state.starter_catalog = STUB


def _search(**values: Any) -> dict[str, Any]:
    return section(**values).model_dump(mode="json")


@pytest.fixture
def a(factory: sessionmaker[Session], api: Api) -> Who:
    who = _signed(factory, api, "e2e-starter-a@example.org")
    set_profile(factory, who.user_id, _search(title_include=["AI engineer"], location=["Paris"]))
    return who


@pytest.fixture
def b(factory: sessionmaker[Session], api: Api) -> Who:
    who = _signed(factory, api, "e2e-starter-b@example.org")
    set_profile(
        factory,
        who.user_id,
        _search(title_include=["AI engineer"], never_apply=["Defence: defence"]),
    )
    return who


def read(api: Api, who: Who) -> dict[str, Any]:
    response = api.get(BASE, token=who.token)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def choose(api: Api, who: Who, **body: Any) -> Any:
    return api.post(BASE, token=who.token, json=body)


def stored(factory: sessionmaker[Session], who: Who) -> tuple[int, dict[str, Any]]:
    with factory() as db:
        profile = db.get(Profile, who.user_id)
        return profile.revision, dict(profile.sections or {})


def test_the_list_of_the_caller(api: Api, a: Who) -> None:
    found = read(api, a)
    assert found["targets"] is True
    assert [e["key"] for e in found["entries"]] == A_LIST
    assert found["entries"][3] == {
        "key": "ashby:fake-ok",
        "kind": "board",
        "company": "Example Robotics",
        "ats": "ashby",
        "org": "fake-ok",
        "source": None,
        "query": None,
        "location": None,
        "why": "Artificial intelligence, France",
        "added": False,
    }
    assert (found["version"], found["checked_on"], found["blocked"]) == (1, "2026-10-20", 0)


def test_an_empty_profile_gets_the_general_list(factory: sessionmaker[Session], api: Api) -> None:
    who = _signed(factory, api, "e2e-starter-new@example.org")
    found = read(api, who)
    assert found["targets"] is False
    assert [e["key"] for e in found["entries"]] == [
        "source:vie",
        "source:google_careers",
        "ashby:fake-ok",
        "greenhouse:fake-ok2",
    ]


def test_adding_puts_enabled_boards_and_sources_in_the_search(
    factory: sessionmaker[Session], api: Api, a: Who
) -> None:
    revision, _ = stored(factory, a)
    answer = choose(api, a, add=["ashby:fake-ok", "source:google_careers"])
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["added"] == ["ashby:fake-ok", "source:google_careers"]
    added = {e["key"]: e["added"] for e in body["list"]["entries"]}
    assert added == {key: key in ("ashby:fake-ok", "source:google_careers") for key in A_LIST}
    after, sections = stored(factory, a)
    assert after == revision + 2  # one revision of search, one of preferences
    assert sections["search"]["boards"] == [
        {
            "company": "Example Robotics",
            "ats": "ashby",
            "org": "fake-ok",
            "enabled": True,
            "extra_exclude": {"advanced_regex": False, "terms": []},
        }
    ]
    assert sections["search"]["browser_sources"] == [
        {
            "kind": "google_careers",
            "query": "AI engineer",
            "location": "Paris",
            "enabled": True,
            "interactive_only": False,
            # browser B10 shape: the acceptance fields, null for a source that is not linkedin
            "accepted_at": None,
            "text_version": None,
        }
    ]
    handled = sections["preferences"]["onboarding"]["starter_handled"]
    assert handled == ["ashby:fake-ok", "source:google_careers"]
    # the same choice again changes nothing
    again = choose(api, a, add=["ashby:fake-ok"])
    assert again.json()["added"] == []
    assert stored(factory, a)[0] == after


def test_removing_and_deleting_are_never_proposed_again(
    factory: sessionmaker[Session], api: Api, a: Who
) -> None:
    removed = choose(api, a, remove=["lever:fake-404"])
    assert removed.status_code == 200, removed.text
    assert "lever:fake-404" not in [e["key"] for e in removed.json()["list"]["entries"]]
    _, sections = stored(factory, a)
    assert sections["search"]["boards"] == []  # a removal adds nothing
    assert choose(api, a, add=["ashby:fake-ok"]).status_code == 200
    # the person deletes the board in Search options: it is not proposed again
    revision, sections = stored(factory, a)
    search = {**sections["search"], "boards": []}
    put = api.request(
        "PUT",
        "/api/profile/search",
        token=a.token,
        json={"base_revision": revision, "data": search},
    )
    assert put.status_code == 200, put.text
    assert [e["key"] for e in read(api, a)["entries"]] == [
        "source:vie",
        "source:google_careers",
        "lever:fake-unicode",
        "greenhouse:fake-ok2",
    ]
    # nor can it be added back from the list
    assert choose(api, a, add=["ashby:fake-ok", "lever:fake-404"]).json()["added"] == []


def test_a_board_on_the_never_apply_list_is_never_proposed_nor_added(
    factory: sessionmaker[Session], api: Api, b: Who
) -> None:
    found = read(api, b)
    assert "lever:fake-404" not in [e["key"] for e in found["entries"]]
    assert found["blocked"] == 1
    answer = choose(api, b, add=["lever:fake-404"])
    assert answer.status_code == 200
    assert answer.json()["added"] == []
    assert stored(factory, b)[1]["search"]["boards"] == []


def test_two_people_get_independent_lists(
    factory: sessionmaker[Session], api: Api, a: Who, b: Who
) -> None:
    assert choose(api, a, add=["greenhouse:fake-ok2"], remove=["ashby:fake-ok"]).status_code == 200
    entries = {e["key"]: e["added"] for e in read(api, b)["entries"]}
    assert entries["ashby:fake-ok"] is False
    assert entries["greenhouse:fake-ok2"] is False
    assert stored(factory, b)[1]["search"]["boards"] == []
    assert "starter_handled" not in str(stored(factory, b)[1].get("preferences"))


def test_an_unknown_key_is_refused(api: Api, a: Who) -> None:
    unknown = choose(api, a, add=["ashby:not-in-the-catalog"])
    assert unknown.status_code == 422
    assert unknown.json() == {
        "detail": "That board or site is not on the starter list.",
        "code": "unknown_key",
    }
    for body in (
        {"add": ["linkedin:jobs"]},
        {"add": ["source:linkedin"]},
        {"add": ["source:site"]},
        {"remove": ["ASHBY:FAKE-OK"]},
        {"add": "ashby:fake-ok"},
        {"add": ["ashby:fake-ok"] * 51},
        {"other": []},
    ):
        assert api.post(BASE, token=a.token, json=body).status_code == 422, body


def test_a_search_that_cannot_take_more_is_refused(
    factory: sessionmaker[Session], api: Api, a: Who
) -> None:
    full = [board(f"org-{n}") for n in range(300)]
    set_profile(factory, a.user_id, _search(title_include=["AI engineer"], boards=full))
    refused = choose(api, a, add=["ashby:fake-ok"])
    assert refused.status_code == 409
    assert refused.json()["code"] == "too_many"
    assert len(stored(factory, a)[1]["search"]["boards"]) == 300


def test_an_unreadable_search_is_refused(factory: sessionmaker[Session], api: Api, a: Who) -> None:
    with factory() as db, db.begin():
        profile = db.get(Profile, a.user_id)
        profile.sections = {**profile.sections, "search": {"min_fit": "high"}}
    assert api.get(BASE, token=a.token).json()["code"] == "search_unreadable"
    assert api.get(BASE, token=a.token).status_code == 409
    assert choose(api, a, add=["ashby:fake-ok"]).status_code == 409


def test_the_handled_keys_are_written_only_by_the_starter_route(
    factory: sessionmaker[Session], api: Api, a: Who
) -> None:
    assert choose(api, a, remove=["lever:fake-404"]).status_code == 200
    loaded = api.get("/api/profile", token=a.token).json()
    prefs = loaded["sections"]["preferences"]
    assert prefs["onboarding"]["starter_handled"] == ["lever:fake-404"]
    changed = {**prefs, "onboarding": {**prefs["onboarding"], "starter_handled": []}}
    refused = api.request(
        "PUT",
        "/api/profile/preferences",
        token=a.token,
        json={"base_revision": loaded["revision"], "data": changed},
    )
    assert refused.status_code == 422, refused.text
    exported = api.get("/api/profile/export", token=a.token).json()
    exported["sections"]["preferences"]["onboarding"]["starter_handled"] = ["ashby:fake-ok"]
    assert api.post("/api/profile/import", token=a.token, json=exported).status_code == 200
    onboarding = stored(factory, a)[1]["preferences"]["onboarding"]
    assert onboarding["starter_handled"] == ["lever:fake-404"]


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        (["ashby:openai", "source:vie", "ashby:openai"], True),
        (["greenhouse:mistral.ai", "lever:a_b-c"], True),
        (["ashby:OpenAI"], False),
        (["workday:x"], False),
        (["source:linkedin"], False),
        (["ashby:"], False),
        (["ashby:-x"], False),
        ("ashby:x", False),
        ([f"ashby:o{n}" for n in range(401)], False),
    ],
)
def test_the_handled_keys_are_checked(value: Any, ok: bool) -> None:
    data = {"onboarding": {"starter_handled": value}}
    if ok:
        kept = validate_section("preferences", data)["onboarding"]["starter_handled"]
        assert kept == list(dict.fromkeys(value))
    else:
        with pytest.raises(ValidationError):
            validate_section("preferences", data)


def test_the_routes_need_a_session_and_the_csrf_header(api: Api, a: Who) -> None:
    assert api.get(BASE).status_code == 401
    assert api.post(BASE, json={"add": []}).status_code in (401, 403)
    refused = api.post(BASE, token=a.token, json={"add": ["ashby:fake-ok"]}, csrf=False)
    assert refused.status_code == 403
