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
    # tests/deploy_test.sh and tests/migrate_test.sh read the runbooks: the cron line and a
    # stale sentence of the backups one, the recovery statement of the continuous delivery one.
    (
        "docs/runbooks/*",
        frozenset({"shell"}),
        "the runbooks are read by shell tests",
    ),
    ("docs/*", NONE, "documentation"),
    ("README.md", frozenset({"shell"}), "the README is scanned by a shell test"),
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
# 1. An api/app/ file (owner's decision of 2026-10-25: narrow the gate) runs the groups of its
#    own module (API_AREAS) and those of every api file that imports it, directly or
#    transitively, read from the Python imports of the tree (Imports.api). main.py and worker.py
#    are wiring and carry nothing on, as routes.tsx does for the web, save what main.py runs
#    beyond the wiring (a middleware, a handler, what the start builds: Imports.main_reach),
#    which runs every group. So does a file whose routes a page of WEB_SHARED_PAGES calls
#    (Imports.shared_api: the HTTP consumers the imports cannot see). Every group runs for a
#    shared file (app/*.py), a module's __init__.py, the models, a worker registration module
#    (worker.py REGISTRATION_MODULES), an asset, a module no line decides, and whenever the
#    reader is unsure: any dynamic import outside the reviewed loaders of API_LOADERS, an
#    unresolved import, a hook registered on an object it cannot resolve, a parse error.
# 2. RULES above: a file whose units leave out e2e (documentation, task notes, Claude Code's
#    configuration) touches no group. The same rules as the pull request plan of CI, not a copy.
# 3. JOURNEY_RULES below: the gate itself and this mapping run every group, the fakes of a group
#    run that group, the scripts of operations and the shell tests run none.
# 4. A web feature (web/src/features/<feature>/) touches its
#    groups and those of every feature that imports the file, directly or transitively.
#    Web wiring (routes.tsx, App.tsx, main.tsx) carries nothing on; files outside a feature
#    (web/src/lib/) are shared. AREA_GROUPS names the groups of web features.
# 5. A spec or a group config of web/e2e touches its own group; any other file there (stack.ts,
#    a helper, playwright.config.ts) every group.
# 6. Anything else runs every group: a path nobody decided is checked in full.
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
    (
        "browser/Dockerfile",
        frozenset({"browser", "apply"}),
        "the browser image, started only by the stacks of the browser profile",
    ),
    (
        "renderer/Dockerfile",
        frozenset({"documents", "apply"}),
        "the renderer image, as the renderer's own files",
    ),
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
        "tests/feeds-stub.compose.yml",
        frozenset({"opportunities"}),
        "the fake feeds server of the opportunities group",
    ),
    (
        "tests/feeds_stub.py",
        frozenset({"opportunities"}),
        "the fake feeds server of the opportunities group",
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
    ("api/tests/jobs/fake_ats.py", EVERY, "the fake ATS of every journey stack"),
    ("api/tests/mail/*", frozenset({"mail"}), "the fake Google and its mailbox data"),
    ("api/tests/ingestion/*", EVERY, "input data and streams read by journeys"),
    ("api/tests/documents/fixtures/*", EVERY, "document input data read by journeys"),
    (
        "api/tests/*",
        NO_GROUP,
        "other pytest files, the gate runs the suite in full",
    ),
    ("browser/*", frozenset({"browser", "apply"}), "the browser service"),
    ("renderer/*", frozenset({"documents", "apply"}), "the PDF renderer"),
    (".pre-commit-config.yaml", NO_GROUP, "pre-commit, run in full by the gate"),
    (".gitignore", NO_GROUP, "git configuration"),
    (".rsyncignore", NO_GROUP, "the file list of a deploy"),
)

# The journey groups of web features (web/src/features/<feature>), by folder name.
# EVERY for the features every group goes through:
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

