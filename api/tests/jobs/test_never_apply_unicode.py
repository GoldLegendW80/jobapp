"""Unicode invariants shared by sweep exclusions and Apply (#335)."""

import sys
import unicodedata

import pytest

from app.jobs.never_apply import NeverApplyRule, fold, matching_rule

HIDDEN_WHITESPACE = "\x0b\x0c\x1c\x1d\x1e\x1f\x85"


@pytest.mark.parametrize("hidden", HIDDEN_WHITESPACE, ids=lambda c: f"U+{ord(c):04X}")
def test_hidden_whitespace_inside_an_excluded_term_is_not_a_gap(hidden: str) -> None:
    rule = NeverApplyRule.parse("Excluded roles: analyst")
    assert rule.matches("Example Robotics", f"Ana{hidden}lyst")
    disguised = NeverApplyRule.parse(f"Excluded roles: ana{hidden}lyst")
    assert matching_rule([disguised], "Analyst") is disguised


@pytest.mark.parametrize("gap", [" ", "\t", "\n", "\r", "\u00a0", "\u2028", "\u2029"])
def test_visible_word_gaps_do_not_join_a_split_term(gap: str) -> None:
    rule = NeverApplyRule.parse("Excluded roles: analyst")
    assert not rule.matches("Example Robotics", f"Ana{gap}lyst")
    phrase = NeverApplyRule.parse("Excluded roles: data analyst")
    assert phrase.matches("Example Robotics", f"Data{gap}Analyst")


def test_every_unicode_letter_reads_alike_in_each_case() -> None:
    for point in range(sys.maxunicode + 1):
        letter = chr(point)
        if not unicodedata.category(letter).startswith("L"):
            continue
        variants = (letter, letter.upper(), letter.lower(), letter.title(), letter.casefold())
        assert len({fold(text) for text in variants}) == 1, f"U+{point:04X}"


def test_polytonic_iota_subscript_is_a_letter_before_marks_are_removed() -> None:
    letters = [
        chr(point)
        for point in range(0x1F80, 0x1FFD)
        if "\u0345" in unicodedata.normalize("NFD", chr(point))
    ]
    assert len(letters) == 63
    for letter in letters:
        rule = NeverApplyRule.parse(f"Excluded roles: {letter}")
        assert rule.matches("Example Robotics", letter.upper()), f"U+{ord(letter):04X}"
        assert fold(letter) == fold(letter.upper())


@pytest.mark.parametrize("size", [50_000, 100_000, 200_000])
def test_large_hidden_control_runs_keep_the_exclusion(size: int) -> None:
    rule = NeverApplyRule.parse("Excluded roles: analyst")
    assert rule.matches("Example Robotics", "x" * size + " Ana" + "\x1f" * size + "lyst")
    assert not rule.matches("Example Robotics", "x" * size + " Ana ly st")
