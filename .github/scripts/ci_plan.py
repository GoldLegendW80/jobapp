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

import ast
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
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
            "remote_ops_test",
            "extra_ca_test",
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


# --- the journey groups of the batch check (issue #552, owner's decision of 2026-10-21) ----------
#
#     python3 .github/scripts/ci_plan.py journeys <base commit> <head commit> <groups
#
# ops/local-check.sh runs every journey group of web/e2e on a stack of its own, and they are most
# of its time. A batch runs only the groups its changed files touch: the files of <head> against
# <base> (the merged tree against the main it was merged onto), each file judged by the first of
# these that answers:
#
# 1. RULES above: a file whose units leave out e2e (documentation, task notes, Claude Code's
#    configuration) touches no group. The same rules as the pull request plan of CI, not a copy.
# 2. JOURNEY_RULES below: the gate itself and this mapping run every group, the fakes of a group
#    run that group, the scripts of operations and the shell tests run none.
# 3. A file of an api module (api/app/<module>/) or of a web feature (web/src/features/<feature>/)
#    touches the groups of its module and of every module that imports it, directly or through
#    other files: the imports are read from the tree (python's ast, the import lines of
#    TypeScript), file by file, so a module that shares one helper with another passes on only
#    that file. The files that only wire everything together (api/app/main.py, worker.py,
#    models.py; web/src/routes.tsx, App.tsx, main.tsx) carry nothing on, and any other file
#    outside a module (api/app/db.py, web/src/lib/) is shared: every group runs. AREA_GROUPS
#    names the groups of each module.
# 4. A spec or a group config of web/e2e touches its own group; any other file there (stack.ts,
#    a helper, playwright.config.ts) every group.
# 5. Anything else runs every group: a path nobody decided is checked in full.
#
# Input on stdin: one line per group, `<group>\t<its Playwright arguments>` as journey_specs of
# ops/local-check.sh prints them (a list of spec files, or `-c e2e/<group>-all.config.ts`). Output:
# `changed\t<number of files>\t<sha256 of their sorted list>`, then one line per group in the order
# given, `run\t<group>\t<why>` or `skip\t<group>\t<why>`. The pytest suite, the lint, the type
# check, the build, Vitest and the shell tests are not scoped: the gate runs them in full.

EVERY = "every"
NO_GROUP: frozenset[str] = frozenset()

# (pattern, groups or EVERY, why), first match wins, matched as RULES are.
JOURNEY_RULES: tuple[tuple[str, frozenset[str] | str, str], ...] = (
    ("ops/local-check.sh", EVERY, "the gate itself"),
    ("ops/ship.sh", EVERY, "the script that runs the gate"),
    (".github/scripts/ci_plan.py", EVERY, "this mapping"),
    (
        ".github/scripts/e2e_groups.py",
        EVERY,
        "it reads the group configs for this mapping",
    ),
    (".github/scripts/ci_env.sh", EVERY, "the .env of every journey stack"),
    ("api/alembic/*", EVERY, "a migration, every journey stack runs them"),
    ("api/pyproject.toml", EVERY, "the api's dependencies"),
    ("api/uv.lock", EVERY, "the api's dependencies"),
    ("web/package.json", EVERY, "the web's dependencies"),
    ("web/pnpm-lock.yaml", EVERY, "the web's dependencies"),
    ("*/Dockerfile", EVERY, "an image of every journey stack"),
    ("compose*.yml", EVERY, "the compose files of every journey stack"),
    (
        ".github/scripts/synthetic_jobs.py",
        frozenset({"apps"}),
        "the data of the apps group",
    ),
    (".github/*", NO_GROUP, "CI configuration, no journey reads it"),
    ("ops/*", NO_GROUP, "an operations script, no journey runs it"),
    ("tests/ats-stub.compose.yml", EVERY, "the fake ATS of every journey stack"),
    ("tests/ats_stub.py", EVERY, "the fake ATS of every journey stack"),
    (
        "tests/ats-forms-stub.compose.yml",
        frozenset({"apply", "opportunities"}),
        "the fake forms server",
    ),
    (
        "tests/ats_forms_stub.py",
        frozenset({"apply", "opportunities"}),
        "the fake forms server",
    ),
    (
        "tests/ats_forms/*",
        frozenset({"apply", "opportunities"}),
        "the fake forms server",
    ),
    (
        "tests/gmail-stub.compose.yml",
        frozenset({"mail"}),
        "the fake Google of the mail group",
    ),
    (
        "tests/browser-pages.compose.yml",
        frozenset({"browser"}),
        "the static pages of the browser group",
    ),
    (
        "tests/browser_pages/*",
        frozenset({"browser"}),
        "the static pages of the browser group",
    ),
    ("tests/*_test.sh", NO_GROUP, "a shell test, the gate runs them all"),
    ("tests/*jetson*", NO_GROUP, "a run on the Jetson, never in the gate"),
    (
        "api/tests/*",
        NO_GROUP,
        "a pytest file, the gate runs the suite in full, no image holds it",
    ),
    ("browser/*", frozenset({"browser", "apply"}), "the browser service"),
    ("renderer/*", frozenset({"documents", "apply"}), "the PDF renderer"),
    (".pre-commit-config.yaml", NO_GROUP, "pre-commit, run in full by the gate"),
    (".gitignore", NO_GROUP, "git configuration"),
    (".rsyncignore", NO_GROUP, "the file list of a deploy"),
)

