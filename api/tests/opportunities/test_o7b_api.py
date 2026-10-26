"""O7b Free-Work consent, exercised through real routes and profile persistence.

All accounts and search words are invented. No platform page is contacted here.
"""

from typing import Any

import pytest

from app.profile import schemas
from tests.identity.api_helpers import Api
from tests.opportunities.conftest import Person

PATH = "/api/opportunities/platforms/freework"
TEXT = (
    "This site's terms forbid automated use; your account may be limited or closed; "
    "the app keeps to a person's pace, but the risk is yours."
)
FORGED = {
    "kind": "freework",
    "query": "Robotics Engineer",
    "enabled": True,
    "accepted_at": "2026-01-01T00:00:00+00:00",
    "text_version": 1,
}


def profile(api: Api, who: Person) -> dict[str, Any]:
    response = api.get("/api/profile", token=who.token)
    assert response.status_code == 200, response.text
    return response.json()


def entry(api: Api, who: Person, kind: str = "freework") -> dict[str, Any] | None:
    search = profile(api, who)["sections"]["search"] or {}
    return next(
        (source for source in search.get("browser_sources", []) if source["kind"] == kind), None
    )


def put(api: Api, who: Person, **body: Any) -> Any:
    return api.request("PUT", PATH, token=who.token, json=body)


def search_save(api: Api, who: Person, search: dict[str, Any], revision: int | None = None) -> Any:
    current = profile(api, who)
    return api.request(
        "PUT",
        "/api/profile/search",
        token=who.token,
        json={
            "data": search,
            "base_revision": current["revision"] if revision is None else revision,
        },
    )


def accept(api: Api, who: Person) -> dict[str, Any]:
    response = put(api, who, enabled=True, accept_version=1, query="Robotics Engineer")
    assert response.status_code == 200, response.text
    return response.json()


def test_default_off_requires_current_acceptance_and_records_server_time(
    api: Api, person_a: Person, person_b: Person, clock: Any
) -> None:
    response = api.get(PATH, token=person_a.token)
    assert response.status_code == 200, response.text
    state = response.json()
    assert (state["kind"], state["name"]) == ("freework", "Free-Work")
    assert (state["enabled"], state["accepted"], state["allowed"]) == (False, False, False)
    assert state["text"] == TEXT
    assert state["text_version"] == 1
    assert state["accepted_at"] is None
    assert state["paused_until"] is None
    assert response.headers["cache-control"] == "no-store"
    assert put(api, person_a, enabled=True, query="Robotics Engineer").status_code == 409
    assert (
        put(api, person_a, enabled=True, query="Robotics Engineer", accept_version=2).status_code
        == 409
    )
    accepted = accept(api, person_a)
    assert (accepted["enabled"], accepted["accepted"], accepted["allowed"]) == (True, True, True)
    assert accepted["accepted_at"] == clock().isoformat()
    assert accepted["accepted_version"] == 1
    assert put(api, person_a, enabled=False).json()["enabled"] is False
    resumed = put(api, person_a, enabled=True)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["accepted_at"] == accepted["accepted_at"]
    other = api.get(PATH, token=person_b.token).json()
    assert (other["enabled"], other["accepted"], other["allowed"]) == (False, False, False)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("accepted_at", "2030-01-01T00:00:00+00:00"),
        ("text_version", 1),
        ("user_id", "00000000-0000-0000-0000-000000000001"),
        ("paused_until", None),
    ],
)
def test_dedicated_route_refuses_forged_protected_fields(
    api: Api, person_a: Person, field: str, value: Any
) -> None:
    response = put(
        api, person_a, enabled=True, accept_version=1, query="Robotics Engineer", **{field: value}
    )
    assert response.status_code == 422, response.text
    assert entry(api, person_a) is None


def test_generic_profile_save_keeps_unaccepted_platform_off(api: Api, person_a: Person) -> None:
    search = profile(api, person_a)["sections"]["search"]
    response = search_save(api, person_a, {**search, "browser_sources": [FORGED]})
    assert response.status_code == 200, response.text
    saved = entry(api, person_a)
    assert saved is not None
    assert (saved["enabled"], saved["accepted_at"], saved["text_version"]) == (False, None, None)
    assert saved["query"] == FORGED["query"]


