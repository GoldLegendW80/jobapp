"""The answer resolver (A3): every standard question from the profile, with its source, and a
`Left` (never a guess) for what the profile does not hold. No model, no database, no clock."""

import uuid
from typing import Any

import pytest

from app.apply.answers import (
    Answer,
    Context,
    Documents,
    FormField,
    Left,
    Posting,
    match_option,
    posting_countries,
    resolve,
)
from app.apply.bank import AnswerBank
from tests.profile.conftest import full

USER = uuid.uuid4()
OTHER = uuid.uuid4()
DECLINE = ("Male", "Female", "I don't wish to answer")
YES_NO = ("Yes", "No")


def sections(**answers: Any) -> dict[str, Any]:
    stored = {name: full(name) for name in ("identity", "experience", "skills")}
    stored["answers"] = {**full("answers"), "eeo_policy": "decline", "leave_blank": [], **answers}
    return stored


def ctx(location: str | None = "Paris, France", **kw: Any) -> Context:
    secs = kw.pop("sections", None) or sections()
    posting = Posting.at(location) if location is not None else None
    return Context(user_id=USER, sections=secs, posting=posting, **kw)


def ask(label: str, kind: str = "text", options: tuple[str, ...] = (), **kw: Any) -> Any:
    context = kw.pop("context", None) or ctx()
    return resolve(FormField(label, kind, options, kw.pop("required", True)), context)  # type: ignore[arg-type]


def value(label: str, kind: str = "text", options: tuple[str, ...] = (), **kw: Any) -> Any:
    result = ask(label, kind, options, **kw)
    assert isinstance(result, Answer), result
    return result.value


def left(label: str, kind: str = "text", options: tuple[str, ...] = (), **kw: Any) -> Left:
    result = ask(label, kind, options, **kw)
    assert isinstance(result, Left), result
    return result


# --- names, contact, links, location ---------------------------------------------------------


def test_names_contact_and_links_come_from_the_identity_with_their_source() -> None:
    identity = full("identity")
    first, _, last = identity["full_name"].partition(" ")
    assert value("First name *") == first
    assert value("Last name") == last
    assert value("Full name") == identity["full_name"]
    assert value("Email address", "text") == identity["email"]
    assert value("Phone") == identity["phone"]
    assert value("LinkedIn profile") == identity["linkedin_url"]
    assert value("Website") == identity["website"]
    assert ask("Email").source == "identity.email"


def test_a_french_form_is_read() -> None:
    identity = full("identity")
    first, _, last = identity["full_name"].partition(" ")
    assert value("Prénom") == first
    assert value("Nom") == last
    assert value("Nom de famille") == last
    assert value("Ville") == identity["city"]
    assert value("Localisation") == f"{identity['city']}, {identity['country']}"
    assert value("Pays") == identity["country"]
    assert value("Courriel") == identity["email"]
    assert value("Téléphone portable") == identity["phone"]


def test_preferred_name_and_pronouns_are_the_persons_and_empty_by_default() -> None:
    assert value("Preferred name") == "Ana"
    assert value("Pronouns") == "she/her"
    empty = ctx(sections=sections(preferred_name=None, pronouns=None))
    assert left("Preferred name", context=empty).reason == "no_preferred_name"
    assert left("Pronouns", context=empty).reason == "no_pronouns"


# --- work authorisation and sponsorship ------------------------------------------------------


def test_work_authorisation_follows_the_country_of_the_posting() -> None:
    assert value("Are you legally authorized to work in this country?", "yes_no") is True
    assert value("Do you require visa sponsorship?", "yes_no") is False
    uk = ctx("London, UK")
    assert value("Are you authorized to work in the country?", "yes_no", context=uk) is False
    assert value("Will you now or in the future require sponsorship?", "yes_no", context=uk) is True
    assert value("Do you require sponsorship?", "select", YES_NO, context=uk) == "Yes"
    assert (
        value("Work authorisation", "textarea")
        == full("answers")["work_authorization"][0]["answer"]
    )


