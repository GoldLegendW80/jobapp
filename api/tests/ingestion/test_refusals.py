"""Why the extraction refused items, kept and shown (owner, 2026-11-08): the counts of a step by
section and rule (`refusals.py`), how a stored correction takes them out, the retry budget that
tells every key its rule once, what the run and its review answer, and how the checker reads the
layouts of a two column CV. The CV is invented: it has the layout of the CV of the owner's run
(two columns read line by line, lists wrapped with their commas, a list one per line far from its
heading, a ligature), none of its words."""

import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.ingestion import citations, items, refusals, tools
from app.ingestion.runs_models import IngestionRunStep
from tests.agents.run_helpers import add_user
from tests.identity.api_helpers import Api
from tests.ingestion.extract_helpers import (
    Step,
    add_document,
    add_step,
    call,
    counters,
    item,
    tools_for,
)
from tests.ingestion.test_runs_api import RUNS, end_step, ready, started

__all__ = ["ready"]

PROPOSE = tools.PROPOSE
NOT_WRITTEN = "This sentence is not written anywhere in the document."
PROGRAMMING = "Python, Scala,\nGo, Rust"
DATA = "SQL, Spark,\nKafka, Airflow,\nKubernetes"
COMPETENCIES = (
    "Data modelling\nStream processing\nTeam leadership\nTechnical writing\n"
    "Arti\ufb01cial intelligence"  # the ligature a PDF gives
)
TWO_DIGITS = "Example Labs, Backend Developer, 09/21 to 02/23"
CV = "\n".join(
    [
        "Camille Durand",
        "Data Engineer",
        "Email: e2e-camille@example.org",
        "ABOUT Data engineer who builds pipelines for regional warehouses.",
        "Awards Key competencies",
        "EXPERIENCE Data Engineer, Example Works",
        "January 2021 \u2013 March 2023",
        TWO_DIGITS,
        "TECHNICAL SKILLS Programming",
        "languages",
        PROGRAMMING,
        "Data & Cloud",
        DATA,
        "LANGUAGES French: Mother tongue English: Fluent",
        "Won the regional data challenge",
        "\u2022",
        COMPETENCIES,
    ]
)
SKILLS = {
    **dict.fromkeys(("Python", "Scala", "Go", "Rust"), PROGRAMMING),
    **dict.fromkeys(("SQL", "Spark", "Kafka", "Airflow", "Kubernetes"), DATA),
    **dict.fromkeys(
        (
            "Data modelling",
            "Stream processing",
            "Team leadership",
            "Technical writing",
            "Artificial intelligence",
        ),
        COMPETENCIES,
    ),
}
UNKNOWN_FIELD = items.REASONS[items.UNKNOWN_FIELD]


@pytest.fixture
def alice_id(factory: sessionmaker[Session]) -> uuid.UUID:
    return add_user(factory, "e2e-refusals@example.org")


@pytest.fixture
def cv(factory: sessionmaker[Session], alice_id: uuid.UUID) -> Step:
    return add_step(factory, alice_id, add_document(factory, alice_id, CV))


def held(factory: sessionmaker[Session], step: uuid.UUID) -> Any:
    with factory() as db:
        return db.get_one(IngestionRunStep, step).refusals


def skill(name: str, excerpt: str, **more: Any) -> dict[str, Any]:
    return item("have", {"name": name, "terms": [name], **more}, excerpt)


def refusal(index: int, rule: str, at: str, reason: str) -> dict[str, Any]:
    return {"index": index, "rule": rule, "at": at, "reason": reason}


# --- the step keeps them --------------------------------------------------------------------------


