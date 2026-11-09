"""B21 (owner, 2026-11-04: "the app refused each one: the first with detail_not_opened, then
every retry with title_not_on_page. The title was on the page each time"): a job website's
posting is verified like a person's eyes on any page its agent opened in this session, and an
address it never opened is still refused, so no invented posting is stored.

The rows of the spec's Testing section first, then the title rule's accept and refuse table.
Invented hosts and titles only."""

import asyncio
import json
from datetime import UTC, datetime

import pytest

from app.browser import finder, sources
from tests.browser.test_b13_runtime import BASE, InteractiveTools, session

JOB = BASE + "offres/246286"


def page(*lines: str) -> str:
    return "\n".join(lines)


OFFER = page(  # the VIE site's offer page: two h1, the title in an h2, an offer number
    "- banner:",
    '  - heading "Mon Volontariat International" [level=1]',
    "- main:",
    '  - heading "Offre n° 246286" [level=1]',
    '  - heading "AI Engineer Consultant (H/F)" [level=2]',
    "  - paragraph: SIA Example Partners, Melbourne (Australie)",
    "  - paragraph: Missions GenAI: RAG, agentic AI.",
)


def opened(*pages: tuple[str, str]) -> tuple[finder.FinderSession, list]:
    """A job website's session that read each (address, snapshot) in turn, and the batches its
    reports committed."""
    batches: list = []

    async def commit(batch):
        batches.append(batch)

    one = session(InteractiveTools(), "e2e-person")
    one.on_verified = commit
    one.clock = lambda: datetime(2026, 11, 4, 9, tzinfo=UTC)
    for url, snapshot in pages:
        one._read(url, snapshot)
    return one, batches


def reported(one: finder.FinderSession, **item) -> dict:
    posting = {"url": JOB, "text": "Invented model text", **item}
    answer, error = asyncio.run(one.report_verified([posting]))
    assert not error
    return json.loads(answer)


# --- the spec's rows ---------------------------------------------------------------------------


def test_a_posting_opened_three_pages_earlier_is_accepted():
    one, batches = opened(
        (JOB, OFFER),
        (BASE + "offres/246285", page('- heading "Data Engineer" [level=1]')),
        (BASE + "offres/recherche?q=genai", page("- paragraph: 9 offres")),
        (BASE + "offres/recherche?q=genai&page=2", page("- paragraph: 3 offres")),
    )
    assert reported(one, title="AI Engineer Consultant (H/F)")["kept"] == 1
    [finding] = batches[0].findings
    assert (finding.url, finding.title) == (JOB, "AI Engineer Consultant (H/F)")
    assert finding.evidence["rule"] == "heading"
    assert "Missions GenAI: RAG, agentic AI." in finding.text  # the page's text, not the model's
    assert "Invented model text" not in finding.text


def test_a_page_with_two_h1_accepts_the_title_found_in_its_text():
    one, batches = opened(
        (JOB, page(
            '- heading "Offres VIE" [level=1]',
            '- heading "Offre n° 246286" [level=1]',
            "- generic: AI Engineer Consultant (H/F)",
            "- paragraph: Missions GenAI.",
        )),
    )  # fmt: skip
    assert reported(one, title="AI Engineer Consultant (H/F)")["kept"] == 1
    [finding] = batches[0].findings
    assert (finding.title, finding.evidence["rule"]) == (
        "AI Engineer Consultant (H/F)",
        "page_text",
    )
    assert finding.text.endswith("Missions GenAI.")  # no single detail: the visible lines


@pytest.mark.parametrize(
    "title",
    [
        "ai engineer consultant (h/f)",  # case
        "AI ENGINEER CONSULTANT",  # case, the marker left out
        "AI Engineer Consultant",  # "(H/F)" left out
        "AI  Engineer   Consultant  (H/F)",  # extra spaces
        "AI Engineer Consultant H/F",  # no brackets
        "AI Engineer Consultant (H / F)",  # spaced marker
        "AI Engineer Consultant, H/F",
        "AI Engineer Consultant (F/H)",  # a marker the page does not show: set aside
        "AI Engineer Consultant (m/w/d)",
    ],
)
def test_a_title_with_other_case_accents_marker_or_spaces_is_accepted(title):
    one, batches = opened((JOB, OFFER))
    assert reported(one, title=title)["kept"] == 1
    assert batches[0].findings[0].evidence["rule"] in {"heading", "page_text"}


