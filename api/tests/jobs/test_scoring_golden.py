"""The scoring port against the engine itself, on Harald's real data (read only).

For every row of ../data/jobs.csv that has data/jobs/<id>/posting.txt, the engine's own
`score_row` (engine/jobs.py, on profile/skills.json) and the port (app/jobs/scoring.py, on the
skills section the profile importer converts from the same skills.json) must give the same fit,
the same competencies and the same gaps, in the same order. The engine scores the whole
posting.txt (header included) with the row's title, as `jobs.py score` does.

The engine is never imported: jobs.py parses arguments and touches files at module level. Its
functions are extracted from the source with `ast` (only the definitions listed in ENGINE_NAMES)
and executed in a namespace where SKILLS points at the real skills.json. The file is only read.

Skipped, with the reason, where those files do not exist (CI, containers). Set JOBAPP_SOURCE_DIR
to the job folder to point at them explicitly.
"""

import ast
import csv
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from app.jobs import scoring, slug
from app.profile.importer import SourcePaths, convert
from app.profile.schemas import SkillsSection

ENGINE_NAMES = {
    "FIELDS",
    "TYPES",
    "STATUSES",
    "REQ_START",
    "REQ_END",
    "_rx",
    "score",
    "score_row",
    "norm",
    "slug",
    "match",
}
REQUIRED = ("engine/jobs.py", "profile/skills.json", "data/jobs.csv")


def _find_job_root() -> Path | None:
    explicit = os.environ.get("JOBAPP_SOURCE_DIR")
    candidates = [Path(explicit)] if explicit else list(Path(__file__).resolve().parents)
    for parent in candidates:
        if all((parent / name).is_file() for name in REQUIRED):
            return parent
    return None


ROOT = _find_job_root()
pytestmark = pytest.mark.skipif(
    ROOT is None,
    reason="Harald's engine and data (engine/jobs.py, profile/skills.json, data/jobs.csv) are not "
    "on this machine; set JOBAPP_SOURCE_DIR to the job folder to run the golden test",
)


@pytest.fixture(scope="module")
def root() -> Path:
    assert ROOT is not None
    return ROOT


def load_engine(root: Path) -> dict[str, Any]:
    """The listed definitions of engine/jobs.py, executed on their own."""
    path = root / "engine" / "jobs.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def wanted(node: ast.stmt) -> bool:
        if isinstance(node, ast.FunctionDef):
            return node.name in ENGINE_NAMES
        if isinstance(node, ast.Assign):
            return any(isinstance(t, ast.Name) and t.id in ENGINE_NAMES for t in node.targets)
        return False

    module = ast.Module(body=[node for node in tree.body if wanted(node)], type_ignores=[])
    namespace: dict[str, Any] = {"re": re, "json": json, "SKILLS": root / "profile" / "skills.json"}
    exec(compile(module, str(path), "exec"), namespace)  # noqa: S102 - the engine's own code
    missing = ENGINE_NAMES - set(namespace)
    assert not missing, f"engine/jobs.py no longer defines {sorted(missing)}"
    return namespace


@pytest.fixture(scope="module")
def engine(root: Path) -> dict[str, Any]:
    return load_engine(root)


@pytest.fixture(scope="module")
def skills(root: Path) -> SkillsSection:
    return convert(SourcePaths.under(root)).sections.skills


@pytest.fixture(scope="module")
def rows(root: Path) -> list[dict[str, str]]:
    with (root / "data" / "jobs.csv").open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_the_engine_is_left_untouched(root: Path) -> None:
    before = (root / "engine" / "jobs.py").stat().st_mtime_ns
    load_engine(root)
    assert (root / "engine" / "jobs.py").stat().st_mtime_ns == before


def test_port_scores_like_the_engine_on_every_posting(
    root: Path,
    engine: dict[str, Any],
    skills: SkillsSection,
    rows: list[dict[str, str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    scored, without_posting, mismatches = 0, [], []
    for row in rows:
        posting = root / "data" / "jobs" / row["id"] / "posting.txt"
        if not posting.is_file():
            without_posting.append(row["id"])
            continue
        text = posting.read_text(encoding="utf-8")
        expected = dict(row)
        engine["score_row"](expected, text)
        port = scoring.score(text, skills, title=row["title"])
        got = (str(port.fit), ", ".join(port.competencies), ", ".join(port.gaps))
        want = (expected["fit"], expected["competencies"], expected["gaps"])
        if got != want:
            mismatches.append((row["id"], want, got))
        scored += 1
        assert len(text) <= scoring.SCORED_CHARS_MAX  # the cap never applies to real postings
    assert not mismatches, mismatches[:5]
    assert scored >= 300, f"only {scored} postings found"
    with capsys.disabled():
        print(
            f"\n  golden scoring: {scored} postings scored identically, "
            f"{len(without_posting)} of {len(rows)} rows have no posting.txt"
        )


def test_every_category_gate_and_years_rule_is_exercised(
    root: Path, skills: SkillsSection, rows: list[dict[str, str]]
) -> None:
    """The golden rows are not a lucky subset: most categories, gates, years gaps and
    requirements sections all occur in them, and the fits spread widely."""
    named: set[str] = set()
    gaps: set[str] = set()
    fits: set[int] = set()
    with_requirements = 0
    for row in rows:
        posting = root / "data" / "jobs" / row["id"] / "posting.txt"
        if posting.is_file():
            text = posting.read_text(encoding="utf-8")
            result = scoring.score(text, skills, title=row["title"])
            named.update(result.competencies)
            gaps.update(result.gaps)
            fits.add(result.fit)
            with_requirements += bool(scoring.requirements_part(text.lower()))
    categories = {c.name for level in (skills.have, skills.adjacent, skills.none) for c in level}
    assert len(named) >= 0.8 * len(categories)
    assert any(g.endswith("+ years") for g in gaps)
    assert {g.name for g in skills.gates} & gaps
    assert len(fits) > 30
    assert with_requirements > 100


def test_slug_is_the_engines_on_ascii_text(
    engine: dict[str, Any], rows: list[dict[str, str]]
) -> None:
    compared = 0
    for row in rows:
        text = f"{row['company']} {row['title']}"
        if text.isascii():
            assert slug.slugify(row["company"], row["title"]) == engine["slug"](text), text
            assert slug.text_key(text) == engine["norm"](text)
            compared += 1
        else:
            # Accents are folded where the engine made a hyphen of them.
            assert slug.slugify(row["company"], row["title"]).isascii()
    for long_text in ("a" * 120, "word " * 40, "x-" * 60):
        assert slug.slug(long_text) == engine["slug"](long_text)
    assert compared > 300


def test_csv_rows_are_all_distinct_postings(
    engine: dict[str, Any], rows: list[dict[str, str]]
) -> None:
    """The engine never stored the same posting twice; the port's rule agrees on every row,
    while comparing company and title alone would have merged distinct postings."""
    keys = [slug.PostingKey.of(r["company"], r["title"], r["location"], r["url"]) for r in rows]
    for index, key in enumerate(keys):
        others = keys[:index] + keys[index + 1 :]
        assert slug.find_duplicate(key, others, lambda k: k) is None, rows[index]["id"]
        # And the engine's match finds no other row either (by url, or company+title+location).
        probe = {k: v for k, v in rows[index].items() if k != "id"}
        assert engine["match"]([r for r in rows if r is not rows[index]], probe) is None
    by_company_title = {(k.identity[0], k.identity[1]) for k in keys}
    assert len(by_company_title) < len(rows)
