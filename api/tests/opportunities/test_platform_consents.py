"""Additional platform consent is owned by one authenticated, dedicated writer per entry."""

import json
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

import pytest

from app.opportunities.platforms import guard
from app.profile import schemas, service
from app.profile.models import Profile
from tests.identity.api_helpers import Api
from tests.jobs.test_status_lock import join_all, run_in_thread, wait_until_blocked
from tests.opportunities.conftest import Person
from tests.opportunities.test_o7b_api import accept as accept_freework
from tests.opportunities.test_o7b_api import entry, profile, search_save
from tests.opportunities.test_o7b_guard import history
from tests.opportunities.test_o7b_profile_preservation import document, snapshot, write
from tests.profile.test_cli import run_cli

KINDS = ("indeed", "welcome_to_the_jungle")
TEXT = (
    "Automated browsing may result in account restrictions or closure. "
    "Enable it only if you accept this risk."
)
FORGED = {
    "enabled": True,
    "accepted_at": "2030-01-01T00:00:00+00:00",
    "text_version": 1,
}


def path(kind: str) -> str:
    return f"/api/opportunities/platforms/{kind}"


def put(api: Api, who: Person, kind: str, **body: Any) -> Any:
    return api.request("PUT", path(kind), token=who.token, json=body)


def accept(api: Api, who: Person, kind: str) -> dict[str, Any]:
    response = put(api, who, kind, enabled=True, accept_version=1)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


@pytest.mark.parametrize(
    ("kind", "name"), [("indeed", "Indeed"), ("welcome_to_the_jungle", "Welcome to the Jungle")]
)
def test_default_off_current_acceptance_server_time_and_person_isolation(
    api: Api, person_a: Person, person_b: Person, clock: Any, kind: str, name: str
) -> None:
    response = api.get(path(kind), token=person_a.token)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    state = response.json()
    assert (state["kind"], state["name"]) == (kind, name)
    assert (state["enabled"], state["accepted"], state["allowed"]) == (False, False, False)
    assert (state["accepted_at"], state["accepted_version"]) == (None, None)
    assert (state["query"], state["location"], state["terms_url"], state["checked_on"]) == (
        None,
    ) * 4
    assert state["text_version"] == 1
    assert state["text"] == TEXT
    assert put(api, person_a, kind, enabled=False).status_code == 200
    assert profile(api, person_a)["sections"]["search"].get("platform_consents", {}) == {}
    assert put(api, person_a, kind, enabled=True).status_code == 409
    assert put(api, person_a, kind, enabled=True, accept_version=2).status_code == 409
    accepted = accept(api, person_a, kind)
    assert (accepted["enabled"], accepted["accepted"], accepted["allowed"]) == (True, True, True)
    assert accepted["accepted_at"] == clock().isoformat()
    assert accepted["accepted_version"] == 1
    assert put(api, person_a, kind, enabled=False).json()["enabled"] is False
    resumed = put(api, person_a, kind, enabled=True).json()
    assert resumed["accepted_at"] == accepted["accepted_at"]
    other = api.get(path(kind), token=person_b.token).json()
    assert (other["enabled"], other["accepted"], other["allowed"]) == (False, False, False)
    search = profile(api, person_a)["sections"]["search"]
    assert search["platform_consents"] == {
        kind: {"enabled": True, "accepted_at": clock().isoformat(), "text_version": 1}
    }
    assert search["browser_sources"] == []
    assert search.get("interactive_sites", []) == []


