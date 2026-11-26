"""Jobs J30 (#829) in the Jobs list: the badge a posting serves, and the `criteria` filter (all,
within, outside) of GET /api/jobs, /places and /facets. The list shows every posting by default."""

from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from tests.identity.api_helpers import Api
from tests.jobs.test_api import Signed, alice, bob, listing, ok, seed, walk

__all__ = ["alice", "bob"]  # the fixtures of test_api, used here by name

ROWS = [
    {"slug": "within", "outside_criteria": []},
    {"slug": "never-computed", "outside_criteria": None},  # stored before migration 0051
    {"slug": "freelance", "outside_criteria": ["contract"], "location": "Lyon"},
    {"slug": "remote-and-old", "outside_criteria": ["work_mode", "older"], "location": "Lyon"},
]


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({}, {"within", "never-computed", "freelance", "remote-and-old"}),
        ({"criteria": "all"}, {"within", "never-computed", "freelance", "remote-and-old"}),
        ({"criteria": "within"}, {"within", "never-computed"}),
        ({"criteria": "outside"}, {"freelance", "remote-and-old"}),
        ({"criteria": "outside", "place": "Lyon"}, {"freelance", "remote-and-old"}),
        ({"criteria": "within", "place": "Lyon"}, set()),
    ],
)
def test_the_criteria_filter(
    api: Api,
    factory: sessionmaker[Session],
    alice: Signed,
    params: dict[str, Any],
    expected: set[str],
) -> None:
    seed(factory, alice.id, ROWS)
    assert set(walk(api, alice, 50, **params)) == expected


@pytest.mark.parametrize("value", ["", "badge", "Outside", "none"])
def test_another_criteria_value_is_422(api: Api, alice: Signed, value: str) -> None:
    assert listing(api, alice, criteria=value).status_code == 422


def test_each_posting_serves_its_badge_in_the_list_and_the_detail(
    api: Api, factory: sessionmaker[Session], alice: Signed
) -> None:
    made = seed(factory, alice.id, ROWS)
    items = {item["slug"]: item["outside_criteria"] for item in ok(api, alice)["items"]}
    assert items == {
        "within": [],
        "never-computed": [],
        "freelance": ["contract"],
        "remote-and-old": ["work_mode", "older"],
    }
    detail = api.get(f"/api/jobs/{made[3].id}", token=alice.token)
    assert detail.status_code == 200, detail.text
    assert detail.json()["outside_criteria"] == ["work_mode", "older"]


def test_the_places_and_the_facets_take_the_filter(
    api: Api, factory: sessionmaker[Session], alice: Signed, bob: Signed
) -> None:
    seed(factory, alice.id, ROWS)
    seed(factory, bob.id, [{"slug": "bobs", "outside_criteria": ["contract"]}])
    facets = api.get("/api/jobs/facets", token=alice.token, params={"criteria": "outside"})
    assert facets.status_code == 200, facets.text
    assert [(p["value"], p["count"]) for p in facets.json()["places"]] == [("Lyon", 2)]
    places = api.get("/api/jobs/places", token=alice.token, params={"criteria": "within"})
    assert places.status_code == 200, places.text
