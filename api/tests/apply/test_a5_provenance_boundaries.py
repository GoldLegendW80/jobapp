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


@pytest.mark.parametrize("quote", ["'{}'", '"{}"', "‘{}’", "“{}”"])
@pytest.mark.parametrize(
    "name_start", ["robots' tools", "robots’ tools", "Someone's robots", "Someone’s robots"]
)
@pytest.mark.parametrize("claim", CLAIMS)
def test_competing_quote_and_possessive_readings_cannot_prove_an_inner_claim(
    quote, name_start, claim
):
    source = f"I built a tool called {quote.format(f'{name_start}.\n{claim}\nAgain')}."
    sources = replace(agent.sources_of({}, "Engineer", "Example Robotics"), profile=(source,))
    assert judged(claim, sources).answer is None
    # Keeping all of the person's words does not reinterpret the ambiguous span.
    assert judged(source, sources).answer
    assert judged(source.replace("Again", "Elsewhere"), sources).answer is None
    assert judged(claim, replace(sources, profile=(source, claim))).answer


@pytest.mark.parametrize(
    "source",
    [
        "I built robots' tools.",
        "I built robots’ tools.",
        "I built someone's tools.",
        "I built someone’s tools.",
        "I don't want to build robots.",
        "I don’t want to build robots.",
        "I built a tool called 'Robots'.",
        "I built a tool called ‘Robots’.",
        'I built a tool called "Robots".',
        "I built a tool called “Robots”.",
    ],
)
def test_ordinary_apostrophes_and_clear_quoted_names_keep_their_complete_source(source):
    sources = replace(agent.sources_of({}, "Engineer", "Example Robotics"), profile=(source,))
    assert judged(source, sources).answer
    if source.startswith("I built"):
        assert judged(source.replace("I built", "I developed", 1), sources).answer


@pytest.mark.parametrize("quote", ["'{}'", "‘{}’"])
@pytest.mark.parametrize("separator", [" ", "\n", "\r\n", "\r"])
def test_an_ambiguous_quote_cannot_supply_any_shorter_personal_sentence(quote, separator):
    name = f"robots{quote[-1]} tools.{separator}I want to build robots.{separator}Again"
    source = f"I built a tool called {quote.format(name)}."
    sources = replace(agent.sources_of({}, "Engineer", "Example Robotics"), profile=(source,))
    assert judged("I want to build robots.", sources).answer is None
    assert (
        judged("I built a tool called " + quote.format("robots") + " tools.", sources).answer
        is None
    )
    assert judged(source, sources).answer


@pytest.mark.parametrize("quote", ["'{}'", "‘{}’"])
def test_unique_quote_interpretation_uses_the_late_closer(quote):
    name = f"robots{quote[-1]} tools.\nI want to build robots.\nAgain."
    outside = "I tested software."
    source = f"I built a tool called {quote.format(name)}.\n{outside}"
    sources = replace(agent.sources_of({}, "Engineer", "Example Robotics"), profile=(source,))
    assert judged("I want to build robots.", sources).answer is None
    assert judged(outside, sources).answer
    assert judged(source, sources).answer


@pytest.mark.parametrize(
    "unit", ["I built robots' tools. ", "'robots' tools. I want to build robots. Again'. "]
)
def test_quote_interpretation_visits_each_token_at_most_twice(monkeypatch, unit):
    # The largest facts body keeps cost linear even for repeated ambiguous spans.
    source = (unit * (FACTS_MAX_BYTES // len(unit))).rstrip()
    original = agent._CLAIM_TOKEN
    visited = 0

    class CountedTokens:
        def finditer(self, text):
            nonlocal visited
            for token in original.finditer(text):
                visited += 1
                yield token

    monkeypatch.setattr(agent, "_CLAIM_TOKEN", CountedTokens())
    agent._claim_sentences(source)
    assert visited <= 2 * sum(1 for _ in original.finditer(source))


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
