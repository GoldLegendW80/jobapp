"""The job feeds in a sweep, against real Postgres (app/jobs/feeds, SPEC-jobs Job feeds): a sweep
of feeds alone starts and ends done, stores and scores what they keep, dedupes against the
person's rows and its own earlier postings, marks never apply roles skipped, records one stats
entry per feed, reads the previous sweep's cursor and reads Adzuna's key from the encrypted store.
Also the feeds setting of the search section, a website only sweep freezing no feed key, the
feeds' total time leaving the websites their share, and the key routes (rate limited, each
person's own). The APIs are the fake of test_job_feeds.py: no network."""

import asyncio
import os
import time
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.agents.crypto import Keyring
from app.agents.ratelimit import RateLimiter
from app.jobs import feeds, sweep
from app.jobs.feeds import keys
from app.jobs.models import ContractType, Job, JobEvent, JobStatus, SweepStatus
from tests.identity.api_helpers import Api
from tests.jobs.sweep_helpers import jobs_of, new_sweep, read_sweep, search, set_profile
from tests.jobs.test_api import Signed, sign_in
from tests.jobs.test_job_feeds import APP_ID, APP_KEY, NOW, Slow, fixture, stamp
from tests.jobs.test_job_feeds import Api as FakeApi

KEYRING = Keyring(os.urandom(32))


def feeds_search(**overrides: Any) -> dict[str, Any]:
    return search(
        interactive_sites=[],
        title_include={"terms": ["forward deployed", "ai engineer"], "advanced_regex": False},
        location={"terms": ["paris", "lyon", "bordeaux"], "advanced_regex": False},
        **overrides,
    )


def fake_api() -> FakeApi:
    return FakeApi(
        {
            ("www.arbeitnow.com", "1"): fixture("arbeitnow_page1"),
            ("www.arbeitnow.com", "2"): fixture("arbeitnow_page2"),
            ("api.adzuna.com", "1"): fixture("adzuna"),
        }
    )


def run_feeds(
    factory: sessionmaker[Session], sweep_id: uuid.UUID, api: FakeApi, keyring: Keyring | None
) -> None:
    async def sleep(seconds: float) -> None:
        return None

    runner = feeds.FeedRunner(
        factory,
        keys=keys.reader(factory, keyring),
        transport=api.transport,
        sleep=sleep,
        clock=lambda: NOW,
    )
    asyncio.run(sweep.run_sweep(factory, sweep_id, lambda: NOW, feeds=runner))


def add_job(factory: sessionmaker[Session], user_id: uuid.UUID, **values: Any) -> None:
    with factory() as db, db.begin():
        db.add(
            Job(
                user_id=user_id,
                slug=values["slug"],
                company=values["company"],
                title=values["title"],
                location=values["location"],
                url=values["url"],
                source="manual",
                status=JobStatus.found,
                found_on=NOW.date(),
            )
        )


