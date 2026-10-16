"""D24: what code reads of an uploaded CV's text (`app.documents.upload`), pure. The accept and
refuse tables of a name, a field and a date against the text, with their neighbours (case,
accents, punctuation, spacing, a longer name, a shorter one, a number inside a longer number,
another script, French and English months, two digit years), the fixed block and the experience
entries it drops, the language of a text, and the cost on the largest text."""

from __future__ import annotations

from typing import Any

import pytest

from app.documents import content as c
from app.documents import upload
from app.documents.models import Language

TEXT = (
    "Morgan Exampleton\n"
    "Quillmere Example Works, Platform Engineer, Nantes\n"
    "February 2023 to present\n"
    "Tarnwick Sample Studio · Software Developer · Rennes · 01/2021 – 01/2023\n"
    "L'ÉCOLE EXEMPLE D'INGÉNIEURS, Diplôme d'ingénieur, 2016 à 2021\n"
    "Brumeval S.A., depuis mars 2022, ２０２４\n"
    "Northwind-Example GmbH, 2019-07 to 2020/11, Phone 06 12 20 19 30, ref 120194\n"
    "Languages: English (fluent), Français : langue maternelle\n"
)
READ = upload._read(TEXT)


@pytest.mark.parametrize(
    "value",
    [
        "Quillmere Example Works",
        "quillmere example works",
        "QUILLMERE EXAMPLE WORKS",
        "Quillmere  Example\tWorks",
        "Quillmere Example Works.",
        "Tarnwick Sample Studio",
        "Platform Engineer",
        "platform engineer",
        "Software Developer",
        "Nantes",
        "L'École Exemple d'Ingénieurs",
        "l'ecole exemple d'ingenieurs",
        "Ecole Exemple d Ingenieurs",
        "Diplôme d'ingénieur",
        "Brumeval S.A.",
        "Brumeval S A",
        "Northwind-Example GmbH",
        "Northwind Example GmbH",
        "English",
        "fluent",
        "Français",
        "francais",
        "langue maternelle",
        "Ｎａｎｔｅｓ",  # full width letters fold to their plain form
        "Northwind",  # a word of a longer name stands in the text: the reader's whole name counts
    ],
)
def test_a_value_the_text_states_is_present(value: str) -> None:
    assert upload.present(value, READ)


@pytest.mark.parametrize(
    "value",
    [
        "Quillmere Example Work",  # a word cut short
        "Quillmere Example Works Ltd",  # a longer name
        "Example Works Quillmere",  # another order
        "Quillmere Works",  # a word missing in between
        "Quillmeer Example Works",  # a typo
        "Senior Platform Engineer",  # a seniority word the file does not write
        "Platform Engineers",  # plural
        "Engineer Platform",
        "Software Development",
        "Nante",
        "Brumeval SAS",
        "German",
        "native",
        "Paris",
        "",
        "   ",
        "—",
        "Κουιλμερ",  # another script
    ],
)
def test_a_value_the_text_does_not_state_is_absent(value: str) -> None:
    assert not upload.present(value, READ)


@pytest.mark.parametrize(
    ("stored", "kept"),
    [
        ("2023-02", "2023-02"),  # February 2023
        ("2023", "2023"),
        ("2021-01", "2021-01"),  # 01/2021
        ("2023-01", "2023-01"),  # 01/2023 after an en dash
        ("2022-03", "2022-03"),  # mars 2022
        ("2019-07", "2019-07"),  # 2019-07
        ("2020-11", "2020-11"),  # 2020/11
        ("2016", "2016"),
        ("2021", "2021"),
        ("2024", "2024"),  # full width figures fold
        (None, None),
        ("", ""),
        ("2023-03", "2023"),  # a month the file does not write: the year alone
        ("2021-02", "2021"),
        ("2022-04", "2022"),
        ("2019-08", "2019"),
        ("2016-09", "2016"),
        ("2018", None),  # a year the file does not write
        ("2018-02", None),
        ("2012", None),  # "06 12 20 19": figures of a phone number are no year
        ("2019-12", "2019"),  # "ref 120194" holds 2019 inside a longer number: not that
        ("1201", None),
        ("2030", None),
    ],
)
def test_a_date_keeps_what_the_text_states(stored: str | None, kept: str | None) -> None:
    assert upload.period(stored, READ) == kept


