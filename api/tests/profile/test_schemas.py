"""Unit tests of the six section models, the string and regex rules, and completeness. No
database: these validate dictionaries exactly as the API receives them (parsed JSON)."""

import copy
import json
import re
import unicodedata
from typing import Any

import pytest
from pydantic import ValidationError

from app.profile import schemas
from app.profile.schemas import (
    FACTS_MAX_BYTES,
    REGEX_MAX,
    SECTIONS,
    check_regex,
    completeness,
    format_period,
    parse_period,
    period_key,
    period_span,
    validate_section,
)
from tests.profile.conftest import FULL, full


def errors(section: str, data: Any) -> list[dict[str, Any]]:
    with pytest.raises(ValidationError) as caught:
        validate_section(section, data)
    return caught.value.errors()


def first(section: str, data: Any) -> tuple[str, str]:
    """(dotted location, message) of the first error."""
    error = errors(section, data)[0]
    return ".".join(str(p) for p in error["loc"]), error["msg"]


def with_(section: str, path: str, value: Any) -> dict[str, Any]:
    """The full sample with one value replaced; path like 'employers.0.end'."""
    data = full(section)
    *parents, last = path.split(".")
    node: Any = data
    for part in parents:
        node = node[int(part)] if isinstance(node, list) else node[part]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return data


def without(section: str, path: str) -> dict[str, Any]:
    data = full(section)
    *parents, last = path.split(".")
    node: Any = data
    for part in parents:
        node = node[int(part)] if isinstance(node, list) else node[part]
    del node[last]
    return data


# --- every section: full sample, empty, extra fields, wrong top-level type ----------------------


@pytest.mark.parametrize("section", SECTIONS)
def test_full_sample_is_valid_and_stored_unchanged(section: str) -> None:
    assert validate_section(section, full(section)) == FULL[section]


@pytest.mark.parametrize("section", SECTIONS)
def test_empty_object_is_valid_with_defaults(section: str) -> None:
    stored = validate_section(section, {})
    # Idempotent: validating the stored form again gives the same document.
    assert validate_section(section, stored) == stored
    json.dumps(stored)  # JSON-serialisable as stored in JSONB


@pytest.mark.parametrize("section", SECTIONS)
def test_unknown_top_level_field_is_refused(section: str) -> None:
    loc, msg = first(section, {**full(section), "surprise": 1})
    assert loc == "surprise"
    assert msg == "Extra inputs are not permitted"


@pytest.mark.parametrize(
    ("section", "path"),
    [
        ("identity", "languages.0"),
        ("experience", "employers.0"),
        ("experience", "education.0"),
        ("facts", "blocks.0"),
        ("skills", "have.0"),
        ("skills", "gates.0"),
        ("search", "boards.0"),
        ("search", "title_include"),
        ("answers", "work_authorization.0"),
    ],
)
def test_unknown_nested_field_is_refused(section: str, path: str) -> None:
    data = full(section)
    node: Any = data
    for part in path.split("."):
        node = node[int(part)] if isinstance(node, list) else node[part]
    node["extra"] = "x"
    loc, msg = first(section, data)
    assert loc == f"{path}.extra"
    assert msg == "Extra inputs are not permitted"


@pytest.mark.parametrize("section", SECTIONS)
@pytest.mark.parametrize("value", [None, [], "text", 3])
def test_section_must_be_an_object(section: str, value: Any) -> None:
    assert errors(section, value)


# --- strings: normalisation, control characters, unicode, sizes ---------------------------------


def test_strings_are_stripped_and_nfc_normalised() -> None:
    decomposed = unicodedata.normalize("NFD", "  Hélène Ørsted  ")
    assert decomposed != "  Hélène Ørsted  "
    stored = validate_section("identity", {"full_name": decomposed})
    assert stored["full_name"] == "Hélène Ørsted"
    assert unicodedata.is_normalized("NFC", stored["full_name"])


@pytest.mark.parametrize(
    "text",
    [
        "李小龍",  # CJK
        "محمد الأحمد",  # Arabic, right to left
        "דוד כהן",  # Hebrew, right to left
        "Zoë 🚀 Dev 👩🏽‍💻",  # emoji with skin tone and ZWJ sequence
        "Ελένη Παπαδοπούλου",
        "𝔘𝔫𝔦𝔠𝔬𝔡𝔢",  # astral plane letters
    ],
)
def test_unicode_survives_validation(text: str) -> None:
    stored = validate_section("identity", {"full_name": text, "headline": text})
    assert stored["full_name"] == unicodedata.normalize("NFC", text)