def test_a_country_not_in_the_list_is_left() -> None:
    result = left(
        "Are you authorized to work in this country?", "yes_no", context=ctx("Tokyo, Japan")
    )
    assert result.reason == "country_not_in_work_authorization"


def test_a_posting_without_a_known_country_is_left() -> None:
    assert left("Do you require sponsorship?", "yes_no", context=ctx("Remote")).reason == (
        "no_posting_country"
    )


def test_a_posting_in_two_countries_needs_both_and_answers_only_when_they_agree() -> None:
    both = ctx("France or United Kingdom")
    assert posting_countries("France or United Kingdom") == ("United Kingdom", "France")
    assert left("Do you require sponsorship?", "yes_no", context=both).reason == (
        "countries_disagree_or_unstated"
    )
    secs = sections(
        work_authorization=[
            {"country": "France", "answer": "Citizen.", "needs_sponsorship": False},
            {"country": "Germany", "answer": "Citizen.", "needs_sponsorship": False},
        ]
    )
    agreeing = ctx("Paris or Berlin", sections=secs)
    assert value("Do you require sponsorship?", "yes_no", context=agreeing) is False
    one_missing = ctx("Paris or London", sections=secs)
    assert left("Do you require sponsorship?", "yes_no", context=one_missing).reason == (
        "country_not_in_work_authorization"
    )


def test_a_profile_run_without_a_posting_leaves_the_country_questions() -> None:
    nowhere = ctx(None)
    assert left("Do you require sponsorship?", "yes_no", context=nowhere).reason == (
        "no_posting_country"
    )
    assert value("Email", context=nowhere) == full("identity")["email"]


# --- notice, start, relocation, travel -------------------------------------------------------


def test_notice_start_relocation_and_travel() -> None:
    answers = full("answers")
    assert value("Notice period") == answers["notice_period"]
    assert value("Préavis") == answers["notice_period"]
    assert value("Earliest start date") == answers["earliest_start"]
    assert value("Start date", "date") == answers["earliest_start"]
    assert value("Are you willing to relocate?", "textarea") == answers["relocation"]
    assert value("Willingness to travel", "text") == answers["travel"]


def test_a_start_date_that_is_not_a_date_is_left_for_a_date_field() -> None:
    secs = sections(earliest_start="As soon as possible")
    assert left("Start date", "date", context=ctx(sections=secs)).reason == "start_is_not_a_date"
    assert value("Start date", context=ctx(sections=secs)) == "As soon as possible"


def test_a_relocation_sentence_is_not_a_yes() -> None:
    assert left("Willing to relocate?", "yes_no").reason == "not_a_yes_or_no"


def test_onsite_hybrid_background_and_reference_checks_are_the_persons_choices() -> None:
    assert value("Are you willing to work on-site?", "yes_no") is True
    assert value("Are you open to a hybrid schedule?", "select", YES_NO) == "Yes"
    assert value("Do you consent to a background check?", "yes_no") is False
    assert value("May we contact your references? (reference check)", "yes_no") is True
    unset = ctx(sections=sections(willing_onsite=None, background_check_consent=None))
    assert left("Willing to work on-site?", "yes_no", context=unset).reason == "no_willing_onsite"
    assert left("Background check consent", "yes_no", context=unset).reason == (
        "no_background_check_consent"
    )


# --- salary ----------------------------------------------------------------------------------


def test_salary_under_each_stance() -> None:
    label = "Salary expectations"
    blank = ctx(sections=sections(salary_stance="leave_blank"))
    assert left(label, required=True, context=blank).reason == "salary_leave_blank"
    sentence = ctx(sections=sections(salary_stance="sentence"))
    assert value(label, "textarea", context=sentence) == "Never invent a number."
    by_range = ctx(sections=sections(salary_stance="range"))
    assert value("Salary expectations in GBP", context=by_range) == "45000 GBP per year".replace(
        "45000", "from 45000"
    )
    assert value("Expected salary (€)", context=by_range) == "50000 to 60000 EUR per year"
    assert left(label, context=by_range).reason == "no_single_salary_range"
    assert left(label, "select", ("a", "b"), context=by_range).reason == "no_single_salary_range"


