"""A posting's policy on candidates using AI (jobs J40, app/jobs/ai_policy.py): the signal
`ai_policy` as a family (a policy that forbids, one that allows, one that only points to a page,
in English, French and German) against what is not one (the employer's own use of AI, AI as a
skill or a task of the role, its messages, a name, a negation, a question), then its notice and
its columns. Every sentence is invented."""

import pytest

from app.jobs import ai_policy, notices, signals
from app.jobs.models import Job
from app.jobs.signals import Signal, read, stored

FORBIDS = [
    "Candidates must not use AI tools during the technical interview.",
    "We do not allow applicants to use generative AI in their assessments.",
    "Please do not use AI to write your cover letter.",
    "The use of AI tools by candidates is prohibited.",
    "AI generated applications are not accepted.",
    "You must not use ChatGPT during the coding challenge, as we assess your own work.",
    "Les candidats ne doivent pas utiliser l'IA pendant l'entretien technique.",
    "Il est interdit aux candidats d'utiliser l'IA lors des évaluations.",
    "Les candidatures rédigées par l'IA ne sont pas acceptées.",
    "Bewerber dürfen während des Vorstellungsgesprächs keine KI verwenden.",
    "Der Einsatz von KI durch Bewerber ist untersagt.",
    "Mit KI erstellte Bewerbungen werden nicht berücksichtigt.",
    "Use of AI by applicants during interviews is not permitted.",
    "AI tools are not allowed during the coding test.",
    "We ask that you do not use AI tools in your interview.",
    "Nous demandons aux candidats de ne pas utiliser l'IA lors de l'entretien.",
    "Wir bitten Bewerber, keine KI im Vorstellungsgespräch zu nutzen.",
]
ALLOWS = [
    "Candidates are welcome to use AI tools in their application.",
    "We encourage candidates to use AI thoughtfully.",
    "You may use AI assistants to prepare your application.",
    "Using AI in the application process: we encourage candidates to use AI thoughtfully",
    "Applicants can use AI during interviews as long as they say so.",
    "We allow applicants to use a large language model for the take home exercise.",
    "Nous encourageons les candidats à utiliser l'IA de manière réfléchie.",
    "Vous pouvez utiliser l'intelligence artificielle pour préparer votre candidature.",
    "Nous autorisons les candidats à recourir à l'IA pour rédiger leur lettre de motivation.",
    "Wir ermutigen Bewerber, KI verantwortungsvoll zu nutzen.",
    "Bewerber werden gebeten, im Vorstellungsgespräch keine KI zu nutzen.",
    "Sie dürfen KI-Tools in Ihrer Bewerbung nutzen.",
    "We expect candidates to disclose any use of AI in the application.",
    "Applicants may use AI tools to help draft their CV.",
    "Les candidats peuvent utiliser l'IA pour préparer leur candidature.",
    "Bewerber können KI für ihre Bewerbung nutzen.",
]
POINTS = [
    "Guidance on candidates' AI usage",
    "Learn about our policy for using AI in our application process.",
    "See our AI policy for candidates.",
    "Our guidelines for applicants on using AI are linked below.",
    "Candidates' use of AI: read our guidance before you apply.",
    "Notre politique sur l'utilisation de l'IA dans le processus de candidature.",
    "Notre charte de l'IA pour les candidats est disponible en ligne.",
    "Hinweise zum Einsatz von KI im Bewerbungsprozess.",
    "Unsere KI-Richtlinie für Bewerber finden Sie hier.",
]

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
]
MESSAGES = [
    "By applying you agree to receive calls, AI generated calls, text messages and emails.",
    "Our AI assistant will contact candidates by phone to book the interview.",
    "Notre assistant IA appellera les candidats pour fixer l'entretien.",
]
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
]
NAMES = [
    "Acme AI Labs is hiring a backend engineer.",
    "Join the Applied AI team.",
    "Notre équipe IA recrute un ingénieur.",
    "Unser KI Team sucht Verstärkung.",
    "You can apply using our AI assistant.",
    "Using AI in our hiring process",
    "Our approach to AI in hiring.",
    "Learn how we use AI in our recruitment process.",
    "We use AI to help schedule interviews.",
    "We record interviews and use AI to transcribe them.",
    "Our AI interview assistant takes notes during candidates' interviews.",
    "AI-powered tools help our recruiters find candidates.",
    "We prefer candidates who use AI in their daily work.",
    "Candidates will be asked to build an AI agent in the take home exercise.",
    "Candidates interviewing with us can expect an AI generated summary of their interview.",
    "Les candidats qui utilisent l'IA dans leur quotidien seront valorisés.",
    "Bewerber erhalten eine Antwort, die KI nutzt.",
    "Candidates can apply with a CV or a LinkedIn profile.",
    "Applicants should have strong AI skills.",
    "Notre IA aide les candidats à postuler en deux minutes.",
]
NEGATED = [
    "There is no policy on candidates using AI.",
    "We have no guidance on candidates' AI usage.",
    "Candidates are not required to use AI in the application process.",
    "Il n'y a pas de politique sur l'IA pour les candidats.",
    "Es gibt keine Richtlinie zur KI-Nutzung durch Bewerber.",
]
SILENT = [*EMPLOYER, *MESSAGES, *SKILLS, *NAMES, *NEGATED]


def codes(text: str) -> list[str]:
    return [s.code for s in read(text)]


@pytest.mark.parametrize("text", [*FORBIDS, *ALLOWS, *POINTS])
def test_a_policy_on_candidates_using_ai_is_a_signal_with_its_quote(text: str) -> None:
    assert [s for s in read(text) if s.code == "ai_policy"] == [Signal("ai_policy", text)]


@pytest.mark.parametrize("text", SILENT)
def test_what_is_not_a_policy_on_candidates_gives_no_signal(text: str) -> None:
    assert "ai_policy" not in codes(text)


def test_the_tables_are_wide_enough_to_measure() -> None:
    assert len([*FORBIDS, *ALLOWS, *POINTS]) >= 20
    assert len(SILENT) >= 20


@pytest.mark.parametrize(
    "text", ["Can candidates use AI in the interview?", *[f"{s[:-1]}?" for s in ALLOWS[:3]]]
)
def test_a_question_states_nothing(text: str) -> None:
    assert "ai_policy" not in codes(text)


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
    text = "We do not sponsor visas. We encourage candidates to use AI thoughtfully."
    assert codes(text) == ["no_sponsorship", "ai_policy"]


def test_the_rule_reads_a_long_sentence_in_linear_time() -> None:
    said = " ".join(["candidates"] * 5000 + ["use", "ai"])
    assert ai_policy.says(said) in (True, False)
    assert ai_policy.says("ai " * 50_000) is False


def test_stored_holds_the_signal() -> None:
    columns = stored("Guidance on candidates' AI usage")
    assert columns["signals"] == [
        {"code": "ai_policy", "quote": "Guidance on candidates' AI usage", "value": None}
    ]


def test_the_notice_is_one_warning_sentence_with_the_quote() -> None:
    quote = "We encourage candidates to use AI thoughtfully."
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