@pytest.mark.parametrize("char", ["\x00", "\x07", "\x1b", "\x7f", "\x85", "\n", "\t", "\r"])
def test_single_line_fields_refuse_control_characters(char: str) -> None:
    loc, msg = first("identity", {"full_name": f"Ada{char}Lovelace"})
    assert loc == "full_name"
    assert "control characters" in msg


@pytest.mark.parametrize("char", ["\x00", "\x07", "\x1b", "\x7f", "\x9f"])
def test_multiline_fields_refuse_control_characters_except_newline_and_tab(char: str) -> None:
    data = with_("facts", "blocks.0.body", f"line{char}one")
    loc, msg = first("facts", data)
    assert loc == "blocks.0.body"
    assert "control characters" in msg


def test_multiline_fields_keep_newlines_and_tabs_and_unify_crlf() -> None:
    data = with_("facts", "blocks.0.body", "a\r\nb\rc\n\td")
    assert validate_section("facts", data)["blocks"][0]["body"] == "a\nb\nc\n\td"


def test_lone_surrogate_is_refused() -> None:
    loc, msg = first("identity", {"full_name": "bad \ud800 text"})
    assert loc == "full_name"
    assert "surrogate" in msg


def test_blank_optional_string_becomes_null() -> None:
    assert validate_section("identity", {"headline": "   "})["headline"] is None
    assert validate_section("answers", {"motivation": "\n\n"})["motivation"] is None


@pytest.mark.parametrize(
    ("section", "path"),
    [
        ("identity", "languages.0.name"),
        ("experience", "employers.0.organisation"),
        ("experience", "employers.0.title"),
        ("experience", "education.0.school"),
        ("facts", "blocks.0.title"),
        ("skills", "have.0.name"),
        ("search", "boards.0.company"),
        ("answers", "work_authorization.0.country"),
    ],
)
def test_required_strings_cannot_be_blank(section: str, path: str) -> None:
    loc, msg = first(section, with_(section, path, "   "))
    assert loc == path
    assert "at least 1 character" in msg


@pytest.mark.parametrize(
    ("section", "path", "limit"),
    [
        ("identity", "full_name", 200),
        ("identity", "headline", 200),
        ("experience", "employers.0.title", 200),
        ("answers", "notice_period", 2_000),
        ("answers", "motivation", 10_000),
        ("search", "never_apply.0", 500),
    ],
)
def test_oversize_strings_are_refused(section: str, path: str, limit: int) -> None:
    assert validate_section(section, with_(section, path, "é" * limit))  # at the limit: fine
    loc, msg = first(section, with_(section, path, "é" * (limit + 1)))
    assert loc == path
    assert f"at most {limit} characters" in msg


@pytest.mark.parametrize(
    ("section", "path", "value", "loc"),
    [
        ("identity", "full_name", 42, "full_name"),
        ("identity", "languages", "French", "languages"),
        ("identity", "languages", [{"name": 1}], "languages.0.name"),
        ("experience", "employers", {"key": "a"}, "employers"),
        ("experience", "employers.0.start", 202509, "employers.0.start"),
        ("facts", "blocks.0.body", ["a"], "blocks.0.body"),
        ("skills", "years_experience", "3.5", "years_experience"),
        ("skills", "years_experience", True, "years_experience"),
        ("skills", "gates.0.penalty", "25", "gates.0.penalty"),
        ("skills", "gates.0.penalty", 2.5, "gates.0.penalty"),
        ("skills", "have.0.advanced_regex", "true", "have.0.advanced_regex"),
        ("skills", "have.0.terms", "agent", "have.0.terms"),
        ("search", "min_fit", "70", "min_fit"),
        ("search", "days", 7.0, "days"),
        ("search", "boards.0.enabled", 1, "boards.0.enabled"),
        ("search", "contract_types", "CDI", "contract_types"),
        (
            "answers",
            "work_authorization.0.needs_sponsorship",
            "no",
            "work_authorization.0.needs_sponsorship",
        ),
    ],
)
def test_wrong_types_are_refused_not_coerced(section: str, path: str, value: Any, loc: str) -> None:
    assert first(section, with_(section, path, value))[0] == loc


# --- required fields inside list items ----------------------------------------------------------


@pytest.mark.parametrize(
    ("section", "path"),
    [
        ("identity", "languages.0.name"),
        ("identity", "links.0.url"),
        ("experience", "employers.0.key"),
        ("experience", "employers.0.organisation"),
        ("experience", "employers.0.title"),
        ("experience", "employers.0.start"),
        ("experience", "education.0.school"),
        ("experience", "education.0.degree"),
        ("facts", "blocks.0.title"),
        ("facts", "blocks.0.body"),
        ("skills", "have.0.name"),
        ("skills", "gates.0.name"),
        ("search", "boards.0.company"),
        ("search", "boards.0.ats"),
        ("search", "boards.0.org"),
        ("answers", "work_authorization.0.country"),
        ("answers", "work_authorization.0.answer"),
    ],
)
def test_missing_required_field(section: str, path: str) -> None:
    loc, msg = first(section, without(section, path))
    assert loc == path
    assert msg == "Field required"


