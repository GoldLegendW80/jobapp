"""A stated number of years of experience (`citations.years_of`, the field type `years`): a figure
a document writes in digits or in English or French words is checked against the value
`skills.years_experience` an agent proposes. Pure, no database; invented sentences only."""

import pytest

from app.ingestion import citations, items
from app.ingestion.citations import FieldType, Rule

STATED = [
    ("Il a 5 ans d'expérience", 5),  # "a" is the verb, not "à"
    ("3.5 years of software engineering", 3.5),
    ("3,5 ans d'expérience en développement", 3.5),
    ("trois ans et demi d'ingénierie logicielle", 3.5),
    ("Trois années et demie de développement", 3.5),
    ("three and a half years building data platforms", 3.5),
    ("Three years and a half in robotics", 3.5),
    ("5+ years of Python", 5),
    ("5 + years in production systems", 5),
    ("Over 10 years of experience", 10),
    ("more than 7 years in the field", 7),
    ("plus de 5 ans dans la santé", 5),
    ("at least 4 yrs of backend work", 4),
    ("2 yr of consulting", 2),
    ("un an et demi en alternance", 1.5),
    ("one year at a startup", 1),
    ("twenty five years of teaching", 25),
    ("twenty-five years of teaching", 25),
    ("vingt-cinq ans de métier", 25),
    ("vingt et un ans de carrière", 21),
    ("dix-sept ans de pratique", 17),
    ("quinze ans d'expérience", 15),
    ("5ans d'expérience", 5),
    ("8yrs experience", 8),
    ("3 years and 6 months of support", 3.5),
    ("3 ans et 6 mois de support", 3.5),
    ("3 years and 4 months of support", 3.3),
    ("SIX YEARS OF EXPERIENCE", 6),
    ("Expérience : 12 années", 12),
    ("experience : 4 ans", 4),
    # what follows says what the years were spent on, or a label or a duration word stands before
    ("Expérience professionnelle : 8 ans", 8),
    ("Total experience: 6 years", 6),
    ("I have worked in robotics for 5 years.", 5),
    ("Je travaille dans la robotique depuis 5 ans.", 5),
    ("Over the past 5 years, I built data platforms", 5),
    ("5 years' experience in Python", 5),
    ("5 years hands-on experience", 5),
    ("5 years professional experience", 5),
    ("5 years as a data engineer", 5),
    ("5 years leading teams of engineers", 5),
    ("5 years with Example Robotics", 5),
    ("5 ans chez Example Robotics", 5),
    ("5 ans au sein d'Example Robotics", 5),
    ("5 ans en tant que développeuse", 5),
    ("5 ans comme ingénieure", 5),
    ("3 ans et 6 mois chez Example Robotics", 3.5),
    ("2,5 ans d'expérience", 2.5),
    ("3.75 years of support", 3.75),
    ("at least 5 years of experience", 5),
    ("au moins 5 ans d'expérience", 5),
    ("10 years in the field, 3 of them in robotics", 10),
]
NOT_STATED = [
    # Round 3 of the review of #762: a French age with "à", and a bound after a comma.
    ("j'ai rejoint la société à 25 ans comme stagiaire", 25),
    ("À 22 ans, premier poste en alternance", 22),
    ("5 years experience, at most", 5),
    ("5 years of experience (at most)", 5),
    ("5 ans d'expérience, au plus", 5),
    ("3 years of Python, or less", 3),
    ("less than 2 years of experience", 2),
    ("fewer than 3 years", 3),
    ("up to 5 years", 5),
    ("moins de 2 ans", 2),
    ("près de 4 ans", 4),
    ("presque 4 ans", 4),
    ("almost 4 years", 4),
    ("nearly 4 years", 4),
    ("under 3 years", 3),
    ("jusqu'à 5 ans", 5),
    ("not 5 years", 5),
    ("3 to 5 years", 5),
    ("3-5 years", 5),
    ("3 – 5 ans", 5),
    ("3 à 5 ans", 5),
    ("3 or 4 years", 4),
    ("a team of 5 engineers", 5),
    ("5 projects shipped in 2 months", 5),
    ("2019 to 2023 at Example Robotics", 4),
    ("twenty five years of teaching", 5),
    ("dix-sept ans de pratique", 7),
    ("3.5 years", 35),
    ("3 years and 6 months", 3),
    ("65 years of tradition", 65),
    ("0 years", 0),
    ("years of experience: many", 1),
    # a bound above, before the figure or after it, in English and in French
    ("at most 2 years of experience", 2),
    ("AT MOST two years of experience", 2),
    ("no more than 2 years of experience", 2),
    ("not more than 2 years in the field", 2),
    ("No more than 5+ years of Python", 5),
    ("a maximum of 2 years of experience", 2),
    ("maximum 2 years of experience", 2),
    ("max 2 years of experience", 2),
    ("max. 2 years of experience", 2),
    ("up to 5+ years of experience", 5),
    ("up to five years of experience", 5),
    ("within 2 years of joining", 2),
    ("below 3 years of experience", 3),
    ("short of 5 years of experience", 5),
    ("< 5 years of experience", 5),
    ("≤ 5 years of experience", 5),
    ("close to 5 years of experience", 5),
    ("pas plus de 2 ans d'expérience", 2),
    ("Pas plus de deux ans d'expérience", 2),
    ("au plus 2 ans d'expérience", 2),
    ("tout au plus 2 ans d'expérience", 2),
    ("au maximum 2 ans d'expérience", 2),
    ("jusqu'à 5 ans d'expérience", 5),
    ("JUSQU'À CINQ ANS D'EXPÉRIENCE", 5),
    ("inférieure à 3 ans d'expérience", 3),
    ("Je n'ai pas 5 ans d'expérience", 5),
    ("2 years of experience at most", 2),
    ("2 years of experience at the most", 2),
    ("5 ans d'expérience maximum", 5),
    ("5 ans maximum", 5),
    ("5 years max", 5),
    ("5 years or less", 5),
    ("5 ans ou moins", 5),
    ("5 ans au plus", 5),
    ("deux ans d'expérience tout au plus", 2),
    ("5 ans d'expérience au grand maximum", 5),
    # a range, by its two ends
    ("between 3 and 5 years", 5),
    ("between 3 and 5 years of experience", 3),
    ("Between three and five years of experience", 5),
    ("between 3 years and 5 years of experience", 3),
    ("between 3 years and 5 years of experience", 5),
    ("entre 3 et 5 ans", 5),
    ("entre 3 et 5 ans d'expérience", 3),
    ("entre trois et cinq ans d'expérience", 5),
    ("from 3 years to 5 years of experience", 3),
    ("from 3 years to 5 years of experience", 5),
    ("de 3 ans à 5 ans d'expérience", 3),
    ("de 3 ans à 5 ans d'expérience", 5),
    ("3 ou 4 ans d'expérience", 4),
    ("3/5 years of experience", 5),
    ("3 and 5 years of experience", 5),
    # a point in time, an age
    ("5 years ago", 5),
    ("founded 30 years ago", 30),
    ("il y a 5 ans", 5),
    ("Il y a cinq ans, j'ai rejoint Example Robotics", 5),
    ("Il y a 5 ans de cela", 5),
    ("five years later", 5),
    ("cinq ans plus tard", 5),
    ("5 years after graduating", 5),
    ("5 ans auparavant", 5),
    ("in 5 years", 5),
    ("dans 5 ans", 5),
    ("Jean Exemple, 25 ans", 25),
    ("25 ans", 25),
    ("aged 25", 25),
    ("aged 25 years", 25),
    ("I am 25", 25),
    ("I am 25 years old", 25),
    ("I'm 25 years old", 25),
    ("25 years of age", 25),
    ("Âge : 25 ans", 25),
    ("âgée de 25 ans", 25),
    ("J'ai 25 ans.", 25),
    ("Jean Exemple\n25 ans\nExpérience : 3 ans", 25),
    # a thousands separator is not a decimal sign
    ("1,000 years of history", 1),
    ("1.000 ans d'histoire", 1),
    ("1 000 ans d'histoire", 1),
    ("5,000+ years of history", 5),
    # nothing after the years and no label before: not shown to be years held
    ("Python: 5 years", 5),
    ("5 years and counting", 5),
]


