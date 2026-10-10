"""The bounds of the citation checker. It is called on hostile input, a document of up to 500,000
characters and a model's output, so its cost must stay linear and no regular expression may
backtrack.

Complexity, as built (app/ingestion/citations.py):
- `prepare(document)`: one pass, O(n). The caller keeps the result for the step.
- `find`: one substring search of the document's skeleton, O(n), then at most OCCURRENCES_MAX
  occurrences are looked at, each in constant time, and a span is at most SPAN_MAX characters.
- every value rule reads spans of bounded size: O(length of the value) per part of the value.

The time bounds are on CPU time (`time.process_time`, the best of three), not on the wall clock,
which other pytest-xdist workers sharing the runner's cores would stretch. They are generous: 20
to 60 times what the Mac measures, since CI traces coverage and shares three CPUs. What they
catch is a change of order: a quadratic scan of 500,000 characters takes minutes, not seconds.
Measured figures are in tasks/ingestion/todo.md, "Measurements".
"""

import random
import re
import time
import tracemalloc
from collections.abc import Callable

import pytest

from app.ingestion import citations
from app.ingestion.citations import Accepted, Refusal, Rule, Span, check_item, find, prepare

LARGE = 500_000  # the limit of one document's text (SPEC-ingestion.md, "Limits")
CRAFTED = 200_000
PREPARE_BUDGET_S = 5.0  # one document of 500,000 characters: 0.09 s on the Mac
FIND_BUDGET_S = 1.0  # one excerpt in it: under 0.001 s on the Mac
CHECK_BUDGET_S = 2.0  # one item against it, twenty excerpts and a long value included
GROWTH_MAX = 8.0  # the cost of four times the text: about 4 when linear, 16 when quadratic

WORDS = [
    "Betclic",
    "Thales",
    "développeur",
    "ingénieur",
    "full-stack",
    "Python",
    "Docker",
    "équipe",
    "projet",
    "depuis",
    "janvier",
    "2023",
    "mission",
    "chez",
    "Bordeaux",
    "l\u2019équipe",
    "GenAI",
    "à",
    "de",
    "la",
    "et",
    "en",
    "production",
    "10\u202f000",
    "3,5",
    "ans",
    "camille.durand@example.org",
    "06",
    "39",
    "98",
    "12",
    "34",
    "https://www.example.org/in/camille-durand",
    "01/2023",
]


def cpu(action: Callable[[], object], repeat: int = 3) -> float:
    """The CPU seconds of one call, the best of `repeat`."""
    best = float("inf")
    for _ in range(repeat):
        started = time.process_time()
        action()
        best = min(best, time.process_time() - started)
    return best


def cv(size: int, seed: int = 7) -> str:
    """A text of `size` characters that reads like a long French dossier."""
    rng = random.Random(seed)  # noqa: S311 (test data, not a secret)
    out = []
    length = 0
    while length < size:
        word = rng.choice(WORDS)
        out.append(word)
        out.append("\n" if rng.random() < 0.05 else " ")
        length += len(word) + 1
    return "".join(out)[:size]


@pytest.fixture(scope="module")
def large() -> str:
    return cv(LARGE)


# --- the large document --------------------------------------------------------------------------


def test_a_document_of_500_000_characters_is_prepared_in_bounded_time(large: str) -> None:
    assert len(large) == LARGE
    elapsed = cpu(lambda: prepare(large))
    assert elapsed < PREPARE_BUDGET_S, f"{elapsed:.3f} s"


