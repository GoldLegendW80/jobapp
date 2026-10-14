"""Tests of e2e_groups.py, on made-up repositories and on this one.

python3 .github/scripts/e2e_groups_test.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_groups

SCRIPT = Path(__file__).resolve().parent / "e2e_groups.py"

WORKFLOW = """\
jobs:
  e2e:
    steps:
      # A comment naming ghost.spec.ts must not count as running it.
      - name: Chain
        run: pnpm exec playwright test -c e2e/chain.config.ts --reporter=list,html
      - name: Apps
        run: >-
          pnpm exec playwright test jobs.spec.ts jobs-map.spec.ts
          agents-runs.spec.ts --workers=1
"""
CONFIG = 'const SPECS = [\n  "identity",\n  "throttle",\n] as const;\n'
ON_DISK = ["identity", "throttle", "jobs", "jobs-map", "agents-runs", "manual"]
EXCLUDED = {"manual.spec.ts": "needs a real account"}


class Repo:
    """A throwaway repository with a workflow, a chained config and spec files."""

    def __init__(
        self, workflow: str = WORKFLOW, specs: list[str] | None = None
    ) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".github/workflows").mkdir(parents=True)
        (self.root / ".github/workflows/ci.yml").write_text(workflow, encoding="utf-8")
        (self.root / "web/e2e").mkdir(parents=True)
        (self.root / "web/e2e/chain.config.ts").write_text(CONFIG, encoding="utf-8")
        for name in ON_DISK if specs is None else specs:
            (self.root / "web/e2e" / f"{name}.spec.ts").write_text("", encoding="utf-8")

    def problems(self, excluded: dict[str, str] | None = None) -> list[str]:
        return e2e_groups.check(self.root, EXCLUDED if excluded is None else excluded)


class CheckTest(unittest.TestCase):
    def repo(self, *args: object, **kwargs: object) -> Repo:
        made = Repo(*args, **kwargs)  # type: ignore[arg-type]
        self.addCleanup(made.tmp.cleanup)
        return made

    def test_every_spec_in_a_group_or_excluded(self) -> None:
        self.assertEqual(self.repo().problems(), [])

    def test_specs_read_from_commands_and_from_the_chained_config(self) -> None:
        self.assertEqual(
            sorted(e2e_groups.specs_in_ci(self.repo().root)),
            [
                "agents-runs.spec.ts",
                "identity.spec.ts",
                "jobs-map.spec.ts",
                "jobs.spec.ts",
                "throttle.spec.ts",
            ],
        )

    def test_new_spec_in_no_group_fails(self) -> None:
        problems = self.repo(specs=[*ON_DISK, "brand-new"]).problems()
        self.assertEqual(len(problems), 1)
        self.assertIn("web/e2e/brand-new.spec.ts is in no e2e group", problems[0])

    def test_spec_named_only_in_a_comment_is_not_run(self) -> None:
        problems = self.repo(specs=[*ON_DISK, "ghost"]).problems()
        self.assertEqual(len(problems), 1)
        self.assertIn("ghost.spec.ts is in no e2e group", problems[0])

    def test_spec_whose_name_ends_like_another_is_not_mistaken_for_it(self) -> None:
        # "jobs.spec.ts" in the workflow must not cover "my-jobs.spec.ts", nor the reverse.
        problems = self.repo(specs=[*ON_DISK, "my-jobs"]).problems()
        self.assertEqual(len(problems), 1)
        self.assertIn("my-jobs.spec.ts is in no e2e group", problems[0])

    def test_run_and_excluded_at_once_fails(self) -> None:
        problems = self.repo().problems({**EXCLUDED, "jobs.spec.ts": "why not"})
        self.assertEqual(len(problems), 1)
        self.assertIn("jobs.spec.ts is run by", problems[0])

    def test_run_twice_fails(self) -> None:
        twice = WORKFLOW + "      - run: pnpm exec playwright test identity.spec.ts\n"
        problems = self.repo(workflow=twice).problems()
        self.assertEqual(len(problems), 1)
        self.assertIn("identity.spec.ts is run 2 times", problems[0])

    def test_group_naming_a_deleted_spec_fails(self) -> None:
        problems = self.repo(specs=[n for n in ON_DISK if n != "jobs-map"]).problems()
        self.assertEqual(len(problems), 1)
        self.assertIn("runs jobs-map.spec.ts, which is not in", problems[0])

    def test_exclusion_of_a_deleted_spec_fails(self) -> None:
        problems = self.repo(specs=[n for n in ON_DISK if n != "manual"]).problems()
        self.assertEqual(len(problems), 1)
        self.assertIn("NOT_IN_CI lists manual.spec.ts", problems[0])

    def test_workflow_naming_no_spec_fails(self) -> None:
        problems = self.repo(workflow="jobs: {}\n", specs=["manual"]).problems()
        self.assertEqual(len(problems), 1)
        self.assertIn("names no spec at all", problems[0])

    def test_empty_e2e_directory_reports_the_stale_names(self) -> None:
        problems = self.repo(specs=[]).problems({})
        self.assertEqual(len(problems), 5)
        self.assertTrue(all("which is not in" in p for p in problems))

    def test_config_without_specs_array_is_an_error(self) -> None:
        repo = self.repo()
        (repo.root / "web/e2e/chain.config.ts").write_text("export default {};\n")
        with self.assertRaises(ValueError):
            repo.problems()

    def prefixed(self, specs: list[str]) -> Repo:
        """A repository whose workflow also runs a config that takes every module-*.spec.ts."""
        workflow = (
            WORKFLOW
            + "      - run: pnpm exec playwright test -c e2e/module-all.config.ts\n"
        )
        repo = self.repo(workflow=workflow, specs=[*ON_DISK, *specs])
        (repo.root / "web/e2e/module-all.config.ts").write_text(
            'const SPEC_PREFIX = "module-";\nexport default {};\n', encoding="utf-8"
        )
        return repo

    def test_a_config_with_a_prefix_covers_every_spec_named_with_it(self) -> None:
        repo = self.prefixed(["module-a", "module-b2"])
        self.assertEqual(repo.problems(), [])
        self.assertEqual(
            [name for name in e2e_groups.specs_in_ci(repo.root) if "module" in name],
            ["module-a.spec.ts", "module-b2.spec.ts"],
        )

    def test_a_spec_added_under_the_prefix_needs_no_change_to_the_workflow(
        self,
    ) -> None:
        repo = self.prefixed(["module-a"])
        (repo.root / "web/e2e/module-new-task.spec.ts").write_text("", encoding="utf-8")
        self.assertEqual(repo.problems(), [])

    def test_a_prefix_covers_nothing_else(self) -> None:
        # "module-" covers neither "my-module-a" nor "modules", and a config file is no spec.
        problems = self.prefixed(["module-a", "my-module-a", "modules"]).problems()
        self.assertEqual(len(problems), 2)
        self.assertIn("modules.spec.ts is in no e2e group", problems[0])
        self.assertIn("my-module-a.spec.ts is in no e2e group", problems[1])

    def test_a_prefixed_spec_also_named_in_a_command_runs_twice(self) -> None:
        repo = self.prefixed(["module-a"])
        workflow = repo.root / ".github/workflows/ci.yml"
        workflow.write_text(
            workflow.read_text(encoding="utf-8")
            + "      - run: pnpm exec playwright test module-a.spec.ts\n",
            encoding="utf-8",
        )
        problems = repo.problems()
        self.assertEqual(len(problems), 1)
        self.assertIn("module-a.spec.ts is run 2 times", problems[0])

    def test_a_prefix_with_no_spec_covers_nothing(self) -> None:
        self.assertEqual(self.prefixed([]).problems(), [])

    def test_command_line_exit_status_and_message(self) -> None:
        good = self.repo(specs=[n for n in ON_DISK if n != "manual"])
        # The real NOT_IN_CI applies on the command line: its entries have no file here.
        ran = subprocess.run(
            [sys.executable, str(SCRIPT), str(good.root)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ran.returncode, 1)
        self.assertIn("NOT_IN_CI lists caddy.spec.ts", ran.stderr)
        for name in e2e_groups.NOT_IN_CI:
            (good.root / "web/e2e" / name).write_text("", encoding="utf-8")
        ran = subprocess.run(
            [sys.executable, str(SCRIPT), str(good.root)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("5 specs run in CI, 4 excluded", ran.stdout)


class LocalCheckGroupTest(unittest.TestCase):
    """A journey group that only ops/local-check.sh runs (the mail module's)."""

    CONFIG = "e2e/mail-all.config.ts"

    def repo(self, script: str | None, specs: list[str]) -> Repo:
        made = Repo(specs=[*ON_DISK, *specs])
        self.addCleanup(made.tmp.cleanup)
        (made.root / "web" / self.CONFIG).write_text(
            'const SPEC_PREFIX = "mail-";\n', encoding="utf-8"
        )
        if script is not None:
            (made.root / "ops").mkdir()
            (made.root / "ops/local-check.sh").write_text(script, encoding="utf-8")
        return made

    def test_a_spec_of_a_group_the_local_check_names_is_accounted_for(self) -> None:
        repo = self.repo(
            '  mail) echo "-c e2e/mail-all.config.ts" ;;\n', ["mail-m1", "mail-m4"]
        )
        self.assertEqual(repo.problems(), [])
        self.assertEqual(
            e2e_groups.specs_in_local_check(repo.root),
            ["mail-m1.spec.ts", "mail-m4.spec.ts"],
        )

    def test_without_the_line_in_the_local_check_the_spec_is_in_no_group(self) -> None:
        for script in (None, "", '# mail) echo "-c e2e/mail-all.config.ts" ;;\n'):
            problems = self.repo(script, ["mail-m1"]).problems()
            self.assertEqual(len(problems), 1, script)
            self.assertIn("mail-m1.spec.ts is in no e2e group", problems[0])

    def test_a_config_that_is_not_listed_accounts_for_nothing(self) -> None:
        repo = self.repo('  other) echo "-c e2e/other-all.config.ts" ;;\n', ["other-a"])
        (repo.root / "web/e2e/other-all.config.ts").write_text(
            'const SPEC_PREFIX = "other-";\n', encoding="utf-8"
        )
        self.assertEqual(len(repo.problems()), 1)

    def test_a_spec_run_by_both_or_also_excluded_is_a_problem(self) -> None:
        repo = self.repo('  mail) echo "-c e2e/mail-all.config.ts" ;;\n', ["mail-m1"])
        excluded = {**EXCLUDED, "mail-m1.spec.ts": "why"}
        self.assertTrue(
            any("pick one" in problem for problem in repo.problems(excluded))
        )
        workflow = (
            WORKFLOW
            + "      - name: Mail\n        run: pnpm exec playwright test mail-m1.spec.ts\n"
        )
        both = Repo(workflow=workflow, specs=[*ON_DISK, "mail-m1"])
        self.addCleanup(both.tmp.cleanup)
        (both.root / "web" / self.CONFIG).write_text(
            'const SPEC_PREFIX = "mail-";\n', encoding="utf-8"
        )
        (both.root / "ops").mkdir()
        (both.root / "ops/local-check.sh").write_text(
            'mail) echo "-c e2e/mail-all.config.ts" ;;\n', encoding="utf-8"
        )
        self.assertTrue(any("pick one" in problem for problem in both.problems()))


class ThisRepositoryTest(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[2]

    def test_every_local_check_config_exists_is_named_and_has_a_reason(self) -> None:
        script = e2e_groups.uncommented(
            (self.ROOT / "ops/local-check.sh").read_text(encoding="utf-8")
        )
        for config, reason in e2e_groups.LOCAL_CHECK_CONFIGS.items():
            self.assertTrue((self.ROOT / "web" / config).is_file(), config)
            self.assertIn(f"-c {config}", script)
            self.assertTrue(reason.strip(), config)
        # The local check's own groups: mail (mail-*.spec.ts) and apply (apply-*.spec.ts).
        local = set(e2e_groups.specs_in_local_check(self.ROOT))
        self.assertLessEqual(
            {"mail-m1.spec.ts", "apply-a1.spec.ts", "apply-a3.spec.ts"}, local
        )
        on_disk = {
            p.name
            for prefix in ("mail-", "apply-")
            for p in (self.ROOT / "web/e2e").glob(f"{prefix}*.spec.ts")
        }
        self.assertEqual(local, on_disk)
        self.assertFalse(local & set(e2e_groups.specs_in_ci(self.ROOT)))

    def test_the_real_workflow_accounts_for_every_spec(self) -> None:
        self.assertEqual(e2e_groups.check(self.ROOT), [])

    def test_the_real_groups_are_the_known_ones(self) -> None:
        # The identity chain (10: profile-dates.spec.ts joined it with issue #48; apply-a3.spec.ts
        # left it for the apply group of the local check with A1), the sweep (1) and the apps
        # group (6: agents-streaming.spec.ts joined it with issue #33), each named one by one; and the ingestion group, which takes
        # every ingestion-*.spec.ts through its config, so its count grows with the module and is
        # not written here.
        ran = set(e2e_groups.specs_in_ci(self.ROOT))
        ingestion = {name for name in ran if name.startswith("ingestion-")}
        self.assertEqual(len(ran - ingestion), 17)
        self.assertIn("ingestion-g1.spec.ts", ingestion)
        on_disk = {p.name for p in (self.ROOT / "web/e2e").glob("ingestion-*.spec.ts")}
        self.assertEqual(ingestion, on_disk)

    def test_every_exclusion_has_a_reason(self) -> None:
        for name, reason in e2e_groups.NOT_IN_CI.items():
            self.assertTrue(reason.strip(), name)


if __name__ == "__main__":
    unittest.main()
