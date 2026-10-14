"""The document rules (D2): the dash rule, banned phrases, sentences, soft lengths, keys."""

from __future__ import annotations

import pytest

from app.documents import content as c
from app.documents import rules
from tests.documents.conftest import written

EM, EN = "—", "–"
NBSP, NNBSP = " ", " "

SEPARATORS = [
    # (case, text, fixed text)
    ("an em dash between words", f"Python {EM} Go", "Python, Go"),
    ("an em dash without spaces", f"Python{EM}Go", "Python, Go"),
    ("an en dash between words", f"Lyon {EN} Paris", "Lyon, Paris"),
    ("a spaced hyphen", "Python - Go", "Python, Go"),
    ("a double hyphen", "Python -- Go", "Python, Go"),
    ("a hyphen look-alike spaced", "Python ‑ Go", "Python, Go"),
    ("a full width hyphen spaced", "Python － Go", "Python, Go"),
    ("French non breaking spaces", f"Lyon{NNBSP}{EM}{NBSP}Paris", "Lyon, Paris"),
    ("a dash that opens the field", "- Built tools", "Built tools"),
    ("a dash that closes the field", f"Built tools {EM}", "Built tools"),
    ("a dash that opens a line", "One\n- Two", "One\nTwo"),
    ("a dash after a comma", f"Lyon, {EM} Paris", "Lyon, Paris"),
    ("a dash before a period", f"Lyon {EM}.", "Lyon."),
    ("two dashes in a row", f"a {EM} {EM} b", "a, b"),
    ("a range of years", f"2019{EN}2021", "2019-2021"),
    ("several in one text", f"A {EM} B - C {EN} D", "A, B, C, D"),
]

UNCHANGED = [
    ("a hyphenated word", "full-stack"),
    ("a hyphenated word with look-alike", "full‑stack"),
    ("a minus sign", "from -5 to 5"),
    ("a hyphen on one side only", "Python -Go"),
    ("an address with a hyphen", "jean-pierre@example.org"),
    ("no dash at all", "Plain words."),
    ("an empty text", ""),
]


@pytest.mark.parametrize(
    ("text", "fixed"), [case[1:] for case in SEPARATORS], ids=[case[0] for case in SEPARATORS]
)
def test_dash_separators_are_found_and_fixed(text: str, fixed: str) -> None:
    found = rules.separators(text)
    assert found
    assert rules.apply_separators(text, found) == fixed
    compact = rules.apply_separators(text, found, compact=True)
    assert len(compact) <= len(text)


@pytest.mark.parametrize(
    "text", [case[1] for case in UNCHANGED], ids=[case[0] for case in UNCHANGED]
)
def test_hyphenated_words_are_never_separators(text: str) -> None:
    assert rules.separators(text) == []


def test_the_dash_scan_is_linear_on_hostile_input() -> None:
    text = (" -" * 50_000) + (EM * 50_000) + ("a-" * 50_000)
    found = rules.separators(text)
    assert found  # finishes; the runs are merged, not quadratic
    assert len(found) < 10


BANNED = [
    # (case, text, phrases, expected flagged texts)
    ("same case", "I am passionate about code.", ["passionate"], ["passionate"]),
    ("other case", "I am PASSIONATE about code.", ["passionate"], ["PASSIONATE"]),
    ("accents folded", "Je suis passionné de code.", ["PASSIONNE"], ["passionné"]),
    (
        "combining accent kept in the span",
        "Je suis passionné de code.",
        ["passionné"],
        ["passionné"],
    ),
    ("a phrase of two words", "A true team  player.", ["team player"], ["team  player"]),
    ("never inside a word", "Compassionate people.", ["passionate"], []),
    ("never a prefix", "Synergies matter.", ["synergy"], []),
    ("full width letters", "Ｓｙｎｅｒｇｙ first", ["synergy"], ["Ｓｙｎｅｒｇｙ"]),
    ("curly apostrophe", "Don’t hesitate.", ["don't hesitate"], ["Don’t hesitate"]),
    ("no phrase", "Anything.", [], []),
    ("a blank phrase", "Anything.", ["   "], []),
]


