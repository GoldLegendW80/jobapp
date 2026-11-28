"""Jobs J39 (issue #877, items 4, 5b and 6): the pass over the postings stored before J39
(app/jobs/visible_pass.py). Their texts become what a store writes now, with the signals and the
fit computed again from them (a fit set by hand kept), and nothing else moves: no History event,
`updated_at`, the reader's `read_at` and attempts, the status. A row another transaction holds, a
row a reader's or a rating's run is bound to, and a person's row while her search runs are left
and come back; the walk survives a restart and two workers, and once it ended it reads nothing.
The feed decides an answer escaped twice on the answer itself and converts it once; the pass
repairs the Arbeitnow postings stored with their HTML left in only when they are that failed shape
and the conversion keeps every word, and leaves every other text as stored, counted. Invented
postings only."""

import html
import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import rescore, scoring, service, signals, visible_pass
from app.jobs.feeds import TEXT_MAX, arbeitnow, plain_text
from app.jobs.models import Job, JobStatus, Sweep, SweepStatus, SweepTrigger
from app.jobs.visible_text import visible
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
    assert after["description"] == visible(OLD[:300])
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

STYLE = (
    "font-family: Arial, Helvetica, sans-serif; font-size: 14px; line-height: 1.6; "
    "color: #1f2933; background-color: transparent; font-weight: 400; font-style: normal; "
    "text-decoration: none; vertical-align: baseline; white-space: pre-wrap"
)


def answer(paragraphs: int, items: int, *, spans: bool = False, newlines: bool = False) -> str:
    """An invented description's HTML as an employer writes it (escaped once): paragraphs, then a
    list, each text wrapped in a styled span or not, the blocks on one line or one each."""
    gap = "\n" if newlines else ""

    def wrap(text: str) -> str:
        return f'<span style="{STYLE}">{text}</span>' if spans else text

    said = (
        f"Paragraph {n}: Example Robotics plans the routes of its warehouse robots, part {n}."
        for n in range(paragraphs)
    )
    blocks = [f"<p>{wrap(words)}</p>" for words in said]
    duties = (f"<li>{wrap(f'Duty {n} with&nbsp;Python &amp; SQL')}</li>" for n in range(items))
    blocks.append("<ul>" + gap.join(duties) + "</ul>")
    return gap.join(blocks)


def stored_before(html_once: str) -> str:
    """What the feed stored before J39 for the same answer escaped twice: one conversion."""
    return plain_text(html.escape(html_once))


# Invented twins of the three texts the measure found (about 90, 120 and 600 tags; the third's
# markup passed the store's 50,000 characters, so its stored text ends inside a span's tag).
TWINS = [
    pytest.param(answer(20, 24), id="90 tags on one line"),
    pytest.param(answer(25, 34, newlines=True), id="120 tags, a block a line"),
    pytest.param(answer(150, 5, spans=True), id="600 tags with styled spans, cut by the store"),
]
MEASURED = (
    "<p>Lists use &lt;li&gt; items.</p><p>Avoid inline &lt;script&gt; tags for safety.</p>"
    "<p>Benefits: 30 days off, remote, a salary of 70k.</p><p>Apply now.</p>"
)
MEASURED_TEXT = (
    "Lists use <li> items.\nAvoid inline <script> tags for safety.\n"
    "Benefits: 30 days off, remote, a salary of 70k.\nApply now."
)
# Escaped once, its prose naming tags and entities: converted once, every word kept.
PROSE_ANSWERS = [
    pytest.param(MEASURED, MEASURED_TEXT, id="the review's measured example"),
    pytest.param(
        "<p>Java: List&lt;String&gt;, Map&lt;K, V&gt; and a &lt;p&gt; in templates.</p>",
        "Java: List<String>, Map<K, V> and a <p> in templates.",
        id="generics beside a block tag",
    ),
    pytest.param(
        "<p>Read the terms&amp;notice first.</p><ul><li>A &lt;div&gt; per card</li></ul>",
        "Read the terms&notice first.\nA <div> per card",
        id="an entity of a word beside a block tag",
    ),
    pytest.param(
        "<p>Never &lt;style&gt; inline, keep &lt;!-- notes --&gt; out of &lt;br&gt; pages.</p>",
        "Never <style> inline, keep <!-- notes --> out of <br> pages.",
        id="a style tag and a comment named in prose",
    ),
]


