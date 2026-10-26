"""Profile items, the pure part (app/ingestion/items.py): the field type of every path, what is
never proposable, the key of every section, the value as the section model cleans it and its
hash, the bounds on a hostile value, apply (add, replace, remove, where a new entry goes) and
its inverse, and the properties: apply then read gives the item, applying twice changes nothing,
two independent items commute. Every name and address here is made up."""

import itertools
import json
import logging
import random
import types
import typing
from typing import Any

import pytest
from pydantic import BaseModel

from app.ingestion import citations, items
from app.ingestion.citations import FieldType
from app.ingestion.items import Action, ItemError, Origin, ProfileConflict
from app.profile import schemas
from tests.profile.conftest import FULL, full

MARKER = "ZZMARKERZZ"

EMPLOYER = {"organisation": "Globex", "title": "Engineer", "start": "2024", "end": "2025-08"}
SCHOOL = {"school": "Night School", "degree": "BSc", "start": "2019-09", "end": "2022-06"}


def build(section: str, key: str, value: Any = None, action: str = "add", **kwargs: Any) -> Any:
    return items.build(section, key, action, value, **kwargs)


def refusal(section: Any, key: Any, value: Any = None, action: Any = "add", **kwargs: Any) -> Any:
    with pytest.raises(ItemError) as caught:
        items.build(section, key, action, value, **kwargs)
    return caught.value


def proposable(slot: items.Slot, entry: dict[str, Any]) -> dict[str, Any]:
    return {name: child for name, child in entry.items() if name not in slot.setting_fields}


def sample_items() -> list[items.Item]:
    """One agent item per proposable thing the complete sample profile holds, plus a new
    employer and a new school. Lists written as regular expressions are left out: an item never
    writes one."""
    found: list[items.Item] = []
    for section, slots in items.SLOTS.items():
        for name, slot in slots.items():
            stored = FULL[section][name]
            if slot.kind is items.SlotKind.setting or slot.derived:
                continue
            if slot.kind is items.SlotKind.terms:
                if not stored["advanced_regex"]:
                    found.append(build(section, name, {"terms": stored["terms"]}))
            elif slot.keyed:
                for entry in stored:
                    if isinstance(entry, dict) and entry.get("advanced_regex"):
                        continue
                    value = proposable(slot, entry) if isinstance(entry, dict) else entry
                    found.append(build(section, name, value))
            else:
                found.append(build(section, name, stored))
    found.append(build("experience", "employers", EMPLOYER))
    found.append(build("experience", "education", SCHOOL))
    return found


SAMPLES = sample_items()
BASES = {"empty": None, "full": "full"}


def base(section: str, which: str) -> dict[str, Any]:
    return items.canonical(section, None if which == "empty" else full(section))


def holds(section: str, data: dict[str, Any], item: items.Item) -> bool:
    """Whether the section holds the item's value at its key."""
    stored = items.read(section, data, item.key)
    if item.slot.kind is items.SlotKind.terms:
        folded = {term.casefold() for term in stored["terms"]}
        return all(term.casefold() in folded for term in item.value["terms"])
    if item.slot.kind is items.SlotKind.words:
        return all(word in stored for word in item.value)
    if isinstance(item.value, dict):
        return all(
            stored[name] == child
            for name, child in item.value.items()
            if child is not None and name != "terms"
        )
    return bool(stored == item.value)


# --- field types ----------------------------------------------------------------------------------


def test_every_path_of_every_section_has_a_type() -> None:
    assert set(items.section_paths()) == set(items.FIELD_TYPES)
    assert all(isinstance(kind, FieldType) for kind in items.FIELD_TYPES.values())


def test_a_field_added_to_a_section_without_a_type_is_seen(monkeypatch: pytest.MonkeyPatch) -> None:
    class Wider(schemas.SECTION_MODELS["facts"]):  # type: ignore[misc, name-defined]
        added_later: str | None = None

    monkeypatch.setattr(schemas, "SECTION_MODELS", {**schemas.SECTION_MODELS, "facts": Wider})
    assert set(items.section_paths()) - set(items.FIELD_TYPES) == {"facts.added_later"}


def test_term_and_enum_only_where_the_field_is_one() -> None:
    types = items.FIELD_TYPES
    assert types["skills.have[].terms[]"] is FieldType.TERM
    assert types["experience.employers[].contract"] is FieldType.ENUM
    assert types["identity.links[].label"] is FieldType.TEXT  # a caption, seldom in the document
    assert types["identity.full_name"] is FieldType.NAME
    assert types["experience.employers[].start"] is FieldType.DATE
    unchecked = {path for path, kind in types.items() if kind in (FieldType.TERM, FieldType.ENUM)}
    assert not {path for path in unchecked if path.startswith(("identity.", "facts."))}
    assert not {path for path in unchecked if "organisation" in path or "school" in path}


def test_settings_are_typed_setting() -> None:
    types = items.FIELD_TYPES
    schedule = [path for path in types if path.startswith("search.sweep_schedule")]
    assert schedule
    settings = [
        *schedule,
        *[path for path in types if path.endswith(".advanced_regex")],
        *[path for path in types if path.startswith("search.boards[].extra_exclude")],
        "skills.gates[].penalty",
        "search.min_fit",
        "search.days",
        "search.boards[].enabled",
        *[path for path in types if path.startswith("search.browser_sources")],
        "search.browser_sources[].accepted_at",
        "search.browser_sources[].text_version",
        "search.browser_sources[].interactive_only",
        *[path for path in types if path.startswith("search.feeds")],
        "search.feeds[].kind",
        "search.feeds[].terms.terms[]",
        *[path for path in types if path.startswith("search.interactive_")],
        "search.interactive_sites[].url",
        "search.interactive_instructions",
        "experience.employers[].key",
    ]
    assert all(types[path] is FieldType.SETTING for path in settings)