def test_a_profile_with_only_the_old_paragraph_answers_with_that_sentence() -> None:
    old = full("answers")
    for name in ("salary_stance", "salary_ranges"):
        old.pop(name)
    old_ctx = ctx(sections={**sections(), "answers": old})
    assert value("Salary expectations", context=old_ctx) == "Never invent a number."
    assert ask("Salary expectations", context=old_ctx).source == "answers.salary_policy"


def test_an_empty_paragraph_and_no_stance_leaves_the_salary() -> None:
    empty = ctx(sections=sections(salary_policy=None, salary_stance=None))
    assert left("Salary expectations", context=empty).reason == "salary_leave_blank"
    asks_sentence = ctx(sections=sections(salary_policy=None, salary_stance="sentence"))
    assert left("Salary expectations", context=asks_sentence).reason == "no_salary_sentence"


# --- EEO -------------------------------------------------------------------------------------


def test_eeo_under_each_policy_with_and_without_a_decline_option() -> None:
    label = "What is your gender?"
    decline = ctx(sections=sections(eeo_policy="decline"))
    assert value(label, "select", DECLINE, context=decline) == "I don't wish to answer"
    assert ask(label, "select", DECLINE, context=decline).source == "answers.eeo_policy"
    no_option = left(label, "select", ("Male", "Female"), context=decline)
    assert no_option.reason == "eeo_no_decline_option"
    assert no_option.options == ("Male", "Female")
    assert left(label, "text", context=decline).reason == "eeo_no_decline_option"
    keep = ctx(sections=sections(eeo_policy="leave"))
    assert left(label, "select", DECLINE, context=keep).reason == "eeo_policy_leave"
    stored = ctx(sections=sections(eeo_policy="stored"))
    assert value(label, "select", ("Man", "Woman", "Other"), context=stored) == "Woman"
    assert left("What is your ethnicity?", "select", DECLINE, context=stored).reason == (
        "eeo_not_stored"
    )
    nothing = left(label, "select", ("Man", "Other"), context=stored)
    assert nothing.reason == "no_matching_option"


@pytest.mark.parametrize(
    "label",
    [
        "Disability status",
        "Veteran status",
        "Race",
        "Sexual orientation",
        "Age",
        "Date of birth",
        "Ethnicity",
    ],
)
def test_every_eeo_topic_declines_and_none_is_inferred(label: str) -> None:
    assert value(label, "select", ("Yes", "No", "Prefer not to say")) == "Prefer not to say"
    assert value(label, "select", ("Je préfère ne pas répondre", "Oui")) == (
        "Je préfère ne pas répondre"
    )


def test_manage_and_page_are_not_eeo() -> None:
    assert left("Do you manage a team of engineers?", "yes_no").reason == "unknown_question"


# --- years of experience ---------------------------------------------------------------------


def test_years_when_the_profile_states_fewer_the_answer_is_no() -> None:
    secs = sections()
    secs["skills"] = {**secs["skills"], "years_experience": 3.5}
    context = ctx(sections=secs)
    # Years with one technology are not stored: left, even when the overall years are fewer.
    assert left(
        "Do you have 8+ years of experience in Java?", "yes_no", context=context
    ).reason == ("subject_years_not_stated")
    assert ask("Do you have 8+ years of experience?", "yes_no", context=context).source == (
        "skills.years_experience"
    )
    assert value("Do you have 3+ years of experience?", "yes_no", context=context) is True
    assert left(
        "Do you have 3+ years of experience with COBOL?", "yes_no", context=context
    ).reason == ("subject_years_not_stated")
    assert value("Years of experience", "text", context=context) == "3.5"


