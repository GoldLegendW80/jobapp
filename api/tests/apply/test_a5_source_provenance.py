"""Complete personal sources, opaque names and bounded predicate equivalence (#563)."""

from dataclasses import replace

import pytest

from app.apply import agent


def judged(source: str, value: str, *, source_field: str = "profile"):
    sources = replace(
        agent.sources_of({}, "Engineer", "Example Robotics"), **{source_field: (source,)}
    )
    question = agent.Question("k", "Describe a project", "text", reason="free_text_for_the_agent")
    return agent.judge([question], agent.Reply((agent.Proposal("k", value, "facts"),)), sources)[0]


NAME_FRAMES = [
    "I built a tool called '{}'.",
    'I built a tool called "{}".',
    "I built a tool called ‘{}’.",
    "I built a tool called “{}”.",
    "I built the {} platform.",
    "I built a {} tool.",
    "I tested the {} platform.",
    "We developed the {} system.",
]
NAMES = [
    ("Build and Test It", "Build and Evaluate It"),
    ("Design and Test Them", "Design and Evaluate Them"),
    ("Build and Test the Robots", "Build and Evaluate the Robots"),
    ("Build and We Test It", "Build and We Evaluate It"),
    ("robots and test it", "robots and evaluate it"),
    ("agents and test them", "agents and evaluate them"),
    ("robots and we test it", "robots and we evaluate it"),
]


@pytest.mark.parametrize("frame", NAME_FRAMES)
@pytest.mark.parametrize(("name", "changed"), NAMES)
def test_complete_names_remain_opaque_to_predicate_equivalence(frame, name, changed) -> None:
    source = frame.format(name)
    assert judged(source, frame.format(changed)).answer is None
    assert judged(source, source).answer
    assert judged(source, source.replace("I built ", "I developed ", 1)).answer


@pytest.mark.parametrize("quote", ["'{}'", '"{}"', "‘{}’", "“{}”"])
def test_sentence_punctuation_in_a_name_is_not_a_personal_claim(quote: str) -> None:
    source = f"I built a tool called {quote.format('Robots. I want to build robots.')} ."
    assert judged(source, source).answer
    assert judged(source, "I want to build robots.").answer is None


@pytest.mark.parametrize("actor", ["I", "We"])
@pytest.mark.parametrize(
    "objects", ["robots and software", "robots and agents", "agents and tools"]
)
@pytest.mark.parametrize(("source_verb", "verb"), [("built", "developed"), ("tested", "evaluated")])
def test_main_predicate_equivalence_keeps_complete_noun_lists(actor, objects, source_verb, verb):
    source, value = f"{actor} {source_verb} {objects}.", f"{actor} {verb} {objects}."
    assert judged(source, value).answer
    assert (
        judged(source, value.replace("robots", "systems").replace("agents", "people")).answer
        is None
    )


NEGATIVE_INTENTS = [
    "I would never like to build robots.",
    "I wouldn’t ever like to build robots.",
    "I no longer want to build robots.",
    "I do not want to build robots.",
    "I don't enjoy building robots.",
    "I would not love to build robots.",
    "I am not interested in building robots.",
    "I never want to build robots.",
    "I hardly want to build robots.",
    "I rarely enjoy building robots.",
    "I used to want to build robots.",
    "I stopped wanting to build robots.",
    "I cannot say I want to build robots.",
    "I am unsure whether I want to build robots.",
    "I want to avoid building robots.",
    "I prefer not to build robots.",
    "I have no wish to build robots.",
    "I have lost interest in building robots.",
    "I would like to build robots only in simulation.",
    "We want to build robots.",
]


@pytest.mark.parametrize("source", NEGATIVE_INTENTS)
def test_personal_intent_needs_its_own_complete_source(source: str) -> None:
    value = "I want to build robots."
    assert judged(source, value).answer is None
    assert judged(source, source).answer
    assert judged(value, value).answer


@pytest.mark.parametrize("source_field", ["profile", "answers"])
@pytest.mark.parametrize(
    "value",
    [
        "I want to build robots.",
        "I enjoy building robots.",
        "I hope to develop robots.",
        "I would like to build robots.",
        "I would bring my agent and evaluation work to the team.",
    ],
)
def test_achievement_is_not_personal_preference_provenance(source_field, value) -> None:
    # Earlier neutral-intent controls inferred these from achievement nouns. The
    # no-invention contract instead requires the person's actual intent source.
    assert (
        judged("I built robots and evaluation tools.", value, source_field=source_field).answer
        is None
    )
    assert judged(value, value, source_field=source_field).answer


@pytest.mark.parametrize(
    "source_field", ["known_terms", "participants", "titles", "employers", "schools"]
)
def test_even_a_complete_preference_in_an_entity_field_is_not_personal_provenance(source_field):
    value = "I want to build robots."
    assert judged(value, value, source_field=source_field).answer is None


@pytest.mark.parametrize(
    "object_", ["robots", "agents", "software", "evaluation tools", "robotics tools", "AI tools"]
)
@pytest.mark.parametrize(
    ("source", "value"),
    [
        ("I did not build {}.", "I built {}."),
        ("I plan to build {}.", "I built {}."),
        ("We built {}.", "I built {}."),
        ("I built {}.", "I will build {}."),
        ("I evaluated {}.", "I built {}."),
        ("I built {}.", "We developed {}."),
        ("I will build {}.", "I built {}."),
        ("I built {}.", "I did not build {}."),
        ("I could build {}.", "I built {}."),
        ("I built {} with my team.", "I built {}."),
    ],
)
def test_original_factual_review_controls_keep_the_complete_source(source, value, object_):
    source, value = source.format(object_), value.format(object_)
    assert judged(source, value).answer is None
    assert judged(value, value).answer


@pytest.mark.parametrize(
    ("source", "value"),
    [
        ("I built robots and tested them.", "I built robots and built them."),
        ("We built robots and evaluated them.", "I built robots and tested them."),
        ("I built robots and did not test them.", "I built robots and tested them."),
        ("I plan to build robots and evaluate them.", "I built robots and tested them."),
        ("I built robots and evaluated them.", "I built robots and will test them."),
        ("I built robots and tested them.", "I built robots."),
    ],
)
def test_original_coordinated_review_controls_keep_the_complete_source(source, value):
    assert judged(source, value).answer is None
    assert judged(value, value).answer
