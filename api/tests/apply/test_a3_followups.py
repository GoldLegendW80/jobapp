"""Only explicit stored facts settle the A3 follow-ups (#585)."""

import pytest

from app.apply.answers import Answer, Context, Left, degree_of, language_level
from tests.apply.test_a3_answers import USER, ask, ctx, sections


@pytest.mark.parametrize("consent", [True, False, None])
@pytest.mark.parametrize(
    "label",
    [
        "May we contact your references?",
        "MAY WE CONTACT YOUR REFERENCES?",
        "May we contact your references? (reference check)",
    ],
)
def test_reference_contact_uses_only_explicit_consent(label: str, consent: bool | None) -> None:
    result = ask(label, "yes_no", context=ctx(sections=sections(reference_check_consent=consent)))
    if consent is None:
        assert isinstance(result, Left)
    else:
        assert isinstance(result, Answer)
        assert result.value is consent
        assert result.source == "answers.reference_check_consent"


@pytest.mark.parametrize("needs", [True, False])
@pytest.mark.parametrize(
    "label",
    [
        "Is visa sponsorship required to work in the UK?",
        "Will sponsorship be required?",
        "Is sponsorship required?",
        "Will employer sponsorship be needed to work in the UK?",
    ],
)
def test_is_required_forms_use_the_same_country_fact(label: str, needs: bool) -> None:
    secs = sections(
        work_authorization=[
            {"country": "UK", "answer": "Stored policy", "needs_sponsorship": needs}
        ]
    )
    result = ask(label, "yes_no", context=ctx("London, UK", sections=secs))
    assert isinstance(result, Answer), result
    assert result.value is needs
    assert result.source == "answers.work_authorization"


@pytest.mark.parametrize(
    "label",
    [
        "Do you need a visa to work in the UK?",
        "Is visa sponsorship not required?",
        "Will sponsorship be required for your spouse?",
        "Is sponsorship required to work in Canada?",
        "What is your reference's phone number?",
    ],
)
def test_neighbouring_questions_do_not_reuse_an_unrelated_fact(label: str) -> None:
    assert isinstance(ask(label, "yes_no", context=ctx("London, UK")), Left)


@pytest.mark.parametrize(
    "stored",
    [
        "MSc Computer Science",
        "M.Sc. in Computer Science",
        "Master of Science in Mathematics",
        "BSc Physics",
        "Bachelor of Arts in History",
        "Master 2",
        "Master 2 Informatique",
    ],
)
def test_explicit_degree_with_field_can_pick_its_level(stored: str) -> None:
    level = "Bachelor's degree" if stored.startswith(("BSc", "Bachelor")) else "Master's degree"
    result = ask(
        "Highest degree",
        "select",
        ("Bachelor's degree", "Master's degree", "Doctorate"),
        context=ctx(sections=sections(highest_degree=stored)),
    )
    assert isinstance(result, Answer), result
    assert result.value == level


@pytest.mark.parametrize(
    "stored",
    [
        "MS",
        "M.S.",
        "Diplôme d'ingénieur",
        "Engineer's Degree",
        "MSc in progress",
        "MSc Computer Science (in progress)",
        "Master 2 student",
        "MSc candidate",
        "MSc expected",
        "MSc or equivalent",
        "MSc not completed",
        "MSc in Music",
        "Bac+5",
    ],
)
def test_ambiguous_or_unsupported_degree_stays_unknown(stored: str) -> None:
    result = ask(
        "Highest degree",
        "select",
        ("Bachelor's degree", "Master's degree", "Doctorate"),
        context=ctx(sections=sections(highest_degree=stored)),
    )
    assert isinstance(result, Left), result


@pytest.mark.parametrize(
    ("stored", "option"),
    [
        ("Professional working proficiency", "Professional working"),
        ("Limited working proficiency", "Limited working"),
        ("Business fluent", "Fluent"),
        ("Business", "Business"),
        ("Proficient user", "Fluent"),
        ("Independent user", "Intermediate"),
        ("Basic user", "Basic"),
        ("C1", "Proficient user"),
        ("B2", "Independent user"),
        ("A2", "Basic user"),
    ],
)
def test_explicit_language_band_can_normalize_without_inventing_a_grade(
    stored: str, option: str
) -> None:
    secs = sections()
    secs["identity"]["languages"] = [{"name": "English", "level": stored}]
    result = ask("English level", "select", (option, "Native"), context=Context(USER, secs))
    assert isinstance(result, Answer), result
    assert result.value == option


@pytest.mark.parametrize(
    "stored",
    ["Professional", "Business", "Professional working proficiency", "Limited working proficiency"],
)
def test_vague_or_unrelated_bands_prove_neither_fluency_nor_a_cefr_grade(stored: str) -> None:
    secs = sections()
    secs["identity"]["languages"] = [{"name": "English", "level": stored}]
    context = Context(USER, secs)
    assert isinstance(ask("Are you fluent in English?", "yes_no", context=context), Left)
    assert isinstance(ask("English level", "select", ("B2", "C1", "Fluent"), context=context), Left)


@pytest.mark.parametrize("stored", ["Professional", "Business fluent", "Proficient user"])
def test_a_band_without_an_explicit_grade_cannot_choose_a_specific_grade(stored: str) -> None:
    assert language_level(stored) is not None
    secs = sections()
    secs["identity"]["languages"] = [{"name": "English", "level": stored}]
    assert isinstance(
        ask("English level", "select", ("C1", "C2"), context=Context(USER, secs)), Left
    )


@pytest.mark.parametrize(
    "label", ["I consent to a background check", "I agree to binding arbitration"]
)
def test_stored_consent_never_authorizes_a_legal_checkbox(label: str) -> None:
    assert isinstance(
        ask(label, "checkbox", context=ctx(sections=sections(background_check_consent=True))), Left
    )


def test_field_normalization_does_not_change_exact_degree_identity() -> None:
    assert degree_of("MSc Computer Science") == ("master", 0, "MSc")
    result = ask(
        "Highest degree",
        "select",
        ("MSc Physics", "Master's degree"),
        context=ctx(sections=sections(highest_degree="MSc Computer Science")),
    )
    assert isinstance(result, Answer)
    assert result.value == "Master's degree"
