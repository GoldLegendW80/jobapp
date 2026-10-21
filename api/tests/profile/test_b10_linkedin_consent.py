"""The LinkedIn consent cannot be forged through the profile (browser B10, review round 2, B1).

Only PUT /api/browser/linkedin records the acceptance and turns the switch on. A profile save
(PUT /api/profile/search), an import (POST /api/profile/import, the CLI) and an ingestion apply
keep the stored `enabled`, `accepted_at` and `text_version` of the linkedin entry, off and none
when nothing is stored, and still save every other field."""

import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.browser import linkedin_guard as guard
from app.identity.models import User
from app.profile import schemas
from app.profile import service as profile_service
from app.profile.schemas import SECTIONS
from tests.identity.api_helpers import Api
from tests.ingestion.test_item_proposals import accept, made
from tests.profile.conftest import ALICE, BOB, full

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
FORGED = {
    "kind": "linkedin",
    "query": "x",
    "enabled": True,
    "accepted_at": "2026-01-01T00:00:00+00:00",
    "text_version": guard.TEXT_VERSION,
}


def user_id(factory: sessionmaker[Session], email: str) -> uuid.UUID:
    with factory() as db:
        return db.execute(sa.select(User.id).where(User.email == email)).scalar_one()


def profile(api: Api, token: str) -> dict[str, Any]:
    got = api.get("/api/profile", token=token)
    assert got.status_code == 200, got.text
    return got.json()


def put_search(api: Api, token: str, search: dict[str, Any]) -> Any:
    revision = profile(api, token)["revision"]
    return api.request(
        "PUT",
        "/api/profile/search",
        token=token,
        json={"data": search, "base_revision": revision},
    )


def linkedin_entry(api: Api, token: str) -> dict[str, Any] | None:
    search = profile(api, token)["sections"]["search"] or {}
    return next((s for s in search.get("browser_sources") or [] if s["kind"] == "linkedin"), None)


def turn_on(api: Api, token: str, query: str = "AI Engineer") -> dict[str, Any]:
    got = api.request(
        "PUT",
        "/api/browser/linkedin",
        token=token,
        json={"enabled": True, "query": query, "accept_version": guard.TEXT_VERSION},
    )
    assert got.status_code == 200, got.text
    return got.json()


def allowed(factory: sessionmaker[Session], email: str) -> bool:
    with factory() as db:
        return guard.allowed(db, user_id(factory, email))


