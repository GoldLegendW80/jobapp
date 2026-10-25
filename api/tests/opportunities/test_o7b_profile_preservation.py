"""Generic profile writes retain Free-Work consent without exceeding the source cap."""

import json
import uuid
from copy import deepcopy
from typing import Any

import pytest
import sqlalchemy as sa

from app.profile.models import Profile, ProfileRevision
from app.profile.schemas import SearchSection
from tests.identity.api_helpers import Api
from tests.opportunities.conftest import Person
from tests.opportunities.test_o7b_api import accept, entry, profile, put, search_save
from tests.profile.test_cli import run_cli


def test_accepted_platform_is_not_selected_for_a_browser_read(api: Api, person_a: Person) -> None:
    from app.browser.sources import readers
    from app.jobs.sweep import enabled_sources

    assert accept(api, person_a)["allowed"] is True
    search = SearchSection.model_validate(profile(api, person_a)["sections"]["search"])
    assert search.browser_sources[0].enabled is True
    assert enabled_sources(search) == []
    assert "freework" not in readers()


def snapshot(factory: Any, who: Person) -> tuple[Any, ...]:
    """Read committed state in a fresh session, including every revision and section."""
    with factory() as db:
        stored = db.get(Profile, who.user_id)
        revisions = db.execute(
            sa.select(ProfileRevision.revision, ProfileRevision.section, ProfileRevision.data)
            .where(ProfileRevision.user_id == who.user_id)
            .order_by(ProfileRevision.revision)
        ).all()
        return deepcopy(
            (stored.sections, stored.revision, stored.updated_at, stored.photo, revisions)
        )


def document(api: Api, who: Person, search: dict[str, Any], revision: int) -> dict[str, Any]:
    body = api.get("/api/profile/export", token=who.token).json()
    body["sections"]["search"] = search
    body["base_revision"] = revision
    return body


def write(api: Api, who: Person, method: str, search: dict[str, Any], revision: int) -> Any:
    if method == "save":
        return search_save(api, who, search, revision)
    return api.post(
        "/api/profile/import", token=who.token, json=document(api, who, search, revision)
    )


@pytest.mark.parametrize("method", ["save", "import"])
@pytest.mark.parametrize("enabled", [False, True])
def test_omission_keeps_current_freework_and_intentional_linkedin_removal(
    api: Api, person_a: Person, factory: Any, method: str, enabled: bool
) -> None:
    accept(api, person_a)
    linkedin = api.request(
        "PUT",
        "/api/browser/linkedin",
        token=person_a.token,
        json={"enabled": True, "query": "Robotics Engineer", "accept_version": 1},
    )
    assert linkedin.status_code == 200, linkedin.text
    old = profile(api, person_a)
    assert put(api, person_a, enabled=enabled).status_code == 200
    before = snapshot(factory, person_a)
    expected = entry(api, person_a)
    omitted = {**old["sections"]["search"], "browser_sources": [], "min_fit": 45}
    stale = write(api, person_a, method, omitted, old["revision"])
    assert stale.status_code == 409, stale.text
    assert snapshot(factory, person_a) == before
    response = write(api, person_a, method, omitted, before[1])
    assert response.status_code == 200, response.text
    after = snapshot(factory, person_a)
    assert after[0]["search"]["browser_sources"] == [expected]
    assert after[0]["search"]["min_fit"] == 45
    assert after[1] == before[1] + (1 if method == "save" else 7)
    assert entry(api, person_a, "linkedin") is None


def sources(count: int, canary: str) -> list[dict[str, Any]]:
    return [
        {
            "kind": "vie",
            "query": f"{canary} {n}",
            "location": None,
            "enabled": True,
            "interactive_only": False,
            "accepted_at": None,
            "text_version": None,
        }
        for n in range(count)
    ]


@pytest.mark.parametrize("method", ["save", "import"])
@pytest.mark.parametrize(
    ("others", "included", "status"), [(9, False, 200), (9, True, 200), (10, False, 422)]
)
def test_capacity_is_checked_after_preservation_without_partial_writes(
    api: Api,
    person_a: Person,
    factory: Any,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    method: str,
    others: int,
    included: bool,
    status: int,
) -> None:
    accept(api, person_a)
    expected = entry(api, person_a)
    before = snapshot(factory, person_a)
    canary = uuid.uuid4().hex
    current = profile(api, person_a)
    search = {
        **current["sections"]["search"],
        "min_fit": 45,
        "browser_sources": sources(others, canary) + ([expected] if included else []),
    }
    response = write(api, person_a, method, search, current["revision"])
    assert response.status_code == status, response.text
    after = snapshot(factory, person_a)
    if status == 422:
        assert "browser_sources" in response.json()["detail"]
        captured = capsys.readouterr()
        assert canary not in response.text + caplog.text + captured.out + captured.err
        assert after == before
    else:
        saved = after[0]["search"]
        assert len(saved["browser_sources"]) == 10
        assert saved["browser_sources"] == search["browser_sources"] + (
            [] if included else [expected]
        )
        assert saved["min_fit"] == 45


