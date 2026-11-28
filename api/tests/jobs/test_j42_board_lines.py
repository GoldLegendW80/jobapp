"""Jobs J42 (issue #888): a company board's posting keeps its lines. The readers of Greenhouse,
Workable and Recruitee convert a posting's HTML as the job feeds do (ats.py); a posting stored on
one line before gets the same characters with their lines when its board lists it again
(boards/lines.py), with no History event, `updated_at`, the status, a fit set by hand and the
reader's state kept. Real Postgres; the ATSs replaced by the boards' hand-made fixtures."""

import asyncio
import html
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import match, reader, scoring, signals, sweep
from app.jobs.boards import ats, lines
from app.jobs.boards import run as boards
from app.jobs.feeds import _Text, arbeitnow, himalayas, plain_text
from app.jobs.models import Job, JobStatus
from app.profile.schemas import SearchSection, SkillsSection
from tests.jobs.conftest import new_user
from tests.jobs.sweep_helpers import SKILLS, jobs_of, new_sweep, read_sweep, search, set_profile

FIXTURES = Path(__file__).with_name("boards_fixtures")
CLOCK = datetime(2026, 10, 8, 9, 30, tzinfo=UTC)  # the fixtures' dates are a week before
T0 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
LABS = {"company": "Example Labs", "ats": "greenhouse", "board": "examplelabs"}
URL = "https://job-boards.greenhouse.io/examplelabs/jobs/42"
_TAG = re.compile(r"<[^>]+>")


def before_j42(page: str) -> str:
    """A posting's HTML as the readers stored it before J42 (ats.plain): tags became spaces,
    entities were read, whitespace collapsed into one line."""
    return " ".join(html.unescape(_TAG.sub(" ", page)).split())


def body(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


def answer(name: str, edit: Any = None) -> bytes:
    """A fixture's answer, its first posting changed by `edit` when given."""
    data = json.loads(body(name))
    if edit is not None:
        edit(data)
    return json.dumps(data).encode()


def first(name: str, raw: bytes | None = None) -> ats.BoardPosting:
    board = {"greenhouse": "examplelabs", "workable": "examplemodels", "recruitee": "examplelabs"}
    return ats.parse(name, board[name], raw or body(name)).postings[0]


GREENHOUSE_PAGE = html.unescape(json.loads(body("greenhouse"))["jobs"][0]["content"])
OLD = before_j42(GREENHOUSE_PAGE)
NEW = first("greenhouse").text
WORKABLE_OLD = before_j42(json.loads(body("workable"))["jobs"][0]["description"])
WORKABLE_NEW = first("workable").text
_OFFER = json.loads(body("recruitee"))["offers"][0]
RECRUITEE_OLD = before_j42(f"{_OFFER['description']} {_OFFER['requirements']}")
RECRUITEE_NEW = first("recruitee").text


# --- the readers ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "heading", "paragraph", "item", "nested"),
    [
        (
            "greenhouse",
            "What you will do",
            "and ship them to production every week.",
            "Build agents & tools in Python",
            "offline benchmarks",
        ),
        (
            "workable",
            "Requirements",
            "Example Models trains small models for search and ranking.",
            "Three years with PyTorch",
            "Agents & evaluation in Python",
        ),
        ("recruitee", "The team", "LLM agents for travel bookings.", "SQL", "asyncio"),
    ],
)
def test_each_reader_gives_a_paragraph_a_heading_and_a_list_item_their_own_line(
    name: str, heading: str, paragraph: str, item: str, nested: str
) -> None:
    text = first(name).text
    assert {heading, paragraph, item, nested} <= set(text.split("\n"))
    assert "<" not in text.replace("<canvas>", "")  # every real tag read, none left as words


def test_a_row_of_a_table_reads_its_cells_apart_on_one_line() -> None:
    assert first("recruitee").text.endswith("\nLevel Salary\nSenior 65k")
    assert plain_text("<table><tr><td>Salary</td><td>50k</td></tr></table>") == "Salary 50k"
    assert plain_text("<tr><th>Level<td>Senior<td>65k</tr>") == "Level Senior 65k"  # unclosed
    assert plain_text("<p>Salary<b>50</b>k</p>") == "Salary50k"  # inline tags join, as shown


