"""What a CI run has to run, decided from the paths a pull request changes (issue #91).

    python3 .github/scripts/ci_plan.py                 in a job of ci.yml: writes the step outputs
    python3 .github/scripts/ci_plan.py --paths a b c   by hand: the plan for these paths
    python3 .github/scripts/ci_plan.py verify          last step of a job: what was planned ran

The units of .github/workflows/ci.yml:

    api     ruff, pytest with the 95 percent coverage floor, the docker marked tests
    web     eslint, tsc, build, vitest
    shell   shellcheck and the test scripts of tests/
    e2e     the three Playwright groups on their scratch stacks

pre-commit is no unit: it runs on every run, whatever changed.

Only a pull request is ever narrowed. A push to main, a manual run and anything this script
cannot read for certain run every unit: a green run on main is what deploys. A pull request is
judged on the files of its merge commit against the base branch, each file by the first rule of
RULES that matches it. A path no rule names runs every unit, so the rules only ever list what is
known to be safe to skip, and a new folder is tested in full until someone decides otherwise.

Why each rule may skip what it skips is written next to it, and ci_plan_test.py holds the checks
that keep those reasons true (no api file reads a file of web/, for instance).

Every job of ci.yml computes this plan for itself in its first step (no job waits for another),
gives it to its steps as outputs, and ends with `verify`, which fails the job when a step that
the plan needed did not run. The gate job then fails unless every job succeeded and all of them
computed the same plan.
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
from pathlib import Path

UNITS = ("api", "web", "shell", "e2e")
ALL = frozenset(UNITS)
NONE: frozenset[str] = frozenset()

# (pattern, units, why). First match wins. `*` also crosses `/` (fnmatch), so `tasks/*` is the
# whole folder. A pattern without a slash and without a star names one file at the root.
RULES: tuple[tuple[str, frozenset[str], str], ...] = (
    # tests/agents_real_run_test.sh compares a dry run with this committed evidence.
    (
        "tasks/evidence/agents-a5/*",
        frozenset({"shell"}),
        "evidence read by a shell test",
    ),
    # Prose, plans and screenshots. Nothing executable reads them: ci_plan_test.py fails when a
    # test or a script of the units starts to.
    ("tasks/*", NONE, "task notes and evidence"),
    ("docs/*", NONE, "documentation"),
    # tests/deploy_test.sh checks that the README still gives the recovery statements it tests.
    (
        "README.md",
        frozenset({"shell"}),
        "statements of the README are checked by a shell test",
    ),
    ("CONTRIBUTING.md", NONE, "documentation"),
    ("CAPABILITY-MAP.md", NONE, "documentation"),
    ("SPEC-*.md", NONE, "documentation"),
    (".github/pull_request_template.md", NONE, "the pull request template"),
    # The web app. No api test and no api code reads a file under web/ (checked by
    # ci_plan_test.py), and the api image is built from api/ alone. The shell unit stays: the
    # tailscale test reads web/Dockerfile.
    ("web/*", frozenset({"web", "shell", "e2e"}), "the web app"),
)

# The jobs of ci.yml and, for each unit a job holds, the ids of the steps that are its checks.
# `verify` reads the outcome of exactly these steps; ci_plan_test.py checks that each id exists
# in that job and is run under that unit's output.
LANES: dict[str, dict[str, tuple[str, ...]]] = {
    "api": {
        "api": ("ruff", "pytest", "coverage", "docker_tests", "scoring"),
    },
    "checks": {
        "shell": (
            "shellcheck",
            "env_check_test",
            "e2e_groups",
            "ci_plan_test",
            "deploy_test",
            "agents_real_run_test",
            "ci_runners_test",
            "tailscale_test",
        ),
        "web": ("lint", "typecheck", "build"),
        "e2e": ("e2e_apps",),
    },
    "journeys": {
        "web": ("vitest",),
        "e2e": ("e2e_identity", "e2e_sweep"),
    },
}
# Steps that run on every run, whatever the plan: pre-commit.
ALWAYS: dict[str, tuple[str, ...]] = {"checks": ("precommit",)}


def units_for(path: str) -> tuple[frozenset[str], str]:
    """The units one changed path needs, and the reason (the rule, or that no rule knows it)."""
    for pattern, units, why in RULES:
        if "/" not in pattern and "/" in path:
            continue  # a pattern without a folder names files at the root only
        if fnmatch.fnmatchcase(path, pattern):
            return units, why
    return ALL, "no rule names this path"


def plan_for(paths: list[str]) -> tuple[frozenset[str], list[str]]:
    """The units a set of changed paths needs, and one line per path that asked for any."""
    needed: set[str] = set()
    notes = []
    for path in paths:
        units, why = units_for(path)
        if units:
            notes.append(f"{path}: {', '.join(sorted(units))} ({why})")
        needed |= units
    return frozenset(needed), notes


def git_env() -> dict[str, str]:
    """The environment for a git command that must read the repository at the path it is given.
    Inside a git hook (pre-commit runs ci_plan_test.py) git exports GIT_DIR, GIT_INDEX_FILE and
    the like, and a git started with them works on the hook's repository whatever `-C` says."""
    return {
        name: value for name, value in os.environ.items() if not name.startswith("GIT_")
    }


