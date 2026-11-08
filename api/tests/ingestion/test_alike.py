"""Rules A and B of the merge (`app/ingestion/alike.py`): when two values documents give are one
fact. Pure, no database. Invented names only (Jane-Ann Doe, Example Robotics)."""

from typing import Any

import pytest

from app.ingestion import alike, items

EMPLOYERS = items.SLOTS["experience"]["employers"]
EDUCATION = items.SLOTS["experience"]["education"]
LANGUAGES = items.SLOTS["identity"]["languages"]
HAVE = items.SLOTS["skills"]["have"]


def clean(slot: items.Slot, value: Any) -> Any:
    return items.build(slot.section, slot.name, "add", value).value


def job(organisation: str, start: str, end: str | None, **more: Any) -> dict[str, Any]:
    value = {"organisation": organisation, "title": "Engineer", "start": start, "end": end}
    return clean(EMPLOYERS, {**value, **more})


def school(name: str, start: str | None = None, end: str | None = None, **more: Any) -> Any:
    value = {"school": name, "degree": "Master", "start": start, "end": end}
    return clean(EDUCATION, {**value, **more})


# --- fold -----------------------------------------------------------------------------------------

FOLD_ALIKE = [
    ("Jane-Ann Doe", "JANE-ANN DOE"),
    ("Jane-Ann Doe", "Jane Ann Doe"),
    ("ITECHNOVA - Institute of Applied Computing", "ITECHNOVA – Institute of Applied Computing"),
    ("ITECHNOVA — Applied", "itechnova applied"),
    ("React Native", "react-native"),
    ("Société Générale", "SOCIETE GENERALE"),
    ("Node.js", "node js"),
    ("GenAI Engineer", "GenAI engineer."),
    ("  Example   Robotics ", "Example Robotics"),
    ("CI/CD", "ci cd"),
    ("Français", "Français"),
    ("l'IA générative", "l’IA generative"),
]
FOLD_APART = [
    ("C#", "C"),
    ("C++", "C"),
    ("AT&T", "AT T"),
    ("3.5", "35"),
    ("Node.js", "Nodejs"),
    ("Example Robotics", "Example Robotic"),
    ("Jane Doe", "Jane-Ann Doe"),
    ("", "x"),
]


@pytest.mark.parametrize(("first", "second"), FOLD_ALIKE)
def test_texts_that_fold_alike(first: str, second: str) -> None:
    assert alike.fold(first) == alike.fold(second)


@pytest.mark.parametrize(("first", "second"), FOLD_APART)
def test_texts_that_stay_apart(first: str, second: str) -> None:
    assert alike.fold(first) != alike.fold(second)


# --- phones ---------------------------------------------------------------------------------------

SAME_PHONES = [
    ("06 12 34 56 78", "+33 6 12 34 56 78"),
    ("0033 6 12 34 56 78", "+33 6 12 34 56 78"),
    ("0033612345678", "06.12.34.56.78"),
    ("+33 (0)6 12 34 56 78", "06 12 34 56 78"),
    ("+33 6 12 34 56 78", "+336 12 34 56 78"),
    ("+1 555 123 4567", "(555) 123-4567"),
    ("+39 06 1234 5678", "06 1234 5678"),
    ("+44 20 7946 0958", "020 7946 0958"),
]
OTHER_PHONES = [
    ("06 12 34 56 78", "06 12 34 56 79"),
    ("+33 6 12 34 56 78", "+44 6 12 34 56 78"),
    ("06 12 34 56 78", "+33 7 12 34 56 78"),
    ("12 34", "+33 12 34"),
    ("06 12 34 56 78", "+33612345678 9"),
    ("+33 6 12 34 56 78", "1234 06 12 34 56 78"),
    ("06 12", "06 12"),
]


@pytest.mark.parametrize(("first", "second"), SAME_PHONES)
def test_one_phone_written_two_ways(first: str, second: str) -> None:
    assert alike.same_phone(first, second)
    assert alike.same_phone(second, first)


@pytest.mark.parametrize(("first", "second"), OTHER_PHONES)
def test_two_phones(first: str, second: str) -> None:
    assert not alike.same_phone(first, second)


# --- rule B: one entry ----------------------------------------------------------------------------

