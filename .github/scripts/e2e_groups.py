"""Guard: every Playwright spec of web/e2e is run by CI or excluded on purpose.

    python3 .github/scripts/e2e_groups.py [repo root]

The e2e groups of .github/workflows/ci.yml name their spec files one by one (four groups, in
the jobs checks and journeys), so a spec added later would run nowhere without anyone noticing.
This check reads the workflow and fails when a web/e2e/*.spec.ts file is neither

- named in a `playwright test` command of the workflow, or covered by a Playwright config the
  workflow runs with `-c`: listed in its SPECS (web/e2e/identity-all.config.ts), or named with
  its SPEC_PREFIX (web/e2e/ingestion-all.config.ts runs every ingestion-*.spec.ts, so a task of
  that module adds a spec without touching the workflow), nor
- covered by a journey group that ops/local-check.sh finds on its own and the workflow does
  not run: every web/e2e/<group>-all.config.ts is one (`journey_groups` of the script, issue
  #279), so a module adds its group by adding that file, with no list to edit, nor
- listed in NOT_IN_CI below, with the reason it is not run there.

It also fails on a spec the workflow runs twice, on a spec that is both run and excluded, on a
name (run or excluded) that has no file any more, on a group config that runs no spec, and on a
group config whose name the local check cannot use as a group (apps and sweep are taken, and a
name is lower case letters, digits and hyphens). Exit status 0 when all is in order, 1 otherwise.
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

# The journey groups of ops/local-check.sh are found, not listed: every web/e2e/<group>-all.config.ts
# is the group <group> (`journey_groups` of the script). There is no CI run: the local check
# gates a deploy. The configs among them that the workflow does not run (mail: the fake Google
# of tests/gmail-stub.compose.yml beside the stack; apply: the fake ATS forms server; the groups
# added since) account for their specs here while ops/local-check.sh exists.
GROUP_CONFIG = "-all.config.ts"
GROUP_NAME = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
# The groups of ops/local-check.sh that name their specs one by one: no config may take them.
NAMED_GROUPS = ("apps", "sweep")

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


def group_configs(root: Path) -> list[str]:
    """Every journey group config the local check finds, as `e2e/<group>-all.config.ts`, sorted."""
    return sorted(
        f"e2e/{path.name}"
        for path in (root / E2E_DIR).glob(f"*{GROUP_CONFIG}")
        if path.is_file()
    )


def local_check_configs(root: Path) -> list[str]:
    """The group configs that the local check runs and the workflow does not."""
    if not (root / LOCAL_CHECK).is_file():
        return []
    text = uncommented((root / WORKFLOW).read_text(encoding="utf-8"))
    in_workflow = set(CONFIG.findall(text))
    return [config for config in group_configs(root) if config not in in_workflow]


def specs_in_local_check(root: Path) -> list[str]:
    """The specs of the group configs that the local check runs and the workflow does not."""
    ran: list[str] = []
    for config in local_check_configs(root):
        ran += config_specs(root / "web" / config)
    return ran


def group_problems(root: Path) -> list[str]:
    """The group configs that ops/local-check.sh, when there is one, cannot run as a group."""
    problems: list[str] = []
    if not (root / LOCAL_CHECK).is_file():
        return problems
    for config in group_configs(root):
        group = config.removeprefix("e2e/").removesuffix(GROUP_CONFIG)
        if not GROUP_NAME.match(group) or group in NAMED_GROUPS:
            problems.append(
                f"web/{config} cannot be a journey group of {LOCAL_CHECK}: rename it "
                f"(lower case letters, digits, hyphens; not {' or '.join(NAMED_GROUPS)})"
            )
        elif not config_specs(root / "web" / config):
            problems.append(f"web/{config} runs no spec: Playwright would find no test")
    return problems


def check(root: Path, not_in_ci: dict[str, str] | None = None) -> list[str]:
    """The problems found, one sentence each; empty when every spec is accounted for."""
    excluded = NOT_IN_CI if not_in_ci is None else not_in_ci
    on_disk = {path.name for path in (root / E2E_DIR).glob("*.spec.ts")}
    ran = Counter(specs_in_ci(root))
    local = set(specs_in_local_check(root))
    problems = group_problems(root)
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
