"""The fact check rules for a draft (mail M9): a table of named cases in fixtures/m9, then the
properties that no single case shows: determinism, the offsets, the trust model, and hostile
inputs of 100,000 characters that must finish (the algorithm is linear, so there is no timing
assertion anywhere)."""

from __future__ import annotations

import dataclasses
import json
import random
from pathlib import Path
from typing import Any

import pytest

from app.mail.factcheck_rules import CODES, ISSUE_KINDS, Issue, Sources, check

FIXTURES = Path(__file__).parent / "fixtures" / "m9"
SOURCES: dict[str, dict[str, Any]] = json.loads((FIXTURES / "sources.json").read_text("utf-8"))
CASES: list[dict[str, Any]] = json.loads((FIXTURES / "cases.json").read_text("utf-8"))

FIELDS = {field.name for field in dataclasses.fields(Sources)}


def build_sources(name: str = "jane", replace: dict[str, list[str]] | None = None) -> Sources:
    data = {**SOURCES[name], **(replace or {})}
    return Sources(**{key: tuple(value) for key, value in data.items() if key in FIELDS})


def seen(body: str, sources: Sources) -> list[tuple[str, str, str]]:
    return [(i.kind, i.code, body[i.start : i.end]) for i in check(body, sources)]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_case_table(case: dict[str, Any]) -> None:
    sources = build_sources(case.get("sources", "jane"), case.get("replace"))
    expected = [tuple(item) for item in case["expect"]]
    assert seen(case["body"], sources) == expected


def test_case_names_are_unique() -> None:
    names = [case["name"] for case in CASES]
    assert len(names) == len(set(names))


def test_every_code_and_kind_is_exercised_by_the_table() -> None:
    codes = {item[1] for case in CASES for item in case["expect"]}
    kinds = {item[0] for case in CASES for item in case["expect"]}
    assert codes == CODES
    assert kinds == set(ISSUE_KINDS)


# ---------------------------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------------------------

MIXED = (
    "Call 07 00 00 00 00, I have 4 years, 12 projects, a 20% rise and 120k\u20ac at Google. "
    "Since March 2019, as Head of Engineering, 14 rue de la Gare, https://x.example.com/a, "
    "me@x.example.com, trois ans, 11h, 4 November."
)


def test_same_body_same_sources_same_issues() -> None:
    sources = build_sources()
    first = check(MIXED, sources)
    assert len(first) >= 10
    assert check(MIXED, sources) == first
    assert check(MIXED, build_sources()) == first


def test_the_order_of_the_entries_of_a_source_changes_nothing() -> None:
    reference = check(MIXED, build_sources())
    rng = random.Random(9)  # noqa: S311
    base = build_sources()
    for _ in range(10):
        shuffled = Sources(
            **{
                name: tuple(rng.sample(list(value), len(value)))
                for name, value in dataclasses.asdict(base).items()
            }
        )
        assert check(MIXED, shuffled) == reference


def test_issues_are_sorted_and_unique() -> None:
    issues = check(MIXED, build_sources())
    keys = [(i.start, i.end, i.kind, i.code) for i in issues]
    assert keys == sorted(set(keys))
    assert all(isinstance(i, Issue) for i in issues)


def test_the_sources_are_not_modified() -> None:
    sources = build_sources()
    before = dataclasses.asdict(sources)
    check(MIXED, sources)
    assert dataclasses.asdict(sources) == before


def test_an_issue_is_the_plans_shape() -> None:
    issue = check("I have 4 years.", build_sources())[0]
    assert dataclasses.asdict(issue) == {
        "kind": "number",
        "start": 7,
        "end": 14,
        "code": "duration_not_in_profile",
    }
    with pytest.raises(dataclasses.FrozenInstanceError):
        issue.start = 0  # type: ignore[misc]


# ---------------------------------------------------------------------------------------------
# Offsets are into the ORIGINAL body
# ---------------------------------------------------------------------------------------------


def test_offsets_after_accents_and_multi_code_unit_characters() -> None:
    body = "Caf\u00e9 \u2615 \U0001f600\U0001f600 voil\u00e0, I have 4 years of experience."
    (issue,) = check(body, build_sources())
    assert body[issue.start : issue.end] == "4 years"


