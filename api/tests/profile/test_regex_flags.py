"""Global inline flags in advanced terms (bug found in P5). The matcher wraps every advanced term in
(?:...) and joins the list with |, and Python only accepts a global flag such as (?i) at the very
start of a whole pattern. A term like "(?m)x" compiled alone, passed check_regex, was saved, and
then broke compile_terms for the whole list ("global flags not at the start"). check_regex now
refuses a global inline flag anywhere, with a sentence that says what to write instead, and
scoped flags such as (?i:...) stay allowed."""

import re
from typing import Any

import pytest
from pydantic import ValidationError

from app.profile import schemas
from app.profile.matching import compile_terms, matches
from app.profile.schemas import (
    TermList,
    check_joined,
    check_regex,
    global_flags,
    group_names,
    joining_problem,
    validate_section,
)
from tests.identity.api_helpers import Api
from tests.profile.test_api import read, save

GLOBAL = [
    ("(?i)llm", 0, "(?i)"),
    ("(?m)^ai$", 0, "(?m)"),
    ("(?s)a.b", 0, "(?s)"),
    ("(?x) a b", 0, "(?x)"),
    ("(?a)\\w+", 0, "(?a)"),
    ("(?u)\\w", 0, "(?u)"),
    ("(?im)x", 0, "(?im)"),
    ("(?imsx)x", 0, "(?imsx)"),
    ("(?L)x", 0, "(?L)"),  # does not even compile on str, still reported as a flag first
    ("ab(?i)c", 2, "(?i)"),  # not at the start: Python refuses it alone too
    ("(?:(?i)x)", 3, "(?i)"),
    ("(x)|(?s)y", 4, "(?s)"),
    ("[a](?m)", 3, "(?m)"),
    ("\\((?i)", 2, "(?i)"),  # escaped parenthesis first, then a real flag group
]


@pytest.mark.parametrize(("pattern", "position", "flag"), GLOBAL)
def test_global_flags_are_refused_with_position_and_advice(
    pattern: str, position: int, flag: str
) -> None:
    found = global_flags(pattern)
    assert found is not None
    assert found.start() == position
    with pytest.raises(ValueError, match="inline flags") as caught:
        check_regex(pattern)
    message = str(caught.value)
    assert message.startswith(f"invalid regular expression at position {position}: ")
    assert f"inline flags such as {flag} are not allowed" in message
    assert "already case-insensitive" in message
    assert "(?s:...)" in message
    for dash in (" - ", "\u2014", "\u2013"):
        assert dash not in message


SCOPED_OR_LOOKALIKE = [
    "(?i:llm)",
    "(?-i:LLM)",
    "(?i-m:x)",
    "(?s:a.b)",
    "(?x: a b )",
    "(?a:\\w+)",
    "(?ms:^a.$)",
    "[(?i)]",  # inside a class, literal characters
    "[]?(i)]x",  # class opening with ]
    "\\(?i\\)",  # escaped: an optional literal ( then "i)"
    "(?:x)",
    "(?P<name>a)(?P=name)",
    "(?#comment)a",
    "(?=a)a",
    "(?<!b)a",
    "(?i:a)|(?s:b)",
]


@pytest.mark.parametrize("pattern", SCOPED_OR_LOOKALIKE)
def test_scoped_flags_and_lookalikes_pass(pattern: str) -> None:
    assert global_flags(pattern) is None
    assert check_regex(pattern) == pattern


def test_a_list_of_accepted_terms_always_compiles() -> None:
    terms = [*SCOPED_OR_LOOKALIKE, r"fine[- ]?tun\w*", r"\b(staff|principal)\b"]
    for term in terms:
        check_regex(term)
    pattern = compile_terms(terms, advanced_regex=True)
    assert pattern is not None
    assert pattern.flags & re.IGNORECASE


def test_the_old_bug_is_what_the_check_prevents() -> None:
    """Before the fix (?m)x compiled alone yet broke the joined list."""
    re.compile("(?m)x", re.IGNORECASE)
    with pytest.raises(re.error, match="global flags not at the start"):
        compile_terms(["a", "(?m)x"], advanced_regex=True)