@pytest.mark.parametrize(("description", "text"), PROSE_ANSWERS)
def test_an_answer_escaped_once_is_converted_once_whatever_its_prose_names(
    description: str, text: str
) -> None:
    assert not arbeitnow.escaped_twice(description)
    assert arbeitnow.text_of(description) == text


ESCAPED_TWICE = [
    pytest.param("&lt;p&gt;Python&lt;/p&gt;", True, id="a paragraph"),
    pytest.param("&lt;P&gt;Python&lt;/P&gt;", True, id="upper case"),
    pytest.param("Python&lt;br/&gt;SQL", True, id="a line break"),
    pytest.param("Python&lt;br /&gt;SQL", True, id="a line break with a space"),
    pytest.param("&lt;li class=&quot;x&quot;&gt;Python&lt;/li&gt;", True, id="an attribute"),
    pytest.param("&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;", True, id="a list"),
    pytest.param("&lt;h2&gt;Role&lt;/h2&gt;x", True, id="a heading"),
    pytest.param("&lt;tr&gt;a&lt;/tr&gt;", True, id="a table row"),
    pytest.param("&lt;div\n&gt;x", True, id="a line break inside the tag"),
    pytest.param("Remote&lt;/p&gt;", True, id="an end tag alone"),
    pytest.param("<p>Python</p>", False, id="a real tag"),
    pytest.param("<p>Lists use &lt;li&gt; items</p>", False, id="a real tag beside an escaped one"),
    pytest.param("&lt;p&gt;Python&lt;/p&gt;<br>", False, id="one real tag anywhere"),
    pytest.param("a<b and &lt;p&gt;", False, id="a real tag in prose"),
    pytest.param("&lt;span&gt;x&lt;/span&gt;", False, id="inline tags only"),
    pytest.param("&lt;pre&gt;code&lt;/pre&gt;", False, id="a tag starting like p"),
    pytest.param("&lt;param&gt; &lt;track&gt;", False, id="other tags starting alike"),
    pytest.param("List&lt;String&gt;", False, id="generics"),
    pytest.param("x &lt; 5 and y &gt; 3", False, id="comparisons"),
    pytest.param("&amp;lt;p&amp;gt;", False, id="escaped three times"),
    pytest.param("&lt;p", False, id="an escaped tag never closed"),
    pytest.param("Python &lt;3", False, id="a heart"),
    pytest.param("&lt;li-item&gt;", False, id="a custom element"),
    pytest.param("&nbsp; alone", False, id="an entity alone"),
    pytest.param("", False, id="empty"),
]


@pytest.mark.parametrize(("description", "twice"), ESCAPED_TWICE)
def test_the_feed_decides_the_double_escape_on_the_answer_itself(
    description: str, twice: bool
) -> None:
    assert arbeitnow.escaped_twice(description) is twice


# A plain text description (no real tag) whose prose writes an escaped block tag, then a tag the
# conversion would swallow with the words after it: read as escaped twice it would lose words.
PLAIN_PROSE = [
    pytest.param("&lt;script&gt;track('apply')&lt;/script&gt;", id="a script tag"),
    pytest.param("&lt;style&gt;p { color: red; }&lt;/style&gt;", id="a style tag"),
    pytest.param("&lt;!-- the old benefits ", id="an unclosed comment"),
]


