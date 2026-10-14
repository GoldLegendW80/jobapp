"""The probes of the third review of D2, the policy of step 5, and twenty more true CVs, as
named table cases.

Everything is invented: "Sacha Duvalier" is a "Data Engineer" at "Ferme Numérique" since
2021-09 (an open period) and was a "Software Developer" at "Kestrel Analytics Ltd" from 2018-03
to 2021-08, holds a "BSc Mathematics" from the "University of Northvale" (2014 to 2017) and the
"Google Professional Data Engineer, 2023" certification, states 8.5 years of experience and two
percents ("by 20%", "by 30%"). Sacha writes to "Polaris Exemple", which wants a "Senior
Analytics Engineer". The generation date is 2026-10-14, given to the checks as data.

- a true case: the field is the same after the checks and the correction, with no record;
- a false case, by code (`GONE`): a piece of the claim is gone after the correction;
- a false case, ambiguous by construction (`FLAGGED`, `rules.POLICY`): the claim is recorded as
  `needs_rewrite` and, when no token cut keeps the sentence whole, kept for the person to see;
- a corrected sentence (`AFTER`): the exact text the field holds after the correction, printed
  before and after.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pytest

from app.documents import checks, corrections, rules
from app.documents import content as c
from app.documents.settings import DocumentSettings
from app.documents.sources import sources_for
from tests.documents.conftest import base_cv, load, posting, view_of

OFF = DocumentSettings()
TODAY = date(2026, 10, 14)

LETTER: dict[str, Any] = {
    "schema": 1,
    "headline": "Data Engineer",
    "recipient": "Hiring team, Polaris Exemple",
    "subject": "Application",
    "salutation": "Dear hiring team,",
    "paragraphs": ["I build data pipelines in Python and Spark."],
    "closing": "Kind regards",
}


def _document(kind: str, path: str, text: str) -> dict[str, Any]:
    if kind == "letter":
        data = copy.deepcopy(LETTER)
    else:
        data = copy.deepcopy(load("base_cv_sacha.json"))
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


def _run(kind: str, path: str, text: str) -> checks.Checks:
    base = base_cv("sacha")
    sources = sources_for(view_of("sacha", base), base, None, posting("polaris"), kind=kind)  # type: ignore[arg-type]
    return checks.run(kind, _document(kind, path, text), sources, OFF, today=TODAY)  # type: ignore[arg-type]


def _correct(kind: str, path: str, text: str) -> tuple[c.CvContent | c.LetterContent, list]:
    base = base_cv("sacha")
    result = _run(kind, path, text)
    assert result.content is not None
    fallbacks = corrections.fallbacks_for(view_of("sacha", base), base, posting("polaris"))
    return corrections.remove_remaining(result.content, result, fallbacks)


def _field(content: c.CvContent | c.LetterContent, path: str) -> str:
    return dict(c.text_fields(content)).get(path, "")


S = "summary"
H = "highlights.0"
B0 = "experience.0.bullets.0"  # Ferme Numérique, since 2021-09
B1 = "experience.1.bullets.0"  # Kestrel Analytics Ltd, 2018-03 to 2021-08
P = "paragraphs.0"
L = "letter"


def _t(name: str, path: str, text: str, kind: str = "cv") -> tuple[str, str, str, str]:
    return (name, kind, path, text)


# --- the review's true probes: (a), passed and lost, and the inputs of B11 to B13 ---------------

REVIEW_TRUE: list[tuple[str, str, str, str]] = [
    # Dates, passed.
    _t("since_september_2021", B0, "Data Engineer at Ferme Numérique since September 2021."),
    _t(
        "from_march_to_august",
        B1,
        "Software Developer at Kestrel Analytics Ltd from March 2018 to August 2021.",
    ),
    _t("brackets_2018_2021", B1, "Software Developer at Kestrel Analytics Ltd (2018 to 2021)."),
    _t("comma_2018_2021", B1, "Software Developer at Kestrel Analytics Ltd, 2018 to 2021."),
    _t(
        "depuis_de_mars_a_aout",
        S,
        "Data Engineer chez Ferme Numérique depuis septembre 2021, Software Developer chez "
        "Kestrel Analytics Ltd de mars 2018 à août 2021.",
    ),
    _t("to_present_2021", B0, "Data Engineer at Ferme Numérique (2021 to present)."),
    _t("since_2021", B0, "Data Engineer at Ferme Numérique since 2021."),
    # The degree, passed.
    _t("bsc_in", H, "BSc in Mathematics, University of Northvale"),
    _t("bachelor_of_science", H, "Bachelor of Science in Mathematics, University of Northvale"),
    _t("bachelors_degree_in", H, "Bachelor's degree in Mathematics, University of Northvale"),
    _t("bsc_hons", H, "BSc (Hons) Mathematics, University of Northvale"),
    _t("bsc_dotted", H, "B.Sc. Mathematics, University of Northvale"),
    _t("licence_de_mathematiques", H, "Licence de mathématiques, University of Northvale"),
    _t("with_a_bsc", S, "Data Engineer with a BSc in Mathematics."),
    _t(
        "graduated_in_2017",
        S,
        "Graduated from the University of Northvale in 2017 with a BSc in Mathematics.",
    ),
    # Versions, passed.
    _t("python_item", "skills.0.items.0", "Python 3.11"),
    _t("spark_item", "skills.0.items.1", "Spark 3.5"),
    _t("airflow_item", "skills.0.items.2", "Airflow 2"),
    _t("python_competency", "competencies.0", "Python 3.11"),
    # Percents and counts, passed.
    _t("by_20", B0, "Reduced pipeline costs by 20% by moving batch jobs to Spark."),
    _t("by_20_spaced", B0, "Reduced pipeline costs by 20 % by moving batch jobs to Spark."),
    _t("team_of_three", B0, "Work in a team of three data engineers."),
    _t("letter_by_20", P, "At Ferme Numérique I reduced pipeline costs by 20%.", L),
    # Connectors, passed.
    _t(
        "previously",
        S,
        "Data Engineer at Ferme Numérique, previously Software Developer at Kestrel Analytics Ltd.",
    ),
    _t(
        "auparavant",
        S,
        "Data Engineer chez Ferme Numérique, auparavant Software Developer chez Kestrel "
        "Analytics Ltd.",
    ),
    # Durations, passed.
    _t("three_years_at", B1, "3 years at Kestrel Analytics Ltd."),
    _t("spent_three_years", S, "Spent three years at Kestrel Analytics Ltd."),
    _t("more_than_8", S, "Data engineer with more than 8 years of experience."),
    _t("eight_plus", S, "Data engineer with 8+ years of experience."),
    _t("eight_and_a_half", S, "Data engineer with 8.5 years of experience."),
    # Certification, passed.
    _t("cert_year", H, "Google Professional Data Engineer (2023)"),
    _t("cert_certified", H, "Google Professional Data Engineer certified"),
    _t("cert_certificate", H, "Google Professional Data Engineer certificate"),
    _t("certified_cert", H, "Certified Google Professional Data Engineer"),
    # Letters, passed.
    _t(
        "letter_join",
        P,
        "I would be glad to join Polaris Exemple as Senior Analytics Engineer.",
        L,
    ),
    _t(
        "letter_notice_location",
        P,
        "My notice period is two months, and I can work remotely or on site in Lyon.",
        L,
    ),
    # (a) lost 1 and B11: a total rounded down.
    _t("b11_eight_years", S, "Data engineer with 8 years of experience in Python and Spark."),
    _t("b11_eight_words", S, "Data engineer with eight years of experience."),
    # (a) lost 2 and 4, B13: "<stated name> certification".
    _t("b13_cert_highlight", H, "Google Professional Data Engineer certification"),
    _t(
        "b13_cert_bullet",
        B0,
        "In 2023, earned the Google Professional Data Engineer certification.",
    ),
    _t(
        "b13_letter_cert",
        P,
        "I hold a BSc in Mathematics from the University of Northvale and the Google "
        "Professional Data Engineer certification.",
        L,
    ),
    # (a) lost 3 and B12: a duration at a named employer, closed or open.
    _t(
        "b12_letter_after_three_years",
        P,
        "I have been a Data Engineer at Ferme Numérique since September 2021, after three "
        "years as a Software Developer at Kestrel Analytics Ltd.",
        L,
    ),
    _t("b12_three_years_as", S, "Three years as a Software Developer at Kestrel Analytics Ltd."),
    _t("b12_for_three_years", B1, "Software Developer at Kestrel Analytics Ltd for three years."),
    _t("b12_five_years_at", S, "Five years at Ferme Numérique."),
    _t(
        "b12_for_5_years_running",
        B0,
        "Data Engineer at Ferme Numérique for 5 years, running the ingestion platform.",
    ),
    _t(
        "b12_letter_spent_five",
        P,
        "I have spent five years at Ferme Numérique building its data platform.",
        L,
    ),
    # B15's counterpart: a year inside the entry's own period.
    _t("b15_snowflake_2023", B0, "Migrated the warehouse to Snowflake in 2023."),
]

# --- twenty more true CVs: durations, current jobs, "to present", totals, certifications ------

OWN_TRUE: list[tuple[str, str, str, str]] = [
    _t("own_comma_to_present", B0, "Data Engineer at Ferme Numérique, 2021 to present."),
    _t(
        "own_month_to_present",
        B0,
        "Data Engineer at Ferme Numérique (September 2021 to present).",
    ),
    _t("own_a_aujourdhui", B0, "Data Engineer chez Ferme Numérique de 2021 à aujourd'hui."),
    _t("own_a_ce_jour", B0, "Data Engineer chez Ferme Numérique de 2021 à ce jour."),
    _t("own_seven_years", S, "Data engineer with 7 years of experience."),
    _t("own_seven_professional", S, "Data engineer with seven years of professional experience."),
    _t("own_more_than_eight", S, "More than 8 years of experience in data engineering."),
    _t("own_eight_in_data", S, "Data engineer with 8 years in data platforms."),
    _t("own_plus_de_huit", S, "Plus de 8 ans d'expérience en ingénierie des données."),
    _t("own_huit_ans", S, "8 ans d'expérience en ingénierie des données."),
    _t(
        "own_three_years_building",
        B1,
        "Three years at Kestrel Analytics Ltd building ETL pipelines in Scala.",
    ),
    _t("own_entry_three_years", B1, "Built ETL pipelines in Scala for three years."),
    _t("own_entry_five_years", B0, "Ran the ingestion platform for five years."),
    _t("own_cert_year", H, "Google Professional Data Engineer certification (2023)"),
    _t("own_certified_comma", H, "Certified Google Professional Data Engineer, 2023"),
    _t(
        "own_letter_certificate",
        P,
        "I earned the Google Professional Data Engineer certificate in 2023.",
        L,
    ),
    _t("own_cert_competency", "competencies.1", "Google Professional Data Engineer certification"),
    _t("own_entry_year_2022", B0, "Migrated the warehouse to Snowflake in 2022."),
    _t("own_entry_year_2020", B1, "Moved batch jobs to Spark in 2020."),
    _t(
        "own_letter_both_jobs",
        P,
        "Since September 2021 I have been a Data Engineer at Ferme Numérique, and for three "
        "years before that a Software Developer at Kestrel Analytics Ltd.",
        L,
    ),
    _t(
        "own_summary_both",
        S,
        "Data Engineer at Ferme Numérique since 2021, previously Software Developer at Kestrel "
        "Analytics Ltd (2018 to 2021).",
    ),
]


def _g(name: str, path: str, text: str, gone: str, kind: str = "cv") -> tuple:
    return (name, kind, path, text, gone)


# --- the review's false probes: (b), the planned list, and the follow-ups of B12 to B15 ---------

GONE: list[tuple[str, str, str, str, str]] = [
    _g("percent_35", B0, "Reduced pipeline costs by 35%.", "35"),
    _g("percent_50", B0, "Cut query time by 50%.", "50"),
    _g("de_40", B0, "Réduit les coûts de 40 %.", "40"),
    _g("team_of_12", B0, "Led a team of 12 data engineers.", "12"),
    _g("five_engineers", B0, "Managed five engineers.", "five"),
    _g("equipe_de_6", B0, "Dirigé une équipe de 6 ingénieurs.", "6"),
    _g("senior", S, "Senior Data Engineer at Ferme Numérique.", "Senior"),
    _g("lead", B0, "Lead Data Engineer at Ferme Numérique.", "Lead"),
    _g("principal", S, "Principal Data Engineer at Ferme Numérique.", "Principal"),
    _g("head_of_data", S, "Head of Data at Ferme Numérique.", "Head of"),
    _g("msc", H, "MSc in Mathematics, University of Northvale", "MSc"),
    _g("master_data_science", H, "Master of Science in Data Science", "Data Science"),
    _g("phd", H, "PhD in Mathematics, University of Northvale", "PhD"),
    _g("bsc_physics", H, "BSc in Physics, University of Northvale", "Physics"),
    _g("oxford", S, "Graduated from the University of Oxford.", "Oxford"),
    _g("aws_pro", H, "AWS Certified Solutions Architect Professional", "AWS"),
    _g("cka", H, "Certified Kubernetes Administrator (CKA)", "Kubernetes"),
    _g("award_2023", H, "2023 Data Innovation Award", "Award"),
    _g("award_winning", S, "Award-winning data engineer.", "Award"),
    _g("since_2019", B0, "Data Engineer at Ferme Numérique since 2019.", "2019"),
    _g(
        "from_2014_to_2021",
        B1,
        "Software Developer at Kestrel Analytics Ltd from 2014 to 2021.",
        "2014",
    ),
    _g(
        "from_march_2017",
        B1,
        "Software Developer at Kestrel Analytics Ltd from March 2017.",
        "2017",
    ),
    _g("range_2018_2022", B1, "Software Developer at Kestrel Analytics Ltd, 2018 to 2022.", "2022"),
    _g("over_10_years", S, "Data engineer with over 10 years of experience.", "10"),
    _g("a_decade", S, "Data engineer with a decade of experience.", "decade"),
    _g("nine_years", S, "Data engineer with 9 years of experience.", "9"),
    _g("letter_over_12", P, "I have over 12 years of experience in data engineering.", "12", L),
    _g("doubled", B0, "Doubled the throughput of the ingestion platform.", "Doubled"),
    _g("in_half", B0, "Cut pipeline costs in half.", "half"),
    _g("tenfold", B0, "Grew the data volume tenfold.", "tenfold"),
    _g("threefold", B0, "Reduced query time threefold.", "threefold"),
    _g("top_5", S, "Top 5% of engineers at Ferme Numérique.", "5%"),
    _g("top_20", S, "Top 20% of engineers at Ferme Numérique.", "20%"),
    _g("two_million", B0, "Saved 2 million euros in cloud costs.", "million"),
    _g(
        "letter_posting_title_at_employer",
        P,
        "As a Senior Analytics Engineer at Ferme Numérique, I run the ingestion platform.",
        "Senior",
        L,
    ),
    # B14: a start moved at the current job, written "to present".
    _g(
        "b14_to_present_brackets", B0, "Data Engineer at Ferme Numérique (2018 to present).", "2018"
    ),
    _g("b14_to_present_comma", B0, "Data Engineer at Ferme Numérique, 2018 to present.", "2018"),
    _g("b14_a_aujourdhui", B0, "Data Engineer chez Ferme Numérique de 2018 à aujourd'hui.", "2018"),
    _g("b14_since_2018", B0, "Data Engineer at Ferme Numérique since 2018.", "2018"),
    # B15: a year of an experience bullet outside its own period.
    _g("b15_in_2018_opening", B0, "In 2018, migrated the warehouse to Snowflake.", "2018"),
    _g("b15_built_in_2018", B0, "Built the ingestion platform in 2018.", "2018"),
    _g("b15_kestrel_2023", B1, "Launched the client reporting API in 2023.", "2023"),
    _g("b15_kestrel_2014", B1, "Launched the reporting API in 2014.", "2014"),
    # B15: a percent never sources a bare count.
    _g("b15_twenty_engineers", B0, "Managed 20 engineers.", "20"),
    _g("b15_forbes_30", H, "Named to the Forbes 30 Under 30 list.", "Forbes"),
    # B13's counterpart.
    _g(
        "b13_other_cert",
        H,
        "Google Professional Machine Learning Engineer certification",
        "Machine Learning",
    ),
    # B12's counterpart, cut where the sentence stays whole.
    _g(
        "b12_for_five_at_kestrel",
        B1,
        "Software Developer at Kestrel Analytics Ltd for five years.",
        "five",
    ),
]

# Ambiguous by construction: a duration no token cut can take keeps its sentence, flagged.
FLAGGED: list[tuple[str, str, str, str, str]] = [
    _g("b12_ten_years_at_ferme", S, "Ten years at Ferme Numérique.", "duration_not_in_profile"),
    _g(
        "b12_letter_six_years_as",
        P,
        "I have been a Data Engineer for a while, after six years as a Software Developer at "
        "Kestrel Analytics Ltd.",
        "duration_not_in_profile",
        L,
    ),
    _g(
        "translated_school",
        S,
        "Studied mathematics at Northvale University.",
        "school_not_in_profile",
    ),
]

# --- B16 and (c): the exact text after the correction -------------------------------------------

AFTER: list[tuple[str, str, str, str, str]] = [
    _g(
        "b16_comma_range_end",
        B1,
        "Software Developer at Kestrel Analytics Ltd, 2018 to 2022.",
        "Software Developer at Kestrel Analytics Ltd.",
    ),
    _g(
        "b16_comma_range_middle",
        B1,
        "Software Developer at Kestrel Analytics Ltd, 2018 to 2022, building ETL pipelines.",
        "Software Developer at Kestrel Analytics Ltd, building ETL pipelines.",
    ),
    _g(
        "b16_brackets_middle",
        B1,
        "Software Developer at Kestrel Analytics Ltd (2018 to 2022), building ETL pipelines.",
        "Software Developer at Kestrel Analytics Ltd, building ETL pipelines.",
    ),
    _g(
        "b16_comma_to_present",
        B0,
        "Data Engineer at Ferme Numérique, 2018 to present.",
        "Data Engineer at Ferme Numérique.",
    ),
    _g("c_percent", B0, "Reduced pipeline costs by 35%.", "Reduced pipeline costs."),
    _g("c_count", B0, "Managed 12 engineers.", "Managed engineers."),
    _g(
        "c_since",
        B0,
        "Data Engineer at Ferme Numérique since 2019.",
        "Data Engineer at Ferme Numérique.",
    ),
    _g(
        "c_from_to",
        B1,
        "Software Developer at Kestrel Analytics Ltd from 2014 to 2021.",
        "Software Developer at Kestrel Analytics Ltd.",
    ),
    _g("c_de_40", B0, "Réduit les coûts de 40 %.", "Réduit les coûts."),
    _g(
        "c_led_across",
        B0,
        "Led 12 engineers across three countries to rebuild the ingestion platform.",
        "Led engineers across three countries to rebuild the ingestion platform.",
    ),
    _g(
        "c_in_2023_by",
        B0,
        "Cut pipeline costs in 2023 by 50% by moving batch jobs to Spark.",
        "Cut pipeline costs in 2023 by moving batch jobs to Spark.",
    ),
    _g(
        "c_mentored_and",
        B0,
        "Mentored 4 junior engineers and ran the on-call rota.",
        "Mentored junior engineers and ran the on-call rota.",
    ),
    _g(
        "c_in_2018_opening",
        B0,
        "In 2018, migrated the warehouse to Snowflake.",
        "Migrated the warehouse to Snowflake.",
    ),
    _g(
        "c_built_in_2018",
        B0,
        "Built the ingestion platform in 2018.",
        "Built the ingestion platform.",
    ),
    _g("c_twenty", B0, "Managed 20 engineers.", "Managed engineers."),
    _g("c_with_over_10", S, "Data engineer with over 10 years of experience.", "Data engineer."),
    _g(
        "c_nine_in_python",
        S,
        "Data engineer with 9 years of experience in Python and Spark.",
        "Data engineer with experience in Python and Spark.",
    ),
    _g(
        "c_letter_over_12",
        P,
        "I have over 12 years of experience in data engineering.",
        "I have experience in data engineering.",
        L,
    ),
    _g(
        "c_for_five",
        B1,
        "Software Developer at Kestrel Analytics Ltd for five years.",
        "Software Developer at Kestrel Analytics Ltd.",
    ),
    _g("c_a_decade", S, "Data engineer with a decade of experience.", "Data engineer."),
    _g("c_plus_de_12_ans", S, "Plus de 12 ans d'expérience en Python.", "Expérience en Python."),
    _g(
        "c_year_in_a_list",
        B0,
        "Shipped releases in 2019 and 2022.",
        "Shipped releases in 2019 and 2022.",
    ),
    _g(
        "c_staff_headline",
        "headline",
        "Staff Data Engineer",
        "Data Engineer",
    ),
]

# `corrections.tidy` alone: what a cut can leave, and what it becomes.
TIDY: list[tuple[str, str, str]] = [
    (
        "comma_stop",
        "Software Developer at Kestrel Analytics Ltd,.",
        "Software Developer at Kestrel Analytics Ltd.",
    ),
    (
        "double_comma",
        "Kestrel Analytics Ltd,, building ETL pipelines.",
        "Kestrel Analytics Ltd, building ETL pipelines.",
    ),
    ("space_comma", "Built the API , then the UI.", "Built the API, then the UI."),
    ("empty_brackets", "Built the API ( ) in Rust.", "Built the API in Rust."),
    ("bare_brackets", "Built the API () in Rust.", "Built the API in Rust."),
    ("dangling_and", "Built the API and.", "Built the API."),
    ("dangling_comma_and", "Built the API, and.", "Built the API."),
    ("dangling_et", "Construit l'API et.", "Construit l'API."),
    ("double_space", "Built  the  API .", "Built the API."),
    ("trailing_comma", "Built the API,", "Built the API"),
    ("leading_comma", ", built the API.", "built the API."),
    (
        "untouched",
        "Built the API in Rust, Python and SQL.",
        "Built the API in Rust, Python and SQL.",
    ),
]


def _ids(table: list[tuple]) -> list[str]:
    return [row[0] for row in table]


def _rows(table: list[tuple]) -> list[tuple]:
    return [row[1:] for row in table]


def _assert_true(kind: str, path: str, text: str) -> None:
    result = _run(kind, path, text)
    assert result.claims == (), [(i.code, text[i.start : i.end]) for i in result.claims]
    fixed, records = _correct(kind, path, text)
    assert _field(fixed, path) == text
    assert records == []


@pytest.mark.parametrize(("kind", "path", "text"), _rows(REVIEW_TRUE), ids=_ids(REVIEW_TRUE))
def test_review_true(kind: str, path: str, text: str) -> None:
    _assert_true(kind, path, text)


@pytest.mark.parametrize(("kind", "path", "text"), _rows(OWN_TRUE), ids=_ids(OWN_TRUE))
def test_own_true(kind: str, path: str, text: str) -> None:
    _assert_true(kind, path, text)


@pytest.mark.parametrize(("kind", "path", "text", "gone"), _rows(GONE), ids=_ids(GONE))
def test_false_gone(kind: str, path: str, text: str, gone: str) -> None:
    fixed, records = _correct(kind, path, text)
    after = _field(fixed, path)
    print(f"\n  before: {text}\n  after:  {after}")
    assert gone not in after
    assert records, "a correction is always recorded"


@pytest.mark.parametrize(("kind", "path", "text", "code"), _rows(FLAGGED), ids=_ids(FLAGGED))
def test_false_flagged(kind: str, path: str, text: str, code: str) -> None:
    result = _run(kind, path, text)
    assert [i.code for i in result.needs_rewrite] == [code]
    fixed, records = _correct(kind, path, text)
    assert _field(fixed, path) == text  # kept: no cut leaves it whole, the model rewrites it
    assert [(r.before, r.after, r.code, r.reason) for r in records] == [
        (text, text, code, corrections.KEPT)
    ]


@pytest.mark.parametrize(("kind", "path", "text", "after"), _rows(AFTER), ids=_ids(AFTER))
def test_corrected_sentence(kind: str, path: str, text: str, after: str) -> None:
    fixed, records = _correct(kind, path, text)
    print(f"\n  before: {text}\n  after:  {_field(fixed, path)}")
    assert _field(fixed, path) == after
    assert records


@pytest.mark.parametrize(("before", "after"), _rows(TIDY), ids=_ids(TIDY))
def test_tidy(before: str, after: str) -> None:
    print(f"\n  before: {before!r}\n  after:  {corrections.tidy(before)!r}")
    assert corrections.tidy(before) == after


# --- the policy ---------------------------------------------------------------------------------


def test_policy_table_is_the_spec() -> None:
    """The table of SPEC-documents.md step 5, as data: every code it names, and its class."""
    assert rules.policy("percent_not_in_sources") == rules.BY_CODE
    assert rules.policy("number_not_in_sources") == rules.BY_CODE
    assert rules.policy(rules.CREDENTIAL) == rules.BY_CODE
    assert rules.policy(rules.SENIORITY) == rules.BY_CODE
    assert rules.policy(rules.CHANGE) == rules.BY_CODE
    assert rules.policy("duration_not_in_profile") == rules.NEEDS_REWRITE
    assert rules.policy(rules.DECADE) == rules.NEEDS_REWRITE
    assert rules.policy(rules.DATE_AT_EMPLOYER) == rules.NEEDS_REWRITE
    assert rules.policy("year_not_in_sources") == rules.NEEDS_REWRITE
    assert rules.policy("employer_not_in_profile") == rules.NAME
    assert rules.policy("an_unknown_code") == rules.BY_CODE


def test_unknown_employer_is_by_code_and_a_paraphrase_is_not() -> None:
    sure = _run("cv", S, "Data Engineer at Google.")
    assert [(i.code, i.rewrite) for i in sure.claims] == [("employer_not_in_profile", False)]
    near = _run("cv", S, "Studied mathematics at Northvale University.")
    assert [(i.code, i.rewrite) for i in near.claims] == [("school_not_in_profile", True)]


def test_open_period_needs_the_date() -> None:
    """Without the generation date an open period sources no duration; with it, its whole
    years up to the month before."""
    base = base_cv("sacha")
    sources = sources_for(view_of("sacha", base), base, None, posting("polaris"), kind="cv")
    data = _document("cv", S, "Five years at Ferme Numérique.")
    assert checks.run("cv", data, sources, OFF).claims != ()
    assert checks.run("cv", data, sources, OFF, today=TODAY).claims == ()
    early = date(2026, 8, 15)  # five whole years need September 2021 to August 2026
    assert checks.run("cv", data, sources, OFF, today=early).claims != ()