# The journey groups of the api modules (api/app/<module>/), first match wins, matched as RULES
# are. What each group's journeys reach: every journey signs up (identity), passes the onboarding
# gate (opportunities onboarding), seeds postings and a profile (jobs, profile) and sets a model
# credential (agents). Apply's routes are driven by the apply group, by the browser group (B10's
# LinkedIn step lists applications) and by the opportunities group (a Like queues an application
# and writes its documents); only the apply and browser stacks start the browser service
# (COMPOSE_PROFILES=browser in ops/local-check.sh). The worker runs the browser sources and the
# freelance feeds on every sweep (worker.py browser_sources, feed_sources): the browser,
# opportunities and sweep groups sweep. The imports add the rest: a file of browser that apply
# imports runs apply's groups too.
API_AREAS: tuple[tuple[str, frozenset[str] | str, str], ...] = (
    (
        "api/app/browser/sources/*",
        frozenset({"apply", "browser", "opportunities", "sweep"}),
        "a browser source, run by every sweep",
    ),
    (
        "api/app/opportunities/sources/*",
        frozenset({"browser", "opportunities", "sweep"}),
        "a freelance feed, run by every sweep",
    ),
    (
        "api/app/opportunities/onboarding*",
        EVERY,
        "the onboarding gate every new account passes",
    ),
    ("api/app/identity/*", EVERY, "every journey signs in"),
    ("api/app/profile/*", EVERY, "every journey has a profile"),
    ("api/app/jobs/*", EVERY, "every journey has postings"),
    ("api/app/agents/*", EVERY, "every journey sets a model credential"),
    (
        "api/app/apply/*",
        frozenset({"apply", "browser", "opportunities"}),
        "the api module apply",
    ),
    ("api/app/browser/*", frozenset({"apply", "browser"}), "the api module browser"),
    (
        "api/app/documents/*",
        frozenset({"apply", "documents", "opportunities"}),
        "the api module documents",
    ),
    (
        "api/app/ingestion/*",
        frozenset({"documents", "ingestion", "opportunities"}),
        "the api module ingestion",
    ),
    ("api/app/mail/*", frozenset({"mail"}), "the api module mail"),
    (
        "api/app/opportunities/*",
        frozenset({"opportunities"}),
        "the api module opportunities",
    ),
)
API_WIRING = frozenset({"api/app/main.py", "api/app/worker.py"})
# app/worker.py REGISTRATION_MODULES: the worker imports <module>.<name> of every module by
# itself, and what they register (crons, hooks, callbacks) runs for modules that never import
# them. Every group runs for one, and for every file it imports, except the reviewed ones here.
REGISTRATION_NAMES = frozenset({"agents", "engine", "freeze", "jobs", "writers"})
SCOPED_REGISTRATIONS = {
    "api/app/apply/agents.py": "it registers apply's internal answer agent and its tool, and "
    "imports the internal form and check agents: only the apply run starts them",
}
# The reviewed dynamic loaders: each imports, by name, the modules a literal of its own file
# names (a tuple, or a dict of them), from its own package and no other (no string of the file
# names another "app.<module>"). Those modules are read as imported by it. Any other dynamic
# import fails the reader: every group runs.
API_LOADERS = {
    "api/app/apply/registry.py": "ROUTER_MODULES",
    "api/app/browser/registry.py": "ROUTER_MODULES",
    "api/app/documents/routers.py": "ROUTERS",
    "api/app/documents/writers.py": "WRITERS",
    "api/app/ingestion/sandbox/main.py": "HANDLERS",  # by path, from its own folder
    "api/app/mail/registry.py": "MODULES",
    "api/app/mail/routers.py": "ROUTER_MODULES",
    "api/app/opportunities/registry.py": "ROUTER_MODULES",
}
# Names that load code by name: a file that mentions one is not read (every group), save the
# reviewed loaders, the wiring, and `importlib.util.find_spec("<a package outside app>")`.
DYNAMIC_NAMES = frozenset(
    {
        "__import__",
        "importlib",
        "import_module",
        "builtins",
        "__builtins__",
        "eval",
        "exec",
    }
    | {"compile", "__loader__", "__spec__"}
)
DYNAMIC_MODULES = frozenset(
    {"importlib", "builtins", "runpy", "pkgutil", "zipimport", "imp"}
)
# Registries of names, looked up only by the runs that name them: an agent definition (when it
# is internal: GET /api/agents lists the others) and an agent tool. Every other call of a
# `register...` function is a hook: the file is read as imported by the hook's module.
# app/agents/fake_streams/<agent>_<case>.json: the fake answers of an agent, read only by the
# runs that name them (agents/fake_backend.py load). An agent's name starts with its module.
STREAM_OWNERS = {
    "apply": "apply",
    "browser": "browser",
    "documents": "documents",
    "extract": "ingestion",
    "ingest": "ingestion",
    "transcribe": "ingestion",
}
NAMED_REGISTRIES = frozenset(
    {"api/app/agents/definitions.py", "api/app/agents/tools.py"}
)
# The web pages that journeys of many groups open, with failed requests watched: an api file
# whose routes their code calls runs every group, whatever its module (the imports above see no
# HTTP call). Read from the web tree (Imports.shared_api): the files each page imports, the
# request paths they hold, matched against the routes the api files declare. A new card on one
# of these pages widens its api file by itself; a new page shared this way is added here.
WEB_SHARED_PAGES = {
    "web/src/features/shell/app-shell.tsx": "the shell of every signed in page",
    "web/src/features/auth/auth-provider.tsx": "the session of every page",
    "web/src/features/auth/login-page.tsx": "the sign in of every journey",
    "web/src/features/onboarding/onboarding-gate.tsx": "the onboarding gate",
    "web/src/features/onboarding/welcome-page.tsx": "the onboarding",
    "web/src/features/jobs/jobs-page.tsx": "the job list",
    "web/src/features/jobs/job-detail-page.tsx": "the job page",
    "web/src/features/profile/profile-page.tsx": "the profile page",
    "web/src/features/account/account-page.tsx": "the account page",
}
# A string of the web code that is a page address, not a request: a link or a navigation.
WEB_LINK = re.compile(r"(?:\b(?:to|href|path)\s*[=:]\s*\{?\s*|\bnavigate\(\s*)$")
WEB_STRING = re.compile(r"""(["'`])(/(?:(?!\1)[^\n\\])*)\1""")
WEB_CONSTANT = re.compile(
    r"""\bconst\s+(\w+)(?:\s*:\s*\w+)?\s*=\s*(["'`])(/[^"'`$\n\\]*)\2"""
)
WEB_LEADING = re.compile(r"`\$\{(\w+)\}((?:[^`\n\\])*)`")
WEB_IMPORT = re.compile(r"""(?:\bfrom|\bimport)\s*\(?\s*["']([^"'\n]+)["']""")
ROUTE_DECORATOR = re.compile(
    r"@\s*[\w.]+\s*\.\s*(?:get|post|put|patch|delete|head|options|api_route|websocket)\b"
)
ROUTE_METHODS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "api_route"}
    | {"websocket"}
)


