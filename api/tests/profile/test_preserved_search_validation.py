"""Generic writers validate preserved browser authority before changing profile state."""

import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from app.browser import linkedin_guard
from app.browser.models import LinkedInCheckpoint
from app.jobs.models import Sweep, SweepStatus, SweepTrigger
from app.profile import service
from app.profile.models import Profile, ProfileRevision
from app.profile.schemas import validate_section
from tests.identity.api_helpers import Api, make_user

KINDS = ("linkedin", "freework")
FIELDS = ("enabled", "accepted_at", "text_version", "interactive_only")
INVALID_TIMESTAMPS = (
    "not-a-date",
    "",
    "2026-10-20",
    "2026-10-20T09:00:00",
    "2026-02-30T09:00:00+00:00",
    "2026-10-20T09:00:00+00:00" + "0" * 20,
)
VALID_TIMESTAMPS = (
    "2026-10-20T09:00:00+00:00",
    "2026-10-20T09:00:00Z",
    "2026-10-20T11:00:00+02:00",
)


@dataclass(frozen=True)
class Person:
    user_id: uuid.UUID
    token: str


def source(kind: str, accepted_at: str, **changes: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "query": "Example robotics",
        "location": "Paris",
        "enabled": True,
        "interactive_only": True,
        "accepted_at": accepted_at,
        "text_version": 1,
        **changes,
    }


@pytest.fixture
def people(factory: Any, api: Api, clock: Any) -> tuple[Person, Person]:
    result = []
    for label in ("a", "b"):
        email = f"e2e-648-preserved-{label}@example.org"
        user = make_user(factory, email=email, name=f"Example Person {label}")
        who = Person(user.id, api.login_token(email))
        with factory() as db, db.begin():
            service.save_section(
                db, who.user_id, "search", validate_section("search", {}), 0, clock()
            )
            profile = db.get(Profile, who.user_id)
            search = validate_section(
                "search",
                {
                    "browser_sources": [source(kind, clock().isoformat()) for kind in KINDS],
                    "interactive_sites": [],
                    "interactive_instructions": None,
                },
            )
            profile.sections = {**profile.sections, "search": search}
            linkedin_guard.record_checkpoint(db, who.user_id, clock())
            db.add(
                Sweep(
                    user_id=who.user_id,
                    trigger=SweepTrigger.manual,
                    status=SweepStatus.done,
                    started_at=clock(),
                    finished_at=clock(),
                    stats={
                        "sources": [
                            {"kind": "freework", "checkpoint_at": clock().isoformat()},
                        ]
                    },
                )
            )
        result.append(who)
    return result[0], result[1]


def snapshot(factory: Any, who: Person) -> Any:
    with factory() as db:
        profile = db.get(Profile, who.user_id)
        history = [
            tuple(
                tuple(row)
                for row in db.execute(
                    sa.select(*model.__table__.columns)
                    .where(model.user_id == who.user_id)
                    .order_by(*model.__table__.primary_key.columns)
                )
            )
            for model in (ProfileRevision, LinkedInCheckpoint, Sweep)
        ]
        return deepcopy(
            (
                profile.sections,
                profile.revision,
                profile.updated_at,
                profile.photo,
                profile.photo_mime,
                *history,
            )
        )


def seed_timestamp(factory: Any, who: Person, kind: str, timestamp: str) -> None:
    with factory() as db, db.begin():
        profile = db.get(Profile, who.user_id)
        search = deepcopy(profile.sections["search"])
        next(row for row in search["browser_sources"] if row["kind"] == kind)["accepted_at"] = (
            timestamp
        )
        profile.sections = {**profile.sections, "search": search}


def incoming_search(kind: str, present: bool) -> dict[str, Any]:
    # Keep the other kind present so it cannot accidentally trigger the restoration-only check.
    return {
        "min_fit": 41,
        "browser_sources": [
            source(
                other,
                "2030-01-01T00:00:00+00:00",
                query="Edited robotics",
                location="Lyon",
                enabled=False,
                interactive_only=False,
            )
            for other in KINDS
            if present or other != kind
        ],
    }


def exported(api: Api, who: Person) -> Any:
    response = api.get("/api/profile/export", token=who.token)
    assert response.status_code == 200, response.text
    return response


