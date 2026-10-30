"""Company boards through the app: the list saved with the search section (PUT
/api/profile/search: stored, kept by a save that leaves it out, cleared by an empty list), the
starter list, a pasted address detected, suggestions from the person's own postings, a search
started with boards only, isolation and sign in. Nothing reaches an ATS here."""

import uuid
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.jobs.models import Job, JobStatus
from tests.identity.api_helpers import Api
from tests.jobs.sweep_helpers import search, set_profile
from tests.jobs.test_api import Signed, sign_in
from tests.jobs.test_sweep_api import FakeQueue, fake_queue, start

__all__ = ["fake_queue"]  # the fixture, used by name

LABS = {"company": "Example Labs", "ats": "greenhouse", "board": "examplelabs", "enabled": True}


@pytest.fixture
def alice(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "alice@example.org")


@pytest.fixture
def bob(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "bob@example.org")


def stored(api: Api, who: Signed) -> dict[str, Any]:
    got = api.get("/api/profile", token=who.token)
    assert got.status_code == 200, got.text
    return got.json()


def put_search(api: Api, who: Signed, data: dict[str, Any]) -> Any:
    revision = stored(api, who)["revision"]
    return api.request(
        "PUT",
        "/api/profile/search",
        token=who.token,
        json={"data": data, "base_revision": revision},
    )


def test_the_list_is_saved_kept_when_left_out_and_cleared_by_an_empty_list(
    api: Api, alice: Signed
) -> None:
    section = search(interactive_sites=[])
    first = put_search(api, alice, section)
    assert first.status_code == 200, first.text
    assert stored(api, alice)["sections"]["search"].get("company_boards") is None  # never saved
    assert put_search(api, alice, {**section, "company_boards": [LABS]}).status_code == 200
    assert stored(api, alice)["sections"]["search"]["company_boards"] == [LABS]
    # An editor that does not know the field (the Search tab) keeps it.
    assert put_search(api, alice, section).status_code == 200
    assert stored(api, alice)["sections"]["search"]["company_boards"] == [LABS]
    assert put_search(api, alice, {**section, "company_boards": []}).status_code == 200
    assert stored(api, alice)["sections"]["search"]["company_boards"] == []


def test_an_explicit_null_is_never_stored(api: Api, alice: Signed) -> None:
    section = search(interactive_sites=[])
    assert put_search(api, alice, {**section, "company_boards": [LABS]}).status_code == 200
    # Null means never saved: the key is dropped, not stored as null.
    assert put_search(api, alice, {**section, "company_boards": None}).status_code == 200
    assert "company_boards" not in stored(api, alice)["sections"]["search"]
    assert put_search(api, alice, {**section, "company_boards": None}).status_code == 200
    assert "company_boards" not in stored(api, alice)["sections"]["search"]


@pytest.mark.parametrize(
    "boards",
    [
        [{**LABS, "board": "../admin"}],
        [{**LABS, "ats": "teamtailor"}],
        [LABS, {**LABS, "board": "ExampleLabs"}],
        [{**LABS, "board": f"b{i}"} for i in range(101)],
    ],
)
def test_a_wrong_list_is_422(api: Api, alice: Signed, boards: list[dict[str, Any]]) -> None:
    answer = put_search(api, alice, {**search(), "company_boards": boards})
    assert answer.status_code == 422, answer.text


def test_the_starter_list(api: Api, alice: Signed) -> None:
    got = api.get("/api/jobs/boards/starter", token=alice.token)
    assert got.status_code == 200
    body = got.json()
    assert len(body["boards"]) == 33
    openai = next(b for b in body["boards"] if b["board"] == "openai")
    assert openai == {
        "company": "OpenAI",
        "ats": "ashby",
        "board": "openai",
        "enabled": True,
        "url": "https://jobs.ashbyhq.com/openai",
    }


def test_detect_a_pasted_address(api: Api, alice: Signed) -> None:
    got = api.post(
        "/api/jobs/boards/detect",
        token=alice.token,
        json={"url": "https://jobs.ashbyhq.com/example-robotics/1234"},
    )
    assert got.status_code == 200, got.text
    assert got.json() == {
        "company": "Example Robotics",
        "ats": "ashby",
        "board": "example-robotics",
        "enabled": True,
        "url": "https://jobs.ashbyhq.com/example-robotics",
    }
    refused = api.post(
        "/api/jobs/boards/detect", token=alice.token, json={"url": "https://example.org/jobs"}
    )
    assert refused.status_code == 422
    assert "Ashby" in refused.json()["detail"]
    eu = api.post(
        "/api/jobs/boards/detect",
        token=alice.token,
        json={"url": "https://job-boards.eu.greenhouse.io/examplelabs"},
    )
    assert eu.status_code == 422
    assert "European" in eu.json()["detail"]
    for detail in (refused.json()["detail"], eu.json()["detail"]):
        assert not any(dash in detail for dash in (" - ", "\u2014", "\u2013"))


def add_job(factory: sessionmaker[Session], user: uuid.UUID, n: int, url: str, **more: Any) -> None:
    with factory() as db, db.begin():
        db.add(
            Job(
                user_id=user,
                slug=f"example-{n}",
                company=more.pop("company", "Example Robotics"),
                title=f"AI Engineer {n}",
                location="Paris",
                url=url,
                source="import",
                status=JobStatus.found,
                legacy={},
                **more,
            )
        )


def test_suggestions_come_from_the_persons_own_postings(
    api: Api, factory: sessionmaker[Session], alice: Signed, bob: Signed
) -> None:
    add_job(factory, alice.id, 1, "https://jobs.ashbyhq.com/example-robotics/1")
    add_job(factory, alice.id, 2, "https://jobs.ashbyhq.com/example-robotics/2")
    add_job(
        factory,
        alice.id,
        3,
        "https://www.example-board.test/jobs/3",
        company="Employer not stated",
        posting_text="Apply at https://jobs.lever.co/exampleworks/abc today.",
    )
    add_job(factory, alice.id, 4, "https://job-boards.greenhouse.io/examplelabs/jobs/4")
    set_profile(factory, alice.id, search(company_boards=[LABS]))
    add_job(factory, bob.id, 5, "https://jobs.ashbyhq.com/bob-only/5")
    got = api.get("/api/jobs/boards/suggestions", token=alice.token)
    assert got.status_code == 200, got.text
    assert got.headers["cache-control"] == "no-store"
    assert [(b["ats"], b["board"], b["company"], b["postings"]) for b in got.json()["boards"]] == [
        ("ashby", "example-robotics", "Example Robotics", 2),
        ("lever", "exampleworks", "Exampleworks", 1),  # no employer stated: from the id
    ]  # Example Labs is listed already; Bob's postings are his


def test_a_search_with_boards_only_starts(
    api: Api, factory: sessionmaker[Session], alice: Signed, fake_queue: FakeQueue
) -> None:
    set_profile(factory, alice.id, search(interactive_sites=[], company_boards=[LABS]))
    answer = start(api, alice)
    assert answer.status_code == 202, answer.text
    assert answer.json()["search_scope"]["source_count"] == 1
    # The board is named by its company in the sweep the api shows (web sweep-sources).
    [source] = answer.json()["sources"]
    assert (source["kind"], source["query"], source["company"]) == (
        "board", "greenhouse:examplelabs", "Example Labs",
    )  # fmt: skip


def test_signed_out_is_401(api: Api) -> None:
    assert api.get("/api/jobs/boards/starter").status_code == 401
    assert api.get("/api/jobs/boards/suggestions").status_code == 401
