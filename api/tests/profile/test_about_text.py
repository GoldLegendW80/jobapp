"""The "About you" document, pure (app/profile/about_text.py): the parts (skills, answers, fact
blocks), the writer that converts a profile once (golden output for the owner's fixture shapes,
invented data), the round trip through a reading that states what the text says, the checks that
refuse a value the text does not write, and the facts read by code."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.profile import about_text as t
from app.profile.schemas import validate_section

FIXTURES = Path(__file__).parent / "fixtures"
SECTIONS = ("identity", "experience", "search", "preferences")


def owner() -> dict[str, Any]:
    raw = json.loads((FIXTURES / "owner_export.json").read_text(encoding="utf-8"))["sections"]
    return {name: validate_section(name, data) for name, data in raw.items()}


SMALL: dict[str, Any] = {
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
            {"country": "France", "answer": "EU citizen.", "needs_sponsorship": False},
            {"country": "Canada", "answer": "Work permit needed.", "needs_sponsorship": None},
        ],
        "preferred_name": "Sam",
        "highest_degree": "Master of data science",
        "highest_degree_year": 2018,
        "willing_onsite": True,
        "willing_hybrid": False,
        "background_check_consent": True,
        "reference_check_consent": False,
        "salary_stance": "range",
        "salary_ranges": [
            {"currency": "EUR", "minimum": 50000, "maximum": 60000, "period": "year"},
            {"currency": "CHF", "minimum": 700, "period": "day"},
        ],
        "eeo_policy": "stored",
        "eeo_answers": [{"topic": "gender", "answer": "Woman"}],
        "leave_blank": ["Date of birth"],
    },
}


def small() -> dict[str, Any]:
    return {name: validate_section(name, data) for name, data in SMALL.items()}


def said(value: bool, quote: str) -> t.SaidIn:
    return t.SaidIn(value=value, quote=quote)


def faithful(part: t.Part, sections: dict[str, Any]) -> t.Interpretation:
    """What a model that reads the part well submits: the values the writer wrote, each switch
    with the line that states it."""
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
    answers = sections["answers"]
    switches = {
        name: said(answers[name], t.SWITCHES[name][2 if answers[name] else 3])
        for name in t.SWITCHES
        if answers[name] is not None
    }
    sponsor = lambda need: (  # noqa: E731
        None if need is None else said(need, f"Sponsorship needed: {'yes' if need else 'no'}.")
    )
    stance = answers["salary_stance"]
    return t.Interpretation(
        answers=t.AnswersIn(
            **{name: answers[name] for name in t.PROSE},
            highest_degree_year=answers["highest_degree_year"],
            work_authorization=[
                t.AuthorizationIn(
                    country=w["country"],
                    answer=w["answer"],
                    needs_sponsorship=sponsor(w["needs_sponsorship"]),
                )
                for w in answers["work_authorization"]
            ],
            salary_stance=None
            if stance is None
            else t.ChoiceIn(value=stance, quote=t.STANCES[stance][1]),
            salary_ranges=[
                t.RangeIn(
                    **{k: r[k] for k in ("currency", "minimum", "maximum", "period")},
                    quote=t._range_words(r),
                )
                for r in answers["salary_ranges"]
            ],
            eeo_policy=t.ChoiceIn(
                value=answers["eeo_policy"], quote=t.POLICIES[answers["eeo_policy"]][1]
            ),
            eeo_answers=[t.EeoIn(**one) for one in answers["eeo_answers"]],
            leave_blank=answers["leave_blank"],
            **switches,
        )
    )


def round_trip(
    sections: dict[str, Any], allow: dict[str, list[str]] | None = None
) -> dict[str, Any]:
    converted = t.convert(sections)
    current = copy.deepcopy(sections)
    for part in t.split(converted.text)[1]:
        owned = converted.parts[part.key]["blocks"]
        if part.kind == "facts":
            outcome = t.read_facts(part, current, owned)
        else:
            outcome = t.interpret(part, current, faithful(part, sections))
        assert outcome is not None
        assert outcome.refused == [], (part.heading, outcome.refused)
        assert outcome.lines == (allow or {}).get(part.heading, []), (part.heading, outcome.lines)
        current.update(outcome.changes)
    return current


def test_the_owner_s_profile_converts_to_the_golden_document() -> None:
    converted = t.convert(owner())
    assert converted.text == (FIXTURES / "owner_about.md").read_text(encoding="utf-8")
    preamble, parts = t.split(converted.text)
    assert preamble == ""
    assert parts[0].kind == "skills"
    assert parts[-1].kind == "answers"
    assert {p.kind for p in parts[1:-1]} == {"facts"}
    for part in parts:
        state = converted.parts[part.key]
        assert state["applied"] == part.digest
        assert state["basis"] == t.basis(part.kind, owner(), state["blocks"])
    # Only skills, facts and answers are written: the jobs, schools, identity and search stay
    # forms (owner, 2026-11-05), and regular expressions stay as stored.
    assert "### GenAI Engineer, Lumetra Group" not in converted.text
    assert "e2e-lucien.quillard@example.org" not in converted.text.split("## IDENTITY")[0]
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
    assert round_trip(small(), {"Projects": ["1 fact block updated: Projects"]}) == expected


def test_the_answers_are_short_sentences_under_their_headings() -> None:
    text = t.convert(small()).text
    answers = text.split("## Answers\n\n")[1]
    for line in (
        "### Work authorisation\n- France: EU citizen.\n  Sponsorship needed: no.\n"
        "- Canada: Work permit needed.\n",
        "### Highest degree\nMaster of data science\n\nYear: 2018",
        "### Salary on forms\nOn forms, I give the salary range below.",
        "### Salary ranges\n- EUR 50000 to 60000 a year\n- CHF 700 or more a day",
        "### Working on site\nYes, I can work on site.",
        "### Hybrid work\nNo, I cannot work hybrid.",
        "### Background checks\nYes, I consent to background checks.",
        "### Reference checks\nNo, I do not consent to reference checks.",
        "### Equal opportunity questions\nI answer the equal opportunity questions with the "
        "answers below.\n\n- Gender: Woman",
        "### Questions I leave blank\n- Date of birth",
    ):
        assert line in answers, line
    # The other sections are not in the document.
    assert t.kind_of("Experience") == "facts"
    headings = [p.heading for p in t.split(text)[1]]
    assert headings == ["Skills", "Projects", "Notes: Skills", "Answers"]


def test_a_heading_inside_a_code_fence_starts_no_part() -> None:
    preamble, parts = t.split("Intro\n## Skills\n```\n## not a part\n```\n## Travel notes\nOften")
    assert preamble == "Intro"
    assert [(p.heading, p.kind) for p in parts] == [("Skills", "skills"), ("Travel notes", "facts")]
    assert parts[0].body == "```\n## not a part\n```"
    assert t.kind_of("  COMPÉTENCES ") == "skills"
    assert t.kind_of("Réponses") == "answers"


def _part(sections: dict[str, Any], kind: str) -> t.Part:
    return next(p for p in t.split(t.convert(sections).text)[1] if p.kind == kind)


def test_a_skill_or_a_number_the_text_does_not_write_is_not_taken() -> None:
    sections = small()
    part = _part(sections, "skills")
    given = faithful(part, sections)
    assert given.categories is not None
    data = given.categories[0].model_copy(update={"terms": ["Python", "Spark", "Airflow", "Rust"]})
    lacking = t.CategoryIn(bucket="none", name="kotlin", terms=["Kotlin"])
    gate = t.GateIn(name="phd", terms=["PhD required"], penalty=40)
    given = given.model_copy(
        update={
            "categories": [data, *given.categories[1:], lacking],
            "gates": [gate],
            "years_experience": 7,
        }
    )
    outcome = t.interpret(part, sections, given)
    assert outcome is not None
    assert outcome.refused == [
        "Years of experience: the number is not written in this part.",
        "1 term of data tools: not written in this part.",
        "1 term of kotlin: not written in this part.",
        "phd: its penalty is not written in this part.",
    ]
    assert outcome.lines == ["1 skill removed from requirements that lower my fit: PhD required"]
    skills = outcome.changes["skills"]
    assert skills["years_experience"] == 5  # the stored years stay
    # The regular expression category stays where it was.
    assert skills["have"] == sections["skills"]["have"]


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
    outcome = t.interpret(part, sections, given)
    assert outcome is not None
    assert outcome.refused == []
    assert [c["name"] for c in outcome.changes["skills"]["have"]] == ["dbt", "cloud"]
    assert outcome.lines == [
        "Years of experience: 6",
        "2 skills removed from what I have: Spark, SQL",
        "1 skill removed from requirements that lower my fit: PhD required",
    ]


def test_a_yes_or_no_the_text_does_not_state_clearly_is_left_as_it_was_and_said() -> None:
    sections = small()
    part = t.Part(
        "Answers",
        "### Background checks\nI do not mind them.\n\n"
        "### Reference checks\nYes, but no calls to my employer, reference checks only.\n\n"
        "### Working on site\nNo, I cannot work on site.\n\n"
        "### Work authorisation\n- France: EU citizen.\n  Sponsorship needed: yes.\n\n"
        "### Motivation\nI like small teams.",
    )
    given = t.Interpretation(
        answers=t.AnswersIn(
            motivation="I like small teams.",
            background_check_consent=said(True, "I do not mind them."),
            reference_check_consent=said(
                True, "Yes, but no calls to my employer, reference checks only."
            ),
            willing_onsite=said(False, "No, I cannot work on site."),
            # The model says yes, but quotes a line of another topic.
            willing_hybrid=said(True, "Yes, I can work on site."),
            work_authorization=[
                t.AuthorizationIn(
                    country="France",
                    answer="EU citizen.",
                    needs_sponsorship=said(True, "Sponsorship needed: yes."),
                )
            ],
        )
    )
    outcome = t.interpret(part, sections, given)
    assert outcome is not None
    answers = outcome.changes["answers"]
    assert answers["background_check_consent"] is True  # unchanged
    assert answers["reference_check_consent"] is False  # unchanged
    assert answers["willing_hybrid"] is False  # unchanged
    assert answers["willing_onsite"] is False
    assert outcome.refused == [
        "Not changed: the text does not say clearly whether you can work hybrid. Start the line "
        "with Yes or No.",
        "Not changed: the text does not say clearly whether you consent to background checks. "
        "Start the line with Yes or No.",
        "Not changed: the text does not say clearly whether you consent to reference checks. "
        "Start the line with Yes or No.",
        "Not changed: the text does not say how you answer the salary on forms.",
        "Not changed: the text does not say how you answer the equal opportunity questions.",
    ]
    assert answers["salary_stance"] == "range"
    assert answers["eeo_policy"] == "stored"
    # Each change to a switch is a line of its own, old value and new.
    assert "Working on site: was yes, now no" in outcome.lines
    assert "Sponsorship for France: was no, now yes" in outcome.lines
    assert f"{t.AUTHORIZATION} for Canada: removed" in outcome.lines
    assert any(
        line.startswith("Salary range: was EUR 50000 to 60000 a year, now none")
        for line in outcome.lines
    )


def test_a_salary_or_an_equal_opportunity_answer_not_written_is_not_taken() -> None:
    sections = small()
    part = _part(sections, "answers")
    given = faithful(part, sections)
    assert given.answers is not None
    ranges = [
        given.answers.salary_ranges[0].model_copy(update={"maximum": 65000}),
        given.answers.salary_ranges[1],
    ]
    answers = given.answers.model_copy(
        update={
            "salary_ranges": ranges,
            "eeo_answers": [t.EeoIn(topic="age", answer="Forty")],
            "eeo_policy": t.ChoiceIn(value="leave", quote=t.POLICIES["stored"][1]),
            "notice_period": "One month.",
        }
    )
    outcome = t.interpret(part, sections, t.Interpretation(answers=answers))
    assert outcome is not None
    assert outcome.refused == [
        "Notice period: not written that way in this part.",
        "Not changed: the text does not say clearly how you answer the equal opportunity "
        "questions.",
        "Not changed: a salary range in EUR is not written clearly.",
        "Not changed: your age answer is not written there.",
    ]
    stored = sections["answers"]
    # The range not written as read stays as stored, in its place.
    assert outcome.changes["answers"]["salary_ranges"] == stored["salary_ranges"]
    assert outcome.changes["answers"]["eeo_policy"] == "stored"
    assert outcome.changes["answers"]["notice_period"] == "Three months."
    # The gender answer is no longer in the reading: its removal is its own line.
    assert "Gender answer: was Woman, now none" in outcome.lines


def test_a_fact_part_is_its_block_read_by_code() -> None:
    sections = small()
    part = t.Part("Projects", "- Built a search engine")
    outcome = t.read_facts(part, sections, ["Projects"])
    assert outcome.refused == []
    assert outcome.changes["facts"]["blocks"][0] == {
        "title": "Projects",
        "body": "- Built a search engine",
    }
    assert outcome.lines == ["1 fact block updated: Projects"]
    emptied = t.read_facts(t.Part("Projects", ""), sections, ["Projects"])
    assert emptied.changes["facts"]["blocks"] == [sections["facts"]["blocks"][1]]
    assert emptied.lines == ["1 fact block removed: Projects"]
    clash = t.read_facts(t.Part("Notes: Skills", "Another."), sections, [])
    assert clash.refused == ["Skills: another part already has a block of this title."]
    assert clash.changes == {}
    renamed = t.read_facts(t.Part("Notes: Skills", "Kept."), sections, ["Skills"])
    assert renamed.changes["facts"]["blocks"][1] == {"title": "Skills", "body": "Kept."}


def test_a_reading_without_its_kind_is_no_answer() -> None:
    sections = small()
    assert t.interpret(_part(sections, "skills"), sections, t.Interpretation(gates=[])) is None
    assert (
        t.interpret(_part(sections, "answers"), sections, t.Interpretation(categories=[])) is None
    )


@pytest.mark.parametrize(
    ("raw", "ok"),
    [("Built the streaming platform", True), ("the streaming", True), ("Built platform", False)],
)
def test_written_reads_words_in_order(raw: str, ok: bool) -> None:
    assert t.Written("- **Built** the streaming-platform.")(raw) is ok