def test_offsets_after_decomposed_accents() -> None:
    body = "Cafe\u0301 re\u0301ussi, I have 4 years of experience."
    (issue,) = check(body, build_sources())
    assert body[issue.start : issue.end] == "4 years"
    assert issue.start == body.index("4 years")


def test_offsets_after_characters_that_fold_to_several_or_none() -> None:
    # "\u00df" folds to "ss", a zero width space to nothing, "\ufb03" to "ffi".
    body = "Stra\u00dfe \u200b\u200b o\ufb03ce \ufb03 I have 4 years of experience."
    (issue,) = check(body, build_sources())
    assert body[issue.start : issue.end] == "4 years"


def test_offsets_after_full_width_and_astral_digits() -> None:
    body = "\U0001d7d1\U0001d7d1 \uff14 projects, 4 years"
    got = seen(body, build_sources())
    assert ("number", "duration_not_in_profile", "4 years") in got
    for _, _, text in got:
        assert text in body


def test_a_combining_mark_stays_inside_the_span_of_its_letter() -> None:
    body = "I worked at Google\u0301 yesterday."
    (issue,) = check(body, build_sources())
    assert body[issue.start : issue.end] == "Google\u0301"


def test_every_span_is_inside_the_body_and_not_empty() -> None:
    body = MIXED + " \U0001f600 \u00e9\u0301 " + MIXED
    for issue in check(body, build_sources()):
        assert 0 <= issue.start < issue.end <= len(body)


# ---------------------------------------------------------------------------------------------
# The trust model
# ---------------------------------------------------------------------------------------------


def test_the_thread_legitimises_numbers_but_not_durations_phones_links_or_emails() -> None:
    thread = "salary 90k, 4 years, +33 1 98 76 54 32, https://m.example.com/a, x@m.example.com"
    sources = Sources(thread=(thread,), profile=("GenAI Engineer",))
    body = "90k. 4 years. +33 1 98 76 54 32. https://m.example.com/a. x@m.example.com."
    got = seen(body, sources)
    assert [(kind, code) for kind, code, _ in got] == [
        ("number", "duration_not_in_profile"),
        ("address", "phone_not_in_profile"),
        ("link", "link_not_in_profile"),
        ("address", "email_not_in_profile"),
    ]


def test_every_own_source_legitimises() -> None:
    body = "1234 5678 9 years 24 July 2031 https://a.example.com x@a.example.com"
    for field in ("profile", "answers", "instructions"):
        sources = Sources(**{field: (body,)})
        assert check(body, sources) == []
    assert check(body, Sources(thread=(body,)))  # the mail alone is not enough


def test_nothing_legitimises_without_sources() -> None:
    got = seen("I have 3 years at Acme.", Sources())
    assert [item[2] for item in got] == ["3 years", "Acme"]


def test_a_recipient_is_not_flagged_but_another_address_is() -> None:
    sources = Sources(recipients=("Recruiter <recruiter@acme.example.org>",))
    assert check("Hello recruiter@acme.example.org", sources) == []
    assert check("Hello other@acme.example.org", sources)


def test_a_phrase_does_not_match_across_two_sources() -> None:
    sources = Sources(profile=("I am a senior", "engineer at heart"))
    assert [i.code for i in check("I am a senior engineer.", sources)] == ["title_not_in_profile"]


# ---------------------------------------------------------------------------------------------
# Hostile inputs: they must finish; there is no timing assertion, the rules are linear
# ---------------------------------------------------------------------------------------------

SIZE = 100_000


