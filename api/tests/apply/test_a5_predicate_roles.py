"""The answer sink preserves predicate roles and explicit preference polarity (#563)."""

from dataclasses import replace

import pytest

from app.apply import agent


def judged(source: str, value: str):
    sources = replace(agent.sources_of({}, "Engineer", "Example Robotics"), profile=(source,))
    question = agent.Question("k", "Describe a project", "text", reason="free_text_for_the_agent")
    return agent.judge([question], agent.Reply((agent.Proposal("k", value, "facts"),)), sources)[0]


PREDICATE_PAIRS = [
    ("I built a design tool.", "I developed a design tool."),
    ("I developed a build tool.", "I designed a build tool."),
    ("I tested a build system.", "I evaluated a build system."),
    ("I built the Test platform.", "I created the Test platform."),
    ("We built agents and evaluated them.", "We built agents and tested them."),
    ("We build software and test it.", "We develop software and evaluate it."),
    (
        "I built robots and tested the Design tool.",
        "I developed robots and evaluated the Design tool.",
    ),
    ("My work built a design tool.", "My work developed a design tool."),
    ("Our work tested a build system.", "Our work evaluated a build system."),
    (
        "The robots I built are in the Test platform.",
        "The robots I designed are in the Test platform.",
    ),
    (
        "The software we tested is in the Design system.",
        "The software we evaluated is in the Design system.",
    ),
    ("I built robots and we tested them.", "I designed robots and we evaluated them."),
]


@pytest.mark.parametrize(("source", "value"), PREDICATE_PAIRS)
def test_equivalence_changes_clear_predicates_and_preserves_objects(
    source: str, value: str
) -> None:
    verdict = judged(source, value)
    assert verdict.answer is not None, verdict
    assert verdict.answer.value == value


OBJECT_PAIRS = [
    ("design tool", "build tool"),
    ("build tool", "design tool"),
    ("robot design tool", "robot build tool"),
    ("test system", "evaluate system"),
    ("testing system", "evaluating system"),
    ("Design tools", "Build tools"),
    ("Test platform", "Evaluate platform"),
    ("Building platform", "Designing platform"),
    ("develop system", "create system"),
    ("created tools", "designed tools"),
    ("Build and Test platform", "Design and Evaluate platform"),
    ("design systems", "build systems"),
]


@pytest.mark.parametrize(("source_object", "changed_object"), OBJECT_PAIRS)
@pytest.mark.parametrize(
    ("source_frame", "value_frame"),
    [
        ("I developed a {}.", "I developed a {}."),
        ("I tested a {}.", "I evaluated a {}."),
        ("I built robots and tested the {}.", "I designed robots and evaluated the {}."),
    ],
)
def test_technical_nouns_and_proper_names_are_not_predicates(
    source_object: str, changed_object: str, source_frame: str, value_frame: str
) -> None:
    source = source_frame.format(source_object)
    assert judged(source, value_frame.format(changed_object)).answer is None
    verdict = judged(source, source)
    assert verdict.answer is not None, verdict
    assert verdict.answer.value == source


@pytest.mark.parametrize(
    ("source", "value"),
    [
        ("I build robots and design systems.", "I build robots and develop systems."),
        ("I build robots and test systems.", "I build robots and evaluate systems."),
        ("I built robots and design tools.", "I built robots and build tools."),
        ("I tested build and design tools.", "I evaluated build and develop tools."),
        ("I built a Build and Test platform.", "I designed a Build and Evaluate platform."),
        ("My work built a design tool.", "My work developed a build tool."),
        (
            "The robots I built are in the Test platform.",
            "The robots I developed are in the Evaluate platform.",
        ),
        ("I built robots with the design tool.", "I built robots with the build tool."),
    ],
)
def test_uncertain_coordination_and_nominal_objects_stay_exact(source: str, value: str) -> None:
    assert judged(source, value).answer is None
    verdict = judged(source, source)
    assert verdict.answer is not None, verdict
    assert verdict.answer.value == source


PREFERENCES = [
    ("want to build robots", "do not want to build robots"),
    ("want to build robots", "don't want to build robots"),
    ("enjoy building robots", "do not enjoy building robots"),
    ("enjoy building robots", "don't enjoy building robots"),
    ("like to build robots", "do not like to build robots"),
    ("like building robots", "don't like building robots"),
    ("love to build robots", "do not love to build robots"),
    ("love building robots", "don't love building robots"),
    ("hope to build robots", "do not hope to build robots"),
    ("would like to build robots", "would not like to build robots"),
    ("would love to build robots", "wouldn't love to build robots"),
    ("am interested in building robots", "am not interested in building robots"),
    ("want to build robots", "never want to build robots"),
    ("enjoy building robots", "don't ever enjoy building robots"),
]


@pytest.mark.parametrize(("positive", "negative"), PREFERENCES)
@pytest.mark.parametrize("actor", ["I", "We"])
def test_neutral_motivation_cannot_reverse_an_explicit_preference(
    positive: str, negative: str, actor: str
) -> None:
    if actor == "We":
        positive, negative = positive.replace("am ", "are "), negative.replace("am ", "are ")
    source, value = f"{actor} {negative}.", f"{actor} {positive}."
    assert judged(source, value).answer is None
    for own in (source, value):
        verdict = judged(own, own)
        assert verdict.answer is not None, verdict
        assert verdict.answer.value == own


@pytest.mark.parametrize(
    "value",
    [
        "I want to build robots.",
        "I enjoy building robots.",
        "I hope to develop robots.",
        "I would like to build robots.",
    ],
)
def test_personal_intent_requires_a_source_even_without_a_sourced_opposite(value: str) -> None:
    # Reconcile the original neutral-intent controls with no invention: past
    # achievements do not establish preferences. Preserve each sourced companion.
    assert judged("I built robots.", value).answer is None
    verdict = judged(value, value)
    assert verdict.answer is not None, verdict
    assert verdict.answer.value == value


@pytest.mark.parametrize(
    ("source", "value"),
    [
        ("I'm not interested in building robots.", "I am interested in building robots."),
        ("We aren't interested in building robots.", "We are interested in building robots."),
        ("I don't want to build robots.", "I want to develop robots."),
        ("I don't like building robots.", "I enjoy designing robots."),
        ("I do not want to build robots.", "I built robots. I want to build robots."),
        ("I don't want to build robots and test them.", "I want to build robots and test them."),
        (
            "I want to build robots and do not want to test them.",
            "I want to build robots and test them.",
        ),
        ("We do not enjoy testing agents.", "We hope to evaluate agents."),
        ("I am not interested in robotics.", "I am interested in robotics."),
        ("I do not want to build robots.", "I want to build robots with this team."),
        ("I do not enjoy building robots.", "I hope to develop robots for your team."),
        ("I do not want to build robots with this team.", "I want to build robots."),
    ],
)
def test_preference_polarity_survives_contractions_and_technical_paraphrases(
    source: str, value: str
) -> None:
    assert judged(source, value).answer is None


@pytest.mark.parametrize(
    ("action", "gerund"),
    [
        ("improve", "improving"),
        ("join", "joining"),
        ("work", "working"),
        ("learn", "learning"),
        ("help", "helping"),
        ("bring", "bringing"),
    ],
)
def test_polarity_applies_to_the_whole_neutral_action_grammar(action: str, gerund: str) -> None:
    assert judged(f"I do not enjoy {gerund} robots.", f"I want to {action} robots.").answer is None