# The journey groups of each module: an api module (api/app/<module>) or a web feature
# (web/src/features/<feature>), by folder name. EVERY for the modules every group goes through:
# each journey signs in (identity, auth), seeds postings and a profile (jobs, profile), sets a
# model credential (agents) and moves through the shell, the account and the onboarding pages.
# A module missing here runs every group.
AREA_GROUPS: dict[str, frozenset[str] | str] = {
    "identity": EVERY,
    "profile": EVERY,
    "jobs": EVERY,
    "agents": EVERY,
    "auth": EVERY,
    "shell": EVERY,
    "public": EVERY,
    "account": EVERY,
    "admin": EVERY,
    "onboarding": EVERY,
    "health": EVERY,
    "ingestion": frozenset({"ingestion", "documents"}),
    "mail": frozenset({"mail"}),
    "documents": frozenset({"documents", "apply"}),
    "apply": frozenset({"apply", "browser", "opportunities"}),
    "browser": frozenset({"browser", "apply"}),
    "opportunities": frozenset({"opportunities"}),
}

API_WIRING = frozenset({"app.main", "app.worker", "app.models"})
WEB_WIRING = frozenset({"web/src/routes.tsx", "web/src/App.tsx", "web/src/main.tsx"})
SHARED = "<shared>"
TS_IMPORT = re.compile(
    r"""(?:^|[\s;])(?:import|export)\s[^'"`;]*?\bfrom\s*["']([^"']+)["']"""
    r"""|\bimport\(\s*["']([^"']+)["']\s*\)"""
    r"""|^\s*import\s*["']([^"']+)["']""",
    re.MULTILINE,
)


def matches(pattern: str, path: str) -> bool:
    """A rule's pattern against a path, as units_for reads RULES."""
    if "/" not in pattern and "/" in path:
        return False
    return fnmatch.fnmatchcase(path, pattern)


def web_test_file(path: str) -> bool:
    return path.startswith("web/src/test/") or path.endswith((".test.ts", ".test.tsx"))