@pytest.mark.parametrize(("text", "figure"), STATED)
def test_a_stated_figure_is_read(text: str, figure: float) -> None:
    assert figure in citations.years_of(citations.normalise(text).text)


@pytest.mark.parametrize(("text", "figure"), NOT_STATED)
def test_a_bound_a_range_or_another_count_is_not_a_figure(text: str, figure: float) -> None:
    assert figure not in citations.years_of(citations.normalise(text).text)


DOCUMENT = (
    "Jane-Ann Doe, software engineer.\n"
    "Trois ans et demi d'ingénierie logicielle, dont deux en robotique.\n"
    "Example Robotics, 2021 to 2024.\n"
)


@pytest.mark.parametrize("value", [3.5, "3.5", "3,5"])
def test_a_proposed_figure_holds_against_the_sentence_that_states_it(value: object) -> None:
    excerpt = "Trois ans et demi d'ingénierie logicielle"
    result = citations.check_item({"": FieldType.YEARS}, value, DOCUMENT, [excerpt])
    assert isinstance(result, citations.Accepted)


@pytest.mark.parametrize(
    ("value", "excerpt"),
    [
        (3, "Trois ans et demi d'ingénierie logicielle"),
        (4, "Trois ans et demi d'ingénierie logicielle"),
        (3, "Example Robotics, 2021 to 2024."),
        (2, "Trois ans et demi d'ingénierie logicielle, dont deux en robotique."),
        (True, "Trois ans et demi d'ingénierie logicielle"),
        ("about 3.5", "Trois ans et demi d'ingénierie logicielle"),
    ],
)
def test_a_figure_the_excerpt_does_not_state_is_refused(value: object, excerpt: str) -> None:
    result = citations.check_item({"": FieldType.YEARS}, value, DOCUMENT, [excerpt])
    assert isinstance(result, citations.Refusal)
    assert (result.rule, result.index) == (Rule.YEARS_NOT_IN_SOURCE, "value")


def test_the_agent_proposes_the_years_a_document_states_and_never_removes_them() -> None:
    item = items.build("skills", "years_experience", "add", 3.5)
    assert (item.key, item.value, item.field_types) == ("years_experience", 3.5, {"": "years"})
    with pytest.raises(items.ItemError) as removal:
        items.build("skills", "years_experience", "remove")
    assert removal.value.rule == items.SETTING


def test_years_of_is_linear_on_a_long_text() -> None:
    text = citations.normalise("3 years and 6 months of support, " * 20_000).text
    assert citations.years_of(text) == frozenset({3.5})
