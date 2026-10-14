"""The box classifier and the answer bank (A3)."""

import uuid

import pytest

from app.apply.answers import Answer, Context, FormField, Left, resolve
from app.apply.bank import AnswerBank, question_key
from app.apply.terms import BoxKind, classify
from tests.profile.conftest import full

ALLOWED = [
    ("I have read the Privacy Notice for this application", BoxKind.privacy),
    ("I acknowledge the Candidate Privacy Policy", BoxKind.privacy),
    ("J'ai lu la politique de confidentialité", BoxKind.privacy),
    ("I consent to the processing of my personal data for this application", BoxKind.privacy),
    ("I confirm that the information provided is accurate and complete", BoxKind.accuracy),
    ("I certify that my answers are true", BoxKind.accuracy),
    ("Je certifie que les informations sont exactes", BoxKind.accuracy),
]
PERSONS = [
    "I agree to binding arbitration of any dispute",
    "I agree to receive marketing emails",
    "Subscribe to our newsletter",
    "Join our talent community",
    "Add me to your talent pool for future opportunities",
    "I authorize a background check",
    "I consent to a criminal record check",
    "Keep my data for 24 months for other positions",
    "I agree to the Terms and Conditions",
    "I have read the privacy notice and agree to be contacted about future roles",
    "I confirm the information is accurate and agree to arbitration",
    "I agree to receive SMS messages",
    "J'accepte de recevoir des offres de partenaires",
    "I accept the non-disclosure agreement",
    "Share my profile with third parties",
    "Something nobody can classify",
    "",
]


@pytest.mark.parametrize(("label", "kind"), ALLOWED)
def test_only_the_privacy_notice_and_accuracy_are_allowed(label: str, kind: BoxKind) -> None:
    box = classify(label)
    assert box.kind is kind
    assert box.allowed


@pytest.mark.parametrize("label", PERSONS)
def test_every_other_box_is_the_persons(label: str) -> None:
    box = classify(label)
    assert box.kind is BoxKind.person
    assert not box.allowed


def test_a_checkbox_field_goes_through_the_classifier() -> None:
    user = uuid.uuid4()
    context = Context(user_id=user, sections={"identity": full("identity")})
    ticked = resolve(
        FormField("I confirm the information is accurate", "checkbox", (), True), context
    )
    assert ticked == Answer(True, "terms.accuracy")
    refused = resolve(FormField("I agree to arbitration", "checkbox", (), True), context)
    assert refused == Left("person_choice", (), True)
    marketing = resolve(FormField("Send me marketing updates", "checkbox"), context)
    assert isinstance(marketing, Left)


def test_a_checkbox_that_asks_about_the_person_goes_by_the_rules() -> None:
    context = Context(user_id=uuid.uuid4(), sections={"answers": full("answers")})
    result = resolve(FormField("I am willing to work on-site", "checkbox"), context)
    assert result == Answer(True, "answers.willing_onsite")


def test_question_keys_ignore_case_accents_punctuation_and_required_marks() -> None:
    assert question_key("Prénom *") == question_key("prenom") == "prenom"
    assert question_key("Notice period (required):") == "notice period"
    assert question_key("  How   did you hear about us?  ") == "how did you hear about us"
    assert question_key("???") == ""


def test_the_bank_is_updated_by_a_persons_edit_and_forgets_on_empty() -> None:
    user = uuid.uuid4()
    bank = AnswerBank(user)
    first = bank.with_answer("Pronoms *", "elle")
    assert bank.entries == {}
    assert first.lookup("pronoms") == "elle"
    assert first.with_answer("Pronoms", "  il ").lookup("PRONOMS") == "il"
    assert first.with_answer("Pronoms", "").lookup("pronoms") is None
    assert first.with_answer("???", "x") is first
    assert first.lookup("???") is None


def test_one_persons_bank_never_serves_another_persons_context() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    bank_b = AnswerBank(b).with_answer("Notice period", "Never")
    with pytest.raises(ValueError, match="another person"):
        Context(user_id=a, sections={"answers": full("answers")}, bank=bank_b)
    own = Context(user_id=a, sections={"answers": full("answers")}, bank=AnswerBank(a))
    result = resolve(FormField("Notice period"), own)
    assert isinstance(result, Answer)
    assert result.source == "answers.notice_period"