def test_the_types_of_an_item_are_those_of_its_slot() -> None:
    for item in SAMPLES:
        assert item.field_types
        assert all(isinstance(kind, FieldType) for kind in item.field_types.values())
    employer = build("experience", "employers", EMPLOYER)
    assert employer.field_types["organisation"] is FieldType.NAME
    assert employer.field_types["start"] is FieldType.DATE
    assert build("identity", "email", "ada@example.org").field_types == {"": FieldType.EMAIL}
    assert build("search", "contract_types", ["CDI"]).field_types == {"[]": FieldType.ENUM}
    assert build("search", "never_apply", "No sales roles.").field_types == {"": FieldType.TEXT}


def test_the_citation_checker_reads_every_item_with_its_types() -> None:
    document = "Globex\nEngineer\nJanuary 2024 to August 2025\nBordeaux"
    employer = build(
        "experience",
        "employers",
        {"organisation": "Globex", "title": "Engineer", "start": "2024-01", "end": "2025-08"},
    )
    accepted = citations.check_item(employer.field_types, employer.value, document, [document])
    assert isinstance(accepted, citations.Accepted)
    other = build("experience", "employers", {**EMPLOYER, "organisation": "Initech"})
    refused = citations.check_item(other.field_types, other.value, document, [document])
    assert isinstance(refused, citations.Refusal)
    for item in SAMPLES:  # whatever the document, the checker answers and never raises
        answer = citations.check_item(item.field_types, item.value, "nothing", ["nothing"])
        assert isinstance(answer, citations.Accepted | citations.Refusal)


# --- never proposable -----------------------------------------------------------------------------

GATE = {"name": "phd", "terms": ["phd"]}
BOARD = {"company": "Acme", "ats": "ashby", "org": "acme"}


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("search", "sweep_schedule", {"enabled": True}),
        ("search", "browser_sources", {"kind": "vie", "query": "data", "enabled": True}),
        (
            "search",
            "browser_sources",
            {"kind": "linkedin", "accepted_at": "2026-10-20T09:00:00+02:00", "text_version": 1},
        ),
        ("search", "browser_sources", {"kind": "linkedin", "accepted_at": None}),
        ("search", "feeds", {"kind": "remoteok", "enabled": True}),
        ("search", "feeds", {"kind": "hackernews_freelance", "terms": {"terms": ["python"]}}),
        ("search", "min_fit", 80),
        ("search", "days", 3),
        ("skills", "years_experience", 4),
        ("skills", "gates", {**GATE, "penalty": 90}),
        ("skills", "have", {**GATE, "advanced_regex": True}),
        ("search", "title_include", {"terms": ["ai"], "advanced_regex": True}),
        ("search", "boards", {**BOARD, "enabled": False}),
        ("search", "boards", {**BOARD, "extra_exclude": {"terms": ["sales"]}}),
        ("experience", "employers", {**EMPLOYER, "key": "chosen"}),
    ],
)
def test_an_agent_never_proposes_a_setting(section: str, key: str, value: Any) -> None:
    for action in ("add", "replace"):
        error = refusal(section, key, value, action)
        assert error.rule == items.SETTING
        assert error.reason == items.REASONS[items.SETTING]


@pytest.mark.parametrize("key", ["sweep_schedule", "browser_sources", "min_fit", "days"])
def test_a_setting_cannot_be_removed_either(key: str) -> None:
    assert refusal("search", key, None, "remove").rule == items.SETTING


@pytest.mark.parametrize(
    ("section", "key", "rule"),
    [
        ("photo", "photo", items.UNKNOWN_SECTION),
        ("settings", "theme", items.UNKNOWN_SECTION),
        (None, "full_name", items.UNKNOWN_SECTION),
        ("identity", "photo", items.UNKNOWN_KEY),
        ("identity", "", items.UNKNOWN_KEY),
        ("identity", 7, items.UNKNOWN_KEY),
        ("identity", "full_name" + "x" * items.KEY_MAX, items.UNKNOWN_KEY),
    ],
)
def test_the_photo_and_what_is_not_a_section_have_no_key(section: Any, key: Any, rule: str) -> None:
    assert refusal(section, key, "x").rule == rule


def test_the_years_of_experience_are_derived_or_typed_by_the_user() -> None:
    assert build("skills", "years_experience", 4, origin=Origin.derived).value == 4
    assert build("skills", "years_experience", 4.5, origin=Origin.user).value == 4.5
    assert refusal("identity", "full_name", "Ada", origin=Origin.derived).rule == items.UNKNOWN_KEY


def test_a_user_may_set_a_setting_inside_the_entry_they_edit() -> None:
    gate = build("skills", "gates", {**GATE, "penalty": 90}, origin=Origin.user)
    assert gate.value["penalty"] == 90
    assert refusal("search", "min_fit", 80, origin=Origin.user).rule == items.SETTING


def test_a_proposed_gate_takes_the_default_penalty() -> None:
    gate = build("skills", "gates", GATE)
    assert "penalty" not in gate.value
    assert items.apply("skills", None, gate)["gates"] == [
        {"name": "phd", "terms": ["phd"], "advanced_regex": False, "penalty": 25}
    ]