def test_optional_item_fields_have_defaults() -> None:
    stored = validate_section("search", {"boards": [{"company": "X", "ats": "lever", "org": "x"}]})
    assert stored["boards"][0]["enabled"] is True
    assert stored["boards"][0]["extra_exclude"] == {"terms": [], "advanced_regex": False}
    stored = validate_section("skills", {"gates": [{"name": "g"}]})
    assert stored["gates"][0] == {"name": "g", "terms": [], "advanced_regex": False, "penalty": 25}


# --- identity -----------------------------------------------------------------------------------


@pytest.mark.parametrize("email", ["not an email", "a@", "@b.co", "a b@c.co", "a@b"])
def test_identity_bad_email(email: str) -> None:
    assert first("identity", with_("identity", "email", email)) == (
        "email",
        "Value error, must be a valid email address",
    )


def test_identity_email_is_normalised() -> None:
    assert validate_section("identity", {"email": "  Ada@Example.ORG "})["email"] == (
        "ada@example.org"
    )


@pytest.mark.parametrize("phone", ["call me", "12", "+33 6 12 34 56 78 ext 9", "++33612345678"])
def test_identity_bad_phone(phone: str) -> None:
    assert first("identity", with_("identity", "phone", phone))[0] == "phone"


@pytest.mark.parametrize("phone", ["+33 6 12 34 56 78", "06.12.34.56.78", "+1 (415) 555-0100"])
def test_identity_good_phone(phone: str) -> None:
    assert validate_section("identity", {"phone": phone})["phone"] == phone


@pytest.mark.parametrize(
    "url",
    ["linkedin.com/in/ada", "javascript:alert(1)", "ftp://x.org", "https://", "https://nodot",
     "https://a b.org"],
)  # fmt: skip
def test_identity_bad_urls(url: str) -> None:
    assert first("identity", with_("identity", "linkedin_url", url))[0] == "linkedin_url"
    assert first("identity", with_("identity", "links.0.url", url))[0] == "links.0.url"


def test_identity_duplicate_languages() -> None:
    data = with_("identity", "languages.1", {"name": "FRENCH", "level": None})
    loc, msg = first("identity", data)
    assert loc == "languages"
    assert "duplicate language" in msg


def test_identity_list_caps() -> None:
    langs = [{"name": f"L{i}", "level": None} for i in range(21)]
    assert first("identity", {"languages": langs})[0] == "languages"
    links = [{"label": "x", "url": "https://x.org"}] * 11
    assert first("identity", {"links": links})[0] == "links"


def test_identity_has_no_photo_field() -> None:
    assert first("identity", {"photo": "iVBORw0KGgo="})[0] == "photo"


# --- experience ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "month",
    ["2025-13", "2025-00", "2025-9", "25-09", "2025/09", "2025-09-01", "1899-12", "2101-01"],
)
def test_experience_bad_month(month: str) -> None:
    loc, msg = first("experience", with_("experience", "employers.0.start", month))
    assert loc == "employers.0.start"
    assert "YYYY-MM" in msg


# The four dates of the section: a month, or a year alone (P7).
DATE_FIELDS = ["employers.start", "employers.end", "education.start", "education.end"]


def dated(field: str, value: Any, **other: Any) -> dict[str, Any]:
    """A section whose one employer or one school carries `value` in one date field. The
    employer starts in 1900 when its end is the field under test, so the order rule is quiet."""
    kind, name = field.split(".")
    if kind == "employers":
        entry = {"key": "a", "organisation": "A", "title": "T", "start": "1900", **other}
    else:
        entry = {"school": "S", "degree": "D", **other}
    entry[name] = value
    return {kind: [entry]}


def stored_date(field: str, value: Any, **other: Any) -> Any:
    kind, name = field.split(".")
    return validate_section("experience", dated(field, value, **other))[kind][0][name]


@pytest.mark.parametrize("field", DATE_FIELDS)
@pytest.mark.parametrize(
    "value", ["2021", "2021-03", "1900", "2100", "1900-01", "2100-12", "2021-01", "2021-12"]
)
def test_a_year_alone_and_a_month_are_stored_as_written(field: str, value: str) -> None:
    assert stored_date(field, value) == value
    # No padding and no shortening, however often the stored form is validated again.
    once = validate_section("experience", dated(field, value))
    assert validate_section("experience", once) == once
    assert validate_section("experience", json.loads(json.dumps(once))) == once


