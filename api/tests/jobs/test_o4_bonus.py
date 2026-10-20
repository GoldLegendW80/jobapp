"""The score's bonus of opportunities O4 (owner, 2026-10-20): seniority, industries and company
types add a capped bonus to the fit and never filter. The pure rule as accept and refuse tables,
then the rescore through the API: the bonus applied, a manual fit kept, a posting that matches
nothing still listed, and one person's profile never scoring another's postings."""

import time
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import scoring
from app.jobs.models import Job
from app.opportunities import deck as decks
from app.profile.models import Profile
from app.profile.schemas import COMPANY_TYPES, SENIORITIES, SearchSection
from tests.identity.api_helpers import Api
from tests.jobs.conftest import NOW
from tests.jobs.test_api import Signed, seed, sign_in
from tests.jobs.test_scoring import skills as make_skills

SKILLS = make_skills()
POSTING = "About the role\n\nBuild an agent with tool use in Python. Some RAG.\n"


def search(**fields: Any) -> SearchSection:
    data: dict[str, Any] = {}
    if isinstance(fields.get("industries"), list):
        data["industries"] = {"terms": fields.pop("industries"), "advanced_regex": False}
    data.update(fields)
    return SearchSection.model_validate(data)


def levels(title: str, *wanted: str) -> tuple[str, ...]:
    return scoring.bonus("", title, search(seniority=list(wanted or SENIORITIES))).seniority


# --- seniority, in the title -------------------------------------------------------------------

SENIORITY_ACCEPT = [
    ("Senior AI Engineer", "senior"),
    ("senior ai engineer", "senior"),
    ("SENIOR AI ENGINEER", "senior"),
    ("Ingénieur IA Sénior", "senior"),
    ("Ingénieur IA Sénior", "senior"),  # decomposed accent, NFKC composes it
    ("Sr. Backend Engineer", "senior"),
    ("Sr Backend Engineer", "senior"),
    ("Senior-level Data Scientist", "senior"),
    ("Engineer (Senior)", "senior"),
    ("Engineer, Senior", "senior"),
    ("\uff33\uff45\uff4e\uff49\uff4f\uff52 Engineer", "senior"),  # full width letters
    ("Junior Developer", "junior"),
    ("Jr. Developer", "junior"),
    ("Entry-level Analyst", "junior"),
    ("Entry Level Analyst", "junior"),
    ("Développeur débutant", "junior"),
    ("Graduate Software Engineer", "junior"),
    ("Mid-level Backend Engineer", "mid-level"),
    ("Mid‑level Backend Engineer", "mid-level"),  # non breaking hyphen
    ("Développeur confirmé", "mid-level"),
    ("Intermediate Engineer", "mid-level"),
    ("Machine Learning Intern", "intern"),
    ("Stage: Data Engineer", "intern"),
    ("Stagiaire développeur", "intern"),
    ("Alternance Data Analyst", "intern"),
    ("Apprenti ingénieur", "intern"),
    ("Internship, AI", "intern"),
    ("Tech Lead, Platform", "lead"),
    ("Lead Engineer", "lead"),
    ("Team Leader IA", "lead"),
    ("Principal Engineer", "principal"),
    ("Staff Software Engineer", "principal"),
    ("Staff Machine Learning Engineer", "principal"),
    ("Staff Engineer", "principal"),
    ("Distinguished Engineer", "principal"),
]


@pytest.mark.parametrize(("title", "level"), SENIORITY_ACCEPT)
def test_a_seniority_word_of_the_title_is_read(title: str, level: str) -> None:
    assert level in levels(title)


