"""A posting's policy on candidates using AI (jobs J40, app/jobs/ai_policy.py): the signal
`ai_policy` as a family (a policy that forbids, one that allows, a request to disclose, one that
only points to a page, in English, French and German) against what is not one (the employer's own
use of AI, its product for candidates, its messages, AI as a skill or a task of the role, a name,
privacy boilerplate, an AI interviewer, a bare AI term, a negation, a question), then its notice,
its columns and a rescore. Every sentence is invented."""

from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.jobs import ai_policy, notices, service, signals
from app.jobs.models import Job
from app.jobs.signals import Signal, read, stored
from tests.jobs.conftest import NOW
from tests.jobs.sweep_helpers import SKILLS as PROFILE_SKILLS
from tests.jobs.test_api import Signed, alice, seed
from tests.jobs.test_notices import give_profile

__all__ = ["alice"]

FORBIDS = [
    "Candidates must not use AI tools during the technical interview.",
    "We do not allow applicants to use generative AI in their assessments.",
    "Please do not use AI to write your cover letter.",
    "The use of AI tools by candidates is prohibited.",
    "AI generated applications are not accepted.",
    "You must not use ChatGPT during the coding challenge, as we assess your own work.",
    "Use of AI by applicants during interviews is not permitted.",
    "AI tools are not allowed during the coding test.",
    "We ask that you do not use AI tools in your interview.",
    "AI-generated cover letters will not be considered.",
    "Using GitHub Copilot or Gemini during the live coding interview is not allowed.",
    "Responses written with ChatGPT will be rejected.",
    "Any use of an LLM during the take home exercise leads to disqualification.",
    "Les candidats ne doivent pas utiliser l'IA pendant l'entretien technique.",
    "Il est interdit aux candidats d'utiliser l'IA lors des évaluations.",
    "Les candidatures rédigées par l'IA ne sont pas acceptées.",
    "Nous demandons aux candidats de ne pas utiliser l'IA lors de l'entretien.",
    "Merci de ne pas utiliser d'IA générative pour votre candidature.",
    "L'utilisation de l'IA est interdite pendant vos entretiens.",
    "N'utilisez pas Copilot pendant le test technique.",
    "L'usage d'outils d'IA n'est pas autorisé durant l'entretien d'embauche.",
    "Bewerber dürfen während des Vorstellungsgesprächs keine KI verwenden.",
    "Der Einsatz von KI durch Bewerber ist untersagt.",
    "Mit KI erstellte Bewerbungen werden nicht berücksichtigt.",
    "Wir bitten Bewerber, keine KI im Vorstellungsgespräch zu nutzen.",
    "Der Einsatz von ChatGPT im Bewerbungsprozess ist nicht erlaubt.",
    "Bitte verwenden Sie für Ihr Anschreiben keine generative KI.",
    "Bewerber werden gebeten, im Vorstellungsgespräch keine KI zu nutzen.",
    "Submitting AI written answers in the coding test is not acceptable.",
    "The use of AI in the coding test is prohibited, no exceptions.",
    "L'utilisation d'outils d'IA par les candidats est interdite.",
    "Die KI-Nutzung durch Bewerber ist im technischen Teil untersagt.",
    "Im technischen Interview ist die Nutzung von KI nicht gestattet.",
]
ALLOWS = [
    "Candidates are welcome to use AI tools in their application.",
    "We encourage candidates to use AI thoughtfully in their application.",
    "You may use AI assistants to prepare your application.",
    "Using AI in the application process: we encourage candidates to use AI thoughtfully",
    "Applicants can use AI during interviews as long as they say so.",
    "We allow applicants to use a large language model for the take home exercise.",
    "Applicants may use AI tools to help draft their CV.",
    "Feel free to use Claude or ChatGPT to polish your resume.",
    "AI assisted cover letters are welcome.",
    "We are happy for you to use AI throughout the recruitment process.",
    "Nous encourageons les candidats à utiliser l'IA de manière réfléchie dans leur candidature.",
    "Vous pouvez utiliser l'intelligence artificielle pour préparer votre candidature.",
    "Nous autorisons les candidats à recourir à l'IA pour rédiger leur lettre de motivation.",
    "Les candidats peuvent utiliser l'IA pour préparer leur candidature.",
    "L'utilisation de ChatGPT est autorisée pour préparer vos entretiens.",
    "Wir ermutigen Bewerber, KI im Bewerbungsprozess verantwortungsvoll zu nutzen.",
    "Sie dürfen KI-Tools in Ihrer Bewerbung nutzen.",
    "Bewerber können KI für ihre Bewerbung nutzen.",
    "Die Nutzung von KI für Ihren Lebenslauf ist ausdrücklich erlaubt.",
]
DISCLOSES = [
    "We expect candidates to disclose any use of AI in the application.",
    "Please disclose any use of AI in your application.",
    "If you use AI to write your application, please tell us.",
    "Tell us in your cover letter whether generative AI helped you write it.",
    "Si vous avez utilisé l'IA pour votre lettre de motivation, merci de l'indiquer.",
    "Wenn Sie KI für Ihre Bewerbung genutzt haben, geben Sie das bitte an.",
    "Bitte geben Sie an, ob Sie ChatGPT für Ihren Lebenslauf verwendet haben.",
]
POINTS = [
    "Guidance on candidates' AI usage",
    "Learn about our policy for using AI in our application process.",
    "See our AI policy for candidates.",
    "Our guidelines for applicants on using AI are linked below.",
    "Candidates' use of AI: read our guidance before you apply.",
    "Please visit our AI guidelines for candidates and interviews.",
    "Our rules on AI usage for applicants are on the careers page.",
    "Die Hinweise zur Nutzung von KI für Bewerber stehen unten.",
    "Notre politique IA pour les candidats est en ligne.",
    "Notre politique sur l'utilisation de l'IA dans le processus de candidature.",
    "Notre charte de l'IA pour les candidats est disponible en ligne.",
    "Hinweise zum Einsatz von KI im Bewerbungsprozess.",
    "Unsere KI-Richtlinie für Bewerber finden Sie hier.",
]
FOUND = [*FORBIDS, *ALLOWS, *DISCLOSES, *POINTS]