def test_one_mistake_over_every_skill_is_told_once_per_skill_and_the_correction_clears_it(
    factory: sessionmaker[Session], cv: Step
) -> None:
    """The owner's run: refusals of another section first, then every skill of one call with
    the same wrong field. Before, the items past the budget of 20 were told not to retry, never
    why, and were lost; now each one is told its rule, and its correction is stored."""
    step_tools = tools_for(factory, cv)
    blocks = [
        item("blocks", {"title": f"Project {number}", "body": "Shipped it."}, NOT_WRITTEN)
        for number in range(10)
    ]
    assert call(step_tools, PROPOSE, section="facts", items=blocks)[0]["stored"] == 0
    shaped = [skill(name, excerpt, level="expert") for name, excerpt in SKILLS.items()]
    answer, error = call(step_tools, PROPOSE, section="skills", items=shaped)
    assert error is False
    assert answer == {
        "stored": 0,
        "already_stored": 0,
        "refused": [refusal(index, "unknown_field", "value", UNKNOWN_FIELD) for index in range(14)],
    }
    assert counters(factory, cv) == (0, 24, 0)
    assert held(factory, cv.step_id) == {
        "facts": {"excerpt_not_found": 10},
        "skills": {"unknown_field": 14},
    }
    # A key refused before, past the budget: not told again.
    answer, _ = call(step_tools, PROPOSE, section="skills", items=[shaped[0]])
    assert answer["refused"] == [{"index": 0, "reason": tools.DO_NOT_RETRY}]
    assert held(factory, cv.step_id)["skills"] == {"unknown_field": 15}
    corrected = [skill(name, excerpt) for name, excerpt in SKILLS.items()]
    answer, _ = call(step_tools, PROPOSE, section="skills", items=corrected)
    assert (answer["stored"], answer["refused"]) == (14, [])
    # What was corrected and stored is no longer "not kept"; the facts never were.
    assert held(factory, cv.step_id) == {"facts": {"excerpt_not_found": 10}}
    assert counters(factory, cv) == (14, 25, 0)


def test_settings_two_digit_years_and_items_without_a_key_count_and_a_stored_key_does_not(
    factory: sessionmaker[Session], cv: Step
) -> None:
    step_tools = tools_for(factory, cv)
    assert (
        call(step_tools, PROPOSE, section="skills", items=[skill("Python", PROGRAMMING)])[0][
            "stored"
        ]
        == 1
    )
    gate = item("gates", {"name": "Lyon", "terms": ["Lyon"], "penalty": 0}, PROGRAMMING)
    answer, _ = call(
        step_tools,
        PROPOSE,
        section="skills",
        items=[skill("Python", NOT_WRITTEN), gate, "not an item", item("have", None, DATA)],
    )
    assert [one["rule"] for one in answer["refused"]] == [
        "excerpt_not_found",
        "setting",
        "invalid_item",
        "value_empty",
    ]
    employer = {"organisation": "Example Labs", "title": "Backend Developer", "start": "2021-09"}
    answer, _ = call(
        step_tools, PROPOSE, section="experience", items=[item("employers", employer, TWO_DIGITS)]
    )
    assert answer["refused"][0]["rule"] == "two_digit_year"
    # Python was stored before its second proposal was refused: the person has it.
    assert held(factory, cv.step_id) == {
        "skills": {"setting": 1, "invalid_item": 1, "value_empty": 1},
        "experience": {"two_digit_year": 1},
    }
    assert counters(factory, cv) == (1, 3, 1)


def test_the_key_of_an_entry_that_could_not_be_built_is_the_one_its_value_gives() -> None:
    proposed = tools._proposed_key
    assert proposed("skills", {"key": "have", "value": {"name": " Python ", "x": 1}}) == (
        "have:python"
    )
    assert proposed("skills", {"key": "have:PYTHON"}) == "have:python"
    assert proposed("skills", {"key": "have:", "value": {"name": "Go"}}) == "have:"
    assert proposed("identity", {"key": "city", "value": 3}) == "city"
    employer = {"organisation": "Example Works", "start": "2021-03"}
    assert proposed("experience", {"key": "employers", "value": employer}) == (
        "employers:example-works-2021"
    )
    for value in (None, "Python", ["Python"], {"terms": ["Python"]}, {"name": 3}):
        assert proposed("skills", {"key": "have", "value": value}) is None, value
    assert proposed("experience", {"key": "employers", "value": {"organisation": "X"}}) is None
    late = {"organisation": "X", "start": "21"}
    assert proposed("experience", {"key": "employers", "value": late}) is None
    for entry in ({"key": "nowhere"}, {"key": 3}, "have", {"key": "k" * (items.KEY_MAX + 1)}):
        assert proposed("skills", entry) is None, entry


