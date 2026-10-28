"""Browser B14: the search's permission check reads each person's current platform consent and
records durable search checks in the sweep history (app/browser/platform_policy.py)."""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.browser import linkedin_guard
from app.browser.models import LinkedInCheckpoint
from app.browser.platform_policy import PlatformPolicy
from app.browser.sources.interactive import SourceContext
from app.jobs import sweep
from app.jobs.models import Sweep, SweepStatus, SweepTrigger
from app.opportunities.platforms import guard
from app.profile.schemas import SearchSection
from tests.identity.api_helpers import Api
from tests.opportunities import test_o7b_api as freework_api
from tests.opportunities import test_platform_consents as consent_api
from tests.opportunities.conftest import Person

URLS = {
    "linkedin": "https://www.linkedin.com/jobs/",
    "freework": "https://www.free-work.com/fr/tech-it/jobs",
    "indeed": "https://fr.indeed.com/",
    "welcome_to_the_jungle": "https://www.welcometothejungle.com/fr/jobs",
}
PLATFORMS = tuple(URLS)
RESULTS = '- heading "Jobs" [level=1]\n- link "Robot Engineer" [ref=e3]'
CHECKPOINT = '- heading "Verify you are human" [level=1]'


def accept(api: Api, who: Person, platform: str) -> None:
    if platform == "linkedin":
        response = api.request(
            "PUT",
            "/api/browser/linkedin",
            token=who.token,
            json={
                "enabled": True,
                "accept_version": linkedin_guard.TEXT_VERSION,
                "query": "robot",
            },
        )
        assert response.status_code == 200, response.text
    elif platform == "freework":
        freework_api.accept(api, who)
    else:
        consent_api.accept(api, who, platform)


def turn_off(api: Api, who: Person, platform: str) -> None:
    path = (
        "/api/browser/linkedin"
        if platform == "linkedin"
        else freework_api.PATH
        if platform == "freework"
        else consent_api.path(platform)
    )
    response = api.request("PUT", path, token=who.token, json={"enabled": False})
    assert response.status_code == 200, response.text


def running(
    factory: sessionmaker[Session],
    who: Person,
    at: datetime,
    urls: list[str],
    status: SweepStatus = SweepStatus.running,
) -> uuid.UUID:
    search = SearchSection.model_validate(
        {
            "title_include": {"terms": ["robot"]},
            "interactive_sites": [
                {"platform": "custom", "url": url, "enabled": True, "mode": "llm_browser"}
                for url in urls
            ],
        }
    )
    profile = type("P", (), {"sections": {"search": search.model_dump(mode="json")}, "revision": 1})
    scope = sweep.freeze_scope(profile, SweepTrigger.manual, at)
    with factory() as db, db.begin():
        row = Sweep(
            user_id=who.user_id,
            trigger=SweepTrigger.manual,
            status=status,
            started_at=at,
            finished_at=None if status is SweepStatus.running else at + timedelta(minutes=1),
            stats={
                "search_scope": scope,
                "sources": sweep.initial_sources(scope),
                "picked_at": at.isoformat(),
            },
        )
        db.add(row)
        db.flush()
        return row.id


def context(who: Person, sweep_id: uuid.UUID | None, url: str, platform: str) -> SourceContext:
    url = (
        SearchSection.model_validate(
            {
                "interactive_sites": [
                    {"platform": "custom", "url": url, "enabled": True, "mode": "llm_browser"}
                ]
            }
        )
        .interactive_sites[0]
        .url
    )
    return SourceContext(
        str(who.user_id),
        None if sweep_id is None else str(sweep_id),
        sweep._source_id("llm_browser", url),
        platform,
        url,
    )


