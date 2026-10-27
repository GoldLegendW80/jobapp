"""The plain words of a saved regular expression (app/profile/plain_terms.py, owner 2026-10-28):
what a website search types for a search list saved as expressions before the setup stopped
asking for them. Pure, no database."""

import pytest

from app.profile import matching
from app.profile.plain_terms import PLAIN, VARIANTS_MAX, plain_words, typed_words
from app.profile.schemas import TermList

ACCEPTED = [
    # The owner's own saved shapes.
    ("forward.deployed", ["forward deployed", "forward-deployed"]),
    ("solutions? architect", ["solutions architect", "solution architect"]),
    (r"\bpentest\b", ["pentest"]),
    # Edges: anchors and word boundaries add nothing to a whole word.
    ("^head of ai$", ["head of ai"]),
    (r"\b\bml engineer\b", ["ml engineer"]),
    ("  data engineer  ", ["data engineer"]),
    # Separators.
    (r"forward\sdeployed", ["forward deployed"]),
    (r"forward\s+deployed", ["forward deployed"]),
    (r"forward\s*deployed", ["forward deployed", "forwarddeployed"]),
    ("forward.?deployed", ["forward deployed", "forward-deployed", "forwarddeployed"]),
    ("data[ -]scientist", ["data scientist", "data-scientist"]),
    (r"data[\s-]scientist", ["data scientist", "data-scientist"]),
    ("gen ?ai", ["gen ai", "genai"]),
    (r"full\-stack", ["full-stack"]),
    ("full-stack", ["full-stack"]),
    (r"o\'brien", ["o'brien"]),
    # Optional parts and alternatives.
    ("engineers?", ["engineer"]),
    ("(ML|AI) engineer", ["ML engineer", "AI engineer"]),
    ("(?:applied )?ai engineer", ["applied ai engineer", "ai engineer"]),
    ("^head of (ai|data)$", ["head of ai", "head of data"]),
    ("paris|lyon", ["paris", "lyon"]),
    ("r[eé]sum[eé]", ["resume", "resumé", "résume", "résumé"]),
    ("analy[sz]t", ["analyst", "analyzt"]),
    ("boxes|box", ["box"]),
    # Plain words stay themselves, accents, digits and case included.
    ("machine learning", ["machine learning"]),
    ("Ingénieur IA", ["Ingénieur IA"]),
    ("web3", ["web3"]),
    ("Île-de-France", ["Île-de-France"]),
]

REFUSED = [
    "forward.*engineer",
    "forward.+engineer",
    r"\w+ engineer",
    r"\d+ years",
    "a+",
    "engineers*",
    "ai{2}",
    "[^a] engineer",
    "[a-z]+ engineer",
    "[abcde]",
    r"c\+\+",
    r"node\.js",
    "node.js.",
    "(?i)ai",
    "(?=ai)ai",
    "(?P<x>ai)",
    r"(ai)\1",
    r"ml\bops",
    "ai|",
    "|ai",
    "(ai",
    "ai)",
    "",
    "^$",
    "forward .deployed",
    "machine_learning",
    "C#",
    "(a|b)(c|d)(e|f)(g|h)",
    "engineer??",
    "engineer?+",
    "ai.",
    ".ai",
]


@pytest.mark.parametrize(("pattern", "words"), ACCEPTED)
def test_a_safe_expression_gives_its_plain_words(pattern: str, words: list[str]) -> None:
    assert plain_words(pattern) == words


@pytest.mark.parametrize(("pattern", "words"), ACCEPTED)
def test_each_word_is_one_a_website_search_types_and_the_expression_finds(
    pattern: str, words: list[str]
) -> None:
    for word in words:
        assert PLAIN.fullmatch(word)
        if not pattern.strip().endswith("s?"):  # "engineers?" keeps "engineer" for both
            assert matching.matches([pattern.strip()], True, word)


@pytest.mark.parametrize("pattern", REFUSED)
def test_an_expression_that_is_not_safe_gives_nothing(pattern: str) -> None:
    assert plain_words(pattern) is None


def test_too_many_words_is_not_safe() -> None:
    assert plain_words("(a|b|c|d|e|f|g|h)x") == [f"{c}x" for c in "abcdefgh"]
    assert len(plain_words("(a|b|c|d|e|f|g|h)x") or []) == VARIANTS_MAX
    assert plain_words("(a|b|c|d|e|f|g|h|i)x") is None


def test_a_huge_expression_is_refused_quickly() -> None:
    assert plain_words("ab" * 300 + ".*") is None
    assert plain_words("(" * 200 + "ai" + ")" * 200) is None
    assert plain_words("((ai))") == ["ai"]


def test_a_plain_list_is_typed_as_saved() -> None:
    terms = TermList(terms=["C++ developer", "forward.deployed"])
    assert typed_words(terms) == (["C++ developer", "forward.deployed"], 0)


def test_a_list_of_expressions_types_the_words_of_each_safe_one_and_counts_the_rest() -> None:
    terms = TermList(
        advanced_regex=True,
        terms=["forward.deployed", "solutions? architect", r"\w+ops", "forward deployed"],
    )
    words, dropped = typed_words(terms)
    assert words == [
        "forward deployed",
        "forward-deployed",
        "solutions architect",
        "solution architect",
    ]
    assert dropped == 1
    # Nothing is rewritten: the saved list still reads as the expressions it holds.
    assert terms.advanced_regex is True
    assert terms.terms[2] == r"\w+ops"


def test_a_list_of_expressions_none_safe_types_nothing_and_counts_them_all() -> None:
    assert typed_words(TermList(advanced_regex=True, terms=[r"\w+ops", "a.*b"])) == ([], 2)