def test_accents_are_aside_too():
    one, batches = opened(
        (JOB, page('- heading "Ingénieur Données" [level=1]', "- paragraph: Paris")),
    )
    assert reported(one, title="ingenieur donnees")["kept"] == 1
    assert batches[0].findings[0].title == "Ingénieur Données"  # the heading as shown


def test_a_title_not_on_the_page_stores_the_page_s_one_heading():
    one, batches = opened(
        (JOB, page('- heading "Data Engineer" [level=1]', "- paragraph: Build pipelines.")),
    )
    assert reported(one, title="GenAI Forward Deployed Engineer")["kept"] == 1
    [finding] = batches[0].findings
    assert (finding.title, finding.evidence["rule"]) == ("Data Engineer", "primary_heading")


def test_a_title_not_on_a_page_with_no_clear_heading_is_refused_with_what_to_do():
    one, batches = opened(
        (JOB, page('- heading "One" [level=1]', '- heading "Two" [level=1]', "- paragraph: x")),
    )
    answer = reported(one, title="GenAI Engineer")
    assert answer["kept"] == 0
    [refusal] = answer["refused"]
    assert refusal["reason"] == "title_not_on_page"
    assert "browser_snapshot" in refusal["do"]
    assert not batches


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("/offres/999999", "not_seen"),  # never seen, never opened: an invented posting
        ("/offres/246285", "detail_not_opened"),  # a link of a page read, never opened
        ("https://elsewhere.example.test/offres/1", "not_seen"),
    ],
)
def test_an_address_never_opened_is_refused_and_told_to_open_it(url, reason):
    one, batches = opened(
        (BASE + "offres/recherche", page('- link "Data Engineer"', "  - /url: /offres/246285")),
        (JOB, OFFER),
    )
    answer = reported(one, title="Data Engineer", url=url)
    assert answer["kept"] == 0
    assert answer["refused"] == [
        {"index": 0, "reason": reason, "do": finder.OPEN_THEN_REPORT},
    ]
    assert "open the posting's page" in finder.OPEN_THEN_REPORT.lower()
    assert not batches


def test_a_company_claim_not_on_the_page_is_dropped_not_refusing():
    one, batches = opened((JOB, OFFER))
    answer = reported(
        one,
        title="AI Engineer Consultant",
        company="Invented Employer",
        location="Melbourne",
        published_on="2026-11-01",
    )
    assert answer["kept"] == 1
    [finding] = batches[0].findings
    assert finding.company is None  # not on the page
    assert finding.published_on is None  # not on the page
    assert finding.location == "Melbourne"  # its words are on the page
    assert finding.evidence["location_rule"] == "page_text"
    assert "company" not in finding.evidence


def test_a_labelled_field_wins_over_the_claim():
    one, batches = opened(
        (JOB, page(
            '- heading "Robot Engineer" [level=1]',
            "- paragraph: Entreprise: Example Robotics",
            "- paragraph: Lieu: Lyon",
            "- paragraph: Published: 2026-10-30",
        )),
    )  # fmt: skip
    reported(one, title="Robot Engineer", company="Other", location="Paris", published_on="x")
    [finding] = batches[0].findings
    assert (finding.company, finding.location, finding.published_on) == (
        "Example Robotics", "Lyon", "2026-10-30"
    )  # fmt: skip
    assert finding.evidence["company_rule"] == "label"


@pytest.mark.parametrize(
    ("item", "reason"),
    [
        ({"title": "", "text": "x"}, "missing_field"),
        ({"title": " - ", "text": "x"}, "missing_field"),  # no word
        ({"title": "Robot Engineer", "text": "  "}, "missing_field"),
        ({"title": "(H/F)", "text": "x"}, "missing_field"),  # a marker alone
        ({"title": "R" * (finder.TITLE_MAX + 1), "text": "x"}, "missing_field"),
        ({"title": "Robot Engineer", "text": "x", "url": BASE}, "not_a_posting_page"),
        # A NUL, a control or a lone surrogate: Postgres would refuse the whole batch.
        ({"title": "Robot Engineer\x00", "text": "x"}, "missing_field"),
        ({"title": "Robot\x07 Engineer", "text": "x"}, "missing_field"),
        ({"title": "Robot Engineer\x9b", "text": "x"}, "missing_field"),
        ({"title": "Robot\ud800 Engineer", "text": "x"}, "missing_field"),
        ({"title": "Robot Engineer", "text": "Build\x00robots"}, "missing_field"),
        ({"title": "Robot Engineer", "text": "Build \udfff robots"}, "missing_field"),
        ({"title": "Robot Engineer", "text": "x", "url": JOB + "\x00"}, "missing_field"),
    ],
)
def test_empty_titles_texts_and_field_limits_stay_refused(item, reason):
    one, batches = opened((JOB, page('- heading "Robot Engineer" [level=1]')))
    answer, _ = asyncio.run(one.report_verified([{"url": JOB, **item}]))
    assert json.loads(answer)["refused"][0]["reason"] == reason
    assert not batches


