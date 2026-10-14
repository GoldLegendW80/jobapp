# ruff: noqa: E501, the tables below read best one case to a line
"""The answer table (A3 review of PR #260): every question and box the review probed, rebuilt from
its findings and categories, plus our own cases in English and French, half of them traps
(negations, other countries, third persons, specific technologies, lookalike options).

One invented profile: authorised in France and Germany without sponsorship, sponsorship needed
for the United Kingdom and the United States, nothing for Canada, Switzerland or the EU; four
years overall; Python in the skills; one EUR salary range; EEO policy decline. Each case names
the context it runs in and the answer expected, or LEFT: a field the person fills. A LEFT costs
the person one field; a wrong answer is a false statement in their name.
"""

import uuid
from typing import Any, NamedTuple

import pytest

from app.apply.answers import Answer, Context, Documents, FormField, Left, Posting, resolve
from app.apply.bank import AnswerBank
from app.apply.terms import classify
from tests.profile.conftest import full

USER = uuid.uuid4()
LEFT = "<left>"
YES_NO = ("Yes", "No")
DECLINE = ("Male", "Female", "I don't wish to answer")
BANDS = ("0-1", "2-3", "4-5", "6+")
DISABILITY = (
    "Yes, I have a disability",
    "No, I do not have a disability",
    "I do not want to answer",
)
DOCS = Documents(cv_path="/x/cv.pdf", letter_path="/x/letter.pdf", letter_text="Dear team")


def _sections(**answers: Any) -> dict[str, Any]:
    stored = {name: full(name) for name in ("identity", "experience", "skills")}
    stored["skills"]["years_experience"] = answers.pop("years", 4)
    stored["skills"]["have"].append(
        {"name": "python", "terms": ["python"], "advanced_regex": False}
    )
    stored["answers"] = {
        **full("answers"),
        "work_authorization": [
            {
                "country": "France",
                "answer": "EU citizen, no sponsorship.",
                "needs_sponsorship": False,
            },
            {"country": "Germany", "answer": "EU citizen.", "needs_sponsorship": False},
            {"country": "UK", "answer": "Sponsorship needed.", "needs_sponsorship": True},
            {"country": "United States", "answer": "Visa needed.", "needs_sponsorship": True},
        ],
        "salary_policy": "Open to a fair package for the role.",
        "salary_stance": "sentence",
        "salary_ranges": [
            {"currency": "EUR", "minimum": 50000, "maximum": 60000, "period": "year"}
        ],
        "eeo_policy": "decline",
        "eeo_answers": [],
        "leave_blank": [],
        **answers,
    }
    return stored


def _bank(**entries: str) -> AnswerBank:
    bank = AnswerBank(USER)
    for label, answer in entries.items():
        bank = bank.with_answer(label.replace("_", " "), answer)
    return bank


CONTEXTS: dict[str, Any] = {
    "fr": lambda: Context(USER, _sections(), Posting.at("Lyon, France")),
    "uk": lambda: Context(USER, _sections(), Posting.at("London, United Kingdom")),
    "us": lambda: Context(USER, _sections(), Posting.at("New York, United States")),
    "none": lambda: Context(USER, _sections(), None),
    "range": lambda: Context(USER, _sections(salary_stance="range"), Posting.at("Paris")),
    "stated": lambda: Context(
        USER, _sections(years=None, years_experience_stated="About 3.5 years"), Posting.at("Paris")
    ),
    "leave_bank": lambda: Context(
        USER, _sections(eeo_policy="leave"), Posting.at("Paris"), bank=_bank(Gender="Female")
    ),
    "decline_bank": lambda: Context(
        USER, _sections(), Posting.at("Paris"), bank=_bank(Gender="Female")
    ),
    "stored": lambda: Context(
        USER,
        _sections(
            eeo_policy="stored",
            eeo_answers=[
                {"topic": "gender", "answer": "Woman"},
                {"topic": "disability", "answer": "No"},
            ],
        ),
        Posting.at("Paris"),
    ),
    "blank_bank": lambda: Context(
        USER,
        _sections(salary_stance="leave_blank"),
        Posting.at("Paris"),
        bank=_bank(Salary_expectations="70000 EUR", Current_salary="55000 EUR"),
    ),
    "referral_bank": lambda: Context(
        USER, _sections(), Posting.at("Paris"), bank=_bank(Referrer_s_full_name="Ada Example")
    ),
    "docs": lambda: Context(USER, _sections(), Posting.at("Paris"), documents=DOCS),
    # The second review's invented profile: authorised in France and the EU, sponsorship needed
    # for the UK, the US, Switzerland, Canada and Singapore; a background check consent of yes.
    "probe": lambda: Context(USER, _probe(), Posting.at("Paris, France")),
    "probe_ny": lambda: Context(USER, _probe(), Posting.at("New York, United States")),
    "probe_berlin": lambda: Context(USER, _probe(), Posting.at("Berlin, Germany")),
}


def _probe() -> dict[str, Any]:
    return _sections(
        work_authorization=[
            {"country": "France", "answer": "EU citizen.", "needs_sponsorship": False},
            {"country": "the EU", "answer": "EU citizen.", "needs_sponsorship": False},
            *(
                {"country": country, "answer": "Sponsorship needed.", "needs_sponsorship": True}
                for country in ("UK", "United States", "Switzerland", "Canada", "Singapore")
            ),
        ],
        background_check_consent=True,
    )


class Case(NamedTuple):
    name: str
    label: str
    kind: str = "text"
    options: tuple[str, ...] = ()
    context: str = "fr"
    expected: Any = LEFT
    origin: str = "review"  # "review" (named or rebuilt from its categories), "own", "trap"


ID = full("identity")
A = full("answers")
C = Case