def entries(factory: sessionmaker[Session], sweep_id: uuid.UUID) -> list[dict[str, Any]]:
    with factory() as db:  # a new session: only what was committed
        return db.get(Sweep, sweep_id).stats["sources"]  # type: ignore[union-attr]


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_each_platform_is_refused_without_consent_and_reserved_durably_with_it(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Any, platform: str
) -> None:
    policy = PlatformPolicy(factory, clock)
    sweep_id = running(factory, person_a, clock(), [URLS[platform]])
    ctx = context(person_a, sweep_id, URLS[platform], platform)
    assert run(policy.check(ctx)) == "permission_unavailable"
    assert run(policy.reserve(ctx)) == "permission_unavailable"
    assert "opened_at" not in entries(factory, sweep_id)[0]

    accept(api, person_a, platform)
    assert run(policy.check(ctx)) is None
    assert "opened_at" not in entries(factory, sweep_id)[0]  # a check alone records nothing
    assert run(policy.reserve(ctx)) is None
    stored = entries(factory, sweep_id)[0]
    assert stored["opened_at"] == clock().isoformat()
    assert stored["platform"] == platform
    assert run(policy.observe(ctx, URLS[platform], RESULTS)) is None


@pytest.mark.parametrize("platform", PLATFORMS)
def test_consent_is_per_person(
    api: Api,
    person_a: Person,
    person_b: Person,
    factory: sessionmaker[Session],
    clock: Any,
    platform: str,
) -> None:
    accept(api, person_a, platform)
    policy = PlatformPolicy(factory, clock)
    a = context(
        person_a, running(factory, person_a, clock(), [URLS[platform]]), URLS[platform], platform
    )
    b = context(
        person_b, running(factory, person_b, clock(), [URLS[platform]]), URLS[platform], platform
    )
    assert run(policy.check(a)) is None
    assert run(policy.check(b)) == "permission_unavailable"
    assert run(policy.reserve(b)) == "permission_unavailable"
    # A's checkpoint pauses A only.
    assert run(policy.reserve(a)) is None
    assert run(policy.observe(a, URLS[platform] + "?q=1", CHECKPOINT)) is not None
    accept(api, person_b, platform)
    assert run(policy.check(b)) is None
    assert run(policy.check(a)) == "permission_checkpoint"


def test_a_stale_sentence_version_is_not_consent(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Any, monkeypatch: Any
) -> None:
    accept(api, person_a, "indeed")
    policy = PlatformPolicy(factory, clock)
    ctx = context(
        person_a, running(factory, person_a, clock(), [URLS["indeed"]]), URLS["indeed"], "indeed"
    )
    assert run(policy.check(ctx)) is None
    newer = guard.Platform("Indeed", guard.RISK_TEXT, 2)
    monkeypatch.setitem(guard.PLATFORMS, "indeed", newer)
    assert run(policy.check(ctx)) == "permission_unavailable"


def test_a_custom_label_on_a_platform_address_needs_that_platform(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Any
) -> None:
    url = "https://www.indeed.fr/jobs"  # saved as a custom website; its host is Indeed's
    policy = PlatformPolicy(factory, clock)
    sweep_id = running(factory, person_a, clock(), [url])
    ctx = context(person_a, sweep_id, url, "custom")
    assert run(policy.check(ctx)) == "permission_unavailable"
    accept(api, person_a, "indeed")
    assert run(policy.reserve(ctx)) is None
    assert entries(factory, sweep_id)[0]["platform"] == "indeed"
    assert entries(factory, sweep_id)[0]["opened_at"] == clock().isoformat()


def test_no_sweep_identity_or_foreign_sweep_never_reserves(
    api: Api, person_a: Person, person_b: Person, factory: sessionmaker[Session], clock: Any
) -> None:
    accept(api, person_a, "indeed")
    policy = PlatformPolicy(factory, clock)
    assert run(policy.check(context(person_a, None, URLS["indeed"], "indeed"))) == (
        "permission_unavailable"
    )
    foreign = running(factory, person_b, clock(), [URLS["indeed"]])
    ctx = context(person_a, foreign, URLS["indeed"], "indeed")
    assert run(policy.reserve(ctx)) == "permission_unavailable"
    assert "opened_at" not in entries(factory, foreign)[0]


