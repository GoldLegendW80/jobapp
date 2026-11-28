"""The copied runs (D32, issue #862): the rule of `copied_runs` as accept and refuse tables, its
place in `checks.run`, its correction, and the length note of a letter's body.

Every posting, profile and letter here is invented ("Example Robotics", "Alex Example")."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.documents import checks, copied_runs, corrections, rules, submit, writer_letter
from app.documents import content as c
from app.documents.settings import DocumentSettings
from app.documents.sources import PostingView, sources_for
from tests.documents.conftest import base_cv, view_of, written

TITLE = "Senior Data Platform Engineer"
COMPANY = "Example Robotics"
POSTING = (
    "Example Robotics is hiring a Senior Data Platform Engineer.\n"
    "You will design and build reliable data pipelines with Python and Airflow for our fleet "
    "of delivery robots.\n"
    "- Work closely with product managers to translate business needs into data solutions.\n"
    "We value clear writing, ownership and a calm approach to incidents.\n"
    "Certifications such as AWS Certified Solutions Architect Associate are a plus."
)
POSTING_FR = (
    "Vous concevrez des pipelines de données fiables avec Python et Airflow pour notre flotte "
    "de robots de livraison."
)
SKILLS = ("Python", "Airflow", "Kubernetes", "incident response")


def found(text: str, posting: str = POSTING, terms: tuple[str, ...] = SKILLS) -> list[str]:
    against = copied_runs.posting(posting, (TITLE, COMPANY, *terms))
    return [text[start:end] for start, end in copied_runs.runs(text, against)]


# (case, field text, the runs found). Generated from the rule of the module docstring: a run is
# 5 counted words or more; the title, the company, the person's skills and short quotes count 0.
TABLE = [
    ("a run of 4", "I translate business needs into reports.", []),
    (
        "a run of 5",
        "Each week I translate business needs into data for the team.",
        ["translate business needs into data"],
    ),
    (
        "a run of 6, the whole run",
        "I translate business needs into data solutions.",
        ["translate business needs into data solutions"],
    ),
    (
        "across a line break",
        "I will design and build\nreliable data pipelines.",
        ["will design and build\nreliable data pipelines"],
    ),
    (
        "with other punctuation",
        "Design, and build: reliable data-pipelines!",
        ["Design, and build: reliable data-pipelines"],
    ),
    (
        "in capitals, with accents",
        "WE VALUE CLEAR WRITING, OWNERSHIP AND TEA.",
        ["WE VALUE CLEAR WRITING, OWNERSHIP AND"],
    ),
    (
        "the company and the title aside",
        "Example Robotics is hiring a Senior Data Platform Engineer, and I applied.",
        [],
    ),
    (
        "a skill term aside",
        "I chose Python and Airflow for our fleet.",
        [],
    ),
    (
        "skill terms do not break the sentence around them",
        "I build reliable data pipelines with Python and Airflow every day.",
        ["build reliable data pipelines with Python and Airflow"],
    ),
    (
        "a quoted name of a certification aside",
        'I hold the "AWS Certified Solutions Architect Associate" certification.',
        [],
    ),
    (
        "the same name unquoted counts",
        "I hold the AWS Certified Solutions Architect Associate certification.",
        ["AWS Certified Solutions Architect Associate"],
    ),
    (
        "a quote of more than 8 words is text",
        'I read "work closely with product managers to translate business needs" twice.',
        ["work closely with product managers to translate business needs"],
    ),
    (
        "an unclosed quote is text",
        'I read "work closely with product managers to translate them.',
        ["work closely with product managers to translate"],
    ),
    (
        "two runs in one field",
        "We value clear writing, ownership and care. I translate business needs into data.",
        ["We value clear writing, ownership and", "translate business needs into data"],
    ),
    ("a word inside a longer one", "I design and build reliable metadata pipelines.", []),
    (
        "a stock phrase of the posting still counts",
        "I work closely with product managers.",
        ["work closely with product managers"],
    ),
    ("the posting's words in another order", "Pipelines of reliable data I build and design.", []),
    ("an empty field", "", []),
]


@pytest.mark.parametrize(
    ("text", "runs"), [case[1:] for case in TABLE], ids=[case[0] for case in TABLE]
)
def test_the_copied_run_rule(text: str, runs: list[str]) -> None:
    assert found(text) == runs


def test_a_term_of_the_posting_that_is_not_the_persons_counts() -> None:
    text = "I chose Python and Airflow for our fleet."
    assert found(text, terms=()) == ["Python and Airflow for our fleet"]


@pytest.mark.parametrize(
    ("text", "posting", "runs"),
    [
        ("I design reliable data pipelines with Python for robots.", POSTING_FR, []),
        (
            "Je conçois des pipelines de donnees fiables avec Python.",
            POSTING_FR,
            ["des pipelines de donnees fiables avec Python"],
        ),
        ("Vous concevrez des pipelines robustes.", POSTING_FR, []),
    ],
    ids=["an English letter, a French posting", "French, accents folded", "French, a run of 4"],
)
def test_a_posting_in_another_language(text: str, posting: str, runs: list[str]) -> None:
    assert found(text, posting) == runs


# Sentences in the person's own words, about the same posting: none of them is a copied run.
OWN_WORDS = [
    "I run the data platform of a delivery company and keep its pipelines healthy.",
    "At Example Corp I moved our nightly loads from cron scripts to Airflow.",
    "My part was the scheduler, the alerting and the runbooks.",
    "I write short design notes before I build anything that others will run.",
    "When a load fails at night, I fix the cause the next morning and write down why.",
    "Product managers ask me what the data can answer, and I say plainly what it cannot.",
    "I would like to bring that care to Example Robotics as its Senior Data Platform Engineer.",
    "I am based in Lyon and can start in one month.",
    "Kubernetes and incident response are part of my weekly work.",
    "I hold no AWS certification yet, and I say so.",
    "Our fleet of sensors sends data every second.",
    "Clear writing matters to me as much as clean code.",
    "I designed the pipelines; a colleague built the dashboards.",
    "Data solutions only matter when people use them.",
    "I have about 3.5 years of professional engineering.",
    "I translate needs into small, tested changes.",
    "Reliable pipelines start with honest monitoring.",
    "I would be glad to talk about the role.",
    "Robots, sensors and maps: I have worked with all three.",
    "Python is the language I use every day, with SQL beside it.",
]


@pytest.mark.parametrize("text", OWN_WORDS)
def test_sentences_in_the_persons_words_stay_untouched(text: str) -> None:
    assert found(text) == []


def test_a_posting_with_no_words_finds_nothing() -> None:
    assert copied_runs.posting("  \n- ...", SKILLS) is None
    assert copied_runs.runs("I translate business needs into data.", None) == []


def test_the_posting_is_read_up_to_its_cap() -> None:
    filler = "lorem " * (copied_runs.POSTING_CHARS_MAX // 6)
    late = filler + "translate business needs into data solutions"
    assert found("I translate business needs into data solutions.", late) == []
    early = "translate business needs into data solutions " + filler
    assert found("I translate business needs into data solutions.", early) != []


def test_a_hostile_posting_and_field_stay_linear() -> None:
    posting = "w " * (copied_runs.POSTING_CHARS_MAX // 2)
    against = copied_runs.posting(posting, ("w w",) * 50)
    assert against is not None
    assert len(against.automaton.lengths) <= 2 * (copied_runs.POSTING_CHARS_MAX // 2) + 1
    field = "w " * 1000
    assert copied_runs.runs(field, copied_runs.posting(posting, ())) == [(0, len(field) - 1)]
    quotes = '"w" ' * 500
    assert copied_runs.runs(quotes, against) == []
    small = "to " * 1000
    assert copied_runs.runs(small, copied_runs.posting("to " * 30_000, ())) == []
    phrases = "in order to " * 1000
    assert copied_runs.runs(phrases, copied_runs.posting(phrases * 9, ())) == []


# A posting full of small words, in English, French and German.
SMALL_POSTING = (
    "In order to be able to ship every week, you will be part of a team that is able to work "
    "as well as it can. We are looking for a person who will be one of the first.\n"
    "Track each order to the warehouse.\n"
    "Afin de pouvoir livrer chaque semaine, vous rejoindrez l'équipe qui est en mesure de le "
    "faire.\n"
    "Sie sind in der Lage, mit uns an der Plattform zu arbeiten."
)

# (case, field text, the runs found). A run of 5 counted words or more needs 2 of them that are
# not small words (SMALL_WORDS, or a word of a SMALL_PHRASES phrase where the phrase stands).
SMALL_TABLE = [
    ("only small words", "I write tests in order to be able to ship.", []),
    ("only small words, in capitals and with commas", "IN ORDER TO, BE ABLE TO.", []),
    ("one word that is not small", "We are looking for a new role.", []),
    ("small words and one word that is not", "I will be one of the first to say so.", []),
    ("two phrases and one word", "I am able to work as well as it can, I think.", []),
    ("you will be part of a, one word", "As you will be part of a group, I say so.", []),
    ("French, one word", "Celle qui est en mesure de le faire, c'est moi.", []),
    ("French, an elision", "Je suis l'équipe qui est en mesure de.", []),
    (
        "a phrase cut short is no phrase",
        "Je suis l'équipe qui est en mesure.",
        ["l'équipe qui est en mesure"],
    ),
    ("German, no word", "Wir sind in der Lage, mit uns an der Sache.", []),
    (
        "two words that are not small",
        "I work in order to be able to ship every week.",
        ["in order to be able to ship every week"],
    ),
    (
        "part and team",
        "This time you will be part of a team that ships.",
        ["you will be part of a team that"],
    ),
    (
        "order outside its phrase counts",
        "I track each order to the warehouse.",
        ["track each order to the warehouse"],
    ),
    ("able outside its phrase is no phrase", "Able to work as well as it can.", []),
    (
        "French, two words",
        "Je rejoindrez l'équipe qui est en mesure de le faire.",
        ["rejoindrez l'équipe qui est en mesure de le faire"],
    ),
    (
        "German, two words",
        "Ich will mit uns an der Plattform zu arbeiten.",
        ["mit uns an der Plattform zu arbeiten"],
    ),
    (
        "a team that is able to work",
        "I lead a team that is able to work alone.",
        ["a team that is able to work"],
    ),
]


@pytest.mark.parametrize(
    ("text", "runs"), [case[1:] for case in SMALL_TABLE], ids=[case[0] for case in SMALL_TABLE]
)
def test_a_run_needs_two_words_that_are_not_small(text: str, runs: list[str]) -> None:
    assert found(text, SMALL_POSTING) == runs


def test_the_small_words_are_single_folded_words() -> None:
    for word in copied_runs.SMALL_WORDS:
        assert [w.folded for w in copied_runs.words(word)] == [word]
    for phrase in copied_runs.SMALL_PHRASES:
        assert len(copied_runs.words(phrase)) >= 2
    # Words of the postings' content are never small.
    for word in ("order", "able", "team", "data", "work", "ship", "week", "plattform", "faire"):
        assert word not in copied_runs.SMALL_WORDS


# --- in the checks of a document -------------------------------------------------------------

OFF = DocumentSettings()


def _sources(kind: c.Kind, text: str = POSTING) -> Any:
    base = base_cv("alex")
    seen = PostingView(company=COMPANY, title=TITLE, text=text)
    return sources_for(view_of("alex", base), base, None, seen, kind=kind)


def _letter(paragraphs: list[str]) -> dict[str, Any]:
    data = copy.deepcopy(written("letter_typical"))
    data["recipient"] = "Hiring team, Example Robotics"
    data["paragraphs"] = paragraphs
    return data


COPIED = "Every day I translate business needs into data solutions for the team."


def _copied(result: checks.Checks) -> list[rules.Issue]:
    return [issue for issue in result.claims if issue.code == rules.COPIED_RUN]


def test_a_letter_holds_a_copied_run_as_a_claim_for_the_rewrite() -> None:
    result = checks.run("letter", _letter([COPIED]), _sources("letter"), OFF)
    copied = [i for i in result.claims if i.code == rules.COPIED_RUN]
    assert [(i.field, i.rewrite) for i in copied] == [("paragraphs.0", True)]
    assert checks.excerpt(result.content, copied[0]) == (  # type: ignore[arg-type]
        "translate business needs into data solutions"
    )
    assert rules.policy(rules.COPIED_RUN) == rules.NEEDS_REWRITE


def test_the_recipient_and_a_letter_without_posting_text_are_not_read() -> None:
    data = _letter(["I run data pipelines."])
    data["recipient"] = "Work closely with product managers to translate business needs"
    assert not _copied(checks.run("letter", data, _sources("letter"), OFF))
    bare = sources_for(view_of("alex"), None, None, PostingView(COMPANY, TITLE), kind="letter")
    assert not _copied(checks.run("letter", _letter([COPIED]), bare, OFF))


def test_a_cv_is_read_in_its_free_sentences_only() -> None:
    data = copy.deepcopy(written("cv_short"))
    data["summary"] = "I translate business needs into data solutions."
    data["competencies"] = ["design and build reliable data pipelines"]
    result = checks.run("cv", data, _sources("cv"), OFF)
    assert [i.field for i in result.claims if i.code == rules.COPIED_RUN] == ["summary"]


def test_a_base_cv_and_a_proposal_have_no_posting_text() -> None:
    sources = _sources("proposal")
    assert sources.posting_text == ()
    assert checks.copied_fields("base", [("summary", COPIED)]) == []


def test_a_copied_run_left_after_the_rewrite_is_kept_and_shown_with_its_sentence() -> None:
    sources = _sources("letter")
    result = checks.run("letter", _letter([COPIED, "I am based in Lyon."]), sources, OFF)
    assert result.content is not None
    content, records = corrections.remove_remaining(result.content, result, corrections.Fallbacks())
    assert content.paragraphs == result.content.paragraphs  # type: ignore[union-attr]
    assert [
        (r.field_path, r.before, r.after, r.reason) for r in records if r.code == rules.COPIED_RUN
    ] == [("paragraphs.0", COPIED, COPIED, corrections.COPIED_KEPT)]


def test_the_writers_rewrite_of_a_copied_run_is_recorded() -> None:
    sources = _sources("letter")
    first = checks.run("letter", _letter([COPIED]), sources, OFF)
    rewritten = c.parse("letter", _letter(["Each day I turn what product teams ask into data."]))
    assert first.content is not None
    records = corrections.diff_corrections(first.content, rewritten, first.claims)  # type: ignore[arg-type]
    assert [(r.by, r.reason) for r in records if r.field_path == "paragraphs.0"] == [
        ("writer", corrections.COPIED)
    ]
    assert corrections.COPIED in corrections.REASONS
    assert corrections.COPIED_KEPT in corrections.REASONS


def test_the_writer_is_asked_to_say_a_copied_run_again_never_to_remove_it() -> None:
    sources = _sources("letter")
    result = checks.run("letter", _letter([COPIED]), sources, OFF)
    assert result.content is not None
    answer = submit._correction_answer(result.content, result)
    line = next(line for line in answer.splitlines() if line.startswith("- paragraphs.0"))
    assert line.endswith(submit.COPIED_HOW)
    assert "remove" not in line
    assert "copy the posting" in answer


# --- the length of a letter's body -------------------------------------------------------------


def _words(count: int) -> list[str]:
    half = count // 2
    return [" ".join(["word"] * half), " ".join(["word"] * (count - half))]


@pytest.mark.parametrize(
    ("count", "codes"),
    [
        (249, [rules.LETTER_UNDER_LENGTH]),
        (250, []),
        (350, []),
        (420, []),
        (421, [rules.LETTER_OVER_LENGTH]),
        (590, [rules.LETTER_OVER_LENGTH]),
    ],
)
def test_the_length_note_of_a_letters_body(count: int, codes: list[str]) -> None:
    result = checks.run("letter", _letter(_words(count)), _sources("letter", ""), OFF)
    notes = [i.code for i in result.notes if i.field == "paragraphs"]
    assert notes == codes
    assert not result.invalid


# (the person's length, the body's words, the note): the bounds are 5/7 and 6/5 of the length.
LENGTHS = [
    (150, 106, [rules.LETTER_UNDER_LENGTH]),
    (150, 107, []),
    (150, 180, []),
    (150, 181, [rules.LETTER_OVER_LENGTH]),
    (500, 356, [rules.LETTER_UNDER_LENGTH]),
    (500, 357, []),
    (500, 421, []),
    (500, 600, []),
    (500, 601, [rules.LETTER_OVER_LENGTH]),
    (600, 428, [rules.LETTER_UNDER_LENGTH]),
    (600, 429, []),
    (600, 720, []),
]


@pytest.mark.parametrize(("target", "count", "codes"), LENGTHS)
def test_the_length_note_follows_the_persons_length(
    target: int, count: int, codes: list[str]
) -> None:
    mine = DocumentSettings(letter_words=target)
    paragraphs = [" ".join(["word"] * (count // 5 + (i < count % 5))) for i in range(5)]
    result = checks.run("letter", _letter(paragraphs), _sources("letter", ""), mine)
    assert rules.letter_words(result.content) == count  # type: ignore[arg-type]
    assert [i.code for i in result.notes if i.field == "paragraphs"] == codes
    assert (
        rules.letter_bounds(target) == {150: (107, 180), 500: (357, 600), 600: (429, 720)}[target]
    )


def test_the_length_note_counts_every_paragraph_and_skips_an_empty_letter() -> None:
    content = c.parse("letter", _letter(["one two three"] * 4))
    assert rules.letter_words(content) == 12  # type: ignore[arg-type]
    assert rules.letter_bounds(c.LETTER_WORDS) == (250, 420)
    empty = c.parse("letter", _letter([]))
    assert rules.letter_length(empty) == []
    assert rules.letter_length(c.parse("cv", written("cv_short"))) == []


def test_the_letter_writer_is_told_the_length() -> None:
    definition = writer_letter._definition()
    message = definition.prompt({"language": "en", "limits": {}}, None)
    assert f"about {c.LETTER_WORDS} words" in message
    assert "five or more words copied from the posting" in message


@pytest.mark.parametrize(
    ("words", "told"),
    [(480, 480), (150, 150), (600, 600), (None, 350), (149, 350), (601, 350), (True, 350)],
)
def test_the_letter_writer_is_told_the_persons_length(words: object, told: int) -> None:
    # A run queued before its input held `words`, or with a value no input allows, asks 350.
    data: dict[str, Any] = {"language": "en", "limits": {}}
    if words is not None:
        data["words"] = words
    message = writer_letter._definition().prompt(data, None)
    assert f"The paragraphs together: about {told} words." in message