def test_scoped_flags_match_as_expected() -> None:
    assert matches(["(?s:a.b)"], True, "a\nb")
    assert not matches(["a.b"], True, "a\nb")
    assert matches(["(?-i:LLM)"], True, "LLM")
    assert not matches(["(?-i:LLM)"], True, "llm")
    assert matches(["(?x: l l m )"], True, "LLM")


def test_existing_kinds_of_terms_are_unaffected() -> None:
    """The engine's own patterns (boards.json, skills.json) use only (?:...) groups."""
    for pattern in [
        r"\b(ai|ml|llm|genai)\b.*engineer",
        r"(?:forward|solutions?)[ -]deployed",
        r"\bml\b",
        r"^ai engineer$",
        r"z(ü|u)rich",
        r"(?:ab){2}",
    ]:
        assert check_regex(pattern) == pattern


def _first_error(section: str, data: dict[str, Any]) -> tuple[str, str]:
    with pytest.raises(ValidationError) as caught:
        validate_section(section, data)
    error = caught.value.errors()[0]
    return ".".join(str(part) for part in error["loc"]), str(error["msg"])


@pytest.mark.parametrize(
    ("section", "data", "where"),
    [
        (
            "search",
            {"title_include": {"terms": ["ok", "(?m)x"], "advanced_regex": True}},
            "title_include.terms",
        ),
        (
            "search",
            {"title_exclude": {"terms": ["(?i)cyber"], "advanced_regex": True}},
            "title_exclude.terms",
        ),
        (
            "search",
            {"location": {"terms": ["(?s)paris"], "advanced_regex": True}},
            "location.terms",
        ),
        (
            "skills",
            {
                "gates": [
                    {"name": "PhD", "terms": ["(?i)phd"], "advanced_regex": True, "penalty": 25}
                ]
            },
            "gates.0.terms",
        ),
    ],
)
def test_each_section_refuses_a_global_flag(section: str, data: dict[str, Any], where: str) -> None:
    loc, msg = _first_error(section, data)
    assert loc == where
    assert "inline flags such as" in msg


def test_plain_terms_may_contain_flag_text() -> None:
    """A plain term is escaped, so "(?i)" is just characters there."""
    validate_section("search", {"title_include": {"terms": ["(?i)"], "advanced_regex": False}})


def test_api_save_is_a_422_with_one_sentence(api: Api, alice: str) -> None:
    data = {"title_include": {"terms": ["agent", "(?m)^x"], "advanced_regex": True}}
    response = save(api, alice, "search", data, 0)
    assert response.status_code == 422
    assert response.json() == {
        "detail": "data.title_include.terms: term 2: invalid regular expression at position 0: "
        "inline flags such as (?m) are not allowed. Matching is already case-insensitive; to "
        "apply a flag to part of the term, scope it as a group such as (?s:...)"
    }
    assert read(api, alice)["revision"] == 0


def test_api_save_of_scoped_flags_succeeds(api: Api, alice: str) -> None:
    data = {"title_include": {"terms": ["(?i:agent)", "(?s:a.b)"], "advanced_regex": True}}
    response = save(api, alice, "search", data, 0)
    assert response.status_code == 200, response.text


# --- review of P6: comments, numbered references, group names across the list -------------------


@pytest.mark.parametrize("pattern", ["(?#x(?i)", "(?#(?m))a", "a(?#[)b", "(?#\\)x"])
def test_comments_are_skipped_by_the_scanner(pattern: str) -> None:
    assert global_flags(pattern) is None
    assert joining_problem(pattern) is None


def test_a_flag_after_a_comment_is_still_found() -> None:
    found = global_flags("(?#[)(?i)x")
    assert found is not None
    assert found.start() == 5


@pytest.mark.parametrize(
    ("pattern", "position", "shown"),
    [
        (r"(a)\1", 3, r"\1"),
        (r"(a)(b)(c)(d)(e)(f)(g)(h)(i)(j)\10", 30, r"\10"),
        (r"(ab)\2", 4, r"\2"),
        (r"x(a)\1y", 4, r"\1"),
        (r"(a)\18", 3, r"\18"),  # 8 is not octal: a group reference
    ],
)
def test_numbered_backreferences_are_refused(pattern: str, position: int, shown: str) -> None:
    problem = joining_problem(pattern)
    assert problem is not None
    assert problem[0] == position
    assert f"numbered backreferences such as {shown} are not allowed" in problem[1]
    with pytest.raises(ValueError, match=rf"^invalid regular expression at position {position}: "):
        check_regex(pattern)