@pytest.mark.parametrize("field", DATE_FIELDS)
@pytest.mark.parametrize(
    "value",
    [
        "2021-3",  # the month takes two digits
        "21",  # a two digit year is never read
        "2021-13",
        "2021-00",
        "1899",
        "2101",
        "1899-12",
        "2101-01",
        "2021/03",
        "2021.03",
        "2021-03-01",  # a day
        "03-2021",
        "03/2021",
        "2021-",
        "-03",
        "20211",
        "202",
        "2021 03",
        "2021-03-",
        "March 2021",
        "2021-Q1",
        "2021-2023",
        "present",
        "+2021",
        "2021.0",
        "0x7e5",
    ],
)
def test_a_date_in_any_other_form_is_refused_with_the_rule(field: str, value: str) -> None:
    kind, name = field.split(".")
    loc, msg = first("experience", dated(field, value))
    assert loc == f"{kind}.0.{name}"
    assert msg == (
        "Value error, must be a year or a month, written YYYY or YYYY-MM, between 1900 and 2100"
    )


@pytest.mark.parametrize("field", DATE_FIELDS)
@pytest.mark.parametrize("value", [2021, 2021.0, 202103, True, ["2021"], {"year": 2021}])
def test_a_date_is_text_never_a_number(field: str, value: Any) -> None:
    kind, name = field.split(".")
    loc, msg = first("experience", dated(field, value))
    assert loc == f"{kind}.0.{name}"
    assert msg == "Input should be a valid string"


@pytest.mark.parametrize("field", DATE_FIELDS[1:])
@pytest.mark.parametrize("value", ["", "   ", None])
def test_an_optional_date_left_empty_is_null(field: str, value: Any) -> None:
    assert stored_date(field, value) is None


def test_the_employer_start_is_still_required() -> None:
    entry = {"key": "a", "organisation": "A", "title": "T"}
    assert first("experience", {"employers": [entry]}) == ("employers.0.start", "Field required")
    loc, msg = first("experience", dated("employers.start", ""))
    assert (loc, msg) == ("employers.0.start", "String should have at least 1 character")
    loc, msg = first("experience", dated("employers.start", None))
    assert (loc, msg) == ("employers.0.start", "Input should be a valid string")
    # An end alone does not stand in for it.
    assert first("experience", {"employers": [{**entry, "end": "2023"}]})[0] == "employers.0.start"


def test_a_date_is_stripped_but_never_rewritten() -> None:
    assert stored_date("employers.start", "  2021 ") == "2021"
    assert stored_date("education.end", "\t2021-03\n") == "2021-03"


# start, end, accepted. Only what is certain is refused: the last month the end can mean is
# before the first month the start can mean.
RANGES = [
    ("2023-05", "2023", True),  # the end may be December 2023
    ("2023", "2022-12", False),  # the start is January 2023 at the earliest
    ("2023", "2023", True),
    ("2023-06", "2023-05", False),
    ("2021", "2021-03", True),  # the start may be January 2021
    ("2021-03", "2021", True),
    ("2023-12", "2023", True),
    ("2024-01", "2023", False),
    ("2023", "2023-01", True),
    ("2023", "2024", True),
    ("2024", "2023", False),
    ("2023-05", "2023-05", True),
    ("2023-05", "2023-06", True),
    ("1900", "2100", True),
    ("2100", "1900", False),
    ("1999-12", "2000", True),
    ("2000", "1999-12", False),
    ("2000-01", "1999", False),
]


@pytest.mark.parametrize("kind", ["employers", "education"])
@pytest.mark.parametrize(("start", "end", "accepted"), RANGES)
def test_end_before_start_compares_periods(kind: str, start: str, end: str, accepted: bool) -> None:
    data = dated(f"{kind}.end", end, start=start)
    if accepted:
        entry = validate_section("experience", data)[kind][0]
        assert (entry["start"], entry["end"]) == (start, end)
    else:
        assert first("experience", data) == (
            f"{kind}.0",
            "Value error, end must not be before start",
        )


def test_the_order_rule_is_not_a_comparison_of_text() -> None:
    # As text "2023" is before "2023-05": the month only model compared strings.
    assert "2023" < "2023-05"
    assert stored_date("employers.end", "2023", start="2023-05") == "2023"


def test_a_date_that_is_not_a_period_is_reported_before_the_order() -> None:
    loc, msg = first("experience", dated("employers.end", "2021-3", start="2023"))
    assert loc == "employers.0.end"
    assert "YYYY or YYYY-MM" in msg