@pytest.mark.parametrize(
    ("text", "stored", "kept"),
    [
        ("March 2019", "2019-03", "2019-03"),
        ("Mar. 2019", "2019-03", "2019-03"),
        ("Mar 2019", "2019-03", "2019-03"),
        ("March, 2019", "2019-03", "2019-03"),
        ("mars 2019", "2019-03", "2019-03"),
        ("MARS 2019", "2019-03", "2019-03"),
        ("févr. 2019", "2019-02", "2019-02"),
        ("fevrier 2019", "2019-02", "2019-02"),
        ("Sept. 2019", "2019-09", "2019-09"),
        ("septembre 2019", "2019-09", "2019-09"),
        ("août 2019", "2019-08", "2019-08"),
        ("déc. 2019", "2019-12", "2019-12"),
        ("3/2019", "2019-03", "2019-03"),
        ("03/2019", "2019-03", "2019-03"),
        ("03.2019", "2019-03", "2019-03"),
        ("03-2019", "2019-03", "2019-03"),
        ("03 / 2019", "2019-03", "2019-03"),
        ("2019-03", "2019-03", "2019-03"),
        ("2019/03", "2019-03", "2019-03"),
        ("2019.03", "2019-03", "2019-03"),
        ("March 2019", "2019-04", "2019"),  # another month
        ("03/2019", "2019-04", "2019"),
        ("2019-04", "2019-03", "2019"),
        ("led 3 2019 projects", "2019-03", "2019"),  # a count next to a year is no month
        ("3 2019", "2019-03", "2019"),
        ("2019 3", "2019-03", "2019"),
        ("2019-3", "2019-03", "2019"),  # one figure after the year is no month
        ("Q1 2019", "2019-03", "2019"),
        ("spring 2019", "2019-03", "2019"),
        ("Marzo 2019", "2019-03", "2019"),  # a month of a third language
        ("March-2019", "2019-03", "2019"),
        ("March 2018 to 2019", "2019-03", "2019"),
        ("03/19", "2019-03", None),  # a two digit year is not read
        ("in 2019", "2019-03", "2019"),
        ("20190", "2019", None),
        ("٢٠١٩", "2019", None),  # Arabic-Indic figures are not taken for 2019
        ("", "2019-03", None),
    ],
)
def test_a_month_counts_only_next_to_its_year(text: str, stored: str, kept: str | None) -> None:
    assert upload.period(stored, upload._read(text)) == kept


def _fixed(**parts: Any) -> c.Fixed:
    return c.Fixed.model_validate(parts)


def test_the_fixed_block_keeps_what_the_text_states_and_records_the_rest() -> None:
    fixed = _fixed(
        employers=[
            {
                "key": "quillmere",
                "organisation": "Quillmere Example Works",
                "title": "Senior Platform Engineer",
                "start": "2023-02",
                "end": None,
                "location": "Nantes",
            },
            {
                "key": "globex",
                "organisation": "Globex Example Systems",
                "title": "Engineer",
                "start": "2019",
            },
            {
                "key": "tarnwick",
                "organisation": "Tarnwick Sample Studio",
                "title": "Software Developer",
                "start": "2021-03",
                "end": "2023-01",
                "location": "Lyon",
            },
        ],
        schools=[
            {"school": "L'École Exemple d'Ingénieurs", "degree": "Master", "start": "2016"},
            {"school": "Example University", "degree": "BSc"},
        ],
        languages=[{"name": "English", "level": "native"}, {"name": "German", "level": "fluent"}],
    )
    checked = upload.check_fixed(fixed, TEXT)
    assert [e.organisation for e in checked.fixed.employers] == [
        "Quillmere Example Works",
        "Tarnwick Sample Studio",
    ]
    first, second = checked.fixed.employers
    assert (first.title, first.start, first.end, first.location) == ("", "2023-02", None, "Nantes")
    assert (second.title, second.start, second.end, second.location) == (
        "Software Developer",
        "2021",
        "2023-01",
        "",
    )
    assert [(s.school, s.degree) for s in checked.fixed.schools] == [
        ("L'École Exemple d'Ingénieurs", "")
    ]
    assert [(x.name, x.level) for x in checked.fixed.languages] == [("English", "")]
    assert checked.dropped_keys == {"globex"}
    assert [(r.field_path, r.before, r.after) for r in checked.corrections] == [
        ("fixed.employers.0.title", "Senior Platform Engineer", ""),
        ("fixed.employers.1.organisation", "Globex Example Systems", ""),
        ("fixed.employers.2.start", "2021-03", "2021"),
        ("fixed.employers.2.location", "Lyon", ""),
        ("fixed.schools.0.degree", "Master", ""),
        ("fixed.schools.1.school", "Example University", ""),
        ("fixed.languages.0.level", "native", ""),
        ("fixed.languages.1.name", "German", ""),
    ]
    assert all(r.by == "code" and r.code == upload.FIXED_NOT_IN_UPLOAD for r in checked.corrections)