def test_a_duplicate_stays_refused():
    one, batches = opened((JOB, OFFER))
    assert reported(one, title="AI Engineer Consultant")["kept"] == 1
    assert reported(one, title="AI Engineer Consultant")["refused"][0]["reason"] == "duplicate"
    assert len(batches) == 1


def test_the_record_of_opened_pages_is_bounded_oldest_first():
    one, batches = opened((JOB, OFFER))
    for index in range(finder.OPENED_MAX):
        one._read(BASE + f"offres/{index}", page(f'- heading "Role {index}" [level=1]'))
    assert len(one.pages_read) == finder.OPENED_MAX
    assert JOB not in one.pages_read  # the oldest went first
    assert reported(one, title="AI Engineer Consultant")["refused"][0]["reason"] in {
        "detail_not_opened",
        "not_seen",
    }
    one._read(JOB, OFFER)  # opened again: the newest
    assert reported(one, title="AI Engineer Consultant")["kept"] == 1
    assert len(batches) == 1


def test_the_report_path_without_commits_reads_any_opened_page_too():
    """`report` (no run's commits: a caller that is not a sweep) keeps the same rule."""
    one, _ = opened((JOB, OFFER), (BASE + "offres/recherche", page("- paragraph: 9 offres")))
    one.on_verified = None
    answer, _ = asyncio.run(
        one.report_verified([{"title": "ai engineer consultant", "url": JOB, "text": "x"}])
    )
    assert json.loads(answer)["kept"] == 1
    assert one.found[0].title == "AI Engineer Consultant (H/F)"  # the heading it equals
    assert "Missions GenAI" in one.found[0].text


# --- the title rule's accept and refuse table (a word sequence, never a pattern) ----------------


def read(*lines: str) -> finder.PageRead:
    """The record code keeps of a page read (`FinderSession._read`)."""
    one, _ = opened((JOB, page(*lines)))
    return one.pages_read[JOB]


SHOWN = read(  # two h1: no clear primary heading
    '- heading "Offres" [level=1]',
    '- heading "Ingénieur IA (H/F)" [level=1]',
    "- paragraph: GenAI Solution Architect, Paris 75001 C++ & R&D",
)


@pytest.mark.parametrize(
    "title",
    [
        "Ingénieur IA (H/F)",
        "ingénieur ia (h/f)",
        "INGENIEUR IA",
        "Ingenieur IA H/F",
        "Ingénieur  IA",
        "Ingénieur-IA",
        "Ingénieur.IA",
        "ingénieur ia (f/h)",
        "ingénieur ia - h/f",
        "GenAI Solution Architect",
        "genai solution architect",
        "GenAI  Solution\tArchitect",
        "GenAI-Solution-Architect",
        "Solution Architect",
        "Architect, Paris",
        "Paris 75001",
        "C++",
        "R&D",
        "R D",
        "ＧｅｎＡＩ Solution Architect",  # full width letters fold
        "IA (H/F) GenAI",
        "Ingénieur IA H F",
    ],
)
def test_titles_whose_words_are_on_the_page_are_accepted(title):
    assert finder.title_on(SHOWN, title) is not None


@pytest.mark.parametrize(
    "title",
    [
        "Ingénieur IAX",  # a longer word
        "Ingénieurs IA",  # plural
        "GenAI Solutions Architect",
        "Architect Solution GenAI",  # word order
        "GenAI Architect",  # a word missing in between
        "Solution Architecte",
        "Paris 7500",  # a number inside a longer one
        "Paris 750011",
        "75001 Paris",
        "Genai Engineer",
        "Lyon",
        "Ingénieur ML",
        "",
        " ",
        "(H/F)",  # the marker alone has words not on the page as a whole title
        "R\u043ebot",  # a Cyrillic o
        "Data Scientist (H/F)",
        "GenAI Solution Architect Lead",
        "Senior GenAI Solution Architect",
        "Ingénieur IA (H/F) Senior",
    ],
)
def test_titles_whose_words_are_not_on_a_page_without_primary_heading_are_refused(title):
    assert finder.title_on(SHOWN, title) is None


