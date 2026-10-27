"""Browser B10 over real Postgres: the LinkedIn guard (acceptance of the current sentence, the
switch, the 7 day pause of a checkpoint kept in the sweeps table, one sweep a day), GET and PUT
/api/browser/linkedin, a sweep with a LinkedIn source, the same posting found on LinkedIn and on
Greenhouse as one row naming both sources, and person B never affected by A."""

import asyncio
import random
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.browser import linkedin_guard as guard
from app.browser.sources import MemoryPacer, Runner
from app.jobs import sweep
from app.jobs.models import Sweep, SweepStatus, SweepTrigger
from tests.browser import b10_pages as pages
from tests.browser.test_b8_sources import FakeLeases, FakeTools
from tests.identity.api_helpers import Api, make_user
from tests.jobs.fake_ats import FakeAts
from tests.jobs.sweep_helpers import board, jobs_of, new_sweep, run, search, set_profile

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
ACCEPTED = {"accepted_at": "2026-10-19T08:00:00+00:00", "text_version": guard.TEXT_VERSION}


def linkedin(**kw: Any) -> dict[str, Any]:
    return {"kind": "linkedin", "query": "AI Engineer", "enabled": True} | ACCEPTED | kw


@pytest.fixture
def fake_ats() -> Iterator[FakeAts]:
    with FakeAts() as server:
        yield server


def checked(
    factory: sessionmaker[Session], tools: FakeTools, now: datetime | None = None
) -> Runner:
    clock = [0.0]

    async def sleep(seconds: float) -> None:
        clock[0] += seconds

    return Runner(
        FakeLeases(),  # type: ignore[arg-type]
        lambda user, lease, token: tools,
        sleep=sleep,
        rng=random.Random(1),  # noqa: S311  a seeded pacing, not a secret
        monotonic=lambda: clock[0],
        pacer=MemoryPacer(lambda: clock[0]),
        checks={"linkedin": guard.sweep_check(factory, lambda: now or datetime.now(UTC))},
        walls={"linkedin": guard.sweep_wall(factory, lambda: now or datetime.now(UTC))},
    )


def add_sweep(
    factory: sessionmaker[Session], user: uuid.UUID, entry: dict[str, Any], at: datetime
) -> uuid.UUID:
    with factory() as db, db.begin():
        row = Sweep(
            user_id=user,
            trigger=SweepTrigger.manual,
            status=SweepStatus.done,
            started_at=at,
            finished_at=at + timedelta(minutes=5),
            stats={"sources": [{"kind": "linkedin", "result_pages": 1} | entry]},
        )
        db.add(row)
        db.flush()
        return row.id


def refusal(factory: sessionmaker[Session], user: uuid.UUID, now: datetime = NOW) -> str | None:
    with factory() as db:
        return guard.refusal(db, user, now)


# --- the guard ----------------------------------------------------------------------------------