def test_experience_end_before_start() -> None:
    data = with_("experience", "employers.1.end", "2022-12")
    loc, msg = first("experience", data)
    assert loc == "employers.1"
    assert msg == "Value error, end must not be before start"
    data = with_("experience", "education.0.end", "2022-08")
    assert first("experience", data)[0] == "education.0"


def _month_only(value: str) -> bool:
    """The rule of the model before P7, copied here: a month YYYY-MM, 1900 to 2100."""
    match = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", value)
    return bool(match) and 1900 <= int(match.group(1)) <= 2100


def test_every_date_valid_before_is_still_valid_and_unchanged() -> None:
    """No stored value needs a migration: the new rule accepts all the old one did, as it is."""
    months = [f"{year:04d}-{month:02d}" for year in range(1900, 2101) for month in range(1, 13)]
    # The old pattern took the digits of other scripts for the year: still accepted, see schemas.
    months += ["٢٠٢١-03", "２０２１-12"]
    assert all(_month_only(value) for value in months)
    for field in DATE_FIELDS:
        assert [stored_date(field, value) for value in months] == months


def test_a_profile_saved_before_the_change_loads_and_saves_unchanged() -> None:
    """The stored form of a month only section is what comes back, and it still reads as
    complete (completeness validates the stored section on every read of the profile)."""
    before = full("experience")
    assert all(_month_only(e["start"]) for e in before["employers"])
    stored = validate_section("experience", before)
    assert stored == FULL["experience"]
    assert validate_section("experience", stored) == stored
    assert completeness({"experience": stored}).sections["experience"].percent == 100


# --- periods: the order, the span and the words -------------------------------------------------


def test_period_key_orders_a_year_alone_before_the_months_of_its_year() -> None:
    values = ["2022", "2021-12", "2021", "2020-12", "2021-01", "2022-01", "2021-06"]
    assert sorted(values, key=period_key) == [
        "2020-12",
        "2021",
        "2021-01",
        "2021-06",
        "2021-12",
        "2022",
        "2022-01",
    ]
    assert period_key("2021") == (2021, 0)
    assert period_key("2021-03") == (2021, 3)


def test_period_key_is_a_total_order_so_sorting_is_stable_and_defined() -> None:
    values = [str(year) for year in range(1900, 2101)]
    values += [f"{year}-{month:02d}" for year in range(1900, 2101) for month in range(1, 13)]
    assert len({period_key(value) for value in values}) == len(values)
    # Newest first, the way a list of employers reads: a year alone after its months.
    newest = sorted(["2021", "2021-03", "2020", "2021-11"], key=period_key, reverse=True)
    assert newest == ["2021-11", "2021-03", "2021", "2020"]


def test_period_span_is_the_first_and_last_month_a_period_can_mean() -> None:
    assert period_span("2021") == (2021 * 12, 2021 * 12 + 11)
    assert period_span("2021-01") == (2021 * 12, 2021 * 12)
    assert period_span("2021-12") == (2021 * 12 + 11, 2021 * 12 + 11)
    assert period_span("2022-01")[0] == period_span("2021")[1] + 1
    # A duration that invents nothing reads a start at its last month and an end at its first:
    # 2021 to 2023 is at least January 2023 minus December 2021, and one same year is zero.
    assert period_span("2023")[0] - period_span("2021")[1] == 13
    assert max(0, period_span("2021")[0] - period_span("2021")[1]) == 0


def test_format_period_prints_a_year_alone_as_the_year() -> None:
    assert format_period("2021") == "2021"
    assert format_period("2021-03") == "March 2021"
    assert [format_period(f"1999-{month:02d}") for month in range(1, 13)] == [
        f"{name} 1999" for name in schemas.MONTH_NAMES
    ]
    for year in ("1900", "2021", "2100"):
        assert not any(name in format_period(year) for name in schemas.MONTH_NAMES)


@pytest.mark.parametrize("value", ["", "2021-3", "21", "2021-13", "1899", "2101", None, 2021])
@pytest.mark.parametrize("read", [period_key, period_span, format_period, parse_period])
def test_period_helpers_refuse_what_is_not_a_period(read: Any, value: Any) -> None:
    with pytest.raises(ValueError, match="YYYY or YYYY-MM"):
        read(value)


