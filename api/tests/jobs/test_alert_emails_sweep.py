"""Job alert e-mails in a sweep (app/mail/alerts/source.py; SPEC-mail.md, "Job alert e-mails: a
narrow exception"), against real Postgres with a fake mailbox: the postings of an authenticated
alert go through the sweep's commit path (scored, rule checked, deduplicated), a refused sender
gives nothing, the cursor reads each mail once, no link is ever fetched, and the setting is the
person's per provider switch in Find jobs setup.
"""

from __future__ import annotations

import asyncio
import socket
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import worker
from app.config import Settings
from app.jobs import sweep
from app.jobs.models import Job, JobEvent, JobEventKind, JobStatus, SweepStatus, SweepTrigger
from app.mail.alerts import source
from app.mail.google import SCOPE_READONLY
from app.mail.models import AccountStatus, MailAccount
from app.profile import service as profile_service
from app.profile.schemas import validate_section
from tests.conftest import make_settings
from tests.jobs.sweep_helpers import (
    SITE,
    finding,
    jobs_of,
    new_sweep,
    read_sweep,
    run,
    search,
    set_profile,
    websites,
)
from tests.mail import alert_support as a

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
CONNECTED = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
FIRST_MS = 1_793_343_600_000  # 2026-10-30 07:00 UTC
JK = ("0a1b2c3d4e5f6a7b", "1234567890abcdef", "fedcba9876543210")


def connect(
    factory: sessionmaker[Session],
    user_id: uuid.UUID,
    *,
    connected_at: datetime = CONNECTED,
    status: AccountStatus = AccountStatus.ok,
    scopes: tuple[str, ...] = (SCOPE_READONLY,),
) -> None:
    with factory() as db, db.begin():
        db.execute(sa.delete(MailAccount).where(MailAccount.user_id == user_id))
        db.add(
            MailAccount(
                user_id=user_id,
                email=a.ME,
                google_sub=f"sub-{user_id}",
                refresh_token_encrypted=b"sealed-in-the-test",
                key_version=1,
                scopes=list(scopes),
                connected_at=connected_at,
                status=status,
            )
        )


def alerts_on(**overrides: Any) -> dict[str, Any]:
    """A search with no website and Indeed's alert e-mails on."""
    return search(**{"interactive_sites": [], "alert_emails": {"indeed": True}, **overrides})


def reader(
    factory: sessionmaker[Session], mailbox: Any, html: dict[str, str | None] | None = None
) -> source.Reader:
    async def html_text(markup: str) -> str | None:
        return (html or {}).get("version")

    return source.Reader(factory, mailbox, html_text, now=lambda: NOW)


def sweep_with(
    factory: sessionmaker[Session], user_id: uuid.UUID, mailbox: Any, **options: Any
) -> Any:
    sweep_id = new_sweep(factory, user_id)
    hook = options.pop("hook", None) or reader(factory, mailbox, options.pop("html", None))
    options.setdefault("browser", None)
    return run(factory, sweep_id, alerts=hook, **options)


def entry(row: Any, key: str = "indeed") -> dict[str, Any]:
    found = [s for s in row.stats["sources"] if s.get("host") == f"mail:{key}"]
    assert len(found) == 1, row.stats["sources"]
    return found[0]


def indeed_mail(**options: Any) -> dict[str, Any]:
    options.setdefault("internal_ms", FIRST_MS)
    return a.gmail_json(a.eml("indeed-fr-plain.eml"), **options)


