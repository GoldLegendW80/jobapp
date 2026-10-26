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
    sources = SOURCES
    if value == "I want to build robots.":
        # Keep the former achievement-only intent control as an explicit refusal
        # and its companion with the person's actual positive preference source.
        assert judged(value, label).answer is None
        sources = replace(SOURCES, profile=(*FACTS, value))
    verdict = judged(value, label, sources)
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


COORDINATED_FACTS = (
    "The robots this team builds are the kind of work I want to do.",
    "This role puts agents in front of real operations.",
    "I build agents and evaluate them.",
    "I build software and improve it.",
    "I build agents and design tools for them.",
    "I build agents and develop tools for them.",
    "I build agents and create tools for them.",
    "I build agents and test the software.",
    "We build agents and test them.",
    "We build software and improve it.",
    "The software this team builds is work I want to do.",
    "This team builds robots for real operations.",
)


@pytest.mark.parametrize("value", COORDINATED_FACTS)
def test_factual_coordination_is_not_invented_from_the_original_topic_words(value: str) -> None:
    assert judged(value).answer is None


@pytest.mark.parametrize(
    "value",
    [
        "The robots this team builds are the kind of work I want to do.",
        "This role puts agents in front of real operations.",
        "I would bring my agent and evaluation work to the team.",
        "I would bring my software and evaluation work to your team.",
        "I would bring my evaluation work to this team.",
        "I want to build agents and test them with this team.",
        "I want to build agents, and test them, with this team.",
        "I want to build agents — and test them – with this team.",
        "I build agents and evaluate them.",
        "I build software and improve it.",
        "I build agents and design tools for them.",
        "I build agents and develop tools for them.",
        "I build agents and create tools for them.",
        "I build agents and test the software.",
        "We build agents and test them.",
        "We build software and improve it.",
        "I would bring my robotics and software work to the team.",
        "I would bring my agent and robotics work to your team.",
        "The software this team builds is work I want to do.",
        "This team builds robots for real operations.",
        "I want to build agents and I want to evaluate them.",
    ],
)
def test_coordinated_job_motivation_preserves_its_subject_and_objects(value: str) -> None:
    # Preserve every original intent control: unrelated achievements prove no
    # desire. The person's complete source supports the genuine positive companion.
    assert judged(value).answer is None
    sources = replace(SOURCES, profile=(*FACTS, value))
    verdict = judged(value, sources=sources)
    assert verdict.answer is not None, verdict
    assert verdict.answer.value == agent.undashed(value)


@pytest.mark.parametrize(
    "tail",
    [
        "Berlin suits me",
        "Lisbon suits me",
        "Remote, please",
        "Hybrid, please",
        "Onsite, please",
        "Full-time, please",
        "the office suits me",
        "our US team suits me",
        "I prefer Lisbon",
        "I can work from Unknownville",
        "I require sponsorship",
        "I consent to a background check",
        "I can start tomorrow",
        "I am available now",
        "I am willing to relocate",
        "I have a PhD",
        "I am certified",
        "test them in Lisbon",
        "build a quantum compiler",
        "bring my salary expectations",
        "I live in Berlin",
        "I have a security clearance",
        "my work is at https://agents.example.org/demo",
    ],
)
def test_coordination_does_not_lend_grounding_to_another_claim(tail: str) -> None:
    assert judged(f"I build agents and {tail}.").answer is None


def test_a_posting_link_is_still_reported_by_the_fact_check() -> None:
    value = (
        "I would bring my agent and evaluation work to the team. "
        "My work is at https://agents.example.org/demo."
    )
    assert judged(value).code == "fact_check"


@pytest.mark.parametrize("entity", ["company", "skill", "title", "employer", "project"])
@pytest.mark.parametrize(
    "claim",
    [
        "I build agents and test them in Modelport.",
        "I build agents and design software near Modelport.",
        "I create robots at Modelport.",
        "I develop tools from Modelport.",
    ],
)
def test_entity_words_do_not_prove_a_work_location(entity: str, claim: str) -> None:
    fields = {
        "company": {"participants": ("Modelport Robotics",)},
        "skill": {"known_terms": ("Modelport",)},
        "title": {"titles": ("Modelport Engineer",)},
        "employer": {"employers": ("Modelport",)},
        "project": {"profile": ("I built the Modelport project for agents.",)},
    }
    assert judged(claim, sources=replace(SOURCES, **fields[entity])).answer is None


