"""Jobs J39 (issue #877, items 2, 3 and 6): every text of a posting that comes from outside is
stored as a reader sees it, from every source, and what the app derives from it reads the stored
text: the fit and its competencies, the signals' quotes, the duplicate check and the never apply
rules. A feed, a company board, an alert e-mail, a website search's report, a listed page the app
read itself, the person's paste (a NUL answers 200), a posting added by hand, an edit, an import,
the reader's cut, and a statement no store wrote (the column's own type). Invented postings and
`e2e-*@example.org` accounts only."""

import html
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import feeds, scoring, service, signals, sweep
from app.jobs.boards.run import BoardFinding
from app.jobs.models import Job, JobEvent, JobEventKind, JobStatus
from app.jobs.schemas import ImportJob
from app.jobs.visible_text import visible
from app.mail.alerts.source import AlertFinding
from tests.identity.api_helpers import Api
from tests.jobs import test_reader
from tests.jobs.conftest import new_user
from tests.jobs.sweep_helpers import (
    SKILLS,
    finding,
    jobs_of,
    new_sweep,
    now,
    read_sweep,
    search,
    set_profile,
)
from tests.jobs.test_api import Signed, seed, sign_in
from tests.jobs.test_job_feeds import Api as FakeApi
from tests.jobs.test_job_feeds import stamp
from tests.jobs.test_job_feeds_sweep import feeds_search, run_feeds
from tests.jobs.test_scoring import skills

CLEAN = (
    "AI engineer at Example Robotics, Paris. You build agents with Python and FastAPI for the "
    "planning of our warehouse robots, with retrieval over the maintenance notes and tool use "
    "against the fleet's own interfaces. You ship every week with a team of six, write the tests "
    "and the evaluation sets, and talk to the operators who use what you build.\n"
    "The closing date is 15 January 2027. \u2764\ufe0f Remote days are welcome."
)
# What a page can hide in it: a byte order mark, zero-width characters, a soft hyphen, a word
# joiner, a private use bullet, a run of selectors (an emoji's own one stays), tag characters, a
# NUL and a lone surrogate.
DIRTY = (
    "\ufeff"
    + CLEAN.replace("Python", "Py\u200bthon")
    .replace("FastAPI", "Fast\xadAPI")
    .replace("closing date", "clos\u2060ing date")
    .replace("January", "Janu\u200dary")
    .replace("agents", "ag\ue000ents")
    .replace("\u2764\ufe0f", "\u2764\u200b\ufe0f")
    .replace("welcome.", "welcome.\ufe00\ufe0f\ufe0e")
    + "\U000e0041\U000e0042\x00\ud800"
)
# Pydantic refuses a lone surrogate in a request or an import row before any store reads it.
TYPED = DIRTY.replace("\ud800", "")
TITLE, DIRTY_TITLE = "AI engineer", "AI engi\u200bneer\u202e"
COMPANY, DIRTY_COMPANY = "Example Robotics", "Example\xad Ro\u200cbotics"
PLACE, DIRTY_PLACE = "Paris, France", "Pa\u200dris, France\ufeff"
SKILLED = skills()


def test_the_texts_hide_something_from_the_derived_values() -> None:
    """The fixture can fail the tests below: read as written, it scores and quotes otherwise."""
    assert visible(DIRTY) == CLEAN
    assert visible(DIRTY_TITLE) == TITLE
    assert visible(DIRTY_COMPANY) == COMPANY
    assert visible(DIRTY_PLACE) == PLACE
    clean = scoring.score(CLEAN, SKILLED, title=TITLE)
    dirty = scoring.score(DIRTY, SKILLED, title=DIRTY_TITLE)
    assert "python" in clean.competencies
    assert "python" not in dirty.competencies
    assert clean.fit != dirty.fit
    assert signals.stored(CLEAN)["signals"] != signals.stored(DIRTY)["signals"]