# --- keys -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("section", "slot", "value", "key"),
    [
        ("identity", "full_name", "Ada Example", "full_name"),
        ("identity", "headline", "GenAI Engineer", "headline"),
        ("identity", "languages", {"name": "Français", "level": "C1"}, "languages:français"),
        ("identity", "languages", {"name": "FRANÇAIS ", "level": "C1"}, "languages:français"),
        (
            "identity",
            "links",
            {"label": "Code", "url": "https://www.GitHub.com/ada/"},
            "links:github.com/ada",
        ),
        ("experience", "employers", EMPLOYER, "employers:globex-2024"),
        ("experience", "employers", {**EMPLOYER, "start": "2024-03"}, "employers:globex-2024"),
        ("experience", "education", SCHOOL, "education:night school:2019"),
        (
            "experience",
            "education",
            {"school": "Night School", "degree": "BSc"},
            "education:night school:",
        ),
        ("facts", "blocks", {"title": "Hard  Rules", "body": "None."}, "blocks:hard rules"),
        ("skills", "have", {"name": "agents", "terms": ["agent"]}, "have:agents"),
        ("skills", "adjacent", {"name": "rag", "terms": ["rag"]}, "adjacent:rag"),
        ("skills", "none", {"name": "research", "terms": ["pytorch"]}, "none:research"),
        ("skills", "gates", GATE, "gates:phd"),
        ("search", "title_include", {"terms": ["ai engineer"]}, "title_include"),
        ("search", "contract_types", ["CDI"], "contract_types"),
        ("search", "remote_stance", "hybrid", "remote_stance"),
        ("search", "boards", {**BOARD, "org": "Acme"}, "boards:ashby:acme"),
        (
            "answers",
            "work_authorization",
            {"country": "France", "answer": "EU citizen.", "needs_sponsorship": False},
            "work_authorization:france",
        ),
        ("answers", "notice_period", "One month.", "notice_period"),
        ("answers", "leave_blank", ["gender"], "leave_blank"),
    ],
)
def test_the_key_of_every_section(section: str, slot: str, value: Any, key: str) -> None:
    item = build(section, slot, value)
    assert item.key == key
    # the key the model typed is never read for an add: the value says what the fact is
    assert build(section, f"{slot}:whatever the model wrote", value).key == key
    assert items.parse_key(section, key) == (items.SLOTS[section][slot], key)
    assert items.from_payload(payload_of(item)) == item


def payload_of(item: items.Item) -> dict[str, Any]:
    return {
        "section": item.section,
        "key": item.key,
        "action": item.action.value,
        "value": item.value,
    }


def test_a_line_is_keyed_by_the_hash_of_its_text() -> None:
    first = build("search", "never_apply", "No cybersecurity roles.")
    assert first.key.startswith("never_apply:")
    assert len(first.key.removeprefix("never_apply:")) == 16
    assert build("search", "never_apply", "no  CYBERSECURITY roles.").key == first.key
    assert build("search", "never_apply", "No sales roles.").key != first.key


def test_a_removal_needs_the_whole_key_and_carries_no_value() -> None:
    item = build("identity", "languages:FRENCH ", action="remove")
    assert (item.key, item.value, item.action) == ("languages:french", None, Action.remove)
    assert item.value_hash == items.value_hash(None)
    assert refusal("identity", "languages", None, "remove").rule == items.UNKNOWN_KEY
    assert refusal("identity", "full_name:x", None, "remove").rule == items.UNKNOWN_KEY
    assert refusal("identity", "languages:French", {"name": "French"}, "remove").rule == (
        items.VALUE_NOT_EXPECTED
    )
    assert refusal("identity", "languages", {"name": "French"}, "merge").rule == (
        items.UNKNOWN_ACTION
    )


def test_what_is_locked() -> None:
    assert items.is_locked("identity", "full_name")
    assert items.is_locked("identity", "languages:french")
    assert not items.is_locked("identity", "headline")
    assert items.is_locked("experience", "employers:globex-2024")
    assert not items.is_locked("facts", "blocks:hard rules")
    assert not items.is_locked("skills", "have:agents")
    assert items.is_locked("identity", "nothing known")  # what is unknown is never taken in bulk
    assert items.is_locked("nowhere", "full_name")
    assert build("identity", "email", "ada@example.org").locked
    assert not build("facts", "blocks", {"title": "T", "body": "B"}).locked


# --- the value ------------------------------------------------------------------------------------


def test_the_value_and_its_hash_are_those_of_the_cleaned_form() -> None:
    plain = build("identity", "full_name", "Amélie Example")
    assert plain.value == "Amélie Example"
    for spelling in ("  Amélie Example ", "Amélie Example", "Amélie​ Example"):
        item = build("identity", "full_name", spelling)
        assert (item.value, item.value_hash) == (plain.value, plain.value_hash)
    assert plain.value_hash == items.value_hash("Amélie Example")
    assert items.value_hash(["CDI", "VIE"]) == items.value_hash(["VIE", "CDI"])
    assert items.value_hash({"a": 1, "b": None}) == items.value_hash({"b": None, "a": 1})
    assert items.value_hash("a") != items.value_hash("b")
    employer = build("experience", "employers", EMPLOYER)
    assert employer.value["location"] is None  # what the model leaves out is null, as stored
    assert "key" not in employer.value  # the slug of an employer is never proposed


@pytest.mark.parametrize(
    ("section", "slot", "value", "index"),
    [
        ("experience", "employers", {**EMPLOYER, "start": f"March {MARKER}"}, "value.start"),
        ("experience", "employers", {**EMPLOYER, "start": "2025-09", "end": "2024-01"}, "value"),
        ("experience", "employers", {"title": "Engineer"}, None),
        ("identity", "email", f"not an address {MARKER}", "value"),
        ("identity", "links", {"label": "Code", "url": f"javascript:{MARKER}"}, "value.url"),
        ("search", "contract_types", ["CDI", MARKER], "value[1]"),
        ("search", "remote_stance", MARKER, "value"),
        ("identity", "full_name", {"first": "Ada"}, None),
    ],
)
def test_a_value_the_section_model_refuses(
    section: str, slot: str, value: Any, index: str | None
) -> None:
    error = refusal(section, slot, value)
    assert error.rule in (items.INVALID_VALUE, items.UNKNOWN_FIELD)
    if index is not None:
        assert error.index == index
    assert MARKER not in error.reason
    assert MARKER not in str(error)


