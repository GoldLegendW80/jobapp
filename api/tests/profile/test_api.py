"""The profile API against a real Postgres: lazy creation, saves with revisions, 422 and 409,
revision history and pagination, isolation between users, CSRF and the session guard."""

import unicodedata
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy.orm import Session, sessionmaker

from app.identity.deps import CSRF_REFUSED, PUBLIC_ROUTES, UNAUTHORIZED
from app.profile.models import Profile, ProfileRevision
from app.profile.router import REVISION_NOT_FOUND, STALE, UNKNOWN_SECTION
from app.profile.schemas import SECTIONS
from tests.identity.api_helpers import Api, Clock
from tests.profile.conftest import FULL, full

PROFILE_ROUTES = {
    ("GET", "/api/profile"),
    ("PUT", "/api/profile/{section}"),
    ("GET", "/api/profile/revisions"),
    ("GET", "/api/profile/revisions/{revision}"),
    ("GET", "/api/profile/export"),
    ("POST", "/api/profile/import"),
}


def read(api: Api, token: str) -> dict[str, Any]:
    response = api.get("/api/profile", token=token)
    assert response.status_code == 200, response.text
    return response.json()


def save(api: Api, token: str, section: str, data: Any, base: int, **kwargs: Any) -> Any:
    return api.request(
        "PUT",
        f"/api/profile/{section}",
        token=token,
        json={"base_revision": base, "data": data},
        **kwargs,
    )


def save_ok(api: Api, token: str, section: str, data: Any, base: int) -> dict[str, Any]:
    response = save(api, token, section, data, base)
    assert response.status_code == 200, response.text
    return response.json()


def rows(factory: sessionmaker[Session], model: type) -> list[Any]:
    with factory() as db:
        return list(db.execute(sa.select(model)).scalars())


# --- reading ------------------------------------------------------------------------------------


