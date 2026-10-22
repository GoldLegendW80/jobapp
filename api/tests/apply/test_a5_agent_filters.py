"""What the answer agent may answer, over generated families (issue #317, after the review of PR
#323): the practical and leaving labels in any wording, the employer words that put a label on
topic only in a question of motivation, "why <name>" checked against the posting's company, the
typed statements of a practicality, and labels, options and values read through `_plain`
(compatibility forms, invisible characters and letters drawn like Latin ones). Pure: no store."""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import replace

import pytest

from app.apply import agent, run
from app.apply.ats import base as ats_base
from app.jobs.gazetteer import GAZETTEER_PATH
from tests.apply.test_a5_guards import LEAKS, LEAVING_AND_PRACTICAL, OFF_TOPIC, OPEN_LEAKS

SOURCES = agent.sources_of({}, "Engineer", "Example Robotics")

# The ten labels of the review of PR #323 (comment of 2026-10-15), six of them asked before.
REVIEW_LABELS = [
    "How soon could you join?",
    "What is your preferred work location?",
    "Which office would you join?",
    "Which time zone do you work in?",
    "What is your ideal working arrangement?",
    "Why Berlin?",
    "Why do you want to join our London office?",
    "Why do you want to exit your last role?",
    "Why are you looking for a new role?",
    "Tell us about your experience as a parent returning to work",
]


def _product(*parts: list[str]) -> list[str]:
    return ["".join(words) for words in itertools.product(*parts)]


TIME = [
    *_product(
        ["How soon ", "How quickly ", "How fast ", "When "],
        ["can you ", "could you ", "would you be able to ", "will you "],
        [
            "join",
            "start",
            "begin",
            "take up the role",
            "come on board",
            "join the team",
            "join us",
            "start in this role",
            "join Example Robotics",
            "be available to start",
        ],
        ["?", "", " if we make you an offer?"],
    ),
    "What is your earliest start date in this role?",
    "By when could you join the team?",
    "What date could you join us?",
    "How much notice do you need to give your company?",
    "Quand pouvez-vous nous rejoindre ?",
    "À partir de quand pouvez-vous rejoindre l'équipe ?",
    "Wann können Sie bei uns anfangen?",
    "Ab wann können Sie die Stelle antreten?",
    "¿Cuándo puede unirse al equipo?",
    "¿Desde cuándo puede empezar en la empresa?",
]
PLACE = [
    frame.format(noun)
    for frame, noun in itertools.product(
        [
            "Which {} would you join?",
            "Which {} would you prefer?",
            "What {} do you work from?",
            "Which {} suits you best?",
            "What is your preferred {}?",
            "Tell us about your preferred {}",
            "Why do you want to join our {}?",
            "Describe the {} you would work in",
            "What {} are you in?",
        ],
        [
            "office",
            "location",
            "work location",
            "city",
            "country",
            "time zone",
            "timezone",
            "hub",
            "campus",
            "headquarters",
            "region",
        ],
    )
] + [
    "Quel bureau préférez-vous rejoindre ?",
    "Pourquoi voulez-vous rejoindre notre bureau de Lyon ?",
    "Welcher Standort passt zu Ihnen?",
    "¿En qué oficina quiere trabajar?",
]
ARRANGEMENT = [
    frame.format(noun)
    for frame, noun in itertools.product(
        [
            "What is your ideal {}?",
            "Describe your preferred {}",
            "Which {} would you like in this role?",
            "Tell us about the {} you want in our team",
            "Why do you want this {}?",
        ],
        [
            "working arrangement",
            "work arrangement",
            "work schedule",
            "working hours",
            "work pattern",
            "working week",
            "hybrid setup",
            "remote setup",
            "number of days in the office",
            "part-time schedule",
            "full-time arrangement",
            "weekend availability",
        ],
    )
]
LEAVING = (
    [
        frame.format(verb, own)
        for frame, verb, own in itertools.product(
            [
                "Why do you want to {} {}?",
                "Why did you {} {}?",
                "Why would you {} {}?",
                "What made you {} {}?",
                "Tell us why you want to {} {}",
                "Explain why you {} {}",
            ],
            ["leave", "exit", "quit", "move on from", "step away from", "walk away from", "change"],
            [
                "your last role",
                "your current role",
                "your previous company",
                "your current employer",
                "your last team",
                "your current job",
                "your present position",
            ],
        )
    ]
    + [
        frame.format(seek, new)
        for frame, seek, new in itertools.product(
            ["Why are you {} {}?", "Why are you currently {} {}?", "What has you {} {}?"],
            ["looking for", "searching for", "seeking", "hunting for", "applying for"],
            [
                "a new role",
                "a new job",
                "another position",
                "a new opportunity",
                "a new challenge",
                "a different company",
                "your next role",
                "new opportunities",
            ],
        )
    ]
    + [
        frame.format(away)
        for frame, away in itertools.product(
            ["Tell us about {}", "Describe {}", "Explain {}", "Why {}?"],
            [
                "returning to work",
                "your return to work",
                "your career break",
                "your sabbatical",
                "your time off",
                "going back to work after a break",
                "being a parent returning to work",
            ],
        )
    ]
    + [
        "What attracts you away from your current company?",
        "Pourquoi voulez-vous quitter votre poste actuel ?",
        "Pourquoi souhaitez-vous changer d'entreprise ?",
        "Warum möchten Sie Ihre aktuelle Stelle verlassen?",
        "¿Por qué quiere dejar su empresa actual?",
    ]
)