def test_the_marker_is_set_aside_only_at_the_end_and_after_a_separator():
    assert finder.GENDER_MARK.sub("", "AI Engineer (H/F)") == "AI Engineer"
    assert finder.GENDER_MARK.sub("", "AI Engineer(m/w/d)") == "AI Engineer"
    assert finder.GENDER_MARK.sub("", "AI Engineer, F/H") == "AI Engineer"
    assert finder.GENDER_MARK.sub("", "Engineer 3D/X") == "Engineer 3D/X"  # inside a word
    assert finder.GENDER_MARK.sub("", "H/F Engineer") == "H/F Engineer"  # not at the end
    assert finder.GENDER_MARK.sub("", "Engineer (A/B)") == "Engineer (A/B)"  # not a marker


def test_a_page_read_holds_no_link_or_button_text():
    """A link or a button is never the visible text a title is verified on (an agent reporting
    a listed title for the page it is on). A heading that wraps a link keeps its own name: it
    is a heading of the page (declared open point 1), after the primary heading."""
    root = sources.parse_snapshot(
        page(
            '- heading "Data Engineer" [level=1]',
            '- link "AI Engineer Consultant" [ref=e3]:',
            "  - /url: /offres/246285",
            '- button "Robot Engineer" [ref=e4]',
        )
    )
    lines, headings = finder._seen_text(root)
    assert (lines, headings) == (["Data Engineer"], ["Data Engineer"])
    wrapped = read(
        '- heading "Data Analyst" [level=1]',
        "- complementary:",
        '  - heading "Senior GenAI Engineer" [level=3]:',
        '    - link "Senior GenAI Engineer":',
        "      - /url: /offres/9",
    )
    assert wrapped.headings == ("Data Analyst", "Senior GenAI Engineer")
    assert finder.title_on(wrapped, "Senior GenAI Engineer") == ("Senior GenAI Engineer", "heading")
    assert finder.title_on(wrapped, "Analyst") == ("Data Analyst", "heading")


# --- review of B21, round 1 -------------------------------------------------------------------
# A shortened title never gets past the unwanted titles or a never apply rule: the title
# stored is the page's own, never shorter than what the page says (`finder.title_on`).