def assert_seen(job: Job, title: str = TITLE) -> None:
    """Stored as a reader sees it, and derived from what is stored."""
    assert (job.title, job.company, job.location) == (title, COMPANY, PLACE)
    assert job.posting_text == CLEAN
    assert job.description == CLEAN[: service.DESCRIPTION_CHARS]
    expected = scoring.score(CLEAN, SKILLED, title=title)
    assert (job.fit, job.competencies) == (expected.fit, list(expected.competencies))
    assert job.signals == signals.stored(CLEAN)["signals"]


def raw(factory: sessionmaker[Session], job_id: uuid.UUID) -> dict[str, Any]:
    """The row as Postgres holds it, read without the model."""
    with factory() as db:
        row = db.execute(
            sa.text(
                "select title, company, location, description, posting_text from jobs "
                "where id = :id"
            ),
            {"id": job_id},
        ).one()
        return dict(row._mapping)


# --- the column's own type: no store can skip it ---------------------------------------------


def test_a_write_no_store_made_is_stored_as_a_reader_sees_it(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-j39-columns@example.org")
    job = Job(user_id=user, slug="example-robotics-ai", company=DIRTY_COMPANY, title=DIRTY_TITLE)
    job.posting_text = DIRTY
    assert (job.company, job.title, job.posting_text) == (COMPANY, TITLE, CLEAN)  # on the object
    statement_id = uuid.uuid4()
    with factory() as db, db.begin():
        db.add(job)
        db.execute(
            pg_insert(Job).values(
                id=statement_id,
                user_id=user,
                slug="example-robotics-ai-2",
                company=DIRTY_COMPANY,
                title=DIRTY_TITLE,
                location=DIRTY_PLACE,
                description=DIRTY[:300],
                posting_text=DIRTY,
            )
        )
    row = raw(factory, statement_id)
    assert row == {
        "title": TITLE,
        "company": COMPANY,
        "location": PLACE,
        "description": visible(DIRTY[:300]),
        "posting_text": CLEAN,
    }
    with factory() as db, db.begin():
        db.execute(sa.update(Job).where(Job.id == job.id).values(location=DIRTY_PLACE))
    assert raw(factory, job.id)["location"] == PLACE


# --- every source of a search ------------------------------------------------------------------

ARBEITNOW_URL = "https://www.arbeitnow.com/jobs/companies/example-robotics/ai-engineer-paris-1"


def _feed(text: str, title: str, url: str) -> Any:
    return feeds.Finding(
        source="arbeitnow",
        external_id="ai-engineer-paris-1",
        title=title,
        company=DIRTY_COMPANY,
        location=DIRTY_PLACE,
        url=url,
        text=text,
    )


def _board(text: str, title: str, url: str) -> Any:
    return BoardFinding(
        title=title,
        location=DIRTY_PLACE,
        url=url,
        text=text,
        company=DIRTY_COMPANY,
        published_on=None,
        detail_read_at=now().isoformat(),
    )


def _alert(text: str, title: str, url: str) -> Any:
    return AlertFinding(
        title=title,
        location=DIRTY_PLACE,
        url=url,
        text=text,
        company=DIRTY_COMPANY,
        published_on=None,
        evidence={"alert": "example"},
        detail_read_at=now().isoformat(),
    )


WEBSITE_URL = "https://jobs.example-robotics.test/jobs/ai-engineer"


def _website(text: str, title: str, url: str = WEBSITE_URL) -> Any:
    return finding(title, company=DIRTY_COMPANY, location=DIRTY_PLACE, url=url, text=text)


SOURCES = [
    pytest.param(_feed, "arbeitnow", ARBEITNOW_URL, id="a feed"),
    pytest.param(
        _board, "jobs.ashbyhq.test", "https://jobs.ashbyhq.test/example/1", id="a company board"
    ),
    pytest.param(
        _alert, "alerts.example.test", "https://alerts.example.test/jobs/1", id="an alert e-mail"
    ),
    pytest.param(_website, "jobs.example-robotics.test", WEBSITE_URL, id="a website's report"),
]


def _claimed(factory: sessionmaker[Session], user: uuid.UUID) -> sweep.Plan:
    plan = sweep.claim(factory, new_sweep(factory, user), now())
    assert plan is not None
    return plan


def _store(factory: sessionmaker[Session], plan: sweep.Plan, source: str, *found: Any) -> None:
    sweep.commit_verified(factory, plan, plan.known, sweep.Stats(), "s", source, found, now())
    with factory() as db, db.begin():  # the sweep ends: the next one reads the stored rows
        db.execute(
            sa.update(sweep.Sweep)
            .where(sweep.Sweep.id == plan.sweep_id)
            .values(status="done", finished_at=now())
        )


@pytest.mark.parametrize(("make", "source", "url"), SOURCES)
def test_every_source_of_a_search_stores_what_a_reader_sees_and_derives_from_it(
    factory: sessionmaker[Session], make: Callable[[str, str, str], Any], source: str, url: str
) -> None:
    user = new_user(factory, f"e2e-j39-{uuid.uuid4().hex[:6]}@example.org")
    set_profile(factory, user, search())
    _store(factory, _claimed(factory, user), source, make(DIRTY, DIRTY_TITLE, url))
    [job] = jobs_of(factory, user)
    assert_seen(job)
    assert job.slug.startswith("example-robotics-ai-engineer")
    # Found again at another address with other hidden characters: the same posting.
    elsewhere = make(DIRTY + "\u200b", "AI\u2060 engineer", f"{url}-again")
    _store(factory, _claimed(factory, user), source, elsewhere)
    assert len(jobs_of(factory, user)) == 1


def test_a_never_apply_rule_reads_the_title_as_a_reader_sees_it(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-j39-rule@example.org")
    set_profile(factory, user, search())
    _store(
        factory,
        _claimed(factory, user),
        "jobs.example-robotics.test",
        _website(DIRTY, "Secu\u200brity AI engineer"),
    )
    [job] = jobs_of(factory, user)
    assert job.title == "Security AI engineer"
    assert job.status is JobStatus.skipped


def test_a_listed_page_the_app_read_itself_stores_what_a_reader_sees(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-j39-listed@example.org")
    set_profile(factory, user, search())
    url = WEBSITE_URL
    card = f"{TITLE} {COMPANY} Paris"
    [job] = seed(
        factory,
        user,
        [
            {
                "title": TITLE,
                "company": COMPANY,
                "location": PLACE,
                "url": url,
                "list_url": "https://jobs.example-robotics.test/search?q=ai",
                "posting_text": card,
                "description": card,
            }
        ],
    )
    plan = _claimed(factory, user)
    page = sweep.ListedPage(job.id, url, "read", finding=_website(DIRTY, DIRTY_TITLE))
    found = sweep.commit_verified(
        factory, plan, plan.known, sweep.Stats(), "s", "jobs.example-robotics.test", (), now(),
        listed=page,
    )  # fmt: skip
    assert found is not None
    assert page.done == "read"
    [stored] = jobs_of(factory, user)
    assert_seen(stored)


# --- what the person types ---------------------------------------------------------------------


@pytest.fixture
def person(factory: sessionmaker[Session], api: Api) -> Signed:
    signed = sign_in(factory, api, "e2e-j39-person@example.org")
    set_profile(factory, signed.id, search())
    return signed


def _stored(factory: sessionmaker[Session], job_id: Any) -> Job:
    with factory() as db:
        job = db.get(Job, uuid.UUID(str(job_id)))
        assert job is not None
        _ = job.posting_text
        db.expunge(job)
        return job


def test_a_pasted_text_with_a_nul_is_stored_as_a_reader_sees_it(
    factory: sessionmaker[Session], api: Api, person: Signed
) -> None:
    card = f"{TITLE} {COMPANY} Paris"
    [job] = seed(
        factory,
        person.id,
        [
            {
                "title": TITLE,
                "company": COMPANY,
                "location": PLACE,
                "list_url": "https://aggregator.example.test/emplois?q=ai",
                "posting_text": card,
                "description": card,
            }
        ],
    )
    response = api.request(
        "PUT", f"/api/jobs/{job.id}/text", token=person.token, json={"text": TYPED}
    )
    assert response.status_code == 200, response.text
    assert_seen(_stored(factory, job.id))


def test_a_paste_made_only_of_hidden_characters_is_too_short(
    factory: sessionmaker[Session], api: Api, person: Signed
) -> None:
    [job] = seed(
        factory,
        person.id,
        [{"list_url": "https://aggregator.example.test/emplois?q=ai", "posting_text": "card"}],
    )
    response = api.request(
        "PUT", f"/api/jobs/{job.id}/text", token=person.token, json={"text": "\u200b" * 400}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "text_too_short"


def test_a_posting_added_by_hand_is_stored_as_a_reader_sees_it(
    factory: sessionmaker[Session], api: Api, person: Signed
) -> None:
    body = {
        "company": DIRTY_COMPANY,
        "title": DIRTY_TITLE,
        "location": DIRTY_PLACE,
        "url": "https://jobs.example-robotics.test/jobs/ai-engineer",
        "posting_text": TYPED,
    }
    response = api.post("/api/jobs", token=person.token, json=body)
    assert response.status_code == 201, response.text
    assert response.json()["title"] == TITLE  # the answer reads the stored values
    job = _stored(factory, response.json()["id"])
    assert (job.title, job.company, job.location, job.posting_text) == (
        TITLE,
        COMPANY,
        PLACE,
        CLEAN,
    )
    expected = scoring.score(CLEAN, SKILLED, title=TITLE)
    assert (job.fit, job.competencies) == (expected.fit, list(expected.competencies))
    twin = {**body, "url": None, "title": "AI\u2060 engineer"}
    assert api.post("/api/jobs", token=person.token, json=twin).status_code == 409
    hidden = {**body, "url": None, "title": "\u200b\u200d"}
    assert api.post("/api/jobs", token=person.token, json=hidden).status_code == 422


def test_an_edit_is_stored_and_recorded_as_a_reader_sees_it(
    factory: sessionmaker[Session], api: Api, person: Signed
) -> None:
    [job] = seed(factory, person.id, [{"title": "Data engineer", "company": COMPANY}])
    response = api.request(
        "PATCH", f"/api/jobs/{job.id}", token=person.token, json={"title": DIRTY_TITLE}
    )
    assert response.status_code == 200, response.text
    assert _stored(factory, job.id).title == TITLE
    with factory() as db:
        [data] = db.scalars(sa.select(JobEvent.data).where(JobEvent.job_id == job.id))
    assert data["changes"]["title"] == {"from": "Data engineer", "to": TITLE}
    hidden = {"title": "\u200b"}
    empty = api.request("PATCH", f"/api/jobs/{job.id}", token=person.token, json=hidden)
    assert empty.status_code == 422


def test_an_import_stores_what_a_reader_sees_and_a_second_changes_nothing(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-j39-import@example.org")
    set_profile(factory, user, None)
    item = ImportJob(
        slug="example-robotics-ai-engineer",
        company=DIRTY_COMPANY,
        title=DIRTY_TITLE,
        location=DIRTY_PLACE,
        description=TYPED[:300],
        posting_text=TYPED,
    )
    at = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)
    with factory() as db, db.begin():
        assert service.import_jobs(db, user, [item], at).created == 1
    [job] = jobs_of(factory, user)
    assert (job.title, job.company, job.location, job.posting_text) == (
        TITLE,
        COMPANY,
        PLACE,
        CLEAN,
    )
    assert job.signals == signals.stored(CLEAN)["signals"]
    with factory() as db, db.begin():
        again = service.import_jobs(db, user, [item], at)
    assert (again.unchanged, again.updated) == (1, 0)


def test_the_readers_cut_of_an_old_text_is_stored_as_a_reader_sees_it(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-j39-reader@example.org")
    set_profile(factory, user, None, skills=SKILLS)
    job_id = test_reader.add_posting(factory, user)
    old = test_reader.POSTING.replace("Python", "Py\u200bthon").replace("SQL", "S\u2060QL")
    with factory() as db, db.begin():  # a text stored before J39: no store wrote it
        db.execute(
            sa.text("update jobs set posting_text = :text where id = :id"),
            {"text": old, "id": job_id},
        )
    saved = test_reader.save(factory, user, job_id)
    assert saved.read, saved.text
    job = test_reader.job_of(factory, job_id)
    assert "Python et SQL" in (job.posting_text or "")
    assert job.posting_text == visible(job.posting_text or "")
    assert job.signals == signals.stored(job.posting_text)["signals"]
    expected = scoring.score(job.posting_text or "", SKILLED, title=job.title)
    assert job.competencies == list(expected.competencies)


# --- the two wirings every caller passes by (each test red without its own line) ---------------


def test_create_job_reads_a_raw_new_posting_as_it_is_stored(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-j39-newjob@example.org")
    at = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)
    raw = service.NewJob(
        company=DIRTY_COMPANY,
        title=DIRTY_TITLE,
        location=DIRTY_PLACE,
        description=TYPED[:300],
        posting_text=TYPED,
    )
    with factory() as db, db.begin():
        job = service.create_job(db, user, raw, at)
        assert job.signals == signals.stored(CLEAN)["signals"]  # derived before the column
        assert job.slug.startswith("example-robotics-ai-engineer")
        twin = service.NewJob(company=COMPANY, title="AI⁠ engineer", location=PLACE)
        with pytest.raises(service.DuplicateJobError):
            service.create_job(db, user, twin, at)


def test_page_text_reads_the_text_it_is_given_as_it_is_stored(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-j39-pagetext@example.org")
    [seeded] = seed(
        factory,
        user,
        [{"title": TITLE, "company": COMPANY, "location": PLACE, "list_url": WEBSITE_URL}],
    )
    with factory() as db, db.begin():
        job = db.get(Job, seeded.id)
        assert job is not None
        service.page_text(
            db, job, TYPED, (JobEventKind.edit, {"text_pasted": {}}), now(),
            rule=None, excluded="", skills=SKILLED, search=None, via="pasted",
        )  # fmt: skip
        db.flush()
    assert_seen(_stored(factory, seeded.id))


# --- a text of hidden characters alone is nothing, on every store path -------------------------

HIDDEN = "​﻿⁠"


def test_a_text_of_hidden_characters_alone_is_stored_as_nothing_on_every_path(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, "e2e-j39-nothing@example.org")
    set_profile(factory, user, search())
    at = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)
    ids: dict[str, uuid.UUID] = {}
    job = Job(user_id=user, slug="example-robotics-ai-0", company=COMPANY, title=TITLE)
    job.posting_text, job.description = HIDDEN, HIDDEN
    assert (job.posting_text, job.description) == (None, None)  # on the object
    with factory() as db, db.begin():
        db.add(job)
        db.flush()
        ids["the model"] = job.id
        ids["a statement"] = uuid.uuid4()
        db.execute(
            pg_insert(Job).values(
                id=ids["a statement"], user_id=user, slug="example-robotics-ai-1",
                company=COMPANY, title=TITLE, description=HIDDEN, posting_text=HIDDEN,
            )
        )  # fmt: skip
        new = service.NewJob(
            company=COMPANY, title="Data engineer", description=HIDDEN, posting_text=HIDDEN
        )
        assert (new.description, new.posting_text) == (None, None)
        ids["create_job"] = service.create_job(db, user, new, at).id
        imported = ImportJob(
            slug="example-robotics-ml-engineer",
            company=COMPANY,
            title="ML engineer",
            description=HIDDEN,
            posting_text=HIDDEN,
        )
        assert service.import_jobs(db, user, [imported], at).created == 1
    _store(
        factory,
        _claimed(factory, user),
        "arbeitnow",
        _feed(HIDDEN, "Robotics engineer", ARBEITNOW_URL),
    )
    for job in jobs_of(factory, user):
        ids.setdefault(job.title, job.id)
    assert {"ML engineer", "Robotics engineer"} <= ids.keys()
    for path, job_id in ids.items():
        row = raw(factory, job_id)
        assert (row["description"], row["posting_text"]) == (None, None), path
    with factory() as db, db.begin():  # an import of the same row again changes nothing
        assert service.import_jobs(db, user, [imported], at).unchanged == 1


def test_a_posting_added_by_hand_with_a_text_of_hidden_characters_has_none(
    factory: sessionmaker[Session], api: Api, person: Signed
) -> None:
    body = {"company": COMPANY, "title": TITLE, "location": PLACE, "posting_text": HIDDEN}
    response = api.post("/api/jobs", token=person.token, json=body)
    assert response.status_code == 201, response.text
    assert raw(factory, uuid.UUID(response.json()["id"]))["posting_text"] is None


# --- a one line value stays on one line -----------------------------------------------------------

LINE_BREAKS = [
    pytest.param("\x0b", id="vertical tab"),
    pytest.param("\x0c", id="form feed"),
    pytest.param("\x1c", id="file separator"),
    pytest.param("\x1e", id="record separator"),
    pytest.param("\x85", id="next line"),
]


@pytest.mark.parametrize("brk", LINE_BREAKS)
def test_a_line_break_a_reader_sees_is_a_space_in_a_title_a_company_and_a_place(
    factory: sessionmaker[Session], brk: str
) -> None:
    user = new_user(factory, f"e2e-j39-{uuid.uuid4().hex[:6]}@example.org")
    set_profile(factory, user, search())
    company, place, text = f"Example{brk}Robotics", f"Paris,{brk}France", f"{CLEAN}{brk}Apply now."
    found = feeds.Finding(
        source="arbeitnow", external_id="ai-engineer-paris-1", title=f"AI{brk}engineer",
        company=company, location=place, url=ARBEITNOW_URL, text=text,
    )  # fmt: skip
    _store(factory, _claimed(factory, user), "arbeitnow", found)
    [job] = jobs_of(factory, user)
    statement_id = uuid.uuid4()
    with factory() as db, db.begin():
        db.execute(
            pg_insert(Job).values(
                id=statement_id, user_id=user, slug="example-robotics-data", company=company,
                title=f"Data{brk}engineer", location=place, posting_text=text,
            )
        )  # fmt: skip
        new = service.NewJob(
            company=company, title=f"ML{brk}engineer", location=place, posting_text=text
        )
    assert (new.title, new.company, new.location) == ("ML engineer", COMPANY, PLACE)
    assert new.posting_text == f"{CLEAN}\nApply now."
    for job_id, title in ((job.id, TITLE), (statement_id, "Data engineer")):
        row = raw(factory, job_id)
        assert (row["title"], row["company"], row["location"]) == (title, COMPANY, PLACE)
        assert row["posting_text"] == f"{CLEAN}\nApply now."  # the text keeps its line feed


# --- one description never stops a fetch ----------------------------------------------------------

DIGITS = "9" * 5_000  # past the 4,300 digits int() reads
TOO_LONG = [
    pytest.param(f"&#{DIGITS};", id="5,000 decimal digits"),
    pytest.param(f"&#x{'F' * 5_000};", id="5,000 hexadecimal digits"),
    pytest.param("&#00099999999;", id="8 significant decimal digits, zero padded"),
    pytest.param("&#x00001F60000;", id="7 significant hexadecimal digits, zero padded"),
]
ZEROS = [
    pytest.param(f"&#{'0' * 5_000};", id="5,000 decimal zeros"),
    pytest.param(f"&#x{'0' * 5_000}1F600;", id="5,000 hexadecimal zeros then a code point"),
]


@pytest.mark.parametrize("reference", TOO_LONG)
def test_a_numeric_reference_too_long_to_be_a_code_point_stays_as_written(reference: str) -> None:
    for value in (
        f"Pay {reference} a year",
        f"<p>Pay {reference} a year</p>",
        f'<p><a href="https://example.org/{reference}">Pay</a> {reference} a year</p>',
    ):
        assert feeds.plain_text(value) == f"Pay {reference} a year"
    assert feeds.line(f"<b>Pay</b> {reference}", 20) == f"Pay {reference}"[:20]


@pytest.mark.parametrize("reference", ZEROS)
def test_a_numeric_reference_of_a_thousand_zeros_stays_as_written(reference: str) -> None:
    assert feeds.plain_text(f"<p>Pay {reference} a year</p>") == f"Pay {reference} a year"


def test_an_ordinary_numeric_reference_still_reads() -> None:
    written = "<p>Caf&#233; &#x1F600; &#0000233; &#x01F600; &eacute; &#9999999;</p>"
    assert feeds.plain_text(written) == "Caf\u00e9 \U0001f600 \u00e9 \U0001f600 \u00e9 \ufffd"


@pytest.mark.parametrize(
    ("written", "read"),
    [
        ("&#00000233;", "\u00e9"),
        ("&#x0001F600;", "\U0001f600"),
        ("&#00000000;", "\ufffd"),
        ("&#x0000000;", "\ufffd"),
        (f"&#{'0' * 900}233;", "\u00e9"),
    ],
    ids=["8 decimal digits", "7 hexadecimal digits", "decimal zeros only", "hex zeros only", "900"],
)
def test_a_zero_padded_numeric_reference_is_read_as_a_browser_reads_it(
    written: str, read: str
) -> None:
    assert feeds.plain_text(f"<p>Pay {written} a year</p>") == f"Pay {read} a year"


REFERENCE = f"&#{DIGITS};"
LONG_HTML = "".join(
    f"<p>Paragraph {n}: Example Robotics plans its routes.</p>" for n in range(5_000)
)
# Each kind of description with such a reference, then an ordinary posting.
DESCRIPTIONS = {
    "HTML": f"<p>AI engineer in Paris.</p><p>Pay {REFERENCE} a year.</p>",
    "plain text": f"AI engineer in Paris. Pay {REFERENCE} a year.",
    "escaped twice": f"&lt;p&gt;AI engineer in Paris.&lt;/p&gt;&lt;p&gt;Pay {REFERENCE} a year.",
    "past 200,000 characters": html.escape(LONG_HTML) + f"&lt;p&gt;Pay {REFERENCE} a year.",
    "ordinary": "<p>AI engineer in Paris.</p><p>Pay 70k a year.</p>",
}


def test_a_description_with_such_a_reference_is_stored_and_the_fetch_goes_on(
    factory: sessionmaker[Session],
) -> None:
    user = new_user(factory, f"e2e-j39-{uuid.uuid4().hex[:6]}@example.org")
    set_profile(factory, user, feeds_search(job_feeds=["arbeitnow"]))
    assert len(LONG_HTML) > 4 * feeds.TEXT_MAX  # the reference is past what the conversion reads
    items = [
        {
            "slug": f"ai-engineer-paris-{n}",
            "company_name": f"Example Robotics {n}",
            "title": "AI Engineer",
            "description": description,
            "url": f"https://www.arbeitnow.com/jobs/companies/example-robotics-{n}/ai-engineer-{n}",
            "location": "Paris, France",
            "created_at": stamp(hours=n + 1),
        }
        for n, description in enumerate(DESCRIPTIONS.values())
    ]
    api = FakeApi({("www.arbeitnow.com", "1"): {"data": items, "links": {"next": None}}})
    sweep_id = new_sweep(factory, user)
    run_feeds(factory, sweep_id, api, None)
    row = read_sweep(factory, sweep_id)
    [entry] = row.stats["sources"]
    assert (entry["outcome"], entry["error"], entry["found"]) == ("complete", None, 5)
    assert entry["cursor"] == stamp(hours=1)
    texts = {job.company: job.posting_text or "" for job in jobs_of(factory, user)}
    assert len(texts) == 5
    for n in range(3):
        assert texts[f"Example Robotics {n}"].endswith(f"Pay {REFERENCE} a year.")
    assert texts["Example Robotics 3"].startswith("Paragraph 0: Example Robotics plans its routes.")
    assert texts["Example Robotics 4"] == "AI engineer in Paris.\nPay 70k a year."