@pytest.mark.parametrize("hidden", PLAIN_PROSE)
def test_a_plain_text_naming_escaped_tags_is_stored_with_every_word(hidden: str) -> None:
    description = (
        f"Lists use &lt;li&gt; items. Avoid {hidden} tags for safety. "
        "Benefits: 30 days off, remote. Apply now."
    )
    assert arbeitnow.escaped_twice(description)
    text = arbeitnow.text_of(description)
    assert text == plain_text(description)
    assert text.endswith("tags for safety. Benefits: 30 days off, remote. Apply now.")
    assert "<li>" in text


# An answer escaped twice holding a tag outside the closed list: converted the plain way, as
# before J39, its tags staying words; the pass leaves it as stored.
OUTSIDE_THE_LIST = [
    pytest.param(
        '<p><img src="https://example.org/logo.png" alt="Example Robotics"></p>',
        id="an image",
    ),
    pytest.param("<table><tr><td>Salary: 70k</td></tr></table>", id="a table"),
]


@pytest.mark.parametrize("inserted", OUTSIDE_THE_LIST)
def test_an_answer_escaped_twice_with_a_tag_outside_the_list_is_stored_the_old_way(
    inserted: str,
) -> None:
    twice = html.escape(_with(answer(20, 24), inserted))
    assert arbeitnow.escaped_twice(twice)
    text = arbeitnow.text_of(twice)
    assert text == plain_text(twice)
    assert "<p>" in text
    assert "Paragraph 19:" in text
    assert "Duty 23 with" in text
    assert arbeitnow.repair(text) == arbeitnow.Repair(left="loses_words")


@pytest.mark.parametrize("once", TWINS)
def test_an_answer_escaped_twice_is_converted_once_as_every_feeds_is(once: str) -> None:
    twice = html.escape(once)
    assert arbeitnow.escaped_twice(twice)
    text = arbeitnow.text_of(twice)
    assert text == arbeitnow.text_of(once)
    assert "<" not in text
    assert "&" not in text.replace("Python & SQL", "")


def test_a_long_answer_escaped_twice_keeps_its_end() -> None:
    once = "".join(
        f'<p><span style="{STYLE}">Paragraph {n}: Example Robotics plans the routes of its '
        f"warehouse robots and of its people, part {n}.</span></p>"
        for n in range(400)
    )
    assert len(once) > 2 * TEXT_MAX
    text = arbeitnow.text_of(html.escape(once))
    assert text == arbeitnow.text_of(once)
    assert len(text) < TEXT_MAX
    assert text.endswith("of its people, part 399.")


SAID = (
    "Paragraph {n}: Example Robotics plans the routes of its warehouse robots and of its people, "
    "part {n}."
)
WORD_STYLE = "font-family: Arial, sans-serif; font-size: 11pt; color: #000000;"


def long_answer(count: int, *, spans: bool = False, before: dict[int, str] | None = None) -> str:
    """The re-check's long answers (escaped once): `count` paragraphs, each word in its own styled
    span (Word's HTML) or not, with `before[n]` written before paragraph n."""

    def said(n: int) -> str:
        words = SAID.format(n=n).split()
        return " ".join(f'<span style="{WORD_STYLE}">{w}</span>' if spans else w for w in words)

    return "".join((before or {}).get(n, "") + f"<p>{said(n)}</p>" for n in range(count))


# Past the 50,000 characters the conversion keeps (its text, cut there, ends in part 492), and past
# the 200,000 it reads (its text ends in part 126, read up to a span's tag).
LONG = [
    pytest.param(long_answer(600), 64_580, "Paragraph 492: Example Robotics plan", id="kept"),
    pytest.param(
        long_answer(160, spans=True), 250_980, "Example Robotics plans the routes of its", id="read"
    ),
]


