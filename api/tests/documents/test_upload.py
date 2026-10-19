"""D24: what code reads of an uploaded CV's text (`app.documents.upload`), pure. The accept and
refuse tables of a name, a field and a date against the text, with their neighbours (case,
accents, punctuation, spacing, a longer name, a shorter one, a number inside a longer number,
another script, French and English months, two digit years), an employer's start kept open only
where the text writes it open (each written form of a start against each open and closed
neighbour) in an affirmative clause (each open form in each affirmative clause, and in each
negated, ended or unknown one, English and French), the fixed block and the experience entries
it drops, the language of a text, and the cost on the largest text."""

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


# Each written form of one start, with the value the reader stores for it.
STARTS = [
    ("2023", "2023"),
    ("02/2023", "2023-02"),
    ("2/2023", "2023-02"),
    ("02.2023", "2023-02"),
    ("2023-02", "2023-02"),
    ("2023/02", "2023-02"),
    ("February 2023", "2023-02"),
    ("Feb. 2023", "2023-02"),
    ("FEB 2023", "2023-02"),
    ("février 2023", "2023-02"),
    ("févr. 2023", "2023-02"),
    ("２０２３", "2023"),  # full width figures fold
]
WRITTEN_OPEN = [
    "{d} to present",
    "{d} To Present",
    "{d} – present",
    "{d} - present",
    "{d}-present",
    "{d} — Present",
    "{d} − present",
    "{d} -- present",
    "{d} –\npresent",
    "{d}   Present",
    "{d} until now",
    "{d} till today",
    "{d} through today",
    "{d} to date",
    "{d} – current",
    "{d} to now",
    "({d} – present)",
    "from {d} to present",
    "{d} à aujourd'hui",
    "{d} à aujourd’hui",
    "{d} à ce jour",
    "{d} à présent",
    "{d} jusqu'à aujourd'hui",
    "{d} jusqu’à ce jour",
    "{d} – aujourd'hui",
    "de {d} à aujourd'hui",
    "Since {d}",
    "since {d}, in Nantes",
    "SINCE {d}.",
    "Depuis {d}",
    "depuis {d}, Nantes",
    "Dès {d}",
    "From {d}",
    "Starting {d}",
]
WRITTEN_CLOSED = [
    "{d} to 2025",
    "{d} – 2025",
    "{d}-2025",
    "{d} to 06/25",  # a two digit end year
    "{d} – 06/25",
    "{d} to June 2025",
    "{d} à 2025",
    "{d} jusqu'en 2025",
    "from {d} to 2025",
    "From {d} until 06/25",
    "since {d} – 2025",
    "Depuis {d} jusqu'en 2025",
    "de {d} à 2025",
    "de {d}",  # "de" is as often "of"
    "{d}",
    "in {d}",
    "{d} –",  # a dash and nothing else: the cost of refusing when unsure
    "{d}\npresent",  # an open end on the next line with no dash or "to" before it
    "present\n{d}",
    "{d} to 2025\npresent",
    "{d}, present",
    "{d} (present)",
    "{d} a ce jour",  # "a" is not "à"
    "{d} date",
    "{d} – date",
    "{d} ongoing",
    "{d} à nos jours",
    "{d} to the present",
    "{d} presently",
    "Des {d}",  # "des" is not "dès"
    "Depuis {d} à Lyon",  # an "à" after a since date reads as an end
]


def _employer(text: str, start: str | None, end: str | None = None) -> tuple[Any, ...]:
    """The start and end kept of one employer of `text`, and the records made."""
    employer = {"key": "e", "organisation": "Example Corp", "title": "Engineer", "start": start}
    fixed = _fixed(employers=[{**employer, "end": end, "location": "Nantes"}])
    checked = upload.check_fixed(fixed, f"Example Corp, Engineer, Nantes\n{text}\n")
    (kept,) = checked.fixed.employers
    return kept.start, kept.end, [(r.field_path, r.before, r.after) for r in checked.corrections]