# The employer, its people or its tools use AI on the applications (list 3).
EMPLOYER = [
    "We may use AI assisted tools during our recruiting process.",
    "Using AI capabilities, we analyze your application to match your skills.",
    "The use of AI tools for employment decision making is described in our privacy notice.",
    "We use AI to review candidates' applications.",
    "Our recruiters use AI to screen applications before an interview.",
    "Candidates are ranked by an AI model, and a recruiter reads every ranking.",
    "Nous utilisons l'IA pour analyser les candidatures reçues.",
    "Wir setzen KI ein, um Bewerbungen zu sichten.",
    "We do not use AI to make hiring decisions.",
    "We may use AI tools to help review your application and your answers.",
    "Your application may be screened with the help of AI before a recruiter reads it.",
    "AI may be used to support the review of your CV.",
    "Our hiring team uses AI tools during the interview process to take notes.",
    "Nous pouvons utiliser des outils d'IA pour l'analyse de votre candidature.",
    "Votre candidature peut être analysée à l'aide de l'IA.",
    "Ihre Bewerbung wird mithilfe von KI geprüft, bevor ein Mensch sie liest.",
    "In unserem Bewerbungsprozess setzen wir KI ein, um Termine zu planen.",
    "Using AI in our hiring process",
    "Learn how we use AI in our recruitment process.",
    "We record interviews and use AI to transcribe them.",
    "AI tools may be used to screen your application.",
    "Nous utilisons l'IA pendant les entretiens pour prendre des notes.",
    "Des outils d'IA peuvent servir à l'analyse de votre candidature.",
    "L'IA peut être utilisée pour trier votre candidature.",
    "Wir nutzen KI im Vorstellungsgespräch, um Notizen zu erstellen.",
    "Bei der Prüfung Ihrer Bewerbung kommen KI-Tools zum Einsatz.",
    "KI-Tools helfen, Ihre Bewerbung zu sichten.",
    "Our clients use AI to review their applications.",
    "Nos clients utilisent l'IA pour trier leurs candidatures.",
]
# An employer's product for candidates is not the candidates' own use (list 3).
PRODUCT = [
    "Our product helps applicants use AI to write better cover letters.",
    "Our platform lets job seekers use AI to tailor their resume.",
    "Candidates use the AI interviewer to rehearse their first interview.",
    "Notre plateforme aide les candidats à utiliser l'IA pour rédiger leur CV.",
    "Les candidats utilisent l'IA de notre plateforme pour préparer leurs entretiens.",
    "Unsere Plattform hilft Bewerbern, KI für ihre Bewerbung zu nutzen.",
    "Applicants can use our AI coach to improve their CV.",
    "Notre IA aide les candidats à postuler en deux minutes.",
    "You can apply using our AI assistant.",
    "Applicants use Acme AI to prepare for interviews.",
    "Applicants can use our AI assistant to polish their CV.",
    "Les candidats utilisent le recruteur IA pour préparer leur entretien.",
]
MESSAGES = [
    "By applying you agree to receive calls, AI generated calls, text messages and emails.",
    "Our AI assistant will contact candidates by phone to book the interview.",
    "Notre assistant IA appellera les candidats pour fixer l'entretien.",
    "By submitting your application, you agree to receive AI generated calls and text messages.",
    "En envoyant votre candidature, vous acceptez de recevoir des messages générés par l'IA.",
    "By submitting your application you agree to receive messages generated by AI.",
    "Mit Ihrer Bewerbung stimmen Sie KI-generierten Nachrichten zu.",
]
# AI as a skill or a task of the role, the path of someone else's hiring.
SKILLS = [
    "Validate and productionise AI generated code.",
    "Leverage AI assisted development to ship faster.",
    "Experience using AI tools is a plus.",
    "Candidates who use AI tools daily to build products will thrive here.",
    "We are looking for candidates with experience using generative AI.",
    "You will use AI to automate our customer support workflows.",
    "Vous utiliserez l'IA pour automatiser nos processus.",
    "Sie nutzen KI, um Produkte für unsere Kunden zu bauen.",
    "Candidates should be comfortable using AI tools in application development.",
    "Applicants must use AI to build web applications.",
    "Candidates use AI to write application code.",
    "Candidates will be assessed on their ability to use AI tools effectively in the interview.",
    "Our responsible AI policy for application security covers the model training data.",
    "Interviews will cover machine learning and AI system design.",
    "You can use LLMs to build automated evaluations of our agents.",
    "Vous pourrez utiliser l'IA pour optimiser l'entretien des équipements.",
    "You will use generative AI to improve the hiring process for our clients.",
    "Experience using AI tools in the recruitment process is a strong plus.",
    "You will use LLMs to score the assessments of our learners.",
    "Vous utiliserez l'IA pour améliorer le processus de recrutement de nos clients.",
    "Sie verwenden KI, um den Bewerbungsprozess unserer Kunden zu optimieren.",
    "You will use AI across the hiring process of our clients.",
    "You can use AI coding assistants in your application development work.",
    "You will use AI in the application layer of our banking app.",
    "You will use AI in the application of pricing models.",
    "You can use AI to estimate your take home pay.",
    "Use AI to analyze CVs and interview transcripts for our customers.",
    "You will use AI in interviews with customers to find their needs.",
    "Sie bringen Erfahrung im Einsatz von KI im Bewerbungsprozess mit.",
    "Vous avez une expérience de l'utilisation de l'IA dans le processus de recrutement.",
    "Vous utiliserez des outils d'IA pour l'entretien des équipements.",
    "Sie nutzen KI-Tools in den Interviews mit Nutzern.",
    "Vous utiliserez l'IA dans le processus de recrutement de nos clients.",
    "Sie nutzen KI im Bewerbungsprozess unserer Kunden.",
    "Review AI generated application code for security flaws.",
    "Candidates will use AI tools to speed up their application development.",
    "You will use AI tools to streamline the hiring process.",
    "Vous utiliserez l'IA pour fluidifier le processus de recrutement.",
    "Sie nutzen KI, um den Bewerbungsprozess zu vereinfachen.",
    "Mit KI-Tools optimieren Sie den Bewerbungsprozess.",
]
# AI as a topic the person talks about, in the interview or in the letter (list 3).
TOPICS = [
    "In the final interview we will discuss how you use LLMs in your current team.",
    "During your interview, walk us through a feature you shipped using AI tools.",
    "In your cover letter, tell us about the AI tools you use every day.",
    "In your application, give examples of AI assistants you have built.",
    "Pendant l'entretien, nous aborderons votre usage de l'IA au quotidien.",
    "Im Vorstellungsgespräch besprechen wir, welche KI-Tools Sie täglich verwenden.",
]
NAMES = [
    "Acme AI Labs is hiring a backend engineer.",
    "Join the Applied AI team.",
    "Notre équipe IA recrute un ingénieur.",
    "Unser KI Team sucht Verstärkung.",
    "Our approach to AI in hiring.",
    "We are an AI first company reinventing how people apply for jobs.",
    "Millions of job seekers use our AI tools to land interviews.",
    "Wir revolutionieren mit KI den Bewerbungsprozess.",
]
BOILERPLATE = [
    "For more information on how we use AI in the hiring process, see our privacy notice.",
    "Automated decision making: AI tools are not used to make final hiring decisions.",
    "Your personal data, including your CV, may be processed with the help of AI tools.",
    "Les données de votre candidature peuvent être traitées à l'aide d'outils d'IA.",
    "Ihre Bewerbungsdaten können mit KI-Werkzeugen verarbeitet werden.",
    "Our AI policy for applicants and employees is published on our website.",
]
INTERVIEWER = [
    "Our ATS uses AI to parse your resume.",
    "Your first interview is with an AI interviewer and lasts twenty minutes.",
    "Candidates complete a video interview that is scored using AI.",
    "Our AI interview assistant takes notes during candidates' interviews.",
]
# An AI term on the candidate's path with no word of use or rule is a topic (list 1).
BARE = [
    "Your interview will cover LLM agents and retrieval.",
    "Votre entretien portera sur l'IA générative.",
    "Im Vorstellungsgespräch sprechen wir über KI-Agenten.",
]
NEGATED = [
    "There is no policy on candidates using AI.",
    "We have no guidance on candidates' AI usage.",
    "Candidates are not required to use AI in the application process.",
    "Il n'y a pas de politique sur l'IA pour les candidats.",
    "Es gibt keine Richtlinie zur KI-Nutzung durch Bewerber.",
]
OTHER_LANGUAGES = [
    "Los candidatos no deben usar IA durante la entrevista.",
    "I candidati non devono usare l'IA durante il colloquio.",
    "Kandidaten mogen geen AI gebruiken tijdens het sollicitatiegesprek.",
]
SILENT = [
    *EMPLOYER,
    *PRODUCT,
    *MESSAGES,
    *SKILLS,
    *TOPICS,
    *NAMES,
    *BOILERPLATE,
    *INTERVIEWER,
    *BARE,
    *NEGATED,
    *OTHER_LANGUAGES,
]