def test_generic_save_preserves_later_off_and_rejects_stale_revision(
    api: Api, person_a: Person
) -> None:
    accepted = accept(api, person_a)
    before = profile(api, person_a)
    assert put(api, person_a, enabled=False).status_code == 200
    stale = search_save(api, person_a, before["sections"]["search"], before["revision"])
    assert stale.status_code == 409, stale.text
    current = profile(api, person_a)["sections"]["search"]
    response = search_save(api, person_a, {**current, "min_fit": 45, "browser_sources": [FORGED]})
    assert response.status_code == 200, response.text
    saved = entry(api, person_a)
    assert saved is not None
    assert (saved["enabled"], saved["accepted_at"]) == (False, accepted["accepted_at"])
    assert profile(api, person_a)["sections"]["search"]["min_fit"] == 45


def test_import_cannot_transfer_another_persons_acceptance(
    api: Api, person_a: Person, person_b: Person
) -> None:
    accept(api, person_b)
    document = api.get("/api/profile/export", token=person_b.token).json()
    document["sections"] = {
        name: value or schemas.SECTION_MODELS[name]().model_dump(mode="json")
        for name, value in document["sections"].items()
    }
    response = api.post("/api/profile/import", token=person_a.token, json=document)
    assert response.status_code == 200, response.text
    saved = entry(api, person_a)
    assert saved is not None
    assert (saved["enabled"], saved["accepted_at"], saved["text_version"]) == (False, None, None)
    assert api.get(PATH, token=person_b.token).json()["allowed"] is True


def test_switch_route_preserves_linkedin_and_other_sources(api: Api, person_a: Person) -> None:
    linkedin = api.request(
        "PUT",
        "/api/browser/linkedin",
        token=person_a.token,
        json={"enabled": True, "query": "Robotics Engineer", "accept_version": 1},
    )
    assert linkedin.status_code == 200, linkedin.text
    before = entry(api, person_a, "linkedin")
    accept(api, person_a)
    assert entry(api, person_a, "linkedin") == before
    assert put(api, person_a, enabled=False).status_code == 200
    assert entry(api, person_a, "linkedin") == before


def test_linkedin_route_and_trusted_save_preserve_freework_consent(
    api: Api,
    person_a: Person,
    factory: Any,
    clock: Any,
) -> None:
    from app.profile import service

    accepted = accept(api, person_a)
    assert put(api, person_a, enabled=False).status_code == 200
    response = api.request(
        "PUT",
        "/api/browser/linkedin",
        token=person_a.token,
        json={"enabled": True, "query": "Robotics Engineer", "accept_version": 1},
    )
    assert response.status_code == 200, response.text
    with factory() as db, db.begin():
        current = service.get_profile(db, person_a.user_id, clock())
        search = {**current.sections["search"], "browser_sources": [FORGED]}
        data = schemas.validate_section("search", search)
        saved = service.save_section(
            db, person_a.user_id, "search", data, current.revision, clock(), linkedin_consent=True
        )
        source = saved.sections["search"]["browser_sources"][0]
        assert source["enabled"] is False
        assert source["accepted_at"] == accepted["accepted_at"]


def test_route_needs_own_session_and_csrf(api: Api, person_a: Person) -> None:
    assert api.get(PATH).status_code == 401
    assert api.request("PUT", PATH, json={"enabled": False}).status_code == 401
    assert (
        api.request(
            "PUT", PATH, token=person_a.token, csrf=False, json={"enabled": False}
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "kind", ["malt", "upwork", "freelancer", "codeur", "comet", "cremedelacreme", "unknown"]
)
def test_unimplemented_platforms_remain_unavailable(api: Api, person_a: Person, kind: str) -> None:
    path = f"/api/opportunities/platforms/{kind}"
    assert api.get(path, token=person_a.token).status_code == 404
    assert (
        api.request(
            "PUT", path, token=person_a.token, json={"enabled": True, "accept_version": 1}
        ).status_code
        == 404
    )