@pytest.mark.parametrize("actor", ["I", "We"])
@pytest.mark.parametrize("verb", ["built", "created", "developed"])
@pytest.mark.parametrize("preposition", ["in", "at", "near"])
def test_a_locative_paraphrase_retains_the_actual_work_relationship(
    actor, verb, preposition
) -> None:
    source = f"{actor} built agents {preposition} the Example simulation."
    value = f"{actor} {verb} agents {preposition} the Example simulation."
    assert judged(value, sources=replace(SOURCES, profile=(source,))).answer


@pytest.mark.parametrize(
    "value",
    [
        "We created agents in the Example simulation.",
        "I will build agents in the Example simulation.",
        "I create agents in the Example simulation.",
        "I tested agents in the Example simulation.",
        "I built robots in the Example simulation.",
        "I built agents near the Example simulation.",
    ],
)
def test_a_source_relationship_cannot_change_its_actor_predicate_object_or_preposition(
    value,
) -> None:
    source = "I built agents in the Example simulation."
    assert judged(value, sources=replace(SOURCES, profile=(source,))).answer is None


@pytest.mark.parametrize(
    ("source", "value"),
    [
        ("I build agents and test them in Python.", "I build agents and evaluate them in Python."),
        (
            "We built agents and evaluated them in Python.",
            "We built agents and tested them in Python.",
        ),
        ("I built robots in Python.", "I developed robots in Python."),
        ("I built agents in a location service.", "I created agents in a location service."),
    ],
)
def test_grounded_technical_locatives_and_coordinated_predicates_remain_useful(
    source, value
) -> None:
    assert judged(value, sources=replace(SOURCES, profile=(source,))).answer


@pytest.mark.parametrize("actor", ["I", "We"])
def test_a_coordinated_object_list_can_have_more_than_two_nouns(actor) -> None:
    value = f"{actor} would bring my agent and software and evaluation work to the team."
    assert judged(value).answer is None
    assert judged(value, sources=replace(SOURCES, profile=(value,))).answer


# Every frame admitted by the claim sink needs the same relational provenance. A
# company, project or skill can lend a word, but cannot lend a personal location.
NOMINAL_LOCATIVE_FRAMES = [
    "My work is {preposition} Modelport.",
    "Our work is {preposition} Modelport.",
    "My work builds robots {preposition} Modelport.",
    "Our work builds agents {preposition} Modelport.",
    "The robots I built are {preposition} Modelport.",
    "The software we built is {preposition} Modelport.",
    "Robots are {preposition} Modelport.",
    "Agents are {preposition} Modelport.",
]
LOCATIVE_PREPOSITIONS = ["in", "at", "near", "from", "out of"]


@pytest.mark.parametrize("frame", NOMINAL_LOCATIVE_FRAMES)
@pytest.mark.parametrize("preposition", LOCATIVE_PREPOSITIONS)
@pytest.mark.parametrize("entity", ["company", "project", "skill"])
def test_nominal_locatives_cannot_borrow_a_location_from_an_entity(
    frame: str, preposition: str, entity: str
) -> None:
    fields = {
        "company": {"participants": ("Modelport Robotics",)},
        "project": {"profile": ("I built the Modelport project for robots and agents.",)},
        "skill": {"known_terms": ("Modelport",)},
    }
    sources = replace(SOURCES, **fields[entity])
    verdict = judged(frame.format(preposition=preposition), "Describe a project", sources)
    assert verdict.answer is None, verdict


@pytest.mark.parametrize("frame", NOMINAL_LOCATIVE_FRAMES)
@pytest.mark.parametrize("preposition", LOCATIVE_PREPOSITIONS)
def test_exact_sourced_nominal_locatives_remain_untouched(frame: str, preposition: str) -> None:
    value = frame.format(preposition=preposition)
    sources = replace(SOURCES, profile=(value,), employers=("Modelport",))
    verdict = judged(value, "Describe a project", sources)
    assert verdict.answer is not None, verdict
    assert verdict.answer.value == value


