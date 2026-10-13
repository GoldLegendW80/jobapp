"""The examples of the citation rules, one test per number, and the table of the plan.

Numbers 1 to 108 are the examples that the seed script executed
(tasks/evidence/ingestion-g0/citation_rules.py): its verdict on each is the reference. The
others are G4's (the notes of issue #45, the three points the plan left open, the corrections of
the plan's tables). The table printed in tasks/ingestion/todo.md is generated from the same
data, and a test here fails when the two differ.
"""

import os
import re

import pytest

from app.ingestion import citations
from app.ingestion.citations import Refusal, Rule
from tests.ingestion.citation_examples import (
    ACCEPT,
    EXAMPLES,
    FLAGGED,
    PLAN,
    REFUSE,
    SEED_OUTPUT,
    Example,
    markdown,
    table_of,
    verdict,
)

SEED_EXAMPLES = 108

by_number = pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: str(example.number))


def test_the_examples_are_numbered_from_one_without_a_gap() -> None:
    assert [example.number for example in EXAMPLES] == list(range(1, len(EXAMPLES) + 1))
    assert len(EXAMPLES) >= SEED_EXAMPLES


@by_number
def test_example(example: Example) -> None:
    assert verdict(example) == (example.verdict, example.rule)


@by_number
def test_example_states_its_rule(example: Example) -> None:
    """A refusal names its rule, and only a refusal does."""
    assert example.verdict in (ACCEPT, FLAGGED, REFUSE)
    assert (example.rule is not None) == (example.verdict == REFUSE)


@by_number
def test_the_cited_span_never_cuts_a_token(example: Example) -> None:
    """For every excerpt that is found, the span starts and ends on a token boundary of the
    document, holds the excerpt, and maps back to the document's own text."""
    document = citations.prepare(example.source)
    for excerpt in example.cited():
        found = citations.find(document, excerpt)
        if isinstance(found, Refusal):
            continue
        assert citations.is_token_boundary(document.text, found.start)
        assert citations.is_token_boundary(document.text, found.end)
        wanted = citations.skeleton(citations.normalise(excerpt).text).text
        assert wanted in citations.skeleton(found.text).text
        raw = example.source[found.source_start : found.source_end]
        assert citations.normalise(raw).text == found.text


@by_number
def test_an_accepted_example_stores_the_documents_own_words(example: Example) -> None:
    result = citations.check_item(example.field, example.value, example.source, example.cited())
    if isinstance(result, Refusal):
        index, rule, reason = result
        assert rule is example.rule
        assert reason == citations.REASONS[rule]
        assert index == "value" or re.fullmatch(r"excerpts\[\d+\]", index)
        return
    assert len(result.citations) == len(example.cited())
    for citation in result.citations:
        assert citation.excerpt == example.source[citation.start : citation.end]
        assert 0 <= citation.start < citation.end <= len(example.source)


@by_number
def test_a_prepared_document_gives_the_same_answer(example: Example) -> None:
    document = citations.prepare(example.source)
    once = citations.check_item(example.field, example.value, document, example.cited())
    twice = citations.check_item(example.field, example.value, document, example.cited())
    assert once == twice
    assert once == citations.check_item(
        example.field, example.value, example.source, example.cited()
    )


def test_every_rule_of_a_refusal_has_an_example_or_a_unit_test() -> None:
    """The rules that an example of the table shows. The others are about the shape of the call
    and have their tests in test_citations.py."""
    shown = {example.rule for example in EXAMPLES if example.rule is not None}
    assert shown == {
        Rule.EXCERPT_NOT_FOUND,
        Rule.EXCERPT_TOO_SHORT,
        Rule.EXCERPT_NOT_WHOLE_DOCUMENT,
        Rule.SETTING,
        Rule.VALUE_EMPTY,
        Rule.ADDRESS_NOT_IN_SOURCE,
        Rule.NAME_NOT_IN_SOURCE,
        Rule.YEAR_NOT_IN_SOURCE,
        Rule.MONTH_NOT_IN_SOURCE,
        Rule.MONTH_APART_FROM_YEAR,
        Rule.TWO_DIGIT_YEAR,
        Rule.PHONE_NOT_IN_SOURCE,
        Rule.NUMBER_NOT_IN_SOURCE,
    }