@pytest.mark.parametrize(
    ("lines", "title", "stored"),
    [
        (
            ['- heading "Stage - Data Scientist GenAI (F/H)" [level=1]'],
            "Data Scientist GenAI",
            ("Stage - Data Scientist GenAI (F/H)", "heading"),
        ),
        (
            ['- heading "Cybersecurity: AI Engineer" [level=1]'],
            "AI Engineer",
            ("Cybersecurity: AI Engineer", "heading"),
        ),
        (
            ['- heading "Cybersecurity: AI Engineer" [level=1]'],
            "AI Engineer (H/F)",  # the marker set aside, then held by the heading
            ("Cybersecurity: AI Engineer", "heading"),
        ),
        (
            ['- heading "Senior Robot Engineer" [level=1]'],
            "robot ENGINEER",
            ("Senior Robot Engineer", "heading"),
        ),
        (  # the primary heading first, before a listed posting's heading that wraps a link
            [
                "- main:",
                '  - heading "Cybersecurity: AI Engineer" [level=1]',
                "- complementary:",
                '  - heading "AI Engineer" [level=3]:',
                '    - link "AI Engineer":',
                "      - /url: /offres/9",
            ],
            "AI Engineer",
            ("Cybersecurity: AI Engineer", "heading"),
        ),
        (  # no primary heading: the first heading holding it, in page order
            [
                '- heading "Offres" [level=1]',
                '- heading "Stage: Data Scientist" [level=1]',
                '- heading "Data Scientist (Paris)" [level=2]',
            ],
            "Data Scientist",
            ("Stage: Data Scientist", "heading"),
        ),
        (  # no primary heading: a heading equal to it before one holding it
            [
                '- heading "Offres" [level=1]',
                '- heading "Senior AI Engineer Lead" [level=1]',
                '- heading "AI Engineer" [level=2]',
            ],
            "AI Engineer",
            ("AI Engineer", "heading"),
        ),
        (
            ["- generic: Cybersecurity: AI Engineer", "- paragraph: Build agents."],
            "AI Engineer",
            ("Cybersecurity: AI Engineer", "page_text"),
        ),
        (  # the page's own case and marker, not the agent's
            ["- generic: AI Engineer Consultant (H/F)", "- paragraph: Sydney."],
            "ai engineer consultant",
            ("AI Engineer Consultant (H/F)", "page_text"),
        ),
        (
            ["- generic: AI Engineer Consultant", "- paragraph: Sydney."],
            "AI ENGINEER CONSULTANT (H/F)",
            ("AI Engineer Consultant", "page_text"),
        ),
        (  # a title over two lines of the page
            ["- generic: AI Engineer", "- generic: Consultant (H/F)", "- paragraph: Sydney."],
            "AI Engineer Consultant",
            ("AI Engineer Consultant (H/F)", "page_text"),
        ),
        (  # a line without words inside it is kept as shown
            ["- generic: Stage", "- generic: ·", "- generic: Data Scientist"],
            "Stage Data Scientist",
            ("Stage · Data Scientist", "page_text"),
        ),
        (  # the smallest run of lines that holds it
            ["- paragraph: We hire an AI Engineer for our Sydney team.", "- generic: AI Engineer"],
            "AI Engineer",
            ("AI Engineer", "page_text"),
        ),
        (
            ["- paragraph: We hire a Cybersecurity AI Engineer for our Sydney team."],
            "AI Engineer",
            ("We hire a Cybersecurity AI Engineer for our Sydney team.", "page_text"),
        ),
        (  # only a line longer than a title holds it, and no primary heading: refused
            ["- paragraph: " + "Cybersecurity " * 25 + "AI Engineer"],
            "AI Engineer",
            None,
        ),
    ],
)
def test_a_shortened_title_stores_what_the_page_shows(lines, title, stored):
    assert finder.title_on(read(*lines), title) == stored


def test_a_shortened_title_reaches_the_persons_never_apply_rule_with_the_page_s_heading():
    from app.jobs.never_apply import NeverApplyRule, matching_rule

    rule = NeverApplyRule.parse("No cybersecurity roles: cyber, security, pentest, threat")
    one, batches = opened(
        (JOB, page('- heading "Cybersecurity: AI Engineer" [level=1]', "- paragraph: x")),
    )
    assert reported(one, title="AI Engineer")["kept"] == 1
    [finding] = batches[0].findings
    assert finding.title == "Cybersecurity: AI Engineer"
    # What the store applies to every verified posting, with the person's rules (commit_verified).
    assert matching_rule([rule], finding.company or "", finding.title) is rule


@pytest.mark.parametrize(
    "text",
    [
        "AI Engineer (H/F)",
        "AI Engineer(m/w/d)",
        "AI Engineer, F/H",
        "AI Engineer - h/f",
        "AI Engineer [f / m / x]",
        "Engineer 3D/X",
        "H/F Engineer",
        "Engineer (A/B)",
        "(H/F)",
        "AI Engineer" + " -" * 40 + " (H/F)",  # a separator run longer than the tail
        "AI Engineer" + " -" * 40 + " x",
        "x" * 1000 + " (H/F)",
        "x" * 1000,
    ],
)
def test_the_marker_rule_reads_a_bounded_tail_and_sets_aside_what_the_whole_rule_does(text):
    assert finder._words(finder._bare(text)) == finder._words(finder.GENDER_MARK.sub("", text))


# A heading of a separator run with no marker after it: the marker rule's worst case.
HOSTILE = "a" + " -" * 139 + " x"