def _gazetteer_names() -> list[str]:
    places = json.loads(GAZETTEER_PATH.read_text(encoding="utf-8"))
    aliases = {alias for kind in places.values() for entry in kind for alias in entry["aliases"]}
    # "us" is the pronoun in "Why us?", asked below
    return sorted(alias.title() for alias in aliases - {"us"})


WHY_PLACE = [
    frame.format(name)
    for frame, name in itertools.product(
        ["Why {}?", "Why do you want to work in {}?", "Why do you want to join us in {}?"],
        _gazetteer_names(),
    )
] + ["Why the US?", "Why the UK?", "Why now?", "Why the move?", "Why the change?"]

FAMILIES = {
    "review": REVIEW_LABELS,
    "time": TIME,
    "place": PLACE,
    "arrangement": ARRANGEMENT,
    "leaving": LEAVING,
    "why_place": WHY_PLACE,
}

# What must stay asked, on either route: questions of motivation and of experience.
ASKED = (
    [
        frame.format(verb, employer)
        for frame, verb, employer in itertools.product(
            ["Why do you want to {} {}?", "Tell us why you want to {} {}"],
            ["join", "work for", "work with"],
            ["us", "Example Robotics", "our team", "this team", "the company"],
        )
    ]
    + [
        frame.format(employer)
        for frame, employer in itertools.product(
            [
                "What excites you about {}?",
                "What attracts you to {}?",
                "Why are you interested in {}?",
                "What would you bring to {}?",
                "What makes you a great fit for {}?",
                "What made you apply to {}?",
            ],
            [
                "this role",
                "the team",
                "Example Robotics",
                "our mission",
                "this position",
                "the robotics industry",
            ],
        )
    ]
    + [
        frame.format(subject)
        for frame, subject in itertools.product(
            ["Tell us about {}", "Describe {}", "Share {}", "Walk us through {}"],
            [
                "a project you are proud of",
                "a system you designed",
                "a hard bug you fixed",
                "your experience with Kubernetes",
                "a time you led a team",
                "your biggest achievement",
                "a trade-off you made",
                "how you test agents",
                "a time you disagreed with your team",
                "your current role and responsibilities",
            ],
        )
    ]
    + [
        "Why Example Robotics?",
        "Why us?",
        "Why this role?",
        "Why here?",
        "What are you looking for in your next role?",
        "What do you look for in a new team?",
        "Pourquoi souhaitez-vous rejoindre Example Robotics ?",
        "Qu'est-ce qui vous motive dans ce poste ?",
        "Warum möchten Sie bei uns arbeiten?",
        "¿Por qué quieres trabajar aquí?",
    ]
)


def _outcome(label: str, reason: str) -> ats_base.Outcome:
    item = ats_base.Field(key="k", label=label, kind="text")
    return ats_base.Outcome(item, run.Left(reason, ()), ats_base.Filled("left", reason))


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_every_practical_and_leaving_label_of_a_family_stays_the_persons(family: str) -> None:
    asked = [
        (label, reason)
        for label in FAMILIES[family]
        for reason in agent.AGENT_REASONS
        if label != "Why now?"
        and (
            agent.refusal(label, "text", (), reason) is None
            or agent.eligible([_outcome(label, reason)])
        )
    ]
    assert asked == []
    if "Why now?" in FAMILIES[family]:
        for reason in agent.AGENT_REASONS:
            assert agent.refusal("Why now?", "text", (), reason) is None
            assert agent.eligible([_outcome("Why now?", reason)])