WEB_WIRING = frozenset({"web/src/routes.tsx", "web/src/App.tsx", "web/src/main.tsx"})
SHARED = "<shared>"
TS_TOKEN = re.compile(
    # ECMAScript 12.3: LF, CR, LS and PS terminate a single-line comment.
    # https://tc39.es/ecma262/multipage/ecmascript-language-lexical-grammar.html#sec-line-terminators
    r"\s+|//[^\n\r\u2028\u2029]*|/\*[\s\S]*?\*/|'(?:\\[\s\S]|[^'\\])*'|\"(?:\\[\s\S]|[^\"\\])*\"|`(?:\\[\s\S]|[^`\\])*`|[\w$]+|[^\s]"
)


class MappingError(ValueError):
    """Uncertain dependencies require every journey group."""


def source_files(root: Path, suffixes: tuple[str, ...]):
    # Path.rglob can suppress traversal errors, losing every consumer in a directory.
    def failed(error):
        raise error

    for directory, _, names in os.walk(root, onerror=failed):
        for name in sorted(names):
            if name.endswith(suffixes):
                yield Path(directory) / name


def ts_imports(text: str) -> list[str]:
    """Read import declarations/calls as tokens, without pretending to parse TypeScript.
    Unsupported targets fail closed. Comments and strings cannot hide declarations.
    https://www.typescriptlang.org/docs/handbook/modules/reference.html
    """
    tokens = []
    for match in TS_TOKEN.finditer(text):
        token = match.group()
        if not token.isspace() and not token.startswith(("//", "/*")):
            tokens.append(token)
    if any(t in ("'", '"', "`") for t in tokens) or text.count("/*") > text.count("*/"):
        raise MappingError("unterminated TypeScript string or comment")
    for token in tokens:
        if token == "/" or token.startswith("`") and "${" in token:
            raise MappingError("unsupported TypeScript expression can contain imports")
        if token.startswith(("'", '"')) and "\n" in token:
            raise MappingError("unsupported multiline TypeScript string")

    def literal(index):
        token = tokens[index] if index < len(tokens) else ""
        if len(token) < 2 or token[0] not in "'\"`" or "\\" in token or "${" in token:
            raise MappingError("unsupported TypeScript import target")
        return token[1:-1]

    specs = []
    seen_export = False
    for i, token in enumerate(tokens):
        if token == "export":
            seen_export = True
        tail = tokens[i + 1 : i + 2]
        if token in ("import", "require"):
            if tail == ["."]:
                raise MappingError("unsupported module loader property")
            if tail == ["("]:
                specs.append(literal(i + 2))
                if tokens[i + 3 : i + 4] != [")"]:
                    raise MappingError("unsupported TypeScript dynamic import")
                continue
            if token == "require":
                raise MappingError("unsupported require reference")
            if tail and tail[0].startswith(("'", '"')):
                specs.append(literal(i + 1))
                continue
            for j in range(i + 1, len(tokens)):
                if tokens[j] == "from":
                    specs.append(literal(j + 1))
                    break
                if tokens[j] in (";", "import", "export"):
                    raise MappingError("unsupported TypeScript import declaration")
            else:
                raise MappingError("incomplete TypeScript import declaration")
        elif token == "from" and seen_export:
            specs.append(literal(i + 1))
    return specs


def matches(pattern: str, path: str) -> bool:
    """A rule's pattern against a path, as units_for reads RULES."""
    if "/" not in pattern and "/" in path:
        return False
    return fnmatch.fnmatchcase(path, pattern)


