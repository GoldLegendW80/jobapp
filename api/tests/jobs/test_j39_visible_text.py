"""Jobs J39 (issue #877, items 1 and 6): `visible_text.visible`, a text as a reader sees it, by
Unicode category. Each class of character it drops, each kind it keeps (a decomposed accent, an
emoji with its selector, Greek and Cyrillic), the variation selectors' rule, no normalisation,
idempotence over every code point, and a work bound proved by counting, never by a clock."""

import sys
import unicodedata
from collections.abc import Callable
from typing import Any

import pytest

from app.jobs import visible_text
from app.jobs.visible_text import visible

DROPPED = [
    pytest.param("\x00", id="nul"),
    pytest.param("\x01", id="start of heading"),
    pytest.param("\x1b", id="escape"),
    pytest.param("\x0b", id="vertical tab"),
    pytest.param("\x0c", id="form feed"),
    pytest.param("\x7f", id="delete"),
    pytest.param("\x85", id="next line, C1"),
    pytest.param("\x9f", id="application program command, C1"),
    pytest.param("\u200b", id="zero width space"),
    pytest.param("\u200c", id="zero width non joiner"),
    pytest.param("\u200d", id="zero width joiner"),
    pytest.param("\u2060", id="word joiner"),
    pytest.param("\u200e", id="left to right mark"),
    pytest.param("\u200f", id="right to left mark"),
    pytest.param("\u061c", id="arabic letter mark"),
    pytest.param("\u202a", id="left to right embedding"),
    pytest.param("\u202e", id="right to left override"),
    pytest.param("\u2066", id="left to right isolate"),
    pytest.param("\u2069", id="pop directional isolate"),
    pytest.param("\ufeff", id="byte order mark"),
    pytest.param("\xad", id="soft hyphen"),
    pytest.param("\u180e", id="mongolian vowel separator"),
    pytest.param("\ufff9", id="interlinear annotation anchor"),
    pytest.param("\U000e0001", id="language tag"),
    pytest.param("\U000e0041", id="tag latin capital a"),
    pytest.param("\U000e007f", id="cancel tag"),
    pytest.param("\ud800", id="lone high surrogate"),
    pytest.param("\udfff", id="lone low surrogate"),
    pytest.param("\ue000", id="private use, first"),
    pytest.param("\uf0b7", id="private use, an icon font's bullet"),
    pytest.param("\U000f0000", id="supplementary private use"),
    pytest.param("\U0010fffd", id="last private use"),
    pytest.param("\u0378", id="unassigned"),
    pytest.param("\ufdd0", id="noncharacter"),
    pytest.param("\ufffe", id="noncharacter fffe"),
    pytest.param("\U0010ffff", id="last code point"),
    pytest.param("\U000e0100", id="supplementary variation selector"),
    pytest.param("\U000e01ef", id="last supplementary variation selector"),
]


@pytest.mark.parametrize("hidden", DROPPED)
def test_each_class_of_invisible_character_goes(hidden: str) -> None:
    assert visible(f"Py{hidden}thon engineer") == "Python engineer"
    assert visible(hidden * 3) == ""


KEPT = [
    pytest.param("tab\there", id="tab"),
    pytest.param("line one\nline two", id="line feed"),
    pytest.param("line one\r\nline two\rline three", id="carriage returns"),
    pytest.param("  spaced  out  ", id="spaces, untrimmed"),
    pytest.param("prix\xa0: 50\u202f000 \u20ac", id="no-break spaces"),
    pytest.param("one\u2028two\u2029three", id="line and paragraph separators"),
    pytest.param("caf\xe9 cr\xe8me br\xfbl\xe9e", id="precomposed accents"),
    pytest.param("cafe\u0301 cre\u0300me", id="decomposed accents stay decomposed"),
    pytest.param("\u0395\u03bb\u03bb\u03b7\u03bd\u03b9\u03ba\u03ac", id="greek"),
    pytest.param(
        "\u0418\u043d\u0436\u0435\u043d\u0435\u0440 \u0434\u0430\u043d\u043d\u044b\u0445",
        id="cyrillic",
    ),
    pytest.param(
        "\u05de\u05d4\u05e0\u05d3\u05e1 \u0645\u0647\u0646\u062f\u0633", id="hebrew and arabic"
    ),
    pytest.param("\u6570\u636e\u5de5\u7a0b\u5e08 \u30c7\u30fc\u30bf", id="chinese and japanese"),
    pytest.param("\u0928\u092e\u0938\u094d\u0924\u0947", id="devanagari with its marks"),
    pytest.param("\U0001f600 \U0001f680 \U0001f1eb\U0001f1f7", id="emoji and a flag"),
    pytest.param("\u2764\ufe0f team", id="an emoji with its presentation selector"),
    pytest.param("\u263a\ufe0e", id="a text presentation selector"),
    pytest.param("1\ufe0f\u20e3 first", id="a keycap"),
    pytest.param("#\ufe0f\u20e3", id="a keycap on a sign"),
    pytest.param("\U0001f44d\U0001f3fd", id="a skin tone"),
    pytest.param("\u1820\u180b", id="a mongolian letter and its free variation selector"),
    pytest.param("\u8449\ufe00", id="an ideograph and its variation selector"),
    pytest.param("\u20ac 100, \xa9, \u2122, \u2192, \u2014", id="symbols and punctuation"),
    pytest.param("\ufb01nance \uff21\uff22", id="a ligature and full width letters, no nfkc"),
    pytest.param("<li>R&amp;D</li> &nbsp;", id="markup is text here"),
    pytest.param("", id="empty"),
]


