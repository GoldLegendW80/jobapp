"""Fixtures of the profile tests: the identity fixtures (real Postgres at head, emptied after each
test; the app with a frozen clock; the cookie-explicit client), signed-in users, and a complete
sample profile."""

import copy
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from tests.identity import conftest as identity
from tests.identity.api_helpers import Api, make_user

# The identity fixtures, made visible to the tests of this folder.
migrated_url = identity.migrated_url
engine = identity.engine
factory = identity.factory
db = identity.db
clock = identity.clock
app = identity.app
api = identity.api

ALICE = "alice@example.org"
BOB = "bob@example.org"

FULL: dict[str, dict[str, Any]] = {
    "identity": {
        "full_name": "Ada Example",
        "headline": "GenAI Engineer",
        "email": "ada@example.org",
        "phone": "+33 6 12 34 56 78",
        "city": "Bordeaux",
        "country": "France",
        "linkedin_url": "https://www.linkedin.com/in/ada-example/",
        "website": "https://ada.example.org/",
        "links": [{"label": "GitHub", "url": "https://github.com/ada"}],
        "languages": [
            {"name": "French", "level": "Mother tongue"},
            {"name": "English", "level": "Advanced (C1)"},
        ],
    },
    "experience": {
        "employers": [
            {
                "key": "acme",
                "organisation": "Acme Group",
                "title": "GenAI Engineer",
                "start": "2025-09",
                "end": None,
                "location": "Bordeaux",
                "contract": "CDI",
            },
            {
                "key": "clinic",
                "organisation": "Clinic",
                "title": "Full-stack Developer",
                "start": "2023-01",
                "end": "2025-08",
                "location": None,
                "contract": "apprenticeship",
            },
        ],
        "education": [
            {
                "school": "School of Engineering",
                "degree": "MSc",
                "start": "2022-09",
                "end": "2025-08",
                "location": "Bordeaux",
                "details": "Specialization: AI",
            },
            {
                "school": "High school",
                "degree": "Baccalaureate",
                "start": None,
                "end": None,
                "location": None,
                "details": None,
            },
        ],
    },
    "facts": {
        "blocks": [
            {"title": "Hard rules", "body": "1. Every claim traces to this base.\n2. No metric."},
            {"title": "What was built", "body": "## Freight\n\nAn agent-driven back-office."},
        ]
    },
    "skills": {
        "years_experience": 3.5,
        "have": [
            {"name": "agents", "terms": ["agent", "agentic"], "advanced_regex": False},
            {"name": "fine_tuning", "terms": [r"fine[- ]?tun\w*"], "advanced_regex": True},
        ],
        "adjacent": [{"name": "rag", "terms": ["rag", "retrieval"], "advanced_regex": False}],
        "none": [{"name": "research", "terms": ["pytorch"], "advanced_regex": False}],
        "gates": [
            {
                "name": "phd_required",
                "terms": [r"phd (is )?required"],
                "advanced_regex": True,
                "penalty": 25,
            }
        ],
    },
    "search": {
        "title_include": {"terms": ["ai engineer", "llm"], "advanced_regex": False},
        "title_exclude": {"terms": [r"\b(staff|principal)\b"], "advanced_regex": True},
        "location": {"terms": ["paris", "france", "remote"], "advanced_regex": False},
        "location_exclude": {"terms": ["united states"], "advanced_regex": False},
        "contract_types": ["CDI", "VIE"],
        "remote_stance": "hybrid",
        "min_fit": 60,
        "days": 14,
        "never_apply": ["No cybersecurity roles."],
        "browser_sources": [
            {
                "kind": "freework",
                "query": "data engineer",
                "location": "Germany",
                "enabled": False,
                "interactive_only": False,
                "accepted_at": None,
                "text_version": None,
            }
        ],
        # A job website ticked in Find jobs: what every search runs and what completeness needs
        # (owner, 2026-10-28).
        "interactive_sites": [
            {
                "platform": "custom",
                "url": "https://jobs.example.org/",
                "enabled": True,
                "mode": "llm_browser",
            }
        ],
        "interactive_instructions": None,
        "linkedin_search": "api",
        "sweep_schedule": {
            "enabled": False,
            "days": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
            "times": ["06:17"],
            "timezone": "Europe/Paris",
        },
        "documents": {"auto": True, "min_fit": 70, "daily_ceiling": 100},
        "seniority": ["mid", "senior"],
        "industries": {"terms": ["gaming", "fintech"], "advanced_regex": False},
        "company_types": ["scaleup", "large_company"],
        "compensation": {
            "salary": {"min": 55000, "max": 70000, "currency": "EUR"},
            "daily_rate": {"min": 500, "max": 650, "currency": "EUR"},
        },
    },
    "answers": {
        "work_authorization": [
            {
                "country": "France",
                "answer": "EU citizen, no sponsorship.",
                "needs_sponsorship": False,
            },
            {
                "country": "United Kingdom",
                "answer": "Sponsorship needed.",
                "needs_sponsorship": True,
            },
        ],
        "notice_period": "One to three months.",
        "earliest_start": "2026-12-01",
        "salary_policy": "Never invent a number.",
        "relocation": "Open to Paris.",
        "travel": "Monthly travel is fine.",
        "motivation": "I want to build agents that run real operations.\nWith humans in charge.",
        "heard_about": "The company's careers page.",
        "leave_blank": ["gender", "ethnicity"],
        "preferred_name": "Ana",
        "pronouns": "she/her",
        "years_experience_stated": "About 3.5 years",
        "highest_degree": "Master's degree",
        "highest_degree_year": 2022,
        "willing_onsite": True,
        "willing_hybrid": True,
        "previously_applied_default": False,
        "background_check_consent": False,
        "reference_check_consent": True,
        "eeo_policy": "stored",
        "eeo_answers": [{"topic": "gender", "answer": "Woman"}],
        "salary_stance": "range",
        "salary_ranges": [
            {"currency": "EUR", "minimum": 50000, "maximum": 60000, "period": "year"},
            {"currency": "GBP", "minimum": 45000, "maximum": None, "period": "year"},
        ],
    },
    # The onboarding dates stay null: only the wizard's routes write them, a save refuses them.
    "preferences": {
        "onboarding": {
            "completed_at": None,
            "dismissed_at": None,
            "steps_passed": [],
        },
        "documents_language": "fr",
        "documents_tone": "warm",
    },
}


def full(section: str) -> dict[str, Any]:
    """A deep copy of the complete sample of one section, in its stored (normalised) form."""
    return copy.deepcopy(FULL[section])


def signed_in(factory: sessionmaker[Session], api: Api, email: str) -> str:
    make_user(factory, email=email, name=email.split("@")[0])
    return api.login_token(email)


@pytest.fixture
def alice(factory: sessionmaker[Session], api: Api) -> str:
    """Session token of a signed-in user."""
    return signed_in(factory, api, ALICE)


@pytest.fixture
def bob(factory: sessionmaker[Session], api: Api) -> str:
    return signed_in(factory, api, BOB)
