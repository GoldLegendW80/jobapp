"""The "About you" document, pure (app/profile/about_text.py): the parts, the writer that
converts a profile once (golden output for the owner's fixture shapes, invented data), the round
trip through a reading that states what the text says, and the checks that refuse a value the
text does not write."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.profile import about_text as t
from app.profile.schemas import validate_section

FIXTURES = Path(__file__).parent / "fixtures"


def owner() -> dict[str, Any]:
    raw = json.loads((FIXTURES / "owner_export.json").read_text(encoding="utf-8"))["sections"]
    return {name: validate_section(name, data) for name, data in raw.items()}


SMALL: dict[str, Any] = {
    "experience": {
        "employers": [
            {
                "key": "example-robotics",
                "organisation": "Example Robotics",
                "title": "Data engineer",
                "start": "2021-03",
                "end": None,
                "location": "Lyon",
                "contract": "CDI",
            },
            {
                "key": "exdata",
                "organisation": "Example Data",
                "title": "Analyst",
                "start": "2018",
                "end": "2021",
                "location": "Paris",
                "contract": None,
            },
        ],
        "education": [
            {
                "school": "Example School of Engineering",
                "degree": "Master of data science",
                "start": "2016",
                "end": "2018",
                "location": None,
                "details": "Thesis on streaming joins.",
            }
        ],
    },
    "skills": {
        "years_experience": 5,
        "have": [
            {
                "name": "data tools",
                "terms": ["Python", "Spark", "Airflow"],
                "advanced_regex": False,
            },
            {"name": "cloud", "terms": [r"\baws\b"], "advanced_regex": True},
            {"name": "sql", "terms": ["SQL", "dbt"], "advanced_regex": False},
        ],
        "adjacent": [],
        "none": [{"name": "java", "terms": ["Java"], "advanced_regex": False}],
        "gates": [{"name": "phd", "terms": ["PhD required"], "penalty": 30}],
    },
    "facts": {
        "blocks": [
            {"title": "Projects", "body": "- Built the streaming platform\n## Tools\nKafka, Flink"},
            {"title": "Skills", "body": "A block whose title reads as a kind."},
        ]
    },
    "answers": {
        "notice_period": "Three months.",
        "motivation": "I like small teams.\n\nAnd useful tools.",
        "work_authorization": [
            {"country": "France", "answer": "EU citizen.", "needs_sponsorship": False}
        ],
    },
}


def small() -> dict[str, Any]:
    return {name: validate_section(name, data) for name, data in SMALL.items()}


def _entries(body: str) -> list[str]:
    return [c if c.startswith("### ") else f"### {c}" for c in body.split("\n### ") if c.strip()]


def faithful(part: t.Part, sections: dict[str, Any], owned: list[str]) -> t.Interpretation:
    """What a model that reads the part well submits: the values the writer wrote, each with the
    lines that state it."""
    if part.kind == "experience":
        fields = ("organisation", "title", "start", "end", "location", "contract")
        return t.Interpretation(
            employers=[
                t.EmployerIn(**{f: e[f] for f in fields}, excerpts=[lines])
                for e, lines in zip(
                    sections["experience"]["employers"], _entries(part.body), strict=True
                )
            ],
            blocks=[],
        )
    if part.kind == "education":
        fields = ("school", "degree", "start", "end", "location", "details")
        return t.Interpretation(
            education=[
                t.SchoolIn(**{f: e[f] for f in fields}, excerpts=[lines.split("\n\n")[0]])
                for e, lines in zip(
                    sections["experience"]["education"], _entries(part.body), strict=True
                )
            ],
            blocks=[],
        )
    if part.kind == "skills":
        skills = sections["skills"]
        return t.Interpretation(
            years_experience=skills["years_experience"],
            categories=[
                t.CategoryIn(bucket=b, name=c["name"], terms=c["terms"])
                for b in ("have", "adjacent", "none")
                for c in skills[b]
                if not c["advanced_regex"]
            ],
            gates=[
                t.GateIn(name=g["name"], terms=g["terms"], penalty=g["penalty"])
                for g in skills["gates"]
                if not g["advanced_regex"]
            ],
        )
    if part.kind == "answers":
        answers = sections["answers"]
        return t.Interpretation(
            answers=t.AnswersIn(
                **{name: answers[name] for name in t.ANSWERS},
                work_authorization=[
                    t.AuthorizationIn(country=w["country"], answer=w["answer"])
                    for w in answers["work_authorization"]
                ],
            )
        )
    return t.Interpretation(blocks=[t.BlockIn(title=title) for title in owned])


def round_trip(sections: dict[str, Any]) -> dict[str, Any]:
    return round_trip_with(sections, {})


def round_trip_with(sections: dict[str, Any], allow: dict[str, list[str]]) -> dict[str, Any]:
    converted = t.convert(sections)
    current = copy.deepcopy(sections)
    for part in t.split(converted.text)[1]:
        owned = converted.parts[part.key]["blocks"]
        outcome = t.interpret(part, current, owned, faithful(part, sections, owned))
        assert outcome is not None
        assert outcome.refused == [], (part.heading, outcome.refused)
        assert outcome.lines == allow.get(part.heading, []), (part.heading, outcome.lines)
        current.update(outcome.changes)
    return current


def test_the_owner_s_profile_converts_to_the_golden_document() -> None:
    converted = t.convert(owner())
    assert converted.text == (FIXTURES / "owner_about.md").read_text(encoding="utf-8")
    preamble, parts = t.split(converted.text)
    assert preamble == ""
    assert [p.kind for p in parts[:3]] == ["experience", "education", "skills"]
    assert parts[-1].kind == "answers"
    assert {p.kind for p in parts[3:-1]} == {"facts"}
    # Every part is in step with the profile it was written from.
    for part in parts:
        state = converted.parts[part.key]
        assert state["applied"] == part.digest
        assert state["basis"] == t.basis(part.kind, owner(), state["blocks"])
    # The regular expression categories and gates are not written: the code keeps them.
    assert t.advanced_count(owner()) == 9
    assert "\\b" not in converted.text


def test_converting_reading_and_applying_gives_the_profile_back() -> None:
    assert round_trip(owner()) == owner()
    # One thing changes: a level two heading inside a block is level three afterwards, as the
    # document writes it (a level two heading starts a part).
    expected = small()
    expected["facts"]["blocks"][0]["body"] = (
        "- Built the streaming platform\n### Tools\nKafka, Flink"
    )
    sections = small()
    current = round_trip_with(sections, allow={"Projects": ["1 fact block updated: Projects"]})
    assert current == expected


def test_the_writer_keeps_a_part_per_heading_and_dates_in_words() -> None:
    text = t.convert(small()).text
    assert "### Data engineer, Example Robotics\nMarch 2021 to now · Lyon · CDI" in text
    assert "### Analyst, Example Data\n2018 to 2021 · Paris\n" in text
    assert "- data tools: Python, Spark, Airflow\n- sql: SQL, dbt" in text
    assert "- phd, lowers the fit by 30: PhD required" in text
    # A level two heading inside a block would start a part: it is written one level down.
    assert "## Projects\n\n- Built the streaming platform\n### Tools\nKafka, Flink" in text
    # A block whose title reads as another kind keeps its own part.
    headings = [p.heading for p in t.split(text)[1]]
    assert headings == ["Experience", "Education", "Skills", "Projects", "Notes: Skills", "Answers"]


def test_a_heading_inside_a_code_fence_starts_no_part() -> None:
    preamble, parts = t.split("Intro\n## Skills\n```\n## not a part\n```\n## Travel notes\nOften")
    assert preamble == "Intro"
    assert [(p.heading, p.kind) for p in parts] == [("Skills", "skills"), ("Travel notes", "facts")]
    assert parts[0].body == "```\n## not a part\n```"
    assert t.kind_of("  COMPÉTENCES ") == "skills"


def _part(sections: dict[str, Any], kind: str) -> t.Part:
    return next(p for p in t.split(t.convert(sections).text)[1] if p.kind == kind)


def test_a_job_the_text_does_not_write_is_not_taken_and_the_stored_one_stays() -> None:
    sections = small()
    part = _part(sections, "experience")
    given = faithful(part, sections, [])
    assert given.employers is not None
    invented = given.employers[1].model_copy(update={"start": "2017"})  # the text says 2018
    elsewhere = given.employers[0].model_copy(
        update={"organisation": "Example Space", "excerpts": ["### Founder, Example Space 2016"]}
    )
    given = given.model_copy(update={"employers": [given.employers[0], invented, elsewhere]})
    outcome = t.interpret(part, sections, [], given)
    assert outcome is not None
    assert outcome.lines == []
    assert outcome.refused == [
        "A job at Example Data: its dates, title or place are not written that way in this part.",
        "A job at Example Space: its dates, title or place are not written that way in this part.",
    ]
    assert outcome.changes["experience"] == sections["experience"]


def test_a_title_or_place_must_be_written_in_the_lines_the_job_cites() -> None:
    sections = small()
    part = _part(sections, "experience")
    given = faithful(part, sections, [])
    assert given.employers is not None
    for change in ({"title": "Lead data engineer"}, {"location": "Marseille"}, {"contract": "CDD"}):
        one = given.employers[0].model_copy(update=change)
        outcome = t.interpret(part, sections, [], given.model_copy(update={"employers": [one]}))
        assert outcome is not None
        # Refused, so the stored job stays; the other job is no longer in the reading.
        assert [e["key"] for e in outcome.changes["experience"]["employers"]] == [
            "example-robotics"
        ], change
        assert len(outcome.refused) == 1


def test_an_employer_keeps_its_key_while_its_organisation_is_unchanged() -> None:
    sections = small()
    body = (
        "### Senior data engineer, Example Robotics\nMarch 2021 to now · Lyon · permanent\n\n"
        "### Analyst, Example Data\n2018 to 2021 · Paris\n\n"
        "### Intern, Example Data\n2017 · Paris"
    )
    part = t.Part("Experience", body)
    entries = _entries(body)
    given = t.Interpretation(
        employers=[
            t.EmployerIn(
                organisation="Example Robotics",
                title="Senior data engineer",
                start="2021-03",
                end="now",
                location="Lyon",
                contract="permanent",
                excerpts=[entries[0]],
            ),
            t.EmployerIn(
                organisation="Example Data",
                title="Analyst",
                start="2018",
                end="2021",
                location="Paris",
                excerpts=[entries[1]],
            ),
            t.EmployerIn(
                organisation="Example Data",
                title="Intern",
                start="2017",
                end="2017",
                location="Paris",
                excerpts=[entries[2]],
            ),
        ]
    )
    outcome = t.interpret(part, sections, [], given)
    assert outcome is not None
    assert outcome.refused == []
    employers = outcome.changes["experience"]["employers"]
    # The stored keys stay (exdata is not the slug of its organisation); a second position at
    # the same organisation takes a key of its own.
    assert [e["key"] for e in employers] == ["example-robotics", "exdata", "example-data"]
    assert employers[0]["contract"] == "CDI"
    assert outcome.lines == [
        "1 job added: Intern at Example Data, 2017 to 2017",
        "1 job updated: Senior data engineer at Example Robotics, March 2021 to now",
    ]


def test_a_skill_the_text_does_not_write_is_not_taken() -> None:
    sections = small()
    part = _part(sections, "skills")
    given = faithful(part, sections, [])
    assert given.categories is not None
    data = given.categories[0].model_copy(update={"terms": ["Python", "Spark", "Airflow", "Rust"]})
    lacking = t.CategoryIn(bucket="none", name="kotlin", terms=["Kotlin"])
    given = given.model_copy(
        update={"categories": [data, *given.categories[1:], lacking], "years_experience": 7}
    )
    outcome = t.interpret(part, sections, [], given)
    assert outcome is not None
    assert outcome.refused == [
        "Years of experience: the number is not written in this part.",
        "1 term of data tools: not written in this part.",
        "1 term of kotlin: not written in this part.",
    ]
    assert outcome.lines == []
    # The regular expression category stays where it was.
    assert outcome.changes["skills"] == sections["skills"]


def test_skills_changes_are_summed_up_and_a_name_the_text_lacks_takes_its_first_term() -> None:
    sections = small()
    part = t.Part(
        "Skills",
        "Years of experience: 6\n\n### What I have\n- I use dbt, Airflow and Python\n\n"
        "### What I do not have\n- Java",
    )
    given = t.Interpretation(
        years_experience=6,
        categories=[
            t.CategoryIn(bucket="have", name="data tools", terms=["dbt", "Airflow", "Python"]),
            t.CategoryIn(bucket="none", name="java", terms=["Java"]),
        ],
        gates=[],
    )
    outcome = t.interpret(part, sections, [], given)
    assert outcome is not None
    assert outcome.refused == []
    have = outcome.changes["skills"]["have"]
    assert [c["name"] for c in have] == ["dbt", "cloud"]  # "data tools" is not written here
    assert outcome.lines == [
        "Years of experience: 6",
        "2 skills removed from what I have: Spark, SQL",
        "1 skill removed from requirements that lower my fit: PhD required",
    ]


def test_an_answer_or_a_block_the_text_does_not_write_is_not_taken() -> None:
    sections = small()
    part = _part(sections, "answers")
    given = faithful(part, sections, [])
    assert given.answers is not None
    answers = given.answers.model_copy(
        update={
            "notice_period": "One month.",
            "travel": None,
            "work_authorization": [t.AuthorizationIn(country="Canada", answer="EU citizen.")],
        }
    )
    outcome = t.interpret(part, sections, [], given.model_copy(update={"answers": answers}))
    assert outcome is not None
    assert outcome.refused == [
        "Notice period: not written that way in this part.",
        "Work authorisation for Canada: not written there.",
    ]
    assert outcome.changes["answers"]["notice_period"] == "Three months."
    assert outcome.lines == ["1 answer updated: Work authorisation"]
    block = t.Part("Projects", "- Built the streaming platform")
    given = t.Interpretation(blocks=[t.BlockIn(title="Projects", body="Built a search engine")])
    outcome = t.interpret(block, sections, ["Projects"], given)
    assert outcome is not None
    assert outcome.refused == ["A fact block, Projects: not written that way in this part."]
    assert outcome.changes["facts"]["blocks"] == [sections["facts"]["blocks"][1]]
    assert outcome.lines == ["1 fact block removed: Projects"]


def test_a_reading_without_its_kind_is_no_answer() -> None:
    sections = small()
    assert t.interpret(_part(sections, "experience"), sections, [], t.Interpretation()) is None
    assert t.interpret(_part(sections, "skills"), sections, [], t.Interpretation(gates=[])) is None


@pytest.mark.parametrize(
    ("raw", "ok"),
    [("Built the streaming platform", True), ("the streaming", True), ("Built platform", False)],
)
def test_written_reads_words_in_order(raw: str, ok: bool) -> None:
    assert t.Written("- **Built** the streaming-platform.")(raw) is ok