QUESTIONS: list[Case] = [
    # names, contact, links, location
    C("first name", "First name", expected="Ada"),
    C("last name", "Last name", expected="Example"),
    C("full name", "Full name", expected="Ada Example"),
    C("email", "Email", expected=ID["email"]),
    C("phone", "Phone number", expected=ID["phone"]),
    C("linkedin", "LinkedIn profile", expected=ID["linkedin_url"]),
    C("website", "Website", expected=ID["website"]),
    C("github", "GitHub", expected="https://github.com/ada"),
    C("prenom", "Prénom", expected="Ada"),
    C("nom de famille", "Nom de famille", expected="Example"),
    C("adresse e-mail", "Adresse e-mail", expected=ID["email"]),
    C("telephone", "Téléphone", expected=ID["phone"]),
    C("city is the city", "City", expected="Bordeaux"),
    C("ville is the city", "Ville", expected="Bordeaux"),
    C("country", "Country", expected="France"),
    C("pays", "Pays", expected="France"),
    C("current location", "Current location", expected="Bordeaux, France"),
    C("preferred name", "Preferred name", expected="Ana"),
    C("pronouns", "Pronouns", expected="she/her"),
    C("mobile", "Mobile phone", expected=ID["phone"]),
    C("nom", "Nom", expected="Example"),
    C("portfolio", "Portfolio", expected=ID["website"]),
    C("when can you start", "When can you start?", expected=A["notice_period"]),
    C("willing to travel", "Are you willing to travel?", "textarea", expected=A["travel"]),
    C("highest degree", "Highest degree", expected=A["highest_degree"]),
    C(
        "langues",
        "Langues parlées",
        "textarea",
        expected="French (Mother tongue), English (Advanced (C1))",
    ),
    C("comment connu", "Comment avez-vous connu notre entreprise ?", expected=A["heard_about"]),
    C("cv file", "CV", "file", context="docs", expected="/x/cv.pdf"),
    C("lettre file", "Lettre de motivation", "file", context="docs", expected="/x/letter.pdf"),
    C("country of residence", "Country of residence", expected="France", origin="own"),
    # B8 third persons
    C("B8 referrer name", "Referrer's full name"),
    C("B8 emergency phone", "Emergency contact phone number"),
    C("B8 reference email", "Reference email address"),
    C(
        "B8 a banked referrer stays left",
        "Referrer's full name",
        context="referral_bank",
        origin="trap",
    ),
    C("manager email", "Manager's email", origin="trap"),
    C("nom du parrain", "Nom du parrain", origin="trap"),
    C("personne a contacter", "Personne à contacter en cas d'urgence : téléphone", origin="trap"),
    C("recruiter name", "Recruiter name", origin="trap"),
    C("spouse name", "Spouse's full name", origin="trap"),
    C("who referred you", "Who referred you? Email", origin="trap"),
    C(
        "referral source is how heard",
        "Referral source",
        "select",
        ("LinkedIn", "Careers page", "Other"),
        expected="Careers page",
        origin="own",
    ),
    # B9 citizenship
    C("B9 country of citizenship", "Country of citizenship"),
    C("nationality", "Nationality"),
    C("nationalite", "Nationalité", origin="own"),
    C("eu citizen", "Are you an EU citizen?", "yes_no", origin="trap"),
    C("citizenship select", "Citizenship", "select", ("France", "Germany", "Other"), origin="trap"),
    # work authorisation and sponsorship, the posting's country
    C(
        "authorised here",
        "Are you legally authorized to work in this country?",
        "yes_no",
        expected=True,
    ),
    C("sponsorship here", "Do you require visa sponsorship?", "yes_no", expected=False),
    C(
        "uk future sponsorship",
        "Will you now or in the future require sponsorship for employment visa status?",
        "yes_no",
        context="uk",
        expected=True,
    ),
    C(
        "uk authorised where located",
        "Are you authorized to work in the country where this job is located?",
        "yes_no",
        context="uk",
        expected=False,
    ),
    C(
        "uk sponsorship select",
        "Do you require sponsorship?",
        "select",
        YES_NO,
        context="uk",
        expected="Yes",
    ),
    C(
        "work authorisation text",
        "Work authorization",
        "textarea",
        expected="EU citizen, no sponsorship.",
    ),
    C("no posting leaves", "Do you require sponsorship?", "yes_no", context="none"),
    # B1 the question names another country
    C("B1 canada permit", "Do you have a valid work permit for Canada?", "yes_no"),
    C(
        "B1 united states",
        "Are you legally authorized to work in the United States?",
        "yes_no",
        expected=False,
    ),
    C(
        "B1 uk sponsorship",
        "Do you require visa sponsorship to work in the UK?",
        "yes_no",
        expected=True,
    ),
    C("switzerland", "Are you authorized to work in Switzerland?", "yes_no", origin="trap"),
    C("the eu has no entry", "Are you authorized to work in the EU?", "yes_no", origin="trap"),
    C(
        "germany from london",
        "Are you authorized to work in Germany?",
        "yes_no",
        context="uk",
        expected=True,
        origin="own",
    ),
    C(
        "two countries",
        "Do you require sponsorship to work in the US or Canada?",
        "yes_no",
        origin="trap",
    ),
    C(
        "other than france",
        "Are you authorized to work in a country other than France?",
        "yes_no",
        origin="trap",
    ),
    C(
        "residence country work",
        "Are you authorized to work in your country of residence?",
        "yes_no",
        origin="trap",
    ),
    C(
        "named country without posting",
        "Are you authorized to work in the UK?",
        "yes_no",
        context="none",
        expected=False,
        origin="own",
    ),
    C(
        "US in capitals",
        "Are you legally authorized to work in the US?",
        "yes_no",
        context="uk",
        expected=False,
        origin="own",
    ),
    C(
        "need us to sponsor",
        "Do you need us to sponsor a work visa?",
        "yes_no",
        expected=False,
        origin="trap",
    ),
    C(
        "us posting yes/no",
        "Are you legally authorized to work in this country? (Yes/No)",
        "yes_no",
        context="us",
        expected=False,
        origin="own",
    ),
    C(
        "autorise en france",
        "Êtes-vous autorisé(e) à travailler en France ?",
        "yes_no",
        context="uk",
        expected=True,
        origin="own",
    ),
    C(
        "parrainage royaume-uni",
        "Avez-vous besoin d'un parrainage pour travailler au Royaume-Uni ?",
        "yes_no",
        expected=True,
        origin="own",
    ),
    C("hold a valid visa", "Do you hold a valid visa?", "yes_no", origin="trap"),
    C(
        "unknown company word",
        "Will you require Acme to sponsor your visa?",
        "yes_no",
        origin="own",
    ),
    # B2 negations
    C("B2 do you not require", "Do you NOT require sponsorship?", "yes_no"),
    C(
        "B2 france without sponsorship",
        "Are you able to work in France without visa sponsorship?",
        "yes_no",
        expected=True,
    ),
    C(
        "B2 uk without sponsorship",
        "Can you work in the UK without sponsorship?",
        "yes_no",
        expected=False,
    ),
    C("unable to work", "Are you unable to work in France?", "yes_no"),
    C(
        "sans parrainage",
        "Pouvez-vous travailler en France sans parrainage ?",
        "yes_no",
        expected=True,
        origin="own",
    ),
    C("n'avez-vous pas", "N'avez-vous pas besoin de visa ?", "yes_no", origin="trap"),
    C(
        "uk without needing",
        "Do you have the right to work in the UK without needing sponsorship?",
        "yes_no",
        expected=False,
        origin="trap",
    ),
    C("without a visa", "Can you work in the UK without a visa?", "yes_no", origin="trap"),
    C(
        "will not require",
        "Will you not require sponsorship in the future?",
        "yes_no",
        origin="trap",
    ),
    # option wording with a comma
    C(
        "sponsorship long options",
        "Do you require sponsorship?",
        "select",
        ("Yes, I require sponsorship", "No, I do not require sponsorship"),
        expected="No, I do not require sponsorship",
    ),
    C(
        "authorised long options",
        "Are you authorized to work in this country?",
        "select",
        ("Yes, I am authorized", "No, I am not authorized"),
        expected="Yes, I am authorized",
    ),
    C(
        "autorise long options",
        "Êtes-vous autorisé(e) à travailler dans ce pays ?",
        "single",
        ("Oui, je suis autorisé(e)", "Non"),
        expected="Oui, je suis autorisé(e)",
    ),
    # notice, start, relocation, travel, on site, checks
    C("notice", "Notice period", expected=A["notice_period"]),
    C("preavis", "Préavis", expected=A["notice_period"]),
    C("start date", "Earliest start date", "date", expected=A["earliest_start"]),
    C("disponibilite", "Date de disponibilité", expected=A["earliest_start"]),
    C("relocation", "Are you willing to relocate?", "textarea", expected=A["relocation"]),
    C("travel", "Willingness to travel", expected=A["travel"]),
    C("onsite", "Are you willing to work on-site?", "yes_no", expected=True),
    C("hybrid", "Are you open to a hybrid schedule?", "yes_no", expected=True),
    C("background check", "Do you consent to a background check?", "yes_no", expected=False),
    C("reference check", "Do you agree to a reference check?", "yes_no", expected=True),
    C("relocate to paris is free text", "Why do you want to relocate to Paris?", "textarea"),
    C(
        "describe notice is free text",
        "Describe your ideal notice period",
        "textarea",
        origin="trap",
    ),
    C("onsite box", "I am willing to work on-site", "checkbox", expected=True, origin="own"),
    # salary, B6 current salary, B7 the bank under a policy
    C("salary sentence", "Salary expectations", expected="Open to a fair package for the role."),
    C("pretentions", "Prétentions salariales", expected="Open to a fair package for the role."),
    C(
        "salary euro range",
        "Expected salary (€)",
        context="range",
        expected="50000 to 60000 EUR per year",
    ),
    C(
        "desired salary range",
        "Desired salary",
        context="range",
        expected="50000 to 60000 EUR per year",
        origin="own",
    ),
    C("salary usd not stored", "Expected salary (USD)", context="range"),
    C("salary pounds not stored", "Expected salary (£)", context="range", origin="trap"),
    C("B6 current salary", "What is your current salary?"),
    C("B6 current base", "Current base compensation", context="range"),
    C("B6 remuneration actuelle", "Rémunération actuelle"),
    C("salaire actuel", "Salaire actuel brut annuel", origin="trap"),
    C("currently earning", "What are you currently earning?", origin="trap"),
    C("B7 salary bank under leave blank", "Salary expectations", context="blank_bank"),
    C("B7 current salary bank", "Current salary", context="blank_bank"),
    C(
        "salary negotiations is free text",
        "Tell us about your experience with salary negotiations",
        "textarea",
    ),
    # EEO and B7
    C("gender declines", "Gender", "select", DECLINE, expected="I don't wish to answer"),
    C("B7 gender bank under leave", "Gender", context="leave_bank"),
    C("B7 gender bank under decline", "Gender", context="decline_bank"),
    C(
        "B7 gender bank decline option",
        "Gender",
        "select",
        DECLINE,
        context="decline_bank",
        expected="I don't wish to answer",
    ),
    C(
        "veteran declines",
        "Are you a veteran?",
        "select",
        ("Yes", "No", "I don't wish to answer"),
        expected="I don't wish to answer",
    ),
    C(
        "disability stored no",
        "Disability status",
        "select",
        DISABILITY,
        context="stored",
        expected="No, I do not have a disability",
    ),
    C(
        "disability declines",
        "Disability status",
        "select",
        DISABILITY,
        expected="I do not want to answer",
    ),
    C(
        "ethnicity declines",
        "Ethnicity",
        "select",
        ("Asian", "White", "Decline to self identify"),
        expected="Decline to self identify",
    ),
    C(
        "sexe declines",
        "Sexe",
        "select",
        ("Homme", "Femme", "Je préfère ne pas répondre"),
        expected="Je préfère ne pas répondre",
        origin="own",
    ),
    C(
        "stored woman, no such option",
        "Gender",
        "select",
        ("Male", "Female", "Non-binary", "Decline"),
        context="stored",
        origin="trap",
    ),
    C(
        "stored woman",
        "Gender",
        "select",
        ("Man", "Woman", "Decline to self identify"),
        context="stored",
        expected="Woman",
        origin="own",
    ),
    C(
        "age range declines",
        "What is your age range?",
        "select",
        ("18-24", "25-34", "Decline to answer"),
        expected="Decline to answer",
        origin="own",
    ),
    # B5 age
    C("B5 at least 18", "Are you at least 18 years old?", "yes_no"),
    C("B5 over 18", "Are you over 18 years old?", "yes_no"),
    C("18 ans ou plus", "Avez-vous 18 ans ou plus ?", "yes_no", origin="trap"),
    C("legal age", "Are you of legal age to work?", "yes_no", origin="trap"),
    C("majeur", "Êtes-vous majeur(e) ?", "yes_no", origin="trap"),
    C("18+", "Are you 18+?", "yes_no", origin="trap"),
    # years, B3 subjects and B4 comparisons
    C("years text", "How many years of experience do you have?", expected="4"),
    C("B3 react", "How many years of experience do you have with React?"),
    C("B3 kubernetes bands", "Years of experience with Kubernetes", "select", BANDS),
    C("years bands", "Years of experience", "select", BANDS, expected="4-5"),
    C("B4 less than 2", "Do you have less than 2 years of experience?", "yes_no", expected=False),
    C("3+", "Do you have 3+ years of experience?", "yes_no", expected=True),
    C("5+", "Do you have 5+ years of experience?", "yes_no", expected=False),
    C(
        "at least 4",
        "Do you have at least 4 years of professional experience?",
        "yes_no",
        expected=True,
        origin="own",
    ),
    C(
        "more than 4",
        "Do you have more than 4 years of experience?",
        "yes_no",
        expected=False,
        origin="trap",
    ),
    C(
        "at most 5",
        "Do you have at most 5 years of experience?",
        "yes_no",
        expected=True,
        origin="own",
    ),
    C(
        "3 or more",
        "Do you have 3 or more years of experience?",
        "yes_no",
        expected=True,
        origin="own",
    ),
    C(
        "moins de 3 ans",
        "Avez-vous moins de 3 ans d'expérience ?",
        "yes_no",
        expected=False,
        origin="trap",
    ),
    C(
        "au moins 3 ans",
        "Avez-vous au moins 3 ans d'expérience ?",
        "yes_no",
        expected=True,
        origin="own",
    ),
    C(
        "plus de 5 ans",
        "Avez-vous plus de 5 ans d'expérience professionnelle ?",
        "yes_no",
        expected=False,
        origin="own",
    ),
    C("combien d'annees", "Combien d'années d'expérience avez-vous ?", expected="4", origin="own"),
    C("python years", "Do you have 3+ years of experience with Python?", "yes_no", origin="trap"),
    C("relevant years", "Years of relevant experience", origin="trap"),
    C(
        "machine learning bands",
        "Years of experience in machine learning",
        "select",
        BANDS,
        origin="trap",
    ),
    C("management years", "How many years of management experience do you have?", origin="trap"),
    C("two numbers", "Do you have 2 to 5 years of experience?", "yes_no", origin="trap"),
    C("bare 5 years", "Do you have 5 years of experience?", "yes_no", expected=False, origin="own"),
    C("negated years", "Do you NOT have 3 years of experience?", "yes_no", origin="trap"),
    C(
        "word bands",
        "Total years of professional experience",
        "select",
        ("Less than 2 years", "2 to 5 years", "More than 5 years"),
        expected="2 to 5 years",
        origin="own",
    ),
    C(
        "french bands",
        "Années d'expérience",
        "select",
        ("Moins de 2 ans", "2 à 5 ans", "Plus de 5 ans"),
        expected="2 à 5 ans",
        origin="own",
    ),
    C(
        "stated 3+",
        "Do you have 3+ years of experience?",
        "yes_no",
        context="stated",
        expected=True,
        origin="own",
    ),
    C(
        "stated 4+ straddles",
        "Do you have 4+ years of experience?",
        "yes_no",
        context="stated",
        origin="trap",
    ),
    # education, languages, how heard, previously applied
    C("degree", "Highest level of education", expected=A["highest_degree"]),
    C("graduation year", "Graduation year", expected="2022"),
    C("school", "School", expected="School of Engineering"),
    C("niveau d'etudes", "Niveau d'études", expected=A["highest_degree"], origin="own"),
    C("universite", "Université", expected="School of Engineering", origin="own"),
    C("speak french", "Do you speak French?", "yes_no", expected=True),
    C("speak german", "Do you speak German?", "yes_no"),
    C(
        "languages",
        "Languages",
        "textarea",
        expected="French (Mother tongue), English (Advanced (C1))",
    ),
    C("how heard", "How did you hear about us?", expected=A["heard_about"]),
    C(
        "how heard other",
        "How did you hear about us?",
        "select",
        ("Friend", "Other"),
        expected="Other",
    ),
    C("applied before", "Have you previously applied to this company?", "yes_no", expected=False),
    C(
        "deja postule",
        "Avez-vous déjà postulé chez nous ?",
        "single",
        ("Oui", "Non"),
        expected="Non",
        origin="own",
    ),
    # documents and free text
    C("resume file", "Resume/CV", "file", context="docs", expected="/x/cv.pdf"),
    C("letter file", "Cover letter", "file", context="docs", expected="/x/letter.pdf"),
    C("why join", "Why do you want to join us?", "textarea"),
    C("anything else", "Anything else you'd like to share?", "textarea"),
    C("pourquoi", "Pourquoi ce poste ?", "textarea", origin="own"),
    C("unknown", "Favourite colour", origin="own"),
]

