"""Quoted spans and typed profile metadata preserve their provenance (#563)."""

from dataclasses import replace

import pytest

from app.apply import agent
from app.profile.schemas import FACTS_MAX_BYTES

QUESTION = agent.Question("k", "Describe a project", "text", reason="free_text_for_the_agent")


def judged(value, sources):
    return agent.judge([QUESTION], agent.Reply((agent.Proposal("k", value, "facts"),)), sources)[0]


CLAIMS = [
    "I want to build robots.",
    "I built robots.",
    "I tested robots.",
    "We built software.",
    "I enjoy building robots.",
]


@pytest.mark.parametrize("quote", ["'{}'", '"{}"', "‘{}’", "“{}”"])
@pytest.mark.parametrize("separator", [" ", "\n", "\r\n", "\r"])
@pytest.mark.parametrize("claim", CLAIMS)
@pytest.mark.parametrize("name_start", ["Robots", "Someone's robots", "Someone’s robots"])
def test_complete_quoted_names_do_not_create_personal_assertions(
    quote, separator, claim, name_start
):
    name = f"{name_start}.{separator}{claim}{separator}Again"
    source = f"I built a tool called {quote.format(name)}."
    sources = replace(agent.sources_of({}, "Engineer", "Example Robotics"), profile=(source,))
    assert judged(claim, sources).answer is None
    assert judged(source, sources).answer
    explicit = replace(sources, profile=(source, claim))
    assert judged(claim, explicit).answer


@pytest.mark.parametrize("opener", ["'", '"', "‘", "“"])
@pytest.mark.parametrize("claim", CLAIMS)
def test_unbalanced_quoted_sources_cannot_prove_an_inner_personal_sentence(opener, claim):
    source = f"I built a tool called {opener}Robots.\n{claim}\nAgain."
    sources = replace(agent.sources_of({}, "Engineer", "Example Robotics"), profile=(source,))
    assert judged(claim, sources).answer is None


@pytest.mark.parametrize("opener", ["‘", "“"])
def test_many_unmatched_quote_openers_cannot_yield_a_claim(opener):
    suffix, title = "\nI want to build robots.", "Project"
    count = (FACTS_MAX_BYTES - len((suffix + title).encode())) // len(opener.encode())
    source = opener * count + suffix
    sources = agent.sources_of(
        {"facts": {"blocks": [{"title": title, "body": source}]}},
        "Engineer",
        "Example Robotics",
    )
    assert judged("I want to build robots.", sources).answer is None


@pytest.mark.parametrize(
    ("opener", "closer"),
    [
        ("'", '"'),
        ("'", "’"),
        ("'", "”"),
        ('"', "'"),
        ('"', "’"),
        ('"', "”"),
        ("‘", "'"),
        ("‘", '"'),
        ("‘", "”"),
        ("“", "'"),
        ("“", '"'),
        ("“", "’"),
    ],
)
def test_mismatched_quotes_cannot_expose_a_personal_sentence(opener, closer):
    source = f"I built a tool called {opener}Robots.\nI want to build robots.\nAgain{closer}."
    sources = replace(agent.sources_of({}, "Engineer", "Example Robotics"), profile=(source,))
    assert judged("I want to build robots.", sources).answer is None


@pytest.mark.parametrize("apostrophe", ["'", "’"])
def test_a_trailing_word_apostrophe_is_preserved(apostrophe):
    source = f"I built robots{apostrophe} tools."
    sources = replace(agent.sources_of({}, "Engineer", "Example Robotics"), profile=(source,))
    assert judged(source, sources).answer


def metadata_sections(field, value):
    employer = {
        "key": "robotics",
        "title": "Engineer",
        "organisation": "Example Robotics",
        "start": "2019",
        "end": "2021",
    }
    school = {
        "degree": "MSc Robotics",
        "school": "Example University",
        "start": "2016",
        "end": "2018",
    }
    (employer if field in employer else school)[field] = value
    return {"experience": {"employers": [employer], "education": [school]}}


@pytest.mark.parametrize("field", ["title", "organisation", "degree", "school"])
@pytest.mark.parametrize("claim", CLAIMS)
def test_typed_metadata_does_not_become_personal_assertion_provenance(field, claim):
    sections = metadata_sections(field, claim)
    sources = agent.sources_of(sections, "Engineer", "Example Robotics")
    assert judged(claim, sources).answer is None
    assert sources.profile == ()
    # The real facts body, rather than its title or a synthesized employment row,
    # is an explicit source for the same words.
    sections["facts"] = {"blocks": [{"title": "Project", "body": claim}]}
    assert judged(claim, agent.sources_of(sections, "Engineer", "Example Robotics")).answer


@pytest.mark.parametrize("field", ["title", "organisation", "degree", "school"])
@pytest.mark.parametrize("claim", CLAIMS)
def test_a_facts_block_title_is_metadata_not_a_personal_assertion(field, claim):
    sections = metadata_sections(field, claim)
    sections["facts"] = {"blocks": [{"title": claim, "body": "I built a tool."}]}
    sources = agent.sources_of(sections, "Engineer", "Example Robotics")
    assert judged(claim, sources).answer is None
    assert judged("I built a tool.", sources).answer


@pytest.mark.parametrize("claim", CLAIMS)
@pytest.mark.parametrize("source", ["facts", "motivation", "letter"])
def test_actual_personal_prose_remains_a_complete_source(source, claim):
    sections = metadata_sections("title", "Engineer")
    letter = ""
    if source == "facts":
        sections["facts"] = {"blocks": [{"title": "Project", "body": claim}]}
    elif source == "motivation":
        sections["answers"] = {"motivation": claim}
    else:
        letter = claim
    sources = agent.sources_of(sections, "Engineer", "Example Robotics", letter)
    assert judged(claim, sources).answer


def test_metadata_still_supplies_independent_entity_and_date_fact_checks():
    sources = agent.sources_of(metadata_sections("title", "Engineer"), "Engineer", "Robotics")
    assert sources.employers == ("Example Robotics",)
    assert sources.schools == ("Example University",)
    assert "Engineer" in sources.titles
    assert not agent._issues("Engineer at Example Robotics in 2019.", sources)
    assert not agent._issues("MSc Robotics at Example University in 2018.", sources)
    assert "year_not_in_sources" in agent._issues("An achievement in 2025.", sources)
    assert "employer_not_in_profile" in agent._issues("Engineer at Unknown Ventures.", sources)