def test_years_when_the_profile_says_nothing_are_left() -> None:
    secs = sections(years_experience_stated=None)
    secs["skills"] = {**secs["skills"], "years_experience": None}
    context = ctx(sections=secs)
    assert left("Do you have 8+ years of experience?", "yes_no", context=context).reason == (
        "years_not_stated"
    )
    assert left("How many years of experience do you have?", context=context).reason == (
        "years_not_stated"
    )


def test_years_read_the_statement_when_the_skills_hold_no_number() -> None:
    secs = sections(years_experience_stated="About 3.5 years")
    secs["skills"] = {**secs["skills"], "years_experience": None}
    context = ctx(sections=secs)
    assert value("Do you have 5+ years of experience?", "yes_no", context=context) is False
    assert value("Years of experience", context=context) == "About 3.5 years"


def test_years_pick_the_range_option() -> None:
    secs = sections()
    secs["skills"] = {**secs["skills"], "years_experience": 3.5}
    context = ctx(sections=secs)
    options = ("0-2 years", "3-5 years", "6+ years")
    assert value(
        "How many years of experience do you have?", "select", options, context=context
    ) == ("3-5 years")


# --- education, languages, how heard, previously applied -------------------------------------


def test_education_from_the_answers_then_the_experience_section() -> None:
    assert value("Highest level of education") == "Master's degree"
    assert value("Graduation year") == "2022"
    secs = sections(highest_degree=None, highest_degree_year=None)
    context = ctx(sections=secs)
    degree = full("experience")["education"][0]
    assert value("Highest level of education", context=context) == degree["degree"]
    assert ask("Highest level of education", context=context).source == "experience.education"
    assert value("School", context=context) == degree["school"]


def test_a_combobox_whose_options_do_not_contain_the_answer_is_left_with_the_options() -> None:
    options = ("High school", "Bachelor", "PhD")
    result = left("Highest level of education", "combobox", options)
    assert result.reason == "no_matching_option"
    assert result.options == options
    assert result.required is True


def test_languages() -> None:
    names = [lang["name"] for lang in full("identity")["languages"]]
    assert value("Do you speak " + names[0] + "?", "yes_no") is True
    assert left("Do you speak Klingon?", "yes_no").reason == "language_not_in_profile"
    assert names[0] in value("Languages", "textarea")


def test_how_heard_matches_an_option_or_falls_back_to_other() -> None:
    assert value("How did you hear about us?", "text") == "The company's careers page."
    assert (
        value("How did you hear about us?", "select", ("Careers page", "Other")) == "Careers page"
    )
    assert value("How did you hear about us?", "select", ("Friend", "Other")) == "Other"
    assert value(
        "How did you hear about us?", "select", ("The company's careers page", "Other")
    ) == ("The company's careers page")
    assert left("How did you hear about us?", "select", ("LinkedIn", "Friend")).reason == (
        "no_matching_option"
    )


def test_previously_applied_uses_the_apps_own_applications_first() -> None:
    label = "Have you previously applied to or worked at this company?"
    assert value(label, "yes_no") is False
    assert value(label, "yes_no", context=ctx(applied_before=True)) is True
    none = ctx(sections=sections(previously_applied_default=None))
    assert left(label, "yes_no", context=none).reason == "no_previously_applied_default"
    assert value(label, "single", ("Oui", "Non")) == "Non"


# --- documents, free text, leave blank, the bank, an empty profile ---------------------------


def test_documents_and_the_cover_letter() -> None:
    docs = Documents(cv_path="/x/cv.pdf", letter_path="/x/letter.pdf", letter_text="Dear team")
    context = ctx(documents=docs)
    assert value("Resume/CV", "file", context=context) == "/x/cv.pdf"
    assert value("Cover letter", "file", context=context) == "/x/letter.pdf"
    assert value("Cover letter", "textarea", context=context) == "Dear team"
    assert left("Resume", "file").reason == "no_document"