@pytest.mark.parametrize("method", ["save", "import"])
@pytest.mark.parametrize("stored_enabled", [False, True, "false"])
def test_generic_write_preserves_enabled_only_for_stored_true(
    api: Api, person_a: Person, factory: Any, method: str, stored_enabled: Any
) -> None:
    accept(api, person_a)
    current = profile(api, person_a)
    search = deepcopy(current["sections"]["search"])
    search["browser_sources"][0]["enabled"] = False
    body = (
        {"data": search, "base_revision": current["revision"]}
        if method == "save"
        else document(api, person_a, search, current["revision"])
    )
    with factory() as db, db.begin():
        stored = db.get(Profile, person_a.user_id)
        sections = deepcopy(stored.sections)
        sections["search"]["browser_sources"][0]["enabled"] = stored_enabled
        stored.sections = sections
    response = api.request(
        "PUT" if method == "save" else "POST",
        "/api/profile/search" if method == "save" else "/api/profile/import",
        token=person_a.token,
        json=body,
    )
    assert response.status_code == 200, response.text
    saved = snapshot(factory, person_a)[0]["search"]["browser_sources"][0]
    assert saved["enabled"] is (stored_enabled is True)
    assert saved["accepted_at"] == search["browser_sources"][0]["accepted_at"]
    assert saved["text_version"] == search["browser_sources"][0]["text_version"]


@pytest.mark.parametrize(
    ("others", "included", "code"), [(0, False, 0), (9, False, 0), (9, True, 0), (10, False, 2)]
)
def test_cli_import_keeps_off_platform_and_capacity_is_atomic(
    api: Api,
    person_a: Person,
    factory: Any,
    migrated_url: str,
    others: int,
    included: bool,
    code: int,
) -> None:
    accept(api, person_a)
    assert put(api, person_a, enabled=False).status_code == 200
    expected = entry(api, person_a)
    before = snapshot(factory, person_a)
    current = profile(api, person_a)
    canary = uuid.uuid4().hex
    search = {
        **current["sections"]["search"],
        "min_fit": 45,
        "browser_sources": sources(others, canary) + ([expected] if included else []),
    }
    body = document(api, person_a, search, current["revision"])
    body["sections"]["identity"]["full_name"] = canary
    result = run_cli(
        migrated_url,
        "import",
        "--email",
        person_a.email,
        "--file",
        "-",
        stdin=json.dumps(body).encode(),
    )
    assert result.returncode == code, result.stderr
    assert canary.encode() not in result.stdout + result.stderr
    after = snapshot(factory, person_a)
    if code == 2:
        assert b"browser_sources" in result.stderr
        assert b"Nothing was imported" in result.stderr
        assert after == before
    else:
        assert after[0]["search"]["browser_sources"] == search["browser_sources"] + (
            [] if included else [expected]
        )
        assert after[0]["search"]["min_fit"] == 45
        assert after[1] == before[1] + 7


@pytest.mark.parametrize("method", ["save", "import", "cli"])
def test_combined_interactive_scope_and_consent_survive_old_writes_and_explicit_clear(
    api: Api, person_a: Person, factory: Any, migrated_url: str, method: str
) -> None:
    accept(api, person_a)
    chosen = [
        {
            "platform": "custom",
            "url": "https://careers.example.org/jobs",
            "enabled": True,
            "mode": "llm_browser",
        }
    ]
    current = profile(api, person_a)
    response = search_save(
        api,
        person_a,
        {
            **current["sections"]["search"],
            "interactive_sites": chosen,
            "interactive_instructions": "Read the full requirements.",
        },
        current["revision"],
    )
    assert response.status_code == 200, response.text
    assert put(api, person_a, enabled=False).status_code == 200
    expected = entry(api, person_a)
    for clear in (False, True):
        current = profile(api, person_a)
        search = {**current["sections"]["search"], "browser_sources": []}
        search.pop("interactive_sites")
        search.pop("interactive_instructions")
        if clear:
            search.update(interactive_sites=[], interactive_instructions=None)
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
        saved = snapshot(factory, person_a)[0]["search"]
        assert saved["interactive_sites"] == ([] if clear else chosen)
        assert saved["interactive_instructions"] == (
            None if clear else "Read the full requirements."
        )
        assert saved["browser_sources"] == [expected]


@pytest.mark.parametrize("method", ["save", "import"])
def test_stale_draft_after_keep_editing_keeps_newer_dedicated_words(
    api: Api, person_a: Person, factory: Any, method: str
) -> None:
    # The Search tab's Keep editing after a 409 resends the whole draft, hidden Free-Work row
    # included, on the new revision: its old words must not undo the dedicated route's.
    accept(api, person_a)
    old = profile(api, person_a)
    newer = put(api, person_a, enabled=True, query="Drone Engineer", location="Lyon")
    assert newer.status_code == 200, newer.text
    expected = entry(api, person_a)
    assert expected is not None
    assert (expected["query"], expected["location"]) == ("Drone Engineer", "Lyon")
    draft = {**old["sections"]["search"], "min_fit": 45}
    assert draft["browser_sources"][0]["query"] == "Robotics Engineer"
    stale = write(api, person_a, method, draft, old["revision"])
    assert stale.status_code == 409, stale.text
    response = write(api, person_a, method, draft, snapshot(factory, person_a)[1])
    assert response.status_code == 200, response.text
    saved = snapshot(factory, person_a)[0]["search"]
    assert saved["browser_sources"] == [expected]
    assert saved["min_fit"] == 45


def test_rollback_query_names_exactly_the_people_holding_a_freework_entry(
    api: Api, person_a: Person, person_b: Person, factory: Any
) -> None:
    # The one way door declared in the pull request (CONTRIBUTING, Migrations): the release
    # before this one refuses the `freework` kind, so this read only query must name who a
    # rollback would break, and nobody else.
    accept(api, person_a)
    other = profile(api, person_b)
    saved = search_save(
        api, person_b, {**(other["sections"]["search"] or {}), "browser_sources": sources(1, "vie")}
    )
    assert saved.status_code == 200, saved.text
    with factory() as db:
        holders = db.execute(
            sa.text(
                "SELECT user_id FROM profiles WHERE sections->'search'->'browser_sources'"
                ' @> \'[{"kind": "freework"}]\''
            )
        ).scalars()
        assert set(holders) == {person_a.user_id}
