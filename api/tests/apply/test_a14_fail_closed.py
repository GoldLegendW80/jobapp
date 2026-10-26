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


# Round 4 (review on a79718b8): the global rule. A sentence anywhere with a negation and an
# e-mail word makes every instructed address unsure, above, below or far from the instruction.
GLOBAL = [
    "UPDATE: we no longer accept applications by email, please use the Apply button.\n\n"
    f"Send your CV and cover letter to {A}.",
    f"UPDATE: WE NO LONGER ACCEPT APPLICATIONS BY EMAIL.\n\nSEND YOUR CV TO {A}",
    f"We do not accept applications by email.\n\nSend your CV to {A}",
    f"Applications by email are not accepted.\n\nSend your CV to {A}",
    f"Applications by e-mail are no longer read.\n\nSend your CV to {A}",
    f"Les candidatures par mail ne sont plus acceptées.\n\nEnvoyez votre CV à {A}",
    f"Nous n'acceptons plus les candidatures par e-mail.\n\nEnvoyez votre CV à {A}",
    f"Candidatures par courriel refusées.\n\nEnvoyez votre CV à {A}",
    f"Aucune candidature par adresse électronique.\n\nEnvoyez votre CV à {A}",
    f"Send your CV to {A}\n\nUpdate: applications by email are no longer accepted, use the form.",
    f"Send your CV to {A}.\n\nAbout us\n\nWe build robots.\n\nBenefits\n\n- Lunch\n- Gym\n\n"
    "Note: emailed applications will be ignored.",
    "Requirements\n- Python\n- Please do not email the team\n\n"
    f"How to apply\n\nSend your CV to {A}",
    f"Envoyez votre CV à {A}\n\nMise à jour : les mails ne sont plus lus.",
    f"Send your CV to {A}\n\nBewerbungen per E-Mail werden nicht berücksichtigt.",
    f"Send your CV to {A}\n\nBitte keine Bewerbungen per Mail.",
    f"Send your CV to {A}\n\nSollicitaties per e-mail worden niet gelezen.",
    f"Send your CV to {A}\n\nGeen sollicitaties per mail.",
]

# Case: the same address with another case is the same address.
CASES = [
    "Send your CV to E2E-Jobs@example.org.\n\nPlease do not e-mail e2e-jobs@example.org.",
    "Send your CV to e2e-jobs@example.org.\n\nPlease do not contact E2E-JOBS@example.org.",
    "Please do not contact E2E-Jobs@Example.org.\n\nSend your CV to e2e-jobs@example.org",
]

# Round 4 follow ups: a heading two levels up, pasted HTML, German and Dutch negations.
HEADINGS_AND_HTML = [
    f"No email applications\n\nSend your CV to {A}",
    f"How NOT to apply\n\nBy email\n\nSend your CV to {A}",
    f"## What we do not accept\n\n### Email\n\nSend your CV to {A}",
    f"# Do not apply this way\n\n## Contact\n\nWe read everything.\n\nSend your CV to {A}",
    f"<p>Please do not:</p>\n\n<ul>\n\n<li>send your CV to {A}</li>",
    f"<p>Please do not:</p><ul><li>call us</li><li>send your CV to {A}</li></ul>",
    f"<h2>How not to apply</h2>\n\n<p>Send your CV to {A}</p>",
    f"Bitte nicht:\n- send your CV to {A}",
    f"Keine Agenturen. Send your CV to {A}",
    f"Niet doen:\n- send your CV to {A}",
    f"Geen bureaus. Send your CV to {A}",
]

# Round 5: words that name the address itself, a correction above an old instruction.
ADDRESS_WORDS = [
    f"UPDATE: this address is no longer monitored, please use the form.\n\nSend your CV to {A}.",
    f"This inbox is not monitored any more.\n\nSend your CV to {A}.",
    f"Les candidatures par mél ne sont plus acceptées.\n\nEnvoyez votre CV à {A}.",
    f"THIS ADDRESS IS NO LONGER MONITORED.\n\nSend your CV to {A}.",
    f"## This mailbox is not read any more\n\nSend your CV to {A}.",
    f"Cette boîte n'est plus lue.\n\nEnvoyez votre CV à {A}.",
    f"La messagerie ne reçoit plus de candidatures.\n\nEnvoyez votre CV à {A}.",
    f"The contact address is not read any more.\n\nSend your CV to {A}.",
    f"We don&rsquo;t accept applications by email.\n\nSend your CV to {A}.",
]

# Still usable: a negation without an e-mail word elsewhere, an invitation, an address in a
# word, a heading that does not negate.
GLOBAL_TRUE = [
    f"No agencies please.\n\nSend your CV to {A}",
    f"We do not sponsor visas.\n\nApply by email to {A}",
    f"Do not hesitate to email us.\n\nSend your CV to {A}",
    f"N'hésitez pas à nous envoyer un mail.\n\nEnvoyez votre CV à {A}",
    f"No remote work.\n\nPostulez par mail à {A}",
    f"## About us\n\n### How to apply\n\nSend your CV to {A}",
    f"<p>How to apply</p>\n<p>Send your CV to <b>{A}</b></p>",
    f"<p>Send your CV to &lt;{A}&gt;</p>",
    f"Send your CV to {A}.\n\nJane, e2e-no@example.org",
    f"Do not hesitate to write to this address.\n\nSend your CV to {A}",
]


@pytest.mark.parametrize("text", GLOBAL + HEADINGS_AND_HTML)
def test_round4_a_negation_anywhere_about_email_makes_every_address_unsure(text):
    assert email.read(text) == email.Reading((), (A,))


@pytest.mark.parametrize("text", ADDRESS_WORDS)
def test_round5_words_naming_the_address_itself_make_every_address_unsure(text):
    assert email.read(text) == email.Reading((), (A,))


@pytest.mark.parametrize("text", CASES)
def test_round4_addresses_are_compared_casefolded(text):
    reading = email.read(text)
    assert reading.usable == ()
    assert [v.casefold() for v in reading.unsure] == [A]


@pytest.mark.parametrize("text", GLOBAL_TRUE)
def test_round4_a_negation_without_an_email_word_elsewhere_stays_out_of_scope(text):
    assert email.read(text).usable == (A,)


def test_round4_mutation_without_the_global_rule_the_far_sentences_turn_usable(monkeypatch):
    """The GLOBAL rows are refused by the global rule alone: switched off, each turns usable."""
    monkeypatch.setattr(email, "_forbids_email", lambda paragraphs: False)
    for text in GLOBAL:
        assert email.read(text).usable == (A,), text


def test_round4_an_unsure_posting_keeps_apply_myself_on_the_form(db, person_a):
    """The global rule leaves nothing usable, so Apply myself is not refused with
    email_manual: the posting keeps its form."""
    application = _start(db, person_a, GLOBAL[0], Mode.manual)
    assert application.ats == "other"


def test_round4_full_auto_hands_the_global_rule_over_and_never_emails(db, person_a):
    turn_on(db, person_a, done=3)
    application = _start(db, person_a, GLOBAL[0], Mode.full_auto)
    email.prepare(db, application.id, AT)
    db.commit()
    assert application.mode is Mode.review
    codes = db.scalars(
        sa.select(ApplicationEvent.data["code"].astext).where(
            ApplicationEvent.application_id == application.id,
            ApplicationEvent.kind == "sent_to_review",
        )
    ).all()
    assert codes == ["email_unsure"]
    row = _email_to(db, application)
    assert (row.value, row.source, row.options) == (None, AnswerSource.left, [A])


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