@pytest.mark.parametrize(
    ("text", "phrases", "expected"),
    [case[1:] for case in BANNED],
    ids=[case[0] for case in BANNED],
)
def test_banned_phrases_are_whole_words_case_and_accents_folded(
    text: str, phrases: list[str], expected: list[str]
) -> None:
    assert [text[s:e] for s, e in rules.banned(text, phrases)] == expected


SENTENCES = [
    ("three sentences", "One. Two! Three?", ["One. ", "Two! ", "Three?"]),
    ("a decimal point", "I have 3.5 years. Next.", ["I have 3.5 years. ", "Next."]),
    ("an abbreviation", "Tools, e.g. Python. Next.", ["Tools, e.g. Python. ", "Next."]),
    ("lines", "First line\nSecond line", ["First line\n", "Second line"]),
    ("a line ending a sentence", "Done.\nNext", ["Done.\n", "Next"]),
    ("French spacing", f"Pourquoi{NBSP}? Parce que.", [f"Pourquoi{NBSP}? ", "Parce que."]),
    ("a closing quote", 'He said "yes." Then left.', ['He said "yes." ', "Then left."]),
    ("an ellipsis", "Wait… then go.", ["Wait… ", "then go."]),
    ("no end mark", "No end mark", ["No end mark"]),
    (
        "a domain name",
        "Built example.org in Python. Next.",
        ["Built example.org in Python. ", "Next."],
    ),
]


@pytest.mark.parametrize(
    ("text", "expected"), [case[1:] for case in SENTENCES], ids=[case[0] for case in SENTENCES]
)
def test_sentences_cover_the_text(text: str, expected: list[str]) -> None:
    spans = rules.sentences(text)
    assert [text[s:e] for s, e in spans] == expected
    assert "".join(text[s:e] for s, e in spans) == text


def test_soft_lengths_are_notes_never_refusals() -> None:
    model = c.parse(
        "cv",
        {
            "summary": "s" * 331,
            "highlights": ["h" * 61],
            "experience": [
                {"employer_key": "example-corp", "bullets": ["m" * 191, "m" * 190]},
                {"employer_key": "sample-labs", "bullets": ["o" * 101]},
            ],
        },
    )
    notes = rules.soft_lengths(model, rules.DEFAULT_SOFT_LIMITS)
    assert [(n.field, n.start, n.end, n.level) for n in notes] == [
        ("summary", 330, 331, "note"),
        ("highlights.0", 60, 61, "note"),
        ("experience.0.bullets.0", 190, 191, "note"),
        ("experience.1.bullets.0", 100, 101, "note"),
    ]
    tight = rules.SoftLimits(summary=None, highlight=None, main_bullet=None, bullet=None)
    assert rules.soft_lengths(model, tight) == []


def test_a_bullet_of_191_characters_is_a_note() -> None:
    model = c.parse("cv", {"experience": [{"employer_key": "a", "bullets": ["x" * 191]}]})
    assert [n.code for n in rules.soft_lengths(model, rules.DEFAULT_SOFT_LIMITS)] == [
        rules.OVER_SOFT_LENGTH
    ]


def test_an_employer_key_in_neither_the_profile_nor_the_base_cv_is_invalid() -> None:
    model = c.parse("cv", written("cv_typical"))
    assert rules.employer_keys(model, ["example-corp", "sample-labs"]) == []
    issues = rules.employer_keys(model, ["example-corp"])
    assert [(i.field, i.code, i.level) for i in issues] == [
        ("experience.1.employer_key", rules.UNKNOWN_EMPLOYER, "invalid")
    ]
    assert rules.employer_keys(c.parse("letter", {}), []) == []


def test_required_fields_left_empty_are_notes() -> None:
    assert [i.field for i in rules.required_empty(c.parse("cv", {}))] == ["headline", "summary"]
    assert [i.field for i in rules.required_empty(c.parse("letter", {}))] == [
        "subject",
        "paragraphs",
    ]
    assert rules.required_empty(c.parse("cv", written("cv_short"))) == []


def test_structure_maps_errors_to_invalid_issues() -> None:
    model, issues = rules.structure("cv", {"highlights": ["a", "b", "c", "d"]})
    assert model is None
    assert [(i.field, i.code, i.level) for i in issues] == [("highlights", "too_many", "invalid")]
    model, issues = rules.structure("cv", written("cv_short"))
    assert model is not None
    assert issues == []
