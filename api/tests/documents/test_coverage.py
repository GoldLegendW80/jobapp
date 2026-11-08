# ruff: noqa: E501, the accept and refuse table reads best one case to a line
"""The keyword coverage of a posting's CV (SPEC-documents.md, "Keyword coverage"): which of the
posting's requirement terms of the person's own words the CV prints. The accept and refuse table
of the match, the requirement text, the vocabulary, and the route with two e2e people."""

from __future__ import annotations

import copy
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.documents import content as c
from app.documents import coverage, profile_view, store
from app.documents.models import Kind
from tests.documents.render_support import CV, cv_draft, give_profile
from tests.identity.api_helpers import Api, make_user
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url

__all__ = ["api", "app", "clock", "engine", "factory", "migrated_url"]

ALEX = "e2e-coverage-alex@example.org"
BLAKE = "e2e-coverage-blake@example.org"

POSTING = """Example Robotics builds warehouse robots. We use Kubernetes everywhere.

Requirements:
- Kubernetes and Terraform in production
- Airflow pipelines
- Strong TypeScript

Benefits:
- React to the market with a Python budget
"""


@pytest.mark.parametrize(
    ("requirements", "words", "printed", "terms", "missing"),
    [
        # Named in the posting, absent from the CV.
        ("- Kubernetes and Airflow", ["Kubernetes", "Airflow"], ["Kubernetes"], ["Kubernetes", "Airflow"], ["Airflow"]),
        # Every term printed: nothing missing.
        ("- Kubernetes", ["Kubernetes"], ["Ran Kubernetes clusters"], ["Kubernetes"], []),
        # Case and accents folded, the posting's spelling named.
        ("- Modélisation de données", ["modelisation"], ["Data work"], ["Modélisation"], ["Modélisation"]),
        ("- KUBERNETES", ["kubernetes"], ["kubernetes"], ["KUBERNETES"], []),
        # A plural on either side.
        ("- REST APIs", ["API"], ["Designed one API"], ["APIs"], []),
        ("- one API", ["API"], ["Designed APIs"], ["API"], []),
        # Whole words only.
        ("- JavaScript", ["Java"], [], [], []),
        ("- storage and ragged edges", ["rag"], [], [], []),
        ("- Java", ["Java"], ["JavaScript"], ["Java"], ["Java"]),
        # The longer term wins where two overlap.
        ("- Machine learning", ["learning", "machine learning"], [], ["Machine learning"], ["Machine learning"]),
        ("- Machine learning", ["learning", "machine learning"], ["Learning every day"], ["Machine learning"], ["Machine learning"]),
        # Punctuation inside a term.
        ("- CI/CD and C++", ["CI/CD", "C++"], ["Built CI/CD"], ["CI/CD", "C++"], ["C++"]),
        ("- Node.js", ["Node.js"], ["Node"], ["Node.js"], ["Node.js"]),
        # Each term once, in the posting's order.
        ("- Python\n- python 3", ["Python"], [], ["Python"], ["Python"]),
        ("- Terraform\n- AWS", ["AWS", "Terraform"], [], ["Terraform", "AWS"], ["Terraform", "AWS"]),
        # A term written over two lines of the CV is not one phrase.
        ("- Machine learning", ["machine learning"], ["Machine", "learning"], ["Machine learning"], ["Machine learning"]),
        # Nothing to match: no coverage at all.
        ("- Kubernetes", [], ["Kubernetes"], [], []),
        ("", ["Kubernetes"], [], [], []),
        ("   \n  ", ["Kubernetes"], [], [], []),
    ],
)  # fmt: skip
def test_the_match(
    requirements: str, words: list[str], printed: list[str], terms: list[str], missing: list[str]
) -> None:
    found = coverage.check(requirements, printed, words)
    assert list(found.terms) == terms
    assert list(found.missing) == missing


def test_the_requirement_text_leaves_the_pitch_out() -> None:
    text = coverage.requirement_text(POSTING)
    assert "Kubernetes and Terraform" in text
    assert "warehouse robots" not in text
    assert "We use Kubernetes everywhere" not in text
    prose = "About the role\nYou will run Airflow.\n\nRequirements\nYou know Terraform well."
    assert coverage.requirement_text(prose) == "You know Terraform well.\nYou will run Airflow."
    assert coverage.requirement_text("We are a nice company. Join us.") == ""