BOXES: list[tuple[str, bool, str]] = [
    # the eight the review found ticked
    ("I accept the Terms & Conditions and Privacy Policy", False, "review"),
    (
        "I consent to the processing of my personal data for this and future recruitment processes",
        False,
        "review",
    ),
    (
        "J'accepte le traitement de mes données personnelles pour ce recrutement et les recrutements futurs",
        False,
        "review",
    ),
    (
        "I consent to the processing of my personal data and to be considered for other openings",
        False,
        "review",
    ),
    (
        "I certify that the information is true and acknowledge that employment is at-will",
        False,
        "review",
    ),
    (
        "I certify that my answers are accurate and authorize Acme to verify them with previous employers",
        False,
        "review",
    ),
    (
        "I agree to the Privacy Policy and consent to the transfer of my data to the United States",
        False,
        "review",
    ),
    ("I acknowledge the privacy notice and agree to the Acme Candidate Agreement", False, "review"),
    # the two families
    ("I have read the Privacy Notice for this application", True, "review"),
    ("I acknowledge the Candidate Privacy Policy", True, "review"),
    ("J'ai lu la politique de confidentialité", True, "review"),
    ("I consent to the processing of my personal data for this application", True, "review"),
    ("I confirm that the information provided is accurate and complete", True, "review"),
    ("I certify that my answers are true", True, "review"),
    ("Je certifie que les informations sont exactes", True, "review"),
    # every other box
    ("I agree to binding arbitration of any dispute", False, "review"),
    ("I agree to receive marketing emails", False, "review"),
    ("Subscribe to our newsletter", False, "review"),
    ("Join our talent community", False, "review"),
    ("Add me to your talent pool for future opportunities", False, "review"),
    ("I authorize a background check", False, "review"),
    ("I consent to a criminal record check", False, "review"),
    ("Keep my data for 24 months for other positions", False, "review"),
    ("I agree to the Terms and Conditions", False, "review"),
    ("I confirm the information is accurate and agree to arbitration", False, "review"),
    # our own
    ("I have read and understood the Candidate Privacy Notice", True, "own"),
    ("J'ai lu et j'accepte la politique de confidentialité", True, "own"),
    ("I agree to the privacy policy", True, "own"),
    (
        "I confirm that all information provided in this application is true and complete to the best of my knowledge",
        True,
        "own",
    ),
    ("Je certifie que les renseignements fournis sont exacts", True, "own"),
    (
        "I consent to the processing of my personal data for the purposes of this application",
        True,
        "own",
    ),
    (
        "I confirm I have read the privacy notice and that the information above is accurate",
        True,
        "own",
    ),
    ("I acknowledge that I have read the Acme Privacy Policy", False, "trap"),
    ("I agree to the privacy policy and to receive job alerts", False, "trap"),
    ("J'accepte la politique de confidentialité et les CGU", False, "trap"),
    ("I consent to Acme keeping my CV for 2 years", False, "trap"),
    ("I consent to the processing of my sensitive personal data", False, "trap"),
    ("I agree to the Privacy Policy and the Cookie Policy", False, "trap"),
    (
        "I acknowledge the privacy notice and consent to being contacted by recruiters",
        False,
        "trap",
    ),
    ("J'autorise Acme à conserver mes données", False, "trap"),
    ("I accept the policy", False, "trap"),
    ("I certify the information is accurate and I agree to the terms", False, "trap"),
    (
        "I consent to the processing of my personal data in all Acme recruitment processes",
        False,
        "trap",
    ),
    ("J'accepte le transfert de mes données hors de l'Union européenne", False, "trap"),
]