def test_the_families_are_generated_wide() -> None:
    sizes = {name: len(set(labels)) for name, labels in FAMILIES.items()}
    assert sizes == {
        "review": 10,
        "time": 490,
        "place": 103,
        "arrangement": 60,
        "leaving": 447,
        "why_place": sizes["why_place"],
    }
    assert sizes["why_place"] > 400


def test_motivation_and_experience_questions_stay_asked_on_both_routes() -> None:
    refused = [
        (label, reason, agent.refusal(label, "text", (), reason))
        for label in ASKED
        for reason in agent.AGENT_REASONS
        if agent.refusal(label, "text", (), reason) is not None
    ]
    assert refused == []
    assert len(set(ASKED)) == 116


@pytest.mark.parametrize(
    "label",
    ["How soon could you join?", "Which office would you join?"],
)
def test_an_employer_word_alone_puts_no_label_on_topic(label: str) -> None:
    """Joining, the team, the role: on topic only in a question of motivation."""
    for reason in agent.AGENT_REASONS:
        assert agent.refusal(label, "text", (), reason) is not None
    assert agent.refusal("Why do you want to join the team?", "text") is None


@pytest.mark.parametrize(
    ("label", "code"),
    [
        ("Why do you want to join our Berlin team?", "practical"),
        ("What excites you about our New York team?", "practical"),
        ("Why do you want to join Example Robotics in London?", "practical"),
        # only a capitalised word is read as a place: "nice" and "us" in a sentence are none
        ("Why would it be nice to join us?", None),
        ("Why Do You Want To Join Us?", None),
        ("Tell us about a project you led in Paris", None),  # experience, not a motivation
        ("Why do you want to join our US team?", "practical"),
    ],
)
def test_a_capitalised_place_in_a_question_of_motivation_is_practical(
    label: str, code: str | None
) -> None:
    for reason in agent.AGENT_REASONS:
        assert agent.refusal(label, "text", (), reason) == code


# --- "why <name>": the posting's company, checked at the judge --------------------------------


@pytest.mark.parametrize(
    ("label", "company", "code"),
    [
        ("Why Acme Robotics?", "Acme Robotics", "accepted"),
        ("Why Acme?", "Acme Robotics SAS", "accepted"),
        ("Why AcmeRobotics?", "Acme Robotics", "accepted"),
        ("WHY ACME ROBOTICS", "Acme Robotics", "accepted"),
        ("Why Асmе Robotics?", "Acme Robotics", "accepted"),  # Cyrillic letters
        # a city outside the gazetteer reaches the model; its answer is never typed
        ("Why Lisbon?", "Acme Robotics", "off_topic"),
        ("Why Porto Alegre?", "Acme Robotics", "off_topic"),
        ("Why Acme Robotics?", "", "off_topic"),
        ("Why Globex?", "Acme Robotics", "off_topic"),
        # on topic by its words, not by the name ("example" is a subject word)
        ("Why us?", "Acme Robotics", "accepted"),
        ("Why this role?", "Acme Robotics", "accepted"),
        ("Why Example Robotics?", "Acme Robotics", "accepted"),
    ],
)
def test_a_why_name_answer_is_kept_only_for_the_postings_company(
    label: str, company: str, code: str
) -> None:
    sources = agent.sources_of({}, "Engineer", company)
    asked = agent.Question("k", label, "text", reason="free_text_for_the_agent")
    value = "The robots you build are what I want to make."
    sources = replace(sources, profile=(value,))
    reply = agent.Reply((agent.Proposal("k", value, "facts"),))
    (verdict,) = agent.judge([asked], reply, sources)
    assert verdict.code == code


# --- the value side: a practicality the model writes is never typed ---------------------------

