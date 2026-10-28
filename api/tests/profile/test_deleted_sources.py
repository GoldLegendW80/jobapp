"""A profile an older release stored with what was deleted on 2026-10-28 (owner: "Delete it"):
the company job boards (`search.boards`), the freelance feeds (`search.feeds`), the fixed browser
readers (browser_sources rows of kind google_careers, vie and site) and the starter list's handled
entries (`preferences.onboarding.starter_handled`). Such a profile still reads, GET /api/profile
leaves them out, and a save, even one an older tab sends with them, stores the section without
them and is not refused. Invented data only."""

import copy
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.identity.models import User
from app.profile.models import Profile
from tests.identity.api_helpers import Api
from tests.profile.conftest import full, signed_in
from tests.profile.test_api import read, save_ok

EMAIL = "e2e-deleted-sources@example.org"
OLD_BOARD = {
    "company": "Example Robotics",
    "ats": "ashby",
    "org": "example-robotics",
    "enabled": True,
    "extra_exclude": {"terms": [], "advanced_regex": False},
}
OLD_FEED = {"kind": "remoteok", "terms": {"terms": ["python"], "advanced_regex": False}}
OLD_READERS = [
    {"kind": "google_careers", "query": "ai engineer", "location": "Paris", "enabled": True},
    {"kind": "vie", "query": "data", "location": "Germany", "enabled": False},
    {"kind": "site", "query": "https://careers.example.org/", "enabled": True},
]


def old_search() -> dict[str, Any]:
    data = full("search")
    data["boards"] = [OLD_BOARD]
    data["feeds"] = [OLD_FEED]
    data["browser_sources"] = [*OLD_READERS, *data["browser_sources"]]
    return data


def old_preferences() -> dict[str, Any]:
    data = full("preferences")
    data["onboarding"]["starter_handled"] = ["ashby:example-robotics"]
    return data


@pytest.fixture
def owner(factory: sessionmaker[Session], api: Api) -> tuple[str, uuid.UUID]:
    token = signed_in(factory, api, EMAIL)
    with factory() as db:
        user_id = db.execute(sa.select(User.id).where(User.email == EMAIL)).scalar_one()
    return token, user_id


def store(factory: sessionmaker[Session], user_id: uuid.UUID, **sections: Any) -> None:
    """The sections as an older release stored them, straight in the database."""
    with factory() as db, db.begin():
        db.add(Profile(user_id=user_id, sections=sections, revision=1))


def stored(factory: sessionmaker[Session], user_id: uuid.UUID) -> dict[str, Any]:
    with factory() as db:
        profile = db.get(Profile, user_id)
        assert profile is not None
        return copy.deepcopy(profile.sections)


def test_get_leaves_the_deleted_keys_out(
    api: Api, factory: sessionmaker[Session], owner: tuple[str, uuid.UUID]
) -> None:
    token, user_id = owner
    store(factory, user_id, search=old_search(), preferences=old_preferences())
    body = read(api, token)
    assert body["sections"]["search"] == full("search")
    assert body["sections"]["preferences"] == full("preferences")
    assert "boards" not in body["sections"]["search"]
    assert "feeds" not in body["sections"]["search"]
    assert "starter_handled" not in body["sections"]["preferences"]["onboarding"]


def test_a_save_stores_the_search_without_them(
    api: Api, factory: sessionmaker[Session], owner: tuple[str, uuid.UUID]
) -> None:
    token, user_id = owner
    store(factory, user_id, search=old_search())
    data = read(api, token)["sections"]["search"]
    data["min_fit"] = 55
    body = save_ok(api, token, "search", data, 1)
    assert body["sections"]["search"]["min_fit"] == 55
    search = stored(factory, user_id)["search"]
    assert "boards" not in search
    assert "feeds" not in search
    assert [row["kind"] for row in search["browser_sources"]] == ["freework"]


def test_an_older_tab_sending_them_is_not_refused(
    api: Api, factory: sessionmaker[Session], owner: tuple[str, uuid.UUID]
) -> None:
    token, user_id = owner
    body = save_ok(api, token, "search", old_search(), 0)
    assert body["sections"]["search"] == full("search")
    assert stored(factory, user_id)["search"] == full("search")


def test_a_preferences_save_with_the_starter_list_is_not_a_reserved_change(
    api: Api, factory: sessionmaker[Session], owner: tuple[str, uuid.UUID]
) -> None:
    token, user_id = owner
    store(factory, user_id, preferences=old_preferences())
    data = old_preferences()
    data["documents_tone"] = "formal"
    body = save_ok(api, token, "preferences", data, 1)
    assert body["sections"]["preferences"]["documents_tone"] == "formal"
    onboarding = stored(factory, user_id)["preferences"]["onboarding"]
    assert "starter_handled" not in onboarding
    # A fresh profile too: the field is dropped, not compared with the stored onboarding.
    other = signed_in(factory, api, "e2e-deleted-sources-2@example.org")
    assert save_ok(api, other, "preferences", old_preferences(), 0)["revision"] == 1