def test_an_agent_writes_a_date_in_ascii_digits() -> None:
    value = {**EMPLOYER, "start": "٢٠٢٤"}
    error = refusal("experience", "employers", value)
    assert (error.rule, error.index, error.reason) == (
        items.INVALID_VALUE,
        "value.start",
        items.ASCII_DATE,
    )
    # the user's own edit goes through the section model alone, which reads other scripts
    edited = build("experience", "employers", value, origin=Origin.user)
    assert edited.key == "employers:globex-2024"


@pytest.mark.parametrize("value", [None, "", "   ", "​"])
def test_an_empty_value_is_refused(value: Any) -> None:
    assert refusal("identity", "full_name", value).rule in (items.VALUE_EMPTY, items.INVALID_VALUE)
    assert refusal("identity", "full_name", None).rule == items.VALUE_EMPTY


def test_a_hostile_value_is_bounded() -> None:
    deep: Any = "x"
    for _ in range(items.VALUE_DEPTH_MAX + 2):
        deep = [deep]
    assert refusal("facts", "blocks", {"title": "T", "body": deep}, origin=Origin.user).rule == (
        items.VALUE_TOO_LARGE
    )
    many = [f"term {number}" for number in range(items.VALUE_NODES_MAX + 1)]
    assert refusal("skills", "have", {"name": "agents", "terms": many}).rule == (
        items.VALUE_TOO_LARGE
    )
    long = "a" * (items.VALUE_CHARS_MAX + 1)
    assert refusal("facts", "blocks", {"title": "T", "body": long}).rule == items.VALUE_TOO_LARGE
    for origin in (Origin.agent, Origin.user):
        wide = {"title": "T", "body": "B", "x" * (items.FIELD_NAME_MAX + 1): MARKER}
        error = refusal("facts", "blocks", wide, origin=origin)
        assert error.rule == items.UNKNOWN_FIELD
    unknown = refusal("facts", "blocks", {"title": "T", "body": "B", "evil": MARKER})
    assert unknown.rule == items.UNKNOWN_FIELD
    assert MARKER not in unknown.reason
    assert refusal("facts", "blocks", {"title": "T", "body": [MARKER]}).rule == items.UNKNOWN_FIELD
    assert refusal("facts", "blocks", {"title": "T", "body": b"bytes"}).rule == items.INVALID_VALUE
    assert refusal("facts", "blocks", {7: "T"}).rule == items.UNKNOWN_FIELD


def test_invisible_characters_are_removed_from_what_an_agent_proposes() -> None:
    assert items.clean_text("A​da‮ Exa­mple﻿") == "Ada Example"
    assert items.clean_text("plain") == "plain"
    assert items.clean_text("क्‍ष") == "क्‍ष"  # a joiner
    assert items.clean_text("a‍b") == "ab"  # no joiner between ASCII letters
    assert items.clean_text("❤️") == "❤️"  # an emoji keeps its selector
    assert items.clean_text("a️") == "a"
    block = build("facts", "blocks", {"title": "Ru​les", "body": "No‮ metric."})
    assert block.value == {"title": "Rules", "body": "No metric."}
    assert block.key == "blocks:rules"
    assert items.fold(" Hard​  RULES ") == "hard rules"
    assert items.slug("Acme Group") == "acme-group"
    assert items.slug("Société Générale") == "societe-generale"
    assert items.slug("株式会社") != items.slug("有限会社")
    assert len(items.slug("a very long organisation name " * 4)) <= 30


def test_nothing_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    refusal("identity", "email", MARKER)
    refusal("facts", "blocks", {"title": MARKER, "evil": MARKER})
    item = build("facts", "blocks", {"title": MARKER, "body": MARKER})
    items.apply("facts", full("facts"), item)
    assert caplog.records == []


def test_a_stored_item_is_read_again_by_todays_model() -> None:
    item = build("identity", "languages", {"name": "French", "level": "C1"})
    stored = {**payload_of(item), "flags": []}
    assert items.from_payload(stored) == item
    renamed = items.from_payload({**stored, "key": "languages:german"})
    assert renamed.key == "languages:german"  # the stored key says where it applies
    years = build("skills", "years_experience", 4, "replace", origin=Origin.derived)
    assert items.from_payload({**payload_of(years), "flags": [items.FLAG_DERIVED]}) == years
    for broken in (
        {**stored, "section": "nowhere"},
        {**stored, "key": "nothing"},
        {**stored, "action": "merge"},
        {**stored, "value": {"name": "French", "evil": 1}},
        {**payload_of(years), "flags": []},
        {},
    ):
        with pytest.raises(ItemError):
            items.from_payload(broken)


# --- apply ----------------------------------------------------------------------------------------


def organisations(section: dict[str, Any]) -> list[str]:
    return [employer["organisation"] for employer in section["employers"]]


def test_a_new_employer_is_inserted_newest_first_and_nothing_else_moves() -> None:
    data = full("experience")
    assert organisations(data) == ["Acme Group", "Clinic"]

    def add(**fields: Any) -> list[str]:
        nonlocal data
        value = {"title": "Engineer", **fields}
        data = items.apply("experience", data, build("experience", "employers", value))
        return organisations(data)

    # a year alone start and a month end, older than both: last
    assert add(organisation="Initech", start="2021", end="2022-03")[-1] == "Initech"
    # the same end as Clinic and a later start: just above it
    assert add(organisation="Globex", start="2024", end="2025-08") == [
        "Acme Group",
        "Globex",
        "Clinic",
        "Initech",
    ]
    # the very rank of Clinic: after it (a tie never passes an entry that was there)
    assert add(organisation="Twin", start="2023-01", end="2025-08").index("Twin") == 3
    # a year alone lists after the months of its year
    assert add(organisation="Yearly", start="2023-01", end="2025").index("Yearly") == 4
    # no end is the present, above every dated end; a later start above an earlier one
    assert add(organisation="Now Corp", start="2026-01")[0] == "Now Corp"
    assert add(organisation="Earlier Now", start="2020")[:3] == [
        "Now Corp",
        "Acme Group",
        "Earlier Now",
    ]
    assert [name for name in organisations(data) if name in ("Acme Group", "Clinic")] == [
        "Acme Group",
        "Clinic",
    ]