@pytest.mark.parametrize("platform", PLATFORMS)
def test_revocation_after_the_first_page_stops_the_next_call(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Any, platform: str
) -> None:
    accept(api, person_a, platform)
    policy = PlatformPolicy(factory, clock)
    ctx = context(
        person_a, running(factory, person_a, clock(), [URLS[platform]]), URLS[platform], platform
    )
    assert run(policy.check(ctx)) is None
    assert run(policy.reserve(ctx)) is None
    turn_off(api, person_a, platform)  # while the source waits for its page slot
    assert run(policy.check(ctx)) == "permission_revoked"


@pytest.mark.parametrize("platform", ["freework", "indeed", "welcome_to_the_jungle"])
def test_checkpoint_is_committed_before_the_page_returns_and_survives_stale_writes(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Any, platform: str
) -> None:
    accept(api, person_a, platform)
    policy = PlatformPolicy(factory, clock)
    sweep_id = running(factory, person_a, clock(), [URLS[platform]])
    ctx = context(person_a, sweep_id, URLS[platform], platform)
    assert run(policy.reserve(ctx)) is None
    assert run(policy.observe(ctx, URLS[platform], CHECKPOINT)) == "permission_checkpoint"
    # The process is lost here: what a new session reads is what was committed.
    stored = entries(factory, sweep_id)[0]
    assert stored["checkpoint_at"] == clock().isoformat()
    # A stale progress write, then a stale final write, of the same entry keep the record.
    # The stale copy was read before the records: it holds their old values.
    stale = sweep.Stats(
        sources=[
            {
                "source_id": ctx.source_id,
                "error": None,
                "opened_at": None,
                "checkpoint_at": None,
                "platform": "custom",
            }
        ]
    )
    sweep.save_progress(factory, sweep_id, stale, clock())
    sweep.finish_done(factory, sweep_id, stale, clock())
    final = entries(factory, sweep_id)[0]
    assert final["checkpoint_at"] == clock().isoformat()
    assert final["opened_at"] == clock().isoformat()
    assert final["platform"] == platform
    with factory() as db:
        assert guard.state(db, person_a.user_id, platform, clock()).paused_until == (
            clock() + guard.PAUSE
        )
    # Turning the platform off and on again, or saving the Search tab, does not lift it.
    turn_off(api, person_a, platform)
    accept(api, person_a, platform)
    later = PlatformPolicy(factory, lambda: clock() + timedelta(days=1))
    next_sweep = running(factory, person_a, clock() + timedelta(days=1), [URLS[platform]])
    assert run(later.check(context(person_a, next_sweep, URLS[platform], platform))) == (
        "permission_checkpoint"
    )


@pytest.mark.parametrize("platform", ["freework", "indeed", "welcome_to_the_jungle"])
def test_a_checkpoint_met_after_the_sweep_stopped_running_still_pauses_the_platform(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Any, platform: str
) -> None:
    """The reaper (or a cancel) ends the sweep row while the source still reads: the wall
    page it then meets is recorded all the same, so the 7 day pause holds."""
    accept(api, person_a, platform)
    policy = PlatformPolicy(factory, clock)
    sweep_id = running(factory, person_a, clock(), [URLS[platform]])
    ctx = context(person_a, sweep_id, URLS[platform], platform)
    assert run(policy.reserve(ctx)) is None
    with factory() as db, db.begin():
        row = db.get(Sweep, sweep_id)
        assert row is not None
        row.status, row.finished_at = SweepStatus.failed, clock()
    assert run(policy.observe(ctx, URLS[platform], CHECKPOINT)) == "permission_checkpoint"
    stored = entries(factory, sweep_id)[0]
    assert stored["checkpoint_at"] == clock().isoformat()
    assert stored["platform"] == platform
    with factory() as db:
        assert guard.state(db, person_a.user_id, platform, clock()).paused_until == (
            clock() + guard.PAUSE
        )


