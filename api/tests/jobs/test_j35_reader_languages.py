"""Jobs J35 (#858) through the database: the posting reader reads the languages a posting asks for
(each with a quote found in the text, which names it and holds its level, else dropped), the badge
gives `language` to a required language the profile does not list and follows a change of the
profile's languages and a new reading, the notices quote both sides, the backlog reads again the
open postings read before, bounded per tick and per day, and the list and the detail serve them."""

import itertools
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import failures, runs
from app.agents.crypto import Keyring
from app.agents.models import AgentRun, RunStatus
from app.agents.runner import RunContext
from app.jobs import criteria_badge, languages, notices, reader, sweep
from app.jobs.criteria_badge import LANGUAGE
from app.jobs.models import Job, JobStatus
from app.profile import service as profile_service
from app.profile.schemas import validate_section
from tests.agents.run_helpers import execute, save_credential
from tests.identity.api_helpers import Api
from tests.jobs import test_reader as reading
from tests.jobs.conftest import new_user
from tests.jobs.sweep_helpers import finding, jobs_of, new_sweep, run, search, set_profile
from tests.jobs.test_api import sign_in
from tests.jobs.test_j30_criteria_badge import saved_search
from tests.jobs.test_reader import run_context

__all__ = ["run_context"]  # the fixture of test_reader, used here by name

NOW = reading.NOW
TEXT = (
    "Data Engineer (m/w/d)\nExample Robotics GmbH, Berlin\nIhre Aufgaben\nSie bauen Datenpipelines "
    "für unsere Roboterflotte und betreuen die Auswertung der Produktionsdaten gemeinsam mit den "
    "Teams vor Ort.\nIhr Profil\nSehr gute Python und SQL Kenntnisse. Verhandlungssichere "
    "Deutschkenntnisse. Fließendes Englisch in Wort und Schrift. Französischkenntnisse sind von "
    "Vorteil. Gute Italienisch- oder Spanischkenntnisse.\n"
)
GERMAN = {
    "name": "German",
    "level": "Verhandlungssichere",
    "required": True,
    "quote": "Verhandlungssichere Deutschkenntnisse",
}
ENGLISH = {
    "name": "English",
    "level": "Fließendes",
    "required": True,
    "quote": "Fließendes Englisch in Wort und Schrift",
}
FRENCH = {"name": "French", "required": False, "quote": "Französischkenntnisse sind von Vorteil"}
ITALIAN = {
    "name": "Italian",
    "level": "Gute",
    "required": True,
    "alternatives": ["Spanish"],
    "quote": "Gute Italienisch- oder Spanischkenntnisse",
}
WRITTEN = {"value": "German", "quote": "Ihre Aufgaben"}


def posting(factory: sessionmaker[Session], user: uuid.UUID, text: str | None = TEXT) -> uuid.UUID:
    one = uuid.uuid4().hex[:8]
    url = f"https://jobs.example-robotics.test/jobs/{one}"
    return reading.add_posting(factory, user, text=text, url=url, title=f"Data engineer {one}")


def read(factory: sessionmaker[Session], user: uuid.UUID, job_id: uuid.UUID, **facts: Any):
    binding = reading.bound(factory, user, job_id)
    return reader.save(factory, user, binding, {"index": 0, **facts}, NOW)


def set_languages(factory: sessionmaker[Session], user: uuid.UUID, *spoken: tuple[str, Any]):
    with factory() as db:
        revision = profile_service.get_profile(db, user, NOW).revision
        data = validate_section(
            "identity", {"languages": [{"name": name, "level": level} for name, level in spoken]}
        )
        profile_service.save_section(db, user, "identity", data, revision, NOW)
        db.commit()


def person(factory: sessionmaker[Session], user: uuid.UUID, *spoken: tuple[str, Any]) -> None:
    set_profile(factory, user, search())
    set_languages(factory, user, *spoken)


ME = (("English", "C1"), ("Français", "Langue maternelle"), ("Italien", None))


# --- the reader's contract ----------------------------------------------------------------------


