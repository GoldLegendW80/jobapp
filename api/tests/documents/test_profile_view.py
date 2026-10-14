"""The profile view and the fact check sources (D2)."""

from __future__ import annotations

import dataclasses

import pytest

from app.documents import content as c
from app.documents import profile_view
from app.documents.sources import PostingView, sources_for
from app.mail.factcheck_rules import Sources
from tests.documents.conftest import base_cv, posting_text, profile, view_of


def test_fixed_facts_come_from_the_profile_then_the_base_cv_field_by_field() -> None:
    view = view_of("alex", base_cv("alex"))
    by_key = {e.key: e for e in view.employers}
    assert list(by_key) == ["example-corp", "sample-labs", "north-shop"]
    # The profile's title always wins (Decision 7), its location too when it has one.
    assert by_key["example-corp"].title == "Platform Engineer"
    assert by_key["example-corp"].location == "Lyon"
    # A location the profile leaves empty is the base CV's.
    assert by_key["sample-labs"].location == "Grenoble"
    assert by_key["sample-labs"].title == "Software Developer"
    # An employer only the base CV states comes after the profile's own.
    assert by_key["north-shop"].organisation == "North Shop"
    assert by_key["example-corp"].end is None
    assert [s.degree for s in view.schools] == ["Bachelor of Computer Science"]
    assert view.schools[0].location == "Lyon"
    assert [(lang.name, lang.level) for lang in view.languages] == [
        ("English", "Fluent"),
        ("French", "Native"),
        ("Spanish", "B1"),
    ]
    assert view.employer_keys == ("example-corp", "sample-labs", "north-shop")


def test_an_empty_profile_takes_every_fixed_fact_from_the_base_cv() -> None:
    view = view_of("blake", base_cv("blake"))
    assert [(e.key, e.organisation, e.title, e.start, e.end) for e in view.employers] == [
        ("northwind-example", "Northwind Example", "Data Analyst", "2020", "2024")
    ]
    assert [lang.name for lang in view.languages] == ["German"]
    assert view.facts_blocks == ()
    assert view.answers == ()


def test_an_empty_profile_without_a_base_cv_is_an_empty_view() -> None:
    view = profile_view.build({}, 0)
    assert (view.employers, view.schools, view.languages, view.skills_terms) == ((), (), (), ())
    assert view.motivation == ""


def test_the_view_reads_skills_answers_and_identity_apart() -> None:
    view = view_of("alex")
    assert "Kubernetes" in view.skills_terms
    assert "Cloud platforms" in view.skills_terms
    assert "React" in view.skills_terms  # adjacent
    assert "Swift" not in view.skills_terms  # what the profile says the person lacks
    assert view.years_experience == 3.5
    assert "One month." in view.answers
    assert view.motivation.startswith("I enjoy building platforms")
    assert view.identity.full_name == "Alex Example"
    assert view.identity.email == "e2e-alex@example.org"
    assert view.revision == 7
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.revision = 8  # type: ignore[misc]


def test_a_regex_category_gives_its_name_not_its_patterns() -> None:
    sections, revision = profile("alex")
    sections = {
        **sections,
        "skills": {
            "have": [{"name": "Infra", "advanced_regex": True, "terms": [r"k8s|kubernetes"]}]
        },
    }
    assert profile_view.build(sections, revision).skills_terms == ("Infra",)


def test_sources_follow_the_spec(alex_sources: Sources, alex_upload: str) -> None:
    sources = alex_sources
    profile_text = "\n".join(sources.profile)
    assert "moved 40 services to Kubernetes" in profile_text  # facts
    assert "Cut build times by 30% with a shared cache." in sources.profile  # base CV content
    assert alex_upload.strip() in sources.profile  # the uploaded CV's text
    assert "North Shop Weekend cashier 2017 2018" in sources.profile  # base fixed
    assert any(
        "January 2023" in text and "2023-01" not in text for text in sources.profile
    )  # dates in words
    assert "3.5 years of professional experience" in sources.profile
    assert "One month." in sources.answers
    assert sources.employers == ("Example Corp", "Sample Labs", "North Shop")
    assert sources.schools == ("Example Institute of Technology",)
    assert sources.titles[-1] == "Senior Platform Engineer"  # the posting's title
    assert "Platform Engineer" in sources.titles
    assert sources.participants == ("Lumen Example",)
    assert "Kubernetes" in sources.known_terms
    assert sources.thread == ()
    assert sources.instructions == ()
    assert sources.recipients == ()


def test_the_posting_text_is_never_a_source(alex_sources: Sources) -> None:
    text = posting_text("lumen")
    every = [*alex_sources.profile, *alex_sources.answers, *alex_sources.thread]
    assert all(text not in entry and "300 services" not in entry for entry in every)


def test_sources_are_deterministic_and_without_duplicates() -> None:
    base = base_cv("alex")
    view = view_of("alex", base)
    lumen = PostingView("Lumen Example", "Senior Platform Engineer")
    first = sources_for(view, base, None, lumen)
    assert first == sources_for(view, base, None, lumen)
    assert len(set(first.profile)) == len(first.profile)
    assert "" not in first.profile


def test_sources_without_base_upload_or_posting() -> None:
    view = view_of("alex")
    sources = sources_for(view, None, None, None)
    assert sources.participants == ()
    assert "Senior Platform Engineer" not in sources.titles
    assert not any("30%" in text for text in sources.profile)


def test_a_base_cv_built_from_nothing_is_still_a_source() -> None:
    empty = c.parse("base", {})
    assert isinstance(empty, c.BaseCvContent)
    sources = sources_for(view_of("blake", empty), empty, "", None)
    assert sources.profile == ()
