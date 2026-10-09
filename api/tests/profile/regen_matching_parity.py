"""Rewrite the expected values of fixtures/matching_parity.json from the Python implementation, the
judge. Run after changing matching.py or the regex checks of schemas.py, then run both parity
tests (pytest tests/profile/test_matching_parity.py, vitest matching.test.ts):

    uv run --directory api python -m tests.profile.regen_matching_parity

The inputs (terms, texts, patterns) are kept as they are; new cases are added by editing the
JSON or EXTRA below, with any expected value, and running this script."""

import json
from pathlib import Path
from typing import Any

from app.profile.matching import matches
from app.profile.schemas import TermList, check_regex, has_nested_quantifier

FIXTURE = Path(__file__).parent / "fixtures" / "matching_parity.json"

# Global inline flags (refused since P6) and scoped ones (allowed), for both sides.
EXTRA_CHECK_REGEX = [
    "(?m)x",
    "(?s)a.b",
    "(?a)\\w",
    "(?u)\\w",
    "(?im)x",
    "ab(?i)c",
    "(?:(?i)x)",
    "(x)|(?s)y",
    "(?i:llm)",
    "(?-i:LLM)",
    "(?s:a.b)",
    "(?x: a b)",
    "[(?i)]",
    "\\(?i\\)",
    # Comments are skipped by the scanner; numbered references are refused (review of P6).
    "(?#x(?i)",
    "(?#[)(?i)x",
    "(a)\\2",
    "x(a)\\1y",
    "(a)\\18",
    "\\101",
    "\\0",
    "[\\1]",
    "\\\\1",
    "(a)?(?(1)b|c)",
    "(?P<w>a)?(?(w)b|c)",
    "(?P<w>a)(?P=w)",
]

# Whole lists of advanced terms: each term valid alone, the list valid or not once joined.
EXTRA_JOINED = [
    ["(?P<n>a)", "(?P<n>b)"],
    ["ok", "(?P<n>a)", "x", "(?P<n>b)"],
    ["(?P<a>x)", "(?P<b>y)"],
    ["(?P<a>x)(?P=a)", "(?P<b>y)?(?(b)z|w)"],
    ["[(?P<n>)]", "(?P<n>a)"],
    ["(?#P<n>)a", "(?P<n>a)"],
    ["agent", "llm"],
]


def _valid(pattern: str) -> bool:
    try:
        check_regex(pattern)
    except ValueError:
        return False
    return True


def _joined_valid(terms: list[str]) -> bool:
    try:
        TermList.model_validate({"terms": terms, "advanced_regex": True})
    except ValueError:
        return False
    return True


def regenerate(document: dict[str, Any]) -> dict[str, Any]:
    joined = document.setdefault("joined", [])
    known_lists = [case["terms"] for case in joined]
    for terms in EXTRA_JOINED:
        if terms not in known_lists:
            joined.append({"terms": terms, "valid": False})
    for case in joined:
        case["valid"] = _joined_valid(case["terms"])
    known = {case["pattern"] for case in document["check_regex"]}
    for pattern in EXTRA_CHECK_REGEX:
        if pattern not in known:
            document["check_regex"].append({"pattern": pattern, "valid": False})
    for case in document["matching"]:
        case["expected"] = matches(case["terms"], case["advanced_regex"], case["text"])
    for case in document["nested_quantifier"]:
        case["expected"] = has_nested_quantifier(case["pattern"])
    for case in document["check_regex"]:
        case["valid"] = _valid(case["pattern"])
    return document


def main() -> None:
    document = regenerate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    FIXTURE.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