def test_a_new_school_is_inserted_by_its_dates_and_one_without_dates_goes_last() -> None:
    data = items.apply("experience", full("experience"), build("experience", "education", SCHOOL))
    assert [school["school"] for school in data["education"]] == [
        "School of Engineering",
        "Night School",
        "High school",
    ]
    undated = build("experience", "education", {"school": "Evening classes", "degree": "None"})
    data = items.apply("experience", data, undated)
    assert data["education"][-1]["school"] == "Evening classes"
    started = build(
        "experience", "education", {"school": "Bootcamp", "degree": "None", "start": "2026-01"}
    )
    assert items.apply("experience", data, started)["education"][0]["school"] == "Bootcamp"


def test_an_employer_gets_a_slug_and_an_existing_one_keeps_its_own() -> None:
    globex = build("experience", "employers", EMPLOYER)
    data = items.apply("experience", None, globex)
    assert data["employers"][0]["key"] == "globex-2024"
    taken = full("experience")
    taken["employers"][1]["key"] = "globex-2024"  # another employer already holds that slug
    after = items.apply("experience", taken, globex)
    assert [employer["key"] for employer in after["employers"]] == [
        "acme",
        "globex-2024-2",
        "globex-2024",
    ]
    senior = {"organisation": "Acme Group", "title": "Senior Engineer", "start": "2025"}
    added = items.apply("experience", full("experience"), build("experience", "employers", senior))
    assert added["employers"][0] == {
        **full("experience")["employers"][0],
        "title": "Senior Engineer",
        "start": "2025",
    }
    replaced = items.apply(
        "experience", full("experience"), build("experience", "employers", senior, "replace")
    )
    assert replaced["employers"][0] == {
        "key": "acme",
        "organisation": "Acme Group",
        "title": "Senior Engineer",
        "start": "2025",
        "end": None,
        "location": None,
        "contract": None,
    }
    assert organisations(replaced) == ["Acme Group", "Clinic"]


def test_add_replace_and_remove_on_an_existing_and_on_a_missing_key() -> None:
    identity = full("identity")
    german = {"name": "German", "level": "B1"}
    added = items.apply("identity", identity, build("identity", "languages", german))
    assert added["languages"] == [*identity["languages"], german]
    for action in ("add", "replace"):
        level = build("identity", "languages", {"name": "english", "level": "C2"}, action)
        changed = items.apply("identity", identity, level)["languages"]
        assert changed == [identity["languages"][0], {"name": "english", "level": "C2"}]
    removal = build("identity", "languages:French", action="remove")
    assert items.apply("identity", identity, removal)["languages"] == [identity["languages"][1]]
    missing = build("identity", "languages:Klingon", action="remove")
    assert items.apply("identity", identity, missing) == items.canonical("identity", identity)
    assert items.apply("identity", None, missing) == items.canonical("identity", None)
    cleared = items.apply("identity", identity, build("identity", "headline", action="remove"))
    assert cleared["headline"] is None
    assert items.read("identity", cleared, "headline") is None
    assert items.read("identity", identity, "languages:FRENCH") == identity["languages"][0]
    assert items.read("identity", None, "languages:french") is None


def test_words_and_terms_are_joined_by_an_add_and_set_by_a_replace() -> None:
    search = full("search")
    more = build("search", "title_include", {"terms": ["LLM", "genai"]})
    joined = items.apply("search", search, more)["title_include"]
    assert [term.casefold() for term in joined["terms"]] == ["ai engineer", "llm", "genai"]
    exact = build("search", "title_include", {"terms": ["genai"]}, "replace")
    assert items.apply("search", search, exact)["title_include"]["terms"] == ["genai"]
    emptied = build("search", "title_include", action="remove")
    assert items.apply("search", search, emptied)["title_include"]["terms"] == []
    assert items.read("search", items.apply("search", search, emptied), "title_include") is None
    contracts = build("search", "contract_types", ["Stage", "CDI"])
    assert items.apply("search", search, contracts)["contract_types"] == ["CDI", "VIE", "Stage"]
    only = build("search", "contract_types", ["Stage"], "replace")
    assert items.apply("search", search, only)["contract_types"] == ["Stage"]
    none = build("search", "contract_types", action="remove")
    assert items.apply("search", search, none)["contract_types"] == []
    category = build("skills", "have", {"name": "agents", "terms": ["Agentic", "tool use"]})
    terms = items.apply("skills", full("skills"), category)["have"][0]["terms"]
    assert [term.casefold() for term in terms] == ["agent", "agentic", "tool use"]
    line = build("search", "never_apply", "no cybersecurity ROLES.")
    assert items.apply("search", search, line)["never_apply"] == search["never_apply"]
    spelled = items.apply("search", search, build("search", "never_apply", line.value, "replace"))
    assert spelled["never_apply"] == ["no cybersecurity ROLES."]
    new = items.apply("search", search, build("search", "never_apply", "No sales roles."))
    assert new["never_apply"] == [*search["never_apply"], "No sales roles."]


def test_a_setting_of_a_stored_entry_is_kept_by_an_item() -> None:
    skills = full("skills")
    skills["gates"][0] = {"name": "phd", "terms": ["phd"], "advanced_regex": False, "penalty": 70}
    for action in ("add", "replace"):
        gate = build("skills", "gates", {"name": "phd", "terms": ["doctorate"]}, action)
        assert items.apply("skills", skills, gate)["gates"][0]["penalty"] == 70
    board = build("search", "boards", {"company": "Anthropic", "ats": "greenhouse", "org": "x"})
    stored = full("search")
    stored["boards"][1]["org"] = "x"
    after = items.apply("search", stored, board)["boards"][1]
    assert (after["enabled"], after["extra_exclude"]["terms"]) == (False, ["sales"])