SENIORITY_REFUSE = [
    "Seniority Program Manager",  # a word inside another word
    "Seniorité",
    "Unsenior Engineer",
    "Juniority",
    "Mid-Market Account Executive",  # "mid" alone means something else
    "Midfield Analyst",
    "Backstage Engineer",
    "Internal Tools Engineer",
    "International Sales",
    "Interne",
    "Leadership Coach",
    "Mislead Detector",
    "Lead Generation Specialist",
    "Staffing Coordinator",
    "Staff Accountant",  # staff without an engineering noun
    "Principality Office",
    "Software Engineer",
    "AI Engineer",
    "",
    "Srinivasan Role",
    "SRE",
]


@pytest.mark.parametrize("title", SENIORITY_REFUSE)
def test_a_near_miss_reads_no_level(title: str) -> None:
    assert levels(title) == ()


def test_seniority_is_read_in_the_title_only() -> None:
    wanted = search(seniority=["senior"])
    body = "You will work with senior engineers and a principal scientist."
    assert scoring.bonus(body, "AI Engineer", wanted) == scoring.NO_BONUS


def test_a_title_matching_two_levels_earns_one_part() -> None:
    result = scoring.bonus("", "Senior Lead Engineer", search(seniority=["senior", "lead"]))
    assert result.seniority == ("senior", "lead")
    assert result.points == 3


def test_a_level_the_person_did_not_choose_earns_nothing() -> None:
    assert scoring.bonus("", "Senior Engineer", search(seniority=["junior"])).points == 0


# --- company types, in the title and the text -------------------------------------------------

COMPANY_ACCEPT = [
    ("We are a fast-growing startup in Paris.", "startup"),
    ("We are a fast-growing start-up in Paris.", "startup"),
    ("We are a fast-growing start‑up.", "startup"),
    ("An early-stage company.", "startup"),
    ("We raised our Series A last spring.", "startup"),
    ("Une jeune pousse bordelaise.", "startup"),
    ("A scale-up of 200 people.", "scaleup"),
    ("Une scaleup en hypercroissance.", "scaleup"),
    ("We are in hypergrowth.", "scaleup"),
    ("A multinational with offices everywhere.", "large_company"),
    ("A Fortune 500 company.", "large_company"),
    ("Un grand groupe du CAC 40.", "large_company"),
    ("Join the public sector.", "public_sector"),
    ("Rejoignez la fonction publique.", "public_sector"),
    ("Au sein du ministère des Armées.", "public_sector"),
    ("A nonprofit for literacy.", "nonprofit"),
    ("A not-for-profit organisation.", "nonprofit"),
    ("Une ONG internationale.", "nonprofit"),
    ("Une association à but non lucratif.", "nonprofit"),
    ("A registered charity.", "nonprofit"),
    ("A digital agency in Lyon.", "agency"),
    ("Une agence de communication.", "agency"),
    ("Une agence web.", "agency"),
    ("A consulting firm.", "consulting"),
    ("A boutique consultancy.", "consulting"),
    ("Un cabinet de conseil en stratégie.", "consulting"),
    ("Une ESN de 500 personnes.", "consulting"),
    ("Une SSII historique.", "consulting"),
]


@pytest.mark.parametrize(("text", "kind"), COMPANY_ACCEPT)
def test_a_company_type_of_the_text_is_read(text: str, kind: str) -> None:
    result = scoring.bonus(text, "Engineer", search(company_types=[kind]))
    assert result.company_types == (scoring.COMPANY_TYPE_LABELS[kind],)


COMPANY_REFUSE = [
    "You have high agency and ownership.",  # agency alone is a trait
    "Agency is our core value.",
    "Le conseil d'administration valide.",  # conseil alone
    "We organise charity days.",
    "We sell to large enterprises.",  # a customer, not the employer
    "Startupper mindset.",
    "Our upstart team.",
    "Scale up our infrastructure.",  # the verb, spaced
    "Governmental relations are out of scope.",
    "Multinationality matters.",
    "Consulting the docs is encouraged.",
    "Nongovernmental work.",
    "Song writers welcome.",
    "Esnault is our CTO.",
    "A series about AI.",
    "",
]


