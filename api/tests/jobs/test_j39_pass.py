"""Jobs J39 (issue #877, items 4, 5b and 6): the pass over the postings stored before J39
(app/jobs/visible_pass.py). Their texts become what a store writes now, with the signals and the
fit computed again from them (a fit set by hand kept), and nothing else moves: no History event,
`updated_at`, the reader's `read_at` and attempts, the status. A row another transaction holds, a
row a reader's or a rating's run is bound to, and a person's row while her search runs are left
and come back; the walk survives a restart and two workers, and once it ended it reads nothing.
The Arbeitnow postings stored with their HTML left in are converted again, a posting of another
source that writes `<li>` in its prose stays as it is. Invented postings only."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import rescore, scoring, signals, visible_pass
from app.jobs.feeds import arbeitnow
from app.jobs.models import Job, JobStatus, Sweep, SweepStatus, SweepTrigger
from tests.jobs.conftest import new_user
from tests.jobs.sweep_helpers import jobs_of, search, set_profile
from tests.jobs.test_api import seed
from tests.jobs.test_scoring import skills

T0 = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
CLEAN = (
    "AI engineer at Example Robotics, Paris. You build agents with Python and FastAPI for the "
    "planning of our warehouse robots, with retrieval over the maintenance notes and tool use "
    "against the fleet's own interfaces. You ship every week with a team of six, write the tests "
    "and the evaluation sets, and talk to the operators who use what you build.\n"
    "The closing date is 15 January 2027. \u2764\ufe0f Remote days are welcome."
)
# A text stored before J39: Postgres never held a NUL or a lone surrogate, the rest it did.
OLD = (
    "\ufeff"
    + CLEAN.replace("Python", "Py\u200bthon")
    .replace("FastAPI", "Fast\xadAPI")
    .replace("closing date", "clos\u2060ing date")
    .replace("\u2764\ufe0f", "\u2764\u200b\ufe0f")
    .replace("welcome.", "welcome.\ufe00\ufe0f\ufe0e")
    + "\ue000\U000e0041"
)
SKILLED = skills()


@pytest.fixture
def person(factory: sessionmaker[Session]) -> uuid.UUID:
    user = new_user(factory, f"e2e-j39-pass-{uuid.uuid4().hex[:6]}@example.org")
    set_profile(factory, user, search())
    return user


def old(factory: sessionmaker[Session], job_id: uuid.UUID, **texts: str) -> None:
    """Write texts as a store before J39 did: a statement the model's column type never sees."""
    names = ", ".join(f"{name} = :{name}" for name in texts)
    with factory() as db, db.begin():
        db.execute(sa.text(f"update jobs set {names} where id = :id"), {**texts, "id": job_id})


def add(
    factory: sessionmaker[Session], user: uuid.UUID, text: str = OLD, **fields: Any
) -> uuid.UUID:
    """A posting stored before J39 with `text`, read by the reader twice, shortlisted, fit 40."""
    token = uuid.uuid4().hex[:8]
    row = {
        "slug": f"example-robotics-{token}",
        "company": "Example Robotics",
        "title": "AI engineer",
        "url": f"https://jobs.example-robotics.test/{token}",
        "status": JobStatus.shortlisted,
        "fit": 40,
        "read_at": T0,
        "read_attempts": 2,
        "updated_at": T0,
    }
    row.update(fields)
    [job] = seed(factory, user, [row])
    old(factory, job.id, posting_text=text, description=text[:300])
    return job.id


def row_of(factory: sessionmaker[Session], job_id: uuid.UUID) -> dict[str, Any]:
    """The row as Postgres holds it, its version (`xmin`) and its events' count."""
    with factory() as db:
        found = db.execute(
            sa.text(
                "select xmin::text as version, title, company, location, description, "
                "posting_text, fit, fit_overridden, competencies, signals, closes_on, status, "
                "updated_at, read_at, read_attempts, source, "
                "(select count(*) from job_events e where e.job_id = jobs.id) as events "
                "from jobs where id = :id"
            ),
            {"id": job_id},
        ).one()
        return dict(found._mapping)


def walk(factory: sessionmaker[Session], state: visible_pass.State | None = None) -> int:
    """Steps of the pass until its walk ended (a fresh worker's when no state is given)."""
    state = visible_pass.State() if state is None else state
    written = 0
    for _ in range(20):
        written += visible_pass.step(factory, 60.0, state=state)
        if state.done:
            return written
    raise AssertionError("the walk never ended")


