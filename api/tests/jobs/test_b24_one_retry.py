"""Browser B24 (issue 785, owner 2026-11-10: "si le run fail pour la recherche d'un job, je veux
un seul nouvel essai. cela ne doit pas bloquer les autres."): a source of a sweep that ends on a
failure a second attempt can change runs once more, after every other source had its first
attempt, within the sweep's bound (app/jobs/sweep.py, `_retry`). Against real Postgres, with the
real website Runner (its search a stand in) and LinkedIn's public job search on a fake LinkedIn.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.browser import finder, sources
from app.browser.sources import interactive, linkedin_guest
from app.jobs import feeds, sweep
from app.jobs.models import SweepStatus
from tests.browser.fakes import FakeLeases
from tests.browser.test_linkedin_guest import Allow, LinkedInFake
from tests.jobs.sweep_helpers import (
    HOST,
    HOST_2,
    SITE,
    SITE_2,
    finding,
    jobs_of,
    new_sweep,
    now,
    read_sweep,
    search,
    set_profile,
    website_runner,
)

LINKEDIN = {"platform": "linkedin", "url": "https://www.linkedin.com/", "enabled": True,
            "mode": "llm_browser"}  # fmt: skip
FIXTURES = "http://fixtures.example.test"
Searched = Callable[[str, int, Any], Awaitable[str | None]]


def entry_of(stats: dict[str, Any], host: str) -> dict[str, Any]:
    return next(entry for entry in stats["sources"] if entry.get("host") == host)


async def _yes(_: str) -> bool:
    return True


async def _nothing(_: str) -> str:
    return ""


def runner(**options: Any) -> sources.Runner:
    options.setdefault("finder", SimpleNamespace(available=_yes, profile=_nothing))
    return website_runner(object(), FakeLeases(), **options)


def stand_in(monkeypatch: pytest.MonkeyPatch, searched: Searched, calls: list[str]) -> None:
    """The website's search: `searched(host, attempt, site)` says how it ends; it may store
    through the website's own path (`site.verified`)."""

    async def search_stand_in(agent: Any, pages: Any, scope: Any, user: str, site: Any, *_: Any):
        host = site.host
        calls.append(host)
        return await searched(host, calls.count(host), site)

    monkeypatch.setattr(interactive, "_search", search_stand_in)


async def store(site: Any, *found: finder.VerifiedFinding) -> None:
    await site.verified(finder.VerifiedBatch(tuple(found)))


def posting(title: str, host: str = HOST) -> finder.VerifiedFinding:
    slug = "-".join(title.lower().split())
    return finding(title, url=f"https://{host}/jobs/{slug}")


def run(factory: sessionmaker[Session], sweep_id: uuid.UUID, **hooks: Any) -> Any:
    asyncio.run(sweep.run_sweep(factory, sweep_id, now, **hooks))
    return read_sweep(factory, sweep_id)


# --- the websites -------------------------------------------------------------------------------