def codes(text: str) -> list[str]:
    return [s.code for s in read(text)]


@pytest.mark.parametrize("text", FOUND)
def test_a_policy_on_candidates_using_ai_is_a_signal_with_its_quote(text: str) -> None:
    assert [s for s in read(text) if s.code == "ai_policy"] == [Signal("ai_policy", text)]


@pytest.mark.parametrize("text", SILENT)
def test_what_is_not_a_policy_on_candidates_gives_no_signal(text: str) -> None:
    assert "ai_policy" not in codes(text)


def test_the_tables_are_wide_enough_to_measure() -> None:
    assert len(FOUND) >= 40
    assert len(SILENT) >= 60


@pytest.mark.parametrize(
    "text", ["Can candidates use AI in the interview?", *[f"{s[:-1]}?" for s in ALLOWS[:3]]]
)
def test_a_question_states_nothing(text: str) -> None:
    assert "ai_policy" not in codes(text)


def test_a_policy_split_over_two_lines_is_found_on_the_line_that_holds_it() -> None:
    text = "AI in our hiring\nCandidates may not use AI tools in the technical interview."
    [found] = [s for s in read(text) if s.code == "ai_policy"]
    assert found.quote == "Candidates may not use AI tools in the technical interview."


def test_the_quote_is_the_sentence_not_the_paragraph() -> None:
    text = (
        "You will build robots that use AI to plan paths. We encourage candidates to use AI "
        "thoughtfully in the application. Benefits include a laptop."
    )
    [found] = [s for s in read(text) if s.code == "ai_policy"]
    assert found.quote == "We encourage candidates to use AI thoughtfully in the application."


