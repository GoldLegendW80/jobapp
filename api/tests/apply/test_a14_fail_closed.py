"""Round 3 of #687: an address is usable for an application by email only after an explicit
positive instruction, in a block with no negation or prohibition; everything else fails closed.

The block is the paragraph, the lead line of its list (or the open line just above) and the
nearest heading above. The text is folded first (NFKC, quotes, hidden characters, lookalikes).
"""

import pytest
import sqlalchemy as sa

from app.apply import email, full_auto, gate, service
from app.apply.models import AnswerSource, ApplicationAnswer, ApplicationEvent, Mode, State
from app.jobs.models import Job
from app.mail import hosts
from app.mail.draft_models import MailDraft
from tests.apply.conftest import AT
from tests.apply.test_a13_full_auto import turn_on

A = "e2e-jobs@example.org"
B = "e2e-team@example.org"
PAGE = "https://careers.example.org/jobs/7"

# The reviewer's inputs (review 5646369480) and their neighbours, never deleted.
BLOCKS = [
    f"Please do NOT:\n- send your CV to {A}\n- call the office\nApply with the form below.",
    f"Please do NOT:\n* send your CV to {A}\n* call the office",
    f"Please do NOT:\n• send your CV to {A}\n• call the office",
    f"Please do NOT:\n1. send your CV to {A}\n2. call the office",
    f"Please do NOT:\n1) send your CV to {A}",
    f"PLEASE DO NOT:\n- SEND YOUR CV TO {A}",
    f"Please do not:\n\n- send your CV to {A}\n\n- call the office",
    f"Please do not:\n\n- call the office\n\n- send your CV to {A}",
    f"Thank you for your interest.\nPlease avoid the following:\n\n- send your CV to {A}",
    f"Merci de ne pas :\n- envoyer votre CV à {A}\n- appeler l'accueil",
    f"Merci de NE PAS :\n\n- envoyer votre CV à {A}",
    f"À éviter :\n- envoyer votre CV à {A}",
    f"How NOT to apply\n\nSend your CV to {A}",
    f"How not to apply\n\nWe read every application.\n\nSend your CV to {A}",
    f"## How not to apply\nSend your CV to {A}",
    f"What not to do:\n\n- Send your CV to {A}",
    f"Comment ne pas postuler\n\nEnvoyez votre CV à {A}",
    f"Ce qu'il ne faut pas faire\n\nEnvoyez votre CV à {A}",
    f"Please do not, under any circumstances,\n\nsend your CV to {A}",
]

PROHIBITIONS = [
    f"It is forbidden to send your CV to {A}, use the form.",
    f"It is prohibited to send your CV to {A}.",
    f"Send your CV to {A}: applications by email are not accepted.",
    f"Send your CV to {A}, those applications are ignored.",
    f"Send your CV to {A}: such emails are discarded.",
    f"Send your CV to {A}? Those applications are rejected.",
    f"Send your CV to {A}. Emails are disregarded, use the portal.",
    f"Applications by email are refused. Send your CV to {A} only by mistake.",
    f"Email applications are banned. Send your CV to {A}",
    f"Il est interdit d'envoyer votre CV à {A}",
    f"Envoyer votre CV à {A} est interdit.",
    f"Envoyez votre CV à {A} : les candidatures par mail sont refusées.",
    f"Envoyez votre CV à {A} : candidature refusée.",
    f"Envoyez votre CV à {A}, les envois sont proscrits.",
    f"Aucune candidature par mail. Envoyez votre CV à {A}",
    f"Envoyez votre CV à {A}, il sera supprimé.",
    f"Envoyez votre CV à {A}, les mails sont ignorés.",
    f"Envoyez votre CV à {A} : candidatures rejetées.",
]

UNICODE = [
    f"Don＇t send your CV to {A}",
    f"Don’t send your CV to {A}",
    f"Donʼt send your CV to {A}",
    f"Don´t send your CV to {A}",
    f"Don`t send your CV to {A}",
    f"Do n­ot send your CV to {A}",
    f"Do n​ot send your CV to {A}",
    f"Do n‍ot send your CV to {A}",
    f"Do n⁠ot send your CV to {A}",
    f"Do no﻿t send your CV to {A}",
    f"Do ɴot send your CV to {A}",
    f"Do ɴᴏᴛ send your CV to {A}",
    f"Do ＮＯＴ send your CV to {A}",
    f"Do nоt send your CV to {A}",
    f"N＇envoyez pas votre CV à {A}",
    f"Ne p­as envoyer votre CV à {A}",
    f"Il est inter​dit d'envoyer votre CV à {A}",
    f"Il est INTERDIT d'envoyer votre CV à {A}",
]