def test_seven_day_pause_ends_exactly_at_seven_days(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Any
) -> None:
    accept(api, person_a, "indeed")
    met = clock()
    first = running(factory, person_a, met, [URLS["indeed"]])
    ctx = context(person_a, first, URLS["indeed"], "indeed")
    policy = PlatformPolicy(factory, lambda: met)
    assert run(policy.reserve(ctx)) is None
    assert run(policy.observe(ctx, URLS["indeed"], CHECKPOINT)) == "permission_checkpoint"
    sweep.finish_done(factory, first, sweep.Stats(), met)
    for at, expected in (
        (met + guard.PAUSE - timedelta(seconds=1), "permission_checkpoint"),
        (met + guard.PAUSE, None),
    ):
        with factory() as db, db.begin():
            for row in db.query(Sweep).filter(Sweep.user_id == person_a.user_id, Sweep.id != first):
                db.delete(row)
        later = running(factory, person_a, at, [URLS["indeed"]])
        assert (
            run(
                PlatformPolicy(factory, lambda at=at: at).check(
                    context(person_a, later, URLS["indeed"], "indeed")
                )
            )
            == expected
        )


def test_checkpoint_in_the_second_entry_of_the_same_platform_pauses_it(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Any
) -> None:
    accept(api, person_a, "indeed")
    at = clock() - timedelta(days=1)
    with factory() as db, db.begin():
        db.add(
            Sweep(
                user_id=person_a.user_id,
                trigger=SweepTrigger.manual,
                status=SweepStatus.done,
                started_at=at,
                finished_at=at + timedelta(minutes=1),
                stats={
                    "sources": [
                        {"kind": "indeed", "opened_at": at.isoformat()},
                        {"kind": "custom", "platform": "indeed", "checkpoint_at": at.isoformat()},
                    ]
                },
            )
        )
    with factory() as db:
        assert guard.state(db, person_a.user_id, "indeed", clock()).paused_until == (
            at + guard.PAUSE
        )


@pytest.mark.parametrize("platform", ["linkedin", "indeed", "freework", "welcome_to_the_jungle"])
def test_no_daily_limit_a_second_sweep_of_the_day_opens_the_platform_again(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Any, platform: str
) -> None:
    """Owner, 2026-10-28: no limit on a website's searches a day, LinkedIn too. A second sweep
    the same day is checked and reserved like the first (consent, the 7 day pause, pacing)."""
    accept(api, person_a, platform)
    other = URLS[platform] + "search/"
    policy = PlatformPolicy(factory, clock)
    first = running(factory, person_a, clock(), [URLS[platform], other])
    assert run(policy.reserve(context(person_a, first, URLS[platform], platform))) is None
    assert run(policy.reserve(context(person_a, first, other, platform))) is None
    sweep.finish_done(factory, first, sweep.Stats(), clock())
    for minutes in (5, 10):
        later = clock() + timedelta(minutes=minutes)
        second = running(factory, person_a, later, [other])
        assert run(policy.check(context(person_a, second, other, platform))) is None
        assert run(policy.reserve(context(person_a, second, other, platform))) is None
        assert entries(factory, second)[0]["platform"] == platform
        sweep.finish_done(factory, second, sweep.Stats(), later)


def test_linkedin_has_no_page_cap_and_keeps_its_checkpoint_table(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Any
) -> None:
    accept(api, person_a, "linkedin")
    one, two = URLS["linkedin"], "https://www.linkedin.com/jobs/search/"
    sweep_id = running(factory, person_a, clock(), [one, two])
    policy = PlatformPolicy(factory, clock)
    first, second = (
        context(person_a, sweep_id, one, "linkedin"),
        context(person_a, sweep_id, two, "linkedin"),
    )
    assert run(policy.reserve(first)) is None
    for page in range(12):
        assert run(policy.check(first)) is None
        assert run(policy.observe(first, f"{one}?start={page * 25}", RESULTS)) is None
    # No page cap for LinkedIn either (owner, 2026-10-28): its pace and pause bound it.
    assert run(policy.check(first)) is None
    assert run(policy.check(second)) is None
    other = PlatformPolicy(factory, clock)
    ctx = context(person_a, sweep_id, one, "linkedin")
    wall = "https://www.linkedin.com/checkpoint/challenge/"
    assert run(other.observe(ctx, wall, RESULTS)) == "linkedin_checkpoint"
    with factory() as db:
        rows = db.query(LinkedInCheckpoint).filter_by(user_id=person_a.user_id).all()
        assert [row.origin for row in rows] == ["sweep"]
        assert linkedin_guard.refusal(db, person_a.user_id, clock()) == "linkedin_paused"
    assert run(other.check(second)) == "permission_checkpoint"