def test_get_creates_an_empty_profile_lazily(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    assert rows(factory, Profile) == []
    body = read(api, alice)
    assert body["revision"] == 0
    assert body["sections"] == {name: None for name in SECTIONS}
    assert body["completeness"]["overall"] == 0
    assert set(body["completeness"]["sections"]) == set(SECTIONS)
    assert body["completeness"]["sections"]["facts"] == {"percent": 0, "missing": ["blocks"]}
    created = rows(factory, Profile)
    assert len(created) == 1
    assert created[0].sections == {}
    assert created[0].photo_mime is None  # photo itself is deferred: not loaded with the row
    # A second read finds the same row, nothing new, no revision.
    assert read(api, alice) == body
    assert len(rows(factory, Profile)) == 1
    assert rows(factory, ProfileRevision) == []


def test_profile_data_is_never_cached(api: Api, alice: str) -> None:
    responses = [
        api.get("/api/profile", token=alice),
        save(api, alice, "facts", full("facts"), 0),
        save(api, alice, "facts", full("facts"), 0),  # 409 carries the data too
        api.get("/api/profile/revisions", token=alice),
        api.get("/api/profile/revisions/1", token=alice),
    ]
    assert [r.status_code for r in responses] == [200, 200, 409, 200, 200]
    for response in responses:
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["content-type"] == "application/json"


# --- saving -------------------------------------------------------------------------------------


@pytest.mark.parametrize("section", SECTIONS)
def test_put_each_section(
    api: Api, alice: str, section: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    body = save_ok(api, alice, section, full(section), 0)
    assert body["revision"] == 1
    assert body["sections"][section] == FULL[section]
    assert body["completeness"]["sections"][section] == {"percent": 100, "missing": []}
    others = [name for name in SECTIONS if name != section]
    assert all(body["sections"][name] is None for name in others)
    assert read(api, alice) == body
    (revision,) = rows(factory, ProfileRevision)
    assert (revision.revision, revision.section, revision.data) == (1, section, FULL[section])
    assert revision.created_at == clock.now


def test_put_without_a_prior_get_creates_the_profile(api: Api, alice: str) -> None:
    assert save_ok(api, alice, "facts", full("facts"), 0)["revision"] == 1


def test_full_profile_is_100_percent(api: Api, alice: str) -> None:
    for base, section in enumerate(SECTIONS):
        body = save_ok(api, alice, section, full(section), base)
    assert body["revision"] == len(SECTIONS)
    assert body["completeness"]["overall"] == 100
    assert body["sections"] == FULL


def test_each_save_increments_revision_by_exactly_one(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    for expected in range(1, 8):
        clock.advance(seconds=1)
        data = {"full_name": f"Ada {expected}"}
        body = save_ok(api, alice, "identity", data, expected - 1)
        assert body["revision"] == expected
        assert body["updated_at"].startswith(clock.now.isoformat()[:19])
    revisions = sorted(rows(factory, ProfileRevision), key=lambda r: r.revision)
    assert [r.revision for r in revisions] == list(range(1, 8))
    assert [r.data["full_name"] for r in revisions] == [f"Ada {n}" for n in range(1, 8)]


def test_saving_one_section_keeps_the_others(api: Api, alice: str) -> None:
    save_ok(api, alice, "facts", full("facts"), 0)
    save_ok(api, alice, "skills", full("skills"), 1)
    body = save_ok(api, alice, "facts", {"blocks": []}, 2)
    assert body["sections"]["facts"] == {"blocks": []}
    assert body["sections"]["skills"] == FULL["skills"]


def test_saving_identical_data_still_makes_a_revision(api: Api, alice: str) -> None:
    save_ok(api, alice, "facts", full("facts"), 0)
    assert save_ok(api, alice, "facts", full("facts"), 1)["revision"] == 2


def test_put_stores_the_normalised_form(api: Api, alice: str) -> None:
    raw = {"full_name": unicodedata.normalize("NFD", "  Zoë Ünal  "), "email": " ZOE@Example.org"}
    body = save_ok(api, alice, "identity", raw, 0)
    assert body["sections"]["identity"]["full_name"] == "Zoë Ünal"
    assert body["sections"]["identity"]["email"] == "zoe@example.org"
    assert body["sections"]["identity"]["headline"] is None


UNICODE_SAMPLES = [
    "Zoë 🚀 Dev 👩🏽‍💻",
    "محمد الأحمد",
    "דוד כהן",
    "李小龍 ｆｕｌｌｗｉｄｔｈ",
    "𝔘𝔫𝔦𝔠𝔬𝔡𝔢 and ‍ joiner",
    "quotes \" ' \\ and backslash",
]


def test_jsonb_round_trip_preserves_unicode_exactly(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    body = "\n".join(UNICODE_SAMPLES) + "\n\ttabbed"
    blocks = [{"title": text, "body": body} for text in UNICODE_SAMPLES]
    save_ok(api, alice, "facts", {"blocks": blocks}, 0)
    stored = read(api, alice)["sections"]["facts"]["blocks"]
    assert [b["title"] for b in stored] == UNICODE_SAMPLES
    assert all(b["body"] == body for b in stored)
    (revision,) = rows(factory, ProfileRevision)
    assert revision.data["blocks"] == blocks
    # Byte for byte in the database too, read back as text by Postgres itself.
    with factory() as db:
        title = db.execute(
            sa.text("SELECT sections->'facts'->'blocks'->0->>'title' FROM profiles")
        ).scalar_one()
    assert title == UNICODE_SAMPLES[0]


def test_large_fact_base_round_trip(api: Api, alice: str) -> None:
    body = ("Ligne avec accents éàü et emoji 🙂.\n" * 4_000)[:150_000].strip()
    save_ok(api, alice, "facts", {"blocks": [{"title": "Big", "body": body}]}, 0)
    assert read(api, alice)["sections"]["facts"]["blocks"][0]["body"] == body


# --- 422 ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("section", "data", "detail"),
    [
        ("identity", {"surprise": 1}, "data.surprise: Extra inputs are not permitted"),
        ("identity", {"full_name": "a\x00b"}, "data.full_name: control characters are not allowed"),
        (
            "experience",
            {"employers": [{"key": "a", "organisation": "A", "title": "T", "start": "2025-13"}]},
            "data.employers.0.start: must be a year or a month, written YYYY or YYYY-MM, "
            "between 1900 and 2100",
        ),
        (
            "experience",
            {"employers": [{"key": "a", "organisation": "A", "title": "T", "start": "2021-3"}]},
            "data.employers.0.start: must be a year or a month, written YYYY or YYYY-MM, "
            "between 1900 and 2100",
        ),
        (
            "experience",
            {"education": [{"school": "S", "degree": "D", "end": "21"}]},
            "data.education.0.end: must be a year or a month, written YYYY or YYYY-MM, "
            "between 1900 and 2100",
        ),
        (
            "experience",
            {"employers": [{"key": "a", "organisation": "A", "title": "T", "start": 2021}]},
            "data.employers.0.start: Input should be a valid string",
        ),
        (
            "experience",
            {
                "employers": [
                    {
                        "key": "a",
                        "organisation": "A",
                        "title": "T",
                        "start": "2023",
                        "end": "2022-12",
                    }
                ]
            },
            "data.employers.0: end must not be before start",
        ),
        (
            "experience",
            {
                "employers": [
                    {
                        "key": "a",
                        "organisation": "A",
                        "title": "T",
                        "start": "2025-01",
                        "end": "2024-12",
                    }
                ]
            },
            "data.employers.0: end must not be before start",
        ),
        (
            "search",
            {"title_include": {"terms": ["(ai"], "advanced_regex": True}},
            "data.title_include.terms: term 1: invalid regular expression at position 0: "
            "missing ), unterminated subpattern",
        ),
        (
            "skills",
            {"years_experience": "3"},
            "data.years_experience: Input should be a valid number",
        ),
        ("search", {"min_fit": 101}, "data.min_fit: Input should be less than or equal to 100"),
    ],
)
def test_invalid_data_is_422_with_one_sentence(
    api: Api, alice: str, section: str, data: Any, detail: str, factory: sessionmaker[Session]
) -> None:
    response = save(api, alice, section, data, 0)
    assert response.status_code == 422
    assert response.json() == {"detail": detail}
    assert rows(factory, ProfileRevision) == []
    assert read(api, alice)["revision"] == 0


def test_422_never_echoes_the_value(api: Api, alice: str) -> None:
    secret = "do-not-echo-this-value"
    response = save(api, alice, "skills", {"years_experience": secret}, 0)
    assert response.status_code == 422
    assert secret not in response.text
    response = save(api, alice, "identity", {"full_name": secret * 20}, 0)
    assert response.status_code == 422
    assert secret not in response.text
    employer = {"key": "a", "organisation": "A", "title": "T", "start": "not-a-date-1987"}
    response = save(api, alice, "experience", {"employers": [employer]}, 0)
    assert response.status_code == 422
    assert "1987" not in response.text


# --- dates: a month, or a year alone (P7) -------------------------------------------------------


def employer(start: str, end: str | None = None, key: str = "acme") -> dict[str, Any]:
    return {
        "key": key,
        "organisation": "Acme Group",
        "title": "Engineer",
        "start": start,
        "end": end,
        "location": None,
        "contract": None,
    }


def school(start: str | None, end: str | None) -> dict[str, Any]:
    return {
        "school": "School of Engineering",
        "degree": "MSc",
        "start": start,
        "end": end,
        "location": None,
        "details": None,
    }


def test_a_year_alone_is_saved_read_and_kept_in_history_as_written(
    api: Api, alice: str, factory: sessionmaker[Session], clock: Clock
) -> None:
    years = {"employers": [employer("2021", "2023")], "education": [school("2019", None)]}
    body = save_ok(api, alice, "experience", years, 0)
    assert body["sections"]["experience"] == years
    assert body["completeness"]["sections"]["experience"] == {"percent": 100, "missing": []}
    assert read(api, alice)["sections"]["experience"] == years
    # In Postgres too: the JSONB of the profile and of the revision hold the four characters.
    with factory() as db:
        raw = db.execute(
            sa.text(
                "select sections #>> '{experience,employers,0,start}',"
                " sections #>> '{experience,employers,0,end}',"
                " sections #>> '{experience,education,0,start}',"
                " sections #> '{experience,education,0,end}' from profiles"
            )
        ).one()
        kept = db.execute(
            sa.text("select data #>> '{employers,0,start}' from profile_revisions")
        ).scalar_one()
    assert tuple(raw) == ("2021", "2023", "2019", None)
    assert kept == "2021"

    # Edited to a month: a new revision, the old one unchanged, and a restore brings the year back.
    clock.advance(minutes=1)
    months = {"employers": [employer("2021-03", "2023")], "education": [school("2019", None)]}
    assert save_ok(api, alice, "experience", months, 1)["sections"]["experience"] == months
    first = api.get("/api/profile/revisions/1", token=alice).json()
    second = api.get("/api/profile/revisions/2", token=alice).json()
    assert first["data"] == years
    assert second["data"] == months
    assert (first["data"]["employers"][0]["start"], second["data"]["employers"][0]["start"]) == (
        "2021",
        "2021-03",
    )
    restored = save_ok(api, alice, "experience", first["data"], 2)
    assert restored["revision"] == 3
    assert restored["sections"]["experience"] == years


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2023-05", "2023"),
        ("2023", "2023"),
        ("2021", "2021-03"),
        ("2021-03", "2021"),
        ("2021", None),
    ],
)
def test_ranges_that_may_be_in_order_are_saved(
    api: Api, alice: str, start: str, end: str | None
) -> None:
    data = {"employers": [employer(start, end)], "education": [school(start, end)]}
    stored = save_ok(api, alice, "experience", data, 0)["sections"]["experience"]
    assert stored == data


@pytest.mark.parametrize(
    ("start", "end"),
    [("2023", "2022-12"), ("2023-06", "2023-05"), ("2024", "2023"), ("2024-01", "2023")],
)
def test_ranges_certainly_out_of_order_are_422_on_the_entry(
    api: Api, alice: str, factory: sessionmaker[Session], start: str, end: str
) -> None:
    response = save(api, alice, "experience", {"employers": [employer(start, end)]}, 0)
    assert response.status_code == 422
    assert response.json() == {"detail": "data.employers.0: end must not be before start"}
    response = save(api, alice, "experience", {"education": [school(start, end)]}, 0)
    assert response.json() == {"detail": "data.education.0: end must not be before start"}
    assert rows(factory, ProfileRevision) == []


def test_a_profile_stored_before_the_change_reads_and_saves_unchanged(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    """Rows written by the month only model: read as they are, complete, exported as they are,
    and a save of the same data stores the same data. Nothing is rewritten on the way."""
    legacy = full("experience")
    read(api, alice)  # creates the row
    with factory() as db:
        profile = db.execute(sa.select(Profile)).scalar_one()
        profile.sections = {"experience": legacy}
        profile.revision = 1
        db.add(
            ProfileRevision(
                user_id=profile.user_id,
                revision=1,
                section="experience",
                data=legacy,
                created_at=profile.updated_at,
            )
        )
        db.commit()
    body = read(api, alice)
    assert body["sections"]["experience"] == FULL["experience"]
    assert body["completeness"]["sections"]["experience"]["percent"] == 100
    exported = api.get("/api/profile/export", token=alice).json()
    assert exported["sections"]["experience"] == FULL["experience"]
    assert api.get("/api/profile/revisions/1", token=alice).json()["data"] == FULL["experience"]
    saved = save_ok(api, alice, "experience", body["sections"]["experience"], 1)
    assert saved["sections"]["experience"] == FULL["experience"]
    assert rows(factory, ProfileRevision)[-1].data == FULL["experience"]


def test_two_tabs_saving_dates_the_second_gets_409_with_the_first_ones_year(
    api: Api, alice: str
) -> None:
    base = read(api, alice)["revision"]
    save_ok(api, alice, "experience", {"employers": [employer("2021")]}, base)
    conflict = save(api, alice, "experience", {"employers": [employer("2021-03")]}, base)
    assert conflict.status_code == 409
    assert conflict.json()["sections"]["experience"]["employers"][0]["start"] == "2021"
    retry = save_ok(
        api, alice, "experience", {"employers": [employer("2021-03")]}, conflict.json()["revision"]
    )
    assert retry["sections"]["experience"]["employers"][0]["start"] == "2021-03"


def test_openapi_describes_a_year_alone_and_a_month(api: Api) -> None:
    schema = api.client.get("/openapi.json").json()
    body = schema["paths"]["/api/profile/import"]["post"]["requestBody"]["content"]
    employer_schema = body["application/json"]["schema"]["$defs"]["Employer"]["properties"]
    assert "a year alone, YYYY" in employer_schema["start"]["description"]
    assert employer_schema["start"]["examples"] == ["2021-03", "2021"]
    assert employer_schema["start"]["pattern"] == r"^\d{4}(-(0[1-9]|1[0-2]))?$"
    assert "a year alone, YYYY" in employer_schema["end"]["description"]
    put = schema["paths"]["/api/profile/{section}"]["put"]["description"]
    assert "YYYY-MM or YYYY" in put


@pytest.mark.parametrize(
    "body",
    [
        {"data": {}},
        {"base_revision": 0},
        {"base_revision": "0", "data": {}},
        {"base_revision": -1, "data": {}},
        {"base_revision": 2**31, "data": {}},
        {"base_revision": 0, "data": []},
        {"base_revision": 0, "data": "x"},
        {"base_revision": 0, "data": {}, "extra": 1},
        {"base_revision": True, "data": {}},
    ],
)
def test_malformed_body_is_422(api: Api, alice: str, body: dict[str, Any]) -> None:
    response = api.request("PUT", "/api/profile/identity", token=alice, json=body)
    assert response.status_code == 422, response.text
    assert isinstance(response.json()["detail"], str)


def test_not_json_is_422(api: Api, alice: str) -> None:
    response = api.request(
        "PUT",
        "/api/profile/identity",
        token=alice,
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("section", ["Identity", "unknown", "revisions", "export", "photos"])
def test_unknown_section_is_404(api: Api, alice: str, section: str) -> None:
    response = save(api, alice, section, {}, 0)
    assert response.status_code == 404
    assert response.json() == {"detail": UNKNOWN_SECTION}


# --- 409 ----------------------------------------------------------------------------------------


def test_stale_base_revision_is_409_with_current_data(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    save_ok(api, alice, "facts", full("facts"), 0)
    response = save(api, alice, "facts", {"blocks": []}, 0)
    assert response.status_code == 409
    body = response.json()
    assert body["detail"] == STALE
    assert body["revision"] == 1
    assert body["sections"]["facts"] == FULL["facts"]
    assert body["sections"]["identity"] is None
    assert len(rows(factory, ProfileRevision)) == 1
    assert read(api, alice)["sections"]["facts"] == FULL["facts"]


def test_future_base_revision_is_409(api: Api, alice: str) -> None:
    response = save(api, alice, "facts", full("facts"), 5)
    assert response.status_code == 409
    assert response.json()["revision"] == 0


def test_two_tabs_editing(api: Api, alice: str) -> None:
    """Both tabs load revision 0; the first save wins, the second gets 409 and retries."""
    base = read(api, alice)["revision"]
    save_ok(api, alice, "identity", {"full_name": "Tab one"}, base)
    conflict = save(api, alice, "identity", {"full_name": "Tab two"}, base)
    assert conflict.status_code == 409
    assert conflict.json()["sections"]["identity"]["full_name"] == "Tab one"
    retry = save_ok(api, alice, "identity", {"full_name": "Tab two"}, conflict.json()["revision"])
    assert retry["revision"] == 2
    assert retry["sections"]["identity"]["full_name"] == "Tab two"


# --- revisions ----------------------------------------------------------------------------------


def make_history(api: Api, token: str, clock: Clock) -> None:
    """Revisions 1..9: identity, facts, skills, then identity, facts, skills..."""
    order = ["identity", "facts", "skills"]
    for n in range(9):
        clock.advance(minutes=1)
        section = order[n % 3]
        data = {"full_name": f"Ada {n + 1}"} if section == "identity" else full(section)
        save_ok(api, token, section, data, n)


def test_revisions_list_newest_first(api: Api, alice: str, clock: Clock) -> None:
    make_history(api, alice, clock)
    response = api.get("/api/profile/revisions", token=alice)
    assert response.status_code == 200
    body = response.json()
    assert [item["revision"] for item in body["items"]] == list(range(9, 0, -1))
    assert body["next_before"] is None
    assert set(body["items"][0]) == {"revision", "section", "created_at"}  # no data in the list
    assert body["items"][-1]["section"] == "identity"


def test_revisions_filtered_by_section(api: Api, alice: str, clock: Clock) -> None:
    make_history(api, alice, clock)
    body = api.get("/api/profile/revisions?section=identity", token=alice).json()
    assert [item["revision"] for item in body["items"]] == [7, 4, 1]
    assert {item["section"] for item in body["items"]} == {"identity"}
    body = api.get("/api/profile/revisions?section=answers", token=alice).json()
    assert body == {"items": [], "next_before": None}


def test_revisions_pagination(api: Api, alice: str, clock: Clock) -> None:
    make_history(api, alice, clock)
    seen: list[int] = []
    path = "/api/profile/revisions?limit=4"
    pages = 0
    while True:
        body = api.get(path, token=alice).json()
        seen += [item["revision"] for item in body["items"]]
        pages += 1
        if body["next_before"] is None:
            break
        path = f"/api/profile/revisions?limit=4&before={body['next_before']}"
    assert seen == list(range(9, 0, -1))
    assert pages == 3
    # Exactly a page: no next page announced.
    body = api.get("/api/profile/revisions?limit=9", token=alice).json()
    assert len(body["items"]) == 9
    assert body["next_before"] is None
    body = api.get("/api/profile/revisions?limit=2&before=4&section=skills", token=alice).json()
    assert [item["revision"] for item in body["items"]] == [3]


def test_revisions_empty(api: Api, alice: str) -> None:
    body = api.get("/api/profile/revisions", token=alice).json()
    assert body == {"items": [], "next_before": None}


@pytest.mark.parametrize(
    "query",
    ["section=photo", "limit=0", "limit=101", "limit=x", "before=0", "before=-1",
     f"before={2**31}"],
)  # fmt: skip
def test_revisions_bad_query_is_422(api: Api, alice: str, query: str) -> None:
    assert api.get(f"/api/profile/revisions?{query}", token=alice).status_code == 422


def test_get_one_past_revision(api: Api, alice: str, clock: Clock) -> None:
    make_history(api, alice, clock)
    response = api.get("/api/profile/revisions/4", token=alice)
    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == 4
    assert body["section"] == "identity"
    assert body["data"]["full_name"] == "Ada 4"
    assert set(body) == {"revision", "section", "created_at", "data"}


def test_restore_is_a_save_of_old_data(api: Api, alice: str, clock: Clock) -> None:
    make_history(api, alice, clock)
    old = api.get("/api/profile/revisions/1", token=alice).json()
    body = save_ok(api, alice, old["section"], old["data"], 9)
    assert body["revision"] == 10
    assert body["sections"]["identity"]["full_name"] == "Ada 1"


@pytest.mark.parametrize("revision", ["99", str(2**31 - 1)])
def test_unknown_revision_is_404(api: Api, alice: str, revision: str) -> None:
    response = api.get(f"/api/profile/revisions/{revision}", token=alice)
    assert response.status_code == 404
    assert response.json() == {"detail": REVISION_NOT_FOUND}


@pytest.mark.parametrize("revision", ["0", "-1", "x", str(2**31), "1.5"])
def test_bad_revision_number_is_422(api: Api, alice: str, revision: str) -> None:
    assert api.get(f"/api/profile/revisions/{revision}", token=alice).status_code == 422


# --- isolation ----------------------------------------------------------------------------------


def test_users_never_see_each_other(api: Api, alice: str, bob: str, clock: Clock) -> None:
    make_history(api, alice, clock)
    bob_profile = read(api, bob)
    assert bob_profile["revision"] == 0
    assert bob_profile["sections"] == {name: None for name in SECTIONS}
    for n in range(1, 10):
        response = api.get(f"/api/profile/revisions/{n}", token=bob)
        assert response.status_code == 404
        assert response.json() == {"detail": REVISION_NOT_FOUND}  # same as a missing one
    assert api.get("/api/profile/revisions", token=bob).json()["items"] == []
    # Bob's saves number from 1 in his own history and never touch Alice's.
    save_ok(api, bob, "identity", {"full_name": "Bob"}, 0)
    assert api.get("/api/profile/revisions/1", token=bob).json()["data"]["full_name"] == "Bob"
    assert api.get("/api/profile/revisions/1", token=alice).json()["data"]["full_name"] == "Ada 1"
    assert read(api, alice)["revision"] == 9
    assert read(api, alice)["sections"]["identity"]["full_name"] == "Ada 7"
    # Bob cannot write into Alice's profile with her revision number either.
    assert save(api, bob, "identity", {"full_name": "Mallory"}, 9).status_code == 409
    assert read(api, alice)["sections"]["identity"]["full_name"] == "Ada 7"


def test_query_parameters_cannot_select_another_user(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session]
) -> None:
    save_ok(api, alice, "facts", full("facts"), 0)
    with factory() as db:
        alice_id = db.execute(sa.select(Profile.user_id)).scalar_one()
    for path in (
        f"/api/profile?user_id={alice_id}",
        f"/api/profile/revisions?user_id={alice_id}",
        f"/api/profile/revisions/1?user_id={alice_id}",
    ):
        response = api.get(path, token=bob)
        assert alice_id.hex not in response.text.replace("-", "")
        assert "Hard rules" not in response.text


def test_deleting_a_user_deletes_the_profile(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session]
) -> None:
    save_ok(api, alice, "facts", full("facts"), 0)
    save_ok(api, bob, "facts", full("facts"), 0)
    with factory() as db, db.begin():
        db.execute(sa.text("DELETE FROM users WHERE email = 'alice@example.org'"))
    assert len(rows(factory, Profile)) == 1
    assert len(rows(factory, ProfileRevision)) == 1


# --- guards -------------------------------------------------------------------------------------


def test_profile_routes_are_served_and_none_is_public(app: FastAPI) -> None:
    served = set()
    for context in iter_route_contexts(app.routes):
        if isinstance(context.original_route, APIRoute):
            served.update((m, context.path) for m in context.methods or ())
    assert served >= PROFILE_ROUTES
    assert not {path for _, path in PUBLIC_ROUTES if path.startswith("/api/profile")}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/profile"),
        ("PUT", "/api/profile/identity"),
        ("PUT", "/api/profile/nope"),
        ("GET", "/api/profile/revisions"),
        ("GET", "/api/profile/revisions/1"),
        ("GET", "/api/profile/export"),
        ("POST", "/api/profile/import"),
    ],
)
def test_without_a_session_every_route_is_401(api: Api, method: str, path: str) -> None:
    response = api.request(method, path, json={"base_revision": 0, "data": {}})
    assert response.status_code == 401
    assert response.json() == {"detail": UNAUTHORIZED}