@pytest.mark.parametrize(
    "pattern",
    [r"\101", r"\0", r"\012", r"[\1]", r"\\1", r"(?P<w>a)(?P=w)", r"(?P<w>a)?(?(w)b|c)", r"\d\w"],
)
def test_octal_escapes_classes_and_named_references_pass(pattern: str) -> None:
    assert joining_problem(pattern) is None
    assert check_regex(pattern) == pattern


@pytest.mark.parametrize(("pattern", "position"), [(r"(a)?(?(1)b|c)", 4), (r"(?(12)x)", 0)])
def test_numbered_conditions_are_refused(pattern: str, position: int) -> None:
    problem = joining_problem(pattern)
    assert problem is not None
    assert problem[0] == position
    assert "conditions on a numbered group" in problem[1]
    assert "(?(word)...)" in problem[1]


def test_the_old_backreference_bug_is_what_the_check_prevents() -> None:
    """Joined, term 2's \\1 points at term 1's group and matches the wrong thing, silently."""
    joined = compile_terms(["(x)", r"(a)\1"], advanced_regex=True)
    assert joined is not None
    assert joined.search("aa") is None  # alone, (a)\1 matches "aa"
    assert re.search(r"(a)\1", "aa") is not None


def test_group_names_lists_named_groups_outside_classes_and_comments() -> None:
    assert group_names(r"(?P<a>x)(?:y)(?P<b2>z)[(?P<c>)](?#(?P<d>))") == ["a", "b2"]


def test_duplicate_group_names_across_terms_are_refused() -> None:
    with pytest.raises(ValidationError) as caught:
        TermList.model_validate({"terms": ["ok", "(?P<n>a)", "(?P<n>b)"], "advanced_regex": True})
    message = caught.value.errors()[0]["msg"]
    assert message == (
        'Value error, term 3: the group name "n" is already used by term 2; '
        "group names must be unique across the list"
    )


def test_distinct_group_names_and_references_combine() -> None:
    terms = ["(?P<a>x)(?P=a)", "(?P<b>y)?(?(b)z|w)", "plain"]
    validated = TermList.model_validate({"terms": terms, "advanced_regex": True})
    pattern = compile_terms(validated.terms, advanced_regex=True)
    assert pattern is not None
    assert pattern.search("xx") is not None
    assert pattern.search("yz") is not None


def test_check_joined_catches_any_other_combine_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(terms: object, advanced_regex: bool) -> None:
        raise re.error("too many groups")

    monkeypatch.setattr(schemas, "compile_terms", broken)
    with pytest.raises(
        ValueError, match=r"^the terms do not combine into one expression: too many"
    ):
        check_joined(["a", "b"])


def test_plain_lists_are_never_checked_as_a_whole() -> None:
    TermList.model_validate({"terms": ["(?P<n>a)", "(?P<n>b)"], "advanced_regex": False})


def test_api_refuses_duplicate_group_names_with_one_sentence(api: Api, alice: str) -> None:
    data = {
        "have": [{"name": "x", "terms": ["(?P<n>a)", "(?P<n>b)"], "advanced_regex": True}],
    }
    response = save(api, alice, "skills", data, 0)
    assert response.status_code == 422
    assert response.json() == {
        "detail": 'data.have.0.terms: term 2: the group name "n" is already used by term 1; '
        "group names must be unique across the list"
    }


def test_api_refuses_a_numbered_backreference(api: Api, alice: str) -> None:
    data = {"title_exclude": {"terms": [r"(a)\1"], "advanced_regex": True}}
    response = save(api, alice, "search", data, 0)
    assert response.status_code == 422
    assert response.json()["detail"].startswith(
        "data.title_exclude.terms: term 1: invalid regular expression at position 3: "
        "numbered backreferences such as \\1 are not allowed"
    )
