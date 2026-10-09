"""Unit tests of the scoring port (app/jobs/scoring.py): every rule of the engine, edge cases,
and the cost on a 1 MB text. The golden test against the engine itself is
test_scoring_golden.py."""

import time
from typing import Any

import pytest

from app.jobs import scoring
from app.jobs.scoring import ScoreResult, score
from app.profile.schemas import SkillsSection


def skills(**overrides: Any) -> SkillsSection:
    data: dict[str, Any] = {
        "years_experience": 3.5,
        "have": [
            {"name": "agents", "terms": ["agent", "agentic", "tool use"]},
            {"name": "python", "terms": ["python", "fastapi"]},
            {"name": "dotnet", "terms": ["c#", ".net"]},
        ],
        "adjacent": [{"name": "rag", "terms": ["rag", "retrieval"]}],
        "none": [{"name": "research", "terms": ["pytorch", "publication"]}],
        "gates": [
            {"name": "phd_required", "terms": [r"phd (is )?required"], "advanced_regex": True},
            {
                "name": "german_required",
                "terms": [r"(fluent|native) (in )?german"],
                "advanced_regex": True,
                "penalty": 10,
            },
        ],
    }
    data.update(overrides)
    return SkillsSection.model_validate(data)


SKILLS = skills()


def test_empty_text_scores_zero() -> None:
    assert score("", SKILLS) == ScoreResult(fit=0, competencies=[], gaps=[])
    assert score("   \n\t ", SKILLS) == ScoreResult(fit=0, competencies=[], gaps=[])


def test_no_skills_scores_zero() -> None:
    empty = SkillsSection()
    assert score("We build agents in Python.", empty) == ScoreResult(0, [], [])
    # No years_experience: the years step is skipped even for a senior title.
    assert score("10 years of experience", empty, title="Senior Engineer") == ScoreResult(0, [], [])


def test_categories_with_no_terms_match_nothing() -> None:
    section = skills(have=[{"name": "empty", "terms": []}], adjacent=[], none=[], gates=[])
    assert score("anything at all", section) == ScoreResult(0, [], [])


def test_have_only_is_100() -> None:
    assert score("Agents in Python.", SKILLS) == ScoreResult(100, ["agents", "python"], [])


def test_credit_per_level_and_gap_names() -> None:
    # agents 1 x 1, rag 1 x 0.5: 1.5 / 2 = 75. Adjacent and none are gaps, in model order.
    assert score("agent rag", SKILLS) == ScoreResult(75, ["agents", "rag"], ["rag"])
    # agents 1, research 1 x -0.5: 0.5 / 2 = 25.
    assert score("agent pytorch", SKILLS) == ScoreResult(25, ["agents", "research"], ["research"])
    # Only none: negative credit, clamped to 0.
    assert score("pytorch", SKILLS).fit == 0


def test_rounding_is_python_round() -> None:
    # Halves go to the even number, as Python's round does.
    text = "agent python rag pytorch"
    assert score(text, SKILLS).fit == round(100 * (1 + 1 + 0.5 - 0.5) / 4) == 50
    section = skills(
        have=[{"name": "a", "terms": ["alpha"]}],
        none=[],
        adjacent=[{"name": "b", "terms": ["beta"]}],
        gates=[],
    )
    # alpha x3 and beta x1: (3 + 0.5) / 4 = 87.5 -> 88 (even); alpha x1 beta x3: 2.5/4 = 62.5 -> 62
    assert score("alpha alpha alpha beta", section).fit == 88
    assert score("alpha beta beta beta", section).fit == 62


def test_occurrence_cap_is_three_per_part() -> None:
    section = skills(adjacent=[{"name": "rag", "terms": ["rag"]}], none=[], gates=[])
    # 10 agents and 1 rag: agents capped at 3, (3 + 0.5) / 4 = 87.5 -> 88
    assert score("agent " * 10 + "rag", section).fit == 88
    assert score("agent " * 3 + "rag", section).fit == 88
    assert score("agent " * 2 + "rag", section).fit == round(100 * 2.5 / 3)