@pytest.mark.parametrize(("once", "size", "end"), LONG)
def test_a_long_answer_escaped_twice_is_stored_as_escaped_once(
    once: str, size: int, end: str
) -> None:
    assert len(once) == size
    twice = html.escape(once)
    assert arbeitnow.escaped_twice(twice)
    text = arbeitnow.text_of(twice)
    assert text == arbeitnow.text_of(once) == plain_text(once)
    assert text.endswith(end)
    assert "<" not in text
    assert "Paragraph 492:" in text if size < 4 * TEXT_MAX else "Paragraph 126:" in text


SCRIPT = "<script>track('apply');</script>"
# Something hides words in what the conversion reads and keeps: the old way, as before J39.
HIDDEN_IN_LONG = [
    pytest.param(long_answer(600, before={10: SCRIPT}), id="a script, past what it keeps"),
    pytest.param(
        long_answer(160, spans=True, before={10: SCRIPT}), id="a script, past what it reads"
    ),
    pytest.param(
        long_answer(600, before={300: "<style>p { color: red; }"}),
        id="a style never closed, before the cut of what it keeps",
    ),
    pytest.param(
        long_answer(160, spans=True, before={100: "<!-- the old benefits "}),
        id="a comment never closed, in what it reads",
    ),
    pytest.param(
        long_answer(160, spans=True, before={100: "<script>track('apply');"}),
        id="a script closed past what it reads",
    ),
]


@pytest.mark.parametrize("once", HIDDEN_IN_LONG)
def test_a_long_answer_escaped_twice_hiding_words_is_stored_the_old_way(once: str) -> None:
    twice = html.escape(once)
    text = arbeitnow.text_of(twice)
    assert text == plain_text(twice)
    assert "<p>" in text
    if SCRIPT in once:  # its words and those after it, as written
        assert text.index("track('apply');") < text.index("10:")


# The read bound falls in the paragraph after the padding: inside a word, a tag or a reference.
TAIL = (
    f'<p>Send it to Example Robotics by email&nbsp;today <span style="{WORD_STYLE}">now</span></p>'
)
CUT_INSIDE = [
    pytest.param("email", 2, "Send it to Example Robotics by em", id="a word"),
    pytest.param("style=", 3, "Send it to Example Robotics by email today", id="a tag"),
    pytest.param("&nbsp;", 3, "Send it to Example Robotics by email", id="a reference"),
]


@pytest.mark.parametrize(("where", "into", "end"), CUT_INSIDE)
def test_a_long_answer_cut_by_what_the_conversion_reads(where: str, into: int, end: str) -> None:
    head = long_answer(100, spans=True)
    pad = 4 * TEXT_MAX - len(head) - len("<p>Padding.</p>") - TAIL.index(where) - into
    once = head + "<p>Padding." + " " * pad + "</p>" + TAIL + long_answer(20)
    assert once[: 4 * TEXT_MAX].endswith(where[:into])
    text = arbeitnow.text_of(html.escape(once))
    assert text.endswith(f"Padding.\n{end}")
    assert "<" not in text
    assert "&" not in text
    if where != "&nbsp;":
        assert text == arbeitnow.text_of(once)
    else:  # read once, the cut reference is written as is: "&nb"
        assert arbeitnow.text_of(once) == f"{text}&nb"


def test_a_failed_shape_whose_conversion_would_be_cut_is_left_as_stored() -> None:
    # No feed stores one (its text is TEXT_MAX at most, and a failed shape converts shorter).
    stored = "\n".join(f"<p>{SAID.format(n=n)}</p>" for n in range(600))
    assert len(visible(plain_text(stored))) == TEXT_MAX
    assert arbeitnow.failed_shape(stored)
    assert arbeitnow.repair(stored) == arbeitnow.Repair(left="loses_words")