# The HTML standard's rendering (15.3): every element it shows as a block, a list item or a table's
# part, so on its own line. col and colgroup hold no text.
OWN_LINE = (
    "address", "article", "aside", "blockquote", "body", "caption", "center", "dd", "details",
    "dialog", "dir", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1",
    "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "html", "legend", "li", "listing", "main",
    "menu", "nav", "ol", "p", "pre", "search", "section", "summary", "table", "tbody", "tfoot",
    "thead", "tr", "ul", "xmp",
)  # fmt: skip
# Phrasing content, and the controls and media a browser lays out inline in the line.
INLINE = (
    "a", "abbr", "b", "bdi", "bdo", "button", "cite", "code", "data", "del", "dfn", "em", "font",
    "i", "ins", "kbd", "label", "mark", "q", "s", "samp", "small", "span", "strike", "strong",
    "sub", "sup", "time", "tt", "u", "var",
)  # fmt: skip


@pytest.mark.parametrize("tag", OWN_LINE)
def test_an_element_a_browser_shows_on_its_own_line_ends_a_line(tag: str) -> None:
    assert plain_text(f"Alpha<{tag}>Beta</{tag}>Gamma") == "Alpha\nBeta\nGamma"
    assert plain_text(f"<{tag}>Alpha</{tag}><{tag}>Beta</{tag}>") == "Alpha\nBeta"


@pytest.mark.parametrize(
    ("page", "read"),
    [
        ("Alpha<hr>Beta", "Alpha\nBeta"),
        ("Alpha<br/>Beta<hr/>Gamma", "Alpha\nBeta\nGamma"),
        ("Alpha<plaintext>Beta", "Alpha\nBeta"),  # the rest of the page is its text
    ],
)
def test_an_empty_element_on_its_own_line_ends_a_line(page: str, read: str) -> None:
    assert plain_text(page) == read


@pytest.mark.parametrize("tag", INLINE)
def test_an_inline_element_stays_in_its_line(tag: str) -> None:
    assert plain_text(f"Alpha <{tag}>Beta</{tag}>Gamma") == "Alpha BetaGamma"


def test_a_pre_keeps_its_line_breaks_and_folds_its_spaces() -> None:
    """As every line break of a page's text is kept: its indentation and blank lines go."""
    assert plain_text("<p>Run:</p><pre>make test\n    --fast\n\nmake ship</pre>") == (
        "Run:\nmake test\n--fast\nmake ship"
    )


def test_arbeitnows_block_list_is_unchanged() -> None:
    """J39 reads BLOCKS as the markup of an answer escaped twice: an element on its own line
    outside it is a reader's word there, as before."""
    headings = {f"h{level}" for level in range(1, 7)}
    assert headings | {"p", "div", "br", "li", "ul", "ol", "tr"} == arbeitnow.BLOCKS
    assert not arbeitnow.MARKUP & _Text.OWN_LINE


def test_an_arbeitnow_answer_escaped_twice_with_an_element_on_its_own_line_goes_the_old_way() -> (
    None
):
    """Its conversion ends a line at the element and the words it keeps read the tag as a word,
    so the lists differ and it is converted the plain way, its tags staying words, as before."""
    twice = html.escape("<p>Pay</p><dl><dt>Salary</dt><dd>50k</dd></dl><p>More</p>")
    assert arbeitnow.escaped_twice(twice)
    assert arbeitnow.text_of(twice) == plain_text(twice)
    assert "<dl><dt>Salary</dt><dd>50k</dd></dl>" in arbeitnow.text_of(twice)


def test_a_feeds_text_puts_these_elements_on_their_own_lines_too() -> None:
    item = {
        "title": "AI Engineer",
        "guid": "example-labs-ai-engineer",
        "applicationLink": "https://himalayas.app/companies/example-labs/jobs/ai-engineer",
        "description": "<dl><dt>Salary</dt><dd>50k</dd></dl><section>Remote</section>",
    }
    assert himalayas.finding_of(item, CLOCK).text == "Salary\n50k\nRemote"


SHAPES = (
    "<dl><dt>Salary</dt><dd>50k</dd></dl>"
    "<section>First block</section><section>Second block</section>"
    "<blockquote>Quote</blockquote><blockquote>Other</blockquote>"
    "Before<hr>After<pre>line one\nline two</pre>"
)
SHAPES_READ = (
    "Salary\n50k\nFirst block\nSecond block\nQuote\nOther\nBefore\nAfter\nline one\nline two"
)