@pytest.mark.parametrize("text", COMPANY_REFUSE)
def test_a_near_miss_reads_no_company_type(text: str) -> None:
    assert scoring.bonus(text, "Engineer", search(company_types=list(COMPANY_TYPES))).points == 0


def test_the_title_counts_for_a_company_type() -> None:
    result = scoring.bonus("", "Engineer, Consulting Firm", search(company_types=["consulting"]))
    assert result.company_types == ("consulting",)


# --- industries: the person's own terms ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "terms", "expected"),
    [
        ("We build fintech products.", ["fintech"], ("fintech",)),
        ("We build FinTech products.", ["fintech"], ("fintech",)),
        ("Leader in online gaming.", ["gaming", "fintech"], ("gaming",)),
        ("For gaming and fintech alike.", ["fintech", "gaming"], ("gaming", "fintech")),
        ("Two banks, one platform.", ["bank"], ("bank",)),  # plural as every term list
        ("Santé numérique.", ["santé"], ("santé",)),
        ("We serve hospitals.", ["health"], ()),
        ("Healthcare software.", ["health"], ()),  # inside a longer word
        ("Insurtech.", ["insurance"], ()),
        ("", ["fintech"], ()),
    ],
)
def test_industries_are_whole_word_terms(
    text: str, terms: list[str], expected: tuple[str, ...]
) -> None:
    assert scoring.bonus(text, "Engineer", search(industries=terms)).industries == expected


def test_industries_name_at_most_three() -> None:
    wanted = search(industries=["a1", "b2", "c3", "d4"])
    assert scoring.bonus("a1 b2 c3 d4", "x", wanted).industries == ("a1", "b2", "c3")


def test_an_advanced_industry_term_shows_what_the_text_has() -> None:
    wanted = search(industries={"terms": [r"fin\w*"], "advanced_regex": True})
    assert scoring.bonus("Our finance team.", "x", wanted).industries == ("finance",)


# --- the points and the cap --------------------------------------------------------------------


def test_no_field_set_means_no_bonus() -> None:
    assert scoring.bonus("A senior startup in fintech.", "Senior", search()) == scoring.NO_BONUS


@pytest.mark.parametrize(
    ("title", "text", "points"),
    [
        ("Engineer", "Nothing here.", 0),
        ("Senior Engineer", "Nothing here.", 3),
        ("Senior Engineer", "A fintech.", 7),
        ("Senior Engineer", "A fintech startup.", 10),
        ("Engineer", "A fintech startup.", 7),
    ],
)
def test_each_part_is_a_third_of_the_cap(title: str, text: str, points: int) -> None:
    wanted = search(seniority=["senior"], industries=["fintech"], company_types=["startup"])
    assert scoring.bonus(text, title, wanted).points == points


def test_the_fit_rises_by_the_bonus_and_never_above_100() -> None:
    wanted = search(seniority=["senior"], industries=["fintech"], company_types=["startup"])
    text = POSTING + "A fintech startup.\n"
    base = scoring.score(text, SKILLS, title="Senior Engineer")
    raised = scoring.score(text, SKILLS, title="Senior Engineer", search=wanted)
    assert raised.fit == min(100, base.fit + scoring.BONUS_CAP)
    assert raised.bonus.points == scoring.BONUS_CAP
    # The competencies and gaps the card shows never change with the bonus.
    assert (raised.competencies, raised.gaps) == (base.competencies, base.gaps)
    top = make_skills(adjacent=[], none=[], years_experience=None)
    assert scoring.score(text, top, title="Senior Engineer").fit == 100
    assert scoring.score(text, top, title="Senior Engineer", search=wanted).fit == 100


def test_a_gated_posting_below_zero_stays_at_zero() -> None:
    wanted = search(seniority=["senior"])
    text = "Python agent. PhD is required. Fluent German. PhD required.\n"
    harsh = make_skills(gates=[{"name": "phd", "terms": ["phd"], "penalty": 100}])
    assert scoring.score(text, harsh, title="Senior Engineer", search=wanted).fit == 0