@pytest.mark.parametrize(
    ("held_now", "section", "change", "after"),
    [
        ({}, "skills", {"unknown_field": 2}, {"skills": {"unknown_field": 2}}),
        ({"skills": {"unknown_field": 2}}, "skills", {"unknown_field": -2}, {}),
        ({"skills": {"unknown_field": 1}}, "skills", {"unknown_field": -5}, {}),
        ({"skills": {"setting": 1}}, "skills", {"unknown_field": -1}, {"skills": {"setting": 1}}),
        ({"facts": {"x": 1}}, "facts", {"y": 0}, {"facts": {"x": 1}}),
        ({"facts": {"x": 1}}, "skills", {}, {"facts": {"x": 1}}),
        (
            {"facts": {"x": 1}, "skills": {"y": 2}},
            "skills",
            {"y": 1, "z": 1},
            {"facts": {"x": 1}, "skills": {"y": 3, "z": 1}},
        ),
        (None, "skills", {"y": 1}, {"skills": {"y": 1}}),
        ([], "skills", {"y": 1}, {"skills": {"y": 1}}),
        ({"skills": {"y": True}}, "skills", {}, {}),
        ({"skills": {"y": 1.5, "z": -1, "w": "2"}}, "skills", {}, {}),
        ({"skills": "y", 3: {"y": 1}}, "skills", {}, {}),
    ],
)
def test_moved_counts_never_go_under_zero_and_drop_what_is_not_a_count(
    held_now: object, section: str, change: dict[str, int], after: dict[str, Any]
) -> None:
    assert refusals.moved(held_now, section, change) == after


def test_every_rule_has_the_constant_sentence_the_agent_was_given() -> None:
    for rule in citations.Rule:
        assert refusals.sentence(rule.value) in {
            citations.REASONS[rule],
            items.REASONS.get(rule.value),
            refusals.REASONS.get(rule.value),
        }
    for rule, said in items.REASONS.items():
        assert refusals.sentence(rule) == refusals.REASONS.get(rule, said)
    assert refusals.sentence("unknown_field") == UNKNOWN_FIELD
    assert refusals.sentence("removal_of_a_list") == tools.REASONS[tools.REMOVAL_OF_A_LIST]
    for unknown in ("", "made_up", "Unknown_Field"):
        assert refusals.sentence(unknown) == refusals.REASONS[items.INVALID_VALUE]