def test_the_json_schema_describes_both_forms() -> None:
    """What the OpenAPI document shows for the four dates (the import body carries the models)."""
    defs = schemas.ImportIn.model_json_schema()["$defs"]
    required = defs["Employer"]["properties"]["start"]
    assert required["type"] == "string"
    assert "YYYY-MM" in required["description"]
    assert "YYYY," in required["description"]
    assert required["examples"] == ["2021-03", "2021"]
    assert re.fullmatch(required["pattern"], "2021")
    assert re.fullmatch(required["pattern"], "2021-03")
    assert not re.fullmatch(required["pattern"], "2021-3")
    assert "start" in defs["Employer"]["required"]
    optional = [
        defs["Employer"]["properties"]["end"],
        defs["School"]["properties"]["start"],
        defs["School"]["properties"]["end"],
    ]
    for described in optional:
        assert {"type": "null"} in described["anyOf"]
        assert "YYYY-MM" in described["description"]
        assert None in described["examples"]
        for value in ("", "2021", "2021-03"):
            assert re.fullmatch(described["pattern"], value)
        assert not re.fullmatch(described["pattern"], "21")


def test_experience_same_month_and_present() -> None:
    data = with_("experience", "employers.1.end", "2023-01")
    assert validate_section("experience", data)["employers"][1]["end"] == "2023-01"
    data = with_("experience", "employers.1.end", "")
    assert validate_section("experience", data)["employers"][1]["end"] is None  # present


def test_experience_school_without_start_may_have_end() -> None:
    data = {"education": [{"school": "S", "degree": "D", "end": "2018-06"}]}
    assert validate_section("experience", data)["education"][0]["end"] == "2018-06"


def test_experience_duplicate_employer_keys() -> None:
    data = with_("experience", "employers.1.key", "acme")
    loc, msg = first("experience", data)
    assert loc == "employers"
    assert "duplicate employer key" in msg


@pytest.mark.parametrize("key", ["Acme", "acme group", "acme_group", "-acme", "acme-", "a--b", "é"])
def test_experience_key_must_be_a_slug(key: str) -> None:
    assert first("experience", with_("experience", "employers.0.key", key))[0] == (
        "employers.0.key"
    )


def test_experience_contract_enum() -> None:
    assert first("experience", with_("experience", "employers.0.contract", "permanent"))[0] == (
        "employers.0.contract"
    )


def test_experience_list_caps() -> None:
    base = full("experience")["employers"][0]
    employers = [{**base, "key": f"e{i}"} for i in range(31)]
    assert first("experience", {"employers": employers})[0] == "employers"
    assert len(validate_section("experience", {"employers": employers[:30]})["employers"]) == 30


# --- facts --------------------------------------------------------------------------------------


def test_facts_duplicate_titles() -> None:
    data = with_("facts", "blocks.1.title", "HARD RULES")
    loc, msg = first("facts", data)
    assert loc == "blocks"
    assert "duplicate block title" in msg


def test_facts_total_size_cap_in_bytes() -> None:
    # Multi-byte characters: the cap is on UTF-8 bytes, not characters.
    half = "é" * (FACTS_MAX_BYTES // 4)  # FACTS_MAX_BYTES / 2 bytes
    ok = {"blocks": [{"title": "a", "body": half[:-1]}, {"title": "b", "body": half[:-1]}]}
    assert validate_section("facts", ok)
    too_big = {"blocks": [{"title": "a", "body": half}, {"title": "b", "body": half + "é"}]}
    loc, msg = first("facts", too_big)
    assert loc == "blocks"
    assert f"at most {FACTS_MAX_BYTES} allowed" in msg


def test_facts_markdown_is_kept_verbatim() -> None:
    body = "# Title\n\n- item `code`\n\n| a | b |\n|---|---|\n\n> quote — with dash"
    stored = validate_section("facts", {"blocks": [{"title": "t", "body": body}]})
    assert stored["blocks"][0]["body"] == body


def test_facts_empty_blocks_list_is_valid() -> None:
    assert validate_section("facts", {"blocks": []}) == {"blocks": []}


# --- skills -------------------------------------------------------------------------------------


@pytest.mark.parametrize("years", [-0.5, 60.5, float("nan"), float("inf")])
def test_skills_years_out_of_range(years: float) -> None:
    assert first("skills", with_("skills", "years_experience", years))[0] == "years_experience"


@pytest.mark.parametrize("years", [0, 0.0, 3.5, 60])
def test_skills_years_in_range(years: float) -> None:
    assert validate_section("skills", {"years_experience": years})["years_experience"] == years


@pytest.mark.parametrize("penalty", [-1, 101])
def test_skills_gate_penalty_range(penalty: int) -> None:
    assert first("skills", with_("skills", "gates.0.penalty", penalty))[0] == "gates.0.penalty"


def test_skills_category_names_unique_across_buckets() -> None:
    data = with_("skills", "none.0.name", "Agents")
    loc, msg = first("skills", data)
    assert loc == ""
    assert "duplicate category" in msg
    data = full("skills")
    data["gates"].append(dict(data["gates"][0]))
    assert "duplicate gate" in first("skills", data)[1]


def test_skills_terms_deduplicated_ignoring_case() -> None:
    data = with_("skills", "have.0.terms", ["Agent", "agent", " AGENT ", "agentic"])
    assert validate_section("skills", data)["have"][0]["terms"] == ["Agent", "agentic"]


def test_skills_regex_terms_checked_only_when_advanced() -> None:
    plain = with_("skills", "have.0.terms", ["c++", "(unbalanced"])
    assert validate_section("skills", plain)["have"][0]["terms"] == ["c++", "(unbalanced"]
    advanced = with_("skills", "have.1.terms", ["ok", "(unbalanced"])
    loc, msg = first("skills", advanced)
    assert loc == "have.1.terms"
    assert msg.startswith("Value error, term 2: invalid regular expression at position 0")


def test_skills_empty_lists() -> None:
    stored = validate_section("skills", {"have": [], "adjacent": [], "none": [], "gates": []})
    assert stored["have"] == []
    assert (
        validate_section("skills", {"have": [{"name": "x", "terms": []}]})["have"][0]["terms"] == []
    )


# --- regular expressions ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern",
    [
        r"fine[- ]?tun\w*",
        r"\b(staff|principal|director)\b",
        r"phd (is )?required|requires? (a )?phd",
        r"(arabic|dutch|german)[ -]speaking",
        r"z(ü|u)rich",
        r"^ai engineer$",
        r"\bml\b",
        r"(?:ab){2}",
    ],
)
def test_valid_regex(pattern: str) -> None:
    assert check_regex(pattern) == pattern