def test_a_sweep_of_feeds_alone_stores_dedupes_and_records(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, feeds_search(job_feeds=["arbeitnow", "adzuna"]))
    # Already in the list: the same employer, title and place at another address.
    add_job(
        factory,
        alice,
        slug="example-robotics-forward-deployed-engineer",
        company="Example Robotics",
        title="Forward Deployed Engineer",
        location="Paris, France",
        url="https://careers.example-robotics.test/fde",
    )
    sweep_id = new_sweep(factory, alice)
    frozen = read_sweep(factory, sweep_id).stats["search_scope"]["sources"]
    assert [(s["kind"], s["mode"], s["host"]) for s in frozen] == [
        ("arbeitnow", "api", "www.arbeitnow.com"),
        ("adzuna", "api", "api.adzuna.com"),
    ]
    api = fake_api()
    run_feeds(factory, sweep_id, api, KEYRING)

    row = read_sweep(factory, sweep_id)
    assert row.status is SweepStatus.done
    assert row.error is None
    arbeitnow, adzuna = row.stats["sources"]
    assert arbeitnow["kind"] == "arbeitnow"
    assert arbeitnow["mode"] == "api"
    assert arbeitnow["outcome"] == "complete"
    assert arbeitnow["reason"] is None
    assert arbeitnow["found"] == 3  # kept by the criteria
    assert arbeitnow["postings"] == 2  # one was the row already listed
    assert arbeitnow["duplicates"] == 1
    assert arbeitnow["skipped_by_rule"] == 1
    assert arbeitnow["result_pages"] == 2
    assert arbeitnow["cursor"] == stamp(hours=1)
    # No key stored: skipped, never a failure, and no request sent to Adzuna.
    assert adzuna["outcome"] == "not_attempted"
    assert adzuna["reason"] == "no_key"
    assert {r.url.host for r in api.requests} == {"www.arbeitnow.com"}
    assert row.stats["new"] == 2
    assert row.stats["duplicates"] == 1

    jobs = {job.title: job for job in jobs_of(factory, alice)}
    lyon = jobs["Senior AI Engineer"]
    assert lyon.source == "arbeitnow"
    assert lyon.status is JobStatus.found
    assert lyon.contract_type is ContractType.Stage  # the feed's stated type
    assert lyon.url.startswith("https://www.arbeitnow.com/jobs/companies/example-freight/")
    assert lyon.posting_text
    assert "LLM agents" in lyon.posting_text
    assert lyon.fit is not None  # scored
    shield = jobs["Security AI Engineer"]
    assert shield.status is JobStatus.skipped
    assert "security" in shield.notes
    assert jobs["Forward Deployed Engineer"].source == "manual, arbeitnow"
    with factory() as db:
        events = db.execute(
            sa.select(JobEvent.data).where(JobEvent.job_id == lyon.id, JobEvent.kind == "sweep")
        ).scalars()
        [event] = list(events)
    assert event["discovery"]["evidence"] == {
        "api": "arbeitnow",
        "external_id": "senior-ai-engineer-lyon-2001",
    }

    # The next sweep reads back to the cursor less its hour of overlap (one page): the one
    # posting of that window is a duplicate now.
    second = new_sweep(factory, alice)
    again = fake_api()
    run_feeds(factory, second, again, KEYRING)
    entry = read_sweep(factory, second).stats["sources"][0]
    assert [r.url.params["page"] for r in again.requests] == ["1"]
    assert entry["postings"] == 0
    assert entry["duplicates"] == 1
    assert len(jobs_of(factory, alice)) == 3


def test_adzuna_reads_the_stored_key(factory: sessionmaker[Session], alice: uuid.UUID) -> None:
    set_profile(factory, alice, feeds_search(job_feeds=["adzuna"]))
    with factory() as db, db.begin():
        keys.save(db, KEYRING, alice, "adzuna", {"app_id": APP_ID, "app_key": APP_KEY}, NOW)
    sweep_id = new_sweep(factory, alice)
    api = fake_api()
    run_feeds(factory, sweep_id, api, KEYRING)
    entry = read_sweep(factory, sweep_id).stats["sources"][0]
    assert entry["outcome"] == "complete"
    assert entry["postings"] == 2
    assert api.requests[0].url.params["app_key"] == APP_KEY
    job = next(job for job in jobs_of(factory, alice) if job.title == "AI Engineer H/F")
    assert job.source == "adzuna"
    assert job.url == "https://www.adzuna.fr/land/ad/4001"
    assert job.contract_type is ContractType.CDI
    # The key is stored encrypted, bound to its owner and feed.
    with factory() as db:
        stored = db.execute(sa.text("SELECT secret_encrypted FROM job_feed_keys")).scalar_one()
    assert APP_KEY.encode() not in stored


def test_a_key_unreadable_by_this_server_skips_adzuna(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, feeds_search(job_feeds=["adzuna"]))
    with factory() as db, db.begin():
        keys.save(db, KEYRING, alice, "adzuna", {"app_id": APP_ID, "app_key": APP_KEY}, NOW)
    sweep_id = new_sweep(factory, alice)
    api = fake_api()
    run_feeds(factory, sweep_id, api, Keyring(os.urandom(32)))  # another server key
    row = read_sweep(factory, sweep_id)
    assert row.status is SweepStatus.done
    assert row.stats["sources"][0]["reason"] == "key_unreadable"
    assert api.requests == []


def test_no_website_and_no_feed_still_fails_with_the_message(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, feeds_search())
    sweep_id = new_sweep(factory, alice)
    api = fake_api()
    run_feeds(factory, sweep_id, api, KEYRING)
    row = read_sweep(factory, sweep_id)
    assert row.status is SweepStatus.failed
    assert row.error == sweep.NO_WEBSITES
    assert "job feed" in sweep.NO_WEBSITES
    assert api.requests == []