@pytest.mark.parametrize("form", WRITTEN_OPEN)
def test_a_start_the_text_writes_open_stays_ongoing(form: str) -> None:
    got = {written: _employer(form.format(d=written), stored) for written, stored in STARTS}
    assert got == {written: (stored, None, []) for written, stored in STARTS}


@pytest.mark.parametrize("form", WRITTEN_CLOSED)
def test_a_start_the_text_does_not_write_open_loses_its_period(form: str) -> None:
    got = {written: _employer(form.format(d=written), stored) for written, stored in STARTS}
    assert got == {
        written: (None, None, [("fixed.employers.0.start", stored, "")])
        for written, stored in STARTS
    }


@pytest.mark.parametrize(
    ("text", "start", "end", "kept"),
    [
        # The review's probes: an end the text does not state never makes a role current.
        ("2018 to 2020", "2018", "2021", (None, None)),
        ("2018 to 06/20", "2018", "2020-06", (None, None)),
        ("2018 to 2020", "2018", None, (None, None)),
        ("2018 to 2020\nPresent", "2018", None, (None, None)),
        ("2018 to 2020\nExample Labs, 2015 to present", "2018", None, (None, None)),
        # The reader ended it, the text does not say when: neither is current.
        ("2023 to present", "2023", "2026", (None, None)),
        # An end the text states keeps the period whole.
        ("2018 to 2020", "2018", "2020", ("2018", "2020")),
        ("03/2018 – 06/2020", "2018-03", "2020-06", ("2018-03", "2020-06")),
        ("2018 – 2020", "2018", "2020-06", ("2018", "2020")),
        # A month counts only when the open end follows that month.
        ("March 2023 to present\nFebruary 2023", "2023-02", None, (None, None)),
        ("March 2023 to present\nFebruary 2023", "2023", None, ("2023", None)),
        ("Since 2023", "2023-02", None, ("2023", None)),
        ("since ٢٠٢٣\n2023 to 2025", "2023", None, (None, None)),
        ("since 120230\n2023 to 2025", "2023", None, (None, None)),
        ("Since 2023 – present", "2023", None, ("2023", None)),
        # A name outside the fixed block is not affirmative.
        ("Example Labs, 2018 to present\n2018 to 2020", "2018", None, (None, None)),
        # Declared: an open end of another role with the same start keeps this one current.
        ("2018 to present\n2018 to 2020", "2018", None, ("2018", None)),
        ("", None, None, (None, None)),
    ],
)
def test_the_review_probes_of_an_open_start(
    text: str, start: str | None, end: str | None, kept: tuple[str | None, str | None]
) -> None:
    assert _employer(text, start, end)[:2] == kept


