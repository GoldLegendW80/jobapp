"""B21 (owner, 2026-11-04: "the agent must find jobs that fit with my profile, i don't want to find
exactly the title job I'v set"): the agent of a job website gets, in its first message, a
bounded summary of the person's profile, what the scoring and the documents writers read, and
none of their name, e-mail, phone, address, birth date, links or any other identity datum: the
page it reads can steer it. Each of those is planted as a random e2e token in the profile and
looked for in the model's first message."""

import asyncio
import dataclasses
import json
import secrets
from datetime import UTC, datetime

import pytest

from app.browser import finder
from app.profile import fit_summary, service
from tests.browser import fakes
from tests.browser.b13_runtime import Recording
from tests.browser.runtime_support import (  # noqa: F401
    Runtime,
    alice,
    bob,
    clock,
    db,
    engine,
    factory,
    migrated_url,
    one_connection,
    runtime,
)
from tests.browser.test_b13_runtime import BASE, InteractiveTools, session
from tests.browser.test_b21_vie import Vie, run, saved

NOW = datetime(2026, 11, 4, 9, tzinfo=UTC)


def planted() -> tuple[list[str], dict]:
    """A profile whose every identity and contact datum is a random token (CLAUDE.md rule 5:
    e2e accounts, invented data), beside the fit data the summary keeps."""
    token = secrets.token_hex(6)
    canaries = [
        f"Plantedname{token}",
        f"e2e-planted-{token}@example.org",
        f"+33 6 {int(token[:6], 16) % 100_000_000:08d}",
        f"Plantedcity{token}",
        f"Plantedland{token}",
        f"planted-{token}.example.org",
        f"Plantedheadline{token}",
        f"Plantedemployer{token}",
        f"Plantedstreet{token}",
        f"Plantedschool{token}",
        f"Plantedfact{token} born 1990-01-01",
        f"Plantedmotivation{token}",
        f"Plantedpreferred{token}",
    ]
    name, email, phone, city, country, site, headline, employer, street, school, fact, why, pref = (
        canaries
    )
    sections = {
        "identity": {
            "full_name": name,
            "headline": headline,
            "email": email,
            "phone": phone,
            "city": city,
            "country": country,
            "linkedin_url": f"https://www.linkedin.example.org/in/{site}",
            "website": f"https://{site}/",
            "links": [{"label": "Portfolio", "url": f"https://{site}/work"}],
            "languages": [
                {"name": "French", "level": "native"},
                {"name": "English", "level": "C1"},
            ],
        },
        "experience": {
            "employers": [
                {
                    "key": "beta",
                    "organisation": employer,
                    "title": "Data Scientist",
                    "start": "2021",
                    "end": "2023",
                    "location": street,
                },
                {
                    "key": "acme",
                    "organisation": employer,
                    "title": "GenAI Engineer",
                    "start": "2024",
                    "end": None,
                    "location": street,
                },
            ],
            "education": [{"school": school, "degree": "MSc", "start": "2019", "location": city}],
        },
        "facts": {"blocks": [{"title": "About", "body": fact}]},
        "skills": {
            "years_experience": 4,
            "have": [{"name": "LLM", "terms": ["RAG", "agents"]}],
            "adjacent": [{"name": "MLOps", "terms": ["Kubernetes"]}],
        },
        "search": {
            "title_include": {"terms": ["genai engineer"]},
            "seniority": ["mid", "senior"],
            "industries": {"terms": ["consulting"]},
            "company_types": ["consulting"],
        },
        "answers": {"motivation": why, "preferred_name": pref},
    }
    return canaries, sections


EXPECTED = {
    "years_experience": 4.0,
    "recent_roles": ["GenAI Engineer", "Data Scientist"],  # the current one first
    "skills": [{"name": "LLM", "terms": ["RAG", "agents"]}],
    "adjacent_skills": [{"name": "MLOps", "terms": ["Kubernetes"]}],
    "seniority": ["mid", "senior"],
    "languages": [{"name": "French", "level": "native"}, {"name": "English", "level": "C1"}],
    "industries": ["consulting"],
    "company_types": ["consulting"],
}