INVITATIONS = [
    f"Do not hesitate to send your CV to {A}",
    f"Don't hesitate to send your application to {A}",
    f"Don’t hesitate to send your CV to {A}",
    f"Do not ever hesitate to send your CV to {A}",
    f"N'hésitez pas à envoyer votre CV à {A}",
    f"N’hésitez pas à envoyer votre candidature à {A}",
    f"N'hesitez pas a envoyer votre CV a {A}",
    f"N'hésitez surtout pas à envoyer votre CV à {A}",
    f"Ne hésitez pas à envoyer votre CV à {A}",
]

POSITIVE = [
    f"Send your CV to {A}",
    f"Send your CV to: {A}",
    f"Apply by email to {A}",
    f"Apply by e-mail to {A}",
    f"Email your resume to {A}",
    f"Envoyez votre candidature à {A}",
    f"Envoyez votre candidature par mail à {A}",
    f"Postulez par mail à {A}",
    f"Postulez par e-mail à {A}",
    f"Postulez par courriel à {A}",
    f"How to apply\n\nSend your CV to {A}",
    f"Requirements\n- Python\n- No gambling experience\n\nHow to apply\n\nSend your CV to {A}",
    f"Ne pas envoyer de CV à {B}.\n\nEnvoyez votre CV à {A}",
    f"We do not sponsor visas.\n\nSend your CV to {A}",
]

# No instruction on the allow list: a signature, a footer, a contact line, other wording.
NOTHING = [
    f"Apply through our careers page.\n\nJane Doe\nTalent team\n{A}",
    f"Apply on the form below.\n\n--\nExample Robotics, {A}, Bordeaux",
    f"Questions? {A}",
    f"Contact: {A}",
    f"Applications: {A}",
    f"Write to {A}",
    f"CV → {A}",
    f"Senden Sie Ihren Lebenslauf an {A}",
    f"Sеnd your CV to {A}",
    f"Our recruiter {A} reads every application sent through the form.",
    f"Sending your CV to {A} is not accepted. Send your CV through the form.",
    f"Applications sent to {A} are ignored. Send your CV through the form.",
]


@pytest.mark.parametrize("text", BLOCKS + PROHIBITIONS + UNICODE)
def test_a_negated_block_makes_the_address_unsure(text):
    reading = email.read(text)
    assert reading.usable == ()
    assert A in reading.unsure
    assert email.recipients(text) == []


@pytest.mark.parametrize("text", INVITATIONS + POSITIVE)
def test_an_explicit_positive_instruction_is_usable(text):
    assert email.read(text).usable == (A,)


@pytest.mark.parametrize("text", NOTHING)
def test_an_address_outside_an_instruction_is_nothing(text):
    assert email.read(text) == email.Reading()


def test_an_address_negated_anywhere_is_unsure_everywhere():
    text = f"Please do not email {A} about the role.\n\nSend your CV to {A}"
    assert email.read(text) == email.Reading((), (A,))


def test_the_reader_is_bounded_on_a_huge_text():
    text = ("Send your CV to the form. " * 20_000) + f"\n\nSend your CV to {A}"
    assert email.read(text) == email.Reading()


def test_mutation_without_the_negation_check_every_negated_row_turns_usable(monkeypatch):
    """The refusals come from the block's negation check and nothing else: switched off, every
    row turns usable (so the tables above test the rule, not an accident)."""
    monkeypatch.setattr(email, "negated", lambda text: False)
    for text in BLOCKS + PROHIBITIONS + UNICODE:
        assert email.read(text).usable == (A,), text


def test_mutation_the_default_is_closed(monkeypatch):
    """With the allow list emptied, no address is usable: nothing is ever read as an e-mail
    application by default."""
    monkeypatch.setattr(email, "_INSTRUCTION", __import__("re").compile(r"(?!x)x"))
    for text in POSITIVE + INVITATIONS:
        assert email.read(text) == email.Reading(), text