def test_a_forged_profile_put_records_no_acceptance(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    got = put_search(api, alice, {**full("search"), "browser_sources": [FORGED]})
    assert got.status_code == 200, got.text
    entry = linkedin_entry(api, alice)
    assert entry is not None
    assert (entry["enabled"], entry["accepted_at"], entry["text_version"]) == (False, None, None)
    assert entry["query"] == "x"  # the words are the person's to edit
    assert allowed(factory, ALICE) is False
    card = api.get("/api/browser/linkedin", token=alice).json()
    assert (card["enabled"], card["accepted"], card["allowed"]) == (False, False, False)


def test_a_forged_put_over_an_acceptance_keeps_the_stored_one(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    accepted_at = turn_on(api, alice)["accepted_at"]
    forged = {**FORGED, "accepted_at": "2030-01-01T00:00:00+00:00", "enabled": False}
    assert put_search(api, alice, {**full("search"), "browser_sources": [forged]}).is_success
    entry = linkedin_entry(api, alice)
    assert entry is not None
    # The switch is kept on, the acceptance is the one PUT /api/browser/linkedin recorded.
    assert (entry["enabled"], entry["accepted_at"]) == (True, accepted_at)
    assert allowed(factory, ALICE) is True


def test_enabling_through_the_profile_put_stays_off(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    turn_on(api, alice)
    off = api.request("PUT", "/api/browser/linkedin", token=alice, json={"enabled": False})
    assert off.json()["enabled"] is False
    entry = linkedin_entry(api, alice)
    assert entry is not None
    assert put_search(
        api, alice, {**full("search"), "browser_sources": [{**entry, "enabled": True}]}
    ).is_success
    after = linkedin_entry(api, alice)
    assert after is not None
    assert after["enabled"] is False
    assert after["accepted_at"] == entry["accepted_at"]
    assert allowed(factory, ALICE) is False


def test_an_import_carrying_an_acceptance_does_not_record_it(
    api: Api, alice: str, bob: str, factory: sessionmaker[Session]
) -> None:
    # Bob accepted and exported; Alice imports Bob's file.
    turn_on(api, bob)
    exported = api.get("/api/profile/export", token=bob)
    assert exported.status_code == 200
    document = exported.json()
    document["sections"] = {
        name: document["sections"].get(name) or schemas.SECTION_MODELS[name]().model_dump()
        for name in SECTIONS
    }
    got = api.post("/api/profile/import", token=alice, json=document)
    assert got.status_code == 200, got.text
    entry = linkedin_entry(api, alice)
    assert entry is not None
    assert (entry["enabled"], entry["accepted_at"], entry["text_version"]) == (False, None, None)
    assert entry["query"] == "AI Engineer"
    assert allowed(factory, ALICE) is False
    assert allowed(factory, BOB) is True
    # A file with the switch off and no acceptance never turns off a stored acceptance either:
    # the import keeps what Alice herself recorded.
    turn_on(api, alice)
    accepted_at = linkedin_entry(api, alice)["accepted_at"]  # type: ignore[index]
    sections = {name: profile(api, alice)["sections"][name] for name in SECTIONS}
    sections = {
        name: value or schemas.SECTION_MODELS[name]().model_dump(mode="json")
        for name, value in sections.items()
    }
    sections["search"]["browser_sources"] = [
        {"kind": "linkedin", "query": "Other words", "enabled": False}
    ]
    again = api.post("/api/profile/import", token=alice, json={**document, "sections": sections})
    assert again.status_code == 200, again.text
    entry = linkedin_entry(api, alice)
    assert entry is not None
    assert (entry["enabled"], entry["accepted_at"], entry["query"]) == (
        True,
        accepted_at,
        "Other words",
    )


def test_the_editors_round_trip_saves_the_other_fields_and_keeps_the_acceptance(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    put_search(api, alice, full("search"))
    turn_on(api, alice)
    search = profile(api, alice)["sections"]["search"]
    before = linkedin_entry(api, alice)
    changed = {**search, "min_fit": 55, "days": 12}
    changed["browser_sources"] = [
        {**s, "location": "Paris"} if s["kind"] == "linkedin" else s
        for s in search["browser_sources"]
    ]
    got = put_search(api, alice, changed)
    assert got.status_code == 200, got.text
    after = profile(api, alice)["sections"]["search"]
    assert (after["min_fit"], after["days"]) == (55, 12)
    entry = linkedin_entry(api, alice)
    assert entry == {**before, "location": "Paris"}  # type: ignore[dict-item]
    assert allowed(factory, ALICE) is True
    # Removing the entry still turns LinkedIn off.
    assert put_search(api, alice, {**after, "browser_sources": []}).is_success
    assert linkedin_entry(api, alice) is None
    assert allowed(factory, ALICE) is False


def test_an_ingestion_apply_keeps_the_entry_as_stored(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    put_search(api, alice, full("search"))
    turn_on(api, alice)
    before = linkedin_entry(api, alice)
    alice_id = user_id(factory, ALICE)
    pid = made(factory, alice_id, "search", "never_apply", "No relocation.")
    accept(factory, alice_id, pid)
    after = profile(api, alice)["sections"]["search"]
    assert "No relocation." in after["never_apply"]
    assert linkedin_entry(api, alice) == before
    assert allowed(factory, ALICE) is True


def test_the_service_keeps_consent_unless_the_linkedin_route_asks(
    api: Api, alice: str, factory: sessionmaker[Session]
) -> None:
    api.get("/api/profile", token=alice)
    alice_id = user_id(factory, ALICE)
    data = schemas.validate_section("search", {"browser_sources": [FORGED]})
    with factory() as db, db.begin():
        rev = profile_service.get_profile(db, alice_id, NOW).revision
        saved = profile_service.save_section(db, alice_id, "search", data, rev, NOW)
        entry = saved.sections["search"]["browser_sources"][0]
        assert (entry["enabled"], entry["accepted_at"]) == (False, None)
        saved = profile_service.save_section(
            db, alice_id, "search", data, saved.revision, NOW, linkedin_consent=True
        )
        assert saved.sections["search"]["browser_sources"][0]["enabled"] is True