def test_a_hostile_page_of_the_largest_size_costs_a_report_what_a_plain_page_does(monkeypatch):
    """128 such headings at the snapshot's size, and one report of POSTINGS_MAX refused
    postings with that page's address: the work counted (the folds of `_words`, the calls of the
    marker rule and what it reads) is done once per page read, and a report folds its own titles
    only, whatever the page's headings."""
    from collections import Counter

    hostile = page(*[f'- heading "{HOSTILE}" [level=2]'] * 128)
    assert finder.SNAPSHOT_MAX - 2000 < len(hostile) <= finder.SNAPSHOT_MAX
    plain = page('- heading "Data Engineer" [level=2]', '- heading "Offres" [level=2]')
    titles = [f"Invented Role {index} (H/F)" for index in range(finder.POSTINGS_MAX)]
    items = [{"title": title, "url": JOB, "text": "x"} for title in titles]
    work: Counter[str] = Counter()
    words, mark = finder._words, finder.GENDER_MARK

    def folded(text):
        work["folds"] += 1
        work["folded"] += len(text)
        return words(text)

    class Marks:
        def search(self, text, pos=0):
            work["marks"] += 1
            work["mark_read"] = max(work["mark_read"], len(text) - pos)
            return mark.search(text, pos)

        def sub(self, repl, text, count=0):
            work["marks"] += 1
            work["mark_read"] = max(work["mark_read"], len(text))
            return mark.sub(repl, text, count)

    monkeypatch.setattr(finder, "_words", folded)
    monkeypatch.setattr(finder, "GENDER_MARK", Marks())

    def report_on(snapshot):
        work.clear()
        one, batches = opened((JOB, snapshot))
        read_work = dict(work)
        work.clear()
        answer, _ = asyncio.run(one.report_verified(items))
        assert json.loads(answer)["kept"] == 0
        assert one.refused == {"title_not_on_page": finder.POSTINGS_MAX}
        assert not batches
        return read_work, dict(work)

    read_hostile, report_hostile = report_on(hostile)
    _, report_plain = report_on(plain)
    assert report_hostile == report_plain  # nothing per heading
    assert report_hostile["folds"] <= 3 * len(items)
    assert report_hostile["folded"] <= 3 * sum(len(title) for title in titles)
    assert report_hostile["marks"] <= 2 * len(items)
    assert report_hostile["mark_read"] <= finder.MARK_TAIL
    # The page read folds each line and heading once.
    assert read_hostile["folded"] <= 3 * len(hostile)
    assert read_hostile["marks"] <= 128
    assert read_hostile["mark_read"] <= finder.MARK_TAIL


def test_a_text_s_line_breaks_and_tabs_are_no_control():
    one, _ = opened((JOB, OFFER))
    text = "Line one\nLine two\r\n\tIndented"
    assert reported(one, title="AI Engineer Consultant", text=text)["kept"] == 1


ADVICE = finder.REPORT_ADVICE["missing_field"]


@pytest.mark.parametrize(
    "item",
    [
        {"title": "AI Engineer Consultant\x00"},
        {"title": "AI Engineer\ud800 Consultant"},
        {"text": "Sydney\x00"},
        {"location": "Sydney\x1b"},
        {"url": JOB + "\x00"},
    ],
)
def test_the_path_without_commits_refuses_controls_too(item):
    """`report` (`_keep`), the path of a website's search without the sweep's commits."""
    one, _ = opened((JOB, OFFER))
    one.on_verified = None
    posting = {"title": "AI Engineer Consultant", "url": JOB, "text": "x", **item}
    clean = {
        k: v.replace("\x00", "").replace("\ud800", "").replace("\x1b", "")
        for k, v in posting.items()
    }
    answer, _ = asyncio.run(one.report_verified([posting, clean]))
    assert json.loads(answer)["refused"] == [{"index": 0, "reason": "missing_field", "do": ADVICE}]
    assert json.loads(answer)["kept"] == 1  # the clean one: one bad item fails alone


# An HTTP error page (the journeys' 404: Python http.server's own page, one h1 "Error response")
# is never a posting.
GONE = BASE + "j15/jobs/robot-engineer.html"
ERROR_PAGE = page(
    '- heading "Error response" [level=1]',
    "- paragraph: Error code: 404",
    "- paragraph: Message: File not found.",
)
ROBOT_PAGE = page('- heading "Robot Engineer" [level=1]', "- paragraph: Build robots.")


class StatusTools(InteractiveTools):
    """browserd's navigate answers GONE with an HTTP status, as browserd's `pages.navigate`
    reports it (`status`), or with none (a page reached another way)."""

    def __init__(self, status: int | None, snapshot: str) -> None:
        super().__init__()
        self.pages[GONE] = snapshot
        self.status = status

    async def navigate(self, url):
        answer = await super().navigate(url)
        if url == GONE and answer.get("ok") and self.status is not None:
            answer = {**answer, "status": self.status}
        return answer