def test_a_section_that_cannot_take_the_item_is_a_conflict() -> None:
    with pytest.raises(ProfileConflict) as advanced:
        items.apply(
            "search", full("search"), build("search", "title_exclude", {"terms": ["intern"]})
        )
    assert advanced.value.message == items.ADVANCED_LIST
    with pytest.raises(ProfileConflict):
        items.apply(
            "skills",
            full("skills"),
            build("skills", "have", {"name": "fine_tuning", "terms": ["lora"]}),
        )
    same = build("skills", "have", {"name": "fine_tuning", "terms": [r"fine[- ]?tun\w*"]})
    assert items.apply("skills", full("skills"), same) == items.canonical("skills", full("skills"))
    identity = full("identity")
    identity["links"] = [
        {"label": f"Link {number}", "url": f"https://example.org/{number}"} for number in range(10)
    ]
    one_more = build("identity", "links", {"label": MARKER, "url": "https://example.org/more"})
    with pytest.raises(ProfileConflict) as capped:
        items.apply("identity", identity, one_more)
    assert MARKER not in capped.value.message


def test_keys_that_collide_after_normalisation() -> None:
    facts = {
        "blocks": [
            {"title": "Hard rules", "body": "First."},
            {"title": "hard  RULES", "body": "Second."},
            {"title": "Other", "body": "Third."},
        ]
    }
    assert items.read("facts", facts, "blocks:Hard Rules")["body"] == "First."
    assert items.held("facts", facts, "blocks:hard rules") is None
    # an item changes one entry at most: where two share the key, no action applies
    value = {"title": "HARD RULES", "body": "New."}
    for action, given in (("add", value), ("replace", value), ("remove", None)):
        key = "blocks" if given else "blocks:hard rules"
        with pytest.raises(ProfileConflict) as shared:
            items.apply("facts", facts, build("facts", key, given, action=action))
        assert shared.value.message == items.SHARED_KEY
        # the user's own value for the proposal, applied where the proposal was
        if given:
            typed = build("facts", "blocks", {"title": "Mine", "body": "x"}, origin=Origin.user)
            with pytest.raises(ProfileConflict):
                items.apply("facts", facts, typed, at="blocks:hard rules")
    lines = {"never_apply": ["No sales roles.", "no  SALES roles."]}
    line = build("search", "never_apply", "No sales roles.")
    with pytest.raises(ProfileConflict):
        items.apply("search", lines, build("search", line.key, action="remove"))
    # once the entries are told apart, the item applies to the one at its key and to no other
    facts["blocks"][1]["title"] = "Soft rules"
    removal = build("facts", "blocks:hard rules", action="remove")
    assert items.apply("facts", facts, removal)["blocks"] == facts["blocks"][1:]


def test_an_edit_applies_where_the_proposal_was() -> None:
    renamed = build("identity", "languages", {"name": "Deutsch", "level": "B2"}, origin=Origin.user)
    after = items.apply("identity", full("identity"), renamed, at="languages:english")
    assert [language["name"] for language in after["languages"]] == ["French", "Deutsch"]


# --- properties -----------------------------------------------------------------------------------


@pytest.mark.parametrize("which", BASES)
def test_apply_then_read_gives_the_item(which: str) -> None:
    for item in SAMPLES:
        after = items.apply(item.section, base(item.section, which), item)
        assert holds(item.section, after, item), item.key
        replaced = items.Item(item.section, item.key, Action.replace, item.value, item.value_hash)
        exact = items.apply(item.section, base(item.section, which), replaced)
        assert holds(item.section, exact, item), item.key
        removal = items.build(item.section, item.key, "remove")
        assert items.read(item.section, items.apply(item.section, after, removal), item.key) is None


@pytest.mark.parametrize("which", BASES)
def test_applying_twice_changes_nothing(which: str) -> None:
    for item in SAMPLES:
        for action in Action:
            value = None if action is Action.remove else item.value
            again = items.Item(item.section, item.key, action, value, items.value_hash(value))
            once = items.apply(item.section, base(item.section, which), again)
            assert items.apply(item.section, once, again) == once, (item.key, action)


def test_an_item_the_profile_holds_changes_nothing() -> None:
    for item in SAMPLES[:-2]:  # all but the new employer and the new school
        stored = base(item.section, "full")
        assert items.apply(item.section, stored, item) == stored, item.key


def unordered(section: str, data: dict[str, Any]) -> dict[str, Any]:
    """The section with its appended lists read as sets: two new entries of one such list are
    listed in the order they were accepted, which is all that depends on the order."""
    if section == "experience":
        return data
    return {
        name: sorted(child, key=lambda entry: json.dumps(entry, sort_keys=True))
        if isinstance(child, list)
        else child
        for name, child in data.items()
    }


@pytest.mark.parametrize("which", BASES)
def test_two_independent_items_commute(which: str) -> None:
    rng = random.Random(51)  # noqa: S311, a fixed draw of actions, nothing secret
    pairs = [
        (first, second)
        for first, second in itertools.combinations(SAMPLES, 2)
        if first.section == second.section and first.key != second.key
    ]
    assert len(pairs) > 40
    for first, second in pairs:
        actions = [rng.choice(list(Action)), rng.choice(list(Action))]
        steps = [
            items.Item(item.section, item.key, action, value, items.value_hash(value))
            for item, action in zip((first, second), actions, strict=True)
            for value in [None if action is Action.remove else item.value]
        ]
        section = first.section
        one = items.apply(section, items.apply(section, base(section, which), steps[0]), steps[1])
        two = items.apply(section, items.apply(section, base(section, which), steps[1]), steps[0])
        assert unordered(section, one) == unordered(section, two), (first.key, second.key, actions)
        if first.slot is not second.slot:
            assert one == two