@pytest.mark.parametrize("name", ["greenhouse", "workable", "recruitee"])
def test_each_reader_keeps_the_words_of_these_elements_apart(name: str) -> None:
    def edit(data: dict[str, Any]) -> None:
        if name == "greenhouse":
            data["jobs"][0]["content"] = html.escape(SHAPES, quote=True)
        elif name == "workable":
            data["jobs"][0]["description"] = SHAPES
        else:
            data["offers"][0].update(description=SHAPES, requirements="")

    text = first(name, answer(name, edit)).text
    assert text == SHAPES_READ
    assert lines.relined(before_j42(SHAPES), text, "") == text  # stored before: its lines


def escaped_once(page: str) -> bytes:
    """A Greenhouse answer whose posting is `page`, escaped once as Greenhouse escapes it."""

    def edit(data: dict[str, Any]) -> None:
        data["jobs"][0]["content"] = html.escape(page, quote=True)

    return answer("greenhouse", edit)


@pytest.mark.parametrize(
    ("prose", "read"),
    [
        ("the &lt;canvas&gt; element", "the <canvas> element"),
        ("write &lt;li&gt; items", "write <li> items"),
        ("a &lt;script&gt;alert(1)&lt;/script&gt; tag", "a <script>alert(1)</script> tag"),
        ("the &amp;nbsp; entity", "the &nbsp; entity"),
        ("R&amp;amp;D", "R&amp;D"),
        ("a &lt;!-- comment --&gt; here", "a <!-- comment --> here"),
        ("&lt;br&gt;", "<br>"),
        ("Fish &amp; chips", "Fish & chips"),
    ],
)
def test_an_escaped_answer_is_unescaped_once_and_converted_once(prose: str, read: str) -> None:
    """A tag or an entity the posting's prose writes as words is still a reader's words: one
    unescape and one conversion, never two of either."""
    text = first("greenhouse", escaped_once(f"<h2>About</h2><p>Use {prose} daily.</p>")).text
    assert text == f"About\nUse {read} daily."


def test_greenhouses_fixture_keeps_the_words_its_prose_writes_as_tags() -> None:
    assert "Draw charts with the <canvas> element and its &nbsp; entity" in NEW.split("\n")


@pytest.mark.parametrize("name", ["greenhouse", "workable", "recruitee"])
def test_a_reference_too_long_for_int_fails_no_board_and_stays_as_written(name: str) -> None:
    """One poisoned posting beside a good one: the board is read, the reference kept as written
    (html.unescape and int() raise on 4,301 digits)."""
    poison = "&#" + "1" * 4_301 + ";"

    def edit(data: dict[str, Any]) -> None:
        jobs = data["offers" if name == "recruitee" else "jobs"]
        bad = dict(jobs[0], title="AI Engineer, poisoned")
        key = {"greenhouse": "content", "workable": "description", "recruitee": "description"}
        bad[key[name]] = f"<p>Pay {poison} a year</p>"
        bad.pop("requirements", None)
        for url in ("absolute_url", "url", "careers_url"):
            if url in bad:
                bad[url] += "-poisoned"
        jobs.append(bad)

    parsed = ats.parse(name, "examplelabs", answer(name, edit))
    poisoned = next(p for p in parsed.postings if p.title == "AI Engineer, poisoned")
    assert poisoned.text == f"Pay {poison} a year"
    assert parsed.postings[0].text == first(name).text


def test_a_boards_text_keeps_the_boards_bound_not_the_feeds() -> None:
    """At most 200,000 characters kept (sweep.POSTING_TEXT_MAX, the feeds keep 50,000), read from
    the first 800,000 characters of the HTML."""

    def long(paragraphs: int) -> bytes:
        def edit(data: dict[str, Any]) -> None:
            data["jobs"][0]["description"] = (
                "<p>Ten words of a long posting, then more.</p>" * (paragraphs)
                + "<p>LAST-WORDS</p>"
            )

        return answer("workable", edit)

    kept = first("workable", long(10_000)).text
    assert len(kept) == ats.TEXT_MAX == sweep.POSTING_TEXT_MAX == 200_000
    unread = first("workable", long(20_000)).text  # the last paragraph past 800,000 characters
    assert "LAST-WORDS" not in unread
    assert "LAST-WORDS" in first("workable", long(4_000)).text


def test_ats_has_one_converter() -> None:
    assert not hasattr(ats, "plain")


# --- the same characters with their lines (lines.relined) ----------------------------------------

TITLE = "AI Engineer (m/w/d)"
CUT_START, CUT_END = OLD.index("About the role"), OLD.index("for this role") + len("for this role")


def _new_cut() -> str:
    lines_ = NEW.split("\n")
    start = lines_.index("About the role")
    end = lines_.index("Visa sponsorship is not available for this role")
    return "\n".join(lines_[start : end + 1])


