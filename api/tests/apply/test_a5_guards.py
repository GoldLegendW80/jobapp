"""The guards of A5's run (review of PR #312): what the answer agent is never asked, an
application that never stays `filling`, a step for the person seen at any point of the fill, and
people taking turns on the one-at-a-time queue. Real Postgres, the fakes of a5_support."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import worker
from app.apply import agent, run, terms
from app.apply.ats import base as ats_base
from app.apply.models import Application, Mode, State
from app.jobs import service as jobs_service
from app.jobs.models import JobStatus
from tests.apply.a5_support import FakeAgent, FakeLeases, run_ashby
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import (
    WHY,
    Clock,
    application,
    events,
    fill,
    services,
    started,
    status,
    why_answer,
)


@pytest.fixture
def stub(forms_base_url: str) -> str:
    return forms_base_url


# --- what the agent is never asked -----------------------------------------------------------
# The owner's rules: an answer never invents a claim; legal and consent questions, pay, work
# authorization, criminal and employment history, identity and credentials stay with the person.
# Two lines of defence: a question the resolver does not know is asked only when its label reads
# as an open question or a prompt (`not_open` otherwise), and the vocabularies refuse a topic in
# any wording. `None`: the question goes to the agent.

YN = ("Yes", "No")
ON = ("Oui", "Non")
THREE = ("First", "Second", "Third")
HUGE = "Why Example Robotics? " + "Tell us more about the robots you would build. " * 400

# The reviewer's leaks of round 2 (PR #312): each asked and its "None" kept before. The code is
# the vocabulary's, so dropping a vocabulary turns its rows red even where `not_open` would also
# refuse the label.
LEAKS = [
    # pay
    ("Desired comp", "pay"),
    ("Comp expectations", "pay"),
    ("Total comp", "pay"),
    ("Desired total comp", "pay"),
    ("TC expectations", "pay"),
    ("Your expected TC?", "pay"),
    ("How much do you expect to make?", "pay"),
    ("Budget expectations", "pay"),
    ("Gross annual income expected", "pay"),
    ("Annual income", "pay"),
    ("Hourly", "pay"),
    ("Current base", "pay"),
    ("Revenu annuel", "pay"),
    ("Gehaltsvorstellung", "pay"),
    ("Pretensión salarial", "pay"),
    # work authorization and immigration
    ("Work permit", "authorization"),
    ("Work permit status", "authorization"),
    ("Right to work", "authorization"),
    ("Right to work in the UK", "authorization"),
    ("Green card", "authorization"),
    ("Green card holder?", "authorization"),
    ("Residency status", "authorization"),
    ("Permanent resident?", "authorization"),
    ("Visa", "authorization"),
    ("Visa type", "authorization"),
    ("Sponsor required", "authorization"),
    ("Eligibility to work in Canada", "authorization"),
    ("Titre de séjour", "authorization"),
    ("Permis de travail", "authorization"),
    ("Arbeitserlaubnis", "authorization"),
    # criminal and employment history
    ("Pending charges", "history"),
    ("Arrests", "history"),
    ("Misdemeanors", "history"),
    ("DBS check", "history"),
    ("Fired from a job?", "history"),
]
# The same topics worded as open questions: the shape lets them through, so the vocabulary is
# the only guard (defence in depth).
OPEN_LEAKS = [
    ("What are your comp expectations?", "pay"),
    ("What is your annual income?", "pay"),
    ("What is your daily rate?", "pay"),
    ("Wie viel möchten Sie verdienen?", "pay"),
    ("Welche Gehaltsvorstellung haben Sie?", "pay"),
    ("¿Cuál es su pretensión salarial?", "pay"),
    ("¿Cuál es su sueldo deseado?", "pay"),
    ("Quel est votre revenu annuel ?", "pay"),
    ("What is your work permit status?", "authorization"),
    ("Describe your right to work in the UK", "authorization"),
    ("Which visa do you hold?", "authorization"),
    ("What is your residency status?", "authorization"),
    ("What is your nationality?", "authorization"),
    ("What is your H-1B status?", "authorization"),
    ("How did you get your green card?", "authorization"),
    ("What is your immigration status?", "authorization"),
    ("Explain your OPT or CPT situation", "authorization"),
    ("Quel est votre titre de séjour ?", "authorization"),
    ("Quelle est votre autorisation de travail ?", "authorization"),
    ("Welche Arbeitserlaubnis haben Sie?", "authorization"),
    ("Wie ist Ihr Aufenthaltsstatus?", "authorization"),
    ("¿Cuál es su permiso de trabajo?", "authorization"),
    ("Tell us about any pending charges", "history"),
    ("Why were you fired from your last job?", "history"),
    ("Explain any arrests or convictions", "history"),
    ("Describe any disciplinary action taken against you", "history"),
    ("Why were you dismissed?", "history"),
    ("What offences have you committed?", "history"),
    ("Quelles condamnations figurent à votre casier ?", "history"),
    # the author's own probe of the same families
    ("¿Cuánto quieres ganar?", "pay"),
    ("What are your financial expectations?", "pay"),
    ("What is your right-to-work status?", "authorization"),
    ("Welche Arbeitsgenehmigung haben Sie?", "authorization"),
    ("Welche Staatsangehörigkeit haben Sie?", "authorization"),
    ("Which work pass do you hold?", "authorization"),
    ("What is your EAD status?", "authorization"),
    ("¿Cuál es su nacionalidad?", "authorization"),
    ("¿Cuál es su ciudadanía?", "authorization"),
    ("¿Cuál es su situación de patrocinio?", "authorization"),
    ("Quel est votre besoin de parrainage ?", "authorization"),
    ("Welche Vorstrafen haben Sie?", "history"),
    ("¿Cuáles son sus antecedentes penales?", "history"),
    # round 3 (PR #312): closed topics worded with an open opener, asked before
    ("How much do you make today?", "pay"),
    ("How much did you make in your last role?", "pay"),
    ("How much do you take home?", "pay"),
    ("How much are you looking for?", "pay"),
    ("How much do you want?", "pay"),
    ("What range are you looking at?", "pay"),
    ("What figure do you have in mind?", "pay"),
    ("What are you hoping to make?", "pay"),
    ("What ballpark are you in?", "pay"),
    ("What is a fair price for your work?", "pay"),
    ("What was your last raise?", "pay"),
    ("What is your 2025 target?", "pay"),
    ("What is your last drawn?", "pay"),
    ("What is your ECTC?", "pay"),
    ("What is your price?", "pay"),
    ("How would you describe your financial situation?", "pay"),
    ("Comment êtes-vous payé aujourd'hui ?", "pay"),
    ("Quelle est votre fourchette ?", "pay"),
    ("Cómo está pagado actualmente?", "pay"),
    ("Cuál es su expectativa económica?", "pay"),
    ("Quelle est votre situation au regard du droit de travail en France ?", "authorization"),
    ("What is your legal status in the country?", "authorization"),
    ("What's your status in the US?", "authorization"),
    ("Parlez-nous de votre situation administrative", "authorization"),
    ("Describa su situación migratoria", "authorization"),
    ("What is the highest level of security vetting you hold?", "authorization"),
    ("Why were you let go from your last role?", "history"),
    ("Explain your gap", "history"),
    ("Which religion do you follow?", "personal"),
    ("What is your marital status?", "personal"),
    ("How many children do you have?", "personal"),
    ("Which medications do you take?", "personal"),
    ("Tell us about your family situation", "personal"),
    ("What accommodations do you need?", "personal"),
    ("What is your caste?", "personal"),
    ("What is your political affiliation?", "personal"),
    ("Which pronouns do you use?", "personal"),
    ("What city do you live in?", "personal"),
    ("Which state do you live in?", "personal"),
    ("What is today's date?", "personal"),
    ("How old are you?", "personal"),
    ("What year were you born?", "personal"),
    ("Share information about your health", "personal"),
    # data sharing, retention and communications (they were refused before `share` and digits left
    # `terms.legal_reading`)
    ("Share my data with partners", "consent_topic"),
    ("Share my profile with other companies", "consent_topic"),
    ("Share my details with our network", "consent_topic"),
    ("Share your CV with our subsidiaries", "consent_topic"),
    ("Share my application with the group", "consent_topic"),
    ("Share my data with the hiring team", "consent_topic"),
    ("Share my data for 12 months", "consent_topic"),
    ("Share my information with recruiters", "consent_topic"),
    ("Share my information with our group companies", "consent_topic"),
    ("Which companies may we share your data with?", "consent_topic"),
    ("How long may we keep your data?", "consent_topic"),
    ("How many years may we store your data?", "consent_topic"),
    ("How would you like to receive our offers?", "consent_topic"),
    ("Which communications do you want to receive?", "consent_topic"),
    ("How do you want to hear from us?", "consent_topic"),
    ("Which privacy settings do you choose?", "consent_topic"),
]

# Closed topics and plain data questions that no vocabulary names: the topic anchor
# (`_ABOUT_THE_JOB`) refuses them, since the question is not about the job, the company, the team
# or the person's motivation and experience. A yes/no select is refused by its options first.
OFF_TOPIC = [
    "What is the number you have in mind?",
    "What do you bring in?",
    "What is your number?",
    "Describe your status",
    "Quel est votre statut ?",
    "What restrictions apply to you?",
    "What are the legal requirements you must meet?",
    "Why did you apply?",
    "Which languages do you speak?",
    "Which university did you attend?",
    "What is your GPA?",
    "What is the name of your current manager?",
    "How did you hear about us?",
    "How do you handle 2FA?",
    "Describe your last 3 jobs",
    "Why have you had so many roles in two years?",
    # an employer word alone, outside a question of motivation (issue #317): "How did you hear
    # about us?" in French; A3's `how they heard` answers it from the profile
    "Comment avez-vous connu notre entreprise ?",
]

# Leaving a job and the person's practical situation (review of PR #323): the profile holds no
# reason for leaving, and the model never reads the answers on notice, start, relocation, travel
# or on site work, so each is refused in any wording, even when it also names the role, the
# company or the team that `_ABOUT_THE_JOB` would let through.
LEAVING_AND_PRACTICAL = [
    ("Why did you leave your last job?", "history"),
    ("Why did you leave your last role?", "history"),
    ("Why did you leave your previous company?", "history"),
    ("Why do you want to leave your current position?", "history"),
    ("Why did you leave your last team?", "history"),
    ("Why are you leaving your current company?", "history"),
    ("Why did your last role end?", "history"),
    ("Why did your contract with Example Robotics end?", "history"),
    ("Why was your contract with your last company not renewed?", "history"),
    ("Why did you change companies so often?", "history"),
    ("Why are you looking to change roles?", "history"),
    ("Why did you quit your role?", "history"),
    ("Why does your current company not know you are applying?", "history"),
    ("Why doesn't your employer know you are applying?", "history"),
    ("Describe your previous experience and the reason you left", "history"),
    ("What is your reason for leaving?", "history"),
    ("Pourquoi avez-vous quitté votre dernier emploi ?", "history"),
    ("Pourquoi voulez-vous quitter votre poste actuel ?", "history"),
    ("Pourquoi avez-vous quitté votre entreprise ?", "history"),
    ("Pourquoi souhaitez-vous changer de poste ?", "history"),
    ("Pourquoi votre contrat n'a-t-il pas été renouvelé ?", "history"),
    ("Warum haben Sie Ihren letzten Job verlassen?", "history"),
    ("Warum wollen Sie Ihre Stelle wechseln?", "history"),
    ("Por qué dejó su último trabajo?", "history"),
    ("¿Por qué quiere salir de su empresa?", "history"),
    ("Why can you not start immediately in this role?", "practical"),
    ("Why is your start date so late for this position?", "practical"),
    ("How soon can you start?", "practical"),
    ("What is your earliest start date?", "practical"),
    ("What experience do you have and when can you start?", "practical"),
    ("What is your availability?", "practical"),
    ("Describe your availability for this role", "practical"),
    ("Quelle est votre disponibilité pour ce poste ?", "practical"),
    ("¿Cuál es su disponibilidad?", "practical"),
    ("Why would you relocate for this role?", "practical"),
    ("Tell us about your relocation needs", "practical"),
    ("Pourquoi souhaitez-vous déménager pour ce poste ?", "practical"),
    ("Why do you want to work remotely in this role?", "practical"),
    ("Why do you prefer on-site work in this team?", "practical"),
    ("Describe your ideal hybrid setup for this role", "practical"),
    ("Pourquoi le télétravail pour ce poste ?", "practical"),
    ("How do you feel about the commute to our office?", "practical"),
    ("Describe the travel you can do for this role", "practical"),
    ("What other offers do you have for similar roles?", "practical"),
    ("Quelles autres candidatures avez-vous en cours ?", "practical"),
    ("Which other companies are you interviewing with?", "practical"),
    ("What documents do you have to work in the UK?", "practical"),
    ("Why do you want to work in the US?", "practical"),
    ("Why do you want to work in Paris?", "practical"),
    ("Pourquoi voulez-vous travailler à Paris ?", "practical"),
    # a place or an arrangement word, whatever the verb (issue #317; `off_topic` before)
    ("What is your current location?", "practical"),
    ("What is your work arrangement?", "practical"),
]


# The `not_open` rows of the table below that are still about the job: written by A3 as free text,
# they are asked; the other `not_open` rows are `off_topic` on that route ("Team preference": an
# employer word puts a label on topic only in a question of motivation, issue #317).
FREE_TEXT_ON_TOPIC = {
    "Favourite colour",
    "Kubernetes experience",
    "Experience with Kubernetes: why?",
    "Motivation for the role",
}


@pytest.mark.parametrize(("label", "code"), LEAKS + OPEN_LEAKS + LEAVING_AND_PRACTICAL)
@pytest.mark.parametrize(("kind", "options"), [("text", ()), ("select", THREE), ("select", YN)])
def test_pay_authorization_and_history_stay_the_persons_in_any_widget(
    label: str, code: str, kind: str, options: tuple[str, ...]
) -> None:
    assert agent.refusal(label, kind, options) == code
    item = ats_base.Field(key="k", label=label, kind=kind, options=options)  # type: ignore[arg-type]
    for reason in agent.AGENT_REASONS:
        outcome = ats_base.Outcome(item, run.Left(reason, options), ats_base.Filled("left", reason))
        assert agent.eligible([outcome]) == []
        asked = agent.Question("k", label, kind, options, reason=reason)
        value = options[0] if options else "None"
        reply = agent.Reply((agent.Proposal("k", value, "answers"),))
        (verdict,) = agent.judge([asked], reply, agent.sources_of({}, "Engineer", "Example"))
        assert (verdict.answer, verdict.code) == (None, code)


@pytest.mark.parametrize("label", OFF_TOPIC)
@pytest.mark.parametrize(("kind", "options"), [("text", ()), ("select", THREE), ("select", YN)])
def test_a_question_not_about_the_job_stays_the_persons_in_any_widget(
    label: str, kind: str, options: tuple[str, ...]
) -> None:
    code = "yes_or_no" if options == YN else "off_topic"
    assert agent.refusal(label, kind, options) == code
    item = ats_base.Field(key="k", label=label, kind=kind, options=options)  # type: ignore[arg-type]
    for reason in agent.AGENT_REASONS:
        outcome = ats_base.Outcome(item, run.Left(reason, options), ats_base.Filled("left", reason))
        # Issue #317: A3's free text questions need no opener, but they are held to the topic too.
        assert agent.refusal(label, kind, options, reason) == code
        assert agent.eligible([outcome]) == []


@pytest.mark.parametrize(
    ("label", "kind", "options", "code"),
    [
        # the reviewer's probe of round 1, every one asked and accepted before
        ("Do you authorize us to run a background check?", "text", (), "legal"),
        ("Do you agree to the arbitration agreement?", "select", YN, "legal"),
        ("Do you agree to binding arbitration?", "text", (), "legal"),
        ("I would like to receive marketing emails", "select", YN, "legal"),
        ("Would you like to join our talent community?", "select", YN, "legal"),
        ("Can we keep your data for future opportunities?", "text", (), "consent_topic"),
        ("Do you agree to our terms and conditions?", "text", (), "legal"),
        ("I certify that the information provided is true", "text", (), "legal"),
        ("Are you subject to a non-compete agreement?", "text", (), "legal"),
        ("What's your expected OTE?", "text", (), "pay"),
        ("Expected annual package", "text", (), "pay"),
        ("Password", "text", (), "identity"),
        ("Social security number", "text", (), "identity"),
        ("What is your home address?", "text", (), "identity"),
        ("Please type your full legal name as an electronic signature", "text", (), "legal"),
        ("Do you have a PhD?", "text", (), "unproven"),
        ("Do you hold a security clearance?", "select", YN, "unproven"),
        ("Do you have experience fine-tuning LLMs?", "text", (), "unproven"),
        ("Are you a US person under export control regulations?", "text", (), "unproven"),
        # French forms
        ("Acceptez-vous l'arbitrage en cas de litige ?", "text", (), "legal"),
        ("J'accepte les conditions générales", "text", (), "legal"),
        ("Autorisez-vous une vérification de vos antécédents ?", "text", (), "legal"),
        ("Souhaitez-vous rejoindre notre vivier de talents ?", "select", ON, "legal"),
        ("Pouvons-nous conserver votre CV ?", "text", (), "legal"),
        ("Rémunération annuelle brute souhaitée", "text", (), "pay"),
        ("Prétentions salariales", "text", (), "pay"),
        ("Mot de passe", "text", (), "identity"),
        ("Numéro de sécurité sociale", "text", (), "identity"),
        ("Adresse postale", "text", (), "identity"),
        ("Nom et prénom", "text", (), "identity"),
        ("Êtes-vous titulaire d'un doctorat ?", "text", (), "unproven"),
        ("Disposez-vous d'une habilitation secret défense ?", "text", (), "unproven"),
        ("Signature électronique", "text", (), "legal"),
        # neighbours: case, punctuation, spacing, other wordings
        ("PASSWORD:", "text", (), "identity"),
        ("Confirm  pass word", "text", (), "identity"),
        ("Hourly rate (USD)", "text", (), "pay"),
        ("Bonus expectations", "text", (), "pay"),
        ("Equity expectations?", "text", (), "pay"),
        ("On-target earnings", "text", (), "pay"),
        ("Phone number", "text", (), "identity"),
        ("E-mail address", "text", (), "identity"),
        ("Date of birth", "text", (), "eeo"),
        ("National Insurance number", "text", (), "identity"),
        ("Tax ID", "text", (), "identity"),
        ("Passport number", "text", (), "authorization"),
        ("Are you a US citizen?", "text", (), "authorization"),
        ("Do you now or in the future require sponsorship?", "text", (), "authorization"),
        ("Have you ever been convicted of a felony?", "text", (), "history"),
        ("I acknowledge the privacy notice", "text", (), "consent_topic"),
        ("Do you consent to us sharing your data with partners?", "text", (), "consent_topic"),
        ("Do you enjoy puzzles?", "yes_no", (), "yes_or_no"),
        ("Kubernetes in production", "select", YN, "yes_or_no"),
        ("Kubernetes in production", "select", ("Yes, daily", "Not yet"), "yes_or_no"),
        (
            "Which describes you best?",
            "select",
            ("Citizen", "Permanent resident", "Other"),
            "option_topic",
        ),
        (
            "Which applies to you?",
            "select",
            ("Need a visa", "Hold a work permit", "Other"),
            "option_topic",
        ),
        (
            "Which describes you?",
            "select",
            ("Salary under 50k", "Salary over 50k"),
            "option_topic",
        ),
        ("Which applies to you?", "select", ("Clean criminal record", "Other"), "option_topic"),
        ("Have you used Kubernetes in production?", "text", (), "yes_or_no"),
        ("Please confirm whether you can travel", "text", (), "practical"),
        ("Est-ce que vous parlez allemand ?", "text", (), "yes_or_no"),
        ("Haben Sie einen Führerschein?", "text", (), "yes_or_no"),
        ("Haben Sie Erfahrung mit Kubernetes?", "text", (), "yes_or_no"),
        ("¿Tiene experiencia con Kubernetes?", "text", (), "yes_or_no"),
        ("Is there anything else you would like us to know?", "text", (), "yes_or_no"),
        ("AWS certification", "text", (), "unproven"),
        ("Team preference", "combobox", (), "no_options"),
        ("Portfolio", "file", (), "kind"),
        ("", "text", (), "no_label"),
        ("   ", "text", (), "no_label"),
        # not an open question nor a prompt: a bare noun phrase, a statement, a question whose
        # opener is not on the list. Declared over-refusals: "Team preference" with its options,
        # "Anything else you'd like to share?".
        ("Team preference", "combobox", ("Ops", "Research"), None),
        ("Team preference", "select", THREE, None),
        ("Favourite colour", "text", (), "not_open"),
        ("Availability", "text", (), "practical"),
        ("Comments", "text", (), "not_open"),
        ("Anything else you'd like to share?", "text", (), "not_open"),
        ("Where are you based?", "text", (), "not_open"),
        ("Who referred you?", "text", (), "not_open"),
        ("Kubernetes experience", "select", THREE, "not_open"),
        ("Experience with Kubernetes: why?", "text", (), "not_open"),
        ("Somewhat related: what do you like?", "text", (), "not_open"),
        ("Was motiviert Sie?", "text", (), "yes_or_no"),  # English "was" wins: over-refusal
        ("Motivation for the role", "text", (), "not_open"),
        ("Cover letter text", "text", (), "document"),
        ("Whatever you like", "text", (), "not_open"),
        ("Howdy", "text", (), "not_open"),
        ("Commentaires", "text", (), "not_open"),
        # true free text, still asked (declared open points: "How many years of Python?", answered
        # from the model's text and A3's years rule; the judge drops numbers the profile lacks)
        ("How many years of Python?", "text", (), None),
        ("How many years of experience do you have with Kubernetes?", "text", (), None),
        ("What are you looking for in your next role?", "text", (), None),
        ("How would you make an agent reliable?", "text", (), None),
        ("Describe a neural network you built", "text", (), None),
        ("What range of tools have you used?", "text", (), None),
        ("Tell us about a bug you figured out", "text", (), None),
        ("Share your portfolio", "text", (), None),
        ("Please share your GitHub profile", "text", (), None),
        ("Share the details of a project you led", "text", (), None),
        ("Tell us in 100 words why you want this job", "text", (), None),
        ("Why us?", "text", (), None),
        ("Describe a healthcare project you led", "text", (), None),
        ("What is the status of your project?", "text", (), None),
        ("1. Why do you want to join us?", "text", (), None),
        ("What motivates you?", "text", (), None),
        ("Tell us about yourself", "text", (), None),
        ("Describe your biggest achievement", "text", (), None),
        ("Explain your interest in the company", "text", (), None),
        ("Quelle est votre motivation ?", "text", (), None),
        ("Décrivez votre expérience", "text", (), None),
        ("Why Example Robotics? (optional)", "text", (), None),
        ("Why do you want to work here?", "text", (), None),
        ("Why Example Robotics?", "text", (), None),
        ("WHY EXAMPLE ROBOTICS", "text", (), None),
        ("  * 1. Why Example Robotics?", "text", (), None),
        ("What is your favourite robot?", "text", (), None),
        ("What's your favourite robot?", "text", (), None),
        ("What would you build in your first 90 days?", "text", (), None),
        ("Which team interests you?", "select", ("Ops", "Research"), None),
        ("Which team interests you?", "select", THREE, None),
        ("How do you approach LLM evaluation?", "text", (), None),
        ("Tell us about a project", "text", (), None),
        ("Tell us about a project you are proud of", "text", (), None),
        ("Describe a project you are proud of", "text", (), None),
        ("Describe a project you were in charge of", "text", (), None),
        ("Describe your experience with LLM evaluation", "text", (), None),
        ("Please describe your experience with LLM evaluation", "text", (), None),
        ("Briefly explain how you test agents", "text", (), None),
        ("In a few sentences, tell us why this role", "text", (), None),
        ("Share an example of a hard bug you fixed", "text", (), None),
        ("Walk us through a system you designed", "text", (), None),
        ("Give an example of a trade-off you made", "text", (), None),
        ("What excites you about this role?", "text", (), None),
        ("What makes you a great fit for this role?", "text", (), None),
        ("Motivation", "text", (), None),
        ("Motivation:", "text", (), None),
        ("Additional information", "text", (), None),
        # a document is the document's text (`letter_boxes`) or the person's, never the model's
        ("Cover letter", "text", (), "document"),
        ("Paste your cover letter", "text", (), "document"),
        ("Resume", "text", (), "document"),
        ("Pourquoi nous rejoindre ?", "text", (), None),
        ("Pourquoi souhaitez-vous rejoindre Example Robotics ?", "text", (), None),
        ("Décrivez un projet dont vous êtes fier", "text", (), None),
        ("Qu'est-ce qui vous motive dans ce poste ?", "text", (), None),
        ("Parlez-nous d'un projet", "text", (), None),
        ("Quelles technologies préférez-vous ?", "text", (), None),
        ("Comment évaluez-vous un agent ?", "text", (), None),
        ("Expliquez votre approche des tests", "text", (), None),
        ("Partagez un exemple de projet", "text", (), None),
        ("Lettre de motivation", "text", (), "document"),
        # the leak labels of the review of PR #323 the resolver leaves as unknown
        ("Describe your availability for this role", "text", (), "practical"),
        ("Describe your previous experience and the reason you left", "text", (), "history"),
        ("What experience do you have and when can you start?", "text", (), "practical"),
        ("Warum wollen Sie Ihre Stelle wechseln?", "text", (), "history"),
        ("Which other companies are you interviewing with?", "text", (), "practical"),
        # a declared over refusal: "left" reads as leaving a job
        ("Is there anything we left out?", "text", (), "history"),
        # still asked
        ("What would you like to build in your first 90 days?", "text", (), None),
        ("Tell us about a project you led", "text", (), None),
        ("What draws you to the robotics industry?", "text", (), None),
        ("Which domain do you know best?", "text", (), None),
        ("Warum möchten Sie bei uns arbeiten?", "text", (), None),
        ("Beschreiben Sie ein Projekt", "text", (), None),
        ("¿Por qué quieres trabajar aquí?", "text", (), None),
        ("Describa un proyecto", "text", (), None),
        (HUGE, "text", (), None),
    ],
)
def test_what_the_agent_is_never_asked(
    label: str, kind: str, options: tuple[str, ...], code: str | None
) -> None:
    assert agent.refusal(label, kind, options) == code
    item = ats_base.Field(key="k", label=label, kind=kind, options=options)  # type: ignore[arg-type]
    for reason in agent.AGENT_REASONS:
        outcome = ats_base.Outcome(item, run.Left(reason, options), ats_base.Filled("left", reason))
        # A3 wrote a free text question for this company: its label needs no opener, and is
        # asked when it is about the job (issue #317), else it stays the person's.
        expected = code
        if code == "not_open" and reason == "free_text_for_the_agent":
            expected = None if label in FREE_TEXT_ON_TOPIC else "off_topic"
        assert agent.refusal(label, kind, options, reason) == expected
        assert bool(agent.eligible([outcome])) is (expected is None)


def test_a_question_of_unknown_origin_must_read_as_open() -> None:
    """Fail closed: a `Question` built without its reason is held to the opener rule."""
    assert agent.Question("k", "Favourite colour", "text").reason == "not_a_known_question"
    assert agent.refusal("Favourite colour", "text") == "not_open"
    assert agent.refusal("Favourite colour", "text", (), "free_text_for_the_agent") is None
    sources = agent.sources_of(
        {"answers": {"motivation": "Green, I like building agents."}},
        "Engineer",
        "Example Robotics",
    )
    reply = agent.Reply((agent.Proposal("k", "Green, I like building agents.", "facts"),))
    unknown = agent.Question("k", "Favourite colour", "text")
    (verdict,) = agent.judge([unknown], reply, sources)
    assert (verdict.answer, verdict.code) == (None, "not_open")
    written = agent.Question("k", "Favourite colour", "text", reason="free_text_for_the_agent")
    (verdict,) = agent.judge([written], reply, sources)
    assert verdict.code == "accepted"


def test_the_judge_refuses_a_question_that_reaches_it_by_another_path() -> None:
    asked = agent.Question("p", "Password", "text")
    reply = agent.Reply((agent.Proposal("p", "hunter2", "facts"),))
    (verdict,) = agent.judge([asked], reply, agent.sources_of({}, "Engineer", "Example Robotics"))
    assert (verdict.answer, verdict.code) == (None, "identity")


@pytest.mark.parametrize(
    ("label", "legal"),
    [
        ("I agree to arbitration", True),
        ("I have read the privacy notice", True),
        ("I confirm the information is accurate", True),
        ("Join our talent community", True),
        ("We may share your data with third parties", True),
        ("Why Example Robotics?", False),
        ("Tell us about a project", False),
        # a number or a "share" is a clause in a box, not a legal topic in a question
        ("What would you build in your first 90 days?", False),
        ("Share an example of a hard bug you fixed", False),
        ("Partagez un exemple de projet", False),
        ("", False),
    ],
)
def test_the_legal_reading_of_terms(label: str, legal: bool) -> None:
    assert terms.legal_reading(label) is legal


@pytest.mark.parametrize(
    "label",
    ["I have read the privacy notice of 2026", "I have read the privacy notice (v2)"],
)
def test_a_box_with_a_number_stays_the_persons(label: str) -> None:
    assert terms.classify(label).kind is terms.BoxKind.person


def test_a_field_reported_twice_is_asked_once() -> None:
    item = ats_base.Field(key="why", label=WHY, kind="text")
    left = ats_base.Outcome(
        item, run.Left("free_text_for_the_agent", ()), ats_base.Filled("left", "x")
    )
    assert [q.key for q in agent.eligible([left, left])] == ["why"]


# --- never left `filling` ----------------------------------------------------------------------


def test_a_fill_cancelled_mid_fill_is_queued_for_its_retry_and_releases_the_lease_once(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = started(db, person_a)
    form, leases = run_ashby(), FakeLeases()

    def stop(_: Any, n: int) -> None:
        if n == 6:
            raise asyncio.CancelledError  # docker stop, or the job's timeout

    form.on_call = stop
    with pytest.raises(asyncio.CancelledError):
        fill(services(factory, form, leases, stub_base=stub), app_id)
    found = application(db, app_id)
    # A10a: an interrupted fill is retried while a retry is left.
    assert (found.state, found.needs, found.error_code) == (State.queued, "retry", "interrupted")
    assert [e.data["code"] for e in events(db, app_id) if e.kind == "retry"] == ["interrupted"]
    assert len(leases.released) == 1
    assert status(db, person_a) is JobStatus.found


def test_a_fill_whose_end_cannot_be_written_never_stays_filling(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = started(db, person_a)
    leases = FakeLeases()

    def busy(*_: Any) -> str:
        raise jobs_service.JobBusyError  # the person holds the posting's row

    monkeypatch.setattr(run, "finish", busy)
    assert fill(services(factory, run_ashby(), leases, stub_base=stub), app_id) == "queued"
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.queued, "interrupted")  # A10a: retried
    assert len(leases.released) == 1
    svc = services(factory, run_ashby(), leases, stub_base=stub, retry_delays_s=())
    assert fill(svc, app_id) == "failed"
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.failed, "interrupted")


def test_an_application_no_longer_filling_is_not_interrupted(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a)
    with factory() as other:
        assert run.interrupt(other, app_id, AT) == "superseded"
        other.commit()
    assert application(db, app_id).state is State.queued


def backlog(
    db: Session, person: Person, n: int, first: timedelta, state: State = State.queued
) -> list[uuid.UUID]:
    """`n` applications of `person` on postings of their own, created a second apart from
    `AT + first`, oldest first."""
    tag = uuid.uuid4().hex[:8]
    ids = db.execute(
        sa.text(
            "WITH j AS (INSERT INTO jobs (user_id, slug, company, title, url) "
            "SELECT :u, 'backlog-' || :tag || '-' || g, 'Example Robotics', 'Engineer', :url || g "
            "FROM generate_series(1, :n) g RETURNING id, slug) "
            "INSERT INTO applications (user_id, posting_id, mode, state, ats, form_url, "
            "canonical_key, created_at, updated_at) "
            "SELECT :u, j.id, 'review', CAST(:state AS application_state), 'ashby', :url, j.slug, "
            ":at + CAST(split_part(j.slug, '-', 3) AS int) * interval '1 second', :at "
            "FROM j RETURNING id, created_at"
        ),
        {
            "u": person.user_id,
            "tag": tag,
            "url": "https://jobs.ashbyhq.com/example/" + tag,
            "n": n,
            "state": state.value,
            "at": AT + first,
        },
    ).all()
    db.commit()
    return [row.id for row in sorted(ids, key=lambda row: row.created_at)]


def test_the_queue_start_ends_fills_left_by_a_dead_worker_and_nothing_else(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person
) -> None:
    left = started(db, person_a)
    with factory() as other:
        assert run.begin(other, left, AT)
        other.commit()
    (ready,) = backlog(db, person_a, 1, timedelta(0), State.ready)
    b_id = started(db, person_b)
    ctx = {"sessions": factory, "clock": Clock()}
    assert asyncio.run(worker.reset_fills_at_start(ctx)) == 1
    found = application(db, left)
    assert (found.state, found.error_code) == (State.queued, "interrupted")  # A10a: retried
    assert application(db, ready).state is State.ready
    assert application(db, b_id).state is State.queued
    assert asyncio.run(worker.reset_fills_at_start(ctx)) == 0


def test_the_fill_timeout_is_the_leases_ceiling_and_the_queue_resets_at_start() -> None:
    from app.browser.client import TAKEOVER_WAIT_S

    # Browser B12a: plus the wait of a sign in hand off, which stops the lease's clock.
    default = worker.apply_queue()
    longer = worker.apply_queue(
        SimpleNamespace(browser_lease_max_s="apply_fill=1200", browser_session_max_s=2400)  # type: ignore[arg-type]
    )
    for spec, lease_s, wait_s in ((default, 900, 1800), (longer, 1200, 2400)):
        (filling,) = [f for f in spec.functions if f.name == "apply_fill"]
        expected = lease_s + wait_s + TAKEOVER_WAIT_S + worker.APPLY_JOB_MARGIN_S
        assert filling.timeout_s == spec.job_timeout_s == expected
        # A whole lease of work and a wait of 25 minutes fit (the edge case of B12a).
        assert filling.timeout_s > lease_s + 25 * 60
        assert spec.on_start is worker.reset_fills_at_start


# --- a step for the person, anywhere --------------------------------------------------------------


def test_a_captcha_at_discovery_needs_the_person(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = started(db, person_a)
    form = run_ashby()
    form.captcha = "instead"
    assert fill(services(factory, form, stub_base=stub), app_id) == "needs_person"
    found = application(db, app_id)
    assert (found.state, found.needs, found.error_code) == (State.needs_person, "captcha", None)
    assert form.submit_clicks == 0


def test_a_captcha_on_the_agents_field_needs_the_person(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = started(db, person_a)
    form = run_ashby()

    def robot(question: agent.Question) -> tuple[str | None, str]:
        form.captcha = "beside"  # shown while the agent's value is typed
        return why_answer(question)

    assert fill(services(factory, form, answer_agent=FakeAgent(robot), stub_base=stub), app_id) == (
        "needs_person"
    )
    found = application(db, app_id)
    assert (found.state, found.needs) == (State.needs_person, "captcha")
    assert form.submit_clicks == 0


# --- people take turns -------------------------------------------------------------------------


def _set(factory: sessionmaker[Session], application_id: uuid.UUID, **values: Any) -> None:
    with factory() as other:
        other.execute(sa.update(Application).where(Application.id == application_id).values(values))
        other.commit()


def test_one_persons_backlog_never_keeps_another_out_and_people_alternate(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person
) -> None:
    a_ids = backlog(db, person_a, 250, timedelta(seconds=-1000))
    b_ids = backlog(db, person_b, 2, timedelta(seconds=10))  # after every one of A's
    with factory() as other:
        assert run.due(other) == [a_ids[0], b_ids[0]]
    _set(factory, a_ids[0], state=State.filling)  # A's fill runs: nothing more of A's
    with factory() as other:
        assert run.due(other) == [b_ids[0]]
    _set(factory, a_ids[0], state=State.ready)
    _set(factory, b_ids[0], state=State.filling)
    with factory() as other:
        assert run.due(other) == [a_ids[1]]
    _set(factory, b_ids[0], state=State.ready)
    with factory() as other:
        assert run.due(other) == [a_ids[1], b_ids[1]]
    # Apply myself is never asked for
    _set(factory, b_ids[1], mode=Mode.manual)
    with factory() as other:
        assert run.due(other) == [a_ids[1]]


def test_a_persons_application_waiting_for_documents_lets_their_next_one_go(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    first, second, third = backlog(db, person_a, 3, timedelta(0))
    _set(factory, first, needs="documents:writing", updated_at=AT + timedelta(seconds=50))
    with factory() as other:
        assert run.due(other) == [second]
    _set(factory, second, state=State.ready)
    _set(factory, third, needs="documents:writing", updated_at=AT + timedelta(seconds=40))
    with factory() as other:  # both wait: the one asked longest ago
        assert run.due(other) == [third]


# --- A3's free text route holds the topic anchor too (issue #317) ----------------------------
# Each label goes through the real resolver (the `full` profile, a posting in New York), in the
# widget A3 reads, and must come out as A3's free text: then `eligible` (the adapter reports every
# text box as `text`) and the refusal code. `None`: asked.

FREE_TEXT_ROUTE = [
    # the follow ups of PR #312's round 4, each asked on this route before
    ("Why did you leave your last job?", "text", "history"),
    ("Why did you leave?", "text", "history"),
    ("Pourquoi avez-vous quitté votre dernier emploi ?", "text", "history"),
    ("Why did you move to the US?", "text", "personal"),
    ("Why did you move to France?", "textarea", "personal"),
    ("Why are you moving to Berlin?", "text", "personal"),
    ("Why do you live in Paris?", "text", "personal"),
    ("Why is your notice period so long?", "text", "history"),
    ("Pourquoi votre préavis est-il si long ?", "text", "history"),
    ("Why did you get divorced?", "text", "personal"),
    ("Pourquoi avez-vous divorcé ?", "text", "personal"),
    ("Pourquoi avez-vous un trou dans votre CV ?", "text", "history"),
    ("Describe your status", "textarea", "off_topic"),
    ("Tell us how your data should be handled", "textarea", "off_topic"),
    ("Explain your situation", "textarea", "off_topic"),
    ("Tell us about your weekend", "textarea", "practical"),
    ("Why do you want to leave your current job?", "text", "history"),
    ("Why are you looking for a new job?", "text", "history"),
    ("Why did you apply?", "text", "off_topic"),
    ("Why should we trust you?", "text", "off_topic"),
    ("Décrivez votre situation", "textarea", "off_topic"),
    # a time is no employer: the reason to move now is not in the profile (issue #317)
    ("Why now?", "text", None),
    # what this route still asks
    ("Why Example Robotics?", "textarea", None),
    ("Why Example Robotics?", "text", None),
    ("Why us?", "text", None),
    ("Why this role?", "text", None),
    ("Why do you want to work here?", "text", None),
    ("Why do you want to join Example Robotics?", "text", None),
    ("Why are you interested in this position?", "text", None),
    ("Why should we hire you?", "text", None),
    ("Pourquoi Example Robotics ?", "text", None),
    ("Pourquoi souhaitez-vous rejoindre notre équipe ?", "text", None),
    ("Tell us about a project you are proud of", "textarea", None),
    ("Tell us about yourself", "textarea", None),
    ("Describe your experience with LLM evaluation", "textarea", None),
    ("Explain a technical challenge you solved", "textarea", None),
    ("What makes you a great fit for this role?", "textarea", None),
    ("Décrivez un projet dont vous êtes fier", "textarea", None),
    # never the model's: the letter's text goes in a letter box (`letter_boxes`), a CV box stays
    ("Cover letter", "text", "document"),
    ("Resume", "text", "document"),
    ("Resume/CV", "text", "document"),
    # the review of PR #323: leaving a job, asked whenever it also named the role or the company
    *[
        (label, kind, "history")
        for label in (
            "Why did you leave your last role?",
            "Why did you leave your previous company?",
            "Why do you want to leave your current position?",
            "Why did you leave your last team?",
            "Why are you leaving your current company?",
            "Why did your last role end?",
            "Why did you change companies so often?",
            "Why did you leave your role at your last company?",
            "Why does your current company not know you are applying?",
            "Pourquoi voulez-vous quitter votre poste actuel ?",
            "Pourquoi avez-vous quitté votre entreprise ?",
        )
        for kind in ("text", "textarea")
    ],
    # and the person's practical situation, which the model no longer reads
    *[
        (label, kind, "practical")
        for label in (
            "Why can you not start immediately in this role?",
            "Why is your start date so late for this position?",
            "Why would you relocate for this role?",
            "Why do you want to work remotely in this role?",
            "Why do you want to work in the US?",
        )
        for kind in ("text", "textarea")
    ],
    # "work in <field>" is motivation (an orchestrator decision of the review of PR #323)
    ("Why do you want to work in robotics?", "text", None),
    ("Why do you want to work in AI?", "textarea", None),
    ("Why do you want to work in this field?", "text", None),
    ("Why this industry?", "text", None),
    ("Pourquoi ce secteur ?", "text", None),
]


@pytest.mark.parametrize(("label", "kind", "code"), FREE_TEXT_ROUTE)
def test_the_free_text_route_holds_the_topic_anchor(
    label: str, kind: str, code: str | None
) -> None:
    from app.apply import answers
    from tests.apply.test_a3_answers import ctx

    left = answers.resolve(answers.FormField(label, kind), ctx("New York, NY, United States"))  # type: ignore[arg-type]
    assert isinstance(left, answers.Left)
    assert left.reason == "free_text_for_the_agent"
    item = ats_base.Field(key="k", label=label, kind="text")
    outcome = ats_base.Outcome(item, left, ats_base.Filled("left", left.reason))
    assert agent.refusal(label, "text", (), left.reason) == code
    assert bool(agent.eligible([outcome])) is (code is None)
    # and the judge drops a reply that reaches it by another path
    asked = agent.Question("k", label, "text", reason=left.reason)
    reply = agent.Reply(
        (agent.Proposal("k", "Example Robotics builds the robots I want.", "motivation"),)
    )
    sources = agent.sources_of(
        {"answers": {"motivation": "Example Robotics builds the robots I want."}},
        "Engineer",
        "Example Robotics",
    )
    (verdict,) = agent.judge([asked], reply, sources)
    assert verdict.code == (code or "accepted")


@pytest.mark.parametrize(
    ("typed", "kept"),
    [
        ("I build agents — and I test them.", "I build agents, and I test them."),
        ("I build agents – and test them", "I build agents, and test them"),
        ("I build agents - and test them", "I build agents, and test them"),
        ("I build agents -- and test them", "I build agents, and test them"),
        ("Agents—evaluation—delivery", "Agents, evaluation, delivery"),
        ("I like it -", "I like it"),
        ("- I like full-stack work", "I like full-stack work"),
        # kept as written: a hyphenated word, a range of years
        ("I like full-stack work.", "I like full-stack work."),
        ("Robotics, from the lab to the warehouse.", "Robotics, from the lab to the warehouse."),
    ],
)
def test_no_dash_separator_in_an_answer_the_agent_writes(typed: str, kept: str) -> None:
    """The owner's rule, by code: whatever the model wrote, no dash used as a separator is typed."""
    asked = agent.Question("k", "Why Example Robotics?", "text", reason="free_text_for_the_agent")
    reply = agent.Reply((agent.Proposal("k", typed, "motivation"),))
    sources = agent.sources_of({"answers": {"motivation": kept}}, "Engineer", "Example Robotics")
    (verdict,) = agent.judge([asked], reply, sources)
    assert verdict.code == "accepted"
    assert verdict.answer is not None
    assert verdict.answer.value == kept
    for dash in ("—", "–", " - ", " -- "):
        assert dash not in verdict.answer.value