SAME = [
    (EMPLOYERS, job("NOVACARE", "2019-03", "2021-06"), job("NOVACARE GIE", "2019-03", "2021-06")),
    (EMPLOYERS, job("NOVACARE", "2019", "2021-06"), job("NOVACARE GIE", "2019-03", "2021")),
    (EMPLOYERS, job("Example Robotics", "2021", None), job("EXAMPLE ROBOTICS", "2021-03", None)),
    (EMPLOYERS, job("Acme", "2020-01", "2021-01"), job("Acme Group", "2020-01", "2021-01")),
    (
        EDUCATION,
        school("ITECHNOVA - Institute of Applied Computing", "2015-10", "2018-06"),
        school("ITECHNOVA – Institute of Applied Computing", "2015", "2018"),
    ),
    (
        EDUCATION,
        school("Université de Valmont Haute-Rive", "2011", "2015"),
        school("University of Valmont Haute-Rive", "2011", "2015"),
    ),
    (
        EDUCATION,
        school("University of Valmont Haute-Rive", "2011"),
        school("Université de Valmont Haute-Rive", "2011-09", "2015"),
    ),
    (
        EDUCATION,
        school("Lycée Jean Morvel Exempleville", "2009", "2011"),
        school("Lycée Jean Morvel", "2009", "2011"),
    ),
    # the same name: a missing year agrees with any
    (EDUCATION, school("Lycée Jean Morvel"), school("LYCÉE JEAN MORVEL", "2009", "2011")),
    (EDUCATION, school("Lycée Jean-Morvel", None, "2011"), school("Lycée Jean Morvel", "2009")),
    (
        EDUCATION,
        school("ITECHNOVA", "2015"),
        school("ITECHNOVA Institute of Applied", "2015-10", "2018"),
    ),
    (
        EDUCATION,
        school("Université de Valmont", "2011", "2015"),
        school("Université de Valmont Haute-Rive", "2011", "2015"),
    ),
    (LANGUAGES, clean(LANGUAGES, {"name": "English"}), clean(LANGUAGES, {"name": "Anglais"})),
    (LANGUAGES, clean(LANGUAGES, {"name": "French"}), clean(LANGUAGES, {"name": "Français"})),
    (LANGUAGES, clean(LANGUAGES, {"name": "Espagnol"}), clean(LANGUAGES, {"name": "Español"})),
    (LANGUAGES, clean(LANGUAGES, {"name": "German"}), clean(LANGUAGES, {"name": "Deutsch"})),
    (LANGUAGES, clean(LANGUAGES, {"name": "Russian"}), clean(LANGUAGES, {"name": "Русский"})),
    (LANGUAGES, clean(LANGUAGES, {"name": "Klingon"}), clean(LANGUAGES, {"name": "KLINGON"})),
    (
        HAVE,
        clean(HAVE, {"name": "React Native", "terms": []}),
        clean(HAVE, {"name": "react-native", "terms": []}),
    ),
]
APART = [
    # another start, another end, or one end missing: two positions
    (EMPLOYERS, job("NOVACARE", "2019-03", "2021-06"), job("NOVACARE GIE", "2019-04", "2021-06")),
    (EMPLOYERS, job("NOVACARE", "2019-03", None), job("NOVACARE GIE", "2019-03", "2021-06")),
    (EMPLOYERS, job("NOVACARE", "2019-03", "2021-06"), job("NOVACARE", "2020-03", "2021-06")),
    (EMPLOYERS, job("Acme Group", "2020", "2021"), job("Group Acme", "2020", "2021")),
    (EMPLOYERS, job("NOVACARE", "2019", "2020"), job("NOVACARELLA", "2019", "2020")),
    (EMPLOYERS, job("Globex", "2023", "2024"), job("Example Robotics", "2023", "2024")),
    (EDUCATION, school("Université de Lyon", "2011"), school("Université de Valmont", "2011")),
    (EDUCATION, school("École Centrale de Lyon", "2018"), school("Université Lyon 1", "2018")),
    (EDUCATION, school("Paris School of Business"), school("Business School of Lille")),
    (
        EDUCATION,
        school("University of Valmont Haute-Rive", "2011"),
        school("University of Valmont Haute-Rive", "2012"),
    ),
    (EDUCATION, school("Example High School", None, "2015"), school("Example High", None, "2016")),
    # names that differ are one school only over the same years: a start or an end on both sides
    (
        EDUCATION,
        school("Lycée Jean Morvel Exempleville", "2009", "2011"),
        school("Lycée Jean Morvel"),
    ),
    (
        EDUCATION,
        school("ITECHNOVA", "2015"),
        school("ITECHNOVA Institute of Applied", None, "2018"),
    ),
    (
        EDUCATION,
        school("Université de Valmont Haute-Rive", "2011", "2015"),
        school("University of Valmont Haute-Rive"),
    ),
    (
        EDUCATION,
        school("Université de Valmont Haute-Rive", "2011"),
        school("University of Valmont Haute-Rive", None, "2015"),
    ),
    (
        EDUCATION,
        school("Université de Valmont"),
        school("Université de Valmont Haute-Rive", "2011", "2015"),
    ),
    (EDUCATION, school("New York University"), school("City University of New York", "2010")),
    (
        EDUCATION,
        school("New York University", "2010", "2014"),
        school("City University of New York", "2012", "2014"),
    ),
    (EDUCATION, school("ENS"), school("ENS Paris-Saclay", "2015", "2017")),
    (EDUCATION, school("ENS Paris"), school("ENS Paris-Saclay", "2015", "2017")),
    (
        EDUCATION,
        school("École normale supérieure de Paris", None, "2014"),
        school("École normale supérieure Paris-Saclay", "2015", "2017"),
    ),
    (EDUCATION, school("Lycée Saint-Ardouin"), school("Université Saint-Ardouin", "2011", "2015")),
    # the distinctive words of the shorter name are not all in the longer one, or fewer than two
    (EDUCATION, school("UC San Diego"), school("UC San Francisco", "2010", "2014")),
    (EDUCATION, school("UC San Diego", "2010", "2014"), school("UC San Francisco", "2010", "2014")),
    (
        EDUCATION,
        school("University of California, San Diego", "2010", "2014"),
        school("University of California, San Francisco", "2010", "2014"),
    ),
    (EDUCATION, school("Université Valmont 1", "2011"), school("Université Valmont 2", "2011")),
    (EDUCATION, school("Université de Lyon", "2011"), school("École Centrale de Lyon", "2011")),
    (EDUCATION, school("Institut de Valmont", "2011"), school("Valmont Business School", "2011")),
    (LANGUAGES, clean(LANGUAGES, {"name": "English"}), clean(LANGUAGES, {"name": "French"})),
    (LANGUAGES, clean(LANGUAGES, {"name": "Klingon"}), clean(LANGUAGES, {"name": "Elvish"})),
    (
        HAVE,
        clean(HAVE, {"name": "React", "terms": []}),
        clean(HAVE, {"name": "React Native", "terms": []}),
    ),
]