PRACTICAL_VALUES = [
    *_product(
        ["I can ", "I could ", "I would be able to ", "I am ready to ", "I'm happy to "],
        ["start", "join", "begin", "join your team", "start in this role"],
        [
            " immediately.",
            " right away.",
            " in two weeks.",
            " within a month.",
            " from 1 March.",
            " next month.",
            " as soon as you need.",
            " after my notice.",
        ],
    ),
    "I am available from March.",
    "Available immediately, and keen to build agents.",
    "I'm available to start in two weeks.",
    "Immediately available.",
    "My notice period is three months.",
    "I have a one month notice to give.",
    "I am based in Berlin and love robots.",
    "I'm currently living in Lisbon.",
    "I live in Lyon, close to your office.",
    "I've lived in Paris for years.",
    "I would relocate to Berlin for this role.",
    "Relocation is not a problem for me.",
    "I plan to move to Lisbon next year.",
    "I'm open to moving abroad.",
    "I prefer to work remotely.",
    "I would like to work fully remote.",
    "I want a hybrid setup.",
    "I can be on site three days a week.",
    "A remote role suits me best.",
    "I can travel up to 50%.",
    "I'm happy to travel.",
    "Travel is fine, up to 30%.",
    "My time zone is CET.",
    "I work in the same time zone as your team.",
    "I have another offer.",
    "I am interviewing with two other companies.",
    "I have several interviews in progress.",
    "I'm in two parallel processes.",
    "I am looking for a part-time role.",
    "Je peux commencer immédiatement.",
    "Je suis disponible dès maintenant.",
    "Je pourrais rejoindre l'équipe sous un mois.",
    "J'habite à Paris.",
    "Je préfère le télétravail.",
    "Ich kann sofort anfangen.",
    "Ab sofort verfügbar.",
    "Ich bevorzuge Homeoffice.",
    "Puedo empezar de inmediato.",
    "Puedo incorporarme en dos semanas.",
]
# A step of a career, a motivation, a technical word: never a practicality.
TRUE_VALUES = [
    "I joined Example Labs in 2019 and built its agent platform.",
    "I started my career in robotics in 2015.",
    "I started building agents in March 2021.",
    "I led a remote monitoring project for warehouse robots.",
    "I built a hybrid search engine over two million documents.",
    "I want to join Example Robotics because its robots help hospitals.",
    "I would love to join your team and bring my evaluation work.",
    "I could join a team that ships agents to real operations.",
    "We moved our pipeline to Kubernetes and cut costs.",
    "I noticed a race condition in the scheduler and fixed it.",
    "I enjoy working with distributed teams across Europe.",
    "Your mission to make robots safe in hospitals is why I applied.",
    "I designed an on-call rotation and a travel booking feature.",
    "My work was based on a strong evaluation culture.",
    "I can explain my approach to testing agents in detail.",
    "I started a reading group on reinforcement learning.",
    "I live for hard debugging problems.",
    "I am interested in remote sensing and robotics.",
    "Agents that work in production matter to me.",
    "I was available to the team as a mentor.",
    "I fixed a time zone bug in our scheduler.",
    "Je travaille sur des agents depuis cinq ans.",
    "Ich habe Agenten für Lagerroboter gebaut.",
]


def _verdict(value: str) -> agent.Verdict:
    asked = agent.Question("k", "Why do you want to join us?", "text")
    sources = replace(SOURCES, profile=tuple(TRUE_VALUES))
    (verdict,) = agent.judge([asked], agent.Reply((agent.Proposal("k", value, "facts"),)), sources)
    return verdict


def test_a_typed_practicality_is_never_typed() -> None:
    kept = [(value, _verdict(value).code) for value in PRACTICAL_VALUES]
    assert [(value, code) for value, code in kept if code != "practical_statement"] == []
    assert len(set(PRACTICAL_VALUES)) == 239


def test_a_step_of_a_career_is_not_a_practicality() -> None:
    read = [v for v in TRUE_VALUES if agent._PRACTICAL_STATEMENT.search(agent._plain(v))]
    assert read == []
    assert _verdict("I would love to join your team and bring my evaluation work.").code == (
        "accepted"
    )


# --- lookalikes: labels, options and values read through `_plain` -----------------------------

# Letters drawn like Latin ones, as the never apply rules read them (guardrails.py `_LOOKALIKES`).
CYRILLIC = str.maketrans("aceopxyiAEOPCKMHT", "асеорхуіАЕОРСКМНТ")
GREEK = str.maketrans("abeziknoptyxABEZIKMNOPTXY", "αβεζικνορτυχΑΒΕΖΙΚΜΝΟΡΤΧΥ")
SMALL_CAPS = str.maketrans("abcdefghijklmnoprstuvwyz", "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘʀꜱᴛᴜᴠᴡʏᴢ")