def test_an_authenticated_alert_s_postings_are_stored_scored_and_attributed(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, alerts_on())
    connect(factory, alice)
    mailbox = a.FakeMailbox([indeed_mail(message_id="m-first")])
    row = sweep_with(factory, alice, mailbox)

    assert row.status is SweepStatus.done, row.error
    jobs = jobs_of(factory, alice)
    assert sorted(job.url for job in jobs) == sorted(
        f"https://fr.indeed.com/viewjob?jk={key}" for key in JK
    )
    assert {job.source for job in jobs} == {"mail:indeed"}
    assert {job.status for job in jobs} == {JobStatus.found}
    assert all(job.fit is not None for job in jobs)  # scored on the snippet
    data = next(job for job in jobs if job.company == "Exemple Analytique")
    assert data.title == "Ingénieur données confirmé"
    assert data.location == "Lyon (69)"
    assert "Concevoir et maintenir" in (data.posting_text or "")
    assert source.NOTE in (data.posting_text or "")
    assert data.published_on is None  # an alert's "il y a 1 jour" is no date

    stats = entry(row)
    assert stats["mode"] == "mail"
    assert stats["kind"] == "alerts"
    assert stats["outcome"] == "complete"
    assert stats["phase"] == "done"
    assert (stats["found"], stats["postings"], stats["messages"], stats["dropped"]) == (3, 3, 1, 1)
    assert stats["cursor"] == {"after_ms": FIRST_MS, "account": CONNECTED.isoformat()}
    assert row.stats["new"] == 3

    with factory() as db:
        events = db.scalars(
            sa.select(JobEvent).where(
                JobEvent.job_id == data.id, JobEvent.kind == JobEventKind.sweep
            )
        ).all()
    discovery = events[0].data["discovery"]
    assert discovery["evidence"] == {
        "alert": "indeed",
        "gmail_message_id": "m-first",
        "description": "snippet",
    }
    assert discovery["unknown"] == ["published_on"]
    # What was read: one search and one message, of the provider's exact senders.
    assert mailbox.calls[0][0] == "list"
    assert "from:(alert@indeed.com OR " in mailbox.calls[0][1]
    assert mailbox.calls[1:] == [("get", "m-first")]


@pytest.mark.parametrize(
    "options",
    [
        {"auth": "spf-only"},
        {"auth": "dmarc-fail"},
        {"auth": "none"},
        {"from_header": "Indeed <jobs@indeed.com>"},
        {"from_header": "alert@indeed.com <evil@evil.example>", "domain": "evil.example"},
    ],
)
def test_a_refused_sender_gives_nothing(
    factory: sessionmaker[Session], alice: uuid.UUID, options: dict[str, str]
) -> None:
    set_profile(factory, alice, alerts_on())
    connect(factory, alice)
    row = sweep_with(factory, alice, a.FakeMailbox([indeed_mail(**options)]))
    assert jobs_of(factory, alice) == []
    stats = entry(row)
    assert (stats["refused"], stats["messages"], stats["outcome"]) == (1, 0, "no_matches")
    assert stats["cursor"]["after_ms"] == FIRST_MS  # never read again


def test_the_cursor_reads_each_mail_once_and_a_new_connection_starts_over(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, alerts_on())
    connect(factory, alice)
    first = indeed_mail(message_id="m-first")
    sweep_with(factory, alice, a.FakeMailbox([first]))

    later = a.gmail_json(a.eml("indeed-latin1.eml"), internal_ms=FIRST_MS + 3_600_000)
    mailbox = a.FakeMailbox([first, later])
    row = sweep_with(factory, alice, mailbox)
    stats = entry(row)
    assert f"after:{FIRST_MS // 1000} " in mailbox.calls[0][1]
    assert (stats["messages"], stats["postings"], stats["duplicates"]) == (1, 1, 0)
    assert stats["cursor"]["after_ms"] == FIRST_MS + 3_600_000
    assert len(jobs_of(factory, alice)) == 4

    # Connected again: the cursor of the old connection is not this one's.
    connect(factory, alice, connected_at=CONNECTED + timedelta(days=5))
    mailbox = a.FakeMailbox([first, later])
    row = sweep_with(factory, alice, mailbox)
    stats = entry(row)
    first_day = int((NOW - timedelta(days=source.FIRST_DAYS)).timestamp())
    assert f"after:{first_day} " in mailbox.calls[0][1]
    assert (stats["messages"], stats["postings"], stats["duplicates"]) == (2, 0, 4)
    assert len(jobs_of(factory, alice)) == 4