def test_requirements_part_counts_double() -> None:
    section = skills(
        have=[{"name": "agents", "terms": ["agent"]}],
        none=[],
        gates=[],
        adjacent=[{"name": "rag", "terms": ["rag"]}],
    )
    # Without a marker: agents 1, rag 1 -> 1.5 / 2 = 75.
    assert score("agent. rag.", section).fit == 75
    # rag inside requirements: counted once in the whole text and twice in the part: 3.
    # agents 1, rag 3 -> (1 + 1.5) / 4 = 62.5 -> 62.
    assert score("agent. Requirements: rag.", section).fit == 62
    # The part ends at an end marker: rag after "Benefits" is outside it again.
    assert score("Requirements: agent. Benefits: rag.", section).fit == round(100 * (3 + 0.5) / 4)
    # The cap applies to each part: 5 rag in requirements count 3 + 2 x 3 = 9, and agent, in
    # the part too, 1 + 2 = 3: (3 + 9 x 0.5) / 12 = 62.5 -> 62.
    assert score("Requirements: rag rag rag rag rag. agent", section).fit == 62
    assert scoring.requirements_part("intro. about you: x. perks: y") == "about you: x. "
    assert scoring.requirements_part("no marker here") == ""


def test_years_phrases() -> None:
    section = skills(have=[{"name": "agents", "terms": ["agent"]}], adjacent=[], none=[], gates=[])
    base = "agent. "
    # 5 needed against 3.5: minus round(1.5 x 8) = 12.
    cases = {
        "5+ years of experience": (88, ["5+ years"]),
        "5 + years experience": (88, ["5+ years"]),
        "3-5 years of experience": (88, ["5+ years"]),  # the upper bound is read
        "10 yrs experience": (65, ["10+ years"]),  # round(6.5 x 8) = 52, capped at 35
        "3 years of experience": (100, []),  # under 3.5
        "4 years of experience": (96, ["4+ years"]),  # round(0.5 x 8) = 4
        "five years of experience": (100, []),  # words are not read
        "5 ans d'expérience": (100, []),  # French is not read
        "5 years of expérience": (100, []),  # accented: not "experience"
        "5 years": (100, []),  # no "experience" nearby
        "experience" + " " * 85 + "5 years": (100, []),  # outside the 80 character window
        "experience" + " " * 70 + "5 years": (88, ["5+ years"]),  # inside it
        "100 years of experience": (100, []),  # 1 or 2 digits: "00 years" -> 0
        "20 years of experience": (65, ["20+ years"]),  # penalty capped at 35
    }
    for phrase, expected in cases.items():
        result = score(base + phrase, section)
        assert (result.fit, result.gaps) == expected, phrase
    # The largest wins.
    assert score(base + "2 years of experience, ideally 7 years", section).gaps == ["7+ years"]


def test_senior_title_is_a_years_gate() -> None:
    section = skills(have=[{"name": "agents", "terms": ["agent"]}], adjacent=[], none=[], gates=[])
    for title in ("Senior AI Engineer", "Sr. Engineer", "sr engineer", "Tech Lead", "LEAD dev"):
        assert score("agent", section, title=title) == ScoreResult(88, ["agents"], ["5+ years"])
    for title in ("Seniority Manager", "Leader", "AI Engineer", ""):
        assert score("agent", section, title=title) == ScoreResult(100, ["agents"], [])
    # A stated need above 5 still wins over the title's.
    assert score("8 years experience agent", section, title="Senior").gaps == ["8+ years"]
    # The title is only read for seniority, never for categories.
    assert score("", section, title="Agent engineer").competencies == []


def test_years_at_or_under_the_profile_are_free() -> None:
    section = skills(years_experience=5.0)
    assert score("agent, 5 years experience", section, title="Senior").gaps == []
    assert score("agent, 6 years experience", section).fit == 92


def test_gates_take_their_penalty_and_are_named() -> None:
    assert score("agent. PhD is required.", SKILLS) == ScoreResult(75, ["agents"], ["phd_required"])
    assert score("agent. Fluent in German.", SKILLS) == ScoreResult(
        90, ["agents"], ["german_required"]
    )
    both = score("agent. phd required, native german", SKILLS)
    assert both == ScoreResult(65, ["agents"], ["phd_required", "german_required"])
    # Order of gaps: categories, then years, then gates.
    result = score("agent rag, phd required, 6 years of experience", SKILLS)
    assert result.gaps == ["rag", "6+ years", "phd_required"]


def test_only_gates_clamps_at_zero() -> None:
    section = skills(have=[], adjacent=[], none=[])
    assert score("phd required, native german", section) == ScoreResult(
        0, [], ["phd_required", "german_required"]
    )


def test_case_insensitive() -> None:
    assert score("AGENT and PYTHON", SKILLS) == score("agent and python", SKILLS)
    assert score("PHD IS REQUIRED agent", SKILLS).gaps == ["phd_required"]
    upper_terms = skills(have=[{"name": "agents", "terms": ["AGENT"]}], adjacent=[], none=[])
    assert score("an agent", upper_terms).competencies == ["agents"]