def test_free_text_questions_are_left_for_the_agent() -> None:
    assert left("Why do you want to join us?", "textarea").reason == "free_text_for_the_agent"


def test_what_the_person_asked_to_leave_blank_is_left() -> None:
    secs = sections(leave_blank=["Phone: never on forms"])
    assert left("Phone", context=ctx(sections=secs)).reason == "person_leave_blank"
    assert value("Email", context=ctx(sections=secs)) == full("identity")["email"]


def test_a_bank_answer_beats_the_pattern_table() -> None:
    bank = AnswerBank(USER).with_answer("Notice period *", "Two weeks")
    result = ask("Notice period", context=ctx(bank=bank))
    assert result == Answer("Two weeks", "bank")
    picked = ask("Notice period", "select", ("One week", "Two weeks"), context=ctx(bank=bank))
    assert picked.value == "Two weeks"
    assert picked.source == "bank"
    miss = ask("Notice period", "select", ("Month", "Quarter"), context=ctx(bank=bank))
    assert miss.reason == "no_matching_option"
    yes = AnswerBank(USER).with_answer("Do you play chess?", "yes")
    assert value("Do you play chess?", "yes_no", context=ctx(bank=yes)) is True
    assert value("Do you play chess?", "single", YES_NO, context=ctx(bank=yes)) == "Yes"


def test_a_bank_of_another_person_is_refused() -> None:
    bank = AnswerBank(OTHER).with_answer("Notice period", "Never")
    with pytest.raises(ValueError, match="another person"):
        ctx(bank=bank)
    # The same question for A, with A's own empty bank, falls to the pattern table.
    own = ctx(bank=AnswerBank(USER))
    assert value("Notice period", context=own) == full("answers")["notice_period"]


def test_an_empty_profile_leaves_everything_and_invents_nothing() -> None:
    empty = Context(user_id=USER, sections={}, posting=Posting.at("Paris, France"))
    for label, kind in [
        ("First name", "text"),
        ("Email", "text"),
        ("Phone", "text"),
        ("Do you require sponsorship?", "yes_no"),
        ("Notice period", "text"),
        ("Salary expectations", "text"),
        ("Highest level of education", "text"),
        ("Years of experience", "text"),
        ("Do you have 5+ years of experience?", "yes_no"),
        ("How did you hear about us?", "text"),
        ("Have you previously applied here?", "yes_no"),
        ("Gender", "text"),
        ("Resume", "file"),
    ]:
        result = resolve(FormField(label, kind, (), True), empty)  # type: ignore[arg-type]
        assert isinstance(result, Left), (label, result)
    assert resolve(FormField("Something odd", "text"), empty) == Left("unknown_question", ())


def test_a_stored_section_that_no_longer_validates_reads_as_empty() -> None:
    broken = Context(user_id=USER, sections={"identity": {"email": 5}})
    assert isinstance(resolve(FormField("Email"), broken), Left)


# --- option matching -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "options", "expected"),
    [
        ("Yes", ("yes", "no"), "yes"),
        ("French", ("French (Français)", "English"), "French (Français)"),
        ("Linked", ("LinkedIn", "Friend"), "LinkedIn"),
        ("other", ("LinkedIn", "Other (please specify)"), "Other (please specify)"),
        ("autre", ("LinkedIn", "Other"), "Other"),
        ("prefer not to say", ("Man", "Decline to self identify"), "Decline to self identify"),
        ("prefer not to say", ("Man", "I prefer not to say"), "I prefer not to say"),
        ("x", ("Man", "Woman"), None),
        ("an", ("Man", "Woman"), None),
        ("Woman", ("Man", "Other"), None),
        ("man", ("Woman", "Other"), None),
    ],
)
def test_match_option(text: str, options: tuple[str, ...], expected: str | None) -> None:
    assert match_option(text, options) == expected