def test_sign_in_page_is_recorded_and_read_by_the_guard(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Any
) -> None:
    accept(api, person_a, "welcome_to_the_jungle")
    url = URLS["welcome_to_the_jungle"]
    sweep_id = running(factory, person_a, clock(), [url])
    policy = PlatformPolicy(factory, clock)
    ctx = context(person_a, sweep_id, url, "welcome_to_the_jungle")
    assert run(policy.reserve(ctx)) is None
    signin = "https://www.welcometothejungle.com/fr/signin"
    assert run(policy.observe(ctx, signin, RESULTS)) == "platform_signin"
    assert entries(factory, sweep_id)[0]["error"] == "platform_signin"
    with factory() as db:
        state = guard.state(db, person_a.user_id, "welcome_to_the_jungle", clock())
    assert state.needs_signin is True
    assert state.paused_until is None


def test_a_failed_write_stops_the_source(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Any
) -> None:
    accept(api, person_a, "indeed")
    sweep_id = running(factory, person_a, clock(), [URLS["indeed"]])
    ctx = context(person_a, sweep_id, URLS["indeed"], "indeed")

    def broken() -> Session:
        raise RuntimeError("e2e-db-down")

    policy = PlatformPolicy(broken, clock)  # type: ignore[arg-type]
    assert run(policy.check(ctx)) == "permission_unavailable"
    assert run(policy.reserve(ctx)) == "permission_unavailable"
    assert run(policy.observe(ctx, URLS["indeed"], CHECKPOINT)) == "permission_unavailable"


def test_the_agent_of_linkedin_runs_again_in_a_second_sweep_of_the_day(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Any
) -> None:
    """Jobs J18: the agent of the website reserves LinkedIn for its sweep once, through the real
    guard, and runs both its searches there; another sweep the same day runs them again (no
    daily limit, owner 2026-10-28)."""
    from tests.browser.fakes import ScriptedFinder
    from tests.browser.test_multi_search import LINKEDIN, SearchSite, searching
    from tests.browser.test_selected_interactive import execute, selected

    accept(api, person_a, "linkedin")
    policy = PlatformPolicy(factory, clock)
    search = selected(LINKEDIN, title_include={"terms": ["a1", "b2"]})

    def sweep_of(sweep_id: uuid.UUID) -> tuple[dict[str, Any], Any]:
        site = SearchSite(base=LINKEDIN, results={"a1": ["a1 Engineer"]})
        found, _, leases, _, _ = run(
            execute(
                search,
                str(person_a.user_id),
                ScriptedFinder(searching(site)),
                tools=site,
                deadline=100_000,
                policy=policy,
                sweep_id=str(sweep_id),
            )
        )
        [entry] = found
        return entry, leases

    first = running(factory, person_a, clock(), [LINKEDIN])
    entry, leases = sweep_of(first)
    # One lease, both searches on LinkedIn (no commits here: found is counted, not saved).
    assert (entry["searches"], entry["error"], entry["found"]) == (
        2,
        "finding_persistence_pending",
        1,
    )
    assert len(leases.acquired) == 1
    assert entries(factory, first)[0]["opened_at"] == clock().isoformat()
    with factory() as db, db.begin():
        row = db.get(Sweep, first)
        assert row is not None
        row.status, row.finished_at = SweepStatus.done, clock()

    second = running(factory, person_a, clock(), [LINKEDIN])
    entry, leases = sweep_of(second)
    assert (entry["searches"], entry["error"]) == (2, "finding_persistence_pending")
    assert len(leases.acquired) == 1
    assert entries(factory, second)[0]["opened_at"] == clock().isoformat()