def test_the_first_sentence_is_the_only_signal() -> None:
    text = "Candidates may not use AI in interviews. Candidates may use AI in the application."
    assert codes(text).count("ai_policy") == 1


def test_a_posting_full_of_ai_skills_says_nothing_of_a_policy() -> None:
    text = (
        "Senior AI Engineer. You will design AI agents, evaluate LLM outputs and ship AI features. "
        "Experience using AI tools such as coding assistants is required. We use AI in our "
        "hiring to schedule interviews. Our AI team reviews every pull request."
    )
    assert "ai_policy" not in codes(text)


def test_other_signals_still_read_beside_it() -> None:
    text = "We do not sponsor visas. We encourage candidates to use AI in their application."
    assert codes(text) == ["no_sponsorship", "ai_policy"]


def test_the_rule_reads_a_long_sentence_in_linear_time() -> None:
    said = " ".join(["candidates"] * 5000 + ["use", "ai", "in", "your", "application"])
    assert ai_policy.says(said) is True
    assert ai_policy.says("ai " * 50_000) is False
    assert ai_policy.says("we may " * 20_000 + "use ai in your application") is False


def test_stored_holds_the_signal() -> None:
    columns = stored("Guidance on candidates' AI usage")
    assert columns["signals"] == [
        {"code": "ai_policy", "quote": "Guidance on candidates' AI usage", "value": None}
    ]