ACCEPTED = [
    pytest.param(OLD, NEW, "AI Engineer", NEW, id="the whole text"),
    pytest.param(
        f"{TITLE}\n\n{OLD}", NEW, TITLE, f"{TITLE}\n\n{NEW}", id="the title the store put first"
    ),
    pytest.param(OLD[CUT_START:CUT_END], NEW, "AI Engineer", _new_cut(), id="the reader's cut"),
    pytest.param(
        "Requirements : Python , SQL .",
        "Requirements:\nPython, SQL.",
        "",
        "Requirements:\nPython, SQL.",
        id="spaces an end tag put before punctuation",
    ),
    pytest.param(
        "Salary 50k More",
        plain_text("<dl><dt>Salary</dt><dd>50k</dd></dl><p>More</p>"),
        "",
        "Salary\n50k\nMore",
        id="a definition list",
    ),
    pytest.param(
        "First block Second block x",
        plain_text("<section>First block</section><section>Second block</section><p>x</p>"),
        "",
        "First block\nSecond block\nx",
        id="two sections",
    ),
    pytest.param(
        "Quote Other x",
        plain_text("<blockquote>Quote</blockquote><blockquote>Other</blockquote><p>x</p>"),
        "",
        "Quote\nOther\nx",
        id="two blockquotes",
    ),
    pytest.param(
        "Pay : 70 000 EUR a year ( remote ) . Next",
        "Pay: 70 000 EUR a year (remote).\nNext",
        "",
        "Pay: 70 000 EUR a year (remote).\nNext",
        id="spaces beside punctuation go, those between digits stay",
    ),
    pytest.param(
        "Инженер Париж ١٢ ٣٤ हिंदी भाषा x",
        "Инженер\nПариж ١٢ ٣٤\nहिंदी भाषा x",
        "",
        "Инженер\nПариж ١٢ ٣٤\nहिंदी भाषा x",
        id="words and digits of other scripts kept apart",
    ),
    pytest.param(
        "Salary 50k More",
        plain_text("<table><tr><td>Salary</td><td>50k</td></tr></table><p>More</p>"),
        "",
        "Salary 50k\nMore",
        id="a table's cells",
    ),
    pytest.param(
        "Salaire 70 k€ par an",
        "Salaire 70 k€\npar an",
        "",
        "Salaire 70 k€\npar an",
        id="a no-break space",
    ),
    pytest.param(
        "Prénom Straße 東京 مهندس ❤️ ok",
        "Prénom Straße\n東京 مهندس\n❤️ ok",
        "",
        "Prénom Straße\n東京 مهندس\n❤️ ok",
        id="accents, another script, an emoji",
    ),
    pytest.param("A b. A b.", "A b.\nA b.\nA b.", "", "A b.\nA b.", id="a repeated paragraph"),
    pytest.param("one two three", "zero\none two\nthree\nfour", "", "one two\nthree", id="a run"),
    pytest.param("one two", "one\ntwo three", "", "one\ntwo", id="a text the store cut at its end"),
    pytest.param(WORKABLE_OLD, WORKABLE_NEW, "", WORKABLE_NEW, id="workable's fixture"),
    pytest.param(RECRUITEE_OLD, RECRUITEE_NEW, "", RECRUITEE_NEW, id="recruitee's fixture"),
    pytest.param("a b c", "a\tb\nc", "", "a\tb\nc", id="a tab"),
    pytest.param("• Python • SQL", "• Python\n• SQL", "", "• Python\n• SQL", id="bullets"),
    pytest.param(
        "Apply on our site here",
        "Apply on our\nsite here.",
        "",
        "Apply on our\nsite here",
        id="a cut that left the closing punctuation out",
    ),
    pytest.param(
        "agents that read job postings, write tests for them and ship",
        NEW,
        "",
        "agents that read job postings, write tests for them\nand ship",
        id="a cut starting and ending inside lines",
    ),
    pytest.param(
        f"{TITLE}\n\n{OLD[:CUT_END]}",
        NEW,
        TITLE,
        f"{TITLE}\n\n{NEW[: NEW.index('for this role') + len('for this role')]}",
        id="the title and a cut",
    ),
    pytest.param(
        "مهندس ذكاء اصطناعي في باريس",
        "مهندس ذكاء\nاصطناعي في باريس",
        "",
        "مهندس ذكاء\nاصطناعي في باريس",
        id="a right to left text",
    ),
    pytest.param("R&D lab , Paris", "R&D lab,\nParis", "", "R&D lab,\nParis", id="an ampersand"),
    pytest.param("  a b  ", "a\nb", "", "a\nb", id="spaces around the stored text"),
    pytest.param("x" * 50_000 + " y", "x" * 50_000 + "\ny", "", "x" * 50_000 + "\ny", id="long"),
]