@pytest.mark.parametrize(("slot", "first", "second"), SAME)
def test_one_entry_under_two_keys(slot: items.Slot, first: Any, second: Any) -> None:
    one, other = alike.shape(slot, first), alike.shape(slot, second)
    assert alike.same_entry(slot, one, other)
    assert alike.same_entry(slot, other, one)


@pytest.mark.parametrize(("slot", "first", "second"), APART)
def test_two_entries(slot: items.Slot, first: Any, second: Any) -> None:
    assert not alike.same_entry(slot, alike.shape(slot, first), alike.shape(slot, second))


def test_only_the_slots_of_rule_b_are_matched_by_more_than_their_key() -> None:
    matched = {
        (slot.section, slot.name)
        for section in items.SLOTS.values()
        for slot in section.values()
        if alike.matched(slot)
    }
    assert matched == {
        ("experience", "employers"),
        ("experience", "education"),
        ("identity", "languages"),
        ("skills", "have"),
        ("skills", "adjacent"),
        ("skills", "none"),
    }


# --- rule A: one value ----------------------------------------------------------------------------

FULL_NAME = items.SLOTS["identity"]["full_name"]
PHONE = items.SLOTS["identity"]["phone"]
EMAIL = items.SLOTS["identity"]["email"]
WEBSITE = items.SLOTS["identity"]["website"]
HEADLINE = items.SLOTS["identity"]["headline"]
TITLES = items.SLOTS["search"]["title_include"]
CONTRACTS = items.SLOTS["search"]["contract_types"]

