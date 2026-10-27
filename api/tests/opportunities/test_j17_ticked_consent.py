"""Jobs J17 (owner, 2026-10-27): ticking a job website in Find jobs and saving is the person's
permission to search it. The setup records it with the dedicated consent route of the platform,
at the sentence version shown and the reviewed profile revision; the saved selection alone is
still no permission, and the search's checks are those of browser B14, unchanged."""

import asyncio
import uuid
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.browser import linkedin_guard
from app.browser.platform_policy import PlatformPolicy
from app.jobs import sweep
from app.jobs.models import Sweep, SweepStatus, SweepTrigger
from app.opportunities.platforms import guard
from app.profile.schemas import SearchSection
from tests.identity.api_helpers import Api
from tests.opportunities.conftest import Person
from tests.opportunities.test_b14_policy import URLS, context, entries

PLATFORMS = tuple(URLS)
READERS = ("linkedin", "freework")  # a fixed reader row holds their permission


def path(platform: str) -> str:
    if platform == "linkedin":
        return "/api/browser/linkedin"
    return f"/api/opportunities/platforms/{platform}"


def version(platform: str) -> int:
    if platform == "linkedin":
        return linkedin_guard.TEXT_VERSION
    return guard.metadata(platform).text_version


def profile(api: Api, who: Person) -> dict[str, Any]:
    response = api.get("/api/profile", token=who.token)
    assert response.status_code == 200, response.text
    return response.json()


def site(platform: str) -> dict[str, Any]:
    return {"platform": "custom", "url": URLS[platform], "enabled": True, "mode": "llm_browser"}


def tick_and_save(api: Api, who: Person, platform: str) -> int:
    """The setup's generic save of the search with the website ticked: no permission in it."""
    current = profile(api, who)
    search = {**current["sections"]["search"], "interactive_sites": [site(platform)]}
    response = api.request(
        "PUT",
        "/api/profile/search",
        token=who.token,
        json={"base_revision": current["revision"], "data": search},
    )
    assert response.status_code == 200, response.text
    return int(response.json()["revision"])


def give(api: Api, who: Person, platform: str, revision: int, **changes: Any) -> Any:
    """The body search-setup.tsx sends for a ticked website without a current permission."""
    body: dict[str, Any] = {
        "enabled": True,
        "base_revision": revision,
        "accept_version": version(platform),
    }
    if platform in READERS:
        body |= {"interactive_only": True, "query": "robot"}
    return api.request("PUT", path(platform), token=who.token, json={**body, **changes})


def take_back(api: Api, who: Person, platform: str, revision: int) -> Any:
    """The body search-setup.tsx sends for a website unticked and saved."""
    return api.request(
        "PUT",
        path(platform),
        token=who.token,
        json={"enabled": False, "base_revision": revision},
    )


def websites_only(factory: sessionmaker[Session], who: Person, at: datetime) -> uuid.UUID:
    """A running websites only sweep of the person's current search, frozen as the api does."""
    with factory() as db, db.begin():
        stored = sweep.load_search(db, who.user_id)
        assert stored is not None
        holder = type(
            "P", (), {"sections": {"search": stored.model_dump(mode="json")}, "revision": 1}
        )
        scope = sweep.freeze_scope(holder, SweepTrigger.manual, at, websites_only=True)
        assert [s["mode"] for s in scope["sources"]] == ["llm_browser"]
        row = Sweep(
            user_id=who.user_id,
            trigger=SweepTrigger.manual,
            status=SweepStatus.running,
            started_at=at,
            stats={
                "search_scope": scope,
                "sources": sweep.initial_sources(scope),
                "picked_at": at.isoformat(),
            },
        )
        db.add(row)
        db.flush()
        return row.id


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_the_ticked_save_alone_is_no_permission_and_the_consent_route_records_it(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Any, platform: str
) -> None:
    revision = tick_and_save(api, person_a, platform)
    policy = PlatformPolicy(factory, clock)
    sweep_id = websites_only(factory, person_a, clock())
    ctx = context(person_a, sweep_id, URLS[platform], platform)
    # A website saved ticked, with no consent recorded: never opened.
    assert run(policy.check(ctx)) == "permission_unavailable"
    assert run(policy.reserve(ctx)) == "permission_unavailable"

    answer = give(api, person_a, platform, revision)
    assert answer.status_code == 200, answer.text
    assert answer.json()["allowed"] is True
    assert run(policy.check(ctx)) is None
    assert run(policy.reserve(ctx)) is None
    assert entries(factory, sweep_id)[0]["platform"] == platform
    if platform in READERS:
        # The permission is the websites' only: it turns on no fixed reader of the platform.
        rows = profile(api, person_a)["sections"]["search"]["browser_sources"]
        assert [(r["kind"], r["interactive_only"]) for r in rows] == [(platform, True)]
        assert sweep.enabled_sources(SearchSection.model_validate({"browser_sources": rows})) == []

    # Unticked and saved during the search: its next call stops.
    off = take_back(api, person_a, platform, profile(api, person_a)["revision"])
    assert off.status_code == 200, off.text
    assert off.json()["allowed"] is False
    assert run(policy.check(ctx)) == "permission_revoked"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_a_stale_revision_or_sentence_records_nothing(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Any, platform: str
) -> None:
    revision = tick_and_save(api, person_a, platform)
    for changes in (
        {"base_revision": revision - 1},  # another tab saved after the reviewed revision
        {"accept_version": version(platform) + 1},  # a sentence the server does not show
    ):
        refused = give(api, person_a, platform, revision, **changes)
        assert refused.status_code == 409, refused.text
    assert profile(api, person_a)["revision"] == revision
    policy = PlatformPolicy(factory, clock)
    ctx = context(person_a, websites_only(factory, person_a, clock()), URLS[platform], platform)
    assert run(policy.check(ctx)) == "permission_unavailable"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_the_pause_after_a_checkpoint_outlives_a_new_tick(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Any, platform: str
) -> None:
    revision = tick_and_save(api, person_a, platform)
    assert give(api, person_a, platform, revision).status_code == 200
    policy = PlatformPolicy(factory, clock)
    first_id = websites_only(factory, person_a, clock())
    first = context(person_a, first_id, URLS[platform], platform)
    assert run(policy.reserve(first)) is None
    wall = '- heading "Verify you are human" [level=1]'
    assert run(policy.observe(first, URLS[platform], wall)) in (
        "permission_checkpoint",
        "linkedin_checkpoint",
    )
    with factory() as db, db.begin():  # that search ended; the history keeps the checkpoint
        ended = db.get(Sweep, first_id)
        assert ended is not None
        ended.status, ended.finished_at = SweepStatus.done, clock()
    # Unticked, then ticked and saved again: the consent is new, the pause stays.
    assert take_back(api, person_a, platform, profile(api, person_a)["revision"]).status_code == 200
    again = give(api, person_a, platform, profile(api, person_a)["revision"])
    assert again.status_code == 200, again.text
    assert again.json()["paused_until"] is not None
    assert again.json()["allowed"] is False
    later = context(person_a, websites_only(factory, person_a, clock()), URLS[platform], platform)
    assert run(policy.check(later)) == "permission_checkpoint"