@pytest.mark.parametrize(("stored", "board", "title", "expected"), ACCEPTED)
def test_the_same_characters_get_the_boards_lines(
    stored: str, board: str, title: str, expected: str
) -> None:
    new = lines.relined(stored, board, title)
    assert new == expected
    assert "".join(new.split()) == "".join(stored.split())  # only spaces and line breaks moved
    assert lines.relined(new, board, title) is None  # done once


REFUSED = [
    pytest.param(NEW, NEW, "AI Engineer", id="lines already, the same text"),
    pytest.param(NEW.replace("\n", "\n\n"), NEW, "", id="lines already, other spacing"),
    pytest.param(OLD.replace("Python", "Rust"), NEW, "", id="a word changed"),
    pytest.param(OLD.replace("in Python", "in modern Python"), NEW, "", id="a word added"),
    pytest.param(OLD.replace("tools in Python", "tools Python"), NEW, "", id="a word removed"),
    pytest.param(OLD.replace("week.", "week!"), NEW, "", id="punctuation changed"),
    pytest.param(OLD.replace("Python", "python"), NEW, "", id="case changed"),
    pytest.param("Prénom ok", "Prenom\nok", "", id="an accent changed"),
    pytest.param(OLD.replace("70k", "75k"), NEW, "", id="a number changed"),
    pytest.param("", NEW, "", id="no stored text"),
    pytest.param(None, NEW, "", id="a null text"),
    pytest.param("   ", "a\nb", "", id="spaces only"),
    pytest.param(OLD, OLD, "", id="the board gives one line too"),
    pytest.param(OLD, "", "", id="the board gives no text"),
    pytest.param(OLD + " Apply now.", NEW, "", id="more than the board's text"),
    pytest.param("b a", "a\nb", "", id="the order changed"),
    pytest.param(f"Other title\n\n{OLD}", NEW, TITLE, id="a first line that is not the title"),
    pytest.param(f"{TITLE}\n{OLD}", NEW, TITLE, id="the title with one line break"),
    pytest.param(OLD.replace(". ", ".\r", 1), NEW, "", id="a carriage return"),
    pytest.param("Build agents", NEW, "", id="a run within one line"),
    pytest.param("alert(1) Build agents", "<script>\nBuild agents", "", id="a script's words"),
    pytest.param("a b", "ab\nc", "", id="a word the board writes whole on one line"),
    pytest.param("ab", "a\nc b", "", id="characters apart"),
    pytest.param("an e-mail to us", "an email\nto us", "", id="a hyphen added"),
    pytest.param(OLD.replace("70k", "170k"), NEW, "", id="a digit added"),
    pytest.param("ok ❤️ thanks", "ok\nthanks", "", id="an emoji removed"),
    pytest.param("a b c", "a\nb\nd", "", id="the last character changed"),
    pytest.param("Z a b", "a\nb", "", id="the first character changed"),
]

# The board's text glues two words the stored text held apart (a gap of the converter): left.
GLUED = [
    pytest.param(
        "Salary 50k More",
        plain_text("<x-row>Salary</x-row><x-row>50k</x-row><p>More</p>"),
        "",
        id="an element the converter does not know",
    ),
    pytest.param(
        f"{TITLE}\n\nSalary 50k More",
        plain_text("<x-row>Salary</x-row><x-row>50k</x-row><p>More</p>"),
        TITLE,
        id="after the title the store put first",
    ),
    pytest.param("Salary 50k More", "Salary50k\nMore", "", id="the review's definition list"),
    pytest.param(
        "First block Second block x", "First blockSecond block\nx", "", id="the review's sections"
    ),
    pytest.param("Quote Other x", "QuoteOther\nx", "", id="the review's blockquotes"),
    pytest.param("foo bar baz", "foobar\nbaz", "", id="a word an inline tag split, or a glue"),
    pytest.param("one two three", "zero\nonetwo\nthree", "", id="inside a run"),
    pytest.param("a b c", "a\nbc", "", id="the last two characters"),
    pytest.param("70 000 EUR x", "70000 EUR\nx", "", id="two numbers"),
    pytest.param("Level 3 x", "Level3\nx", "", id="a word and a digit"),
    pytest.param("Инженер Париж x", "ИнженерПариж\nx", "", id="cyrillic"),
    pytest.param("Μηχανικός Αθήνα x", "ΜηχανικόςΑθήνα\nx", "", id="greek"),
    pytest.param("東京 大阪 x", "東京大阪\nx", "", id="ideographs"),
    pytest.param("हिंदी भाषा x", "हिंदीभाषा\nx", "", id="a word ending in a vowel sign"),
    pytest.param("مهندس ذكاء x", "مهندسذكاء\nx", "", id="arabic"),
    pytest.param("١٢ ٣٤ x", "١٢٣٤\nx", "", id="arabic-indic digits"),
    pytest.param("ok ❤️ thanks x", "ok\n❤️thanks x", "", id="after an emoji's selector"),
    pytest.param("70\xa0k x", "70k\nx", "", id="a no-break space"),
    pytest.param("a　b x", "ab\nx", "", id="an ideographic space"),
    pytest.param("Prénom Straße x", "PrénomStraße\nx", "", id="accented letters"),
]