@pytest.mark.parametrize("which", BASES)
def test_restore_is_the_inverse_of_apply(which: str) -> None:
    for item in SAMPLES:
        for action in Action:
            value = None if action is Action.remove else item.value
            step = items.Item(item.section, item.key, action, value, items.value_hash(value))
            before = base(item.section, which)
            after = items.apply(item.section, before, step)
            record = items.held(item.section, before, item.key)
            assert record is not None
            made = items.held(item.section, after, item.key)
            assert made is not None
            assert (items.held_hash(made) == items.held_hash(record)) == (after == before)
            assert items.restore(item.section, after, item.key, record) == before, (
                item.key,
                action,
            )
            assert json.loads(json.dumps(record)) == record  # it is stored as JSON


def test_an_entry_removed_returns_to_its_place_or_to_the_end() -> None:
    identity = full("identity")
    record = items.held("identity", identity, "languages:french")
    assert record == {"entries": [[0, identity["languages"][0]]]}
    removed = items.apply(
        "identity", identity, build("identity", "languages:french", action="remove")
    )
    assert items.restore("identity", removed, "languages:french", record) == identity
    emptied = {**removed, "languages": []}
    late = items.held("identity", identity, "languages:english")
    assert late is not None
    assert items.restore("identity", emptied, "languages:english", late)["languages"] == [
        identity["languages"][1]
    ]
    full_list = {
        **identity,
        "languages": [{"name": f"Language {n}", "level": "A1"} for n in range(20)],
    }
    with pytest.raises(ProfileConflict):
        items.restore("identity", full_list, "languages:french", record)


# --- one key per fact, whatever the name (review of PR #109, B1) ---------------------------------

EXPANDING = "\ufdfa" * 30  # one character each, eighteen once NFKC has run
HOSTILE_NAMES = [
    EXPANDING,
    "\u3350" * 150,
    "\u3350" * 40,
    "\u00df",
    "STRASSE",
    "\uff26\uff55\uff4c\uff4c\u3000\uff37\uff49\uff44\uff54\uff48",
    "\u0130stanbul",
    "\u01c5",
    "\ufb03",
    "  padded   name ",
    "\u1fb3\u0345",
    "a\u0345\u0301",
    "\u1e9e",
    "\u2126",
    "\u0131\u017f",
    "e\u0301a\u0301",
    "\u200bzero\u200bwidth",
    "\u200b",
    "\u65e5\u672c\u8a9e",
    "~" + "0" * 32,
    "x" * 200,
    "a:b",
    "name:2020",
    "www.www.name",
    "A/B",
    "Acme",
    "ACME-Labs",
    "acme_labs",
]
HOSTILE_LINKS = [
    "https://www.www.example.com",
    "https://WWW.Example.COM/Path/",
    "https://www.example.com//",
    "https://example.com/a/?q=1#frag",
    "http://www.www.www.www.www.www.www.example.org/x",
    "https://example.com/" + "a" * 600,
    "https://www.example.com/\uff41\uff42\uff43",
    "HTTPS://EXAMPLE.COM",
    "https://example.com/?",
    "https://www.example.com/#",
    "https://www.www.www.example.com/www.",
]
NAMED: dict[tuple[str, str], Any] = {
    ("identity", "languages"): lambda name: {"name": name, "level": "B1"},
    ("identity", "links"): lambda name: {"label": "Site", "url": name},
    ("experience", "employers"): lambda name: {
        "organisation": name,
        "title": "Engineer",
        "start": "2021",
    },
    ("experience", "education"): lambda name: {"school": name, "degree": "BSc", "start": "2019"},
    ("facts", "blocks"): lambda name: {"title": name, "body": "A body."},
    ("skills", "have"): lambda name: {"name": name, "terms": ["term"]},
    ("skills", "adjacent"): lambda name: {"name": name, "terms": ["term"]},
    ("skills", "none"): lambda name: {"name": name, "terms": ["term"]},
    ("skills", "gates"): lambda name: {"name": name, "terms": ["phd"]},
    ("search", "boards"): lambda name: {"company": "Acme", "ats": "ashby", "org": name},
    ("search", "never_apply"): lambda name: name,
    ("answers", "work_authorization"): lambda name: {
        "country": name,
        "answer": "Yes.",
        "needs_sponsorship": False,
    },
}


def test_every_keyed_slot_has_a_hostile_template() -> None:
    keyed = {
        (section, name)
        for section, slots in items.SLOTS.items()
        for name, slot in slots.items()
        if slot.keyed
    }
    assert keyed == set(NAMED)


@pytest.mark.parametrize(("section", "name"), sorted(NAMED))
def test_a_key_that_build_returns_is_the_key_everything_finds(section: str, name: str) -> None:
    """For every value the section model accepts: `parse_key` accepts the key of the built item
    and returns it, a stored proposal reads back the same key, the entry is found at that key
    (once, however often the item is applied) and the stored entry computes the same key."""
    slot = items.SLOTS[section][name]
    names = HOSTILE_LINKS if name == "links" else HOSTILE_NAMES
    built = 0
    for hostile, origin in itertools.product(names, (Origin.agent, Origin.user)):
        try:
            item = build(section, name, NAMED[section, name](hostile), origin=origin)
        except ItemError:
            continue  # the section model refuses this name: nothing to key
        built += 1
        case = (section, name, names.index(hostile), origin)
        assert len(item.key) <= items.KEY_MAX, case
        assert items.parse_key(section, item.key) == (slot, item.key), case
        if origin is Origin.agent:
            assert items.from_payload(payload_of(item)).key == item.key, case
        once = items.apply(section, None, item)
        assert items.apply(section, once, item) == once, case
        assert holds(section, once, item), case
        entry = items.read(section, once, item.key)
        assert items.entry_key(slot, entry) == item.key, case
        assert len(items.held(section, once, item.key)["entries"]) == 1, case
        removal = build(section, item.key, action="remove")
        assert removal.key == item.key, case
        assert items.read(section, items.apply(section, once, removal), item.key) is None, case
    assert built >= 6, (section, name, built)