def test_a_failed_website_is_retried_once_after_the_others_and_its_postings_stored(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_profile(factory, alice, search(interactive_sites=[SITE, SITE_2]))
    sweep_id = new_sweep(factory, alice)
    calls: list[str] = []

    async def searched(host: str, attempt: int, site: Any) -> str | None:
        if host == HOST and attempt == 1:
            await store(site, posting("Robotics Engineer"))
            return "agent_failed"
        await store(site, posting("GenAI Engineer" if host == HOST_2 else "Forward Deployed", host))
        return None

    stand_in(monkeypatch, searched, calls)
    row = run(factory, sweep_id, browser=runner().websites)
    assert row.status is SweepStatus.done
    # The failing website's retry comes once the other one had its first attempt.
    assert calls == [HOST, HOST_2, HOST]
    entry = entry_of(row.stats, HOST)
    assert entry["attempt"] == 2
    assert entry["first_attempt"] == {
        "outcome": "partial",
        "reason": "agent_failed",
        "found": 1,
        "postings": 1,
    }
    # What both attempts stored, counted as any other postings.
    assert (entry["outcome"], entry["reason"], entry["found"], entry["postings"]) == (
        "complete",
        None,
        2,
        2,
    )
    assert "attempt" not in entry_of(row.stats, HOST_2)
    assert row.stats["new"] == 3
    assert sorted(job.title for job in jobs_of(factory, alice)) == [
        "Forward Deployed",
        "GenAI Engineer",
        "Robotics Engineer",
    ]


def test_a_second_failure_is_not_retried_again(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_profile(factory, alice, search(interactive_sites=[SITE]))
    sweep_id = new_sweep(factory, alice)
    calls: list[str] = []

    async def searched(host: str, attempt: int, site: Any) -> str | None:
        return "browser_unavailable" if attempt == 1 else "nothing_reported"

    stand_in(monkeypatch, searched, calls)
    row = run(factory, sweep_id, browser=runner().websites)
    assert calls == [HOST, HOST]
    entry = entry_of(row.stats, HOST)
    assert (entry["attempt"], entry["outcome"], entry["reason"]) == (
        2,
        "failed",
        "nothing_reported",
    )
    assert entry["first_attempt"]["reason"] == "browser_unavailable"
    assert row.status is SweepStatus.done


@pytest.mark.parametrize(
    "code",
    ["no_credential", "signin_wall", "security_check", "cookie_wall", "agent_cancelled"],
)
def test_a_failure_the_person_causes_is_not_retried(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    set_profile(factory, alice, search(interactive_sites=[SITE]))
    sweep_id = new_sweep(factory, alice)
    calls: list[str] = []

    async def searched(host: str, attempt: int, site: Any) -> str | None:
        return code

    stand_in(monkeypatch, searched, calls)
    row = run(factory, sweep_id, browser=runner().websites)
    assert calls == [HOST]
    entry = entry_of(row.stats, HOST)
    assert (entry["reason"], entry.get("attempt"), entry.get("first_attempt")) == (code, None, None)


def test_a_retry_a_stop_keeps_from_starting_leaves_the_first_attempt(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two websites fail; the first one's retry finds the browser taken by the person
    (`lease_ended`, a stop of the websites): the second one's retry never starts and its entry
    says so, its first attempt standing."""
    set_profile(factory, alice, search(interactive_sites=[SITE, SITE_2]))
    sweep_id = new_sweep(factory, alice)
    calls: list[str] = []

    async def searched(host: str, attempt: int, site: Any) -> str | None:
        return "agent_failed" if attempt == 1 else "lease_ended"

    stand_in(monkeypatch, searched, calls)
    row = run(factory, sweep_id, browser=runner().websites)
    assert calls == [HOST, HOST_2, HOST]
    first, second = entry_of(row.stats, HOST), entry_of(row.stats, HOST_2)
    assert (first["attempt"], first["reason"]) == (2, "lease_ended")
    assert (second["attempt"], second["not_retried"]) == (1, "not_started")
    assert (second["phase"], second["outcome"], second["reason"]) == (
        "done",
        "failed",
        "agent_failed",
    )
    assert second["first_attempt"] is None


# --- LinkedIn's public job search ---------------------------------------------------------------


def linkedin_run(
    factory: sessionmaker[Session], alice: uuid.UUID, answers: dict[int, tuple[int, dict]]
) -> tuple[Any, LinkedInFake]:
    set_profile(factory, alice, search(interactive_sites=[LINKEDIN]))
    sweep_id = new_sweep(factory, alice)
    clock = [0.0]
    linkedin = LinkedInFake(clock, answers=answers)
    hooks = runner(
        base_url=FIXTURES,
        interactive_policy=Allow(),
        guest_http=lambda: httpx.MockTransport(linkedin.handler),
        guest_cooldown=linkedin_guest.MemoryCooldown(lambda: clock[0]),
    )
    return run(factory, sweep_id, apis=(hooks.linkedin,), browser=hooks.websites), linkedin


def test_linkedin_in_its_cooldown_is_recorded_as_not_retried(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    row, linkedin = linkedin_run(factory, alice, {1: (429, {})})
    entry = next(e for e in row.stats["sources"] if e.get("kind") == "linkedin")
    assert (entry["outcome"], entry["reason"]) == ("failed", "linkedin_rate_limited")
    assert (entry["attempt"], entry["not_retried"]) == (1, "linkedin_cooldown")
    assert len(linkedin.requests) == 1  # no request in the cooldown
    assert row.status is SweepStatus.done


def test_linkedin_failing_with_no_cooldown_is_retried(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    row, linkedin = linkedin_run(factory, alice, {1: (500, {})})
    entry = next(e for e in row.stats["sources"] if e.get("kind") == "linkedin")
    assert entry["attempt"] == 2
    assert entry["first_attempt"]["reason"] == "page_failed"
    assert len(linkedin.requests) > 1


# --- order and bounds ---------------------------------------------------------------------------


def bound_source(
    mode: str, key: str, log: list[str], ends: Callable[[int], Awaitable[str | None]]
) -> Any:
    """A source hook as the real ones bind their entry: it runs when the plan's search holds it
    (a job feed `key`, or the alert e-mails of provider `key`)."""
    attempts = [0]

    async def hook(plan: sweep.Plan, stats: sweep.Stats, progress: Any, deadline: float) -> None:
        held = sweep.alert_keys(plan.search) if mode == "mail" else plan.search.job_feeds
        if key not in held:
            return
        attempts[0] += 1
        entry = sweep.bind_source(stats, mode, (key,), {"found": 0, "postings": 0, "error": None})
        log.append(f"{key} {attempts[0]} started")
        entry["error"] = await ends(attempts[0])
        sweep.finish_source(entry)
        await progress()
        log.append(f"{key} {attempts[0]} ended")

    return hook


def test_the_other_sources_end_their_first_attempt_before_any_retry_which_never_delays_them(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_profile(
        factory,
        alice,
        search(
            alert_emails={"linkedin": True},
            job_feeds=["arbeitnow"],
            interactive_sites=[SITE, SITE_2],
        ),
    )
    sweep_id = new_sweep(factory, alice)
    log: list[str] = []
    website_started = asyncio.Event()

    async def feed(attempt: int) -> str | None:
        if attempt == 2:
            # The feed's retry ends only once a website started: it runs beside them.
            await website_started.wait()
        return "network" if attempt == 1 else None

    async def mail(attempt: int) -> str | None:
        await asyncio.sleep(0)  # ends after the feed started
        return None

    calls: list[str] = []

    async def searched(host: str, attempt: int, site: Any) -> str | None:
        website_started.set()
        log.append(f"{host} {attempt}")
        return "page_failed" if host == HOST and attempt == 1 else None

    stand_in(monkeypatch, searched, calls)
    row = run(
        factory,
        sweep_id,
        alerts=bound_source("mail", "linkedin", log, mail),
        feeds=bound_source("api", "arbeitnow", log, feed),
        browser=runner().websites,
    )
    assert row.status is SweepStatus.done
    assert set(log[:4]) == {
        "arbeitnow 1 started",
        "arbeitnow 1 ended",
        "linkedin 1 started",
        "linkedin 1 ended",
    }
    assert log.index("arbeitnow 2 started") > log.index("linkedin 1 ended")
    assert log.index(f"{HOST} 1") < log.index("arbeitnow 2 ended")
    assert log.index(f"{HOST} 2") > log.index(f"{HOST_2} 1")
    assert "linkedin 2 started" not in log
    feed_entry = next(e for e in row.stats["sources"] if e.get("kind") == "arbeitnow")
    assert (feed_entry["attempt"], feed_entry["outcome"]) == (2, "no_matches")


def test_no_retry_starts_this_close_to_the_sweeps_bound(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_profile(factory, alice, search(job_feeds=["arbeitnow"], interactive_sites=[SITE]))
    sweep_id = new_sweep(factory, alice)
    clock = [0.0]
    monkeypatch.setattr(sweep, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    log: list[str] = []

    async def feed(attempt: int) -> str | None:
        clock[0] = sweep.SWEEP_TIMEOUT_S - sweep.RETRY_MIN_S + 1  # it took nearly all of it
        return "timeout"

    async def searched(host: str, attempt: int, site: Any) -> str | None:
        return None

    stand_in(monkeypatch, searched, [])
    row = run(
        factory, sweep_id, feeds=bound_source("api", "arbeitnow", log, feed),
        browser=runner().websites,
    )  # fmt: skip
    entry = next(e for e in row.stats["sources"] if e.get("kind") == "arbeitnow")
    assert (entry["reason"], entry["attempt"], entry["not_retried"]) == ("timeout", 1, "no_time")
    assert log == ["arbeitnow 1 started", "arbeitnow 1 ended"]


def test_a_retry_that_outlives_the_sweeps_bound_ends_there_and_the_sweep_is_done(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_profile(factory, alice, search(job_feeds=["arbeitnow"], interactive_sites=[SITE]))
    sweep_id = new_sweep(factory, alice)
    clock = [0.0]
    monkeypatch.setattr(sweep, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(sweep, "RETRY_MIN_S", 0.0)
    log: list[str] = []
    never = asyncio.Event()

    async def feed(attempt: int) -> str | None:
        if attempt == 2:
            await never.wait()  # a retry that would run past the bound
        # The first attempt took all but a twentieth of a second of the step's bound.
        clock[0] = sweep.SWEEP_TIMEOUT_S - 0.05
        return "upstream_error"

    async def searched(host: str, attempt: int, site: Any) -> str | None:
        return None

    stand_in(monkeypatch, searched, [])
    row = run(
        factory, sweep_id, feeds=bound_source("api", "arbeitnow", log, feed),
        browser=runner().websites,
    )  # fmt: skip
    assert row.status is SweepStatus.done
    entry = next(e for e in row.stats["sources"] if e.get("kind") == "arbeitnow")
    assert (entry["attempt"], entry["phase"], entry["reason"]) == (2, "done", "time_budget")
    assert "arbeitnow 2 ended" not in log


# --- what is retried ----------------------------------------------------------------------------


def test_every_code_a_source_ends_with_is_retried_or_not_never_both() -> None:
    """A code no list names is not retried (fail closed); this keeps every known one named."""
    codes = (
        set(sources.ERRORS)
        | set(feeds.ERRORS)
        | {code for code in finder.NO_POSTINGS.values() if code}
        | {
            interactive.SIGNIN_WALL,
            "search_refused",
            "unsupported_interaction",
            "nothing_reported",
            "unsupported_criteria",
            "agent_cancelled",
            "finding_persistence_pending",
            "guard_failed",
            sources.NOT_STARTED,
            linkedin_guest.RATE_LIMITED,
            "linkedin_searched_once",
            "store_refused",
            "no_titles",
            "host_rate_limited",
            "mail_not_connected",
            "mail_needs_reconnect",
            "mail_unavailable",
            "mail_failed",
            "time_limit",
        }
    )
    assert not sweep.RETRIED & sweep.NOT_RETRIED
    assert codes - sweep.RETRIED - sweep.NOT_RETRIED == set()
