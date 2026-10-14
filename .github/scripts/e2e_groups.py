"""Guard: every Playwright spec of web/e2e is run by CI or excluded on purpose.

    python3 .github/scripts/e2e_groups.py [repo root]

The e2e groups of .github/workflows/ci.yml name their spec files one by one (four groups, in
the jobs checks and journeys), so a spec added later would run nowhere without anyone noticing.
This check reads the workflow and fails when a web/e2e/*.spec.ts file is neither

- named in a `playwright test` command of the workflow, or covered by a Playwright config the
  workflow runs with `-c`: listed in its SPECS (web/e2e/identity-all.config.ts), or named with
  its SPEC_PREFIX (web/e2e/ingestion-all.config.ts runs every ingestion-*.spec.ts, so a task of
  that module adds a spec without touching the workflow), nor
- covered by a config of LOCAL_CHECK_CONFIGS below that ops/local-check.sh names (a journey
  group the local check runs and the workflow does not: web/e2e/mail-all.config.ts), nor
- listed in NOT_IN_CI below, with the reason it is not run there.

It also fails on a spec the workflow runs twice, on a spec that is both run and excluded, and on a
name (run or excluded) that has no file any more. Exit status 0 when all is in order, 1 otherwise.
Run by the shell unit of CI (job checks) and by pre-commit; tested by e2e_groups_test.py.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

# Specs CI does not run, each with its reason. Adding a spec here is a decision, not a default:
# a journey that can run against the scratch stack belongs in a group of the e2e job.
NOT_IN_CI: dict[str, str] = {
    # Stops and starts the db service of the stack it is pointed at, so it cannot share a stack
    # with the other journeys (README: never beside identity.spec.ts). Run by hand against a
    # local stack or the Jetson.
    # Browser B3: needs the stack with the compose profile `browser`, which no group starts yet.
    "browser-b3.spec.ts": "needs the browser profile of the stack, no group starts it yet",
    "caddy.spec.ts": "stops the database of the stack under test",
    # The task 5 health card with /api mocked by route interception, against `vite preview`
    # (web/playwright.config.ts), not the Compose stack. It was never part of CI: putting it in
    # a group means checking first that it still passes.
    "health.spec.ts": "mocked /api on vite preview, predates the stack journeys",
    # Its default mode converts and imports Harald's real profile and job folder; its synthetic
    # mode is the Jetson run of tests/e2e_jobs_jetson.sh after a deploy. Neither is a CI input.
    "jobs-edit.spec.ts": "real data by default, synthetic mode runs on the Jetson",
    # Opt in with E2E_REAL_API against an api started by hand, with its database up or stopped.
    "real-api.spec.ts": "needs an api and database started by hand (E2E_REAL_API)",
}

# Playwright configs that the workflow does not run and the local check does, as a journey group
# of its own (ops/local-check.sh, `journey_specs`), each with its reason. There is no CI run:
# the local check gates a deploy. A config listed here accounts for the specs under its
# SPEC_PREFIX only while ops/local-check.sh really names it with `-c`.
LOCAL_CHECK_CONFIGS: dict[str, str] = {
    # The mail journeys need the fake Google of tests/gmail-stub.compose.yml beside the stack
    # (no journey may reach a real Google account), which the workflow's stacks do not start.
    "e2e/mail-all.config.ts": "needs the fake Google, group mail of ops/local-check.sh",
    # The apply journeys need the fake ATS forms server of tests/ats-forms-stub.compose.yml
    # beside the stack (no journey may fill a real job site). apply-a3 ran in the identity group
    # until A1 made this group.
    "e2e/apply-all.config.ts": "needs the fake ATS forms server, group apply of ops/local-check.sh",
}

WORKFLOW = Path(".github/workflows/ci.yml")
LOCAL_CHECK = Path("ops/local-check.sh")
E2E_DIR = Path("web/e2e")
SPEC = re.compile(
    r"(?<![\w-])([\w-]+\.spec\.ts)\b"
)  # the file name, with or without a folder
CONFIG = re.compile(r"-c\s+(e2e/[\w./-]+\.config\.ts)\b")
SPECS_ARRAY = re.compile(r"\bSPECS\s*=\s*\[(.*?)\]", re.DOTALL)
SPEC_PREFIX = re.compile(r"""\bSPEC_PREFIX\s*=\s*["']([\w-]+)["']""")


def uncommented(text: str) -> str:
    """The workflow without its comment lines (they name specs too, as prose)."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def config_specs(config: Path) -> list[str]:
    """The spec files a config runs: the names of its SPECS array plus .spec.ts, or, for a
    config that takes every spec of one module, the files next to it whose name starts with its
    SPEC_PREFIX."""
    text = config.read_text(encoding="utf-8")
    found = SPECS_ARRAY.search(text)
    if found is not None:
        return [
            f"{name}.spec.ts"
            for name in re.findall(r"[\"']([\w-]+)[\"']", found.group(1))
        ]
    prefix = SPEC_PREFIX.search(text)
    if prefix is not None:
        return sorted(
            path.name for path in config.parent.glob(f"{prefix.group(1)}*.spec.ts")
        )
    raise ValueError(f"{config}: neither a SPECS array nor a SPEC_PREFIX found")


def specs_in_ci(root: Path) -> list[str]:
    """Every spec the workflow runs, once per time it is run."""
    text = uncommented((root / WORKFLOW).read_text(encoding="utf-8"))
    ran = SPEC.findall(text)
    for config in CONFIG.findall(text):
        ran += config_specs(root / "web" / config)
    return ran


def specs_in_local_check(root: Path) -> list[str]:
    """The specs of the configs of LOCAL_CHECK_CONFIGS that ops/local-check.sh names."""
    script = root / LOCAL_CHECK
    if not script.is_file():
        return []
    text = uncommented(script.read_text(encoding="utf-8"))
    ran: list[str] = []
    for config in CONFIG.findall(text):
        if config in LOCAL_CHECK_CONFIGS and (root / "web" / config).is_file():
            ran += config_specs(root / "web" / config)
    return ran


def check(root: Path, not_in_ci: dict[str, str] | None = None) -> list[str]:
    """The problems found, one sentence each; empty when every spec is accounted for."""
    excluded = NOT_IN_CI if not_in_ci is None else not_in_ci
    on_disk = {path.name for path in (root / E2E_DIR).glob("*.spec.ts")}
    ran = Counter(specs_in_ci(root))
    local = set(specs_in_local_check(root))
    problems: list[str] = []
    for name in sorted(set(ran) & local):
        problems.append(
            f"{name} is run by {WORKFLOW} and by a group of {LOCAL_CHECK} that the "
            "workflow does not have: pick one"
        )
    for name in sorted(set(excluded) & local):
        problems.append(
            f"{name} is run by a group of {LOCAL_CHECK} and also listed in NOT_IN_CI: pick one"
        )
    if not ran:
        problems.append(
            f"{WORKFLOW} names no spec at all: this check no longer reads it right"
        )
    for name in sorted(on_disk - set(ran) - set(excluded) - local):
        problems.append(
            f"{E2E_DIR / name} is in no e2e group of {WORKFLOW}: add it to a group, or to "
            "NOT_IN_CI in .github/scripts/e2e_groups.py with the reason"
        )
    for name in sorted(set(ran) & set(excluded)):
        problems.append(
            f"{name} is run by {WORKFLOW} and also listed in NOT_IN_CI: pick one"
        )
    for name, times in sorted(ran.items()):
        if times > 1:
            problems.append(
                f"{name} is run {times} times by {WORKFLOW}: once is enough"
            )
    for name in sorted(set(ran) - on_disk):
        problems.append(f"{WORKFLOW} runs {name}, which is not in {E2E_DIR}")
    for name in sorted(set(excluded) - on_disk):
        problems.append(
            f"NOT_IN_CI lists {name}, which is not in {E2E_DIR}: remove the entry"
        )
    return problems


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[2]
    problems = check(root)
    for problem in problems:
        print(f"e2e_groups: {problem}", file=sys.stderr)
    if problems:
        return 1
    ran = set(specs_in_ci(root))
    local = set(specs_in_local_check(root))
    print(
        f"e2e_groups: {len(ran)} specs run in CI, {len(NOT_IN_CI)} excluded with a reason, "
        f"{len(local)} run by a group of the local check only"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