def test_a_name_that_expands_under_nfkc_has_a_short_key() -> None:
    language = build("identity", "languages", {"name": EXPANDING, "level": "B1"})
    assert len(language.key) < 60
    assert items.parse_key("identity", language.key)[1] == language.key
    assert build("identity", "languages", {"name": EXPANDING + " ", "level": "C1"}).key == (
        language.key
    )
    assert build("identity", "languages", {"name": EXPANDING[:-1], "level": "B1"}).key != (
        language.key
    )
    block = build("facts", "blocks", {"title": "\u3350" * 150, "body": "A body."})
    assert len(block.key) < 60
    assert items.from_payload(payload_of(block)).key == block.key


def test_a_link_has_one_key_however_often_it_says_www() -> None:
    link = build("identity", "links", {"label": "Site", "url": "https://www.www.example.com"})
    assert link.key == "links:example.com"
    assert items.from_payload(payload_of(link)).key == link.key
    once = items.apply("identity", None, link)
    assert items.apply("identity", once, link) == once
    assert len(once["links"]) == 1
    assert items.held("identity", once, link.key) == {"entries": [[0, once["links"][0]]]}
    assert build("identity", "links", {"label": "Site", "url": "https://example.com/"}).key == (
        link.key
    )


# --- follow ups of #111 and #184 ------------------------------------------------------------------

HEX = "0123456789abcdef" * 2
FULL_WIDTH = {ord(c): ord(c) + 0xFEE0 for c in "0123456789abcdef"}


@pytest.mark.parametrize(
    ("key", "field", "value"),
    [
        ("blocks", "title", {"title": "~" + HEX, "body": "A made up fact."}),
        ("blocks", "title", {"title": "  ~" + HEX.upper() + " ", "body": "Made up."}),
        # full width tilde and digits fold to the same form (NFKC)
        ("blocks", "title", {"title": "\uff5e" + HEX.translate(FULL_WIDTH), "body": "Made up."}),
        ("languages", "name", {"name": "~" + HEX, "level": "B1"}),
        ("education", "school", {"school": "~" + HEX, "degree": "MSc"}),
        ("have", "name", {"name": "~" + HEX, "terms": ["x"]}),
        ("gates", "name", {"name": "~" + HEX, "terms": ["x"]}),
        ("work_authorization", "country", {"country": "~" + HEX, "answer": "Yes."}),
    ],
)
def test_a_name_written_as_the_hash_form_of_a_key_is_refused(
    key: str, field: str, value: dict[str, Any]
) -> None:
    section = next(name for name, slots in items.SLOTS.items() if key in slots)
    for origin in (Origin.agent, Origin.user):
        with pytest.raises(ItemError) as refused:
            items.build(section, key, "add", value, origin=origin)
        assert (refused.value.index, refused.value.reason) == (f"value.{field}", items.HASH_FORM)


@pytest.mark.parametrize(
    "title",
    ["~" + HEX[:31], "~" + HEX + "0", "~" + HEX[:31] + "g", "x~" + HEX, "~ " + HEX, "~"],
)
def test_a_name_near_the_hash_form_is_kept(title: str) -> None:
    item = items.build("facts", "blocks", "add", {"title": title, "body": "Made up."})
    assert item.value["title"] == title.strip()


def test_an_edit_that_renames_an_entry_onto_another_is_a_conflict() -> None:
    facts = {"blocks": [{"title": "First", "body": "One."}, {"title": "Second", "body": "Two."}]}
    onto = items.build(
        "facts", "blocks", "add", {"title": "second", "body": "Mine."}, origin=Origin.user
    )
    for at in ("blocks:first", "blocks:not stored"):  # a stored entry, and one inserted
        with pytest.raises(ProfileConflict) as taken:
            items.apply("facts", facts, onto, at=at)
        assert taken.value.message == items.TAKEN_KEY
    fresh = items.build(
        "facts", "blocks", "add", {"title": "Third", "body": "Mine."}, origin=Origin.user
    )
    after = items.apply("facts", facts, fresh, at="blocks:first")
    assert [one["title"] for one in after["blocks"]] == ["Third", "Second"]


def _numbers(annotation: Any, path: str) -> list[str]:
    """The paths of a model whose type is a number."""
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        return _numbers(typing.get_args(annotation)[0], path)
    if origin in (typing.Union, types.UnionType):
        return sorted({p for one in typing.get_args(annotation) for p in _numbers(one, path)})
    if origin is list:
        return _numbers(typing.get_args(annotation)[0], f"{path}[]")
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        fields = annotation.model_fields.items()
        return [p for name, f in fields for p in _numbers(f.annotation, f"{path}.{name}")]
    return [path] if annotation in (int, float) else []


def test_no_proposed_value_holds_a_number_left_empty() -> None:
    """The edit form shows a field that holds null as a line of text (#184 item 10): only a
    number would be better served by a number control. The only number a proposal holds is the
    derived years (typed `setting`, built by the merge alone), never null (`build` refuses an
    empty value); a gate's penalty is a setting. A new proposable number fails here, and then
    needs its control."""
    numbers = [
        path
        for name, model in schemas.SECTION_MODELS.items()
        for path in _numbers(model, name)
        if items.FIELD_TYPES.get(path) is not FieldType.SETTING
    ]
    assert numbers == []
    with pytest.raises(ItemError):
        items.build("skills", "years_experience", "add", None, origin=Origin.derived)