def test_a_fixed_block_the_text_states_whole_is_untouched() -> None:
    fixed = _fixed(
        employers=[
            {
                "key": "quillmere",
                "organisation": "Quillmere Example Works",
                "title": "Platform Engineer",
                "start": "2023-02",
                "location": "Nantes",
            }
        ],
        languages=[{"name": "Français", "level": "langue maternelle"}],
    )
    checked = upload.check_fixed(fixed, TEXT)
    assert checked.fixed == fixed
    assert checked.corrections == ()


def test_apply_drops_the_experience_of_a_dropped_employer_and_records_its_bullets() -> None:
    data = {
        "headline": "Platform Engineer",
        "experience": [
            {"employer_key": "globex", "bullets": ["Designed the billing system.", 7]},
            {"employer_key": "quillmere", "bullets": ["Run the platform."]},
            "not an entry",
        ],
        "fixed": {
            "employers": [
                {"key": "globex", "organisation": "Globex Example Systems"},
                {"key": "quillmere", "organisation": "Quillmere Example Works"},
            ]
        },
    }
    applied = upload.apply(data, TEXT)
    assert applied.fixed is not None
    assert [e.key for e in applied.fixed.employers] == ["quillmere"]
    assert applied.data["experience"] == [
        {"employer_key": "quillmere", "bullets": ["Run the platform."]},
        "not an entry",
    ]
    assert [(r.field_path, r.before) for r in applied.corrections] == [
        ("fixed.employers.0.organisation", "Globex Example Systems"),
        ("experience.0.bullets.0", "Designed the billing system."),
    ]
    assert applied.data["headline"] == "Platform Engineer"


@pytest.mark.parametrize(
    "data",
    [
        None,
        "a text",
        ["a", "list"],
        {"fixed": {"employers": [{"key": "Not A Slug", "organisation": "X"}]}},
        {"fixed": "nothing"},
    ],
)
def test_apply_passes_what_is_not_a_base_cv_through_for_the_schema_check(data: Any) -> None:
    applied = upload.apply(data, TEXT)
    assert (applied.data, applied.fixed, applied.corrections) == (data, None, ())


@pytest.mark.parametrize("key", [["k"], {"k": 1}, 3, None])
def test_apply_passes_an_entry_whose_key_is_not_a_text_through(key: Any) -> None:
    data = {"fixed": {}, "experience": [{"employer_key": key, "bullets": ["x"]}]}
    applied = upload.apply(data, TEXT)
    assert applied.data["experience"] == data["experience"]


def test_apply_with_no_fixed_block_keeps_an_empty_one() -> None:
    applied = upload.apply({"headline": "x"}, TEXT)
    assert applied.fixed == c.Fixed()
    assert applied.data == {"headline": "x", "fixed": c.dump(c.Fixed())}


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Ingénieure plateforme. Responsable de la plateforme de déploiement.", Language.fr),
        ("Expérience professionnelle\nDepuis mars 2022, chez Brumeval.", Language.fr),
        ("Compétences : Kubernetes, Terraform, Python et PostgreSQL", Language.fr),
        (
            "Langues : Français, Anglais. Migration des services vers Kubernetes avec Terraform.",
            Language.fr,
        ),
        ("Développeuse Python pour les équipes de la finance", Language.fr),
        ("Je suis ingénieur, et mon équipe est sur Kubernetes", Language.fr),
        (
            "Platform engineer at Example Corp since 2023, and the owner of the platform.",
            Language.en,
        ),
        ("Skills: Kubernetes, Terraform, Python and PostgreSQL", Language.en),
        ("Built internal tools for the support team with React.", Language.en),
        ("Professional experience\nWe moved 30 services to Kubernetes.", Language.en),
        ("Languages: English, French. Responsable de la plateforme.", Language.en),  # a tie
        ("Kubernetes Terraform Python", Language.en),  # no function word
        ("", Language.en),
        ("   \n  ", Language.en),
        ("LE LA LES DES", Language.fr),
        ("THE AND OF", Language.en),
    ],
)
def test_the_language_of_a_text(text: str, language: Language) -> None:
    assert upload.language_of(text) is language


def test_the_largest_text_is_read_whole() -> None:
    # About 500,000 characters, the most ingestion stores; one fold and one cut, no wall clock.
    text = "Quillmere Example Works, Platform Engineer, 03/2019 to 2020-11. " * 8_000
    fixed = _fixed(
        employers=[
            {
                "key": f"k{i}",
                "organisation": f"Organisation number {i} Example",
                "title": "Platform Engineer",
                "start": "2019-03",
                "end": "2020-11",
            }
            for i in range(12)
        ]
        + [{"key": "quillmere", "organisation": "Quillmere Example Works", "start": "2019-03"}]
    )
    checked = upload.check_fixed(fixed, text)
    assert len(checked.dropped_keys) == 12
    assert checked.fixed.employers[0].start == "2019-03"
    assert upload.language_of(text) is Language.en