def test_an_excerpt_is_found_in_500_000_characters_in_bounded_time(large: str) -> None:
    document = prepare(large)
    middle = large.index(" ", LARGE // 2) + 1
    last = large.rindex(" ", 0, LARGE - 400) + 1
    for start in (0, middle, last):
        excerpt = large[start : large.index(" ", start + 300)]
        found = find(document, excerpt)
        assert isinstance(found, Span)
        assert citations.normalise(excerpt).text in found.text
        elapsed = cpu(lambda excerpt=excerpt: find(document, excerpt))
        assert elapsed < FIND_BUDGET_S, f"{elapsed:.3f} s"
    for absent in ("zèbre girafe quokka okapi wombat narval", large[1000:1300] + " zèbre"):
        assert find(document, absent) == Refusal("", Rule.EXCERPT_NOT_FOUND, "excerpt not found")
        elapsed = cpu(lambda absent=absent: find(document, absent))
        assert elapsed < FIND_BUDGET_S, f"{elapsed:.3f} s"


def test_an_item_is_checked_against_500_000_characters_in_bounded_time(large: str) -> None:
    document = prepare(large)
    rng = random.Random(11)  # noqa: S311 (test data, not a secret)
    excerpts = []
    for _ in range(citations.EXCERPTS_MAX):
        start = large.index(" ", rng.randrange(LARGE - 1000)) + 1
        excerpts.append(large[start : large.index(" ", start + 200)])
    types = {
        "organisation": "name",
        "title": "text",
        "start": "date",
        "email": "email",
        "url": "url",
        "phone": "phone",
        "terms[]": "term",
    }
    value = {
        "organisation": "Betclic",
        "title": "Développeur full-stack, 10 000 utilisateurs, 3,5 ans",
        "start": "2023-01",
        "email": "camille.durand@example.org",
        "url": "https://example.org/in/camille-durand",
        "phone": "06 39 98 12 34",
        "terms": ["python", "docker"] * 500,
    }
    result = check_item(types, value, document, excerpts)
    assert isinstance(result, Accepted | Refusal)
    elapsed = cpu(lambda: check_item(types, value, document, excerpts))
    assert elapsed < CHECK_BUDGET_S, f"{elapsed:.3f} s"
    # and without the prepared document, the same answer, in the time of one more pass
    assert check_item(types, value, large, excerpts[:2]) == check_item(
        types, value, document, excerpts[:2]
    )


def test_the_cost_grows_linearly_with_the_document() -> None:
    """Four times the text costs about four times the work. A quadratic scan would cost sixteen
    times: this is the test that the seed script would fail."""
    small, big = cv(LARGE // 4), cv(LARGE)

    def work(text: str) -> None:
        document = prepare(text)
        half = len(text) // 2
        start = text.index(" ", half) + 1
        for excerpt in (text[start : start + 300], text[half : half + 43], "zèbre " * 6):
            find(document, excerpt)
        check_item("text", "10 000 utilisateurs", document, [text[start : start + 200]])

    quarter, whole = cpu(lambda: work(small)), cpu(lambda: work(big))
    assert whole < PREPARE_BUDGET_S, f"{whole:.3f} s"
    assert whole / quarter < GROWTH_MAX, f"{quarter:.3f} s then {whole:.3f} s"


def test_the_memory_of_a_prepared_document_is_bounded(large: str) -> None:
    """N, S and their two maps: a few bytes per character, nothing kept per occurrence."""
    tracemalloc.start()
    try:
        document = prepare(large)
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    size = len(large) + len(document.text)
    assert peak < 48 * size, f"{peak / size:.1f} bytes per character at the peak"
    assert current < 24 * size, f"{current / size:.1f} bytes per character kept"
    assert document.origin.itemsize == document.skeleton_index.itemsize == 4


# --- crafted documents ---------------------------------------------------------------------------

UNITS = {
    "one letter": "a",
    "two letters": "ab",
    "letters and spaces": "a b ",
    "digits": "1",
    "numbers": "1 2 3 4 ",
    "decimals": "1.1,1'1 ",
    "phone": "06 39 98 12 34 ",
    "phone signs": "0(-/.)",
    "plus": "+33 (0)",
    "parentheses": "((((((",
    "months": "janvier ",
    "month chain": "juin à ",
    "month chain with days": "du 1er juin au 31 ",
    "short dates": "09/21 à ",
    "date chain": "01/02-",
    "years": "2019-",
    "emails": "a@a.a.",
    "address signs": "a./?#:=&~",
    "urls": "https://www.",
    "versions": "version 1.",
    "the v": "v",
    "instructions": "ignore all the ",
    "hyphens": "-",
    "spaced hyphens": " - ",
    "full stops": "x.",
    "spaces": " ",
    "line breaks": "a\n",
    "non breaking spaces": "\u00a0x",
    "apostrophes": "jusqu\u2019",
    "accents": "é",
    "combining accents": "e\u0301",
    "zalgo": "\u0301\u0316",
    "superscripts": "\u00b2",
    "fractions": "\u00bd",
    "ligature of eighteen": "\ufdfa",
    "sharp s": "\u00df",
    "soft hyphens": "a\u00ad",
    "bidi marks": "\u202e\u200fa",
    "controls": "\x00\x01a",
    "vertical tabs": "5\x0b",
    "chinese": "阿里巴巴集团担任高级软件工程师",
    "thai": "\u0e17\u0e35\u0e48\u0e19\u0e35\u0e48",
    "hangul in parts": "\u1112\u1161\u11ab",
    "emoji": "\U0001f600",
    "lone surrogates": chr(0xD800) + "a",
    "tags": "\U000e0041",
}


@pytest.mark.parametrize("unit", UNITS.values(), ids=UNITS.keys())
def test_a_crafted_document_costs_no_more_than_its_size(unit: str) -> None:
    """One unit repeated up to 200,000 characters: the worst a document can do to each scan
    (one giant token, an excerpt present everywhere, a number without end)."""
    text = unit * (CRAFTED // len(unit))
    elapsed = cpu(lambda: prepare(text), repeat=1)
    assert elapsed < PREPARE_BUDGET_S, f"prepare: {elapsed:.3f} s"
    document = prepare(text)
    middle = len(text) // 2
    excerpts = [
        text[middle : middle + 300],
        text[middle + 1 : middle + 44],
        unit * 20,
        "zèbre " * 6,
    ]
    for excerpt in excerpts:
        elapsed = cpu(lambda excerpt=excerpt: find(document, excerpt), repeat=1)
        assert elapsed < FIND_BUDGET_S, f"find: {elapsed:.3f} s"
    for field in ("email", "url", "name", "date", "phone", "text"):
        for value in ("2023-01", unit * 8, text[middle : middle + 60]):
            started = time.process_time()
            result = check_item(field, value, document, excerpts[:3])
            elapsed = time.process_time() - started
            assert isinstance(result, Accepted | Refusal)
            assert elapsed < CHECK_BUDGET_S, f"{field}: {elapsed:.3f} s"


@pytest.mark.parametrize("unit", ["a", "ab", "a b ", "1 2 3 4 ", "x.", "e\u0301", "-", "阿里"])
def test_an_excerpt_present_everywhere_is_not_scanned_everywhere(unit: str) -> None:
    """The quadratic case of the seed script: every occurrence cut a token, and each was looked
    at with every token of the document. Here the cost of the search does not depend on how
    many times the excerpt occurs."""
    excerpt = unit * (40 // len(unit))

    def work(size: int) -> float:
        document = prepare(unit * (size // len(unit)))
        return cpu(lambda: find(document, excerpt))

    quarter, whole = work(LARGE // 4), work(LARGE)
    assert whole < FIND_BUDGET_S, f"{whole:.4f} s"
    # too small to compare when both are a fraction of a millisecond
    assert whole < 0.02 or whole / quarter < GROWTH_MAX, f"{quarter:.4f} s then {whole:.4f} s"


def test_a_long_run_of_combining_marks_is_not_sorted_as_one() -> None:
    """Unicode normalisation sorts the marks of one character, and that sort is quadratic: a
    character with 250,000 marks would cost minutes. Clusters are cut at CLUSTER_MAX."""
    text = "a" + "\u0301\u0316" * (LARGE // 4)
    elapsed = cpu(lambda: prepare(text), repeat=1)
    assert elapsed < PREPARE_BUDGET_S, f"{elapsed:.3f} s"
    value = "e" + "\u0301\u0316" * 50_000
    source = "Compétences : Python, Docker, Kubernetes en production depuis trois ans."
    for field in ("text", "name", "email", "url", "phone", "date"):
        elapsed = cpu(lambda field=field: check_item(field, value, source, [source]), repeat=1)
        assert elapsed < CHECK_BUDGET_S, f"{field}: {elapsed:.3f} s"


# --- crafted values and excerpts -----------------------------------------------------------------

SOURCE = (
    "Camille Durand, camille.durand@example.org, +33 6 39 98 12 34, Betclic depuis janvier "
    "2023, version 1.10 du moteur, 10 000 utilisateurs, https://www.example.org/in/camille."
)
VALUES = {
    "numbers": "1 " * 100_000,
    "one number": "1" * 200_000,
    "groups": "1 000" + " 000" * 50_000,
    "decimals": "1.1," * 50_000,
    "versions": "v1.1 " * 40_000,
    "version words": "version " * 25_000,
    "email signs": "a@a." * 50_000,
    "slashes": "/" * 200_000,
    "url heads": "https://www." * 16_000,
    "letters": "a" * 200_000,
    "words": "a b " * 50_000,
    "the source many times": SOURCE * 1000,
    "instructions": "ignore all the " * 13_000,
    "almost an instruction": "as an a" * 28_000,
    "accents": "é" * 200_000,
    "combining": "e\u0301" * 100_000,
    "zalgo": "a" + "\u0301\u0316" * 100_000,
    "spaces": " " * 200_000,
    "hyphens": "-" * 200_000,
    "digits and signs": "0(-/.)" * 33_000,
    "months": "janvier 2023 " * 15_000,
    "chinese": "阿里巴巴" * 50_000,
    "lone surrogates": chr(0xDFFF) * 200_000,
}


@pytest.mark.parametrize("value", VALUES.values(), ids=VALUES.keys())
def test_a_crafted_value_of_200_000_characters_costs_no_more_than_its_size(value: str) -> None:
    document = prepare(SOURCE)
    for field in ("email", "url", "name", "date", "phone", "text", "term", "enum", "setting"):
        started = time.process_time()
        result = check_item(field, value, document, [SOURCE])
        elapsed = time.process_time() - started
        assert isinstance(result, Accepted | Refusal)
        assert elapsed < CHECK_BUDGET_S, f"{field}: {elapsed:.3f} s"


@pytest.mark.parametrize("excerpt", VALUES.values(), ids=VALUES.keys())
def test_a_crafted_excerpt_of_200_000_characters_is_refused_at_once(excerpt: str) -> None:
    document = prepare(SOURCE)
    started = time.process_time()
    result = check_item("text", "Betclic", document, [excerpt])
    elapsed = time.process_time() - started
    assert isinstance(result, Refusal)
    assert result.index == "excerpts[0]"
    assert elapsed < FIND_BUDGET_S, f"{elapsed:.3f} s"


def test_twenty_excerpts_and_two_thousand_parts() -> None:
    document = prepare(SOURCE * 50)
    excerpts = [SOURCE[: 40 + position] for position in range(citations.EXCERPTS_MAX)]
    value = {"body[]": ["10 000 utilisateurs depuis janvier 2023"] * citations.VALUE_LEAVES_MAX}
    types = {"body[][]": "text"}
    assert isinstance(check_item(types, value, document, excerpts), Refusal)
    flat = ["10 000 utilisateurs depuis janvier 2023"] * citations.VALUE_LEAVES_MAX
    result = check_item("text", flat, document, [SOURCE])
    assert isinstance(result, Accepted)
    elapsed = cpu(lambda: check_item("text", flat, document, [SOURCE] * citations.EXCERPTS_MAX))
    assert elapsed < CHECK_BUDGET_S, f"{elapsed:.3f} s"
    names = ["Betclic depuis janvier"] * citations.VALUE_LEAVES_MAX
    elapsed = cpu(lambda: check_item("name", names, document, excerpts), repeat=1)
    assert elapsed < 3 * CHECK_BUDGET_S, f"{elapsed:.3f} s"


def test_a_name_or_an_address_present_everywhere_in_a_span_is_bounded() -> None:
    """At most OCCURRENCES_MAX places of a span are looked at for one value."""
    letters = "ab " * 600
    document = prepare(letters)
    span = letters[: 3 * 190]
    for field, value in (("name", "ab " * 150 + "abab"), ("email", "ab"), ("url", "b ab")):
        result = check_item(field, value, document, [span] * citations.EXCERPTS_MAX)
        assert isinstance(result, Accepted | Refusal)
        elapsed = cpu(
            lambda field=field, value=value: check_item(
                field, value, document, [span] * citations.EXCERPTS_MAX
            )
        )
        assert elapsed < CHECK_BUDGET_S, f"{field}: {elapsed:.3f} s"


def test_a_deeply_nested_value_is_refused_without_recursion() -> None:
    nested: object = "Betclic"
    for _ in range(50_000):
        nested = [nested]
    assert check_item("text", nested, SOURCE, [SOURCE]) == Refusal(
        "value[0]", Rule.UNKNOWN_FIELD, citations.REASONS[Rule.UNKNOWN_FIELD]
    )
    deep: object = "Betclic"
    for _ in range(50_000):
        deep = {"a": deep}
    result = check_item({"a": "text"}, deep, SOURCE, [SOURCE])
    assert isinstance(result, Refusal)
    assert (result.index, result.rule) == ("value.a", Rule.UNKNOWN_FIELD)
    loop: list[object] = []
    loop.append(loop)
    assert isinstance(check_item("text", loop, SOURCE, [SOURCE]), Refusal)
    ring: dict[str, object] = {}
    ring["a"] = ring
    assert isinstance(check_item({"a": "text", "a.a": "text"}, ring, SOURCE, [SOURCE]), Refusal)


def test_a_value_wider_than_the_limit_is_not_walked() -> None:
    wide = ["x"] * 3_000_000
    started = time.process_time()
    result = check_item("text", wide, SOURCE, [SOURCE])
    elapsed = time.process_time() - started
    assert result == Refusal("value", Rule.VALUE_TOO_LARGE, citations.REASONS[Rule.VALUE_TOO_LARGE])
    assert elapsed < 0.5, f"{elapsed:.3f} s"


# --- regular expressions -------------------------------------------------------------------------

PATTERNS = {
    name: pattern for name, pattern in vars(citations).items() if isinstance(pattern, re.Pattern)
}
PATTERNS |= {f"_FRENCH[{position}]": pattern for position, pattern in enumerate(citations._FRENCH)}
# Each of these makes a regular expression with nested or overlapping repetitions backtrack.
ATTACKS = {
    "digits": "1" * 100_000 + "x",
    "numbers": "1 " * 50_000 + "x",
    "numbers with every separator": "1 1.1,1'1" * 11_000 + "x",
    "numbers that almost end": "1." * 50_000 + ".",
    "phone pairs": "06 39 98 12 3" * 7_000 + "x",
    "phone signs": "0(-/.)" * 16_000 + " x",
    "open parentheses": "(" * 100_000,
    "spaces and parentheses": "0 (" * 33_000 + "x",
    "plus signs": "+33" * 33_000,
    "french numbers that almost end": "+33 (0)6" + " 39" * 30_000 + "x",
    "pairs without end": "06" + ".39" * 30_000,
    "letters": "a" * 100_000 + "1",
    "words": "a b " * 25_000,
    "url heads": "https://www." * 8_000,
    "www": "www." * 25_000,
    "almost a url head": "http" * 25_000 + "s:/",
    "versions": "version " * 12_000 + "1",
    "the v": "v" * 100_000 + " 1",
    "almost a version": "versions : " * 9_000,
    "instructions": "ignore all the " * 6_000 + "previou",
    "almost an instruction": "as an a" * 14_000 + "i",
    "en tant": "en tant qu'" * 9_000,
    "spaces": " " * 100_000 + "x",
    "line breaks": "\n" * 100_000,
    "hyphens": "-" * 100_000,
    "accents": "é" * 100_000,
    "underscores": "_" * 100_000 + "1",
    "empty": "",
}


@pytest.mark.parametrize("name", sorted(PATTERNS))
def test_no_regular_expression_backtracks(name: str) -> None:
    """Every compiled pattern of the module, against inputs made to defeat it: each call ends
    in the time of one pass. A pattern added later is tested here without a line of code."""
    pattern = PATTERNS[name]
    for label, text in ATTACKS.items():
        started = time.process_time()
        pattern.search(text)
        pattern.match(text)
        pattern.fullmatch(text)
        for _ in pattern.finditer(text):
            pass
        pattern.sub("", text)
        elapsed = time.process_time() - started
        assert elapsed < 1.0, f"{name} on {label}: {elapsed:.3f} s"


def test_the_patterns_under_test_are_those_of_the_module() -> None:
    assert len(PATTERNS) >= 12
    assert {"_NUMBER_RUN", "_PHONE_RUN", "_DATE_TOKEN", "_INSTRUCTION", "_PLAIN"} <= set(PATTERNS)


@pytest.mark.parametrize("label", sorted(ATTACKS))
def test_the_readers_end_on_the_inputs_made_to_defeat_them(label: str) -> None:
    text = ATTACKS[label]
    started = time.process_time()
    norm = citations.normalise(text).text
    citations.skeleton(norm)
    citations.dates_of(norm)
    citations.phones_of(norm)
    citations.numbers_of(norm)
    citations.looks_like_instruction(norm)
    citations.readings(text)
    elapsed = time.process_time() - started
    assert elapsed < CHECK_BUDGET_S, f"{label}: {elapsed:.3f} s"