SENTENCE = "Open to a fair package for the role."
LANGS = ("English", "French", "German", "Spanish")

# Round 3: every question the second review names (its fresh probe, findings N1 to N10, the plain
# questions it found left and the right answers it quotes), origin "review2"; then our own, half
# of them traps, origin "own3" or "trap3". All run on the second review's profile.
ROUND3: list[Case] = [
    # N1 a work permit is never read as authorisation
    C(
        "N1 need permit france",
        "Do you need a work permit to work in France?",
        "yes_no",
        context="probe",
        origin="review2",
    ),
    C(
        "N1 require permit uk",
        "Would you require a work permit to work in the UK?",
        "yes_no",
        context="probe",
        origin="review2",
    ),
    C(
        "N1 besoin permis",
        "Avez-vous besoin d'un permis de travail pour travailler en France ?",
        "yes_no",
        context="probe",
        origin="review2",
    ),
    # N2 two questions in one label
    C(
        "N2 authorised or sponsorship",
        "Are you authorized to work in France or do you require sponsorship?",
        "yes_no",
        context="probe",
        origin="review2",
    ),
    C(
        "N2 right or sponsorship",
        "Do you have the right to work in the UK, or will you need sponsorship?",
        "yes_no",
        context="probe",
        origin="review2",
    ),
    # N3 past pay
    C(
        "N3 previous role",
        "What was your salary in your previous role?",
        context="probe",
        origin="review2",
    ),
    C(
        "N3 earn last company",
        "What salary did you earn at your last company?",
        context="probe",
        origin="review2",
    ),
    C("N3 salary history", "Please provide your salary history", context="probe", origin="review2"),
    C("N3 last position", "Salary at your last position", context="probe", origin="review2"),
    # N4 other platforms
    C("N4 twitter", "Twitter URL", context="probe", origin="review2"),
    C("N4 scholar", "Google Scholar profile URL", context="probe", origin="review2"),
    # N5 birth
    C(
        "N5 country of birth",
        "Country of birth",
        "select",
        ("France", "Germany", "Spain"),
        context="probe",
        origin="review2",
    ),
    C("N5 pays de naissance", "Pays de naissance", context="probe", origin="review2"),
    # N6 criminal record
    C(
        "N6 criminal record",
        "Do you have a criminal record?",
        "yes_no",
        context="probe",
        origin="review2",
    ),
    C("N6 casier", "Avez-vous un casier judiciaire ?", "yes_no", context="probe", origin="review2"),
    # N7 worked, and a negated applied
    C(
        "N7 worked for acme",
        "Have you previously worked for Acme Group?",
        "yes_no",
        context="probe",
        origin="review2",
    ),
    C(
        "N7 never applied",
        "Have you never applied to Acme before?",
        "yes_no",
        context="probe",
        origin="review2",
    ),
    # N8 inverted on-site and hybrid
    C(
        "N8 unwilling onsite",
        "Are you unwilling to work on-site?",
        "yes_no",
        context="probe",
        origin="review2",
    ),
    C(
        "N8 onsite a problem",
        "Would a fully on-site role be a problem for you?",
        "yes_no",
        context="probe",
        origin="review2",
    ),
    C(
        "N8 hybrid dealbreaker",
        "Is a hybrid schedule a dealbreaker for you?",
        "yes_no",
        context="probe",
        origin="review2",
    ),
    # N9 a preference
    C(
        "N9 city you would like",
        "Which city would you like to work in?",
        "select",
        ("Paris", "Lyon", "Nantes"),
        context="probe",
        origin="review2",
    ),
    C(
        "N9 with the home city listed",
        "Which city would you like to work in?",
        "select",
        ("Paris", "Bordeaux", "Nantes"),
        context="probe",
        origin="review2",
    ),
    # N10 every language
    C(
        "N10 languages spoken",
        "Languages spoken",
        "multi",
        LANGS,
        context="probe",
        expected=("English", "French"),
        origin="review2",
    ),
    # the plain questions the review found left
    C(
        "adresse electronique",
        "Adresse électronique",
        context="probe",
        expected=ID["email"],
        origin="review2",
    ),
    C(
        "phone country code",
        "Phone country code",
        "select",
        ("Germany (+49)", "France (+33)", "United Kingdom (+44)"),
        context="probe",
        expected="France (+33)",
        origin="review2",
    ),
    C(
        "country code",
        "Country code",
        "select",
        ("+44 United Kingdom", "+33 France"),
        context="probe",
        expected="+33 France",
        origin="review2",
    ),
    C(
        "race ethnicity hyphen",
        "What is your race/ethnicity?",
        "select",
        ("White", "Asian", "Decline to self-identify"),
        context="probe",
        expected="Decline to self-identify",
        origin="review2",
    ),
    C(
        "germany from the eu entry",
        "Are you legally eligible to work in Germany?",
        "yes_no",
        context="probe",
        expected=True,
        origin="review2",
    ),
    # the right answers it quotes
    C(
        "switzerland without sponsorship",
        "Can you work in Switzerland without sponsorship?",
        "yes_no",
        context="probe",
        expected=False,
        origin="review2",
    ),
    C(
        "no more than 5",
        "Do you have no more than 5 years of experience?",
        "yes_no",
        context="probe",
        expected=True,
        origin="review2",
    ),
    # our own, plain
    C("given name", "Given name", context="probe", expected="Ada", origin="own3"),
    C("family name", "Family name", context="probe", expected="Example", origin="own3"),
    C("nom complet", "Nom complet", context="probe", expected="Ada Example", origin="own3"),
    C(
        "email required mark",
        "E-mail address *",
        context="probe",
        expected=ID["email"],
        origin="own3",
    ),
    C("adresse courriel", "Adresse courriel", context="probe", expected=ID["email"], origin="own3"),
    C(
        "numero de telephone",
        "Numéro de téléphone",
        context="probe",
        expected=ID["phone"],
        origin="own3",
    ),
    C(
        "mobile required",
        "Mobile number (required)",
        context="probe",
        expected=ID["phone"],
        origin="own3",
    ),
    C(
        "indicatif pays",
        "Indicatif pays",
        "select",
        ("Belgique (+32)", "France (+33)"),
        context="probe",
        expected="France (+33)",
        origin="own3",
    ),
    C(
        "city of residence",
        "City of residence",
        context="probe",
        expected="Bordeaux",
        origin="own3",
    ),
    C("pays de residence", "Pays de résidence", context="probe", expected="France", origin="own3"),
    C(
        "linkedin profile url",
        "LinkedIn profile URL",
        context="probe",
        expected=ID["linkedin_url"],
        origin="own3",
    ),
    C(
        "personal website",
        "Personal website",
        context="probe",
        expected=ID["website"],
        origin="own3",
    ),
    C(
        "authorised in france",
        "Are you authorized to work in France?",
        "yes_no",
        context="probe",
        expected=True,
        origin="own3",
    ),
    C(
        "eligible in the us",
        "Are you legally eligible to work in the United States?",
        "yes_no",
        context="probe",
        expected=False,
        origin="own3",
    ),
    C(
        "right to work in spain",
        "Do you have the right to work in Spain?",
        "yes_no",
        context="probe",
        expected=True,
        origin="own3",
    ),
    C(
        "autorise en allemagne",
        "Êtes-vous autorisé(e) à travailler en Allemagne ?",
        "yes_no",
        context="probe",
        expected=True,
        origin="own3",
    ),
    C(
        "berlin posting from the eu entry",
        "Are you authorized to work in this country?",
        "yes_no",
        context="probe_berlin",
        expected=True,
        origin="own3",
    ),
    C(
        "future sponsorship ny",
        "Will you now or in the future require visa sponsorship?",
        "yes_no",
        context="probe_ny",
        expected=True,
        origin="own3",
    ),
    C(
        "greenhouse sponsorship",
        "Will you now, or in the future, require sponsorship for employment visa status (e.g., H-1B visa status)?",
        "yes_no",
        context="probe_ny",
        expected=True,
        origin="own3",
    ),
    C(
        "sponsorship canada",
        "Do you require visa sponsorship to work in Canada?",
        "yes_no",
        context="probe",
        expected=True,
        origin="own3",
    ),
    C(
        "parrainage france",
        "Aurez-vous besoin d'un parrainage de visa pour travailler en France ?",
        "yes_no",
        context="probe",
        expected=False,
        origin="own3",
    ),
    C(
        "years professional text",
        "How many years of professional experience do you have?",
        context="probe",
        expected="4",
        origin="own3",
    ),
    C(
        "years select ranges",
        "Years of experience",
        "select",
        ("0-2 years", "3-5 years", "6+ years"),
        context="probe",
        expected="3-5 years",
        origin="own3",
    ),
    C(
        "au moins 2 ans",
        "Avez-vous au moins 2 ans d'expérience professionnelle ?",
        "yes_no",
        context="probe",
        expected=True,
        origin="own3",
    ),
    C(
        "what is your notice",
        "What is your notice period?",
        context="probe",
        expected=A["notice_period"],
        origin="own3",
    ),
    C(
        "date de disponibilite question",
        "Quelle est votre date de disponibilité ?",
        context="probe",
        expected=A["earliest_start"],
        origin="own3",
    ),
    C(
        "relocate to the posting city",
        "Are you willing to relocate to Paris?",
        "textarea",
        context="probe",
        expected=A["relocation"],
        origin="own3",
    ),
    C(
        "open to on-site",
        "Are you open to working on-site?",
        "yes_no",
        context="probe",
        expected=True,
        origin="own3",
    ),
    C(
        "salary expectations question",
        "What are your salary expectations?",
        context="probe",
        expected=SENTENCE,
        origin="own3",
    ),
    C(
        "pretentions brut annuel",
        "Prétentions salariales (brut annuel)",
        context="probe",
        expected=SENTENCE,
        origin="own3",
    ),
    C(
        "veteran decline",
        "Veteran status",
        "select",
        ("I am a protected veteran", "I am not a protected veteran", "I don't wish to answer"),
        context="probe",
        expected="I don't wish to answer",
        origin="own3",
    ),
    C(
        "heard about this job",
        "How did you hear about this job?",
        context="probe",
        expected=A["heard_about"],
        origin="own3",
    ),
    C(
        "which languages multi",
        "Which languages do you speak?",
        "multi",
        ("French", "English", "Italian"),
        context="probe",
        expected=("French", "English"),
        origin="own3",
    ),
    C(
        "applied before plain",
        "Have you applied to this company before?",
        "yes_no",
        context="probe",
        expected=False,
        origin="own3",
    ),
    C("contact number", "Contact number", context="probe", expected=ID["phone"], origin="own3"),
    C(
        "when could you start",
        "When could you start?",
        context="probe",
        expected=A["notice_period"],
        origin="own3",
    ),
    C(
        "hispanic latino",
        "Are you Hispanic/Latino?",
        "select",
        ("Yes", "No", "Decline to self-identify"),
        context="probe",
        expected="Decline to self-identify",
        origin="own3",
    ),
    C("experience annees", "Expérience (années)", context="probe", expected="4", origin="own3"),
    C(
        "portfolio or website",
        "Portfolio / Website",
        context="probe",
        expected=ID["website"],
        origin="own3",
    ),
    C(
        "website blog or portfolio",
        "Website, blog or portfolio",
        context="probe",
        expected=ID["website"],
        origin="own3",
    ),
    C("other website", "Other website", context="probe", origin="trap3"),
    # our own, traps
    C(
        "previous employer number",
        "Contact number of your previous employer",
        context="probe",
        origin="trap3",
    ),
    C(
        "start and notice",
        "When could you start, and what notice do you have?",
        context="probe",
        origin="trap3",
    ),
    C(
        "hispanic and veteran",
        "Are you Hispanic/Latino and a veteran?",
        "select",
        ("Yes", "No", "Decline to self-identify"),
        context="probe",
        origin="trap3",
    ),
    C(
        "experience management annees",
        "Expérience en management (années)",
        context="probe",
        origin="trap3",
    ),
    C(
        "besoin d'un visa",
        "Avez-vous besoin d'un visa pour travailler en France ?",
        "yes_no",
        context="probe",
        origin="trap3",
    ),
    C("notice in weeks", "Notice period (weeks)", context="probe", origin="trap3"),
    C("availability alone", "Availability", context="probe", origin="trap3"),
    C(
        "valid permit france",
        "Do you have a valid work permit for France?",
        "yes_no",
        context="probe",
        origin="trap3",
    ),
    C(
        "authorised and id",
        "Are you authorized to work in France and do you hold a valid ID?",
        "yes_no",
        context="probe",
        origin="trap3",
    ),
    C(
        "two question marks",
        "Do you require sponsorship now? Will you in the future?",
        "yes_no",
        context="probe",
        origin="trap3",
    ),
    C(
        "norway not in the eu",
        "Are you authorized to work in Norway?",
        "yes_no",
        context="probe",
        origin="trap3",
    ),
    C(
        "switzerland or the eu",
        "Are you legally eligible to work in Switzerland or the EU?",
        "yes_no",
        context="probe",
        origin="trap3",
    ),
    C(
        "unable without sponsorship",
        "Would you be unable to start without sponsorship?",
        "yes_no",
        context="probe",
        origin="trap3",
    ),
    C(
        "visa to visit",
        "Do you need a visa to visit France?",
        "yes_no",
        context="probe",
        origin="trap3",
    ),
    C(
        "work at acme",
        "Did you previously work at Acme Group?",
        "yes_no",
        context="probe",
        origin="trap3",
    ),
    C(
        "ever employed",
        "Have you ever been employed by this company?",
        "yes_no",
        context="probe",
        origin="trap3",
    ),
    C(
        "deja travaille",
        "Avez-vous déjà travaillé chez nous ?",
        "yes_no",
        context="probe",
        origin="trap3",
    ),
    C("felony", "Have you been convicted of a felony?", "yes_no", context="probe", origin="trap3"),
    C("condamne", "Avez-vous déjà été condamné(e) ?", "yes_no", context="probe", origin="trap3"),
    C(
        "notice and current salary",
        "What is your current notice period and current salary?",
        context="probe",
        origin="trap3",
    ),
    C("last drawn salary", "Last drawn salary", context="probe", origin="trap3"),
    C("salaire precedent", "Salaire précédent", context="probe", origin="trap3"),
    C(
        "expected for previous role",
        "Expected salary for your previous role",
        context="probe",
        origin="trap3",
    ),
    C("facebook url", "Facebook profile URL", context="probe", origin="trap3"),
    C("company website", "Company website", context="probe", origin="trap3"),
    C("ville de naissance", "Ville de naissance", context="probe", origin="trap3"),
    C("where born", "Where were you born?", context="probe", origin="trap3"),
    C(
        "office you prefer",
        "Which office would you prefer to work from?",
        "select",
        ("Paris", "London"),
        context="probe",
        origin="trap3",
    ),
    C(
        "preferred work location",
        "Preferred work location",
        "select",
        ("Paris", "Lyon"),
        context="probe",
        origin="trap3",
    ),
    C(
        "not willing to relocate",
        "Are you not willing to relocate?",
        "yes_no",
        context="probe",
        origin="trap3",
    ),
    C(
        "onsite five days a problem",
        "Is working on-site five days a week a problem?",
        "yes_no",
        context="probe",
        origin="trap3",
    ),
    C(
        "ne souhaitez pas presentiel",
        "Ne souhaitez-vous pas travailler en présentiel ?",
        "yes_no",
        context="probe",
        origin="trap3",
    ),
    C(
        "python and aws years",
        "How many years of experience do you have with Python and AWS?",
        context="probe",
        origin="trap3",
    ),
    C(
        "genai role bands",
        "Years of experience in a GenAI role",
        "select",
        BANDS,
        context="probe",
        origin="trap3",
    ),
    C(
        "french or german",
        "Do you speak French or German?",
        "yes_no",
        context="probe",
        origin="trap3",
    ),
    C("fluent german", "Are you fluent in German?", "yes_no", context="probe", origin="trap3"),
    C(
        "primary language select",
        "Primary language",
        "select",
        ("French", "English"),
        context="probe",
        origin="trap3",
    ),
    C("gender of manager", "Gender of your manager", context="probe", origin="trap3"),
    C("spouse nationality", "Spouse's nationality", context="probe", origin="trap3"),
    C(
        "disability accommodation",
        "Do you have a disability that requires accommodation during the interview?",
        "yes_no",
        context="probe",
        origin="trap3",
    ),
    C(
        "relocate to london from paris",
        "Are you willing to relocate to London?",
        "textarea",
        context="probe",
        origin="trap3",
    ),
]