def hostile(unit: str) -> str:
    return (unit * (SIZE // len(unit) + 1))[:SIZE]


HOSTILE = {
    "digits": "7" * SIZE,
    "digits_with_spaces": hostile("7 "),
    "digits_with_commas": hostile("3,"),
    "comma_groups": hostile("3,3,3,3,3,"),
    "dotted_groups": hostile("1.2."),
    "dated_groups": hostile("12/03/"),
    "times": hostile("10:30 "),
    "hours": hostile("10h30"),
    "trunk": hostile("1 (0)"),
    "capitals": "A" * SIZE,
    "capital_words": hostile("Acme "),
    "prepositions": hostile("at "),
    "prepositions_then_capitals": hostile("at Acme "),
    "long_names": hostile("for Acme Corp Labs Studio Group Holdings Inc "),
    "connectors": hostile("at Acme of "),
    "number_words": hostile("trois "),
    "number_phrases": hostile("quatre vingt dix sept "),
    "number_words_and": hostile("hundred and "),
    "half": hostile("3 and a half years "),
    "years": hostile("3+ years "),
    "months_and_days": hostile("1st of March "),
    "month_names": hostile("may sept mars "),
    "street_words": hostile("rue "),
    "numbered_streets": hostile("12 rue de la "),
    "title_words": hostile("engineer "),
    "head_of": hostile("head of "),
    "at_signs": hostile("a@"),
    "emails": hostile("a@b.com "),
    "long_local_part": "a" * (SIZE // 2) + "@" + "b" * (SIZE // 2 - 1),
    "dots": hostile("a."),
    "urls": hostile("http://"),
    "schemes": hostile("a://"),
    "www": hostile("www."),
    "slashes": "/" * SIZE,
    "percent_and_currency": hostile("3% 4\u20ac "),
    "unicode_digits": hostile("\uff13\u0663"),
    "zero_width": "\u200b" * SIZE,
    "combining": "e" + "\u0301" * SIZE,
    "newlines": "\n" * SIZE,
    "spaces_then_marker": " " * SIZE + "1.",
    "mixed": MIXED * (SIZE // len(MIXED)),
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_a_hostile_body_of_100000_characters_completes(name: str) -> None:
    body = HOSTILE[name]
    issues = check(body, build_sources())
    assert len(issues) <= len(body)
    for issue in issues:
        assert 0 <= issue.start < issue.end <= len(body)


@pytest.mark.parametrize("name", ["digits", "comma_groups", "prepositions_then_capitals", "mixed"])
def test_a_hostile_source_of_100000_characters_completes(name: str) -> None:
    text = HOSTILE[name]
    sources = Sources(
        profile=(text,), answers=(text,), thread=(text,), instructions=(text,), participants=(text,)
    )
    check(MIXED, sources)
    check(text[:2000], sources)


def test_a_very_long_run_of_digits_is_one_opaque_value_not_a_conversion() -> None:
    body = "7" * 5000  # beyond Python's 4300 digit limit for int()
    assert [i.code for i in check(body, Sources())] == ["number_not_in_sources"]
    assert check(body, Sources(profile=(body,))) == []
    assert check(body, Sources(thread=(body,))) == []


def test_one_issue_for_a_run_of_numbers_that_is_not_sourced() -> None:
    body = hostile("3,")
    assert len(check(body, Sources())) == 1


FRAGMENTS = [
    "3", "3.5", "3,5", "1,234.56", "2026", "2026-11-03", "12/03", "10:30", "10h", "+33", "(0)",
    " ", "  ", "\n", ".", ",", "-", "/", ":", "%", "€", "$", "+", "k", "years", "ans", "mois",
    "et demi", "and a half", "at ", "chez ", "with ", "for ", "Acme", "ORBITAL LABS", "Corp",
    "of", "de", "&", "'", "é", "é", "​", "３", "\U0001f600", "March", "mars",
    "may", "sept", "1st", "rue ", "street ", "engineer ", "Head of ", "https://", "www.",
    "example.com", "@", "x@y.fr", "trois", "quatre vingt", "cent", "mille", "un", "et", "and",
    "<", ">", "(", ")", "–", "h", "H",
]  # fmt: skip


def test_random_bodies_never_crash_and_always_give_valid_spans() -> None:
    rng = random.Random(130)  # noqa: S311
    sources = build_sources()
    for _ in range(400):
        body = "".join(rng.choice(FRAGMENTS) for _ in range(rng.randint(1, 40)))
        issues = check(body, sources)
        assert issues == check(body, sources)
        for issue in issues:
            assert 0 <= issue.start < issue.end <= len(body)
            assert issue.kind in ISSUE_KINDS
            assert issue.code in CODES
        # Sources made of the same fragments must not crash either.
        noisy = Sources(profile=(body,), thread=(body[::-1],), participants=(body,))
        check(body[::-1], noisy)


def test_a_bare_string_for_a_source_is_refused() -> None:
    with pytest.raises(TypeError):
        Sources(profile="3 years")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Sources(recipients=["a@b.fr"])  # type: ignore[arg-type]