def test_unknown_session_token_is_401(api: Api) -> None:
    assert api.get("/api/profile", token="x" * 43).status_code == 401


@pytest.mark.parametrize("value", [None, "", "XMLHttpRequest", "JOBAPP"])
def test_put_without_csrf_header_is_403(
    api: Api, alice: str, value: str | None, factory: sessionmaker[Session]
) -> None:
    headers = {} if value is None else {"X-Requested-With": value}
    response = save(api, alice, "facts", full("facts"), 0, csrf=False, headers=headers)
    assert response.status_code == 403
    assert response.json() == {"detail": CSRF_REFUSED}
    assert rows(factory, ProfileRevision) == []


def test_reads_need_no_csrf_header(api: Api, alice: str) -> None:
    assert api.get("/api/profile", token=alice, csrf=False).status_code == 200
    assert api.get("/api/profile/revisions", token=alice, csrf=False).status_code == 200


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_other_methods_are_405(api: Api, alice: str, method: str) -> None:
    assert api.request(method, "/api/profile/identity", token=alice).status_code == 405


def test_nothing_of_the_profile_is_logged(
    api: Api, alice: str, caplog: pytest.LogCaptureFixture
) -> None:
    marker = f"marker-{uuid.uuid4().hex}"
    with caplog.at_level("DEBUG"):
        save_ok(api, alice, "identity", {"full_name": marker}, 0)
        save(api, alice, "identity", {"full_name": marker}, 0)  # 409
        save(api, alice, "identity", {"full_name": marker, "x": 1}, 1)  # 422
        read(api, alice)
    assert marker not in caplog.text