def changed_paths(root: Path) -> list[str] | None:
    """The files of the merge commit a pull request run is checked out at, against its base
    (the first parent). None when that cannot be read for certain: not a merge commit, a shallow
    checkout without the parents, git failing."""

    def git(*args: str) -> str | None:
        done = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
            env=git_env(),
        )
        return done.stdout if done.returncode == 0 else None

    parents = git("rev-list", "--parents", "-n", "1", "HEAD")
    if parents is None or len(parents.split()) != 3:
        return None
    # --no-renames: a moved file counts at its old and at its new path.
    names = git("diff", "--name-only", "--no-renames", "-z", "HEAD^1", "HEAD")
    if names is None:
        return None
    return [name for name in names.split("\0") if name]


def decide(event: str, root: Path) -> tuple[frozenset[str], str, list[str]]:
    """(units, one sentence saying why, detail lines) for this run."""
    if event != "pull_request":
        return ALL, f"event {event or 'unknown'}: every unit runs", []
    paths = changed_paths(root)
    if paths is None:
        return ALL, "the changed files could not be read: every unit runs", []
    if not paths:
        return ALL, "no changed file found: every unit runs", []
    units, notes = plan_for(paths)
    if units == ALL:
        return ALL, f"{len(paths)} changed files, every unit runs", notes
    if not units:
        return (
            units,
            f"{len(paths)} changed files, documentation only: pre-commit alone",
            notes,
        )
    return units, f"{len(paths)} changed files: {', '.join(sorted(units))}", notes


def encode(units: frozenset[str]) -> str:
    """The plan as one string, the same in every job of a run: the units in a fixed order."""
    return ",".join(unit for unit in UNITS if unit in units) or "none"


def write_outputs(units: frozenset[str], sentence: str, notes: list[str]) -> None:
    lines = [f"{unit}={'true' if unit in units else 'false'}" for unit in UNITS]
    lines.append(f"plan={encode(units)}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    print(f"ci_plan: {sentence}")
    for line in lines:
        print(f"ci_plan: {line}")
    shown = notes[:20]
    for note in shown:
        print(f"ci_plan:   {note}")
    if len(notes) > len(shown):
        print(f"ci_plan:   and {len(notes) - len(shown)} more")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"### CI plan\n\n- {sentence}\n- units: {encode(units)}\n\n")


def verify_problems(
    lane: str, plan: str, steps: dict[str, dict[str, object]], job_status: str
) -> list[str]:
    """Why this job must not count as a success (empty when it may).

    `steps` is the `steps` context of the job: id -> {"outcome": ...}. A step the plan needed
    must have run and succeeded; a step of a unit the plan left out must have been skipped (it
    ran by mistake otherwise: its condition no longer follows the plan). A job that already
    failed or was cancelled is not judged again."""
    if lane not in LANES and lane not in ALWAYS:
        return [f"unknown job '{lane}': add it to LANES in ci_plan.py"]
    names = set(plan.split(",")) - {"none", ""}
    if not names <= set(UNITS):
        return [f"the plan '{plan}' names an unknown unit"]
    if job_status != "success":
        return []
    problems = []

    def outcome(step: str) -> str:
        return str(steps.get(step, {}).get("outcome", "missing"))

    for step in ALWAYS.get(lane, ()):
        if outcome(step) != "success":
            problems.append(
                f"step '{step}' runs on every run and its outcome is {outcome(step)}"
            )
    for unit, ids in LANES.get(lane, {}).items():
        for step in ids:
            got = outcome(step)
            if unit in names and got != "success":
                problems.append(f"the plan needs {unit} and its step '{step}' is {got}")
            if unit not in names and got != "skipped":
                problems.append(
                    f"the plan leaves {unit} out and its step '{step}' is {got}"
                )
    return problems


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[2]
    if len(argv) > 1 and argv[1] == "verify":
        lane = os.environ.get("CI_LANE", "")
        plan = os.environ.get("CI_PLAN", "")
        status = os.environ.get("CI_JOB_STATUS", "")
        try:
            steps = json.loads(os.environ.get("CI_STEPS", ""))
        except json.JSONDecodeError:
            print(
                "ci_plan: CI_STEPS is not the JSON of the steps context",
                file=sys.stderr,
            )
            return 1
        if not plan:
            print(
                "ci_plan: no plan was computed in this job (CI_PLAN is empty)",
                file=sys.stderr,
            )
            return 1
        problems = verify_problems(lane, plan, steps, status)
        for problem in problems:
            print(f"ci_plan: {lane}: {problem}", file=sys.stderr)
        if problems:
            return 1
        print(
            f"ci_plan: {lane}: every step the plan '{plan}' needs has run (job {status})"
        )
        return 0
    if len(argv) > 1 and argv[1] == "--paths":
        units, notes = plan_for(argv[2:])
        write_outputs(units, f"{len(argv) - 2} paths given: {encode(units)}", notes)
        return 0
    if len(argv) > 1:
        print(__doc__, file=sys.stderr)
        return 2
    units, sentence, notes = decide(os.environ.get("GITHUB_EVENT_NAME", ""), root)
    write_outputs(units, sentence, notes)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
