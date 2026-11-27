"""Jobs J31 through the routes: GET /api/jobs, /places and /facets take `listing` (all, only the
postings seen in a list, or those whose page was read), and any other value answers 422; the
list item serves `list_url`. Invented postings only."""

from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from tests.identity.api_helpers import Api
from tests.jobs.test_api import Signed, alice, bob, listing, ok, seed, slugs_of

__all__ = ["alice", "bob"]  # the fixtures of test_api, used here by name

LIST = "https://aggregator.example.test/emplois?q=data"


@pytest.mark.parametrize(
    ("choice", "expected"),
    [(None, ["listed", "read"]), ("all", ["listed", "read"]), ("only", ["listed"]),
     ("without", ["read"])],
)  # fmt: skip
def test_the_list_places_and_facets_take_listing(
    api: Api,
    factory: sessionmaker[Session],
    alice: Signed,
    bob: Signed,
    choice: str | None,
    expected: list[str],
) -> None:
    seed(factory, alice.id, [{"slug": "listed", "list_url": LIST}, {"slug": "read"}])
    seed(factory, bob.id, [{"slug": "listed", "list_url": LIST}])
    params: dict[str, Any] = {} if choice is None else {"listing": choice}
    page = ok(api, alice, **params)
    assert sorted(slugs_of(page)) == expected
    served = {item["slug"]: item["list_url"] for item in page["items"]}
    assert served == {slug: LIST if slug == "listed" else None for slug in expected}
    places = api.get("/api/jobs/places", token=alice.token, params=params)
    assert places.status_code == 200
    assert places.json()["total"] == len(expected)
    assert api.get("/api/jobs/facets", token=alice.token, params=params).status_code == 200


@pytest.mark.parametrize("value", ["", "listed", "ONLY", "none", "1"])
def test_any_other_listing_is_422(api: Api, alice: Signed, value: str) -> None:
    assert listing(api, alice, listing=value).status_code == 422
    for route in ("/api/jobs/places", "/api/jobs/facets"):
        assert api.get(route, token=alice.token, params={"listing": value}).status_code == 422