def web_test_file(path: str) -> bool:
    return path.startswith("web/src/test/") or path.endswith((".test.ts", ".test.tsx"))


class Imports:
    """Web paths and their importers in the tree at `root`, read once."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._web: dict[str, set[str]] | None = None
        self._api: dict[str, set[str]] | None = None
        self._shared: dict[str, str] | None = None
        self._main: dict[str, set[str]] | None = None
        self._reach: dict[str, str] | None = None
        self._trees: dict[str, ast.Module] = {}

    def parsed(self, path: str) -> ast.Module:
        """The syntax tree of one api file, parsed once."""
        if path not in self._trees:
            text = (self.root / path).read_text(encoding="utf-8")
            self._trees[path] = ast.parse(text, filename=path)
        return self._trees[path]

    def shared_api(self) -> dict[str, str]:
        """The api files whose routes a page of WEB_SHARED_PAGES calls, and why."""
        if self._shared is None:
            routes = api_routes(self.root, self.parsed)
            found: dict[str, str] = {}
            for page, what in WEB_SHARED_PAGES.items():
                for request in sorted(web_requests(self.root, page)):
                    for file, patterns in routes.items():
                        if file not in found and any(
                            route_matches(request, p) for p in patterns
                        ):
                            found[file] = (
                                f"{what} calls its routes (/{'/'.join(request)})"
                            )
            self._shared = found
        return self._shared

    def main_uses(self) -> dict[str, set[str]]:
        if self._main is None:
            self._main = main_uses(self.root)
        return self._main

    def main_reach(self) -> dict[str, str]:
        """The api files that what main.py runs on every request or start reaches: a whole
        module it uses, and the files the names it uses call."""
        if self._reach is None:
            reach: dict[str, str] = {}
            for path, names in sorted(self.main_uses().items()):
                why = f"main.py runs {shown(path)} for every request or start"
                if "*" in names:
                    reach[path] = why
                    continue
                for other in sorted(member_reach(self.root, path, names)):
                    reach.setdefault(other, why)
            self._reach = reach
        return self._reach

    def api(self) -> dict[str, set[str]]:
        """api/app/ files and the files that import them, with the reviewed loaders and the
        hooks (`<module>.register_x(callback)`, read as the hook's module importing the caller)."""
        if self._api is None:
            graph: dict[str, set[str]] = {}
            for path in source_files(self.root / "api/app", (".py",)):
                me = path.relative_to(self.root).as_posix()
                imported, hosts = python_edges(self.root, me, self.parsed(me))
                for target in imported:
                    if target != me:
                        graph.setdefault(target, set()).add(me)
                # A hook's module calls the callback: it reads as importing the caller.
                for host in hosts:
                    if host != me:
                        graph.setdefault(me, set()).add(host)
            self._api = graph
        return self._api

    def web(self) -> dict[str, set[str]]:
        if self._web is None:
            graph: dict[str, set[str]] = {}
            src = self.root / "web/src"
            for path in source_files(src, (".ts", ".tsx")):
                me = path.relative_to(self.root).as_posix()
                text = path.read_text(encoding="utf-8")
                for spec in ts_imports(text):
                    target = resolve_web(self.root, path, spec)
                    if target is not None:
                        graph.setdefault(target, set()).add(me)
            self._web = graph
        return self._web


def python_files(root: Path, dotted: str) -> list[str] | None:
    """The files that importing `dotted` (app.x.y) runs: each package's __init__.py, then the
    module or package itself; None when it is no module of the tree."""
    found: list[str] = []
    folder = root / "api"
    parts = dotted.split(".")
    for index, part in enumerate(parts):
        folder = folder / part
        if (folder / "__init__.py").is_file():
            found.append((folder / "__init__.py").relative_to(root).as_posix())
        elif index == len(parts) - 1 and folder.with_suffix(".py").is_file():
            found.append(folder.with_suffix(".py").relative_to(root).as_posix())
        else:
            return None
    return found


def python_edges(root: Path, me: str, tree: ast.Module) -> tuple[set[str], set[str]]:
    """(the api files `me` imports, the files of the hooks it registers a callback with)."""
    package = ".".join(Path(me).parent.parts[1:])  # api/app/mail/x.py: app.mail
    parents = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }
    imported: set[str] = set()
    bound: dict[str, str] = {}  # a local name and the app module or member it names
    loader = me in API_LOADERS or me in API_WIRING

    def need(dotted: str) -> list[str]:
        files = python_files(root, dotted)
        if files is None:
            raise MappingError(f"unresolved Python import {dotted} in {me}")
        return files

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in DYNAMIC_MODULES:
                    continue  # judged with the names below
                if alias.name == "app" or alias.name.startswith("app."):
                    imported.update(need(alias.name))
                    bound[alias.asname or "app"] = alias.asname and alias.name or "app"
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                if node.level > len(parts):
                    raise MappingError(f"relative import above app in {me}")
                base = ".".join(parts[: len(parts) - node.level + 1])
                base += f".{node.module}" if node.module else ""
            else:
                base = node.module or ""
            if not loader and (
                base.split(".")[0] in DYNAMIC_MODULES
                or base == "sys"
                and any(a.name == "modules" for a in node.names)
            ):
                raise MappingError(f"a dynamic import in {me}")
            if base != "app" and not base.startswith("app."):
                continue
            imported.update(need(base))
            for alias in node.names:
                if alias.name == "*":
                    continue
                member = python_files(root, f"{base}.{alias.name}")
                imported.update(member or ())
                bound[alias.asname or alias.name] = f"{base}.{alias.name}"
    for node in ast.walk(tree):
        if loader:
            break
        if isinstance(node, ast.alias):
            if node.asname is None and node.name in ("importlib", "importlib.util"):
                continue  # `import importlib.util`, judged where the name is used
            flagged = node.name.split(".")[0] in DYNAMIC_MODULES | DYNAMIC_NAMES
        elif isinstance(node, ast.Name):
            flagged = node.id in DYNAMIC_NAMES and not (
                node.id == "importlib" and finds_a_spec(node, parents)
            )
        elif isinstance(node, ast.Attribute):
            flagged = node.attr in ("__import__", "import_module") or (
                node.attr == "modules"
                and isinstance(node.value, ast.Name)
                and node.value.id == "sys"
            )
        else:
            flagged = False
        if flagged:
            raise MappingError(f"a dynamic import in {me}")
    if me in API_LOADERS:
        imported |= loaded_modules(root, me, tree, package)
    return imported, hook_hosts(root, me, tree, bound)