@pytest.mark.parametrize(("stored", "board", "title"), REFUSED)
def test_anything_else_stays_as_stored(stored: str | None, board: str, title: str) -> None:
    assert lines.relined(stored, board, title) is None


@pytest.mark.parametrize(("stored", "board", "title"), GLUED)
def test_a_board_text_that_glues_two_words_leaves_the_stored_text(
    stored: str, board: str, title: str
) -> None:
    body = stored.removeprefix(f"{title}\n\n") if title else stored
    assert "".join(body.split()) in "".join(board.split())  # the same characters otherwise
    assert lines.relined(stored, board, title) is None


def test_the_title_line_is_the_one_the_store_puts_first() -> None:
    finding = boards.BoardFinding(
        title=TITLE, location="London, UK", url=URL, text=OLD, company="Example Labs",
        published_on="2026-10-02", detail_read_at=CLOCK.isoformat(),
    )  # fmt: skip
    stored = sweep._read_of(finding)[0].text
    now = sweep._read_of(boards.BoardFinding(**{**vars(finding), "text": NEW}))[0].text
    assert stored == f"{TITLE}\n\n{OLD}"
    assert lines.relined(stored, NEW, TITLE) == now


def test_the_same_words_give_the_same_fingerprint() -> None:
    assert signals.fingerprint(OLD) is not None
    assert signals.fingerprint(OLD) == signals.fingerprint(NEW)
    # What the lines change: a sentence a list item made, quoted as it is written.
    assert [s.quote for s in signals.read(NEW)] == [
        "Visa sponsorship is not available for this role"
    ]
    [merged] = signals.read(OLD)
    assert merged.quote.startswith("What you will do Build agents")
    assert merged.quote.endswith("…")


# --- a search that sees the posting on its board again --------------------------------------------


@pytest.fixture
def person(factory: sessionmaker[Session]) -> uuid.UUID:
    return new_user(factory, f"e2e-j42-lines-{uuid.uuid4().hex[:6]}@example.org")


def transport(raw: bytes | None = None) -> httpx.MockTransport:
    return httpx.MockTransport(lambda _: httpx.Response(200, content=raw or body("greenhouse")))


async def no_wait(_: float) -> None:
    return None


SEARCH = search(company_boards=[LABS], interactive_sites=[])
SKILLED = SkillsSection.model_validate(SKILLS)
PLATFORM = f"{URL}-platform"


def two_postings(data: dict[str, Any]) -> None:
    """Posting 42 and a copy of it at another address."""
    data["jobs"].append(dict(data["jobs"][0], title="AI Engineer, platform", absolute_url=PLATFORM))


def search_at(
    factory: sessionmaker[Session], user: uuid.UUID, at: datetime, raw: bytes | None = None
) -> dict[str, Any]:
    set_profile(factory, user, SEARCH)
    sweep_id = new_sweep(factory, user)
    hook = boards.Boards(transport(raw), sleep=no_wait)
    asyncio.run(sweep.run_sweep(factory, sweep_id, lambda: at, apis=(hook,)))
    [entry] = [s for s in read_sweep(factory, sweep_id).stats["sources"] if s["mode"] == "api"]
    return entry


