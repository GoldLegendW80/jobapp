"""Which postings become applications by email (round 2 of #687): a negated instruction gives
no address, and a posting whose address is a form of its own is never an email application."""

import pytest

from app.apply import email, service
from app.apply.models import Mode
from app.jobs.models import Job
from tests.apply.conftest import AT

A = "e2e-jobs@example.org"
B = "e2e-team@example.org"

# Every reviewer input and its neighbours, one row each, never deleted.
NEGATED = [
    f"Do not send your CV to {A}",
    f"Don't send your application to {A}, use the form",
    f"Don’t send your application to {A}, use the form",
    f"Dont send your CV to {A}",
    f"Don t send your CV to {A}",
    f"DO NOT SEND YOUR CV TO {A}",
    f"Please do not email your CV to {A}",
    f"please do not email your resume to {A}",
    f"Never email your resume to {A}",
    f"Never send your CV to {A}",
    f"You should not send your application to {A}",
    f"Candidates must not submit their CV to {A}",
    f"We cannot accept CVs sent to us, so do not apply to {A}",
    f"No need to send your CV to {A}",
    f"Agencies, please do not send CVs to {A}",
    f"Recruitment agencies: do not forward resumes to {A}",
    f"Instead of sending an email, use the form. Rather than email your CV to {A}, apply online",
    f"Avoid sending your CV to {A}, send your CV through the form",
    f"Send your CV through the form, not to {A}",
    f"Send your CV to the form rather than to {A}",
    f"Send your CV to {A} unless you are an agency",
    f"Please do not\nsend your CV to {A}",
    f"Ne pas envoyer de CV à {A}",
    f"Ne pas envoyer de CV a {A}",
    f"N'envoyez pas votre CV à {A}",
    f"N’envoyez pas votre CV à {A}",
    f"n'envoyez pas de candidature à {A}",
    f"N'envoyez jamais votre CV à {A}",
    f"Merci de ne pas envoyer votre CV à {A}",
    f"Inutile d'envoyer votre CV à {A}",
    f"Évitez d'envoyer votre CV à {A}, postulez sur le site",
    f"Plutôt que d'envoyer votre CV à {A}, utilisez le formulaire",
    f"Au lieu d'envoyer votre CV à {A}, utilisez le formulaire",
    f"Aucun CV envoyé à {A} ne sera lu, envoyez votre CV via le site",
    f"Les agences, merci de ne pas envoyer de CV à {A}",
    f"Envoyez votre CV via le formulaire, pas à {A}",
    f"Envoyez votre CV via le formulaire et non à {A}",
    f"Ne postulez pas à {A}",
    f"Send your CV to {A}, no phone calls",
]

TRUE = [
    (f"Send your CV to {A}", [A]),
    (f"Send CVs to {A}", [A]),
    (f"Please send your CV to {A}.", [A]),
    (f"Do not hesitate to send your CV to {A}", [A]),
    (f"Don't hesitate to send your application to {A}", [A]),
    (f"N'hésitez pas à envoyer votre CV à {A}", [A]),
    (f"N’hésitez pas à envoyer votre candidature à {A}", [A]),
    (f"Envoyez votre CV à {A} ou {B}", [A, B]),
    (f"No agencies please. Send your CV to {A}", [A]),
    (f"Pas d'agences. Envoyez votre CV à {A}", [A]),
    (f"No remote work.\nSend your CV to {A}", [A]),
    (f"- No remote work\n- Send your CV to {A}", [A]),
    (f"Not a fit? Send your CV to {A}", [A]),
    (f"Do not send your CV to {B}. Send your CV to {A}", [A]),
    (f"Ne pas envoyer de CV à {B}.\n\nEnvoyez votre CV à {A}", [A]),
    ("Send your CV to e2e-notion@example.org", ["e2e-notion@example.org"]),
    ("Send your CV to e2e-no@example.org", ["e2e-no@example.org"]),
    (f"How to apply:\nSend your CV to {A}", [A]),
    (f"Talent team, recruitment: send your CV to {A}", [A]),
    (f"Envoyez votre CV et votre lettre à {A}", [A]),
]


@pytest.mark.parametrize("text", NEGATED)
def test_negated_instruction_gives_no_address(text):
    assert email.recipients(text) == []


@pytest.mark.parametrize(("text", "expected"), TRUE)
def test_true_instruction_keeps_its_address(text, expected):
    assert email.recipients(text) == expected


@pytest.mark.parametrize(
    ("url", "form"),
    [
        ("", False),
        ("   ", False),
        (f"mailto:{A}", False),
        (f"MAILTO:{A}", False),
        ("https://careers.example.org/jobs/7", False),
        ("http://example.org/careers", False),
        ("https://jobs.ashbyhq.com/acme/0b7e5c2e-0000-4000-8000-000000000000", True),
        ("https://boards.greenhouse.io/acme/jobs/123", True),
        ("https://jobs.lever.co/acme/abc", True),
        ("https://acme.wd3.myworkdayjobs.com/en-US/careers/job/7", True),
        ("https://apply.workable.com/acme/j/7", True),
        ("https://acme.teamtailor.com/jobs/7", True),
        ("https://example.org/careers?gh_jid=123", True),
        ("https://www.linkedin.com/jobs/view/7", True),
        ("https://www.welcometothejungle.com/fr/companies/acme/jobs/7", True),
        ("https://acme.notion.site/Careers", True),
        ("ftp://example.org/job.txt", True),
        ("example.org/careers", True),
    ],
)
def test_posting_address_with_its_own_form(url, form):
    assert email.has_own_form(url) is form
    posting = Job(url=url, posting_text=f"Send your CV to {A}")
    assert email.addresses(posting) == ([] if form else [A])


def test_mailto_posting_names_its_own_address_whatever_the_text():
    posting = Job(url=f"mailto:{B}", posting_text=f"Do not send your CV to {A}")
    assert email.addresses(posting) == [B]


@pytest.mark.parametrize(
    ("url", "text", "ats"),
    [
        # The reviewer's probe: a company's own form and a negated address is a form application.
        (
            "https://careers.example.org/jobs/7",
            f"Don't send your application to {A}, use the form",
            "other",
        ),
        ("https://boards.greenhouse.io/acme/jobs/123", f"Send your CV to {A}", "greenhouse"),
        ("https://careers.example.org/jobs/7", f"Send your CV to {A}", "email"),
    ],
)
def test_start_makes_email_only_from_a_positive_instruction_without_a_form(
    db, person_a, url, text, ats
):
    posting = db.get(Job, person_a.posting_id)
    posting.url, posting.posting_text = url, text
    db.commit()
    application = service.start(
        db, person_a.user_id, posting.id, Mode.review, False, now=AT
    ).application
    assert application.ats == ats
    assert (application.canonical_key or "").startswith("email:") is (ats == "email")


def test_full_auto_like_never_targets_a_negated_address(db, person_a):
    """In Full auto, the company's own page with a negated address is not an email application,
    so the Like can never send to it (own_company alone would accept the domain)."""
    posting = db.get(Job, person_a.posting_id)
    posting.url = "https://careers.example.org/jobs/7"
    posting.posting_text = f"Don't send your application to {A}, use the form"
    db.commit()
    assert email.own_company(posting, A)
    assert email.addresses(posting) == []
