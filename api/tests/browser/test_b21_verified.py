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

SHOWN = finder.PageRead(
    "text",
    f" {finder._words('Ingénieur IA (H/F) GenAI Solution Architect, Paris 75001 C++ & R&D')} ",
    ("Ingénieur IA (H/F)",),
    "",
    {},
    "",
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
        "Rоbot",  # Cyrillic о
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
    """The titles of other postings a page lists are links: never the visible text a title is
    verified on (an agent reporting a listed title for the page it is on)."""
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
