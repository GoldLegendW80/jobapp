"""GET /api/jobs/places through the app: pins per city else per country, counts per status,
job ids in panel order and capped, the jobs described, unplaced locations, every list filter,
bad parameters, the session, no-store, isolation between users, and the route not shadowed by
/api/jobs/{id}."""

import uuid
from datetime import date
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import gazetteer
from app.jobs.models import ContractType, JobStatus
from tests.identity.api_helpers import Api
from tests.jobs.test_api import ALICE, BOB, Signed, seed, sign_in


@pytest.fixture
def alice(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, ALICE)


@pytest.fixture
def bob(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, BOB)


def get(api: Api, who: Signed, **params: Any) -> httpx.Response:
    return api.get("/api/jobs/places", token=who.token, params=params)


def ok(api: Api, who: Signed, **params: Any) -> dict[str, Any]:
    response = get(api, who, **params)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def by_name(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {place["name"]: place for place in body["places"]}


ROWS: list[dict[str, Any]] = [
    {"location": "Paris, France", "status": JobStatus.applied, "fit": 80, "company": "Mistral"},
    {"location": "Paris", "status": JobStatus.found, "fit": 91},
    {"location": "Paris, London", "status": JobStatus.found, "fit": None},
    {"location": "London, UK", "status": JobStatus.skipped, "fit": 12},
    {"location": "Remote, United States", "status": JobStatus.shortlisted, "fit": 70},
    {"location": "Zürich", "status": JobStatus.found, "fit": 40, "contract_type": ContractType.VIE},
    {"location": "Remote", "status": JobStatus.found, "fit": 50},
    {"location": "Remote", "status": JobStatus.interview, "fit": 60},
    {"location": "", "status": JobStatus.found, "fit": None},
    {"location": "東京", "status": JobStatus.found, "fit": 30},
]


def test_empty(api: Api, alice: Signed) -> None:
    body = ok(api, alice)
    assert body == {
        "places": [],
        "jobs": {},
        "unplaced": [],
        "total": 0,
        "placed": 0,
        "unplaced_total": 0,
    }


def test_places_counts_and_unplaced(
    api: Api, alice: Signed, factory: sessionmaker[Session]
) -> None:
    jobs = seed(factory, alice.id, ROWS)
    body = ok(api, alice)
    assert (body["total"], body["placed"], body["unplaced_total"]) == (10, 6, 4)
    assert [(p["name"], p["count"]) for p in body["places"]] == [
        ("Paris", 3),
        ("London", 2),
        ("United States", 1),
        ("Zurich", 1),
    ]
    places = by_name(body)
    paris = places["Paris"]
    assert paris["kind"] == "city"
    assert (paris["lat"], paris["lon"]) == (48.86, 2.35)
    assert paris["counts_by_status"] == {"found": 2, "applied": 1}
    # Panel order: pipeline order, then best fit, unscored last.
    assert paris["job_ids"] == [str(jobs[1].id), str(jobs[2].id), str(jobs[0].id)]
    assert paris["job_ids_truncated"] is False
    assert places["United States"]["kind"] == "country"
    assert places["London"]["counts_by_status"] == {"found": 1, "skipped": 1}
    # Every id listed is described.
    listed = {i for p in body["places"] for i in p["job_ids"]}
    assert set(body["jobs"]) == listed
    assert body["jobs"][str(jobs[0].id)] == {
        "title": "Role 0",
        "company": "Mistral",
        "location": "Paris, France",
        "status": "applied",
        "fit": 80,
    }
    # Unplaced locations with their counts, most first; no location is "".
    assert body["unplaced"] == [
        {"location": "Remote", "count": 2},
        {"location": "", "count": 1},
        {"location": "東京", "count": 1},
    ]


@pytest.mark.parametrize(
    ("params", "pins", "unplaced"),
    [
        ({"status": "found"}, {"Paris": 2, "London": 1, "Zurich": 1}, 3),
        ({"status": ["applied", "skipped"]}, {"Paris": 1, "London": 1}, 0),
        ({"type": "VIE"}, {"Zurich": 1}, 0),
        ({"company": "mistr"}, {"Paris": 1}, 0),
        ({"q": "london"}, {"Paris": 1, "London": 2}, 0),
        ({"fit_min": 60}, {"Paris": 2, "United States": 1}, 1),
        ({"fit_min": 10, "fit_max": 45}, {"London": 1, "Zurich": 1}, 1),
        ({"status": "rejected"}, {}, 0),
    ],
)
def test_filters_are_the_lists(
    api: Api,
    alice: Signed,
    factory: sessionmaker[Session],
    params: dict[str, Any],
    pins: dict[str, int],
    unplaced: int,
) -> None:
    seed(factory, alice.id, ROWS)
    body = ok(api, alice, **params)
    assert {p["name"]: p["count"] for p in body["places"]} == pins
    assert body["unplaced_total"] == unplaced
    # The same filters give the same total as the list.
    listing = api.get("/api/jobs", token=alice.token, params=params).json()
    assert body["total"] == listing["total"]


def test_found_date_filters(api: Api, alice: Signed, factory: sessionmaker[Session]) -> None:
    seed(
        factory,
        alice.id,
        [
            {"location": "Paris", "found_on": date(2026, 9, 1)},
            {"location": "London", "found_on": date(2026, 10, 1)},
        ],
    )
    body = ok(api, alice, found_from="2026-09-15")
    assert [p["name"] for p in body["places"]] == ["London"]
    body = ok(api, alice, found_to="2026-09-15")
    assert [p["name"] for p in body["places"]] == ["Paris"]


@pytest.mark.parametrize(
    "params",
    [
        {"status": "nope"},
        {"type": "Permanent"},
        {"fit_min": 101},
        {"fit_min": 50, "fit_max": 40},
        {"found_from": "2026-10-02", "found_to": "2026-10-01"},
        {"found_from": "2026-02-31"},
        {"q": "x" * 201},
    ],
)
def test_bad_parameters(api: Api, alice: Signed, params: dict[str, Any]) -> None:
    assert get(api, alice, **params).status_code == 422


def test_job_ids_are_capped(api: Api, alice: Signed, factory: sessionmaker[Session]) -> None:
    extra = 3
    seed(factory, alice.id, [{"location": "Lyon"}] * (gazetteer.JOB_IDS_MAX + extra))
    body = ok(api, alice)
    (lyon,) = body["places"]
    assert lyon["count"] == gazetteer.JOB_IDS_MAX + extra
    assert len(lyon["job_ids"]) == gazetteer.JOB_IDS_MAX
    assert lyon["job_ids_truncated"] is True
    assert len(body["jobs"]) == gazetteer.JOB_IDS_MAX


def test_isolation(api: Api, alice: Signed, bob: Signed, factory: sessionmaker[Session]) -> None:
    seed(factory, alice.id, [{"location": "Paris"}, {"location": "Remote"}])
    seed(factory, bob.id, [{"location": "London"}, {"location": "Atlantis"}])
    alice_body = ok(api, alice)
    bob_body = ok(api, bob)
    assert [p["name"] for p in alice_body["places"]] == ["Paris"]
    assert alice_body["unplaced"] == [{"location": "Remote", "count": 1}]
    assert [p["name"] for p in bob_body["places"]] == ["London"]
    assert bob_body["unplaced"] == [{"location": "Atlantis", "count": 1}]
    assert not set(alice_body["jobs"]) & set(bob_body["jobs"])


def test_needs_a_session(api: Api) -> None:
    response = api.get("/api/jobs/places")
    assert response.status_code == 401


def test_not_shadowed_by_a_job_id(api: Api, alice: Signed) -> None:
    # "places" is not a job id: the route answers, not the detail's 404.
    assert get(api, alice).status_code == 200
    assert api.get(f"/api/jobs/{uuid.uuid4()}", token=alice.token).status_code == 404
