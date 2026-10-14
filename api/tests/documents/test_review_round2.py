"""The probes of the second review of D2, and more of the same shapes, as named table cases.

Everything is invented: "Robin Exemplaire" is a "Software Engineer" at "Orbite Données" since
2022-01 and was a "Développeur backend" at "Atelier Nimbus" from 2017-01 to 2021-01, with an "MSc
in Computer Science" (2015 to 2017) and a "Licence Informatique" (2012 to 2015) from "Université
Exemple" and the "Certified Kubernetes Application Developer (CKAD), 2023"; Robin writes for
"Halo Exemple", which wants a "Staff Platform Engineer".

Four lists, each both ways round: the review's fresh probes (`FRESH_*`), its targeted follow-ups
(`TARGETED_*`), and thirty more written the way people write CVs and letters (`MORE_*`).

- a true case: the field after the checks and the correction (the text itself when nothing is
  claimed; a version number stripped from a true skill otherwise);
- a false case: a piece of the claim that must be gone after the correction.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.documents import checks, corrections
from app.documents import content as c
from app.documents.settings import DocumentSettings
from app.documents.sources import sources_for
from tests.documents.conftest import base_cv, load, posting, view_of

OFF = DocumentSettings()

LETTER: dict[str, Any] = {
    "schema": 1,
    "headline": "Software Engineer",
    "recipient": "Hiring team, Halo Exemple",
    "subject": "Application",
    "salutation": "Dear hiring team,",
    "paragraphs": ["I build payment systems in Rust and Python."],
    "closing": "Kind regards",
}


def _document(kind: str, path: str, text: str) -> dict[str, Any]:
    if kind == "letter":
        data = copy.deepcopy(LETTER)
    else:
        data = copy.deepcopy(load("base_cv_robin.json"))
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
    base = base_cv("robin")
    view = view_of("robin", base)
    halo = posting("halo")
    sources = sources_for(view, base, None, halo, kind=kind)  # type: ignore[arg-type]
    result = checks.run(kind, _document(kind, path, text), sources, OFF)  # type: ignore[arg-type]
    assert result.content is not None
    fallbacks = corrections.fallbacks_for(view, base, halo)
    return corrections.remove_remaining(result.content, result, fallbacks)


def _field(content: c.CvContent | c.LetterContent, path: str) -> str:
    return dict(c.text_fields(content)).get(path, "")


S = "summary"
H = "highlights.0"
B0 = "experience.0.bullets.0"
B1 = "experience.1.bullets.0"
P = "paragraphs.0"


def _true(name: str, path: str, text: str, after: str | None = None, kind: str = "cv") -> tuple:
    return (name, kind, path, text, text if after is None else after)


# (name, kind, field, text, the text the field holds after the correction)
FRESH_TRUE: list[tuple[str, str, str, str, str]] = [
    _true("since_month_words", B0, "Software Engineer at Orbite Données since January 2022."),
    _true(
        "de_mois_a_mois",
        B1,
        "Développeur backend chez Atelier Nimbus de janvier 2017 à janvier 2021.",
    ),
    _true("from_abbreviated", B1, "Développeur backend at Atelier Nimbus, Jan 2017 to Jan 2021."),
    _true("from_slash", B1, "Développeur backend at Atelier Nimbus from 01/2017 to 01/2021."),
    _true("from_iso", B1, "Développeur backend at Atelier Nimbus from 2017-01 to 2021-01."),
    _true("since_year", B0, "Software Engineer at Orbite Données since 2022."),
    _true("depuis_year", B0, "Software Engineer chez Orbite Données depuis 2022."),
    _true("year_range", B1, "Développeur backend at Atelier Nimbus, 2017 to 2021."),
    _true("years_at", B1, "4 years at Atelier Nimbus."),
    _true("ans_chez", B1, "4 ans chez Atelier Nimbus."),
    _true("skills_postgres", "skills.0.items.3", "PostgreSQL 15"),
    _true("skills_java", "skills.0.items.2", "Java 17"),
    _true("bullet_versions", B1, "Built the ledger in Java 17 on PostgreSQL 15."),
    _true("about_years", S, "Software Engineer with about 6.5 years of professional experience."),
    _true("msc_short", H, "MSc in Computer Science, Université Exemple"),
    _true("msc_dotted", H, "M.Sc. in Computer Science from Université Exemple"),
    _true("licence", H, "Licence Informatique, Université Exemple"),
    _true("ckad_item", "skills.0.items.0", "Certified Kubernetes Application Developer (CKAD)"),
    _true("ckad_highlight", H, "Certified Kubernetes Application Developer (CKAD)"),
    _true(
        "phone_and_site",
        S,
        "Software Engineer in Rust and Python. +33 7 00 11 22 33, https://robin.example.org.",
    ),
    _true("two_juniors", B1, "Mentored two junior engineers."),
    _true("team_of_four", B1, "Worked in a team of four developers."),
    _true("master_of_science", H, "Master of Science in Computer Science, Université Exemple"),
    _true("graduated_with_msc", S, "Graduated in 2017 with an MSc in Computer Science."),
    _true(
        "puis_chain",
        S,
        "Développeur backend chez Atelier Nimbus de 2017 à 2021, puis Software Engineer chez "
        "Orbite Données depuis janvier 2022.",
    ),
    _true(
        "letter_notice_salary",
        P,
        "My notice period is three months, and my salary expectation is 45 to 50k EUR gross "
        "per year.",
        kind="letter",
    ),
    _true(
        "letter_join_as",
        P,
        "I would be glad to join Halo Exemple as Staff Platform Engineer. You can reach me at "
        "+33 7 00 11 22 33 or e2e-robin@example.org.",
        kind="letter",
    ),
    _true("letter_payments", P, "I run the payments API at Orbite Données.", kind="letter"),
    _true("letter_relocation", P, "I am open to relocating within France.", kind="letter"),
    _true(
        "letter_mentoring",
        P,
        "At Atelier Nimbus I mentored two junior engineers.",
        kind="letter",
    ),
]

TARGETED_TRUE: list[tuple[str, str, str, str, str]] = [
    # B5: a degree in its long form, and a Licence called a Bachelor's.
    _true("masters_degree", S, "Master's degree in Computer Science from Université Exemple."),
    _true("master_in", S, "Master in Computer Science, Université Exemple."),
    _true(
        "bachelor_with_licence",
        H,
        "Bachelor in Computer Science (Licence Informatique), Université Exemple",
    ),
    _true("bachelors_degree_field", H, "Bachelor's degree in Computer Science, Université Exemple"),
    _true("bachelors_degree", H, "Bachelor's degree, Université Exemple"),
    _true(
        "master_en_informatique",
        S,
        "Titulaire d'un Master en informatique de l'Université Exemple.",
    ),
    # B6: a degree after "with" is a credential, never an employer.
    _true("backend_with_msc", S, "Backend engineer with an MSc in Computer Science."),
    _true(
        "engineer_with_master",
        S,
        "Software engineer with a Master of Science in Computer Science.",
    ),
    _true(
        "letter_graduated_with",
        P,
        "I graduated with an MSc in Computer Science from Université Exemple in 2017.",
        kind="letter",
    ),
    _true(
        "with_ckad",
        S,
        "Software engineer with a Certified Kubernetes Application Developer certification.",
    ),
    _true("holds_msc", S, "Holds an MSc in Computer Science from Université Exemple."),
    # B7: a connector before a true title.
    _true(
        "then_title",
        S,
        "Développeur backend at Atelier Nimbus, then Software Engineer at Orbite Données.",
    ),
    _true(
        "previously_title",
        S,
        "Software Engineer at Orbite Données, previously Développeur backend at Atelier Nimbus.",
    ),
    _true(
        "then_before_that",
        S,
        "Software Engineer at Orbite Données since 2022, then Développeur backend before that.",
    ),
    _true(
        "puis_title",
        S,
        "Développeur backend chez Atelier Nimbus, puis Software Engineer chez Orbite Données "
        "depuis janvier 2022.",
    ),
    # The regressions: a credential key used as a verb, a common word.
    _true("certified_as_verb", B0, "Certified the payments API for production use."),
    _true("ckad_competency", "competencies.0", "Certified Kubernetes Application Developer"),
    _true("graduated_from_school", S, "Graduated from Université Exemple in 2017."),
    _true("master_data", B0, "Master data management for the payments API."),
    _true("ckad_certified_engineer", S, "CKAD certified backend engineer."),
    _true("notice_answer", S, "Available with three months notice."),
    _true("notice_fr", S, "Disponible avec un préavis de trois mois."),
]

# (name, kind, field, text, a piece of the claim that must be gone)
FRESH_FALSE: list[tuple[str, str, str, str, str]] = [
    ("percent_spaced", "cv", B0, "Cut the API costs by 40 %.", "40"),
    ("eight_engineers", "cv", B0, "Led 8 engineers.", "8"),
    ("huit_developpeurs", "cv", B0, "Encadré 8 développeurs.", "8"),
    ("senior", "cv", S, "Senior Software Engineer at Orbite Données.", "Senior"),
    ("lead_developer", "cv", B1, "Lead Developer at Atelier Nimbus.", "Lead"),
    ("head_of", "cv", S, "Head of Platform at Orbite Données.", "Head of"),
    ("cka", "cv", H, "Certified Kubernetes Administrator (CKA)", "Administrator"),
    ("aws_pro", "cv", H, "AWS Certified Solutions Architect Professional", "AWS"),
    ("phd", "cv", H, "PhD in Computer Science, Université Exemple", "PhD"),
    ("doctorat", "cv", H, "Doctorat en informatique, Université Exemple", "Doctorat"),
    ("msc_other_school", "cv", H, "MSc in Computer Science, Institut Exemple Paris", "Institut"),
    ("ecole_centrale", "cv", S, "Diplômé de l'École Centrale.", "Centrale"),
    ("since_2018", "cv", B0, "Software Engineer at Orbite Données since 2018.", "2018"),
    ("over_7_years", "cv", S, "Software Engineer with over 7 years of experience.", "7"),
    ("plus_de_7_ans", "cv", S, "Plus de 7 ans d'expérience en Rust.", "7 ans"),
    ("a_decade", "cv", S, "A decade of backend engineering.", "decade"),
    ("une_decennie", "cv", S, "Une décennie de développement backend.", "décennie"),
    ("more_than_doubled", "cv", B0, "More than doubled the throughput of the API.", "doubled"),
    ("x3", "cv", B0, "Made the API x3 faster.", "x3"),
    ("multiplie_par_quatre", "cv", B0, "Multiplié par quatre le nombre de réservations.", "quatre"),
    ("award_winning", "cv", S, "Award-winning engineer.", "Award"),
    ("forbes", "cv", H, "Forbes 30 Under 30", "Forbes"),
    ("posting_company", "cv", S, "Software Engineer at Halo Exemple.", "Halo"),
    ("posting_title_past", "cv", B0, "Staff Platform Engineer at Orbite Données.", "Staff"),
    ("posting_title_headline", "cv", "headline", "Staff Platform Engineer", "Staff"),
    ("two_million", "cv", B0, "Saved 2 million euros in cloud costs.", "million"),
    ("two_hundred_k", "cv", B0, "Saved 200 k€ per year.", "200"),
    ("ten_million_users", "cv", B0, "Served 10 million users.", "10"),
    ("tripled", "cv", B0, "Tripled the bookings.", "Tripled"),
    ("divise_par_deux", "cv", B0, "Divisé par deux le temps de déploiement.", "deux"),
    ("halved", "cv", B0, "Halved the deployment time.", "Halved"),
    ("hackathon_prize", "cv", H, "Won a hackathon prize", "prize"),
    ("laureat", "cv", H, "Lauréat du Prix de l'innovation", "Prix"),
    ("mba", "cv", H, "MBA, INSEAD", "MBA"),
    ("three_squads", "cv", B0, "Managed three squads at Orbite Données.", "three"),
    ("stanford", "cv", S, "Graduated from Stanford University.", "Stanford"),
    ("five_x", "cv", B0, "Made deployments 5x faster.", "5x"),
    ("rust_version", "cv", "skills.0.items.0", "Rust 1.80", "1.80"),
    ("google", "cv", S, "Software Engineer at Google.", "Google"),
    ("letter_phd", "letter", P, "I hold a PhD in Computer Science.", "PhD"),
    ("letter_ten_years", "letter", P, "I have more than 10 years of experience in Rust.", "10"),
    # The four the review found missed.
    (
        "depuis_school_year",
        "cv",
        B1,
        "Développeur backend chez Atelier Nimbus depuis 2015.",
        "2015",
    ),
    ("multiplied_by_four", "cv", B0, "Multiplied the number of bookings by four.", "four"),
    ("top_one_percent", "cv", S, "Top 1% of engineers on the platform.", "1%"),
    (
        "letter_staff_at_employer",
        "letter",
        P,
        "As a Staff Platform Engineer at Orbite Données, I run the payments API.",
        "Staff",
    ),
]

TARGETED_FALSE: list[tuple[str, str, str, str, str]] = [
    # B8: a percent is sourced by a percent, never by a bare number.
    ("top_one_percent_performer", "cv", H, "Top 1% performer", "1%"),
    ("ranked_top_one", "cv", S, "Ranked in the top 1% of the company.", "1%"),
    # B9: a date at an employer is that employer's date.
    ("since_2021", "cv", B0, "Software Engineer at Orbite Données since 2021.", "2021"),
    (
        "since_january_2021",
        "cv",
        B0,
        "Software Engineer at Orbite Données since January 2021.",
        "2021",
    ),
    ("since_2017", "cv", B0, "Software Engineer at Orbite Données since 2017.", "2017"),
    ("from_2015", "cv", B1, "Développeur backend at Atelier Nimbus from 2015 to 2021.", "2015"),
    # B10: everyday change words.
    ("in_half", "cv", B0, "Cut costs in half.", "half"),
    ("fourfold", "cv", B0, "Grew bookings fourfold.", "fourfold"),
    ("divided_by_two", "cv", B0, "Divided the deployment time by two.", "two"),
    # B7's counterpart: a seniority word after a connector stays a claim.
    (
        "then_senior",
        "cv",
        S,
        "Développeur backend at Atelier Nimbus, then Senior Software Engineer at Orbite Données.",
        "Senior",
    ),
    # B5's counterpart: a PhD is no MSc.
    ("phd_against_msc", "cv", H, "PhD in Computer Science, Université Exemple", "PhD"),
]

MORE_TRUE: list[tuple[str, str, str, str, str]] = [
    _true(
        "master_with_acronym_and_range",
        H,
        "Master of Science (MSc) in Computer Science, Université Exemple, 2015 to 2017",
    ),
    _true("licence_range", S, "Licence Informatique, Université Exemple (2012 to 2015)."),
    _true("bachelor_of_science", H, "Bachelor of Science in Computer Science, Université Exemple"),
    _true(
        "diplome_master_en",
        S,
        "Diplômé d'un Master en informatique de l'Université Exemple en 2017.",
    ),
    _true(
        "since_then_previously",
        S,
        "Software Engineer at Orbite Données since January 2022, previously Développeur backend "
        "at Atelier Nimbus.",
    ),
    _true(
        "depuis_auparavant",
        S,
        "Software Engineer chez Orbite Données depuis janvier 2022, auparavant Développeur "
        "backend chez Atelier Nimbus.",
    ),
    _true(
        "range_in_brackets_then",
        S,
        "Développeur backend at Atelier Nimbus (January 2017 to January 2021), then Software "
        "Engineer at Orbite Données.",
    ),
    _true("since_first", S, "Since 2022, Software Engineer at Orbite Données."),
    _true("employer_range_colon", B1, "Atelier Nimbus (2017 to 2021): built the booking API."),
    _true(
        "juniors_and_team",
        B1,
        "Mentored two junior engineers and worked in a team of four developers.",
    ),
    _true(
        "ckad_since",
        S,
        "Holds the Certified Kubernetes Application Developer (CKAD) certification since 2023.",
    ),
    _true("percent_stated", B0, "Cut the p99 latency of the payments API by 35%."),
    _true("pour_cent", B0, "Réduit la latence p99 de l'API de paiement de 35 %."),
    _true("percent_word", B0, "Cut the p99 latency by 35 percent."),
    _true(
        "letter_since_with_msc",
        P,
        "I am a Software Engineer at Orbite Données since January 2022, with an MSc in Computer "
        "Science from Université Exemple.",
        kind="letter",
    ),
]

MORE_FALSE: list[tuple[str, str, str, str, str]] = [
    ("percent_shifted", "cv", B0, "Cut the p99 latency by 40%.", "40"),
    ("top_percent_from_a_percent", "cv", S, "Top 35% of engineers at Orbite Données.", "35%"),
    (
        "senior_with_true_date",
        "cv",
        B0,
        "Senior Software Engineer at Orbite Données since January 2022.",
        "Senior",
    ),
    ("principal", "cv", S, "Principal Engineer at Orbite Données.", "Principal"),
    ("since_march", "cv", B0, "Software Engineer at Orbite Données since March 2022.", "March"),
    ("de_2016", "cv", B1, "Développeur backend chez Atelier Nimbus de 2016 à 2021.", "2016"),
    ("jusqu_en", "cv", B1, "Développeur backend chez Atelier Nimbus jusqu'en 2022.", "2022"),
    ("range_end_moved", "cv", B1, "Atelier Nimbus (2017 to 2022): built the booking API.", "2022"),
    ("multiplied_by_ten", "cv", B0, "Multiplied the throughput of the API by ten.", "ten"),
    ("divise_par_trois", "cv", B0, "Divisé le temps de déploiement par trois.", "trois"),
    ("fivefold", "cv", B0, "Reduced cloud costs fivefold.", "fivefold"),
    ("n_fold", "cv", B0, "Grew bookings 4-fold.", "fold"),
    ("mba_long", "cv", H, "Master of Business Administration, Université Exemple", "Business"),
    (
        "bachelor_other_field",
        "cv",
        H,
        "Bachelor of Science in Physics, Université Exemple",
        "Physics",
    ),
    ("lead_with_msc", "cv", S, "Lead Software Engineer with an MSc in Computer Science.", "Lead"),
]


def _assert_kept(kind: str, path: str, text: str, after: str) -> None:
    fixed, records = _correct(kind, path, text)
    assert _field(fixed, path) == after
    if after == text:
        assert records == []
    else:
        assert [(r.before, r.after, r.code) for r in records] == [
            (text, after, corrections.VERSION_REMOVED)
        ]


def _assert_gone(kind: str, path: str, text: str, gone: str) -> None:
    fixed, records = _correct(kind, path, text)
    assert gone not in _field(fixed, path)
    assert records, "a correction is always recorded"


def _ids(table: list[tuple]) -> list[str]:
    return [row[0] for row in table]


def _rows(table: list[tuple]) -> list[tuple]:
    return [row[1:] for row in table]


@pytest.mark.parametrize(("kind", "path", "text", "after"), _rows(FRESH_TRUE), ids=_ids(FRESH_TRUE))
def test_fresh_true(kind: str, path: str, text: str, after: str) -> None:
    _assert_kept(kind, path, text, after)


@pytest.mark.parametrize(
    ("kind", "path", "text", "after"), _rows(TARGETED_TRUE), ids=_ids(TARGETED_TRUE)
)
def test_targeted_true(kind: str, path: str, text: str, after: str) -> None:
    _assert_kept(kind, path, text, after)


@pytest.mark.parametrize(("kind", "path", "text", "after"), _rows(MORE_TRUE), ids=_ids(MORE_TRUE))
def test_more_true(kind: str, path: str, text: str, after: str) -> None:
    _assert_kept(kind, path, text, after)


@pytest.mark.parametrize(
    ("kind", "path", "text", "gone"), _rows(FRESH_FALSE), ids=_ids(FRESH_FALSE)
)
def test_fresh_false(kind: str, path: str, text: str, gone: str) -> None:
    _assert_gone(kind, path, text, gone)


@pytest.mark.parametrize(
    ("kind", "path", "text", "gone"), _rows(TARGETED_FALSE), ids=_ids(TARGETED_FALSE)
)
def test_targeted_false(kind: str, path: str, text: str, gone: str) -> None:
    _assert_gone(kind, path, text, gone)


@pytest.mark.parametrize(("kind", "path", "text", "gone"), _rows(MORE_FALSE), ids=_ids(MORE_FALSE))
def test_more_false(kind: str, path: str, text: str, gone: str) -> None:
    _assert_gone(kind, path, text, gone)