def test_a_rescore_gives_the_signal_to_a_posting_stored_before(
    factory: sessionmaker[Session], alice: Signed
) -> None:
    policy = "Please do not use AI to write your cover letter."
    text = f"We do not sponsor visas.\n{policy}"
    older = [{"code": "no_sponsorship", "quote": "We do not sponsor visas.", "value": None}]
    [job] = seed(factory, alice.id, [{"posting_text": text, "signals": older}])
    give_profile(factory, alice.id, {"skills": PROFILE_SKILLS})
    with factory() as db:
        service.rescore_batch(
            db, alice.id, service.user_skills(db, alice.id), NOW + timedelta(hours=1), ids=None,
            after=None, limit=100, overwrite_manual=False, outcome=service.RescoreOutcome(),
        )  # fmt: skip
        db.commit()
    with factory() as db:
        row = db.execute(sa.select(Job).where(Job.id == job.id)).scalar_one()
    assert [(s["code"], s["quote"]) for s in row.signals] == [
        ("no_sponsorship", "We do not sponsor visas."),
        ("ai_policy", policy),
    ]


def test_the_notice_is_one_warning_sentence_with_the_quote() -> None:
    quote = "We encourage candidates to use AI thoughtfully in their application."
    job = Job(location="Berlin, Germany", contract_type=None, posting_text=quote, signals=None)
    found = notices.signal_notices(job, [Signal("ai_policy", quote)], notices.Person())
    assert [(n.kind, n.code, n.text, n.quote, n.level) for n in found] == [
        (
            "signal",
            "ai_policy",
            "This posting says something about candidates using AI. Read it before you apply.",
            quote,
            "warning",
        )
    ]
    assert " - " not in found[0].text
    assert signals.words(found[0].text)  # a sentence a person reads, no dash