@pytest.mark.parametrize(
    "url",
    [
        "https://acme.breezy.hr/p/7",
        "https://acme.applytojob.com/apply/7",
        "https://acme.recruitee.com/o/role",
        "https://acme.teamtailor.com/jobs/7",
        "https://careers.smartrecruiters.com/Acme/7",
        "https://apply.workable.com/acme/j/7",
        "https://acme.jobs.personio.de/job/7",
        "https://acme.zohorecruit.eu/jobs/Careers/7",
        "https://acme.pinpointhq.com/postings/7",
        "https://hire.trakstar.com/jobs/7",
        "https://join.com/companies/acme/7",
        "https://acme.homerun.co/role",
        "https://acme.welcomekit.co/jobs/7",
        "https://acme.taleez.com/jobs/7",
        "https://acme.softgarden.io/job/7",
        "https://acme.myshopify.com/pages/careers",
        "https://acme.squarespace.com/careers",
    ],
)
def test_an_ats_or_shared_host_is_never_the_company_site(url):
    assert email.has_own_form(url)
    posting = Job(url=url, posting_text=f"Send your CV to {A}")
    assert email.addresses(posting) == []
    assert email.unsure(posting) == []


def test_new_hosts_are_in_the_ats_and_shared_lists():
    assert hosts.list_of("acme.breezy.hr") == hosts.ATS
    assert hosts.list_of("x.applytojob.com") == hosts.ATS
    assert hosts.list_of("acme.myshopify.com") == hosts.SHARED
    assert hosts.is_company_own("careers.example.org")


def _start(db, person, text, mode):
    posting = db.get(Job, person.posting_id)
    posting.url, posting.posting_text = PAGE, text
    db.commit()
    return service.start(db, person.user_id, posting.id, mode, False, now=AT).application


REVIEWER = BLOCKS[0]


def test_apply_myself_keeps_the_form_for_an_unsure_address(db, person_a):
    application = _start(db, person_a, REVIEWER, Mode.manual)
    assert application.ats == "other"


def _email_to(db, application):
    return db.get(ApplicationAnswer, (application.id, "email_to"))


def test_review_first_shows_the_unsure_address_and_lets_the_person_choose(db, person_a):
    application = _start(db, person_a, REVIEWER, Mode.review)
    assert application.ats == "email"
    email.prepare(db, application.id, AT)
    db.commit()
    row = _email_to(db, application)
    assert (row.value, row.source, row.options) == (None, AnswerSource.left, [A])
    assert "may say not to email" in row.label
    assert application.state is State.needs_person
    draft = db.scalar(sa.select(MailDraft).where(MailDraft.application_id == application.id))
    assert draft.to_addrs == []
    with pytest.raises(gate.InvalidAnswerError):
        email.sync_draft(db, application, draft, AT)
    # The person's choice is the decision: the address is then allowed for this review.
    row.value, row.source = A, AnswerSource.person
    email.sync_draft(db, application, draft, AT)
    assert draft.to_addrs == [A]


def test_full_auto_never_emails_an_unsure_address_and_hands_it_over(db, person_a):
    turn_on(db, person_a, done=3)
    application = _start(db, person_a, REVIEWER, Mode.full_auto)
    email.prepare(db, application.id, AT)
    db.commit()
    assert application.mode is Mode.review
    reasons = [
        e.data.get("code")
        for e in db.scalars(
            sa.select(ApplicationEvent).where(
                ApplicationEvent.application_id == application.id,
                ApplicationEvent.kind == "sent_to_review",
            )
        )
    ]
    assert reasons == ["email_unsure"]
    assert gate.SENTENCES["email_unsure"]
    row = _email_to(db, application)
    assert (row.value, row.source) == (None, AnswerSource.left)


def test_a_full_auto_item_cannot_use_an_unsure_address(db, person_a):
    """Even with the address selected, Full auto's own checks and the draft sync refuse it."""
    application = _start(db, person_a, REVIEWER, Mode.review)
    email.prepare(db, application.id, AT)
    row = _email_to(db, application)
    row.value, row.source = A, AnswerSource.person
    application.mode = Mode.full_auto
    db.flush()
    posting = db.get(Job, application.posting_id)
    assert email.own_company(posting, A)
    assert full_auto.checks(db, application, posting, AT) == "email_company_domain"
    draft = db.scalar(sa.select(MailDraft).where(MailDraft.application_id == application.id))
    with pytest.raises(gate.InvalidAnswerError):
        email.sync_draft(db, application, draft, AT)