def finds_a_spec(node: ast.Name, parents: dict[ast.AST, ast.AST]) -> bool:
    """`importlib.util.find_spec("<a package outside app>")`: it tells whether a package is
    installed and loads nothing."""
    util = parents.get(node)
    find = parents.get(util) if util is not None else None
    call = parents.get(find) if find is not None else None
    return (
        isinstance(util, ast.Attribute)
        and util.attr == "util"
        and isinstance(find, ast.Attribute)
        and find.attr == "find_spec"
        and isinstance(call, ast.Call)
        and call.func is find
        and not call.keywords
        and len(call.args) == 1
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
        and call.args[0].value.split(".")[0] != "app"
    )


def loaded_modules(root: Path, me: str, tree: ast.Module, package: str) -> set[str]:
    """The files a reviewed loader imports by name: its literal tuple, from its own package."""
    strings = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and n.value.startswith("app.")
    }
    if not strings <= {package}:
        raise MappingError(f"the loader {me} names another package")
    names = [
        n.value
        for n in tree.body
        if isinstance(n, ast.Assign | ast.AnnAssign)
        and n.value is not None
        and any(
            isinstance(t, ast.Name) and t.id == API_LOADERS[me]
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
        )
    ]
    if len(names) != 1:
        raise MappingError(f"the loader {me} has no single {API_LOADERS[me]}")
    try:
        listed = ast.literal_eval(names[0])
    except ValueError as error:
        raise MappingError(f"the names of the loader {me} are not literal") from error
    found: set[str] = set()
    for item in listed.values() if isinstance(listed, dict) else listed:
        name = item[0] if isinstance(item, tuple) else item
        if name is None and isinstance(listed, dict):
            continue  # a kind with no file (the sandbox's ping)
        if not isinstance(name, str):
            raise MappingError(f"the names of the loader {me} are not strings")
        found.update(python_files(root, f"{package}.{name}") or ())
    return found


def hook_hosts(
    root: Path, me: str, tree: ast.Module, bound: dict[str, str]
) -> set[str]:
    """The files whose hooks `me` registers a callback with: a call of `register...` on an
    imported app module or member. An internal agent definition or a tool registers a name
    that only its own runs look up; a hook nobody can resolve runs every group."""
    defined = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    hosts: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr.startswith("register"):
            target = func.value
            while isinstance(target, ast.Attribute):
                target = target.value
            dotted = bound.get(target.id) if isinstance(target, ast.Name) else None
            if dotted is None:
                if isinstance(target, ast.Name) and target.id in ("self", "cls"):
                    continue  # a method of the file's own class
                if me in API_LOADERS:
                    continue  # a loaded module's own register(): that file is read itself
                raise MappingError(f"a hook of an unknown object in {me}")
        elif isinstance(func, ast.Name) and func.id.startswith("register"):
            if func.id in defined:
                continue
            dotted = bound.get(func.id)
            if dotted is None:
                raise MappingError(f"a hook of an unknown function in {me}")
            dotted = dotted.rpartition(".")[0]
        else:
            continue
        files = python_files(root, dotted) or python_files(
            root, dotted.rpartition(".")[0]
        )
        if not files:
            raise MappingError(f"a hook of an unknown module in {me}")
        host = files[-1]
        if host in NAMED_REGISTRIES and getattr(func, "attr", "") == "register":
            if host.endswith("definitions.py") and not registers_internal(node, tree):
                hosts.add(
                    host
                )  # a listed agent: GET /api/agents names it to every page
            continue
        hosts.add(host)
    return hosts