def test_the_reader_stores_each_language_with_its_quote_its_level_and_the_ads_language(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-languages-read@example.org")
    job_id = posting(factory, user)
    done = read(
        factory, user, job_id, languages=[GERMAN, ENGLISH, FRENCH, ITALIAN], written_in=WRITTEN
    )
    assert (done.read, done.error, done.refused) == (True, False, 0)
    job = reading.job_of(factory, job_id)
    assert job.languages == {
        "written_in": "German",
        "asked": [
            {**GERMAN, "alternatives": []},
            {**ENGLISH, "alternatives": []},
            {**FRENCH, "level": None, "alternatives": []},
            ITALIAN,
        ],
    }
    [event] = reading.reader_events(factory, job_id)
    assert event["changes"]["languages"]["from"] is None  # type: ignore[index]
    assert "languages" in done.text
    assert "Verhandlungssichere" not in done.text  # names, never values


def test_a_posting_that_asks_for_no_language_is_read_as_none(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-languages-none@example.org")
    job_id = posting(factory, user)
    before = reading.job_of(factory, job_id).updated_at
    assert read(factory, user, job_id).read
    job = reading.job_of(factory, job_id)
    assert job.languages == {"written_in": None, "asked": []}
    # Nothing to tell: History holds no event, and the posting did not change for the person.
    assert reading.reader_events(factory, job_id) == []
    assert job.updated_at == before


@pytest.mark.parametrize(
    ("item", "why"),
    [
        ({**GERMAN, "quote": "Deutsch auf Muttersprachniveau"}, "quote is not in the posting text"),
        ({**GERMAN, "name": "Russian"}, "quote is not in the posting text"),
        ({**GERMAN, "level": "C2"}, "quote is not in the posting text"),
        ({**ITALIAN, "alternatives": ["Portuguese"]}, "quote is not in the posting text"),
        ({**GERMAN, "required": "yes"}, "not a valid value"),
        ({"name": "German", "quote": "Verhandlungssichere Deutschkenntnisse"}, "not a valid value"),
        ({**GERMAN, "name": "German "}, "not a valid value"),
        ({**GERMAN, "level": "<b>"}, "not a valid value"),
    ],
)
def test_a_language_whose_quote_does_not_hold_it_is_dropped_and_counted(
    factory: sessionmaker[Session], item: dict[str, Any], why: str
) -> None:
    user = new_user(factory, f"e2e-languages-drop-{uuid.uuid4().hex[:8]}@example.org")
    job_id = posting(factory, user)
    done = read(factory, user, job_id, languages=[item, ENGLISH])
    assert (done.read, done.refused) == (True, 1)
    assert why in done.text
    assert [one["name"] for one in reading.job_of(factory, job_id).languages["asked"]] == [
        "English"
    ]


def test_past_eight_languages_the_first_eight_are_kept_and_the_rest_counted(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-languages-many@example.org")
    job_id = posting(factory, user)
    location = {"value": "Berlin", "quote": "Example Robotics GmbH, Berlin"}
    done = read(factory, user, job_id, languages=[ENGLISH] * 8 + [GERMAN] * 3, location=location)
    assert (done.read, done.error, done.refused) == (True, False, 3)
    assert "Dropped, past the first 8 languages: languages, languages, languages." in done.text
    job = reading.job_of(factory, job_id)
    assert [one["name"] for one in job.languages["asked"]] == ["English"] * 8
    assert job.location == "Berlin"


def test_the_ads_language_needs_its_quote(factory: sessionmaker[Session]) -> None:
    user = new_user(factory, "e2e-languages-written@example.org")
    job_id = posting(factory, user)
    done = read(factory, user, job_id, written_in={"value": "German", "quote": "Wir suchen"})
    assert done.refused == 1
    assert reading.job_of(factory, job_id).languages == {"written_in": None, "asked": []}


# --- the badge and the notices ------------------------------------------------------------------


def badge(factory: sessionmaker[Session], job_id: uuid.UUID) -> list[str] | None:
    return reading.job_of(factory, job_id).outside_criteria


def notes(factory: sessionmaker[Session], user: uuid.UUID, job_id: uuid.UUID) -> list[Any]:
    with factory() as db:
        job = db.get(Job, job_id)
        assert job is not None
        return [n for n in notices.notices_of(db, user, [job], date(2026, 11, 27))[job_id]]


def test_the_badge_follows_the_reading_and_the_profiles_languages(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-languages-badge@example.org")
    person(factory, user, *ME)
    job_id = posting(factory, user)
    shortlisted = posting(factory, user, text=TEXT + "Team in Wien.\n")
    with factory() as db, db.begin():
        db.execute(sa.update(Job).where(Job.id == shortlisted).values(status=JobStatus.shortlisted))
    read(factory, user, job_id, languages=[GERMAN, ENGLISH, FRENCH, ITALIAN])
    read(factory, user, shortlisted, languages=[GERMAN])
    assert badge(factory, job_id) == badge(factory, shortlisted) == [LANGUAGE]
    said = {n.code: n for n in notes(factory, user, job_id)}
    assert said["language_missing"].text == (
        "This posting requires German, which your profile does not list."
    )
    assert said["language_missing"].quote == "Verhandlungssichere Deutschkenntnisse"
    assert said["language_level"].text == (
        "This posting asks for Italian or Spanish at “Gute”; your profile lists Italien "
        "without a level."
    )
    assert "language_written_in" not in said
    # She adds German: every open posting follows, the shortlisted one included.
    set_languages(factory, user, *ME, ("Deutsch", "Muttersprache"))
    assert badge(factory, job_id) == badge(factory, shortlisted) == []
    assert "language_missing" not in {n.code for n in notes(factory, user, job_id)}
    # Her English drops to B2: a notice with both levels, never the badge.
    set_languages(
        factory, user, ("English", "B2"), ("Français", None), ("Deutsch", "C2"), ("Italien", None)
    )
    [english] = [n for n in notes(factory, user, job_id) if "English" in n.text]
    assert badge(factory, job_id) == []
    assert (english.code, english.level, english.quote) == (
        "language_level",
        "warning",
        "Fließendes Englisch in Wort und Schrift",
    )
    assert english.text == (
        "This posting asks for English at “Fließendes”; your profile lists English at “B2”."
    )
    # A new reading that finds German only welcome takes the badge away.
    set_languages(factory, user, *ME)
    assert badge(factory, job_id) == [LANGUAGE]
    read(factory, user, job_id, languages=[FRENCH])
    assert badge(factory, job_id) == []


def test_a_saved_searchs_postings_follow_and_a_posting_no_longer_open_keeps_its_badge(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-languages-saved@example.org")
    person(factory, user, *ME)
    saved_id = saved_search(factory, user, contract_types=["CDI"])
    mine, applied = posting(factory, user), posting(factory, user, text=TEXT + "Wien.\n")
    for job_id in (mine, applied):
        read(factory, user, job_id, languages=[GERMAN])
    with factory() as db, db.begin():
        db.execute(sa.update(Job).where(Job.id == mine).values(criteria_search_id=saved_id))
        db.execute(sa.update(Job).where(Job.id == applied).values(status=JobStatus.applied))
    set_languages(factory, user, *ME, ("Allemand", "bilingue"))
    assert (badge(factory, mine), badge(factory, applied)) == ([], [LANGUAGE])


def test_no_language_declared_gives_nothing_and_a_posting_without_text_has_none(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-languages-empty@example.org")
    set_profile(factory, user, search())
    job_id = posting(factory, user)
    read(factory, user, job_id, languages=[GERMAN], written_in=WRITTEN)
    assert badge(factory, job_id) == []
    assert not [n for n in notes(factory, user, job_id) if n.code.startswith("language")]
    blank = posting(factory, user, text="   ")
    set_languages(factory, user, *ME)
    assert reading.job_of(factory, blank).languages is None
    assert badge(factory, blank) in (None, [])
    assert badge(factory, job_id) == [LANGUAGE]


def test_the_ads_language_alone_is_a_notice(factory: sessionmaker[Session]) -> None:
    user = new_user(factory, "e2e-languages-ad@example.org")
    person(factory, user, *ME)
    job_id = posting(factory, user)
    read(factory, user, job_id, languages=[FRENCH], written_in=WRITTEN)
    said = {n.code: n for n in notes(factory, user, job_id)}
    assert badge(factory, job_id) == []
    assert said["language_written_in"].text == (
        "This posting is written in German, which your profile does not list; it asks for no "
        "language."
    )
    assert said["language_written_in"].level == "info"


def test_a_posting_found_again_keeps_its_language_code(factory: sessionmaker[Session]) -> None:
    user = new_user(factory, "e2e-languages-again@example.org")
    person(factory, user, *ME)
    found = finding("Applied AI engineer", published_on=(date.today()).isoformat())
    run(factory, new_sweep(factory, user), found)
    [job] = jobs_of(factory, user)
    with factory() as db, db.begin():
        stored = {"written_in": None, "asked": [{**GERMAN, "alternatives": []}]}
        db.execute(sa.update(Job).where(Job.id == job.id).values(languages=stored))
    run(factory, new_sweep(factory, user), found)
    [same] = jobs_of(factory, user)
    assert (same.id, same.outside_criteria) == (job.id, [LANGUAGE])
    plan = sweep.Plan(uuid.uuid4(), user, [], None, None)  # type: ignore[arg-type]
    assert plan.criteria.spoken is None
    assert criteria_badge.codes(criteria_badge.facts_of(same), plan.criteria) == []


# --- the backlog --------------------------------------------------------------------------------


def stale(factory: sessionmaker[Session], user: uuid.UUID, count: int, **values: Any) -> None:
    """Postings the reader read before jobs J35: read, no languages."""
    rows = [
        {
            "user_id": user,
            "slug": f"stale-{uuid.uuid4().hex[:12]}",
            "company": "Example Robotics",
            "title": f"Data engineer {index}",
            "posting_text": TEXT,
            "read_at": NOW - timedelta(days=3),
            "read_attempts": 1,
            "status": JobStatus.found,
            "found_on": date(2026, 11, 1),
            "created_at": NOW - timedelta(days=3, seconds=index),
            **values,
        }
        for index in range(count)
    ]
    with factory() as db, db.begin():
        db.execute(sa.insert(Job), rows)


def finish(factory: sessionmaker[Session], run_id: uuid.UUID, at: datetime) -> None:
    """The run ends; its postings read, as a reading would leave them."""
    with factory() as db, db.begin():
        db.execute(
            sa.update(Job)
            .where(Job.read_run_id == run_id)
            .values(read_at=at, languages={"written_in": None, "asked": []})
        )
        assert runs.fail_queued(db, run_id, failures.NO_RESULT, at)


def held(factory: sessionmaker[Session], run_id: uuid.UUID) -> list[Job]:
    with factory() as db:
        return list(db.scalars(sa.select(Job).where(Job.read_run_id == run_id)))


def test_the_backlog_reads_the_open_postings_read_before_again(
    factory: sessionmaker[Session],
    run_context: tuple[RunContext, Keyring],
    caplog: pytest.LogCaptureFixture,
) -> None:
    rc, keyring = run_context
    user = new_user(factory, "e2e-languages-backlog@example.org")
    save_credential(factory, keyring, user)
    stale(factory, user, 12)
    stale(factory, user, 1, status=JobStatus.shortlisted, created_at=NOW)
    stale(factory, user, 1, status=JobStatus.applied, created_at=NOW)
    stale(factory, user, 1, posting_text="  ", created_at=NOW)
    stale(factory, user, 1, languages={"written_in": None, "asked": []}, created_at=NOW)
    unread = reading.add_posting(factory, user)  # never read: first, as before
    [first] = reading.backlog(factory)
    assert [job.id for job in held(factory, first)] == [unread]
    finish(factory, first, NOW)
    spaced = NOW + reader.REREAD_EVERY
    assert reading.backlog(factory, spaced - timedelta(seconds=1)) == []
    with caplog.at_level(logging.INFO, logger="app.jobs.reader"):
        [again] = reading.backlog(factory, spaced)
    taken = held(factory, again)
    assert len(taken) == reader.BATCH_MAX
    assert {job.status for job in taken} == {JobStatus.found, JobStatus.shortlisted}
    assert all(job.read_at == NOW - timedelta(days=3) and job.read_attempts == 2 for job in taken)
    [record] = [r for r in caplog.records if r.getMessage().startswith("open postings read")]
    assert (record.postings, record.left) == (10, 3)  # type: ignore[attr-defined]
    assert "Deutsch" not in str(record.__dict__)
    with factory() as db:
        run_row = db.get(AgentRun, again)
        assert run_row is not None
        assert run_row.input["languages_only"] is True
        first_id = uuid.UUID(run_row.input["job_ids"][0])
    assert execute(rc, again) is RunStatus.done  # the fake saves its posting 0
    job = reading.job_of(factory, first_id)
    assert job.read_at == NOW - timedelta(days=3)
    assert job.languages == {"written_in": None, "asked": []}
    with factory() as db:
        untouched = db.scalars(
            sa.select(Job.id).where(
                Job.user_id == user,
                Job.read_at == NOW - timedelta(days=3),
                Job.read_attempts == 1,
                Job.read_run_id.is_(None),
            )
        ).all()
    assert len(untouched) == 3 + 3  # three left for later, applied, without text, read already


def test_a_reread_that_cannot_start_leaves_its_postings_read(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-languages-nokey@example.org")
    stale(factory, user, 2)
    statements: list[str] = []

    def seen(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        statements.append(statement)

    engine = factory.kw["bind"]
    sa.event.listen(engine, "before_cursor_execute", seen)
    try:
        assert reading.backlog(factory) == []
    finally:
        sa.event.remove(engine, "before_cursor_execute", seen)
    # No credential: skipped before any work, no posting locked nor changed.
    assert not [
        one
        for one in statements
        if one.lstrip().upper().startswith("UPDATE JOBS") or "FOR UPDATE" in one.upper()
    ]
    with factory() as db:
        reads = db.scalars(sa.select(Job.read_at).where(Job.user_id == user)).all()
    assert reads == [NOW - timedelta(days=3)] * 2


def test_a_reread_that_fails_for_the_provider_stays_a_reading_of_the_languages(
    factory: sessionmaker[Session], run_context: tuple[RunContext, Keyring]
) -> None:
    _, keyring = run_context
    user = new_user(factory, "e2e-languages-provider@example.org")
    save_credential(factory, keyring, user)
    stale(factory, user, 2)
    [first] = reading.backlog(factory)
    reading.end_run(factory, first, failures.BUSY)
    with factory() as db:
        reader.settle(db, first)
        db.commit()
    with factory() as db:
        rows = db.execute(
            sa.select(Job.read_at, Job.read_attempts, Job.languages).where(Job.user_id == user)
        ).all()
    assert [tuple(row) for row in rows] == [(NOW - timedelta(days=3), 1, None)] * 2
    later = NOW + reader.BACKLOG_PAUSE + reader.REREAD_EVERY
    [again] = reading.backlog(factory, later)
    with factory() as db:
        run_row = db.get(AgentRun, again)
        assert run_row is not None
        assert run_row.input["languages_only"] is True


def test_six_hundred_open_postings_are_read_again_in_ten_hours_and_sixty_runs_a_day(
    factory: sessionmaker[Session], run_context: tuple[RunContext, Keyring]
) -> None:
    _, keyring = run_context
    user = new_user(factory, "e2e-languages-600@example.org")
    save_credential(factory, keyring, user)
    stale(factory, user, 600)
    started: list[datetime] = []
    for minute in range(0, 24 * 60, 5):
        at = NOW + timedelta(minutes=minute)
        if minute == 12 * 60:
            stale(factory, user, 15)  # more after the day's runs: they wait for the next day
        for run_id in reading.backlog(factory, at):
            started.append(at)
            finish(factory, run_id, at)
    assert len(started) == reader.REREAD_RUNS_PER_DAY == 60
    assert started[-1] - started[0] < timedelta(hours=10)
    assert all(b - a >= reader.REREAD_EVERY for a, b in itertools.pairwise(started))
    assert reading.backlog(factory, NOW + timedelta(hours=24)) != []


def only_languages(factory: sessionmaker[Session], user: uuid.UUID, job_id: uuid.UUID):
    """The binding of a reading made only for the languages, as the backlog's second reading."""
    with factory() as db:
        run = AgentRun(user_id=user, input={"job_ids": [str(job_id)], "languages_only": True})
        binding = reader.bind_postings(db, run)
    assert binding is not None
    return binding


def test_a_reading_for_the_languages_changes_nothing_else(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-languages-only@example.org")
    person(factory, user, *ME)
    job_id, empty = posting(factory, user), posting(factory, user, text=TEXT + "Wien.\n")
    earlier = NOW - timedelta(days=2)
    with factory() as db, db.begin():
        db.execute(
            sa.update(Job)
            .where(Job.id.in_([job_id, empty]))
            .values(fit=77, updated_at=earlier, read_at=earlier, outside_criteria=[])
        )
    everything = {
        "location": {"value": "Berlin", "quote": "Example Robotics GmbH, Berlin"},
        "company": {"value": "Example Robotics GmbH", "quote": "Example Robotics GmbH, Berlin"},
        "description": {
            "start": "Example Robotics GmbH, Berlin",
            "end": "Gute Italienisch- oder Spanischkenntnisse",
        },
    }
    binding = only_languages(factory, user, job_id)
    assert '"read_only": ["languages", "written_in"]' in reader.postings(
        factory, user, binding, lambda value: value
    )
    done = reader.save(
        factory, user, binding, {"index": 0, **everything, "languages": [GERMAN]}, NOW
    )
    assert (done.read, done.error) == (True, False)
    job = reading.job_of(factory, job_id)
    assert (job.posting_text, job.location, job.company) == (TEXT, "", "Employer not stated")
    assert (job.fit, job.updated_at, job.read_at) == (77, earlier, earlier)
    assert job.languages["asked"][0]["quote"] == GERMAN["quote"]
    assert job.outside_criteria == [LANGUAGE]
    [event] = reading.reader_events(factory, job_id)
    assert set(event["changes"]) == {"languages"}  # type: ignore[arg-type]
    # None found: stored, and nothing else at all.
    binding = only_languages(factory, user, empty)
    assert reader.save(factory, user, binding, {"index": 0, **everything}, NOW).read
    job = reading.job_of(factory, empty)
    assert (job.languages, job.updated_at, job.fit) == (languages.EMPTY, earlier, 77)
    assert reading.reader_events(factory, empty) == []
    # The same facts in a full reading change the posting, as before.
    full = reader.save(
        factory, user, reading.bound(factory, user, empty), {"index": 0, **everything}, NOW
    )
    assert "description" in full.text
    assert reading.job_of(factory, empty).posting_text != TEXT


def test_the_badge_follows_her_languages_not_every_identity_save(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-languages-identity@example.org")
    person(factory, user, *ME)
    job_id = posting(factory, user)
    read(factory, user, job_id, languages=[GERMAN])
    assert badge(factory, job_id) == [LANGUAGE]
    with factory() as db, db.begin():
        db.execute(sa.update(Job).where(Job.id == job_id).values(outside_criteria=["older"]))
    # A name and a phone change no badge: the open postings are not computed again.
    with factory() as db:
        revision = profile_service.get_profile(db, user, NOW).revision
        data = validate_section(
            "identity",
            {
                "full_name": "E2e Languages",
                "phone": "+33 6 00 00 00 00",
                "languages": [{"name": name, "level": level} for name, level in ME],
            },
        )
        profile_service.save_section(db, user, "identity", data, revision, NOW)
        db.commit()
    assert badge(factory, job_id) == ["older"]
    # Her languages change: every open posting follows.
    set_languages(factory, user, *ME, ("Allemand", "C1"))
    assert badge(factory, job_id) == []


# --- the list and the detail --------------------------------------------------------------------


def test_the_profile_names_the_languages_the_app_cannot_read(
    api: Api, factory: sessionmaker[Session]
) -> None:
    signed = sign_in(factory, api, "e2e-languages-unread@example.org")
    assert api.get("/api/profile", token=signed.token).json()["languages_unread"] == []
    person(
        factory, signed.id, ("Français", "natif"), ("Klingon", "C1"), ("N/A", None), ("UK", None)
    )
    body = api.get("/api/profile", token=signed.token).json()
    assert body["languages_unread"] == ["Klingon", "UK"]
    saved = api.request(
        "PUT",
        "/api/profile/identity",
        token=signed.token,
        json={
            "base_revision": body["revision"],
            "data": {"languages": [{"name": "Français"}, {"name": "Latin"}, {"name": "none"}]},
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["languages_unread"] == []
    assert api.get("/api/profile", token=signed.token).json()["languages_unread"] == []


def test_the_list_and_the_detail_serve_the_languages_and_the_badges_words(
    api: Api, factory: sessionmaker[Session]
) -> None:
    signed = sign_in(factory, api, "e2e-languages-api@example.org")
    person(factory, signed.id, *ME)
    job_id = posting(factory, signed.id)
    never = posting(factory, signed.id, text=TEXT + "Wien.\n")
    read(factory, signed.id, job_id, languages=[GERMAN, FRENCH], written_in=WRITTEN)
    items = {item["id"]: item for item in api.get("/api/jobs", token=signed.token).json()["items"]}
    mine = items[str(job_id)]
    assert (mine["outside_criteria"], mine["languages_missing"]) == ([LANGUAGE], ["German"])
    assert mine["languages"]["written_in"] == "German"
    assert [one["name"] for one in mine["languages"]["asked"]] == ["German", "French"]
    assert (items[str(never)]["languages"], items[str(never)]["languages_missing"]) == (None, [])
    outside = api.get("/api/jobs", token=signed.token, params={"criteria": "outside"}).json()
    assert [item["id"] for item in outside["items"]] == [str(job_id)]
    detail = api.get(f"/api/jobs/{job_id}", token=signed.token).json()
    assert detail["languages_missing"] == ["German"]
    codes = [notice["code"] for notice in detail["notices"]]
    assert "language_missing" in codes