# The clauses around an open form `{f}`: each must keep the start of every written form open.
AFFIRMATIVE_CLAUSES = [
    "{f}",
    "Example Corp, {f}",
    "Example Corp, Engineer, Nantes, {f}",
    "Engineer at Example Corp, {f}",
    "Example Corp | Engineer | {f}",
    "I have worked at Example Corp {f}",
    "I have been working as an Engineer at Example Corp {f}",
    "I've been at Example Corp {f}",
    "I’m an Engineer at Example Corp {f}",
    "Still at Example Corp {f}",
    "Example Corp (full-time, remote), {f}",
    "Je travaille chez Example Corp {f}",
    "En poste chez Example Corp {f}",
    "Engineer chez Example Corp, en CDI, {f}",
    "Example Corp, Nantes, à temps plein, {f}",
    "{f} (2 years 8 months)",
    "{f} (2 ans 8 mois)",
    "{f}\nRun the deployment platform.",  # a line that starts with a capital is another clause
    "Not at Example Labs.\n\nExample Corp, {f}",
]
# The clauses that must not: a negation or an ending, before or after the form, English and
# French, any case or apostrophe, and words outside the affirmative vocabulary and the names.
REFUSING_CLAUSES = [
    "I have not worked at Example Corp {f}",
    "I HAVE NOT WORKED AT EXAMPLE CORP {f}",
    "I haven't worked at Example Corp {f}",
    "I haven’t been at Example Corp {f}",
    "I have never worked at Example Corp {f}",
    "Not at Example Corp {f}",
    "No longer at Example Corp {f}",
    "no longer employed at Example Corp, {f}",
    "Example Corp, not employed {f}",
    "Example Corp, not an Engineer {f}",
    "Example Corp, Engineer, not anymore, {f}",
    "Former Engineer at Example Corp, {f}",
    "Formerly at Example Corp {f}",
    "Ex-Engineer at Example Corp {f}",
    "Example Corp, Engineer, previously {f}",
    "I left Example Corp {f}",
    "Quit Example Corp {f}",
    "Retired from Example Corp {f}",
    "Example Corp, without a job {f}",
    "{f}, I have not worked at Example Corp",
    "{f}, no longer at Example Corp",
    "Example Corp {f} (not anymore)",
    "{f}\nnot at Example Corp anymore",  # a line that starts lower case goes on the clause
    "Je ne travaille plus chez Example Corp {f}",
    "JE NE TRAVAILLE PLUS CHEZ EXAMPLE CORP {f}",
    "Je n'ai pas travaillé chez Example Corp {f}",
    "Je n’y suis plus {f}",
    "Plus chez Example Corp {f}",
    "Example Corp, plus en poste {f}",
    "Jamais en poste chez Example Corp {f}",
    "Ancien Engineer chez Example Corp {f}",
    "Anciennement chez Example Corp {f}",
    "J'ai quitté Example Corp {f}",
    "Example Corp, Engineer, poste terminé {f}",
    "Sans emploi {f}",
    "{f}, je ne suis plus chez Example Corp",
    "Unemployed {f}",
    "Example Corp, on leave {f}",
    "Example Corp, closed {f}",
    "Example Corp, en congé {f}",
    "Example Corp, Engineer, Lyon, {f}",  # a location the reader did not keep
    "Morgan Exampleton, Engineer at Example Corp {f}",
    "Example Corp, {f}, run the deployment platform",
]
ONE_LINE_OPEN = [form for form in WRITTEN_OPEN if "\n" not in form]


@pytest.mark.parametrize("clause", AFFIRMATIVE_CLAUSES)
def test_an_open_form_in_an_affirmative_clause_stays_ongoing(clause: str) -> None:
    got = {
        (form, written): _employer(clause.format(f=form.format(d=written)), stored)
        for form in ONE_LINE_OPEN
        for written, stored in STARTS
    }
    assert got == {
        (form, written): (stored, None, []) for form in ONE_LINE_OPEN for written, stored in STARTS
    }


@pytest.mark.parametrize("clause", REFUSING_CLAUSES)
def test_an_open_form_in_a_negated_ended_or_unknown_clause_loses_its_period(clause: str) -> None:
    got = {
        (form, written): _employer(clause.format(f=form.format(d=written)), stored)
        for form in ONE_LINE_OPEN
        for written, stored in STARTS
    }
    assert got == {
        (form, written): (None, None, [("fixed.employers.0.start", stored, "")])
        for form in ONE_LINE_OPEN
        for written, stored in STARTS
    }


# Every input a review quoted, with the employer it used: never deleted.
@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("I have not worked at Example Corp since 2018", None),
        ("No longer at Example Corp since 2018", None),
        ("Example Corp, not employed since 2018", None),
        ("Je ne travaille plus chez Example Corp depuis 2018", None),
        ("I have worked at Example Corp since 2018", "2018"),
        ("Example Corp, Since 2018", "2018"),
        ("Example Corp, Depuis 2018", "2018"),
        ("Example Corp, 2018 to present", "2018"),
        # Their neighbours over a line break: a line that starts lower case goes on the clause.
        ("I have not worked at Example Corp\nsince 2018", None),
        ("Je ne travaille plus chez Example Corp\ndepuis 2018", None),
        ("Since 2018, I have\nnot worked at Example Corp", None),
        ("Example Corp\nsince 2018", "2018"),
        ("Example Corp\nSince 2018", "2018"),
        # Declared: a line that starts with a capital is another clause.
        ("Example Corp\n\nno longer there\nSince 2018", "2018"),
        ("I have not worked at Example Corp\nSince 2018", "2018"),
        ("Je ne travaille plus chez\nExample Corp depuis 2018", "2018"),
    ],
)
def test_the_round_3_review_probes_of_a_negated_open_start(text: str, kept: str | None) -> None:
    fixed = _fixed(employers=[{"key": "example", "organisation": "Example Corp", "start": "2018"}])
    (employer,) = upload.check_fixed(fixed, text).fixed.employers
    assert (employer.start, employer.end) == (kept, None)