def test_whole_words_and_plural_from_matching() -> None:
    # matching.py: whole word, optional "s" or "es".
    assert score("agents", SKILLS).competencies == ["agents"]
    assert score("agentes", SKILLS).competencies == ["agents"]
    assert score("reagent", SKILLS).competencies == []
    assert score("agenty", SKILLS).competencies == []
    assert score("pythonic", SKILLS).competencies == []
    # Terms that start or end with punctuation still match as whole words.
    assert score("C# and .NET", SKILLS).competencies == ["dotnet"]
    assert score("abc#", SKILLS).competencies == []
    # Multi-word terms keep their space.
    assert score("tool use", SKILLS).competencies == ["agents"]
    assert score("tooluse", SKILLS).competencies == []


def test_unicode_and_accents() -> None:
    section = skills(
        have=[{"name": "ia", "terms": ["intelligence artificielle", "ia générative"]}],
        adjacent=[],
        none=[],
        gates=[],
    )
    assert score("L'IA GÉNÉRATIVE et l'Intelligence Artificielle", section).fit == 100
    assert score("ia generative", section).competencies == []  # accents matter
    # Accented letters are word characters: "généraliste" never matches "généra".
    word = skills(have=[{"name": "g", "terms": ["généra"]}], adjacent=[], none=[], gates=[])
    assert score("généraliste", word).competencies == []
    # Emoji, CJK, right-to-left text and lone combining marks do not break anything.
    assert score("🤖 agent 日本語 وكيل ́", SKILLS).competencies == ["agents"]
    # Characters whose lowercase is longer ("İ") do not shift the requirements part.
    assert score("İİİ requirements: agent", SKILLS).fit == 100


def test_advanced_categories_are_regexes() -> None:
    section = skills(
        have=[{"name": "llm", "terms": [r"\bllms?\b", r"\bia\b"], "advanced_regex": True}],
        adjacent=[],
        none=[],
        gates=[],
    )
    assert score("LLMs and IA", section).competencies == ["llm"]
    assert score("media", section).competencies == []


def test_result_lists_are_fresh() -> None:
    first = score("agent rag", SKILLS)
    first.gaps.append("x")
    assert score("agent rag", SKILLS).gaps == ["rag"]


def test_long_text_is_bounded_and_fast() -> None:
    text = ("We build agentic systems in Python with retrieval. " * 25_000)[:1_000_000]
    assert len(text) == 1_000_000
    # As large as Harald's model (35 categories, 6 gates) and then some.
    words = [f"term{n}" for n in range(480)]
    section = skills(
        have=[
            {"name": f"h{i}", "terms": ["agent", *words[i * 12 : i * 12 + 12]]} for i in range(20)
        ],
        adjacent=[
            {"name": f"a{i}", "terms": words[240 + i * 12 : 252 + i * 12]} for i in range(10)
        ],
        none=[{"name": f"n{i}", "terms": words[360 + i * 12 : 372 + i * 12]} for i in range(10)],
        gates=[
            {"name": f"g{i}", "terms": [rf"(fluent|native) (in )?lang{i}"], "advanced_regex": True}
            for i in range(8)
        ],
    )
    score(text, section)  # warm the regex cache
    # The best of 3: the bound is on the scorer, not on a neighbour (another pytest-xdist worker)
    # taking the CPU during a single measurement.
    timings = []
    for _ in range(3):
        started = time.perf_counter()
        result = score(text, section)
        timings.append(time.perf_counter() - started)
    elapsed = min(timings)
    assert elapsed < 0.2, f"{elapsed * 1000:.0f} ms (runs: {[round(t * 1000) for t in timings]})"
    assert result == score(text[: scoring.SCORED_CHARS_MAX], section)
    # The worst case for the patterns: no word boundary to skip ahead with.
    for worst in ("x" * 1_000_000, "é日" * 500_000, " " * 1_000_000):
        runs = []
        for _ in range(3):
            started = time.perf_counter()
            score(worst, section)
            runs.append(time.perf_counter() - started)
        assert min(runs) < 0.2, [round(t * 1000) for t in runs]


def test_text_past_the_cap_is_not_read() -> None:
    padding = "x " * (scoring.SCORED_CHARS_MAX // 2)
    assert score(padding + "agent", SKILLS).competencies == []
    assert score("agent " + padding, SKILLS).competencies == ["agents"]


@pytest.mark.parametrize("penalty", [0, 25, 100])
def test_gate_penalty_is_the_gates_own(penalty: int) -> None:
    section = skills(
        gates=[{"name": "g", "terms": ["visa"], "advanced_regex": False, "penalty": penalty}]
    )
    assert score("agent visa", section).fit == max(0, 100 - penalty)