def test_the_summary_holds_the_fit_data_and_no_identity_datum():
    canaries, sections = planted()
    summary = fit_summary.summary(sections)
    assert summary == EXPECTED
    text = json.dumps(summary, ensure_ascii=False)
    for canary in canaries:
        assert canary not in text
    assert "@" not in text
    assert "http" not in text


def test_the_summary_is_bounded_and_an_invalid_section_gives_nothing():
    many = [{"name": f"Skill {n} " + "x" * 80, "terms": [f"t{i}" * 30 for i in range(40)]}
            for n in range(100)]  # fmt: skip
    summary = fit_summary.summary({"skills": {"have": many}, "experience": {"employers": "bad"}})
    assert len(json.dumps(summary, ensure_ascii=False)) <= fit_summary.SUMMARY_MAX
    assert 0 < len(summary["skills"]) <= fit_summary.CATEGORIES_MAX
    assert all(len(c["terms"]) <= fit_summary.TERMS_MAX for c in summary["skills"])
    assert "recent_roles" not in summary
    assert fit_summary.summary({}) == {}


def test_the_first_message_gets_the_stored_profile_s_summary_and_no_planted_datum(
    runtime: Runtime,  # noqa: F811
    alice,  # noqa: F811
    bob,  # noqa: F811
):
    canaries, sections = planted()
    with runtime.factory() as db_:
        profile = service.get_profile(db_, alice, NOW, lock=True)
        profile.sections = sections
        db_.commit()
    runtime.give_key(alice)

    class Counting(Recording):
        prompt = ""

        async def stream(self, request):
            self.prompt = request.prompt
            async for message in super().stream(request):
                yield message

    backend = Counting()
    context = dataclasses.replace(runtime.context(), backend_factory=lambda: backend)
    agent = finder.RuntimeFinder(lambda: context)
    summary = asyncio.run(agent.profile(str(alice)))
    one = session(InteractiveTools(), alice)
    one.interactive = dataclasses.replace(one.interactive, profile=summary)
    result = asyncio.run(agent(one))
    assert not result.stopped
    first = json.loads(backend.prompt)["search_scope"]
    assert first["person_profile"] == EXPECTED
    for canary in canaries:
        assert canary not in backend.prompt
    # Nobody else's profile: bob has none, and gets an empty summary.
    assert asyncio.run(agent.profile(str(bob))) == "{}"
    assert BASE in backend.prompt


def test_an_unreadable_profile_leaves_the_search_on_its_criteria():
    class Broken:
        sessions = None  # the context's sessions are gone: the read raises

    agent = finder.RuntimeFinder(lambda: Broken())  # type: ignore[arg-type, return-value]
    assert asyncio.run(agent.profile("00000000-0000-0000-0000-000000000001")) == "{}"


def test_the_website_runner_gives_the_agent_the_finder_s_profile_summary(monkeypatch):
    seen: list[str] = []

    async def profile(self, user_id):
        return json.dumps({"recent_roles": ["GenAI Engineer"]})

    monkeypatch.setattr(fakes.ScriptedFinder, "profile", profile)

    async def script(one):
        seen.append(one.interactive.profile)
        return finder.Ran()

    run(saved(), Vie(), script)
    assert seen == ['{"recent_roles": ["GenAI Engineer"]}']


@pytest.mark.parametrize("size", [finder.PROFILE_MAX, finder.PROFILE_MAX + 1])
def test_the_scope_bounds_the_profile(size):
    scope = finder.InteractiveScope(BASE, ("robot",))
    if size > finder.PROFILE_MAX:
        with pytest.raises(ValueError, match="context"):
            dataclasses.replace(scope, profile="x" * size)
    else:
        assert len(dataclasses.replace(scope, profile="x" * size).profile) == size