def registers_internal(call: ast.Call, tree: ast.Module) -> bool:
    """`definitions.register(x)` where the definition x builds says `internal=True`, and no
    other `internal` of it says otherwise: x inline, a name of the file, or a call of one of
    its functions. Anything else counts as a listed agent."""
    if len(call.args) != 1 or call.keywords:
        return False
    arg = call.args[0]
    named = arg.func if isinstance(arg, ast.Call) else arg
    built: ast.AST = arg
    if isinstance(named, ast.Name):
        found = [
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            and n.name == named.id
            or isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == named.id for t in n.targets)
        ]
        if len(found) != 1 and not isinstance(arg, ast.Call):
            return False
        built = found[0] if len(found) == 1 else arg
    flags = [
        n.value
        for n in ast.walk(built)
        if isinstance(n, ast.keyword) and n.arg == "internal"
    ]
    return bool(flags) and all(
        isinstance(v, ast.Constant) and v.value is True for v in flags
    )


def api_own(
    path: str, imports: Imports | None = None
) -> tuple[frozenset[str] | str, str]:
    """The groups an api/app/ file runs for itself, before the files that import it. Without
    `imports`, its module's groups only (what the pages and main.py call is not read)."""
    parts = path.split("/")
    stream = path.removeprefix("api/app/agents/fake_streams/")
    owner = STREAM_OWNERS.get(stream.partition("_")[0]) if stream != path else None
    if owner and "/" not in stream and stream.endswith(".json"):
        for pattern, groups, _ in API_AREAS:
            if matches(pattern, f"api/app/{owner}/agent.py"):
                return groups, f"the fake stream of a {owner} agent"
    if not path.endswith(".py"):
        return EVERY, "an api asset, its readers are not inferred"
    if len(parts) == 3:
        return EVERY, "a shared file of the api"
    if len(parts) == 4 and parts[3] == "__init__.py":
        return EVERY, "a module's __init__.py runs with any import of the module"
    if parts[-1] == "models.py" or parts[-1].endswith("_models.py"):
        return EVERY, "the tables of every journey stack"
    if (
        parts[3].removesuffix(".py") in REGISTRATION_NAMES
    ) and path not in SCOPED_REGISTRATIONS:
        return EVERY, "a worker registration module, its hooks reach every module"
    if imports is not None:
        if path in imports.shared_api():
            return EVERY, imports.shared_api()[path]
        if path in imports.main_reach():
            return EVERY, imports.main_reach()[path]
        if path in imports.main_uses():
            names = ", ".join(sorted(imports.main_uses()[path]))
            return EVERY, f"main.py runs its {names} for every request or start"
    for pattern, groups, why in API_AREAS:
        if matches(pattern, path):
            return groups, why
    return EVERY, f"no journey groups are decided for the api module {parts[2]}"


def api_groups(path: str, imports: Imports) -> tuple[frozenset[str] | str, str]:
    """An api/app/ file: its own groups and those of every api file that imports it."""
    own, why = api_own(path, imports)
    if own == EVERY:
        return EVERY, why
    users = importers(path, imports.api(), lambda name: name in API_WIRING)
    groups: set[str] = set()
    for user in sorted(users):
        decided, reason = api_own(user, imports)
        if decided == EVERY and user in imports.main_uses():
            # main.py runs some names of `user` (a middleware), and those reach no file that
            # imports `path` (main_reach): the rest of `user` is its module's.
            decided, reason = api_own(user)
        if decided == EVERY:
            return EVERY, f"imported by {shown(user)}: {reason}"
        groups |= decided
    others = sorted(u for u in users if u != path)
    by = (
        f", imported by {len(others)} api file{'s' if len(others) != 1 else ''}"
        if others
        else ""
    )
    return frozenset(groups), f"{why}{by}"