def gone(status: int | None, snapshot: str = ROBOT_PAGE, *, commits: bool = True):
    batches: list = []

    async def commit(batch):
        batches.append(batch)

    tools = StatusTools(status, snapshot)
    one = session(tools, "e2e-person")
    one.on_verified = commit if commits else None
    assert not asyncio.run(one.navigate(GONE))[1]
    return one, tools, batches


def reported_gone(one, title: str = "Robot Engineer") -> dict:
    answer, error = asyncio.run(
        one.report_verified([{"url": GONE, "title": title, "text": "Invented model text"}])
    )
    assert not error
    return json.loads(answer)


@pytest.mark.parametrize("status", [400, 403, 404, 410, 429, 500, 503])
@pytest.mark.parametrize("commits", [True, False])
def test_a_page_answered_with_an_error_status_is_no_posting(status, commits):
    one, _, batches = gone(status, commits=commits)  # its title is on it, yet refused
    answer = reported_gone(one)
    assert answer["kept"] == 0
    [refusal] = answer["refused"]
    assert refusal["reason"] == "page_error"
    assert "loaded" in refusal["do"]
    assert not batches
    assert not one.found
    assert GONE not in one.pages_read  # never a posting's page
    assert GONE in one.seen  # its links are still read


@pytest.mark.parametrize("status", [None, 200, 203, 304, 399])
def test_a_page_answered_without_an_error_status_keeps_the_title_rule(status):
    one, _, batches = gone(status)
    assert reported_gone(one)["kept"] == 1
    assert batches[0].findings[0].title == "Robot Engineer"


def test_an_error_page_read_again_stays_no_posting_until_it_loads():
    one, tools, batches = gone(404)
    assert not asyncio.run(one.snapshot())[1]  # read again: still the error page
    assert reported_gone(one)["refused"][0]["reason"] == "page_error"
    assert not batches
    tools.status = 200
    assert not asyncio.run(one.navigate(GONE))[1]  # it loads now
    assert reported_gone(one)["kept"] == 1
    assert batches[0].findings[0].title == "Robot Engineer"
    assert not one.error_pages


def test_the_error_pages_noted_are_bounded_oldest_first():
    one, _, _ = gone(404)
    for index in range(finder.OPENED_MAX):
        one.error_pages[BASE + f"gone/{index}"] = None
        assert not asyncio.run(one.navigate(GONE))[1]  # the newest again
    assert len(one.error_pages) == finder.OPENED_MAX
    assert next(reversed(one.error_pages)) == GONE
    assert BASE + "gone/0" not in one.error_pages  # the oldest went first


# The page's one primary heading stands for a title that is not on the page only when it shares
# a meaningful word with it: an error page answered without a status, a "not found" page or any
# other page's heading never becomes a posting's title it has nothing in common with.


@pytest.mark.parametrize(
    ("lines", "title", "stored"),
    [
        (ERROR_PAGE.splitlines(), "Robot Engineer", None),
        (['- heading "Page not found" [level=1]'], "Data Engineer", None),
        (['- heading "Offre introuvable" [level=1]'], "Ingénieur Données", None),
        (['- heading "Offre n° 246286" [level=1]'], "AI Engineer Consultant", None),
        (['- heading "Welcome (H/F)" [level=1]'], "Robot Engineer (H/F)", None),  # a marker
        (['- heading "The Team of the Year" [level=1]'], "Head of the Lab", None),  # stop words
        (['- heading "Offres de la semaine" [level=1]'], "Chef de projet", None),
        (['- heading "Data Engineer" [level=1]'], "Invented role", None),
        (
            ['- heading "Data Engineer" [level=1]'],
            "GenAI Forward Deployed Engineer",
            "Data Engineer",
        ),
        (['- heading "Ingénieur Données" [level=1]'], "Data INGENIEUR", "Ingénieur Données"),
        (['- heading "Robot Engineering" [level=1]'], "Robot Engineer", "Robot Engineering"),
        (['- heading "Offre n° 246286" [level=1]'], "Offre 246286 VIE", "Offre n° 246286"),
    ],
)
def test_the_primary_heading_stands_for_a_title_only_when_they_share_a_word(lines, title, stored):
    found = finder.title_on(read(*lines), title)
    assert found == (None if stored is None else (stored, "primary_heading"))


def test_an_error_page_answered_without_a_status_is_refused_by_its_heading():
    one, _, batches = gone(None, ERROR_PAGE)
    assert reported_gone(one)["refused"][0]["reason"] == "title_not_on_page"
    assert not batches