@pytest.mark.parametrize(
    ("pattern", "position"),
    [("(abc", 0), ("abc)", 3), ("[a-", 0), ("a{2,1}", 2), ("*a", 0), ("a\\", 1), ("(?P<x", 4)],
)
def test_invalid_regex_names_the_position(pattern: str, position: int) -> None:
    with pytest.raises(ValueError, match=f"invalid regular expression at position {position}"):
        check_regex(pattern)


@pytest.mark.parametrize(
    "pattern",
    [r"(a+)+", r"(a*)*b", r"(\w+\s?)*$", r"(?:x{2,})+", r"(a|b+)*", r"((ab)+)+", r"([^]a]+)+"],
)
def test_nested_quantifiers_are_refused(pattern: str) -> None:
    with pytest.raises(ValueError, match="nested quantifiers"):
        check_regex(pattern)


def test_escaped_plus_is_not_a_quantifier() -> None:
    assert check_regex(r"(c\+\+)+") == r"(c\+\+)+"


def test_regex_size_limit() -> None:
    assert check_regex("a" * REGEX_MAX)
    with pytest.raises(ValueError, match=f"longer than {REGEX_MAX}"):
        check_regex("a" * (REGEX_MAX + 1))


def test_regex_too_long_in_a_section() -> None:
    data = {"title_include": {"terms": ["a" * (REGEX_MAX + 1)], "advanced_regex": True}}
    loc, msg = first("search", data)
    assert loc == "title_include.terms.0"
    assert f"at most {REGEX_MAX} characters" in msg


@pytest.mark.parametrize(
    "pattern", [r"([+*])+", r"[(a+)]+", r"(a)+(b+)", r"(?:ab)+c*", r"[]+]+", r"([^+*]+)x"]
)
def test_quantifiers_that_do_not_nest_pass(pattern: str) -> None:
    assert check_regex(pattern) == pattern


def test_plain_term_size_limit() -> None:
    assert validate_section("search", {"location": {"terms": ["p" * 200]}})
    loc, msg = first("search", {"location": {"terms": ["p" * 201]}})
    assert loc == "location.terms"
    assert "at most 200 characters" in msg


def test_term_list_cap() -> None:
    terms = [f"t{i}" for i in range(schemas.TERMS_MAX + 1)]
    assert first("search", {"location": {"terms": terms}})[0] == "location.terms"


def test_term_cannot_be_blank_or_multiline() -> None:
    assert first("search", {"location": {"terms": ["  "]}})[0] == "location.terms.0"
    assert first("search", {"location": {"terms": ["a\nb"]}})[0] == "location.terms.0"


# --- search -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("min_fit", -1),
        ("min_fit", 101),
        ("days", 0),
        ("days", 366),
        ("remote_stance", "sometimes"),
        ("remote_stance", "Remote"),
        ("contract_types", ["CDI", "Permanent"]),
        ("contract_types", ["cdi"]),
        ("boards.0.ats", "workday"),
        ("boards.0.org", "has space"),
        ("boards.0.org", ".starts-with-dot"),
        ("boards.0.org", "a/b"),
        ("never_apply", ["x"] * 51),
    ],
)
def test_search_invalid_values(path: str, value: Any) -> None:
    loc = first("search", with_("search", path, value))[0]
    assert loc.split(".")[0] == path.split(".")[0]