def test_the_pass_rewrites_old_texts_and_what_derives_from_them_and_nothing_else(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    dirty = add(factory, person)
    clean = add(factory, person, CLEAN)
    manual = add(factory, person, fit=42, fit_overridden=True)
    before = {job_id: row_of(factory, job_id) for job_id in (dirty, clean, manual)}
    assert walk(factory) == 2
    after = row_of(factory, dirty)
    expected = scoring.score(CLEAN, SKILLED, title="AI engineer")
    assert after["posting_text"] == CLEAN
    assert after["description"] == visible_pass.visible(OLD[:300])
    assert (after["fit"], after["competencies"]) == (expected.fit, list(expected.competencies))
    assert after["signals"] == signals.stored(CLEAN)["signals"]
    for name in ("updated_at", "read_at", "read_attempts", "status", "events", "fit_overridden"):
        assert after[name] == before[dirty][name], name
    assert row_of(factory, clean) == before[clean]  # not written at all: its version is the same
    kept = row_of(factory, manual)
    assert (kept["fit"], kept["fit_overridden"]) == (42, True)
    assert kept["competencies"] == list(expected.competencies)
    assert kept["updated_at"] == T0


def test_a_text_that_changed_is_rated_again_by_its_digest(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    from app.jobs import reader

    job_id = add(factory, person)
    rated = {"career": 70, "culture": 60, "text": reader._digest(OLD)}
    with factory() as db, db.begin():
        db.execute(sa.update(Job).where(Job.id == job_id).values(match=rated))
    walk(factory)
    with factory() as db:
        job = db.get(Job, job_id)
        assert job is not None
        assert job.match == rated  # kept; its digest no longer the text's, so it is rated again
        assert reader._digest(job.posting_text or "") != rated["text"]


def test_the_walk_survives_a_restart_and_a_second_worker(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    ids = sorted(add(factory, person) for _ in range(5))
    first = visible_pass.State()
    ticks: Iterator[float] = iter([0.0, 0.0, 100.0])
    # One window of two postings, then the slice is spent and the worker stops.
    written = visible_pass.step(
        factory, 1.0, state=first, scan_rows=2, monotonic=lambda: next(ticks)
    )
    assert written == 2
    assert not first.done
    # A second worker walks at the same time: what the first wrote is not written again.
    assert walk(factory, visible_pass.State()) == 3
    # The first worker restarted: a fresh walk, nothing left to write.
    assert walk(factory) == 0
    for job_id in ids:
        assert row_of(factory, job_id)["posting_text"] == CLEAN


def test_a_held_posting_is_left_and_comes_back(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    locked, bound, rating, free = (add(factory, person) for _ in range(4))
    with factory() as db, db.begin():
        db.execute(sa.update(Job).where(Job.id == bound).values(read_run_id=uuid.uuid4()))
        db.execute(sa.update(Job).where(Job.id == rating).values(match={"run": str(uuid.uuid4())}))
    state = visible_pass.State()
    with factory() as holder:
        holder.execute(sa.select(Job.id).where(Job.id == locked).with_for_update())
        assert visible_pass.step(factory, 60.0, state=state) == 1  # never waits for the lock
        assert state.held == {locked, bound, rating}
        assert not state.done
        holder.rollback()
    with factory() as db, db.begin():
        db.execute(
            sa.update(Job).where(Job.id.in_([bound, rating])).values(read_run_id=None, match=None)
        )
    assert walk(factory, state) == 3
    for job_id in (locked, bound, rating, free):
        assert row_of(factory, job_id)["posting_text"] == CLEAN


def test_a_persons_postings_wait_while_her_search_runs(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    job_id = add(factory, person)
    with factory() as db, db.begin():
        db.add(Sweep(user_id=person, trigger=SweepTrigger.manual, status=SweepStatus.running))
    state = visible_pass.State()
    assert visible_pass.step(factory, 60.0, state=state) == 0
    assert state.held == {job_id}
    with factory() as db, db.begin():
        db.execute(
            sa.update(Sweep)
            .where(Sweep.user_id == person)
            .values(status=SweepStatus.done, finished_at=T0)
        )
    assert walk(factory, state) == 1


def test_once_the_walk_ended_the_worker_reads_nothing(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    add(factory, person)
    state = visible_pass.State()
    walk(factory, state)
    opened: list[int] = []

    def counted() -> Session:
        opened.append(1)
        return factory()

    sessions: Any = counted
    later = add(factory, person)
    assert visible_pass.step(sessions, 60.0, state=state) == 0
    assert opened == []
    assert row_of(factory, later)["posting_text"] == OLD  # stays for the next restart's walk


def test_the_rescore_tick_gives_the_pass_its_slice(
    factory: sessionmaker[Session], person: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = add(factory, person)
    monkeypatch.setattr(visible_pass, "_STATE", visible_pass.State())
    rescore.tick(factory, lambda: T0)
    assert row_of(factory, job_id)["posting_text"] == CLEAN


def test_a_posting_whose_rewrite_fails_fails_alone(
    factory: sessionmaker[Session], person: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    poisoned, good = sorted(add(factory, person) for _ in range(2))
    derived = visible_pass._derived

    def failing(job: Job, texts: dict[str, Any], who: Any) -> dict[str, Any]:
        if job.id == poisoned:
            raise RuntimeError("a store that fails")
        return derived(job, texts, who)

    monkeypatch.setattr(visible_pass, "_derived", failing)
    assert walk(factory) == 1  # the walk goes on and ends
    assert row_of(factory, good)["posting_text"] == CLEAN
    assert row_of(factory, poisoned)["posting_text"] == OLD  # left to the next walk, unchanged
    monkeypatch.setattr(visible_pass, "_derived", derived)
    assert walk(factory) == 1


# --- Arbeitnow's HTML left in -------------------------------------------------------------------

ESCAPED = (
    "&lt;p&gt;AI engineer at Example Robotics.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;"
    "&lt;li&gt;LLM&amp;nbsp;agents&lt;/li&gt;&lt;/ul&gt;&lt;p&gt;Remote&amp;nbsp;days.&lt;/p&gt;"
)
CONVERTED = "AI engineer at Example Robotics.\nPython\nLLM agents\nRemote days."
PROSE = (
    "Frontend engineer at Example Labs. You know when a <li> belongs in a <ul> and when a "
    "&nbsp; keeps two words together.\n</p> closes a paragraph."
)


AGAIN = [
    pytest.param("<p>Python</p>", "Python", id="a paragraph"),
    pytest.param("<P>Python</P>", "Python", id="upper case"),
    pytest.param("Python<br/>SQL", "Python\nSQL", id="a line break"),
    pytest.param('<li class="x">Python</li>', "Python", id="an attribute"),
    pytest.param("<ul><li>Python</li></ul>", "Python", id="a list"),
    pytest.param("<h2>Role</h2>x", "Role\nx", id="a heading"),
    pytest.param("<tr>a</tr>", "a", id="a table row"),
    pytest.param("List<String> and Map<K, V>", None, id="generics"),
    pytest.param("a <b>bold</b> <em>word</em>", None, id="inline tags only"),
    pytest.param("x < 5 and y > 3", None, id="comparisons"),
    pytest.param("<pre>code</pre> <param> <track>", None, id="other tags starting alike"),
    pytest.param("&nbsp; alone", None, id="an entity with no tag"),
    pytest.param("<li", None, id="an open tag never closed"),
    pytest.param("", None, id="empty"),
]


@pytest.mark.parametrize(("text", "converted"), AGAIN)
def test_only_a_text_still_holding_a_block_tag_is_converted_again(
    text: str, converted: str | None
) -> None:
    assert arbeitnow.again(text) == (text if converted is None else converted)


def test_an_answer_escaped_twice_is_converted_as_every_feeds_is() -> None:
    item = {
        "slug": "ai-engineer-example-robotics-1",
        "title": "AI engineer",
        "company_name": "Example Robotics",
        "location": "Berlin",
        "url": "https://www.arbeitnow.com/jobs/companies/example-robotics/ai-engineer-1",
        "description": ESCAPED,
        "created_at": 1_764_000_000,
    }
    found = arbeitnow.finding_of(item, T0)
    assert found is not None
    assert found.text == CONVERTED
    plain = arbeitnow.finding_of(
        {
            **item,
            "description": ESCAPED.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"),
        },
        T0,
    )
    assert plain is not None
    assert plain.text == CONVERTED
    assert arbeitnow.again(CONVERTED) == CONVERTED


def test_the_pass_converts_arbeitnows_stored_html_and_no_other_sources_prose(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    stored = arbeitnow.plain_text(ESCAPED)  # what the feed stored before J39
    assert "<li>" in stored
    feed = add(factory, person, stored, source="arbeitnow, www.linkedin.com")
    feed_ok = add(factory, person, CONVERTED, source="arbeitnow")
    other = add(factory, person, PROSE, source="www.welcometothejungle.com")
    first_elsewhere = add(factory, person, stored, source="www.linkedin.com, arbeitnow")
    before = {job_id: row_of(factory, job_id) for job_id in (feed_ok, other, first_elsewhere)}
    assert walk(factory) == 1
    repaired = row_of(factory, feed)
    assert repaired["posting_text"] == CONVERTED
    assert repaired["description"] == CONVERTED
    assert repaired["signals"] == signals.stored(CONVERTED)["signals"]
    assert repaired["updated_at"] == T0
    for job_id, row in before.items():
        assert row_of(factory, job_id) == row
    assert walk(factory) == 0
    assert jobs_of(factory, person)