def test_a_posting_without_text_reads_the_title() -> None:
    result = scoring.bonus("", "Senior Engineer", search(seniority=["senior"]))
    assert result.points == 3


def test_without_search_the_score_is_the_engines() -> None:
    assert scoring.score(POSTING, SKILLS, title="Senior Engineer").bonus == scoring.NO_BONUS


def test_the_bonus_is_linear_on_a_huge_text() -> None:
    wanted = search(
        seniority=list(SENIORITIES),
        industries=[f"sector{i}" for i in range(30)],
        company_types=list(COMPANY_TYPES),
    )
    huge = "staff lead agency conseil series " * 40_000  # over 1 MB, cut to SCORED_CHARS_MAX
    started = time.process_time()
    scoring.bonus(huge, "Staff " * 5_000, wanted)
    # A loose bound against a quadratic or backtracking rule, not a timing claim.
    assert time.process_time() - started < 5


# --- the match reason ---------------------------------------------------------------------------


def test_the_match_reason_names_what_earned_the_bonus() -> None:
    reason = decks.match_reason(["agents", "rag"], ["rag"], ("senior", "fintech", "startup"))
    assert reason.text == "Matches: agents. Gap: rag. Also fits: senior, fintech, startup."
    assert reason.also == ("senior", "fintech", "startup")
    assert decks.match_reason(["agents"], []).text == "Matches: agents."


# --- the rescore through the API ---------------------------------------------------------------


def set_sections(factory: sessionmaker[Session], user: uuid.UUID, **sections: Any) -> None:
    with factory() as db, db.begin():
        db.merge(Profile(user_id=user, sections=sections, revision=1, updated_at=NOW))


def fits(factory: sessionmaker[Session], user: uuid.UUID) -> dict[str, int | None]:
    with factory() as db:
        rows = db.execute(sa.select(Job.title, Job.fit).where(Job.user_id == user)).all()
    return {title: fit for title, fit in rows}


def test_a_rescore_applies_the_bonus_keeps_manual_fits_and_isolates_people(
    factory: sessionmaker[Session], api: Api
) -> None:
    alice: Signed = sign_in(factory, api, "e2e-o4-alice@example.org")
    bob: Signed = sign_in(factory, api, "e2e-o4-bob@example.org")
    wanted = search(seniority=["senior"], industries=["fintech"], company_types=["startup"])
    skills = SKILLS.model_dump(mode="json")
    set_sections(factory, alice.id, skills=skills, search=wanted.model_dump(mode="json"))
    set_sections(factory, bob.id, skills=skills)
    text = POSTING + "A fintech startup.\n"
    rows = [
        {"title": "Senior Engineer", "posting_text": text},
        {"title": "Engineer", "posting_text": POSTING},  # matches nothing of the three
        {"title": "Senior Manual", "posting_text": text, "fit": 42, "fit_overridden": True},
    ]
    seed(factory, alice.id, rows)
    seed(factory, bob.id, rows[:2])
    response = api.request("POST", "/api/jobs/rescore", token=alice.token, json={})
    assert response.status_code == 200, response.text
    assert response.json()["skipped_manual"] == 1
    base = scoring.score(text, SKILLS, title="Senior Engineer").fit
    plain = scoring.score(POSTING, SKILLS, title="Engineer").fit
    assert fits(factory, alice.id) == {
        "Senior Engineer": min(100, base + 10),
        "Engineer": plain,  # still there, without a bonus
        "Senior Manual": 42,
    }
    response = api.request("POST", "/api/jobs/rescore", token=bob.token, json={})
    assert response.status_code == 200, response.text
    # Bob has no search section: his twin posting gets no bonus from Alice's.
    assert fits(factory, bob.id) == {"Senior Engineer": base, "Engineer": plain}