def run(case: Case) -> Answer | Left:
    field = FormField(case.label, case.kind, case.options, True)  # type: ignore[arg-type]
    return resolve(field, CONTEXTS[case.context]())


@pytest.mark.parametrize("case", QUESTIONS, ids=[c.name for c in QUESTIONS])
def test_question(case: Case) -> None:
    result = run(case)
    if case.expected == LEFT:
        assert isinstance(result, Left), result
    else:
        assert isinstance(result, Answer), result
        assert result.value == case.expected


@pytest.mark.parametrize(("label", "allowed", "origin"), BOXES, ids=[b[0][:60] for b in BOXES])
def test_box(label: str, allowed: bool, origin: str) -> None:
    assert classify(label).allowed is allowed
    ticked = resolve(FormField(label, "checkbox"), CONTEXTS["fr"]())
    if allowed:
        assert isinstance(ticked, Answer), ticked
        assert ticked.value is True
    else:
        assert isinstance(ticked, Left), ticked


def test_the_table_has_our_own_cases_and_half_are_traps() -> None:
    own = [c for c in QUESTIONS if c.origin != "review"] + [b for b in BOXES if b[2] != "review"]
    traps = [c for c in QUESTIONS if c.origin == "trap"] + [b for b in BOXES if b[2] == "trap"]
    assert len(own) >= 40
    assert len(traps) * 2 >= len(own)


@pytest.mark.parametrize("case", ROUND3, ids=[c.name for c in ROUND3])
def test_round3_question(case: Case) -> None:
    test_question(case)


def test_round3_has_every_named_question_and_half_of_our_own_are_traps() -> None:
    review = [c for c in ROUND3 if c.origin == "review2"]
    own = [c for c in ROUND3 if c.origin in ("own3", "trap3")]
    traps = [c for c in own if c.origin == "trap3"]
    assert len(review) >= 29
    assert len(own) >= 60
    assert len(traps) * 2 >= len(own)
    assert any(" fr" not in c.context and c.label.isascii() for c in own)
    assert (
        sum(not c.label.isascii() or c.label.startswith(("Avez", "Ne ", "Quelle")) for c in own)
        >= 8
    )