def test_the_vocabulary_is_the_person_own_words() -> None:
    skills = {
        "have": [
            {"name": "infra", "terms": ["Kubernetes", "kubernetes"]},
            {"name": "patterns", "advanced_regex": True, "terms": [r"k8s|kube\w*"]},
        ],
        "adjacent": [{"name": "front", "terms": ["React"]}],
        "none": [{"name": "mobile", "terms": ["Swift"]}],
        "gates": [{"name": "clearance", "terms": ["security clearance"]}],
    }
    base = c.BaseCvContent(
        competencies=["Platform engineering", "react"],
        skills=[c.SkillGroup(label="Data", items=["Airflow"])],
    )
    assert coverage.vocabulary(skills, base) == [
        "Kubernetes",
        "React",
        "Platform engineering",
        "Airflow",
    ]
    assert coverage.vocabulary({"have": "not a list"}, None) == []
    assert coverage.vocabulary(None, None) == []


def test_what_the_template_prints_counts() -> None:
    view = profile_view.ProfileView(
        revision=1,
        employers=(
            profile_view.Employer(
                "example-corp", "Example Corp", "Platform Engineer", None, None, ""
            ),
        ),
        languages=(profile_view.Language("English", "fluent"),),
    )
    printed = coverage.printed(c.CvContent(headline="Engineer"), view)
    found = coverage.check("- English and platform engineer", printed, ["English", "Platform"])
    assert found.terms == ("English", "platform")
    assert found.missing == ()


# --- the route ----------------------------------------------------------------------------------


def _person(factory: sessionmaker[Session], api: Api, email: str, clock: Any) -> tuple[str, Any]:
    user = make_user(factory, email=email, name=email.split("@")[0])
    give_profile(factory, user.id, clock())
    return api.login_token(email), user.id


def _posting(factory: sessionmaker[Session], user: uuid.UUID, text: str) -> uuid.UUID:
    with factory() as db:
        found: uuid.UUID = db.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title, posting_text) VALUES "
                "(:u, :s, 'Example Robotics', 'Platform engineer', :t) RETURNING id"
            ),
            {"u": user, "s": f"robotics-{uuid.uuid4().hex[:8]}", "t": text},
        ).scalar_one()
        db.commit()
    return found


def test_the_route_names_the_missing_terms(
    factory: sessionmaker[Session], api: Api, clock: Any
) -> None:
    alex, alex_id = _person(factory, api, ALEX, clock)
    blake, _ = _person(factory, api, BLAKE, clock)
    posting = _posting(factory, alex_id, POSTING)
    written = copy.deepcopy(CV)
    written["skills"] = [group for group in written["skills"] if group["label"] != "Frontend"]
    for role in written["experience"]:
        role["bullets"] = [line for line in role["bullets"] if "TypeScript" not in line]
    with factory() as db:
        document = cv_draft(db, alex_id, posting, clock(), written)
        letter = store.get_or_create(db, alex_id, posting, Kind.letter, at=clock())
        db.commit()
        letter_id = letter.id
    read = api.get(f"/api/documents/{document}/coverage", token=alex)
    assert read.status_code == 200, read.text
    body = read.json()
    # Alex's skills: Kubernetes, Terraform, Python have; TypeScript adjacent. Airflow is not his
    # word, and Python stands under Benefits only.
    assert body["terms"] == ["Kubernetes", "Terraform", "TypeScript"]
    assert body["missing"] == ["TypeScript"]
    assert body["draft_revision"] == 2
    assert read.headers["cache-control"] == "private, no-store"
    assert "TypeScript" not in str(written)

    assert api.get(f"/api/documents/{document}/coverage", token=blake).status_code == 404
    assert api.get(f"/api/documents/{uuid.uuid4()}/coverage", token=alex).status_code == 404
    other = api.get(f"/api/documents/{letter_id}/coverage", token=alex).json()
    assert (other["terms"], other["missing"]) == ([], [])


def test_a_posting_without_requirement_lines_says_nothing(
    factory: sessionmaker[Session], api: Api, clock: Any
) -> None:
    alex, alex_id = _person(factory, api, ALEX, clock)
    posting = _posting(factory, alex_id, "We use Kubernetes and TypeScript. Join us.")
    with factory() as db:
        document = cv_draft(db, alex_id, posting, clock())
    body = api.get(f"/api/documents/{document}/coverage", token=alex).json()
    assert (body["terms"], body["missing"]) == ([], [])