def test_the_api_path_imports_without_the_sdk() -> None:
    code = (
        "import sys\n"
        "sys.modules['claude_agent_sdk'] = None  # as in the api image: the import fails\n"
        "from app.ingestion import refusals, review_router, runs_router\n"
        "assert refusals.of_steps and runs_router.run_out and review_router.list_proposals\n"
        "loaded = [name for name in ('app.ingestion.tools', 'claude_agent_sdk') "
        "if sys.modules.get(name) is not None]\n"
        "assert loaded == [], loaded\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert done.returncode == 0, done.stderr


# --- the run and its review answer them -----------------------------------------------------------


def _set(factory: sessionmaker[Session], step: str, counts: dict[str, Any]) -> None:
    with factory() as db, db.begin():
        db.execute(
            sa.update(IngestionRunStep)
            .where(IngestionRunStep.id == uuid.UUID(step))
            .values(refusals=counts)
        )


def test_the_run_and_its_review_say_what_was_not_kept_and_why(
    app: FastAPI, factory: sessionmaker[Session], api: Api, alice: str, ready: list[str]
) -> None:
    run = started(api, alice, ready)
    first, second = (step["id"] for step in run["steps"])
    assert run["refused"] == []
    _set(factory, first, {"skills": {"unknown_field": 3}, "identity": {"name_not_in_source": 1}})
    _set(factory, second, {"skills": {"unknown_field": 9}})
    end_step(app, factory, run)
    # The second step starts now: an attempt counts its refusals afresh.
    assert held(factory, uuid.UUID(second)) == {}
    _set(factory, second, {"skills": {"excerpt_not_found": 4, "unknown_field": 2}})
    end_step(app, factory, run)
    expected = [
        {
            "section": "identity",
            "rule": "name_not_in_source",
            "count": 1,
            "sentence": citations.REASONS[citations.Rule.NAME_NOT_IN_SOURCE],
        },
        {"section": "skills", "rule": "unknown_field", "count": 5, "sentence": UNKNOWN_FIELD},
        {
            "section": "skills",
            "rule": "excerpt_not_found",
            "count": 4,
            "sentence": citations.REASONS[citations.Rule.EXCERPT_NOT_FOUND],
        },
    ]
    done = api.get(f"{RUNS}/{run['id']}", token=alice).json()
    assert done["status"] == "done"
    assert done["refused"] == expected
    review = api.get(f"{RUNS}/{run['id']}/proposals", token=alice)
    assert review.status_code == 200, review.text
    assert review.json()["refused"] == expected
    # A run that reuses both steps shows the reasons of the steps it stands on.
    again = started(api, alice, ready)
    assert [step["status"] for step in again["steps"]] == ["reused", "reused"]
    assert again["refused"] == expected


# --- how the checker reads a two column CV --------------------------------------------------------

SKILL_TYPES = items.SLOTS["skills"]["have"].field_types


@pytest.mark.parametrize(
    ("excerpt", "rule"),
    [
        # As written: a list wrapped with its commas, a list one per line, a heading with its list.
        (PROGRAMMING, None),
        (DATA, None),
        (COMPETENCIES, None),
        ("TECHNICAL SKILLS Programming\nlanguages\nPython, Scala,", None),
        ("Data & Cloud\nSQL, Spark,\nKafka, Airflow,", None),
        # The same words on one line: spaces and line breaks are not compared.
        ("Python, Scala, Go, Rust", None),
        ("SQL, Spark, Kafka, Airflow, Kubernetes", None),
        ("Data modelling Stream processing Team leadership", None),
        ("Python,Scala,Go,Rust", None),
        # The ligature of the PDF typed as two letters, and a hyphen at a line end.
        ("Technical writing\nArtificial intelligence", None),
        ("Team leadership Technical writing Artificial intelligence", None),
        ("Stream pro-\ncessing\nTeam leadership", None),
        # Declared residue: what the document does not write is not found.
        ("Data modelling, Stream processing, Team leadership", "excerpt_not_found"),
        ("Data modelling; Stream processing; Team leadership", "excerpt_not_found"),
        ("\u2022 Data modelling \u2022 Stream processing", "excerpt_not_found"),
        ("Key competencies\nData modelling\nStream processing", "excerpt_not_found"),
        ("Python, Scala, Go, Rust, SQL, Spark", "excerpt_not_found"),
        ("Programming languages: Python, Scala, Go, Rust", "excerpt_not_found"),
        ("Python Scala Go Rust", "excerpt_not_found"),
        # Too short to be found once.
        ("Python, Scala,", "excerpt_too_short"),
        ("Kubernetes", "excerpt_too_short"),
        ("Go, Rust", "excerpt_too_short"),
    ],
)
def test_a_skill_list_of_a_two_column_cv(excerpt: str, rule: str | None) -> None:
    value = {"name": "Programming languages", "terms": ["Python", "Scala"]}
    checked = citations.check_item(SKILL_TYPES, value, CV, [excerpt])
    if rule is None:
        assert isinstance(checked, citations.Accepted), checked
    else:
        assert isinstance(checked, citations.Refusal)
        assert checked.rule.value == rule