def export_error(api: Api, who: Person) -> Any:
    with pytest.raises(ValidationError) as raised:
        api.get("/api/profile/export", token=who.token)
    return raised.value.errors(include_input=False, include_url=False, include_context=False)


def write(
    api: Api, who: Person, method: str, search: dict[str, Any], document: Any, revision: int
) -> Any:
    if method == "save":
        return api.request(
            "PUT",
            "/api/profile/search",
            token=who.token,
            json={"data": search, "base_revision": revision},
        )
    body = deepcopy(document)
    body["sections"]["search"] = search
    body["sections"]["identity"]["headline"] = "This import must be atomic"
    body["base_revision"] = revision
    return api.post("/api/profile/import", token=who.token, json=body)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("method", ["save", "import"])
@pytest.mark.parametrize("present", [False, True], ids=("omitted", "present"))
@pytest.mark.parametrize("timestamp", INVALID_TIMESTAMPS)
def test_malformed_preserved_timestamp_rejects_without_writing(
    api: Api,
    people: tuple[Person, Person],
    factory: Any,
    kind: str,
    method: str,
    present: bool,
    timestamp: str,
) -> None:
    who, other = people
    document = exported(api, who).json()
    other_export = exported(api, other).content
    seed_timestamp(factory, who, kind, timestamp)
    before, other_before = snapshot(factory, who), snapshot(factory, other)
    error_before = export_error(api, who)
    response = write(api, who, method, incoming_search(kind, present), document, before[1])
    assert response.status_code == 422, response.text
    assert snapshot(factory, who) == before
    assert snapshot(factory, other) == other_before
    assert export_error(api, who) == error_before
    assert exported(api, other).content == other_export


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("method", ["save", "import"])
@pytest.mark.parametrize("present", [False, True], ids=("omitted", "present"))
@pytest.mark.parametrize("timestamp", VALID_TIMESTAMPS)
def test_valid_preserved_timestamp_keeps_authority_and_saves_ordinary_edits(
    api: Api,
    people: tuple[Person, Person],
    factory: Any,
    kind: str,
    method: str,
    present: bool,
    timestamp: str,
) -> None:
    who, other = people
    seed_timestamp(factory, who, kind, timestamp)
    document = exported(api, who).json()
    before, other_before = snapshot(factory, who), snapshot(factory, other)
    other_export = exported(api, other).content
    response = write(api, who, method, incoming_search(kind, present), document, before[1])
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == before[1] + (1 if method == "save" else 7)
    search = response.json()["sections"]["search"]
    assert search["min_fit"] == 41
    previous = {row["kind"]: row for row in before[0]["search"]["browser_sources"]}
    for row in search["browser_sources"]:
        assert {field: row[field] for field in FIELDS} == {
            field: previous[row["kind"]][field] for field in FIELDS
        }
        assert row["query"] == (
            "Example robotics" if not present and row["kind"] == kind else "Edited robotics"
        )
    assert exported(api, who).json()["sections"]["search"] == search
    after = snapshot(factory, who)
    assert after[3:5] == before[3:5]
    assert after[6:] == before[6:]
    assert snapshot(factory, other) == other_before
    assert exported(api, other).content == other_export


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("method", ["save", "import"])
def test_invalid_preservation_fails_before_orm_state_is_changed(
    api: Api,
    people: tuple[Person, Person],
    factory: Any,
    clock: Any,
    kind: str,
    method: str,
) -> None:
    who, other = people
    document = exported(api, who).json()
    seed_timestamp(factory, who, kind, "not-a-date")
    before, other_before = snapshot(factory, who), snapshot(factory, other)
    search = validate_section("search", incoming_search(kind, True))
    with factory() as db, db.begin():
        writer = service.save_section if method == "save" else service.import_profile
        args = (
            (db, who.user_id, "search", search, before[1], clock())
            if method == "save"
            else (
                db,
                who.user_id,
                {**document["sections"], "search": search},
                None,
                before[1],
                clock(),
            )
        )
        with pytest.raises(service.PreservedSearchError):
            writer(*args)
        assert not db.dirty
        assert not db.new
    assert snapshot(factory, who) == before
    assert snapshot(factory, other) == other_before