def test_quiet_sweeps_carry_the_cursor_forward(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, alerts_on())
    connect(factory, alice)
    first = indeed_mail(message_id="m-first")
    sweep_with(factory, alice, a.FakeMailbox([first]))
    # More quiet sweeps than the cursor is looked for in: each one writes it again.
    for _ in range(source.CURSOR_SWEEPS + 1):
        row = sweep_with(factory, alice, a.FakeMailbox([]))
        assert entry(row)["cursor"] == {"after_ms": FIRST_MS, "account": CONNECTED.isoformat()}
    mailbox = a.FakeMailbox([first])
    row = sweep_with(factory, alice, mailbox)
    assert f"after:{FIRST_MS // 1000} " in mailbox.calls[0][1]
    assert (entry(row)["messages"], entry(row)["postings"]) == (0, 0)  # never read again


def test_the_first_sweep_reads_nothing_older_than_the_connection(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, alerts_on())
    connected = NOW - timedelta(days=2)
    connect(factory, alice, connected_at=connected)
    before = indeed_mail(internal_ms=int((connected - timedelta(hours=1)).timestamp() * 1000))
    after = a.gmail_json(
        a.eml("indeed-latin1.eml"),
        internal_ms=int((connected + timedelta(hours=1)).timestamp() * 1000),
    )
    # Gmail's `after:` is a pre-filter: the fake lists both, the code still skips the older one.
    mailbox = a.FakeMailbox([before, after])
    row = sweep_with(factory, alice, mailbox)
    assert f"after:{int(connected.timestamp())} " in mailbox.calls[0][1]
    stats = entry(row)
    assert (stats["messages"], stats["postings"]) == (1, 1)
    assert [job.company for job in jobs_of(factory, alice)] == ["Exemple Énergie"]


@pytest.mark.parametrize("where", ["parse", "parser"])
def test_one_bad_message_is_counted_and_passed_never_stopping_its_provider(
    factory: sessionmaker[Session],
    alice: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    where: str,
) -> None:
    set_profile(factory, alice, alerts_on())
    connect(factory, alice)
    if where == "parse":
        original = source.parse.parse

        def parse(raw: Any, own: str) -> Any:
            if raw["id"] == "m-bad":
                raise RuntimeError("a parser bug")
            return original(raw, own)

        monkeypatch.setattr(source.parse, "parse", parse)
    else:
        read = source.read_jobs

        def read_jobs(message: Any, provider: Any) -> Any:
            if message.gmail_message_id == "m-bad":
                raise RuntimeError("a parser bug")
            return read(message, provider)

        monkeypatch.setattr(source, "read_jobs", read_jobs)
    bad = indeed_mail(message_id="m-bad")
    good = a.gmail_json(
        a.eml("indeed-latin1.eml"), internal_ms=FIRST_MS + 1000, message_id="m-good"
    )
    row = sweep_with(factory, alice, a.FakeMailbox([bad, good]))
    stats = entry(row)
    assert (stats["reason"], stats["outcome"]) == (None, "complete")
    assert (stats["failed_messages"], stats["postings"]) == (1, 1)
    assert stats["cursor"]["after_ms"] == FIRST_MS + 1000
    # The next sweep is past both: the bad one is not read again.
    row = sweep_with(factory, alice, a.FakeMailbox([bad, good]))
    assert (entry(row)["failed_messages"], entry(row)["messages"]) == (0, 0)


