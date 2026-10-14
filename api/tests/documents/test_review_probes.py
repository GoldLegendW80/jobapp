"""The probes of the first review of D2, as named table cases, both ways round.

Everything is invented: "Casey Sample" is an "Ingénieure données" at "Exemple SA" since 2022-12
and was a "Data Analyst" at "Sample Labs" from 2020-09 to 2022-11, with an "MSc in Data Science"
from "Exemple University" and the "AWS Certified Solutions Architect Associate (2023)"; she writes
for "Brume Example", which wants a "Senior Data Engineer".

- `TRUE`: a true fact in one field of the base CV (or of a letter) is kept by the checks and the
  correction, at most with an unstated version number stripped from a true skill.
- `FALSE`: an invented claim in one field is gone after the correction.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.documents import checks, corrections, rules
from app.documents import content as c
from app.documents.settings import DocumentSettings
from app.documents.sources import sources_for
from tests.documents.conftest import base_cv, load, posting, view_of

OFF = DocumentSettings()

LETTER: dict[str, Any] = {
    "schema": 1,
    "headline": "Ingénieure données",
    "recipient": "Hiring team, Brume Example",
    "subject": "Application",
    "salutation": "Dear hiring team,",
    "paragraphs": ["I build data platforms in Python and SQL."],
    "closing": "Kind regards",
}


def _document(kind: str, path: str, text: str) -> dict[str, Any]:
    if kind == "letter":
        data = copy.deepcopy(LETTER)
    else:
        data = copy.deepcopy(load("base_cv_casey.json"))
        del data["fixed"]
    *head, last = [int(p) if p.isdigit() else p for p in path.split(".")]
    node: Any = data
    for part in head:
        node = node[part]
    if isinstance(node, list) and last == len(node):
        node.append(text)
    else:
        node[last] = text
    return data


def _correct(kind: str, path: str, text: str) -> tuple[c.CvContent | c.LetterContent, list]:
    base = base_cv("casey")
    view = view_of("casey", base)
    brume = posting("brume")
    sources = sources_for(view, base, None, brume, kind=kind)  # type: ignore[arg-type]
    result = checks.run(kind, _document(kind, path, text), sources, OFF)  # type: ignore[arg-type]
    assert result.content is not None
    fallbacks = corrections.fallbacks_for(view, base, brume)
    return corrections.remove_remaining(result.content, result, fallbacks)


def _field(content: c.CvContent | c.LetterContent, path: str) -> str:
    return dict(c.text_fields(content)).get(path, "")


# (name, kind, field, text, the text the field holds after the correction)
TRUE: list[tuple[str, str, str, str, str]] = [
    ("skills_item_python_version", "cv", "skills.0.items.0", "Python 3.12", "Python"),
    ("skills_item_postgres_version", "cv", "skills.0.items.1", "PostgreSQL 16", "PostgreSQL"),
    (
        "bullet_with_versions",
        "cv",
        "experience.0.bullets.0",
        "Wrote the pipelines in Python 3.12 on PostgreSQL 16.",
        "Wrote the pipelines in Python on PostgreSQL.",
    ),
    ("version_stated_by_profile", "cv", "skills.0.items.0", "Python 3.11", "Python 3.11"),
    ("product_year_version", "cv", "skills.0.items.2", "Excel 2019", "Excel"),
    ("v_version", "cv", "skills.0.items.2", "Airflow v2", "Airflow"),
    ("competency_version", "cv", "competencies.0", "Python 3.12", "Python"),
    (
        "years_en",
        "cv",
        "summary",
        "Data engineer with about 3.5 years of professional experience.",
        "Data engineer with about 3.5 years of professional experience.",
    ),
    (
        "years_fr",
        "cv",
        "summary",
        "Ingénieure données, 3,5 ans d'expérience professionnelle.",
        "Ingénieure données, 3,5 ans d'expérience professionnelle.",
    ),
    (
        "date_words_en",
        "cv",
        "experience.0.bullets.0",
        "Ingénieure données at Exemple SA since December 2022.",
        "Ingénieure données at Exemple SA since December 2022.",
    ),
    (
        "date_words_fr",
        "cv",
        "experience.0.bullets.0",
        "Ingénieure données chez Exemple SA depuis décembre 2022.",
        "Ingénieure données chez Exemple SA depuis décembre 2022.",
    ),
    (
        "date_abbreviated",
        "cv",
        "experience.0.bullets.0",
        "At Exemple SA since Dec 2022.",
        "At Exemple SA since Dec 2022.",
    ),
    (
        "date_numeric_slash",
        "cv",
        "experience.0.bullets.0",
        "At Exemple SA since 12/2022.",
        "At Exemple SA since 12/2022.",
    ),
    (
        "date_numeric_iso",
        "cv",
        "experience.0.bullets.0",
        "At Exemple SA since 2022-12.",
        "At Exemple SA since 2022-12.",
    ),
    (
        "date_numeric_range",
        "cv",
        "experience.1.bullets.0",
        "Data Analyst at Sample Labs from 09/2020 to 11/2022.",
        "Data Analyst at Sample Labs from 09/2020 to 11/2022.",
    ),
    (
        "year_range",
        "cv",
        "experience.1.bullets.0",
        "Data Analyst at Sample Labs from 2020 to 2022.",
        "Data Analyst at Sample Labs from 2020 to 2022.",
    ),
    (
        "duration_of_a_period",
        "cv",
        "experience.1.bullets.0",
        "2 years at Sample Labs building dashboards in SQL.",
        "2 years at Sample Labs building dashboards in SQL.",
    ),
    (
        "certification_of_profile",
        "cv",
        "highlights.0",
        "AWS Certified Solutions Architect Associate (2023)",
        "AWS Certified Solutions Architect Associate (2023)",
    ),
    (
        "degree_and_school",
        "cv",
        "highlights.0",
        "MSc in Data Science from Exemple University.",
        "MSc in Data Science from Exemple University.",
    ),
    (
        "graduated_from_school",
        "cv",
        "highlights.0",
        "Graduated from Exemple University in 2020.",
        "Graduated from Exemple University in 2020.",
    ),
    (
        "phone",
        "letter",
        "paragraphs.1",
        "Reach me at +33 6 12 34 56 78.",
        "Reach me at +33 6 12 34 56 78.",
    ),
    (
        "website",
        "letter",
        "paragraphs.1",
        "Portfolio at https://casey.example.org.",
        "Portfolio at https://casey.example.org.",
    ),
    (
        "email",
        "letter",
        "paragraphs.1",
        "Write to e2e-casey@example.org.",
        "Write to e2e-casey@example.org.",
    ),
    ("city", "cv", "summary", "Based in Nantes, France.", "Based in Nantes, France."),
    ("identity_headline", "cv", "headline", "Ingénieure données", "Ingénieure données"),
    (
        "english_title",
        "cv",
        "experience.1.bullets.0",
        "Data Analyst at Sample Labs.",
        "Data Analyst at Sample Labs.",
    ),
    (
        "letter_names_role_and_company",
        "letter",
        "paragraphs.0",
        "I am applying for the Senior Data Engineer position at Brume Example.",
        "I am applying for the Senior Data Engineer position at Brume Example.",
    ),
    (
        "letter_notice_period",
        "letter",
        "paragraphs.1",
        "My notice period is three months.",
        "My notice period is three months.",
    ),
]


@pytest.mark.parametrize(
    ("kind", "path", "text", "after"), [t[1:] for t in TRUE], ids=[t[0] for t in TRUE]
)
def test_a_true_fact_is_kept(kind: str, path: str, text: str, after: str) -> None:
    fixed, records = _correct(kind, path, text)
    assert _field(fixed, path) == after
    if after == text:
        assert records == []
    else:
        assert [(r.before, r.after, r.code) for r in records] == [
            (text, after, corrections.VERSION_REMOVED)
        ]
        assert records[0].reason == corrections.NUMBER_NOT_STATED


# (name, kind, field, text, a piece of the claim that must be gone)
FALSE: list[tuple[str, str, str, str, str]] = [
    ("metric", "cv", "experience.0.bullets.0", "Cut the cloud costs by 30%.", "30%"),
    ("team_size", "cv", "experience.0.bullets.0", "Led a team of 12 engineers.", "12"),
    (
        "employer",
        "cv",
        "experience.0.bullets.0",
        "Worked at Nimbus Example on the data platform.",
        "Nimbus",
    ),
    ("employer_other", "cv", "highlights.0", "Consultant at Quartz Example in 2021.", "Quartz"),
    ("title_lead", "cv", "highlights.0", "Lead Data Engineer at Exemple SA.", "Lead Data"),
    ("title_head", "cv", "highlights.0", "Head of Data at Exemple SA.", "Head of"),
    ("title_staff", "cv", "highlights.0", "Staff Engineer at Exemple SA.", "Staff"),
    (
        "certification_gcp",
        "cv",
        "highlights.0",
        "Google Cloud Professional Data Engineer certification.",
        "Google Cloud",
    ),
    ("year_shifted", "cv", "experience.0.bullets.0", "At Exemple SA since December 2021.", "2021"),
    (
        "month_shifted",
        "cv",
        "experience.0.bullets.0",
        "At Exemple SA since October 2022.",
        "October",
    ),
    ("years_4", "cv", "summary", "4 years of professional experience.", "4 years"),
    ("years_5_plus", "cv", "summary", "5+ years of Python.", "5+"),
    ("years_four", "cv", "summary", "Four years of professional experience.", "Four years"),
    ("years_five", "cv", "summary", "Five years of Python.", "Five years"),
    ("years_ten", "cv", "summary", "Ten years of data engineering.", "Ten years"),
    ("count_250", "cv", "experience.0.bullets.0", "Built 250 dashboards.", "250"),
    ("count_1m", "cv", "experience.0.bullets.0", "Processed 1M rows a day.", "1M"),
    ("amount", "cv", "experience.0.bullets.0", "Saved 2 million euros.", "million"),
    ("posting_title_headline", "cv", "headline", "Senior Data Engineer", "Senior"),
    ("phd", "cv", "highlights.0", "PhD in Computer Science.", "PhD"),
    ("msc_other_school", "cv", "highlights.0", "MSc from Example Polytechnic.", "Polytechnic"),
    ("cka", "cv", "highlights.0", "Certified Kubernetes Administrator", "Kubernetes Administrator"),
    ("award", "cv", "highlights.0", "Winner of the Exemple SA hackathon award.", "award"),
    (
        "graduated_other_school",
        "cv",
        "highlights.0",
        "Graduated from Stanford University.",
        "Stanford",
    ),
    ("decade", "cv", "summary", "A decade of data engineering.", "decade"),
    (
        "doubled",
        "cv",
        "experience.0.bullets.0",
        "Doubled the throughput of the pipelines.",
        "Doubled",
    ),
    ("three_squads", "cv", "experience.0.bullets.0", "Managed three squads.", "squads"),
    # The review's further direct checks.
    (
        "posting_company_employer",
        "cv",
        "experience.0.bullets.0",
        "Worked at Brume Example on the data platform.",
        "Brume",
    ),
    (
        "posting_company_with_title",
        "cv",
        "highlights.0",
        "Data Engineer at Brume Example since 2023.",
        "Brume",
    ),
    (
        "posting_title_at_employer",
        "cv",
        "highlights.0",
        "Senior Data Engineer at Exemple SA since December 2022.",
        "Senior",
    ),
    ("mba", "cv", "highlights.0", "MBA from HEC Paris.", "MBA"),
    ("doctorate", "cv", "highlights.0", "Doctorate in physics.", "Doctorate"),
    (
        "aws_other_certification",
        "cv",
        "highlights.0",
        "AWS Certified Machine Learning Specialty.",
        "Machine Learning",
    ),
    ("by_half", "cv", "experience.0.bullets.0", "Grew revenue by half.", "half"),
    ("senior_analyst", "cv", "highlights.0", "Senior Data Analyst at Sample Labs.", "Senior"),
    ("engineering_manager", "cv", "highlights.0", "Engineering Manager at Exemple SA.", "Manager"),
    ("team_lead", "cv", "highlights.0", "Team Lead at Exemple SA.", "Team Lead"),
    ("count_5", "cv", "experience.0.bullets.0", "Built 5 pipelines.", "5 pipelines"),
    ("ans_5", "cv", "summary", "5 ans d'expérience professionnelle.", "5 ans"),
    ("ans_4", "cv", "summary", "4 ans d'expérience professionnelle.", "4 ans"),
    (
        "graduated_at_other",
        "cv",
        "highlights.0",
        "Graduated at Example Polytechnic.",
        "Polytechnic",
    ),
    ("count_8", "cv", "experience.0.bullets.0", "Built 8 dashboards.", "8 dashboards"),
    ("count_eight", "cv", "experience.0.bullets.0", "Built eight dashboards.", "eight"),
    ("count_five", "cv", "experience.0.bullets.0", "Built five dashboards.", "five"),
    ("years_two_of_rust", "cv", "summary", "Two years of Rust.", "Rust"),
    # Their neighbours.
    ("tripled", "cv", "experience.0.bullets.0", "Tripled the number of daily jobs.", "Tripled"),
    ("halved", "cv", "experience.0.bullets.0", "Halved the cost of the warehouse.", "Halved"),
    ("times_x10", "cv", "experience.0.bullets.0", "Made the queries x10 faster.", "x10"),
    ("times_2x", "cv", "experience.0.bullets.0", "Made the queries 2x faster.", "2x"),
    (
        "multiplie_par",
        "cv",
        "experience.0.bullets.0",
        "A multiplié par trois les jobs.",
        "multipli",
    ),
    ("decades", "cv", "summary", "Two decades of data engineering.", "decades"),
    ("decennie", "cv", "summary", "Une décennie de data engineering.", "cennie"),
    ("months_of_rust_from_notice", "cv", "summary", "Three months of Rust.", "Rust"),
    (
        "diplome_other_school",
        "cv",
        "highlights.0",
        "Diplômée de l'École Exemple de Paris.",
        "Paris",
    ),
]


@pytest.mark.parametrize(
    ("kind", "path", "text", "gone"), [t[1:] for t in FALSE], ids=[t[0] for t in FALSE]
)
def test_a_false_claim_is_removed(kind: str, path: str, text: str, gone: str) -> None:
    fixed, records = _correct(kind, path, text)
    assert gone not in _field(fixed, path)
    assert records, "a removal is always recorded"


# The edges of the fixes: what each new source or rule must NOT let through, and the common
# words its keys must not take for a claim.
EDGE_TRUE: list[tuple[str, str, str, str, str]] = [
    (
        "master_data_is_no_degree",
        "cv",
        "experience.0.bullets.0",
        "Master data management in SQL.",
        "Master data management in SQL.",
    ),
    (
        "certified_datasets_is_no_certification",
        "cv",
        "experience.0.bullets.0",
        "Certified datasets for the finance team.",
        "Certified datasets for the finance team.",
    ),
    (
        "a_degree_of_autonomy",
        "cv",
        "experience.0.bullets.0",
        "Built the platform with a high degree of autonomy.",
        "Built the platform with a high degree of autonomy.",
    ),
    (
        "degree_words_of_the_school_entry",
        "cv",
        "highlights.0",
        "Degree in Data Science from Exemple University.",
        "Degree in Data Science from Exemple University.",
    ),
    ("skills_label_version", "cv", "skills.0.label", "Python 3.12", "Python"),
    ("version_of_a_base_cv_item", "cv", "skills.0.items.0", "SQL 2016", "SQL"),
    (
        "two_years_in_words",
        "cv",
        "experience.1.bullets.0",
        "Two years at Sample Labs.",
        "Two years at Sample Labs.",
    ),
    (
        "letter_start_from_notice",
        "letter",
        "paragraphs.1",
        "I am available to start in three months, after my notice.",
        "I am available to start in three months, after my notice.",
    ),
]


@pytest.mark.parametrize(
    ("kind", "path", "text", "after"), [t[1:] for t in EDGE_TRUE], ids=[t[0] for t in EDGE_TRUE]
)
def test_an_edge_true_fact_is_kept(kind: str, path: str, text: str, after: str) -> None:
    test_a_true_fact_is_kept(kind, path, text, after)


EDGE_FALSE: list[tuple[str, str, str, str, str]] = [
    # A stored period sources the date, never a bare number of it ("2022-12" is no "12").
    ("bare_month_number", "cv", "experience.0.bullets.0", "Built 12 dashboards.", "12"),
    (
        "numeric_date_shifted",
        "cv",
        "experience.0.bullets.0",
        "At Exemple SA since 12/2021.",
        "2021",
    ),
    (
        "numeric_date_bad_month",
        "cv",
        "experience.0.bullets.0",
        "At Exemple SA since 13/2022.",
        "13",
    ),
    # The phone sources the phone, never its digits elsewhere.
    ("phone_digits", "cv", "experience.0.bullets.0", "Built 34 dashboards.", "34"),
    ("other_phone", "letter", "paragraphs.1", "Reach me at +33 6 99 88 77 66.", "99"),
    ("other_site", "letter", "paragraphs.1", "Portfolio at https://other.example.org.", "other"),
    # A period's length, rounded down, never up, and never an open period (no clock).
    ("duration_rounded_up", "cv", "experience.1.bullets.0", "3 years at Sample Labs.", "3 years"),
    ("duration_open_period", "cv", "experience.0.bullets.0", "5 years at Exemple SA.", "5 years"),
    ("duration_unknown_employer", "cv", "summary", "2 years at Nimbus Example.", "2 years"),
    # A version after a word that is no skills term is a number like any other.
    ("number_after_a_verb", "cv", "experience.0.bullets.0", "Shipped 16 pipelines.", "16"),
    ("version_unstated", "cv", "skills.0.items.0", "Python 3.13", "3.13"),
    # In a letter, the answers still source only their own topic.
    ("letter_team_from_notice", "letter", "paragraphs.1", "I managed three squads.", "squads"),
]


@pytest.mark.parametrize(
    ("kind", "path", "text", "gone"), [t[1:] for t in EDGE_FALSE], ids=[t[0] for t in EDGE_FALSE]
)
def test_an_edge_false_claim_is_removed(kind: str, path: str, text: str, gone: str) -> None:
    test_a_false_claim_is_removed(kind, path, text, gone)


def test_the_new_rules_name_their_codes() -> None:
    base = base_cv("casey")
    sources = sources_for(view_of("casey", base), base, None, posting("brume"), kind="cv")
    data = _document("cv", "highlights.0", "PhD in Computer Science, doubled the jobs.")
    data["summary"] = "A decade of data engineering."
    result = checks.run("cv", data, sources, OFF)
    assert sorted({i.code for i in result.claims}) == [
        "change_not_in_profile",
        "credential_not_in_profile",
        "decade_not_in_profile",
    ]
    assert result.content is not None
    assert [checks.excerpt(result.content, i) for i in result.claims] == [
        "decade",
        "PhD in Computer Science",
        "doubled",
    ]


def test_a_factor_is_no_architecture() -> None:
    entries = rules.corpus(["I ran the jobs on x86."])
    text = "Ported the jobs to x86, made them 2x faster."
    assert [text[s:e] for s, e in rules.changes(text, entries)] == ["2x"]


def test_cv_sources_never_hold_the_posting() -> None:
    base = base_cv("casey")
    view = view_of("casey", base)
    cv = sources_for(view, base, None, posting("brume"), kind="cv")
    letter = sources_for(view, base, None, posting("brume"), kind="letter")
    assert "Senior Data Engineer" not in cv.titles
    assert cv.participants == ()
    assert "Senior Data Engineer" in letter.titles
    assert letter.participants == ("Brume Example",)