def app_import_files(
    root: Path,
    me: str,
    node: ast.Import | ast.ImportFrom,
    only: ast.alias | None = None,
) -> set[str]:
    """The api file each name of one import statement of `me` names (a module or a member's),
    or the one name `only`."""
    found: set[str] = set()
    names = [only] if only is not None else node.names
    if isinstance(node, ast.Import):
        dotted = [a.name for a in names if a.name.startswith("app.")]
    else:
        base = node.module or ""
        if node.level:
            parts = ".".join(Path(me).parent.parts[1:]).split(".")
            base = ".".join(
                parts[: len(parts) - node.level + 1] + ([base] if base else [])
            )
        if base != "app" and not base.startswith("app."):
            return found
        dotted = [f"{base}.{a.name}" for a in names if a.name != "*"] or [base]
    for name in dotted:
        files = python_files(root, name) or python_files(root, name.rpartition(".")[0])
        if not files:
            raise MappingError(f"unresolved Python import {name} in {me}")
        found.add(files[-1])
    return found


def main_uses(root: Path) -> dict[str, set[str]]:
    """The api files main.py calls beyond the wiring (`app.include_router(x)`, `x.include(app)`,
    `x.load()`, a loop over a list of routers), each with the names it uses: a middleware, an
    exception handler, what the start builds. "*" is the whole module."""
    main = root / "api/app/main.py"
    if not main.is_file():
        return {}
    tree = ast.parse(main.read_text(encoding="utf-8"), filename="api/app/main.py")
    parents = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app"):
            for alias in node.names:
                bound[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app.") and alias.asname:
                    bound[alias.asname] = alias.name
    uses: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name) and node.id in bound):
            continue
        dotted = bound[node.id]
        files = python_files(root, dotted)
        use: ast.AST = node
        if files:
            parent = parents.get(node)
            if isinstance(parent, ast.Attribute):
                member, use = parent.attr, parent
            else:
                member = "*"
        else:
            files = python_files(root, dotted.rpartition(".")[0])
            member = dotted.rpartition(".")[2]
        if not files:
            raise MappingError(f"unresolved Python import {dotted} in api/app/main.py")
        around = parents.get(use)
        wiring = (
            isinstance(around, ast.Call)
            and (
                around.func is use
                and member in ("include", "load")
                or use in around.args
                and isinstance(around.func, ast.Attribute)
                and around.func.attr == "include_router"
            )
            or isinstance(around, ast.For)
            and around.iter is use
        )
        if not wiring:
            uses.setdefault(files[-1], set()).add(member)
    return uses


def member_reach(root: Path, path: str, names: set[str]) -> set[str]:
    """The other api files the given top level names of `path` reach through its own code:
    the imported names their bodies use, read transitively inside the file."""
    tree = ast.parse((root / path).read_text(encoding="utf-8"), filename=path)
    defined: dict[str, list[ast.AST]] = {}
    imported: dict[str, tuple[ast.Import | ast.ImportFrom, ast.alias]] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            defined.setdefault(node.name, []).append(node)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for n in ast.walk(target):
                    if isinstance(n, ast.Name):
                        defined.setdefault(n.id, []).append(node)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                imported[local] = (node, alias)
    reached: set[str] = set()
    todo, seen = list(names), set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        if name in imported:
            reached |= app_import_files(root, path, *imported[name])
        for node in defined.get(name, []):
            for n in ast.walk(node):
                if isinstance(n, ast.Name):
                    todo.append(n.id)
                elif isinstance(n, ast.Import | ast.ImportFrom):
                    reached |= app_import_files(root, path, n)
    reached.discard(path)
    return reached


def resolve_web(root: Path, path: Path, spec: str) -> str | None:
    """The web file an import of `path` names, None for a package; fails on a missing file."""
    if spec.startswith("@/"):
        target = root / "web/src" / spec[2:]
    elif spec.startswith("."):
        target = path.parent / spec
    else:
        return None
    for candidate in (
        target,
        target.with_name(target.name + ".ts"),
        target.with_name(target.name + ".tsx"),
        target / "index.ts",
        target / "index.tsx",
    ):
        if candidate.is_file():
            return Path(os.path.normpath(candidate)).relative_to(root).as_posix()
    raise MappingError(f"unresolved TypeScript import {spec}")


def web_requests(root: Path, page: str) -> set[tuple[str, ...]]:
    """The request paths (as segments, a `${...}` part as "*") that a web page and every web
    file it imports hold, links and navigations left out."""
    if not (root / page).is_file():
        raise MappingError(f"the shared web page {page} is missing")
    seen, todo = {page}, [page]
    texts: dict[str, str] = {}
    while todo:
        name = todo.pop()
        texts[name] = (root / name).read_text(encoding="utf-8")
        for spec in WEB_IMPORT.findall(texts[name]):
            if spec.endswith((".css", ".svg", ".png", ".json")):
                continue
            target = resolve_web(root, root / name, spec)
            if (
                target is None
                or target in seen
                or target in WEB_WIRING
                or web_test_file(target)
            ):
                continue
            seen.add(target)
            todo.append(target)
    constants: dict[str, set[str]] = {}
    for text in texts.values():
        for name, _, value in WEB_CONSTANT.findall(text):
            constants.setdefault(name, set()).add(value)
    found: set[tuple[str, ...]] = set()
    for text in texts.values():
        values = [
            m.group(2)
            for m in WEB_STRING.finditer(text)
            if not WEB_LINK.search(text[max(0, m.start() - 40) : m.start()])
        ]
        for m in WEB_LEADING.finditer(text):
            values += [base + m.group(2) for base in constants.get(m.group(1), ())]
        for value in values:
            parts = [
                "*" if "${" in p else p
                for p in re.split(r"[?#]", value, maxsplit=1)[0].split("/")
                if p
            ]
            if parts and parts[0] != "api":
                parts.insert(0, "api")
            if len(parts) > 1 and parts[1] != "*":
                found.add(tuple(parts))
    return found


