"""What a CI run has to run, decided from the paths a pull request changes (issue #91).

    python3 .github/scripts/ci_plan.py                 in a job of ci.yml: writes the step outputs
    python3 .github/scripts/ci_plan.py --paths a b c   by hand: the plan for these paths
    python3 .github/scripts/ci_plan.py verify          last step of a job: what was planned ran

The units of .github/workflows/ci.yml:

    api     ruff, pytest with the 95 percent coverage floor, the docker marked tests
    web     eslint, tsc, build, vitest
    shell   shellcheck and the test scripts of tests/
    e2e     the four Playwright groups on their scratch stacks

pre-commit and the secret scan are no unit: they run on every run, whatever changed.

Only a pull request against main is ever narrowed. A push to main, a manual run and anything
this script cannot read for certain run every unit: a green run on main is what deploys. So does
a pull request against any other branch (a stacked one): its files against that branch say
nothing about what main receives when the base is changed to main later, which starts no run,
and the branch under it may never have had a green run of its own. A pull request against main
is judged on the files of its merge commit against main, each file by the first rule of RULES
that matches it. A path no rule names runs every unit, so the rules only ever list what is known
to be safe to skip, and a new folder is tested in full until someone decides otherwise.

Why each rule may skip what it skips is written next to it, and ci_plan_test.py holds the checks
that keep those reasons true (no api file reads a file of web/, for instance).

Every job of ci.yml computes this plan for itself in its first step (no job waits for another),
gives it to its steps as outputs, and ends with `verify`, which fails the job when a step that
the plan needed did not run or one that it left out did. No job has a condition of its own, so
none is ever skipped, and the run is green when every job is.
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
# The one base branch against which a pull request may be narrowed (GITHUB_BASE_REF).
DEFAULT_BRANCH = "main"

# (pattern, units, why). First match wins. `*` also crosses `/` (fnmatch), so `tasks/*` is the
# whole folder. A pattern without a slash and without a star names one file at the root.
RULES: tuple[tuple[str, frozenset[str], str], ...] = (
    # tests/agents_real_run_test.sh compares a dry run with this committed evidence.
    (
        "tasks/evidence/agents-a5/*",
        frozenset({"shell"}),
        "evidence read by a shell test",
    ),
    # api/tests/ingestion/test_citation_examples.py fails when the table of examples printed in
    # this plan is not the one its data generates, and compares the verdicts of the first 108
    # with the output of the script that first executed them.
    (
        "tasks/ingestion/todo.md",
        frozenset({"api"}),
        "its table of citation examples is checked by an api test",
    ),
    (
        "tasks/evidence/ingestion-g0/citation_rules.txt",
        frozenset({"api"}),
        "evidence read by an api test",
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
    # Claude Code's entry point and project configuration: agent definitions, slash commands and
    # permissions, read by Claude Code only.
    ("CLAUDE.md", NONE, "documentation for Claude Code"),
    (".claude/*", NONE, "Claude Code project configuration"),
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
            "secret_scan_test",
            "tailscale_test",
            "local_check_test",
            "agent_test_test",
            "ship_test",
            "browser_egress_test",
            "dev_setup_test",
        ),
        # ingestion: every web/e2e/ingestion-*.spec.ts through one config, so a task of that
        # module adds a spec without a new step here or in ci.yml (e2e_groups.py, SPEC_PREFIX).
        "e2e": ("e2e_apps", "e2e_ingestion"),
    },
    "journeys": {
        "web": ("lint", "typecheck", "build", "vitest"),
        "e2e": ("e2e_identity", "e2e_sweep"),
    },
}
# Steps that run on every run, whatever the plan: pre-commit, and the secret scan of the
# commits the run brings (documentation can hold a secret as well as code can).
ALWAYS: dict[str, tuple[str, ...]] = {"checks": ("precommit", "secrets")}
# Every other step of a job, by id: what the checks need around them, never a check. The three
# tables together are the whole of ci.yml: ci_plan_test.py fails on a step that has no id or is
# in none of them, so a check added later cannot stay outside `verify`, and on a step listed
# here whose command is a test runner's.
SUPPORT: dict[str, tuple[str, ...]] = {
    "api": (
        "checkout",
        "plan",
        "uv",  # tools
        "pg_nofsync",  # the service container's settings
        "api_install",  # dependencies
        "coverage_summary",  # what is written to the run's summary
        "summary",
        "memory",
        "verify",
    ),
    "checks": (
        "checkout",
        "plan",
        "uv",
        "precommit_cache",
        "pnpm",
        "node",
        "web_install",
        "e2e_env",  # the scratch stack: its .env, images, browser, start, data
        "images",
        "buildx",
        "images_hosted",
        "e2e_ready",
        "apps_stack",
        "apps_data",
        "apps_logs",  # logs, teardown
        "apps_down",
        "ingestion_stack",  # the second scratch stack of this job, after the first is gone
        "ingestion_logs",
        "ingestion_down",
        "upload",
        "memory",
        "verify",
    ),
    "journeys": (
        "checkout",
        "plan",
        "uv",
        "pnpm",
        "node",
        "web_install",
        "summary",
        "e2e_env",
        "images",
        "buildx",
        "images_hosted",
        "e2e_ready",
        "identity_stack",
        "identity_logs",
        "identity_down",
        "sweep_stack",
        "sweep_logs",
        "sweep_down",
        "upload",
        "memory",
        "verify",
    ),
}


def units_for(path: str) -> tuple[frozenset[str], str]:
    """The units one changed path needs, and the reason (the rule, or that no rule knows it)."""
    for pattern, units, why in RULES:
        if "/" not in pattern and "/" in path:
            continue  # a pattern without a folder names files at the root only
        if fnmatch.fnmatchcase(path, pattern):
            return units, why
    return ALL, "no rule names this path"


def shown(text: str) -> str:
    """A name from outside (a file, a branch) as printable text on one line. A file name may
    hold a newline, and the rest of it would start a log line of its own: `::error::`, or any
    other workflow command of the runner."""
    return "".join(
        char if char.isprintable() else char.encode("unicode_escape").decode("ascii")
        for char in text
    )


def plan_for(paths: list[str]) -> tuple[frozenset[str], list[str]]:
    """The units a set of changed paths needs, and one line per path that asked for any."""
    needed: set[str] = set()
    notes = []
    for path in paths:
        units, why = units_for(path)
        if units:
            notes.append(f"{shown(path)}: {', '.join(sorted(units))} ({why})")
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


def decide(event: str, base: str, root: Path) -> tuple[frozenset[str], str, list[str]]:
    """(units, one sentence saying why, detail lines) for this run. `base` is the branch a pull
    request is to be merged into (GITHUB_BASE_REF, empty for any other event)."""
    if event != "pull_request":
        return ALL, f"event {event or 'unknown'}: every unit runs", []
    if base != DEFAULT_BRANCH:
        against = shown(base) or "an unknown base"
        sentence = f"a pull request against {against}, not against {DEFAULT_BRANCH}"
        return ALL, f"{sentence}: every unit runs", []
    # The sentence names the base: the log of a run is then the record of what it was started
    # against, which the checks of a head commit do not say (CONTRIBUTING.md, the merge rule).
    scope = f"a pull request against {DEFAULT_BRANCH}"
    paths = changed_paths(root)
    if paths is None:
        return ALL, f"{scope}, its files could not be read: every unit runs", []
    if not paths:
        return ALL, f"{scope}, no changed file found: every unit runs", []
    units, notes = plan_for(paths)
    scope = f"{scope}, {len(paths)} changed files"
    if units == ALL:
        return ALL, f"{scope}: every unit runs", notes
    if not units:
        return units, f"{scope}, documentation only: pre-commit alone", notes
    return units, f"{scope}: {', '.join(sorted(units))}", notes


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
    failed or was cancelled is not judged again, and those two words are the only ones that
    skip the judgement: an empty or misspelled status is a problem itself, never a pass."""
    if lane not in LANES and lane not in ALWAYS:
        return [f"unknown job '{lane}': add it to LANES in ci_plan.py"]
    names = set(plan.split(",")) - {"none", ""}
    if not names <= set(UNITS):
        return [f"the plan '{plan}' names an unknown unit"]
    if job_status in ("failure", "cancelled"):
        return []
    if job_status != "success":
        status = shown(job_status)
        return [f"CI_JOB_STATUS is '{status}', not success, failure or cancelled"]
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
    units, sentence, notes = decide(
        os.environ.get("GITHUB_EVENT_NAME", ""),
        os.environ.get("GITHUB_BASE_REF", ""),
        root,
    )
    write_outputs(units, sentence, notes)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