def test_not_accepted_switched_off_or_absent_refuses_every_path(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    assert refusal(factory, alice) == "linkedin_off"  # no profile at all
    for entry in (
        {"kind": "linkedin", "query": "AI Engineer", "enabled": True},  # never accepted
        linkedin(enabled=False),  # accepted, then switched off
    ):
        set_profile(factory, alice, search(browser_sources=[entry]))
        assert refusal(factory, alice) == "linkedin_off"
        with factory() as db:
            # What apply's Easy Apply (A16) and profile work (A18) call.
            assert guard.allowed(db, alice, NOW) is False
    set_profile(factory, alice, search(browser_sources=[linkedin()]))
    with factory() as db:
        assert guard.allowed(db, alice, NOW) is True


def test_a_new_version_of_the_sentence_stops_work_until_accepted(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_profile(factory, alice, search(browser_sources=[linkedin()]))
    assert refusal(factory, alice) is None
    monkeypatch.setattr(guard, "TEXT_VERSION", guard.TEXT_VERSION + 1)
    assert refusal(factory, alice) == "linkedin_off"


def checkpoint(
    factory: sessionmaker[Session], user: uuid.UUID, at: datetime, origin: str = "sweep"
) -> None:
    with factory() as db:
        guard.record_checkpoint(db, user, at, origin)
        db.commit()


def test_a_checkpoint_pauses_7_days_kept_in_postgres(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search(browser_sources=[linkedin()]))
    met = NOW - timedelta(days=1)
    checkpoint(factory, alice, met)
    # A new session on a new connection: what a restart of the api and the worker reads.
    assert refusal(factory, alice) == "linkedin_paused"
    assert refusal(factory, alice, met + timedelta(days=7, seconds=-1)) == "linkedin_paused"
    assert refusal(factory, alice, met + timedelta(days=7)) is None
    with factory() as db:
        assert guard.state(db, alice, NOW).paused_until == met + guard.PAUSE


@pytest.mark.parametrize("origin", ["sweep", "easy_apply", "profile"])
def test_record_checkpoint_is_the_one_way_for_every_path(
    factory: sessionmaker[Session], alice: uuid.UUID, origin: str
) -> None:
    set_profile(factory, alice, search(browser_sources=[linkedin()]))
    checkpoint(factory, alice, NOW, origin)
    assert refusal(factory, alice, NOW + timedelta(hours=1)) == "linkedin_paused"
    with factory() as db, pytest.raises(ValueError, match="origin"):
        guard.record_checkpoint(db, alice, NOW, "someone")
    # The sweep's own stats no longer start a pause: only the table does.
    add_sweep(factory, alice, {"error": "linkedin_checkpoint"}, NOW + timedelta(days=30))
    assert refusal(factory, alice, NOW + timedelta(days=31)) is None


def test_a_checkpoint_met_by_a_sweep_is_recorded_in_postgres(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(browser_sources=[linkedin()]))
    tools = FakeTools({pages.search_url(): pages.CHECKPOINT})
    row = run(factory, new_sweep(factory, alice), fake_ats, browser=checked(factory, tools))
    assert row.stats["sources"][0]["error"] == "linkedin_checkpoint"
    assert refusal(factory, alice, datetime.now(UTC)) == "linkedin_paused"


def test_no_daily_limit(factory: sessionmaker[Session], alice: uuid.UUID) -> None:
    """Owner, 2026-10-28: no limit on searches a day, LinkedIn too. A sweep of the day that
    opened LinkedIn refuses no other; the switch and the 7 day pause still do."""
    set_profile(factory, alice, search(browser_sources=[linkedin()]))
    today = add_sweep(factory, alice, {"error": None}, NOW - timedelta(hours=2))
    with factory() as db:
        assert guard.sweep_refusal(db, alice, None, NOW) is None
        assert guard.sweep_refusal(db, alice, today, NOW) is None
        assert guard.sweep_refusal(db, alice, None, NOW + timedelta(days=1)) is None


def test_person_b_never_reads_a(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    set_profile(factory, alice, search(browser_sources=[linkedin()]))
    add_sweep(factory, alice, {"error": "linkedin_signin"}, NOW - timedelta(hours=1))
    assert refusal(factory, bob) == "linkedin_off"
    with factory() as db:
        b = guard.state(db, bob, NOW)
        assert (b.enabled, b.accepted, b.needs_signin, b.last_sweep_at) == (
            False,
            False,
            False,
            None,
        )
        assert guard.state(db, alice, NOW).needs_signin is True
    # A's checkpoint never pauses B.
    set_profile(factory, bob, search(browser_sources=[linkedin()]))
    checkpoint(factory, alice, NOW - timedelta(hours=1))
    assert refusal(factory, alice) == "linkedin_paused"
    assert refusal(factory, bob) is None


# --- the sweep ----------------------------------------------------------------------------------


def test_the_same_posting_on_linkedin_and_greenhouse_is_one_row_naming_both(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    gh = board("fake-ok", "greenhouse", "Acme AI")
    same = pages.Offer("AI Engineer", company="Acme AI", location="Paris, France")
    other = pages.Offer("GenAI Engineer 9", number=4100000009)
    tools = FakeTools(
        {
            pages.search_url(): pages.results([same, other]),
            pages.view(same): pages.posting(same),
            pages.view(other): pages.posting(other),
        }
    )
    set_profile(factory, alice, search(gh, browser_sources=[linkedin()]))
    row = run(factory, new_sweep(factory, alice), fake_ats, browser=checked(factory, tools))
    assert (
        next(source for source in row.stats["sources"] if source["kind"] == "linkedin")["error"]
        is None
    )
    jobs = [
        j for j in jobs_of(factory, alice) if j.title == "AI Engineer" and j.company == "Acme AI"
    ]
    assert len(jobs) == 1
    names = sweep.noted_sources(jobs[0].source)
    assert len(names) == 2
    assert names[1] == "linkedin:AI Engineer"
    assert any(j.title == "GenAI Engineer 9" for j in jobs_of(factory, alice))
    # The next day: the same postings note nothing more.
    tools.calls.clear()
    run(
        factory,
        new_sweep(factory, alice),
        fake_ats,
        browser=checked(factory, tools, datetime.now(UTC) + timedelta(days=1)),
    )
    again = [
        j for j in jobs_of(factory, alice) if j.title == "AI Engineer" and j.company == "Acme AI"
    ]
    assert sweep.noted_sources(again[0].source) == names


def test_linkedins_longer_location_dedupes_and_noting_keeps_updated_at(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    # The board first: its row says "Paris, France".
    gh = board("fake-ok", "greenhouse", "Acme AI")
    set_profile(factory, alice, search(gh))
    run(factory, new_sweep(factory, alice), fake_ats)
    [before] = [j for j in jobs_of(factory, alice) if j.title == "AI Engineer"]
    # Then LinkedIn's Easy Apply card of the same posting, with its longer location.
    same = pages.Offer(
        "AI Engineer", company="Acme AI", location="Paris, Île-de-France, France (Hybrid)"
    )
    tools = FakeTools(
        {pages.search_url(): pages.results([same]), pages.view(same): pages.posting(same)}
    )
    set_profile(factory, alice, search(browser_sources=[linkedin()]))
    row = run(factory, new_sweep(factory, alice), fake_ats, browser=checked(factory, tools))
    assert row.stats["sources"][0]["error"] is None
    [after] = [j for j in jobs_of(factory, alice) if j.title == "AI Engineer"]
    assert after.id == before.id
    assert sweep.noted_sources(after.source)[-1] == "linkedin:AI Engineer"
    assert after.updated_at == before.updated_at  # noting a source is not an edit


def test_a_second_sweep_the_same_day_reads_linkedin_again(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    made = pages.offers(1)
    site = {pages.search_url(): pages.results(made), pages.view(made[0]): pages.posting(made[0])}
    set_profile(factory, alice, search(browser_sources=[linkedin()]))
    first = run(
        factory,
        new_sweep(factory, alice),
        fake_ats,
        browser=checked(factory, FakeTools(dict(site))),
    )
    assert first.stats["sources"][0]["error"] is None
    tools = FakeTools(dict(site))
    second = run(factory, new_sweep(factory, alice), fake_ats, browser=checked(factory, tools))
    assert second.stats["sources"][0]["error"] is None
    assert tools.calls != []


def test_switched_off_after_a_sweep_stops(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(browser_sources=[linkedin(enabled=False)]))
    tools = FakeTools({pages.search_url(): pages.results([])})
    row = run(factory, new_sweep(factory, alice), fake_ats, browser=checked(factory, tools))
    # A disabled source is not even planned.
    assert row.stats.get("sources", []) == []
    assert tools.calls == []


def test_a_night_sweep_on_the_sign_in_wall_tells_the_person(
    factory: sessionmaker[Session], alice: uuid.UUID, fake_ats: FakeAts
) -> None:
    set_profile(factory, alice, search(browser_sources=[linkedin()]))
    tools = FakeTools({pages.search_url(): pages.SIGNIN})
    row = run(
        factory,
        new_sweep(factory, alice, SweepTrigger.schedule),
        fake_ats,
        browser=checked(factory, tools),
    )
    assert row.stats["sources"][0]["error"] == "linkedin_signin"
    with factory() as db:
        assert guard.state(db, alice).needs_signin is True


# --- the routes ---------------------------------------------------------------------------------


@pytest.fixture
def people(factory: sessionmaker[Session], api: Api) -> tuple[str, str, uuid.UUID]:
    a = make_user(factory, email="e2e-b10-a@example.org")
    make_user(factory, email="e2e-b10-b@example.org")
    return api.login_token("e2e-b10-a@example.org"), api.login_token("e2e-b10-b@example.org"), a.id


def put(api: Api, token: str, **body: Any) -> Any:
    return api.request("PUT", "/api/browser/linkedin", token=token, json=body)


def test_the_switch_is_off_by_default_and_needs_the_sentence(
    api: Api, people: tuple[str, str, uuid.UUID], factory: sessionmaker[Session]
) -> None:
    a, b, a_id = people
    got = api.get("/api/browser/linkedin", token=a).json()
    assert (got["enabled"], got["accepted"], got["allowed"]) == (False, False, False)
    assert got["text"] == guard.TEXT
    assert not any(dash in got["text"] for dash in (" - ", chr(0x2014), chr(0x2013)))
    assert put(api, a, enabled=True, query="AI Engineer").status_code == 409
    stale = put(api, a, enabled=True, query="AI Engineer", accept_version=guard.TEXT_VERSION + 1)
    assert stale.status_code == 409
    assert put(api, a, enabled=True, accept_version=guard.TEXT_VERSION).status_code == 422
    on = put(api, a, enabled=True, query="AI Engineer", accept_version=guard.TEXT_VERSION)
    assert on.status_code == 200, on.text
    assert (on.json()["enabled"], on.json()["accepted"], on.json()["allowed"]) == (True, True, True)
    assert on.json()["accepted_version"] == guard.TEXT_VERSION
    off = put(api, a, enabled=False)
    assert (off.json()["enabled"], off.json()["accepted"]) == (False, True)
    assert put(api, a, enabled=True).json()["allowed"] is True  # the same version: no new click
    # B's switch is untouched by A's.
    got_b = api.get("/api/browser/linkedin", token=b).json()
    assert (got_b["enabled"], got_b["accepted"]) == (False, False)
    with factory() as db:
        assert guard.allowed(db, a_id) is True


def test_the_routes_need_a_session(api: Api) -> None:
    assert api.get("/api/browser/linkedin").status_code == 401
    assert api.request("PUT", "/api/browser/linkedin", json={"enabled": False}).status_code == 401


def test_runs_off_the_event_loop() -> None:
    # sweep_check runs its queries in a thread: a coroutine function, as sources.Check wants.
    check = guard.sweep_check(lambda: None)  # type: ignore[arg-type, return-value]
    assert asyncio.iscoroutinefunction(check)