@pytest.mark.parametrize("kind", KINDS)
def test_stale_stored_acceptance_requires_current_explicit_acceptance(
    api: Api, person_a: Person, factory: Any, clock: Any, kind: str
) -> None:
    accepted = accept(api, person_a, kind)
    with factory() as db, db.begin():
        stored = db.get(Profile, person_a.user_id)
        sections = deepcopy(stored.sections)
        sections["search"]["platform_consents"][kind]["text_version"] = 2
        stored.sections = sections
    stale = api.get(path(kind), token=person_a.token).json()
    assert (stale["enabled"], stale["accepted"], stale["allowed"]) == (True, False, False)
    assert put(api, person_a, kind, enabled=True).status_code == 409
    assert put(api, person_a, kind, enabled=True, accept_version=2).status_code == 409
    clock.advance(minutes=1)
    current = accept(api, person_a, kind)
    assert current["accepted_at"] == clock().isoformat() != accepted["accepted_at"]
    assert current["accepted_version"] == 1


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "body",
    [
        {"enabled": "true"},
        {"enabled": 1},
        {"enabled": True, "accept_version": True},
        {"enabled": True, "accept_version": "1"},
        {"enabled": True, "accept_version": 0},
        {"enabled": True, "accept_version": 1001},
        {"enabled": False, "query": ""},
        {"enabled": False, "query": "Robotics"},
        {"enabled": False, "location": ""},
        {"enabled": False, "location": "Paris"},
        {"enabled": False, "accepted_at": FORGED["accepted_at"]},
        {"enabled": False, "text_version": 1},
        {"enabled": False, "user_id": "other"},
        {"enabled": False, "paused_until": None},
    ],
)
def test_new_routes_refuse_legacy_inputs_and_forged_fields(
    api: Api, person_a: Person, factory: Any, kind: str, body: dict[str, Any]
) -> None:
    before = snapshot(factory, person_a)
    response = api.request("PUT", path(kind), token=person_a.token, json=body)
    assert response.status_code == 422, response.text
    assert snapshot(factory, person_a) == before


@pytest.mark.parametrize("kind", KINDS)
def test_new_routes_require_session_and_csrf(api: Api, person_a: Person, kind: str) -> None:
    assert api.get(path(kind)).status_code == 401
    assert api.request("PUT", path(kind), json={"enabled": False}).status_code == 401
    assert (
        api.request(
            "PUT", path(kind), token=person_a.token, csrf=False, json={"enabled": False}
        ).status_code
        == 403
    )
    assert put(api, person_a, kind, enabled=False, query=None, location=None).status_code == 200


@pytest.mark.parametrize("method", ["save", "import", "cli"])
@pytest.mark.parametrize("kind", KINDS)
def test_generic_writes_keep_map_off_and_interactive_omission_or_explicit_clear(
    api: Api,
    person_a: Person,
    person_b: Person,
    factory: Any,
    migrated_url: str,
    kind: str,
    method: str,
) -> None:
    chosen = [
        {
            "platform": "custom",
            "url": "https://careers.example.org/jobs",
            "enabled": True,
            "mode": "llm_browser",
        }
    ]
    current = profile(api, person_a)
    assert (
        search_save(
            api,
            person_a,
            {
                **current["sections"]["search"],
                "interactive_sites": chosen,
                "interactive_instructions": "Read requirements.",
            },
        ).status_code
        == 200
    )
    accept(api, person_b, kind)
    # An unconsented account cannot import or save another person's authority.
    current = profile(api, person_a)
    forged = {**current["sections"]["search"], "platform_consents": {kind: FORGED}}
    response = write(
        api, person_a, "import" if method == "cli" else method, forged, current["revision"]
    )
    assert response.status_code == 200, response.text
    assert profile(api, person_a)["sections"]["search"]["platform_consents"] == {}
    accept(api, person_a, kind)
    assert put(api, person_a, kind, enabled=False).status_code == 200
    expected = deepcopy(profile(api, person_a)["sections"]["search"]["platform_consents"])
    for change in (None, {}, {kind: FORGED}):
        for clear in (False, True):
            current = profile(api, person_a)
            search = deepcopy(current["sections"]["search"])
            for key in ("platform_consents", "interactive_sites", "interactive_instructions"):
                search.pop(key, None)
            if change is not None:
                search["platform_consents"] = change
            if clear:
                search.update(interactive_sites=[], interactive_instructions=None)
            else:
                # Restore explicit intent before testing omission each time.
                assert (
                    search_save(
                        api,
                        person_a,
                        {
                            **current["sections"]["search"],
                            "interactive_sites": chosen,
                            "interactive_instructions": "Read requirements.",
                        },
                    ).status_code
                    == 200
                )
                current = profile(api, person_a)
            if method == "cli":
                result = run_cli(
                    migrated_url,
                    "import",
                    "--email",
                    person_a.email,
                    "--file",
                    "-",
                    stdin=json.dumps(document(api, person_a, search, current["revision"])).encode(),
                )
                assert result.returncode == 0, result.stderr
            else:
                response = write(api, person_a, method, search, current["revision"])
                assert response.status_code == 200, response.text
            saved = profile(api, person_a)["sections"]["search"]
            assert saved["platform_consents"] == expected
            assert saved["interactive_sites"] == ([] if clear else chosen)
            assert saved["interactive_instructions"] == (None if clear else "Read requirements.")
    assert api.get(path(kind), token=person_b.token).json()["enabled"] is True


