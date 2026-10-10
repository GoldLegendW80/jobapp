"""Guard: every Playwright spec of web/e2e is run by CI or excluded on purpose.

    python3 .github/scripts/e2e_groups.py [repo root]

The e2e job of .github/workflows/ci.yml names its spec files one by one (three groups on three
runners), so a spec added later would run nowhere without anyone noticing. This check reads the
workflow and fails when a web/e2e/*.spec.ts file is neither

- named in a `playwright test` command of the workflow, or listed in the SPECS of a Playwright
  config the workflow runs with `-c` (web/e2e/identity-all.config.ts), nor
- listed in NOT_IN_CI below, with the reason it is not run there.

It also fails on a spec the workflow runs twice, on a spec that is both run and excluded, and on a
name (run or excluded) that has no file any more. Exit status 0 when all is in order, 1 otherwise.
Run by the shell job of CI and by pre-commit; tested by e2e_groups_test.py.
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

WORKFLOW = Path(".github/workflows/ci.yml")
E2E_DIR = Path("web/e2e")
SPEC = re.compile(
    r"(?<![\w-])([\w-]+\.spec\.ts)\b"
)  # the file name, with or without a folder
CONFIG = re.compile(r"-c\s+(e2e/[\w./-]+\.config\.ts)\b")
SPECS_ARRAY = re.compile(r"\bSPECS\s*=\s*\[(.*?)\]", re.DOTALL)


def uncommented(text: str) -> str:
    """The workflow without its comment lines (they name specs too, as prose)."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def config_specs(config: Path) -> list[str]:
    """The spec files of a chained config: the names of its SPECS array, plus .spec.ts."""
    found = SPECS_ARRAY.search(config.read_text(encoding="utf-8"))
    if found is None:
        raise ValueError(f"{config}: no SPECS array found")
    return [
        f"{name}.spec.ts" for name in re.findall(r"[\"']([\w-]+)[\"']", found.group(1))
    ]


def specs_in_ci(root: Path) -> list[str]:
    """Every spec the workflow runs, once per time it is run."""
    text = uncommented((root / WORKFLOW).read_text(encoding="utf-8"))
    ran = SPEC.findall(text)
    for config in CONFIG.findall(text):
        ran += config_specs(root / "web" / config)
    return ran


def check(root: Path, not_in_ci: dict[str, str] | None = None) -> list[str]:
    """The problems found, one sentence each; empty when every spec is accounted for."""
    excluded = NOT_IN_CI if not_in_ci is None else not_in_ci
    on_disk = {path.name for path in (root / E2E_DIR).glob("*.spec.ts")}
    ran = Counter(specs_in_ci(root))
    problems: list[str] = []
    if not ran:
        problems.append(
            f"{WORKFLOW} names no spec at all: this check no longer reads it right"
        )
    for name in sorted(on_disk - set(ran) - set(excluded)):
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
    print(
        f"e2e_groups: {len(ran)} specs run in CI, {len(NOT_IN_CI)} excluded with a reason"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