@pytest.mark.parametrize(
    "value",
    [
        "Our work is in the Example simulation.",
        "My work was in the Example simulation.",
        "My work will be in the Example simulation.",
        "My work can be in the Example simulation.",
        "My work is not in the Example simulation.",
        "My work is near the Example simulation.",
        "My work builds robots in the Example simulation.",
        "My work is in the Example simulation and robots are near the Example simulation.",
        "The robots we built are in the Example simulation.",
        "The robots I built were in the Example simulation.",
        "The robots I built will be in the Example simulation.",
        "The robots I built can be in the Example simulation.",
        "The robots I built are not in the Example simulation.",
        "The robots I built are near the Example simulation.",
        "The robots I tested are in the Example simulation.",
        "The agents I built are in the Example simulation.",
    ],
)
def test_nominal_location_relations_retain_actor_tense_modality_negation_and_object(
    value: str,
) -> None:
    sources = replace(
        SOURCES,
        profile=(
            "My work is in the Example simulation.",
            "The robots I built are in the Example simulation.",
        ),
    )
    assert judged(value, "Describe a project", sources).answer is None


@pytest.mark.parametrize(
    "value",
    [
        "My work is in Lisbon.",
        "My work builds robots in Lisbon.",
        "The robots I built are in Lisbon.",
    ],
)
def test_review_nominal_location_regressions(value: str) -> None:
    sources = replace(
        agent.sources_of({}, "Engineer", "Lisbon Robotics"), profile=("I built robots.",)
    )
    assert judged(value, "Describe a project", sources).answer is None


@pytest.mark.parametrize("frame", NOMINAL_LOCATIVE_FRAMES)
@pytest.mark.parametrize("field", ["participants", "known_terms", "employers", "titles"])
def test_even_a_complete_entity_name_is_not_a_personal_locative_source(
    frame: str, field: str
) -> None:
    value = frame.format(preposition="in")
    sources = replace(SOURCES, **{field: (value,)})
    assert judged(value, "Describe a project", sources).answer is None


@pytest.mark.parametrize("preposition", LOCATIVE_PREPOSITIONS)
@pytest.mark.parametrize(
    ("source", "value"),
    [
        (
            "My work built robots {preposition} the Example simulation.",
            "My work developed robots {preposition} the Example simulation.",
        ),
        (
            "The robots I built are {preposition} the Example simulation.",
            "The robots I developed are {preposition} the Example simulation.",
        ),
        (
            "Our work tested agents {preposition} the Example simulation.",
            "Our work evaluated agents {preposition} the Example simulation.",
        ),
        (
            "The software we tested is {preposition} the Example simulation.",
            "The software we evaluated is {preposition} the Example simulation.",
        ),
    ],
)
def test_nominal_relations_allow_the_same_bounded_technical_paraphrases(
    source: str, value: str, preposition: str
) -> None:
    source = source.format(preposition=preposition)
    value = value.format(preposition=preposition)
    verdict = judged(value, "Describe a project", replace(SOURCES, profile=(source,)))
    assert verdict.answer is not None, verdict
    assert verdict.answer.value == value


FACTUAL_OBJECTS = ["robots", "agents", "software", "evaluation tools", "robotics tools", "AI tools"]


@pytest.mark.parametrize("object_", FACTUAL_OBJECTS)
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
    ],
)
def test_factual_provenance_preserves_actor_tense_negation_and_achievement(
    source: str, value: str, object_: str
) -> None:
    sources = replace(SOURCES, profile=(source.format(object_),))
    assert judged(value.format(object_), "Describe a project", sources).answer is None


@pytest.mark.parametrize("object_", FACTUAL_OBJECTS)
@pytest.mark.parametrize(
    ("source", "value"),
    [
        ("I built {}.", "I developed {}."),
        ("I built {}.", "I designed {}."),
        ("I built {}.", "I created {}."),
        ("We evaluated {}.", "We tested {}."),
    ],
)
def test_supported_factual_action_paraphrases_remain_untouched(
    source: str, value: str, object_: str
) -> None:
    value = value.format(object_)
    verdict = judged(
        value, "Describe a project", replace(SOURCES, profile=(source.format(object_),))
    )
    assert verdict.answer is not None, verdict
    assert verdict.answer.value == value