def stored_before_j42(
    factory: sessionmaker[Session], user: uuid.UUID, raw: bytes | None = None, **fields: Any
) -> uuid.UUID:
    """The board's posting 42 as a store before J42 left it: its text on one line and what
    derives from it, then set by the person (shortlisted, a fit by hand) and read by the reader;
    with `raw`, every posting of that answer at an address of the board's."""
    search_at(factory, user, CLOCK, raw)
    values = {
        "posting_text": OLD,
        "description": OLD[:300],
        **signals.stored(OLD),
        "status": JobStatus.shortlisted,
        "fit": 42,
        "fit_overridden": True,
        "competencies": [],
        "gaps": [],
        "read_at": T0,
        "read_attempts": 1,
        "match": {"text": reader._digest(OLD), "career": 70, "culture": 60},
        "updated_at": T0,
        **fields,
    }
    with factory() as db, db.begin():
        db.execute(
            sa.update(Job)
            .where(Job.user_id == user, Job.url.startswith(URL))
            .values(**values)
            .execution_options(synchronize_session=False)
        )
    [job] = [j for j in jobs_of(factory, user) if j.url == URL]
    return job.id


def row_of(factory: sessionmaker[Session], job_id: uuid.UUID) -> dict[str, Any]:
    """The row as Postgres holds it, its version (`xmin`) and its events' count."""
    with factory() as db:
        found = db.execute(
            sa.text(
                "select xmin::text as version, posting_text, description, signals, "
                "text_fingerprint, closes_on, status, fit, fit_overridden, competencies, gaps, "
                "updated_at, read_at, read_attempts, match, "
                "(select count(*) from job_events e where e.job_id = jobs.id) as events "
                "from jobs where id = :id"
            ),
            {"id": job_id},
        ).one()
        return dict(found._mapping)


def scored(text: str) -> Any:
    return scoring.score(text, SKILLED, title="AI Engineer", search=SearchSection(**SEARCH))