def test_a_job_found_twice_is_one_row_naming_both_sources(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    # A website of the person's found the first job before (same company, title and place).
    set_profile(factory, alice, search())
    run(
        factory,
        new_sweep(factory, alice),
        finding("Ingénieur données confirmé", "Exemple Analytique", "Lyon (69)"),
    )
    set_profile(factory, alice, alerts_on())
    connect(factory, alice)
    copy = indeed_mail(message_id="m-copy", internal_ms=FIRST_MS + 1000)
    row = sweep_with(factory, alice, a.FakeMailbox([indeed_mail(message_id="m-one"), copy]))
    stats = entry(row)
    assert (stats["messages"], stats["postings"], stats["duplicates"]) == (2, 2, 4)
    jobs = jobs_of(factory, alice)
    assert len(jobs) == 3
    both = next(job for job in jobs if job.company == "Exemple Analytique")
    assert both.source == "jobs.example-robotics.test, mail:indeed"


def test_no_link_of_an_alert_is_ever_fetched(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_profile(factory, alice, alerts_on())
    connect(factory, alice)
    tried: list[str] = []

    def refuse(*args: Any, **kwargs: Any) -> Any:
        tried.append(repr(args)[:80])
        raise AssertionError("a network call")

    async def refuse_async(*args: Any, **kwargs: Any) -> Any:
        return refuse(*args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse_async)
    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    sweep_with(factory, alice, a.FakeMailbox([indeed_mail()]), html={"version": None})
    assert tried == []
    assert len(jobs_of(factory, alice)) == 3


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        ("none", "mail_not_connected"),
        ("disconnecting", "mail_not_connected"),
        ("no_read_scope", "mail_not_connected"),
        ("needs_reconnect", "mail_needs_reconnect"),
        ("token_refused", "mail_needs_reconnect"),
        ("gmail_down", "mail_unavailable"),
        ("no_client", "mail_unavailable"),
    ],
)
def test_a_provider_that_cannot_read_ends_with_its_reason_and_the_sweep_goes_on(
    factory: sessionmaker[Session], alice: uuid.UUID, setup: str, code: str
) -> None:
    set_profile(factory, alice, alerts_on(interactive_sites=[SITE]))
    mailbox: Any = a.FakeMailbox([indeed_mail()])
    if setup != "none":
        connect(
            factory,
            alice,
            status=AccountStatus.needs_reconnect
            if setup == "needs_reconnect"
            else AccountStatus.ok,
            scopes=() if setup == "no_read_scope" else (SCOPE_READONLY,),
        )
    if setup == "disconnecting":
        with factory() as db, db.begin():
            db.execute(sa.update(MailAccount).values(disconnecting_at=NOW))
    if setup == "token_refused":
        mailbox.error = {"error": "needs_reconnect"}
    if setup == "gmail_down":
        mailbox.fail_after = 0
    if setup == "no_client":
        mailbox = None
    row = sweep_with(factory, alice, mailbox, browser=websites(finding("AI Engineer")))
    assert row.status is SweepStatus.done
    stats = entry(row)
    assert (stats["reason"], stats["outcome"]) == (code, "failed")
    assert [job.title for job in jobs_of(factory, alice)] == ["AI Engineer"]  # the website ran