@pytest.mark.parametrize(
    ("employers", "text", "kept"),
    [
        # The names that make a clause affirmative are those the text states.
        ([{"location": "Nantes"}], "Example Corp, Nantes, since 2018", "2018"),
        ([{"location": "Rennes"}], "Example Corp, Nantes, since 2018", None),
        ([{"title": "Lead Engineer"}], "Lead at Example Corp since 2018", None),
        # A negation or an ending a name holds still refuses the clause: the stated cost.
        ([{"organisation": "Example Plus"}], "Example Plus, depuis 2018", None),
        ([{"organisation": "Example Plus"}], "Example Plus, 2018 to present", None),
        ([{"organisation": "No Example Corp"}], "No Example Corp, since 2018", None),
        # Declared: another employer's names make the clause of the same start affirmative.
        ([{}, {"key": "labs", "organisation": "Example Labs"}], "Example Labs, since 2018", "2018"),
    ],
)
def test_the_names_of_an_affirmative_clause(
    employers: list[dict[str, Any]], text: str, kept: str | None
) -> None:
    first = {"key": "example", "organisation": "Example Corp", "start": "2018"}
    fixed = _fixed(employers=[{**first, **employers[0]}, *employers[1:]])
    checked = upload.check_fixed(fixed, f"{text}\nExample Corp\n")
    assert (checked.fixed.employers[0].start, checked.fixed.employers[0].end) == (kept, None)


def test_a_dropped_end_records_the_start_it_takes_and_one_record_per_field() -> None:
    assert _employer("03/2019 to 2020", "2019-03", "2021-04") == (
        None,
        None,
        [
            ("fixed.employers.0.start", "2019-03", ""),
            ("fixed.employers.0.end", "2021-04", ""),
        ],
    )


def test_a_school_without_an_end_keeps_its_start() -> None:
    fixed = _fixed(schools=[{"school": "Example Institute", "start": "2016"}])
    checked = upload.check_fixed(fixed, "Example Institute, 2016 to 2018")
    assert (checked.fixed.schools[0].start, checked.fixed.schools[0].end) == ("2016", None)
    assert checked.corrections == ()


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
        + [
            {
                "key": "quillmere",
                "organisation": "Quillmere Example Works",
                "start": "2019-03",
                "end": "2020-11",
            }
        ]
    )
    checked = upload.check_fixed(fixed, text)
    assert len(checked.dropped_keys) == 12
    assert (checked.fixed.employers[0].start, checked.fixed.employers[0].end) == (
        "2019-03",
        "2020-11",
    )
    assert upload.language_of(text) is Language.en


@pytest.mark.parametrize(
    ("unit", "kept"),
    [
        ("Example Corp, Engineer, since 2018\n", "2018"),
        ("I have not worked at Example Corp since 2018\n", None),
        # One clause of about 500,000 characters: its other years make each period unsure.
        ("Example Corp, Engineer, since 2018, ", None),
    ],
)
def test_the_largest_text_of_open_forms_is_read_whole(unit: str, kept: str | None) -> None:
    fixed = _fixed(
        employers=[
            {"key": "e", "organisation": "Example Corp", "title": "Engineer", "start": "2018"}
        ]
    )
    checked = upload.check_fixed(fixed, unit * (500_000 // len(unit)))
    assert checked.fixed.employers[0].start == kept