def test_a_posting_stored_on_one_line_gets_its_lines_and_nothing_else_moves(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    job_id = stored_before_j42(factory, person)
    before = row_of(factory, job_id)
    entry = search_at(factory, person, CLOCK + timedelta(days=1))
    after = row_of(factory, job_id)
    assert after["posting_text"] == NEW
    assert after["description"] == NEW[:300]
    # Still one row, found again as before J42: not a new posting, a duplicate as it was.
    assert len(jobs_of(factory, person)) == 2
    assert (entry["found"], entry["postings"], entry["duplicates"]) == (2, 0, 2)
    # No History event; its place in the lists, its status, the fit set by hand, the reader's
    # state stay.
    for name in ("events", "updated_at", "status", "fit", "fit_overridden", "read_at"):
        assert after[name] == before[name], name
    assert after["read_attempts"] == before["read_attempts"] == 1
    # What derives from the text, computed again: the same fingerprint, the line as the quote.
    assert after["text_fingerprint"] == before["text_fingerprint"] == signals.fingerprint(NEW)
    assert after["signals"] == signals.stored(NEW)["signals"] != before["signals"]
    result = scored(NEW)
    assert (after["competencies"], after["gaps"]) == (list(result.competencies), list(result.gaps))
    assert after["competencies"] != before["competencies"]
    # The career and culture reading was of the old text's digest: it is made again.
    assert after["match"] == before["match"]
    with factory() as db:
        assert job_id in set(db.scalars(sa.select(Job.id).where(match._ratable(0))))


def test_a_fit_not_set_by_hand_is_computed_again_without_a_history_event(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    job_id = stored_before_j42(factory, person, fit=3, fit_overridden=False)
    events = row_of(factory, job_id)["events"]
    search_at(factory, person, CLOCK + timedelta(days=1))
    after = row_of(factory, job_id)
    assert after["fit"] == scored(NEW).fit != 3
    assert after["events"] == events


def test_a_posting_its_filters_now_leave_out_gets_its_lines_too(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    job_id = stored_before_j42(factory, person)
    entry = search_at(factory, person, CLOCK + timedelta(days=60))
    assert (entry["left_out"], entry["found"]) == ({"too_old": 2}, 0)
    assert row_of(factory, job_id)["posting_text"] == NEW


def test_a_posting_whose_words_changed_keeps_its_text(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    changed = OLD.replace("Python", "Rust")
    job_id = stored_before_j42(factory, person, posting_text=changed)
    search_at(factory, person, CLOCK + timedelta(days=1))
    assert row_of(factory, job_id)["posting_text"] == changed


def test_a_posting_no_longer_on_its_board_keeps_its_one_line(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    job_id = stored_before_j42(factory, person)

    def gone(data: dict[str, Any]) -> None:
        data["jobs"] = data["jobs"][1:]

    search_at(factory, person, CLOCK + timedelta(days=1), answer("greenhouse", gone))
    assert row_of(factory, job_id)["posting_text"] == OLD


def test_a_description_that_is_not_the_head_of_the_text_is_kept(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    job_id = stored_before_j42(factory, person, description="Agents for Example Labs.")
    search_at(factory, person, CLOCK + timedelta(days=1))
    after = row_of(factory, job_id)
    assert (after["posting_text"], after["description"]) == (NEW, "Agents for Example Labs.")


@pytest.mark.parametrize(
    "held",
    [
        pytest.param({"read_run_id": uuid.uuid4()}, id="a posting reader's run"),
        pytest.param({"match": {"run": str(uuid.uuid4())}}, id="a rating's run"),
    ],
)
def test_a_posting_a_run_holds_is_left_for_the_next_search(
    factory: sessionmaker[Session], person: uuid.UUID, held: dict[str, Any]
) -> None:
    job_id = stored_before_j42(factory, person, **held)
    search_at(factory, person, CLOCK + timedelta(days=1))
    assert row_of(factory, job_id)["posting_text"] == OLD
    with factory() as db, db.begin():
        db.execute(sa.update(Job).where(Job.id == job_id).values(read_run_id=None, match=None))
    search_at(factory, person, CLOCK + timedelta(days=2))
    assert row_of(factory, job_id)["posting_text"] == NEW


def give(factory: sessionmaker[Session], user: uuid.UUID, **options: Any) -> int:
    return lines.give_lines(factory, user, [first("greenhouse")], SKILLED, None, **options)


def test_a_posting_another_transaction_holds_is_skipped_without_waiting(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    job_id = stored_before_j42(factory, person)
    with factory() as holder:
        holder.execute(sa.select(Job.id).where(Job.id == job_id).with_for_update())
        assert give(factory, person) == 0
    before = row_of(factory, job_id)
    assert before["posting_text"] == OLD
    assert give(factory, person) == 1
    after = row_of(factory, job_id)
    assert (after["posting_text"], after["updated_at"]) == (NEW, T0)
    assert give(factory, person) == 0  # done: nothing written again
    assert row_of(factory, job_id)["version"] == after["version"]


def test_nothing_is_written_once_the_run_let_go_of_its_row(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    job_id = stored_before_j42(factory, person)
    before = row_of(factory, job_id)
    assert give(factory, person, stopped=lambda: True) == 0
    assert row_of(factory, job_id)["version"] == before["version"]


def test_another_persons_posting_at_the_same_address_is_never_read(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    other = new_user(factory, f"e2e-j42-other-{uuid.uuid4().hex[:6]}@example.org")
    job_id = stored_before_j42(factory, other)
    assert give(factory, person) == 0
    assert row_of(factory, job_id)["posting_text"] == OLD


def test_a_posting_kept_from_a_card_is_left_to_its_pages_read(
    factory: sessionmaker[Session], person: uuid.UUID
) -> None:
    job_id = stored_before_j42(factory, person, list_url="https://jobs.example-robotics.test/list")
    assert give(factory, person) == 0
    assert row_of(factory, job_id)["posting_text"] == OLD


def test_a_posting_whose_write_fails_fails_alone(
    factory: sessionmaker[Session], person: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = answer("greenhouse", two_postings)
    job_id = stored_before_j42(factory, person, raw)
    [platform] = [j.id for j in jobs_of(factory, person) if j.url == PLATFORM]
    real = lines._derived

    def failing(job: Job, *args: Any) -> dict[str, Any]:
        if job.url == URL:
            raise RuntimeError("one bad posting")
        return real(job, *args)

    monkeypatch.setattr(lines, "_derived", failing)
    postings = ats.parse("greenhouse", "examplelabs", raw).postings
    assert lines.give_lines(factory, person, postings, SKILLED, None) == 1
    assert row_of(factory, job_id)["posting_text"] == OLD
    assert row_of(factory, platform)["posting_text"] == NEW


def test_a_failure_of_the_lines_leaves_the_board_stored(
    factory: sessionmaker[Session], person: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = stored_before_j42(factory, person)

    def broken(*_: Any, **__: Any) -> int:
        raise RuntimeError("the lines failed")

    monkeypatch.setattr(lines, "give_lines", broken)
    entry = search_at(factory, person, CLOCK + timedelta(days=1))
    assert (entry["outcome"], entry["duplicates"]) == ("complete", 2)
    assert row_of(factory, job_id)["posting_text"] == OLD