def test_the_person_s_filters_and_rules_hold(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(
        factory,
        alice,
        alerts_on(
            title_exclude={"terms": ["analyste"], "advanced_regex": False},
            location_exclude={"terms": ["villeurbanne"], "advanced_regex": False},
            never_apply=["No data platform roles: confirmé (test rule)"],
        ),
    )
    connect(factory, alice)
    row = sweep_with(factory, alice, a.FakeMailbox([indeed_mail()]))
    stats = entry(row)
    assert (stats["unwanted"], stats["postings"], stats["skipped_by_rule"]) == (2, 1, 1)
    [job] = jobs_of(factory, alice)
    assert job.status is JobStatus.skipped
    assert job.title == "Ingénieur données confirmé"


def test_contract_types_drop_another_type(factory: sessionmaker[Session], alice: uuid.UUID) -> None:
    set_profile(factory, alice, alerts_on(contract_types=["Stage"]))
    connect(factory, alice)
    row = sweep_with(factory, alice, a.FakeMailbox([indeed_mail()]))
    assert entry(row)["other_contract"] == 3
    assert jobs_of(factory, alice) == []


def test_an_html_only_alert_is_read_through_the_parser_service_s_text(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, alerts_on())
    connect(factory, alice)
    html_only = a.gmail_json(a.eml("indeed-html-only.eml"), internal_ms=FIRST_MS)
    version = (
        "Ingénieur MLOps (https://fr.indeed.com/rc/clk/dl?jk=abcdefabcdef0001&from=ja)\n"
        "Exemple Robotique\tParis (75)\nIndustrialiser les modèles."
    )
    row = sweep_with(factory, alice, a.FakeMailbox([html_only]), html={"version": version})
    assert [job.url for job in jobs_of(factory, alice)] == [
        "https://fr.indeed.com/viewjob?jk=abcdefabcdef0001"
    ]
    assert entry(row)["empty_messages"] == 0

    # When the parser service fails, the mail gives nothing and says so.
    set_profile(factory, alice, alerts_on())
    later = a.gmail_json(a.eml("indeed-html-only.eml"), internal_ms=FIRST_MS + 1)
    row = sweep_with(factory, alice, a.FakeMailbox([later]), html={"version": None})
    assert (entry(row)["messages"], entry(row)["empty_messages"]) == (1, 1)


def test_at_most_max_messages_a_sweep_oldest_first(
    factory: sessionmaker[Session], alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source, "MAX_MESSAGES", 1)
    set_profile(factory, alice, alerts_on())
    connect(factory, alice)
    old = indeed_mail(message_id="m-old")
    new = a.gmail_json(a.eml("indeed-latin1.eml"), internal_ms=FIRST_MS + 5, message_id="m-new")
    mailbox = a.FakeMailbox([new, old], page=1)  # two pages of one
    row = sweep_with(factory, alice, mailbox)
    stats = entry(row)
    assert [c for c in mailbox.calls if c[0] == "get"] == [("get", "m-old")]
    assert (stats["messages_left"], stats["cursor"]["after_ms"]) == (1, FIRST_MS)
    row = sweep_with(factory, alice, a.FakeMailbox([new, old]))
    assert entry(row)["postings"] == 1  # the newer one, the next sweep


def test_one_person_s_sweep_never_reads_another_s_mailbox(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    connect(factory, alice)
    set_profile(factory, bob, alerts_on())
    mailbox = a.FakeMailbox([indeed_mail()])
    row = sweep_with(factory, bob, mailbox)
    assert entry(row)["reason"] == "mail_not_connected"
    assert mailbox.calls == []
    assert jobs_of(factory, alice) == []
    assert jobs_of(factory, bob) == []


def test_the_scope_freezes_the_providers_turned_on(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, search(interactive_sites=[], alert_emails={"linkedin": True}))
    row = read_sweep(factory, new_sweep(factory, alice))
    assert [s["host"] for s in row.stats["search_scope"]["sources"]] == ["mail:linkedin"]
    assert row.status is SweepStatus.running
    # Nothing on, no website: nothing to search.
    set_profile(factory, alice, search(interactive_sites=[], alert_emails={"indeed": False}))
    with factory() as db:
        assert not sweep.has_sources(sweep.user_search(db, alice))


def test_the_setting_is_kept_when_left_out_and_dropped_when_all_off(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    def save(data: dict[str, Any]) -> dict[str, Any]:
        with factory() as db:
            current = profile_service.get_profile(db, alice, NOW)
            saved = profile_service.save_section(
                db, alice, "search", validate_section("search", data), current.revision, NOW
            )
            db.commit()
            return dict(saved.sections["search"])

    base = search()
    assert save({**base, "alert_emails": {"indeed": True}})["alert_emails"] == {
        "indeed": True,
        "linkedin": False,
    }
    # An editor that does not know the field keeps it.
    assert save(base)["alert_emails"]["indeed"] is True
    # Every switch off: stored as absent, read as off.
    assert "alert_emails" not in save({**base, "alert_emails": {"indeed": False}})
    with pytest.raises(ValueError, match="extra"):
        validate_section("search", {**base, "alert_emails": {"monster": True}})
    with pytest.raises(ValueError, match="bool"):
        validate_section("search", {**base, "alert_emails": {"indeed": "yes"}})


def test_the_worker_hands_the_alerts_hook_to_the_sweep(settings_no_mail: Settings) -> None:
    hook = object()
    assert worker.alert_sources({"alert_sources": hook}) is hook
    built = source.hook_for(settings_no_mail, sessionmaker(), None)
    assert isinstance(built, source.Reader)
    assert built.mailbox is None


@pytest.fixture
def settings_no_mail(migrated_url: str) -> Settings:
    return make_settings(migrated_url, google_oauth_client_id=None)


def test_a_sweep_with_alerts_only_starts_and_ends(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    set_profile(factory, alice, alerts_on())
    connect(factory, alice)
    with factory() as db:
        row = sweep.start(db, alice, SweepTrigger.manual, NOW)
        db.commit()
        sweep_id = row.id
    asyncio.run(
        sweep.run_sweep(
            factory,
            sweep_id,
            lambda: NOW,
            alerts=reader(factory, a.FakeMailbox([indeed_mail()])),
        )
    )
    assert read_sweep(factory, sweep_id).status is SweepStatus.done
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(Job)) == 3