def test_an_answer_finding_is_its_text_converted_once() -> None:
    item = {
        "slug": "ai-engineer-example-robotics-1",
        "title": "AI engineer",
        "company_name": "Example Robotics",
        "location": "Berlin",
        "url": "https://www.arbeitnow.com/jobs/companies/example-robotics/ai-engineer-1",
        "description": html.escape(answer(2, 2)),
        "created_at": 1_764_000_000,
    }
    found = arbeitnow.finding_of(item, T0)
    assert found is not None
    assert found.text == arbeitnow.text_of(answer(2, 2))
    measured = arbeitnow.finding_of({**item, "description": MEASURED}, T0)
    assert measured is not None
    assert measured.text == MEASURED_TEXT


@pytest.mark.parametrize("once", TWINS)
def test_the_failed_shape_is_repaired_and_keeps_every_word(once: str) -> None:
    stored = stored_before(once)
    assert arbeitnow.failed_shape(stored)
    repaired = arbeitnow.repair(stored).text
    assert repaired is not None
    whole = arbeitnow.text_of(once)  # the employer's answer read once: every word a reader sees
    if len(stored) < TEXT_MAX:
        assert repaired == whole
    else:  # the store cut it inside a tag: the words up to the cut, in order, none lost
        assert stored.rfind("<") > stored.rfind(">")
        assert whole.startswith(repaired)
        assert len(repaired.split()) > 1_000


def _with(once: str, inserted: str) -> str:
    """A twin's HTML with `inserted` written in the middle, as the employer wrote it."""
    middle = once.index("<p>Paragraph 10")
    return once[:middle] + inserted + once[middle:]


# The failed shape with something the conversion reads as more than markup: the repair would lose
# words, so the text is left exactly as stored, its tags as words, and nothing is lost.
LEFT = [
    pytest.param(_with(answer(20, 24), "<style>p { color: #1f2933; }</style>"), id="a style block"),
    pytest.param(_with(answer(20, 24), "<script>track('apply')</script>"), id="a script block"),
    pytest.param(_with(answer(20, 24), "<!-- the old benefits "), id="an unclosed comment"),
    pytest.param(_with(answer(20, 24), "<p>Java: List<String> in services</p>"), id="a <Word>"),
    pytest.param(_with(answer(20, 24), "<p>Read the terms&notice first.</p>"), id="terms&notice"),
]


@pytest.mark.parametrize("once", LEFT)
def test_the_failed_shape_with_more_than_markup_is_left_as_stored(once: str) -> None:
    stored = stored_before(once)
    assert arbeitnow.failed_shape(stored)
    assert arbeitnow.repair(stored) == arbeitnow.Repair(left="loses_words")
    # What a conversion would have done: words after the markup, or a word's own letters, gone.
    assert plain_text(stored).split() != arbeitnow.kept_words(stored)


def test_an_escaped_tag_in_the_prose_of_the_failed_shape_is_a_word_after_the_repair() -> None:
    once = _with(answer(20, 24), "<p>Java: List&lt;String&gt; and a &lt;script&gt; tag.</p>")
    repaired = arbeitnow.repair(stored_before(once)).text
    assert repaired is not None
    assert "Java: List<String> and a <script> tag." in repaired.split("\n")
    assert repaired == arbeitnow.text_of(once)


NOT_THE_SHAPE = [
    pytest.param(MEASURED_TEXT, id="the review's measured example"),
    pytest.param("Use <li> and </li>, <p> and </p>, <ul> and </ul>.", id="six tags, one line"),
    pytest.param(
        "\n".join(["Frontend engineer at Example Labs."] * 30 + ["<p>" * 15 + "</p>" * 15]),
        id="thirty tags on one line of thirty one",
    ),
    pytest.param("<br>" * 40, id="start tags only"),
    pytest.param("</p>" * 40, id="end tags only"),
    pytest.param("<p>a</p>" * 9 + "<li>", id="nineteen tags"),
    pytest.param("&lt;p&gt;a&lt;/p&gt;" * 20, id="escaped tags are words"),
    pytest.param("<span>a</span>" * 20, id="inline tags only"),
]