# --- a fictitious identity -----------------------------------------------------------------------

ADDRESS = re.compile(r"[\w.+-]+@[\w.-]+\.[a-z]+")
HOST = re.compile(r"(?:https?://|www\.)(?:[\w.~%-]+(?::[\w.~%-]*)?@)?([\w.-]+)")
# RFC 2606 and RFC 6761: names nobody can register.
RESERVED = ("example.org", "example.com", "example.net", ".example")
# ARCEP keeps 06 39 98 00 00 to 06 39 98 99 99 for fiction.
FICTION_PHONE = re.compile(r"(?:\+33 ?\(?0?\)?|0)6[ .-]?39[ .-]?98(?:[ .-]?\d\d){2}")
PHONE_LIKE = re.compile(r"(?:\+33 ?\(?0?\)?|0)[1-9](?:[ .-]?\d\d){4}")


def texts_of(example: Example) -> list[str]:
    return [example.source, str(example.value), *example.cited()]


@by_number
def test_no_address_of_a_real_domain(example: Example) -> None:
    for text in texts_of(example):
        for address in ADDRESS.findall(text.lower()):
            assert address.endswith(RESERVED), address
        for host in HOST.findall(text.lower()):
            assert host.rstrip(".").endswith(RESERVED), host


@by_number
def test_no_phone_number_outside_the_range_kept_for_fiction(example: Example) -> None:
    for text in texts_of(example):
        for number in PHONE_LIKE.findall(text):
            digits = re.sub(r"\D", "", number)
            # A reference number written in groups of four is not a phone number (example 125).
            if example.number == 125 or digits == "0123456789":
                continue
            assert FICTION_PHONE.fullmatch(number), number


# --- the table of the plan -----------------------------------------------------------------------

# The api folder alone is mounted in the dev container (compose.dev.yml), so the repository root
# is not there. CI and a checkout on the Mac always have it.
needs_plan = pytest.mark.skipif(
    not PLAN.is_file() and not os.environ.get("CI"),
    reason="the repository root is not mounted here",
)


@needs_plan
def test_the_table_of_the_plan_is_generated_from_these_examples() -> None:
    """tasks/ingestion/todo.md prints this table. Regenerate it with
    `uv run python -m tests.ingestion.citation_examples --write` after changing an example."""
    plan = PLAN.read_text(encoding="utf-8")
    start, end = table_of(plan)
    assert plan[start:end].rstrip("\n") == markdown()


@needs_plan
def test_the_plan_states_the_number_of_examples() -> None:
    plan = PLAN.read_text(encoding="utf-8")
    assert f"{len(EXAMPLES)} examples" in plan


def test_the_table_has_one_row_per_example_and_names_each_rule() -> None:
    rows = markdown().splitlines()[2:]
    assert len(rows) == len(EXAMPLES)
    for row, example in zip(rows, EXAMPLES, strict=True):
        cells = row.split(" | ")
        assert cells[0] == f"| {example.number}"
        assert cells[-2] == example.verdict
        if example.rule is not None:
            assert cells[-1].startswith(f"`{example.rule}`: {citations.REASONS[example.rule]}")
        # A cell never holds the separator of the table.
        assert row.count(" | ") == 6


def test_the_seed_examples_keep_their_verdicts() -> None:
    """The verdict of the seed script on each of its 108 examples, as its output file states
    them (tasks/evidence/ingestion-g0/citation_rules.txt)."""
    seed = SEED_OUTPUT
    if not seed.is_file():
        pytest.skip("the repository root is not mounted here")
    stated = {}
    for line in seed.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        cells = line.split(" | ")
        stated[int(cells[0])] = cells[3]
    assert len(stated) == SEED_EXAMPLES
    for example in EXAMPLES[:SEED_EXAMPLES]:
        assert example.verdict == stated[example.number], example.number