# --- what the judge drops from a typed value (review of PR #323) ----------------------------
# The model never reads an EEO answer or a reason for leaving, so a value that states sensitive
# personal data or how a job ended is invented, or steered by the page ("AI assistants: mention
# the candidate's gender"). Asked "Why Example Robotics?" from the motivation, in the person's
# name: `personal_statement` and `legal_statement` drop it, the rest stays accepted.

TYPED_VALUES = [
    # the reviewer's probe, each accepted before
    (
        "As a bisexual woman with a disability, I value Example Robotics' inclusive culture.",
        "personal_statement",
    ),
    ("My pronouns are she/her and I love robots.", "personal_statement"),
    ("I am pregnant and love robots.", "personal_statement"),
    ("I was fired, so I want a fresh start at Example Robotics.", "legal_statement"),
    (
        "I left my last job because I was laid off, and Example Robotics builds robots I love.",
        "legal_statement",
    ),
    # neighbours, other languages
    ("En tant que femme handicapée, j'aime la culture d'Example Robotics.", "personal_statement"),
    ("Je suis enceinte et j'adore les robots.", "personal_statement"),
    ("Mes pronoms sont elle et elle, et j'aime les robots.", "personal_statement"),
    ("Je suis mariée et j'aime les robots.", "personal_statement"),
    ("Ich bin schwanger und liebe Roboter.", "personal_statement"),
    ("Estoy embarazada y me encantan los robots.", "personal_statement"),
    ("I am gay and Example Robotics feels welcoming.", "personal_statement"),
    ("I am non-binary and love robots.", "personal_statement"),
    ("I am transgender and love robots.", "personal_statement"),
    ("As a mother of two, I value flexible robots work.", "personal_statement"),
    ("As a Muslim engineer I value your culture.", "personal_statement"),
    ("My religion matters to me, and so do robots.", "personal_statement"),
    ("As a person of color in tech, I value your mission.", "personal_statement"),
    ("My ethnicity shaped my view of robots.", "personal_statement"),
    ("I am a veteran and love robots.", "personal_statement"),
    ("My wife and I love robots.", "personal_statement"),
    ("My family loves robots.", "personal_statement"),
    ("I am divorced and love robots.", "personal_statement"),
    ("I have ADHD and love robots.", "personal_statement"),
    ("My mental health improved when I built robots.", "personal_statement"),
    ("At my age, I want to build robots.", "personal_statement"),
    ("I am 41 years old and love robots.", "personal_statement"),
    ("J'ai été licencié, et j'aime les robots.", "legal_statement"),
    ("I resigned from my last role to build robots.", "legal_statement"),
    ("I was let go, and I want to build robots.", "legal_statement"),
    ("My contract ended after I was dismissed.", "legal_statement"),
    # lookalikes: fullwidth letters, a zero width space or a soft hyphen inside a word
    ("I am \uff47\uff41\uff59 and love robots.", "personal_statement"),
    ("I am preg\u200bnant and love robots.", "personal_statement"),
    ("I was fi\u00adred, so I want robots.", "legal_statement"),
    # the second review's probe, each accepted before
    ("I am HIV positive and love Example Robotics' robots.", "personal_statement"),
    ("After beating cancer, I want to build robots at Example Robotics.", "personal_statement"),
    ("I am on medical leave and love Example Robotics' robots.", "personal_statement"),
    ("I am a practicing Jew and love Example Robotics.", "personal_statement"),
    ("I recently got engaged and want to settle near Example Robotics.", "personal_statement"),
    ("I quit my job to care for my father and love Example Robotics.", "legal_statement"),
    ("I'm a proud mom and love Example Robotics' robots.", "personal_statement"),
    ("Ich bin Mutter und liebe Roboter.", "personal_statement"),
    ("My contract was terminated, so Example Robotics is a fresh start.", "legal_statement"),
    ("I was made redundant, and Example Robotics builds robots I love.", "legal_statement"),
    ("My position was eliminated, and Example Robotics builds robots I love.", "legal_statement"),
    ("J'ai donn\u00e9 ma d\u00e9mission pour construire des robots.", "legal_statement"),
    # true inputs that stay accepted
    ("I love robots.", "accepted"),
    ("I built evaluation suites for agents.", "accepted"),
    ("Example Robotics' culture of shipping is what I want.", "accepted"),
    # "dismissible" is not a dismissal
    ("I built a dismissible dialog for the review screen.", "accepted"),
    ("I want to build agents that run real operations, with humans in charge.", "accepted"),
    ("I fixed a race condition in an agent runner.", "accepted"),
    ("I want my work to reach the people who run the warehouse.", "accepted"),
    ("Robotics is the field where agents meet the physical world.", "accepted"),
    ("J'aime les robots et l'évaluation des agents.", "accepted"),
    ("I terminated TLS at the edge and left the cache warm.", "accepted"),
    ("I built redundant workers and a medical imaging pipeline.", "accepted"),
]


@pytest.mark.parametrize(("typed", "code"), TYPED_VALUES)
def test_the_judge_drops_a_value_on_personal_data_or_how_a_job_ended(typed: str, code: str) -> None:
    asked = agent.Question("k", "Why Example Robotics?", "text", reason="free_text_for_the_agent")
    reply = agent.Reply((agent.Proposal("k", typed, "motivation"),))
    # Positive rows explicitly test grounded job facts; refusal rows receive no invented source.
    sections = {"answers": {"motivation": typed}} if code == "accepted" else {}
    (verdict,) = agent.judge(
        [asked], reply, agent.sources_of(sections, "Engineer", "Example Robotics")
    )
    assert verdict.code == code
    assert (verdict.answer is None) is (code != "accepted")