def test_dedicated_writers_are_isolated_and_keep_legacy_contracts(
    api: Api, person_a: Person, factory: Any, clock: Any
) -> None:
    freework = accept_freework(api, person_a)
    assert (freework["terms_url"], freework["checked_on"]) == (
        "https://www.free-work.com/fr/terms",
        "2026-10-20",
    )
    before = entry(api, person_a)
    accept(api, person_a, "indeed")
    accept(api, person_a, "welcome_to_the_jungle")
    expected = deepcopy(profile(api, person_a)["sections"]["search"]["platform_consents"])
    for trusted in ("indeed", "welcome_to_the_jungle", "freework", "linkedin"):
        with factory() as db, db.begin():
            current = service.get_profile(db, person_a.user_id, clock())
            search = deepcopy(current.sections["search"])
            search["platform_consents"] = {kind: {**FORGED, "enabled": False} for kind in KINDS}
            kwargs = (
                {"linkedin_consent": True}
                if trusted == "linkedin"
                else {"platform_consent": trusted}
            )
            saved = service.save_section(
                db,
                person_a.user_id,
                "search",
                schemas.validate_section("search", search),
                current.revision,
                clock(),
                **kwargs,
            )
            for kind in KINDS:
                assert saved.sections["search"]["platform_consents"][kind] == (
                    search["platform_consents"][kind] if kind == trusted else expected[kind]
                )
            expected = deepcopy(saved.sections["search"]["platform_consents"])
    assert entry(api, person_a) == before
    with factory() as db:
        current = service.get_profile(db, person_a.user_id, clock())
        with pytest.raises(ValueError, match="one dedicated consent"):
            service.save_section(
                db,
                person_a.user_id,
                "search",
                current.sections["search"],
                current.revision,
                clock(),
                linkedin_consent=True,
                platform_consent="indeed",
            )


@pytest.mark.parametrize("kind", KINDS)
def test_seeded_history_is_person_and_kind_bound_and_survives_reacceptance(
    api: Api, person_a: Person, person_b: Person, factory: Any, clock: Any, kind: str
) -> None:
    accept(api, person_a, kind)
    accept(api, person_b, kind)
    met = clock() - timedelta(days=1)
    history(
        factory,
        person_a,
        met,
        kind=kind,
        opened_at=met.isoformat(),
        checkpoint_at=met.isoformat(),
        error=f"{kind}_signin",
    )
    for enabled in (False, True):
        response = put(api, person_a, kind, enabled=enabled, accept_version=1)
        assert response.status_code == 200, response.text
        state = response.json()
        assert state["allowed"] is False
        assert datetime.fromisoformat(state["paused_until"]) == met + timedelta(days=7)
        assert datetime.fromisoformat(state["last_sweep_at"]) == met
        assert state["needs_signin"] is True
    for now, paused in (
        (met + timedelta(days=7, seconds=-1), True),
        (met + timedelta(days=7), False),
    ):
        with factory() as db:
            assert (guard.state(db, person_a.user_id, kind, now).paused_until is not None) is paused
    assert api.get(path(kind), token=person_b.token).json()["allowed"] is True
    other = next(value for value in KINDS if value != kind)
    assert api.get(path(other), token=person_a.token).json()["paused_until"] is None


@pytest.mark.parametrize("method", ["save", "import"])
def test_held_off_update_makes_waiting_generic_write_stale(
    api: Api, person_a: Person, factory: Any, clock: Any, method: str
) -> None:
    accept(api, person_a, "indeed")
    old = profile(api, person_a)
    errors: list[BaseException] = []
    result: list[Any] = []
    thread = None
    with factory() as holder:
        search = deepcopy(old["sections"]["search"])
        search["platform_consents"]["indeed"]["enabled"] = False
        service.save_section(
            holder,
            person_a.user_id,
            "search",
            schemas.validate_section("search", search),
            old["revision"],
            clock(),
            platform_consent="indeed",
        )
        try:
            thread = run_in_thread(
                errors,
                lambda: result.append(
                    write(api, person_a, method, old["sections"]["search"], old["revision"])
                ),
            )
            wait_until_blocked(factory)
            holder.commit()
        finally:
            holder.rollback()
            if thread is not None:
                join_all(thread)
    assert errors == []
    assert result[0].status_code == 409, result[0].text
    saved = snapshot(factory, person_a)
    assert saved[1] == old["revision"] + 1
    assert saved[0]["search"]["platform_consents"]["indeed"]["enabled"] is False
