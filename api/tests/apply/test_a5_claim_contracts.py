"""Positive topics and source tracing at the answer sink (#593), using invented facts."""

from dataclasses import replace

import pytest

from app.apply import agent

FACTS = (
    "I built a Microsoft Office integration.",
    "I developed a location service for robots.",
    "I designed an exit strategy for full-time workers in the Phoenix project.",
    "I built evaluation tools for robots.",
)
SOURCES = replace(agent.sources_of({}, "Engineer", "Example Robotics"), profile=FACTS)


def judged(value: str, label: str = "Why do you want to join us?", sources=SOURCES):
    question = agent.Question("k", label, "text", reason="free_text_for_the_agent")
    return agent.judge([question], agent.Reply((agent.Proposal("k", value, "facts"),)), sources)[0]


@pytest.mark.parametrize("label", ["Why do you want to join us?", "Describe your project"])
@pytest.mark.parametrize(
    "value",
    [
        "Berlin suits me",
        "Lisbon suits me",
        "Remote, please",
        "Hybrid, please",
        "Onsite, please",
        "Full-time, please",
        "The office suits me",
        "Our US team suits me",
        "I prefer Lisbon",
        "I love Berlin for robotics",
        "I love building robots in Unknownville",
        "I want to build robots. Remote, please.",
        "I build robots, and Berlin suits me.",
        "I build robots because Lisbon suits me.",
        "Berlin suits me for building robots.",
        "Remote, please, because I love robots.",
        "I want to join your team in Unknownville.",
        "I prefer the US team because I build robots.",
        "Je préfère Lisbonne.",
        "Le télétravail me convient.",
        "Lisbonne me convient pour les robots.",
        "Hybride, s'il vous plaît.",
        "I would join us in Qwertytown.",
        "I like building robots; I can work from Unknownville.",
    ],
)
def test_unproven_arrangements_are_left_even_inside_a_job_answer(value: str, label: str) -> None:
    assert judged(value, label).answer is None


@pytest.mark.parametrize("place", ["Lisbon", "Qwertytown", "US", "Osaka", "Delft"])
@pytest.mark.parametrize(
    "label",
    [
        "Why do you want to join us in {}?",
        "Why do you want to join our {} team?",
        "Why do you want to work in {}?",
    ],
)
def test_location_qualification_does_not_depend_on_a_gazetteer(place: str, label: str) -> None:
    assert judged("I built evaluation tools for robots.", label.format(place)).answer is None


@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("Describe your experience with Microsoft Office", FACTS[0]),
        ("Tell us about a location service you built", FACTS[1]),
        ("Describe an exit strategy you designed", FACTS[2]),
        ("Tell us about your full-time project experience", FACTS[2]),
        ("Why are you proud of the Phoenix project?", FACTS[2]),
        ("Describe a project", "I developed evaluation tools for robots."),
        ("Describe a project", "I designed a Microsoft Office integration."),
        ("Team preference", "I want to build robots."),
        ("Why now?", "I want to build robots."),
    ],
)
def test_grounded_job_facts_and_supported_paraphrases_are_useful(label: str, value: str) -> None:
    verdict = judged(value, label)
    assert verdict.answer is not None, verdict
    assert verdict.answer.value == value


def test_unproven_technical_claim_is_also_left() -> None:
    assert judged("I developed a quantum compiler for robots.").answer is None


TECHNICAL_OBJECTS = [
    "Microsoft Office integration",
    "location service",
    "exit strategy",
    "full-time project",
    "Phoenix project",
    "remote monitoring tool",
    "hybrid search algorithm",
    "routing algorithm",
    "evaluation service",
    "scheduling tool",
    "queue system",
    "navigation service",
    "reporting integration",
    "training pipeline",
    "storage service",
    "ranking algorithm",
    "planning tool",
    "testing system",
    "mapping service",
    "simulation tool",
    "retrieval system",
    "indexing service",
    "translation tool",
    "delivery system",
]


@pytest.mark.parametrize("object_", TECHNICAL_OBJECTS)
def test_actual_technical_facts_remain_answerable_and_unknown_neighbours_do_not(
    object_: str,
) -> None:
    value = f"I built a {object_}."
    sources = replace(SOURCES, profile=(value,))
    assert judged(value, "Describe a project", sources).answer is not None
    assert judged(f"I built an unproven {object_}.", "Describe a project", sources).answer is None


@pytest.mark.parametrize("object_", TECHNICAL_OBJECTS[7:])
def test_ordinary_technical_paraphrases_use_the_actual_source_words(object_: str) -> None:
    sources = replace(SOURCES, profile=(f"I built a {object_}.",))
    assert judged(f"I developed a {object_}.", "Describe a project", sources).answer is not None


@pytest.mark.parametrize("verb", ["build", "design", "develop", "create", "improve", "reach"])
def test_work_to_an_action_is_purpose_rather_than_a_destination(verb: str) -> None:
    value = f"I want my work to {verb} robots."
    sources = replace(SOURCES, profile=(value,))
    assert judged(value, sources=sources).answer is not None
    assert agent.refusal(f"Why do you want your work to {verb} robots?", "text") is None
    assert judged(value, "Why do you want to work in Unknownville?", sources).answer is None


def test_letter_trace_comes_from_the_prepared_document() -> None:
    value = "I built a scheduling algorithm for robots."
    assert (
        judged(value, sources=agent.sources_of({}, "Engineer", "Example Robotics")).answer is None
    )
    assert judged(value, sources=agent.sources_of({}, "Engineer", "Example Robotics", value)).answer


def test_private_practical_answers_do_not_become_proof() -> None:
    sources = agent.sources_of(
        {"answers": {"relocation": "Berlin suits me"}}, "Engineer", "Example Robotics"
    )
    assert "Berlin suits me" not in sources.answers
    assert judged("Berlin suits me", sources=sources).answer is None


@pytest.mark.parametrize(
    "value",
    [
        "I can work in Berlin.",
        "I want to build robots with our US team.",
    ],
)
def test_a_company_name_is_no_proof_of_a_persons_location(value: str) -> None:
    sources = replace(SOURCES, participants=("Berlin US Robotics",))
    assert judged(value, sources=sources).answer is None


def test_choice_options_get_the_same_practicality_guards_as_text() -> None:
    question = agent.Question("k", "Which team interests you?", "select", ("Remote", "Research"))
    sources = replace(SOURCES, known_terms=("Remote",))
    reply = agent.Reply((agent.Proposal("k", "Remote", "skills"),))
    assert agent.judge([question], reply, sources)[0].answer is None


@pytest.mark.parametrize("label", ["Why do you want to join us?", "Describe your project"])
@pytest.mark.parametrize(
    "value",
    [
        "I consent to a background check",
        "I require sponsorship",
        "I was fired",
        "I am willing to relocate",
        "I can start immediately",
    ],
)
def test_a_source_does_not_authorize_closed_topics(value: str, label: str) -> None:
    assert judged(value, label, sources=replace(SOURCES, profile=(value,))).answer is None