COMBINED = [
    (FULL_NAME, ["Jane-Ann Doe", "JANE-ANN DOE"], "Jane-Ann Doe"),
    (FULL_NAME, ["JANE-ANN DOE", "Jane-Ann Doe"], "Jane-Ann Doe"),
    (FULL_NAME, ["JANE DOE", "Jane Doe", "jane doe"], "Jane Doe"),
    (FULL_NAME, ["JANE DOE", "JANE DOE"], "JANE DOE"),
    (PHONE, ["06 12 34 56 78", "+33 6 12 34 56 78"], "+33 6 12 34 56 78"),
    (PHONE, ["0033 6 12 34 56 78", "06 12 34 56 78"], "0033 6 12 34 56 78"),
    (EMAIL, ["jane.doe@example.org", "Jane.Doe@example.org"], "jane.doe@example.org"),
    (WEBSITE, ["https://www.example.org/", "example.org"], "https://www.example.org/"),
    (HEADLINE, ["GenAI Engineer", "GENAI ENGINEER."], "GenAI Engineer"),
    (
        TITLES,
        [{"terms": ["Data Engineer"]}, {"terms": ["data-engineer", "ML Engineer"]}],
        {"terms": ["Data Engineer", "ML Engineer"]},
    ),
    (CONTRACTS, [["CDI"], ["CDI", "Freelance"]], ["CDI", "Freelance"]),
    (
        EMPLOYERS,
        [
            job("Example Robotics", "2021-03", "2023-06", location=None),
            job("Example Robotics", "2021-03", "2023-06", location="Port-Exemple, France"),
        ],
        job("Example Robotics", "2021-03", "2023-06", location="Port-Exemple, France"),
    ),
    (
        EMPLOYERS,
        [job("NOVACARE", "2019-03", "2021-06"), job("NOVACARE GIE", "2019-03", "2021-06")],
        job("NOVACARE GIE", "2019-03", "2021-06"),
    ),
    (
        EDUCATION,
        [school("Lycée Jean Morvel Exempleville", "2009", "2011"), school("Lycée Jean Morvel")],
        school("Lycée Jean Morvel Exempleville", "2009", "2011"),
    ),
    (
        HAVE,
        [
            clean(HAVE, {"name": "Rust", "terms": ["Rust"]}),
            clean(HAVE, {"name": "Rust", "terms": []}),
        ],
        clean(HAVE, {"name": "Rust", "terms": ["Rust"]}),
    ),
    (
        HAVE,
        [
            clean(HAVE, {"name": "Languages", "terms": ["Python", "Rust"]}),
            clean(HAVE, {"name": "languages", "terms": ["python", "Go"]}),
        ],
        clean(HAVE, {"name": "Languages", "terms": ["Python", "Rust", "Go"]}),
    ),
    (
        LANGUAGES,
        [
            clean(LANGUAGES, {"name": "English"}),
            clean(LANGUAGES, {"name": "Anglais", "level": "C1"}),
        ],
        clean(LANGUAGES, {"name": "English", "level": "C1"}),
    ),
]
DISAGREE = [
    (HEADLINE, ["GenAI Engineer", "Software engineer passionate about generative AI"]),
    (EMAIL, ["jane-doe@example.org", "jane.doe@example.org"]),
    (PHONE, ["06 12 34 56 78", "06 12 34 56 79"]),
    (
        EMPLOYERS,
        [
            job("Example Robotics", "2021", None, title="Self-employed"),
            job("Example Robotics", "2021", None, title="Auto-entrepreneur"),
        ],
    ),
    (EMPLOYERS, [job("Example Robotics", "2021", None), job("Example Robotics", "2021-03", None)]),
    (
        EDUCATION,
        [
            school("ITECHNOVA - Institute of Applied Computing", "2015-10", "2018-06"),
            school("ITECHNOVA – Institute of Applied Computing", "2015", "2018"),
        ],
    ),
    (
        EDUCATION,
        [
            school("Université de Valmont Haute-Rive", "2011", "2015"),
            school("University of Valmont Haute-Rive", "2011", "2015"),
        ],
    ),
    (
        LANGUAGES,
        [
            clean(LANGUAGES, {"name": "English", "level": "C1"}),
            clean(LANGUAGES, {"name": "Anglais", "level": "Courant"}),
        ],
    ),
]


@pytest.mark.parametrize(("slot", "values", "expected"), COMBINED)
def test_values_that_agree_become_one(slot: items.Slot, values: list[Any], expected: Any) -> None:
    got = alike.combine(slot, values)
    assert got == expected
    # it is still a value of the section model
    assert items.build(slot.section, slot.name, "add", got).value == got


@pytest.mark.parametrize(("slot", "values"), DISAGREE)
def test_values_that_disagree_stay_apart(slot: items.Slot, values: list[Any]) -> None:
    assert alike.combine(slot, values) is None


def test_what_the_profile_holds_keeps_its_wording_where_it_agrees() -> None:
    assert alike.combine(PHONE, ["06 12 34 56 78", "+33 6 12 34 56 78"], base=True) == (
        "06 12 34 56 78"
    )
    held = {**job("NOVACARE", "2019-03", "2021-06"), "key": "novacare"}
    got = alike.combine(
        EMPLOYERS,
        [held, job("NOVACARE GIE", "2019-03", "2021-06", location="Port-Exemple")],
        base=True,
    )
    assert got == {**held, "location": "Port-Exemple"}
    assert alike.combine(
        LANGUAGES, [{"name": "Anglais", "level": None}, {"name": "English"}], base=True
    ) == {
        "name": "Anglais",
        "level": None,
    }