def api_routes(root: Path, parsed) -> dict[str, list[tuple[str, ...]]]:
    """The route paths each api/app/ file declares (`@router.get("/x")` on an APIRouter of the
    file, its literal prefix in front), as segments: a parameter is "*", a path parameter "**".
    A route whose prefix is not a literal under /api starts with "**" (any prefix)."""
    routes: dict[str, list[tuple[str, ...]]] = {}
    for file in source_files(root / "api/app", (".py",)):
        me = file.relative_to(root).as_posix()
        text = file.read_text(encoding="utf-8")
        if "APIRouter" not in text and not ROUTE_DECORATOR.search(text):
            continue
        tree = parsed(me)
        prefixes: dict[str, str | None] = {}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign | ast.AnnAssign)
                and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "id", getattr(node.value.func, "attr", ""))
                == "APIRouter"
            ):
                prefix: str | None = ""
                for keyword in node.value.keywords:
                    if keyword.arg == "prefix":
                        value = keyword.value
                        prefix = (
                            value.value
                            if isinstance(value, ast.Constant)
                            and isinstance(value.value, str)
                            else None
                        )
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        prefixes[target.id] = prefix
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                func = getattr(decorator, "func", None)
                if not (isinstance(func, ast.Attribute) and func.attr in ROUTE_METHODS):
                    continue
                if not (
                    isinstance(func.value, ast.Name)
                    and func.value.id in prefixes
                    and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)
                    and isinstance(decorator.args[0].value, str)
                ):
                    raise MappingError(f"a route the reader cannot place in {me}")
                prefix = prefixes[func.value.id]
                full = f"{prefix or ''}{decorator.args[0].value}"
                parts = tuple(
                    "**" if p.endswith(":path}") else "*" if p.startswith("{") else p
                    for p in full.split("/")
                    if p
                )
                if prefix is None or parts[:1] != ("api",):
                    parts = ("**", *parts)
                routes.setdefault(me, []).append(parts)
    return routes


def route_matches(path: tuple[str, ...], route: tuple[str, ...]) -> bool:
    """A web request path against an api route, both as segments ("*" matches one segment,
    "**" in a route the rest; a route starting with "**" matches any prefix)."""
    if route[:1] == ("**",) and len(route) > 1:
        return any(route_matches(path[i:], route[1:]) for i in range(len(path) + 1))
    if not route:
        return not path
    if route[0] == "**":
        return True
    if not path:
        return False
    if path[0] != route[0] and "*" not in (path[0], route[0]):
        return False
    return route_matches(path[1:], route[1:])


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
    if path.startswith("api/app/"):
        return api_groups(path, imports)
    units, why = units_for(path)
    if "e2e" not in units:
        return NO_GROUP, why
    for pattern, groups, why in JOURNEY_RULES:
        if matches(pattern, path):
            return groups, why
    parts = path.split("/")
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
        try:
            # --no-renames exposes old and new paths. A deletion can have consumers only
            # in the base, so the head graph cannot justify skipping any group.
            if (
                path.startswith(("web/src/", "api/app/"))
                and not (root / path).is_file()
            ):
                raise MappingError("deleted or missing source path")
            groups, why = journey_groups_for(path, specs, configs, imports)
        except (OSError, UnicodeError, SyntaxError, ValueError, ImportError) as error:
            groups, why = EVERY, f"dependency mapping failed ({type(error).__name__})"
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
    """The sha256 of a change set: raw pathname bytes sorted, each terminated by NUL. ops/ship.sh computes the same
    from a PASS record's base and head, so a record is never taken for another change set."""
    return hashlib.sha256(
        b"".join(path + b"\0" for path in sorted(os.fsencode(p) for p in paths))
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
        check=False,
        env=git_env(),
    )
    if done.returncode != 0:
        print(
            f"ci_plan: cannot read the files of {head} against {base}", file=sys.stderr
        )
        return 1
    paths = [os.fsdecode(name) for name in done.stdout.split(b"\0") if name]
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