def test_a_website_only_sweep_freezes_no_feed_key(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    # The release before refuses the key: a sweep queued before a rollback must still validate.
    set_profile(factory, alice, search())
    frozen = read_sweep(factory, new_sweep(factory, alice)).stats["search_scope"]
    assert "job_feeds" not in frozen["search"]
    assert all(source.get("mode") != "api" for source in frozen["sources"])
    set_profile(factory, bob, search(job_feeds=["himalayas"]))
    frozen = read_sweep(factory, new_sweep(factory, bob)).stats["search_scope"]
    assert frozen["search"]["job_feeds"] == ["himalayas"]


def test_feeds_leave_the_websites_their_share_of_the_sweep(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search(job_feeds=["arbeitnow", "himalayas"]))
    sweep_id = new_sweep(factory, alice)
    api = fake_api()
    never = Slow(api.handler, lambda request: True)  # feeds that never answer
    left_for_websites: list[float] = []

    async def websites(plan: Any, stats: Any, progress: Any, deadline: float) -> None:
        left_for_websites.append(deadline - time.monotonic())

    async def sleep(seconds: float) -> None:
        return None

    runner = feeds.FeedRunner(
        factory, transport=never, sleep=sleep, clock=lambda: NOW, feed_time_s=0.3,
        feeds_time_s=0.5,
    )  # fmt: skip
    asyncio.run(
        sweep.run_sweep(
            factory, sweep_id, lambda: NOW, feeds=runner, browser=websites, timeout_s=60.0
        )
    )
    row = read_sweep(factory, sweep_id)
    entries = [e for e in row.stats["sources"] if e.get("mode") == "api"]
    assert [e["reason"] for e in entries] == ["time_budget", "time_budget"]
    # The feeds spent their total, 0.5 s, and the website gets the rest of the sweep.
    [left] = left_for_websites
    assert left > 60.0 - 0.5 - 1.0


# --- the setting and the key routes ---------------------------------------------------------------


@pytest.fixture
def owner(factory: sessionmaker[Session], api: Api) -> Signed:
    return sign_in(factory, api, "e2e-feeds-owner@example.org")


def save_search(api: Api, owner: Signed, change: dict[str, Any], drop: str | None = None) -> Any:
    current = api.get("/api/profile", token=owner.token).json()
    data = {**current["sections"]["search"], **change}
    if drop:
        data.pop(drop)
    return api.request(
        "PUT",
        "/api/profile/search",
        token=owner.token,
        json={"data": data, "base_revision": current["revision"]},
    )


def stored_search(factory: sessionmaker[Session], user_id: uuid.UUID) -> dict[str, Any]:
    from app.profile.models import Profile

    with factory() as db:
        profile = db.get(Profile, user_id)
        assert profile is not None
        return dict(profile.sections["search"])


def test_the_feeds_setting_persists(
    factory: sessionmaker[Session], api: Api, owner: Signed
) -> None:
    set_profile(factory, owner.id, feeds_search())
    saved = save_search(api, owner, {"job_feeds": ["arbeitnow", "adzuna"]})
    assert saved.status_code == 200, saved.text
    assert stored_search(factory, owner.id)["job_feeds"] == ["arbeitnow", "adzuna"]
    # An editor that does not know the field keeps it.
    kept = save_search(api, owner, {"days": 14}, drop="job_feeds")
    assert kept.status_code == 200, kept.text
    assert stored_search(factory, owner.id)["job_feeds"] == ["arbeitnow", "adzuna"]
    for bad in (["arbeitnow", "arbeitnow"], ["indeed"]):
        refused = save_search(api, owner, {"job_feeds": bad})
        assert refused.status_code == 422, refused.text
    # Unticking all of them stores no key at all (the release before refuses it).
    cleared = save_search(api, owner, {"job_feeds": []})
    assert cleared.status_code == 200, cleared.text
    assert "job_feeds" not in stored_search(factory, owner.id)
    shown = api.get("/api/profile", token=owner.token).json()["sections"]["search"]
    assert shown.get("job_feeds", []) == []


def test_the_adzuna_key_routes(app: FastAPI, api: Api, owner: Signed) -> None:
    listed = api.get("/api/jobs/feeds", token=owner.token)
    assert listed.status_code == 200
    assert [f["name"] for f in listed.json()["feeds"]] == ["arbeitnow", "himalayas", "adzuna"]
    assert listed.json()["keys_available"] is False
    body = {"app_id": APP_ID, "app_key": APP_KEY}
    unavailable = api.request("PUT", "/api/jobs/feeds/adzuna/key", token=owner.token, json=body)
    assert unavailable.status_code == 503

    app.state.credentials_keyring = KEYRING
    refused = api.request(
        "PUT", "/api/jobs/feeds/adzuna/key", token=owner.token,
        json={"app_id": APP_ID, "app_key": "too short"},
    )  # fmt: skip
    assert refused.status_code == 422
    assert "too short" not in refused.text
    saved = api.request("PUT", "/api/jobs/feeds/adzuna/key", token=owner.token, json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["key_stored"] is True
    assert saved.json()["key_last4"] == APP_KEY[-4:]
    adzuna = api.get("/api/jobs/feeds", token=owner.token).json()["feeds"][2]
    assert adzuna["needs_key"] is True
    assert adzuna["key_stored"] is True
    for answer in (saved.text, api.get("/api/jobs/feeds", token=owner.token).text):
        assert APP_KEY not in answer
        assert APP_ID not in answer
    assert (
        api.request("PUT", "/api/jobs/feeds/indeed/key", token=owner.token, json=body).status_code
        == 422
    )
    assert api.request("PUT", "/api/jobs/feeds/adzuna/key", json=body).status_code == 401
    removed = api.request("DELETE", "/api/jobs/feeds/adzuna/key", token=owner.token)
    assert removed.status_code == 204
    adzuna = api.get("/api/jobs/feeds", token=owner.token).json()["feeds"][2]
    assert adzuna["key_stored"] is False
    assert adzuna["key_last4"] is None


def test_the_adzuna_key_save_is_rate_limited(app: FastAPI, api: Api, owner: Signed) -> None:
    app.state.credentials_keyring = KEYRING
    app.state.feed_key_saves = RateLimiter(1, 900.0)
    body = {"app_id": APP_ID, "app_key": APP_KEY}
    first = api.request("PUT", "/api/jobs/feeds/adzuna/key", token=owner.token, json=body)
    assert first.status_code == 200, first.text
    again = api.request("PUT", "/api/jobs/feeds/adzuna/key", token=owner.token, json=body)
    assert again.status_code == 429
    assert int(again.headers["Retry-After"]) > 0
    assert APP_KEY not in again.text
    # A refused shape is answered before the limit is spent, as for the model credential.
    shape = api.request(
        "PUT", "/api/jobs/feeds/adzuna/key", token=owner.token,
        json={"app_id": APP_ID, "app_key": "short"},
    )  # fmt: skip
    assert shape.status_code == 422


def test_the_adzuna_key_routes_touch_only_the_callers_key(
    factory: sessionmaker[Session], app: FastAPI, api: Api, owner: Signed
) -> None:
    other = sign_in(factory, api, "e2e-feeds-other@example.org")
    app.state.credentials_keyring = KEYRING
    mine = {"app_id": APP_ID, "app_key": APP_KEY}
    saved = api.request("PUT", "/api/jobs/feeds/adzuna/key", token=owner.token, json=mine)
    assert saved.status_code == 200, saved.text

    def adzuna(token: str) -> dict[str, Any]:
        found: dict[str, Any] = api.get("/api/jobs/feeds", token=token).json()["feeds"][2]
        return found

    # The other person sees no key, removing hers leaves the owner's, saving hers is her own.
    assert adzuna(other.token)["key_stored"] is False
    removed = api.request("DELETE", "/api/jobs/feeds/adzuna/key", token=other.token)
    assert removed.status_code == 204
    assert adzuna(owner.token)["key_stored"] is True
    theirs = {"app_id": "cd" * 4, "app_key": "y" * 28 + "7777"}
    assert (
        api.request("PUT", "/api/jobs/feeds/adzuna/key", token=other.token, json=theirs).status_code
        == 200
    )
    assert adzuna(owner.token)["key_last4"] == APP_KEY[-4:]
    assert adzuna(other.token)["key_last4"] == "7777"
    with factory() as db:
        assert keys.reveal(db, KEYRING, owner.id, "adzuna") == mine
        assert keys.reveal(db, KEYRING, other.id, "adzuna") == theirs
