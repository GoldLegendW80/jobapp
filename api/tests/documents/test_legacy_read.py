"""D15, the pure part: a legacy `cv.json` or `letter.json` read as content (`legacy.read`).

Tables in both directions: the files that must come through whole (true inputs untouched), and
the near misses that must be left out and noted, never accepted and never guessed: an employer
key is kept exactly or left out, a role always leaves the content for the notes, anything the
content model does not name is noted `legacy_invalid`. All files are invented.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from app.documents import content as c
from app.documents import legacy, rules
from app.documents.models import Kind
from tests.documents.conftest import load, written

KEYS = ("example-corp", "sample-labs")
VALID_CV = load("legacy/cv_valid.json")
VALID_LETTER = load("legacy/letter_valid.json")


def raw(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode()


def cv(**fields: Any) -> dict[str, Any]:
    return {**copy.deepcopy(VALID_CV), **fields}


def entry(key: str = "example-corp", **fields: Any) -> dict[str, Any]:
    return {"employer_key": key, "bullets": ["Designed the deployment pipeline."], **fields}


# --- true inputs: kept whole --------------------------------------------------------------------

UNTOUCHED_CVS = [
    pytest.param(written("cv_typical"), id="the app's own content"),
    pytest.param(written("cv_short"), id="a short one"),
    pytest.param(written("cv_long"), id="a long one"),
    pytest.param({}, id="empty object"),
    pytest.param({"schema": 1}, id="schema 1 stated"),
    pytest.param({"headline": "Ingénieure plateforme, déploiements sereins"}, id="French"),
    pytest.param({"headline": "Platform engineer, observability 🛰"}, id="emoji"),
    pytest.param({"headline": "  Platform   engineer  "}, id="spaces normalised, not noted"),
    pytest.param({"summary": "Line one.\r\nLine two."}, id="CRLF in a multiline field"),
    pytest.param({"highlights": ["a", "b", "c"]}, id="highlights at the maximum"),
    pytest.param({"competencies": ["x"] * 8}, id="competencies at the maximum"),
    pytest.param({"experience": [entry(), entry("sample-labs")]}, id="two known employers"),
    pytest.param({"experience": [entry(bullets=[])]}, id="an entry with no bullet"),
    pytest.param({"experience": [entry(bullets=["b"] * 8)]}, id="bullets at the maximum"),
    pytest.param({"skills": [{"label": "Languages", "items": []}]}, id="a group with no item"),
    pytest.param({"notes": "For me only."}, id="notes of the file"),
    pytest.param({"target": ""}, id="an empty line field"),
    pytest.param({"summary": "s" * c.SUMMARY_MAX}, id="summary at its hard limit"),
    pytest.param(
        {"experience": [entry(bullets=["b" * c.BULLET_MAX])]}, id="bullet at its hard limit"
    ),
    pytest.param({"headline": "A <script>alert(1)</script> headline"}, id="markup is text"),
    pytest.param(
        {"experience": [entry("sample-labs"), entry("example-corp")]}, id="file order kept"
    ),
]


@pytest.mark.parametrize("data", UNTOUCHED_CVS)
def test_a_valid_cv_without_role_comes_through_whole(data: dict[str, Any]) -> None:
    reading = legacy.read(Kind.cv, raw(data), KEYS)
    assert reading.valid, reading.left_out
    assert reading.content == c.parse("cv", data)
    assert (reading.roles, reading.unknown_employers) == (0, ())


UNTOUCHED_LETTERS = [
    pytest.param(VALID_LETTER, id="the invented legacy letter"),
    pytest.param(written("letter_typical"), id="the app's own letter"),
    pytest.param(written("letter_fr"), id="a French letter"),
    pytest.param({"paragraphs": ["One."]}, id="one paragraph"),
    pytest.param({"paragraphs": ["p"] * 5}, id="paragraphs at the maximum"),
    pytest.param({}, id="empty object"),
]


@pytest.mark.parametrize("data", UNTOUCHED_LETTERS)
def test_a_valid_letter_comes_through_whole(data: dict[str, Any]) -> None:
    reading = legacy.read(Kind.letter, raw(data), KEYS)
    assert reading.valid, reading.left_out
    assert reading.content == c.parse("letter", data)


def test_a_byte_order_mark_is_read() -> None:
    reading = legacy.read(Kind.letter, b"\xef\xbb\xbf" + raw(VALID_LETTER), KEYS)
    assert reading.valid
    assert reading.content == c.parse("letter", VALID_LETTER)


# --- Decision 7: the role leaves the content for the notes --------------------------------------


def test_the_invented_legacy_cv_moves_each_role_to_notes() -> None:
    reading = legacy.read(Kind.cv, raw(VALID_CV), KEYS)
    assert reading.valid
    assert reading.roles == 2
    assert isinstance(reading.content, c.CvContent)
    assert [r.employer_key for r in reading.content.experience] == ["example-corp", "sample-labs"]
    assert reading.content.notes == (
        "Role in the imported CV for example-corp: Senior Platform Engineer\n"
        "Role in the imported CV for sample-labs: Software Engineer"
    )
    dumped = c.dump(reading.content)
    assert all("role" not in item for item in dumped["experience"])


ROLES = [
    # (role value, notes of the file, expected notes, roles counted, left out)
    pytest.param("Lead", None, "Role in the imported CV for example-corp: Lead", 1, (), id="text"),
    pytest.param("", None, "", 0, (), id="empty: nothing to keep"),
    pytest.param("   ", None, "", 0, (), id="blank: nothing to keep"),
    pytest.param(
        "Lead\nof\tplatform",
        None,
        "Role in the imported CV for example-corp: Lead of platform",
        1,
        (),
        id="newlines and tabs: one line",
    ),
    pytest.param(
        "Responsable plateforme",
        "Mine.",
        "Mine.\n\nRole in the imported CV for example-corp: Responsable plateforme",
        1,
        (),
        id="after the file's notes",
    ),
    pytest.param(
        "Lead",
        "  ",
        "Role in the imported CV for example-corp: Lead",
        1,
        (),
        id="blank notes of the file",
    ),
    pytest.param(7, None, "", 0, (("experience.0.role", "wrong_type"),), id="a number"),
    pytest.param(None, None, "", 0, (("experience.0.role", "wrong_type"),), id="null"),
    pytest.param(["Lead"], None, "", 0, (("experience.0.role", "wrong_type"),), id="a list"),
    pytest.param(
        "Lead",
        ["not", "text"],
        "Role in the imported CV for example-corp: Lead",
        1,
        (("notes", "wrong_type"),),
        id="notes of the file not text",
    ),
]


@pytest.mark.parametrize(("role", "notes", "expected", "count", "left"), ROLES)
def test_a_role_is_never_content(
    role: Any, notes: Any, expected: str, count: int, left: tuple[tuple[str, str], ...]
) -> None:
    data: dict[str, Any] = {"experience": [entry(role=role)]}
    if notes is not None:
        data["notes"] = notes
    reading = legacy.read(Kind.cv, raw(data), KEYS)
    assert isinstance(reading.content, c.CvContent)
    assert reading.content.notes == expected
    assert (reading.roles, reading.left_out) == (count, left)
    assert [r.employer_key for r in reading.content.experience] == ["example-corp"]


def test_roles_too_long_for_the_notes_are_noted_not_cut() -> None:
    data = {"experience": [entry(role="r" * c.NOTES_MAX)]}
    reading = legacy.read(Kind.cv, raw(data), KEYS)
    assert reading.left_out == (("notes", "too_long"),)
    assert isinstance(reading.content, c.CvContent)
    assert reading.content.notes == ""


def test_a_letter_has_no_role_rule() -> None:
    reading = legacy.read(Kind.letter, raw({**VALID_LETTER, "role": "Lead"}), KEYS)
    assert reading.left_out == (("role", "unknown_field"),)


# --- employer keys: exact, kept and noted, never guessed ----------------------------------------

KEY_CASES = [
    # (key in the file, kept as written, noted unknown, left out with this code)
    pytest.param("example-corp", True, False, None, id="exact"),
    pytest.param("sample-labs", True, False, None, id="exact, second"),
    pytest.param("example-corp-2", True, True, None, id="a longer key"),
    pytest.param("example", True, True, None, id="a shorter key"),
    pytest.param("examplecorp", True, True, None, id="glued"),
    pytest.param("sample-lab", True, True, None, id="singular"),
    pytest.param("example-crop", True, True, None, id="a typo"),
    pytest.param("corp-example", True, True, None, id="other word order"),
    pytest.param("harbor-sample-works", True, True, None, id="an employer not in the profile"),
    pytest.param("Example-Corp", False, False, "bad_format", id="upper case"),
    pytest.param("example_corp", False, False, "bad_format", id="underscore"),
    pytest.param("example corp", False, False, "bad_format", id="space"),
    pytest.param("exemple-société", False, False, "bad_format", id="accents"),
    pytest.param("ｅｘａｍｐｌｅ-corp", False, False, "bad_format", id="full width"),
    pytest.param("", False, False, "empty", id="empty"),
]


@pytest.mark.parametrize(("key", "kept", "unknown", "code"), KEY_CASES)
def test_an_employer_key_is_kept_exactly_or_left_out(
    key: str, kept: bool, unknown: bool, code: str | None
) -> None:
    reading = legacy.read(Kind.cv, raw({"experience": [entry(key)]}), KEYS)
    assert isinstance(reading.content, c.CvContent)
    keys = [r.employer_key for r in reading.content.experience]
    assert keys == ([key] if kept else [])
    noted = [(i.field, i.code) for i in reading.unknown_employers]
    assert noted == ([("experience.0.employer_key", rules.UNKNOWN_EMPLOYER)] if unknown else [])
    if code is None:
        assert reading.left_out == ()
    else:
        assert [(path.split(".")[:2], why) for path, why in reading.left_out] == [
            (["experience", "0"], code)
        ]


def test_a_key_of_a_profile_with_no_employer_is_noted() -> None:
    reading = legacy.read(Kind.cv, raw(VALID_CV), ())
    assert [i.field for i in reading.unknown_employers] == [
        "experience.0.employer_key",
        "experience.1.employer_key",
    ]
    assert reading.valid  # kept: a note, not a change


def test_the_unmatched_fixture_keeps_its_entry_and_notes_it() -> None:
    reading = legacy.read(Kind.cv, load_raw("cv_unmatched"), KEYS)
    assert isinstance(reading.content, c.CvContent)
    assert [r.employer_key for r in reading.content.experience] == [
        "example-corp",
        "harbor-sample-works",
    ]
    assert [(i.field, i.code, i.level) for i in reading.unknown_employers] == [
        ("experience.1.employer_key", rules.UNKNOWN_EMPLOYER, "invalid")
    ]
    assert reading.content.notes == "Role in the imported CV for harbor-sample-works: Intern"


# --- near misses: left out and noted, the rest kept ---------------------------------------------


def load_raw(name: str) -> bytes:
    return raw(load(f"legacy/{name}.json"))


INVALID_CVS = [
    # (data, the kept content, left out)
    pytest.param(
        {"headline": "H", "photo": "x"},
        {"headline": "H"},
        (("photo", "unknown_field"),),
        id="an unknown field",
    ),
    pytest.param(
        {"headline": "H", "kind": "cv", "language": "en"},
        {"headline": "H"},
        (("kind", "unknown_field"), ("language", "unknown_field")),
        id="the fixture wrapper's fields",
    ),
    pytest.param({"headline": 3}, {}, (("headline", "wrong_type"),), id="a number for text"),
    pytest.param({"headline": None}, {}, (("headline", "wrong_type"),), id="null for text"),
    pytest.param(
        {"headline": "h" * (c.HEADLINE_MAX + 1)}, {}, (("headline", "too_long"),), id="too long"
    ),
    pytest.param({"headline": "a\x00b"}, {}, (("headline", "control_character"),), id="NUL"),
    pytest.param({"schema": 2}, {}, (("schema", "unknown_value"),), id="another schema"),
    pytest.param({"schema": "1"}, {}, (("schema", "unknown_value"),), id="schema as text"),
    pytest.param(
        {"highlights": ["a", "b", "c", "d"]},
        {"highlights": ["a", "b", "c"]},
        (("highlights.3", "too_many"),),
        id="one highlight too many",
    ),
    pytest.param(
        {"highlights": ["a", "", "b"]},
        {"highlights": ["a", "b"]},
        (("highlights.1", "empty"),),
        id="an empty highlight",
    ),
    pytest.param(
        {"highlights": "a, b"}, {}, (("highlights", "wrong_type"),), id="a text for a list"
    ),
    pytest.param(
        {"competencies": [1, "Python"]},
        {"competencies": ["Python"]},
        (("competencies.0", "wrong_type"),),
        id="a number in a list",
    ),
    pytest.param(
        {"experience": [entry(), entry()]},
        {"experience": [entry()]},
        (("experience.1", "duplicate_employer"),),
        id="the same employer twice",
    ),
    pytest.param(
        {"experience": [entry(bullets=["b" * (c.BULLET_MAX + 1)]), entry("sample-labs")]},
        {"experience": [entry("sample-labs")]},
        (("experience.0.bullets.0", "too_long"),),
        id="a bullet over its limit",
    ),
    pytest.param(
        {"experience": [entry(bullets=["b"] * 9)]},
        {},
        (("experience.0.bullets", "too_many"),),
        id="nine bullets",
    ),
    pytest.param(
        {"experience": [{"key": "example-corp", "bullets": ["b"]}]},
        {},
        (("experience.0.employer_key", "required"),),
        id="another name for the key",
    ),
    pytest.param(
        {"experience": [entry(title="Lead")]},
        {},
        (("experience.0.title", "unknown_field"),),
        id="title is not the role field",
    ),
    pytest.param(
        {"experience": ["example-corp"]}, {}, (("experience.0", "wrong_type"),), id="a bare key"
    ),
    pytest.param(
        {"skills": [{"label": "", "items": ["Go"]}, {"label": "Data", "items": ["SQL"]}]},
        {"skills": [{"label": "Data", "items": ["SQL"]}]},
        (("skills.0.label", "empty"),),
        id="a skills group without label",
    ),
    pytest.param(
        {"fixed": {"employers": []}}, {}, (("fixed", "unknown_field"),), id="a base CV's block"
    ),
]


@pytest.mark.parametrize(("data", "kept", "left"), INVALID_CVS)
def test_what_does_not_validate_is_left_out_and_noted(
    data: dict[str, Any], kept: dict[str, Any], left: tuple[tuple[str, str], ...]
) -> None:
    reading = legacy.read(Kind.cv, raw(data), KEYS)
    assert reading.left_out == left
    assert not reading.valid
    assert reading.content == c.parse("cv", kept)


def test_the_invalid_fixture_keeps_every_valid_part() -> None:
    reading = legacy.read(Kind.cv, load_raw("cv_invalid"), KEYS)
    assert reading.left_out == (
        ("experience.0.role", "wrong_type"),
        ("photo", "unknown_field"),
        ("highlights.1", "empty"),
        ("highlights.4", "too_many"),
        ("experience.1.bullets.0", "too_long"),
        ("experience.2", "duplicate_employer"),
    )
    assert isinstance(reading.content, c.CvContent)
    assert reading.content.headline == "Platform engineer"
    assert reading.content.highlights == ["First highlight", "Second highlight", "Third highlight"]
    assert [r.employer_key for r in reading.content.experience] == ["example-corp"]
    assert reading.content.notes == "Kept from the command line."


def test_the_invalid_letter_keeps_every_valid_part() -> None:
    reading = legacy.read(Kind.letter, load_raw("letter_invalid"), KEYS)
    assert reading.left_out == (
        ("date", "unknown_field"),
        ("paragraphs.1", "empty"),
        ("closing", "wrong_type"),
    )
    assert isinstance(reading.content, c.LetterContent)
    assert len(reading.content.paragraphs) == 2
    assert reading.content.closing == ""


UNREADABLE = [
    pytest.param(b"", "not_json", id="empty file"),
    pytest.param(b"{", "not_json", id="cut"),
    pytest.param(b"\xff\xfe{}", "not_json", id="not UTF-8"),
    pytest.param(b"{'headline': 'x'}", "not_json", id="single quotes"),
    pytest.param(b"[]", "not_object", id="a list"),
    pytest.param(b'"cv"', "not_object", id="a string"),
    pytest.param(b"null", "not_object", id="null"),
    pytest.param(b"42", "not_object", id="a number"),
    pytest.param(b"[" * 20_000 + b"]" * 20_000, "not_json", id="deep nesting under the cap"),
    pytest.param(b"1" * 5_000, "not_json", id="a number too long for Python"),
    pytest.param(b" " * (c.RAW_MAX_BYTES + 1), "content_too_large", id="over the cap"),
]


@pytest.mark.parametrize(("data", "code"), UNREADABLE)
@pytest.mark.parametrize("kind", [Kind.cv, Kind.letter])
def test_a_file_that_cannot_be_read_gives_an_empty_noted_draft(
    data: bytes, code: str, kind: Kind
) -> None:
    reading = legacy.read(kind, data, KEYS)
    assert reading.left_out == (("", code),)
    assert reading.content == c.parse(kind.value, {})  # type: ignore[arg-type]
    assert reading.notes()[0]["code"] == legacy.LEGACY_INVALID


def test_over_the_size_of_a_content_what_comes_first_is_kept() -> None:
    # Twelve full entries are over CONTENT_MAX_BYTES on their own: the entries that fit are kept
    # in the file's order, and a field that would cross the cap after them is noted.
    data = {
        "summary": "s" * c.SUMMARY_MAX,
        "experience": [
            {"employer_key": f"employer-{i}", "bullets": ["b" * c.BULLET_MAX] * 8}
            for i in range(c.EMPLOYERS_MAX)
        ],
        "notes": "n" * c.NOTES_MAX,
    }
    reading = legacy.read(Kind.cv, raw(data), KEYS)
    assert reading.left_out == (
        *((f"experience.{i}", "content_too_large") for i in range(8, c.EMPLOYERS_MAX)),
        ("notes", "content_too_large"),
    )
    assert isinstance(reading.content, c.CvContent)
    assert [r.employer_key for r in reading.content.experience] == [
        f"employer-{i}" for i in range(8)
    ]
    assert reading.content.summary
    assert not reading.content.notes
    assert len(c.canonical_json(reading.content)) <= c.CONTENT_MAX_BYTES


def test_the_notes_name_the_left_out_paths_up_to_a_cap() -> None:
    data = {"competencies": [""] * 200, "experience": [entry("other-employer")]}
    reading = legacy.read(Kind.cv, raw(data), KEYS)
    assert len(reading.left_out) == 200
    notes = reading.notes()
    invalid = [n for n in notes if n["code"] == legacy.LEGACY_INVALID]
    assert len(invalid) == legacy.NOTED_MAX + 1
    assert invalid[0] == {
        "field": "competencies",
        "start": 0,
        "end": 0,
        "code": legacy.LEGACY_INVALID,
        "level": "invalid",
        "rewrite": False,
        "legacy_path": "competencies.0",
        "reason": "empty",
    }
    assert invalid[-1]["reason"] == f"{200 - legacy.NOTED_MAX} more"
    assert notes[-1]["field"] == "experience.0.employer_key"
    assert notes[-1]["code"] == rules.UNKNOWN_EMPLOYER


def test_an_unknown_field_is_noted_on_the_whole_content() -> None:
    notes = legacy.read(Kind.cv, raw({"photo": "x"}), KEYS).notes()
    assert [(n["field"], n["legacy_path"], n["reason"]) for n in notes] == [
        ("", "photo", "unknown_field")
    ]


def test_the_worst_shape_at_the_cap_is_read_whole() -> None:
    # Every item of every list invalid, as many as the raw cap holds: one small check per item.
    items = (c.RAW_MAX_BYTES - 200) // 6
    data = {"highlights": [""] * (items // 2), "competencies": [1] * (items // 2)}
    body = raw(data)
    assert len(body) <= c.RAW_MAX_BYTES
    reading = legacy.read(Kind.cv, body, KEYS)
    assert len(reading.left_out) == 2 * (items // 2)
    assert reading.content == c.parse("cv", {})


def test_reading_is_pure() -> None:
    body = load_raw("cv_invalid")
    first, second = legacy.read(Kind.cv, body, KEYS), legacy.read(Kind.cv, body, KEYS)
    assert first == second
    assert first.notes() == second.notes()