def _disguises(word: str) -> list[str]:
    """One word written seven ways a reader still reads as the word."""
    middle = len(word) // 2
    return [
        word.translate(CYRILLIC),
        word.translate(GREEK),
        word.translate(SMALL_CAPS),
        "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in word),  # fullwidth
        word[:middle] + "​" + word[middle:],  # zero width space
        word[:middle] + "­" + word[middle:],  # soft hyphen
        "".join(c + "⃒" for c in word),  # a combining overlay outside U+0300 to U+036F
    ]


# A label asked before because only its refused word was disguised: a job word anchors it.
DISGUISED_LABELS = [
    ("What is your desired {} for this role?", "salary", "pay"),
    ("What {} do you hold for this role?", "visa", "authorization"),
    ("Tell us about any {} in your past roles", "convictions", "history"),
    ("Why do you want to {} your last role?", "leave", "history"),
    ("Why do you want to join our {} team?", "Berlin", "practical"),
    ("How {} could you join this team?", "soon", "practical"),
    ("Describe your {} for this role", "availability", "practical"),
    ("Describe the {} you would want in this role", "arrangement", "practical"),
    ("What {} would you join?", "office", "practical"),
    ("Tell us about your {} in this role", "religion", "personal"),
    ("Explain why your {} matters for this role", "password", "identity"),
]


@pytest.mark.parametrize(("frame", "word", "code"), DISGUISED_LABELS)
def test_a_disguised_word_of_a_label_is_read_as_itself(frame: str, word: str, code: str) -> None:
    assert agent.refusal(frame.format(word), "text") == code
    for disguised in _disguises(word):
        for reason in agent.AGENT_REASONS:
            assert agent.refusal(frame.format(disguised), "text", (), reason) == code, disguised


DISGUISED_VALUES = [
    ("I hold a work {} for the EU.", "permit", "legal_statement"),
    ("I have no criminal {}.", "convictions", "legal_statement"),
    ("My {} expectation is 90k.", "salary", "legal_statement"),
    ("As a {}, I bring a different view.", "woman", "personal_statement"),
    ("I can start {}.", "immediately", "practical_statement"),
    ("I am {} in Berlin.", "based", "practical_statement"),
    ("I would {} to Lisbon.", "relocate", "practical_statement"),
]


@pytest.mark.parametrize(("frame", "word", "code"), DISGUISED_VALUES)
def test_a_disguised_word_of_a_value_is_read_as_itself(frame: str, word: str, code: str) -> None:
    assert _verdict(frame.format(word)).code == code
    for disguised in _disguises(word):
        assert _verdict(frame.format(disguised)).code == code, disguised


def test_an_option_is_read_through_plain() -> None:
    options = ("Сitizen", "Реrmanent resident", "Other")  # Cyrillic C, P and e
    assert agent.refusal("Which describes you?", "select", options) == "option_topic"
    assert agent.refusal("Which team interests you?", "select", ("Yеs", "Nо")) == "yes_or_no"


def test_a_label_in_another_script_stays_the_persons() -> None:
    """A whole label in Cyrillic or Greek is read with its lookalike letters as Latin ones: no
    vocabulary reads it, so it is refused, never asked."""
    assert agent.refusal("Почему вы хотите работать у нас?", "text") == "not_open"
    assert agent.refusal("Γιατί θέλετε να δουλέψετε εδώ;", "text") == "not_open"


# --- the opener rule, written verbose ---------------------------------------------------------


def test_the_opener_rule_is_verbose_and_reads_the_tables_as_before() -> None:
    assert agent._OPEN.flags & re.VERBOSE
    corpus = [label for label, _ in LEAKS + OPEN_LEAKS + LEAVING_AND_PRACTICAL] + OFF_TOPIC
    opened = {label for label in corpus if agent._OPEN.search(agent._plain(label))}
    assert len(opened) == 170
    assert "Why Example Robotics?" not in corpus
    assert agent._OPEN.search("  * 1. please, briefly: why example robotics?")
    assert agent._OPEN.search("in a few words, tell us about a project")
    assert agent._OPEN.search("motivation:")
    assert not agent._OPEN.search("favourite colour")
    assert not agent._OPEN.search("was motiviert sie?")