@pytest.mark.parametrize("stance", ["remote", "hybrid", "office", "any", None])
def test_search_remote_stances(stance: str | None) -> None:
    assert validate_section("search", {"remote_stance": stance})["remote_stance"] == stance


def test_search_all_contract_types() -> None:
    every = ["CDI", "CDD", "VIE", "Stage", "Alternance", "Freelance"]
    assert validate_section("search", {"contract_types": every})["contract_types"] == every


def test_search_duplicate_contract_type() -> None:
    assert "duplicate contract type" in first("search", {"contract_types": ["CDI", "CDI"]})[1]


def test_search_duplicate_board() -> None:
    data = full("search")
    data["boards"].append({**data["boards"][0], "company": "Mistral again", "org": "MISTRAL.AI"})
    loc, msg = first("search", data)
    assert loc == "boards"
    assert "duplicate board" in msg
    # The same org on another ATS is a different board.
    data["boards"][-1]["ats"] = "lever"
    assert len(validate_section("search", data)["boards"]) == 3


def test_search_board_extra_exclude_regex_checked() -> None:
    data = with_("search", "boards.0.extra_exclude", {"terms": ["(x"], "advanced_regex": True})
    assert first("search", data)[0] == "boards.0.extra_exclude.terms"


def test_search_defaults() -> None:
    stored = validate_section("search", {})
    assert stored["min_fit"] == 70
    assert stored["days"] == 30
    assert stored["boards"] == []
    assert stored["title_include"] == {"terms": [], "advanced_regex": False}


# --- answers ------------------------------------------------------------------------------------


def test_answers_duplicate_country() -> None:
    data = with_("answers", "work_authorization.1.country", "france")
    assert "duplicate country" in first("answers", data)[1]


def test_answers_multiline_motivation_kept() -> None:
    text = "Line one.\n\nLine two, with émoji 🙂."
    assert validate_section("answers", {"motivation": text})["motivation"] == text


def test_answers_leave_blank_items_are_single_line() -> None:
    assert first("answers", {"leave_blank": ["a\nb"]})[0] == "leave_blank.0"


# --- completeness -------------------------------------------------------------------------------


def test_completeness_of_an_empty_profile() -> None:
    result = completeness({})
    assert result.overall == 0
    assert set(result.sections) == set(SECTIONS)
    for section in result.sections.values():
        assert section.percent == 0
        assert section.missing
    assert result.sections["identity"].missing == [
        "full_name", "headline", "email", "phone", "city", "country", "languages"
    ]  # fmt: skip
    assert result.sections["search"].missing == [
        "title_include", "location", "contract_types", "remote_stance", "boards"
    ]  # fmt: skip


def test_completeness_of_a_full_profile() -> None:
    result = completeness(copy.deepcopy(FULL))
    assert result.overall == 100
    assert all(s.percent == 100 and s.missing == [] for s in result.sections.values())


def test_completeness_of_a_partial_profile() -> None:
    sections = {
        "identity": validate_section(
            "identity", {"full_name": "Ada", "email": "ada@example.org", "city": "Paris"}
        ),
        "experience": validate_section(
            "experience", {"employers": FULL["experience"]["employers"]}
        ),
        "facts": FULL["facts"],
        "search": validate_section(
            "search",
            {
                **FULL["search"],
                "boards": [{**FULL["search"]["boards"][1]}],  # only a disabled board
            },
        ),
    }
    result = completeness(sections)
    by = result.sections
    assert (by["identity"].percent, by["identity"].missing) == (
        43,
        ["headline", "phone", "country", "languages"],
    )
    assert (by["experience"].percent, by["experience"].missing) == (50, ["education"])
    assert (by["facts"].percent, by["facts"].missing) == (100, [])
    assert by["skills"].percent == 0
    assert (by["search"].percent, by["search"].missing) == (80, ["boards"])
    assert by["answers"].percent == 0
    assert result.overall == round((43 + 50 + 100 + 0 + 80 + 0) / 6)


def test_completeness_of_a_saved_but_empty_section() -> None:
    assert completeness({"skills": validate_section("skills", {})}).sections["skills"].percent == 0
    zero_years = completeness({"skills": validate_section("skills", {"years_experience": 0})})
    assert zero_years.sections["skills"].missing == ["have"]  # 0 years is a real answer