@pytest.mark.parametrize("text", KEPT)
def test_what_a_reader_sees_stays_exactly_as_it_is(text: str) -> None:
    assert visible(text) == text


SELECTORS = [
    pytest.param("\u2764\ufe0f\ufe0f", "\u2764", id="a run of two goes whole"),
    pytest.param("A" + "\ufe00\ufe0f" * 8, "A", id="a run of sixteen, bytes spelled, goes"),
    pytest.param("\ufe0fA", "A", id="at the start"),
    pytest.param("go \ufe0f", "go ", id="after a space"),
    pytest.param("line\n\ufe0f", "line\n", id="after a line break"),
    pytest.param("e\u0301\ufe0f", "e\u0301", id="after a combining mark"),
    pytest.param("\u2764\u200b\ufe0f", "\u2764\ufe0f", id="after an emoji, once the space goes"),
    pytest.param("A\U000e0100\ufe0f", "A\ufe0f", id="a supplementary one never counts"),
    pytest.param("\u1820\u180b\u180c", "\u1820", id="a mongolian run"),
]


@pytest.mark.parametrize(("text", "seen"), SELECTORS)
def test_a_variation_selector_stays_only_alone_after_a_visible_character(
    text: str, seen: str
) -> None:
    assert visible(text) == seen


ACCEPTED = [
    pytest.param("\U0001f468\u200d\U0001f4bb", "\U0001f468\U0001f4bb", id="a joined emoji"),
    pytest.param(
        "\U0001f3f4\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f",
        "\U0001f3f4",
        id="a flag written with tag characters",
    ),
    pytest.param(
        "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645",
        "\u0645\u06cc\u062e\u0648\u0627\u0647\u0645",
        id="persian loses its zero width non joiner",
    ),
]


@pytest.mark.parametrize(("text", "seen"), ACCEPTED)
def test_the_known_and_accepted_losses(text: str, seen: str) -> None:
    assert visible(text) == seen


def _kept_alone(char: str) -> bool:
    """What the module docstring keeps of a character on its own (no selector)."""
    category = unicodedata.category(char)
    if char in "\t\n\r":
        return True
    low, high = visible_text.SUPPLEMENTARY_SELECTORS
    return category not in visible_text.DROPPED and not low <= ord(char) <= high


EVERY = "".join(chr(point) for point in range(sys.maxunicode + 1))


def test_every_code_point_is_dropped_or_kept_by_its_category_and_twice_is_once() -> None:
    seen = visible(EVERY)
    # In code point order the selectors stand in runs (U+180E between two of them is a format
    # character), so none of them stays.
    expected = "".join(
        c for c in EVERY if _kept_alone(c) and not visible_text.SELECTORS.fullmatch(c)
    )
    assert seen == expected
    assert visible(seen) == seen


@pytest.mark.parametrize("step", [1, 7, 97, 1009])
def test_twice_is_once_on_every_mix(step: int) -> None:
    last = sys.maxunicode + 1
    mixed = "".join(chr(p) + chr((p * 31) % last) for p in range(0, last, step))
    once = visible(mixed)
    assert visible(once) == once
    # A seen text never starts with a selector, so two of them side by side are seen too.
    assert visible(once + once) == once + once


def _counted(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Counts each category read and each match handed back to Python."""
    visible("warm")  # the class is built once, before counting
    calls = [0]

    def counting(function: Callable[..., Any]) -> Callable[..., Any]:
        def counted(*args: Any) -> Any:
            calls[0] += 1
            return function(*args)

        return counted

    monkeypatch.setattr(visible_text.unicodedata, "category", counting(unicodedata.category))
    monkeypatch.setattr(visible_text, "_kept", counting(visible_text._kept))
    monkeypatch.setattr(visible_text, "_selector", counting(visible_text._selector))
    return calls


WORST = [
    pytest.param("a\ufe0f", id="a selector after each letter"),
    pytest.param("\u2764\ufe0f\ufe0f", id="runs of selectors"),
    pytest.param("\U0001f600", id="astral emoji"),
    pytest.param("\U000f0000", id="astral private use"),
    pytest.param("x\u200b", id="a hidden space after each letter"),
    pytest.param("plain text ", id="plain text"),
]


@pytest.mark.parametrize("unit", WORST)
def test_the_work_grows_with_the_length_and_no_faster(
    unit: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _counted(monkeypatch)
    size = 200_000
    text = unit * (size // len(unit))
    visible(text)
    single = calls[0]
    calls[0] = 0
    visible(text + text)
    # A deterministic budget, independent of the machine: at most two steps of Python per
    # character, and twice the text costs twice the steps.
    assert single <= 2 * len(text)
    assert calls[0] == 2 * single