class Imports:
    """Who imports whom in the tree at `root`, file by file, read once: for the api, python
    modules (app.jobs.sweep); for the web, paths (web/src/features/jobs/List.tsx)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._api: dict[str, set[str]] | None = None
        self._web: dict[str, set[str]] | None = None

    def api(self) -> dict[str, set[str]]:
        if self._api is None:
            self._api = {}
            base = self.root / "api"
            modules: dict[str, Path] = {}
            for path in sorted((base / "app").rglob("*.py")):
                name = ".".join(path.relative_to(base).with_suffix("").parts)
                modules[name.removesuffix(".__init__")] = path

            def resolve(name: str) -> str | None:
                while name and name not in modules:
                    name = name.rpartition(".")[0]
                return name or None

            for name, path in modules.items():
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"))
                except (SyntaxError, UnicodeDecodeError, OSError):
                    continue  # pytest and ruff fail on it; here it carries nothing on
                for node in ast.walk(tree):
                    targets: list[str] = []
                    if isinstance(node, ast.Import):
                        targets = [alias.name for alias in node.names]
                    elif (
                        isinstance(node, ast.ImportFrom)
                        and node.module
                        and not node.level
                    ):
                        targets = [
                            f"{node.module}.{alias.name}" for alias in node.names
                        ]
                    for target in targets:
                        if not target.startswith("app."):
                            continue
                        found = resolve(target)
                        # Importing app.mail.accounts runs app/mail/__init__.py first: the
                        # packages above a module are imported with it.
                        while found and found != "app":
                            if found != name:
                                self._api.setdefault(found, set()).add(name)
                            found = found.rpartition(".")[0]
        return self._api

    def web(self) -> dict[str, set[str]]:
        if self._web is None:
            self._web = {}
            src = self.root / "web/src"
            for path in sorted(src.rglob("*")):
                if path.suffix not in (".ts", ".tsx") or not path.is_file():
                    continue
                me = path.relative_to(self.root).as_posix()
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for found in TS_IMPORT.finditer(text):
                    spec = next(group for group in found.groups() if group)
                    if spec.startswith("@/"):
                        target = src / spec[2:]
                    elif spec.startswith("."):
                        target = path.parent / spec
                    else:
                        continue  # a package
                    for candidate in (
                        target,
                        target.with_name(target.name + ".ts"),
                        target.with_name(target.name + ".tsx"),
                        target / "index.ts",
                        target / "index.tsx",
                    ):
                        if candidate.is_file():
                            name = Path(os.path.normpath(candidate)).relative_to(
                                self.root
                            )
                            self._web.setdefault(name.as_posix(), set()).add(me)
                            break
        return self._web


def api_area(module: str, root: Path) -> str:
    """app.jobs.sweep and app.jobs (its __init__.py) -> jobs; app.db (a file of the core) ->
    SHARED."""
    parts = module.split(".")
    if len(parts) > 2 or (len(parts) == 2 and (root / "api/app" / parts[1]).is_dir()):
        return parts[1]
    return SHARED


def web_area(path: str) -> str:
    parts = path.split("/")
    return parts[3] if len(parts) > 4 and parts[2] == "features" else SHARED


def importers(start: str, graph: dict[str, set[str]], stop) -> set[str]:
    """`start` and everything that imports it, directly or not, without going through `stop`."""
    seen = {start}
    todo = [start]
    while todo:
        for user in graph.get(todo.pop(), ()):
            if user not in seen and not stop(user):
                seen.add(user)
                todo.append(user)
    return seen


def areas_groups(areas: set[str]) -> tuple[frozenset[str] | str, str]:
    groups: set[str] = set()
    for area in sorted(areas):
        if area == SHARED:
            return EVERY, "a file outside the modules is shared"
        decided = AREA_GROUPS.get(area)
        if decided is None:
            return EVERY, f"no journey groups are decided for the module {area}"
        if decided == EVERY:
            return EVERY, f"every journey goes through the module {area}"
        groups |= decided
    names = ", ".join(sorted(areas))
    return frozenset(groups), f"the module{'s' if len(areas) > 1 else ''} {names}"


def journey_groups_for(
    path: str, specs: dict[str, str], configs: dict[str, str], imports: Imports
) -> tuple[frozenset[str] | str, str]:
    """(the groups one changed path touches, or EVERY; why). `specs` maps a spec file name to its
    group, `configs` a config path (web/e2e/x-all.config.ts) to its group."""
    units, why = units_for(path)
    if "e2e" not in units:
        return NO_GROUP, why
    for pattern, groups, why in JOURNEY_RULES:
        if matches(pattern, path):
            return groups, why
    parts = path.split("/")
    if path.startswith("api/app/"):
        if len(parts) < 4:
            return EVERY, "the api core"
        area = parts[2]
        if not path.endswith(".py"):
            return areas_groups({area})
        module = ".".join(Path(path).with_suffix("").parts[1:]).removesuffix(
            ".__init__"
        )
        users = importers(module, imports.api(), lambda name: name in API_WIRING)
        return areas_groups({api_area(name, imports.root) for name in users} | {area})
    if path.startswith("web/src/"):
        if web_test_file(path):
            return NO_GROUP, "a Vitest file, the gate runs Vitest in full"
        users = importers(
            path, imports.web(), lambda name: name in WEB_WIRING or web_test_file(name)
        )
        return areas_groups({web_area(name) for name in users})
    if path.startswith("web/e2e/") and len(parts) == 3:
        if parts[2] in specs:
            return frozenset({specs[parts[2]]}), "a spec of the group"
        if path in configs:
            return frozenset({configs[path]}), "the config of the group"
        return EVERY, "a file of web/e2e that is not one group's spec or config"
    return EVERY, "no rule names this path"


def journey_plan(
    root: Path, paths: list[str], group_args: Sequence[tuple[str, str]]
) -> list[tuple[str, str, str]]:
    """[(run|skip, group, why)] for the changed paths, in the order of the groups given."""
    import e2e_groups  # beside this file; only this command needs it

    specs: dict[str, str] = {}
    configs: dict[str, str] = {}
    for group, args in group_args:
        words = args.split()
        for index, word in enumerate(words):
            if word.endswith(".spec.ts"):
                specs[word.rpartition("/")[2]] = group
            if word == "-c" and index + 1 < len(words):
                config = words[index + 1]
                configs[f"web/{config}"] = group
                for name in e2e_groups.config_specs(root / "web" / config):
                    specs[name] = group
    imports = Imports(root)
    pulled: dict[str, str] = {}
    every = ""
    for path in paths:
        groups, why = journey_groups_for(path, specs, configs, imports)
        if groups == EVERY:
            every = every or f"{shown(path)}: {why}"
            continue
        for group in sorted(groups):
            pulled.setdefault(group, f"{shown(path)}: {why}")
    plan = []
    for group, _ in group_args:
        if every:
            plan.append(("run", group, f"every group, {every}"))
        elif group in pulled:
            plan.append(("run", group, pulled[group]))
        else:
            plan.append(("skip", group, "no changed file touches it"))
    return plan


def changes_digest(paths: list[str]) -> str:
    """The sha256 of a change set: its paths sorted, one per line. ops/ship.sh computes the same
    from a PASS record's base and head, so a record is never taken for another change set."""
    return hashlib.sha256(
        "".join(f"{path}\n" for path in sorted(paths)).encode()
    ).hexdigest()


def journeys_main(root: Path, base: str, head: str) -> int:
    done = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            base,
            head,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=git_env(),
    )
    if done.returncode != 0:
        print(
            f"ci_plan: cannot read the files of {head} against {base}", file=sys.stderr
        )
        return 1
    paths = [name for name in done.stdout.split("\0") if name]
    group_args = []
    for line in sys.stdin.read().splitlines():
        if line.strip():
            group, _, args = line.partition("\t")
            group_args.append((group, args))
    print(f"changed\t{len(paths)}\t{changes_digest(paths)}")
    for verdict, group, why in journey_plan(root, paths, group_args):
        print(f"{verdict}\t{group}\t{why}")
    return 0


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[2]
    if len(argv) == 4 and argv[1] == "journeys":
        return journeys_main(root, argv[2], argv[3])
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