@pytest.mark.parametrize("text", NOT_THE_SHAPE)
def test_prose_that_names_a_tag_is_not_the_failed_shape(text: str) -> None:
    assert not arbeitnow.failed_shape(text)
    assert arbeitnow.repair(text).text is None


def test_a_text_with_no_block_tag_is_not_counted() -> None:
    assert arbeitnow.repair("Python and SQL, a <String> or two.") == arbeitnow.Repair()


def _pass_log(caplog: pytest.LogCaptureFixture) -> logging.LogRecord:
    [record] = [r for r in caplog.records if r.getMessage() == "jobs.visible_pass"]
    return record


def test_the_pass_repairs_arbeitnows_failed_shape_and_leaves_every_other_text(
    factory: sessionmaker[Session], person: uuid.UUID, caplog: pytest.LogCaptureFixture
) -> None:
    twins = [stored_before(p.values[0]) for p in TWINS]
    repaired = [add(factory, person, text, source="arbeitnow, www.linkedin.com") for text in twins]
    measured = add(factory, person, MEASURED_TEXT, source="arbeitnow")
    styled = add(factory, person, stored_before(LEFT[0].values[0]), source="arbeitnow")
    other = add(factory, person, twins[0], source="www.welcometothejungle.com")
    first_elsewhere = add(factory, person, twins[0], source="www.linkedin.com, arbeitnow")
    kept = (measured, styled, other, first_elsewhere)
    before = {job_id: row_of(factory, job_id) for job_id in kept}
    caplog.set_level(logging.INFO, logger="app.jobs.visible_pass")
    assert walk(factory) == 3
    for job_id, text, param in zip(repaired, twins, TWINS, strict=True):
        row = row_of(factory, job_id)
        assert row["posting_text"] == arbeitnow.repair(text).text
        assert arbeitnow.text_of(param.values[0]).startswith(row["posting_text"])
        assert row["description"] == row["posting_text"][: service.DESCRIPTION_CHARS]
        assert row["signals"] == signals.stored(row["posting_text"])["signals"]
        assert row["updated_at"] == T0
    for job_id, row in before.items():
        assert row_of(factory, job_id) == row  # not written at all: its version is the same
    record = _pass_log(caplog)
    assert record.__dict__["not_repaired"] == 2
    assert record.__dict__["not_repaired_by_reason"] == {"not_a_document": 1, "loses_words": 1}
    assert record.__dict__["not_repaired_ids"] == sorted(str(i) for i in (measured, styled))
    # The pass run twice changes nothing the second time.
    after = {job_id: row_of(factory, job_id) for job_id in (*repaired, *kept)}
    assert walk(factory) == 0
    for job_id, row in after.items():
        assert row_of(factory, job_id) == row
    assert jobs_of(factory, person)


def test_a_text_that_held_only_hidden_characters_is_stored_as_nothing(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    job_id = add(factory, person, "\u200b\ufeff\u2060")
    assert walk(factory) == 1
    row = row_of(factory, job_id)
    assert (row["posting_text"], row["description"]) == (None, None)


def test_a_policy_sentence_hiding_a_character_gives_its_signal_at_the_store_and_in_the_pass(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    policy = "Please do not use AI to write your cover letter."
    hidden = policy.replace("cover", "co​ver").replace("AI", "A⁠I")
    text = f"{CLEAN}\n{hidden}"
    assert signals.stored(text)["signals"] != signals.stored(visible(text))["signals"]
    raw = service.NewJob(
        company="Example Robotics", title="AI engineer", description=text[:300], posting_text=text
    )
    with factory() as db, db.begin():
        stored = service.create_job(db, person, raw, T0).signals
    old_row = add(factory, person, text)
    assert walk(factory) == 1
    pass_signals = row_of(factory, old_row)["signals"]
    for found in (stored, pass_signals):
        assert [s["quote"] for s in found if s["code"] == "ai_policy"] == [policy]
