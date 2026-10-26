"""Tests of ci_plan.py: the path rules, the plan of a run, `verify`, and this repository.

    python3 .github/scripts/ci_plan_test.py

Three kinds of checks:
- the rules on made-up paths and on throwaway git repositories (a pull request merge commit, a
  push, a shallow checkout);
- the reasons that let a rule skip a unit, checked on this repository: no api file reads a file
  of web/, and nothing executable reads the documentation the rules call documentation;
- .github/workflows/ci.yml itself: every job computes the plan first and verifies it last, every
  step named in LANES exists and runs under its unit, and no job can be skipped (none has a
  condition or waits for another), which is what lets the run's own result stand for all.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ci_plan

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve().parent / "ci_plan.py"
WORKFLOW = ROOT / ".github/workflows/ci.yml"
EVERYTHING = frozenset(ci_plan.UNITS)


# --- a small reader of ci.yml (no YAML library on a bare python3) ------------------------------


def read_jobs(text: str) -> dict[str, dict[str, object]]:
    """job id -> {"keys": {key: value}, "steps": [{key: value}]} for the workflow's jobs, read by
    indentation: jobs at 2 spaces under `jobs:`, their keys at 4, steps at 6, step keys at 8.
    A value continued on deeper lines (`>-`, `|`, a mapping) is joined into one string."""
    jobs: dict[str, dict[str, object]] = {}
    in_jobs = False
    job: dict[str, object] | None = None
    step: dict[str, str] | None = None
    target: dict[str, str] | None = None
    key = ""
    indent = 0
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        depth = len(raw) - len(raw.lstrip())
        line = raw.strip()
        if depth == 0:
            in_jobs = line == "jobs:"
            continue
        if not in_jobs:
            continue
        if depth == 2 and line.endswith(":"):
            job = {"keys": {}, "steps": []}
            jobs[line[:-1]] = job
            step = target = None
            continue
        if job is None:
            continue
        if depth == 4:
            step = None
            name, _, value = line.partition(":")
            target, key, indent = job["keys"], name, depth  # type: ignore[assignment]
            target[key] = value.strip()
            continue
        if depth == 6 and line.startswith("- "):
            step = {}
            job["steps"].append(step)  # type: ignore[union-attr]
            name, _, value = line[2:].partition(":")
            target, key, indent = step, name, depth + 2
            target[key] = value.strip()
            continue
        if step is not None and depth == 8 and re.match(r"[\w-]+:", line):
            name, _, value = line.partition(":")
            target, key, indent = step, name, depth
            target[key] = value.strip()
            continue
        if target is not None and depth > indent:
            target[key] = (target[key] + " " + line).strip()
    return jobs


def steps_by_id(job: dict[str, object]) -> dict[str, dict[str, str]]:
    return {step["id"]: step for step in job["steps"] if "id" in step}  # type: ignore[union-attr]


def chain_follows(step_id: str, unit: str, steps: dict[str, dict[str, str]]) -> bool:
    """Does this step run only when the plan has `unit`? Its condition names that unit's output
    and no other, or the success of earlier steps that all do in turn (`A || B` on two steps,
    the two ways of building the images, needs both)."""
    seen: set[str] = set()
    todo = [step_id]
    while todo:
        current = todo.pop()
        if current not in steps:
            return False
        if current in seen:
            continue
        seen.add(current)
        condition = steps[current].get("if", "")
        direct = f"steps.plan.outputs.{unit} == 'true'" in condition
        others = re.findall(r"steps\.(\w+)\.outcome == 'success'", condition)
        if direct and "steps.plan.outputs." in condition:
            named = set(re.findall(r"steps\.plan\.outputs\.(\w+) == 'true'", condition))
            if named == {unit}:
                continue  # this branch ends on the unit's own output
            return False
        if not others:
            return False
        todo.extend(others)  # every step it may depend on must itself follow the unit
    return True


class RulesTest(unittest.TestCase):
    def units(self, *paths: str) -> frozenset[str]:
        return ci_plan.plan_for(list(paths))[0]

    def test_documentation_alone_needs_no_unit(self) -> None:
        docs = [
            "CONTRIBUTING.md",
            "CAPABILITY-MAP.md",
            "SPEC-platform.md",
            "SPEC-agents.md",
            "docs/HANDOFF.md",
            "docs/STATUS.md",
            "docs/agents/implementer.md",
            "CLAUDE.md",
            ".claude/settings.json",
            ".claude/agents/reviewer.md",
            ".claude/commands/ship.md",
            "tasks/todo.md",
            "tasks/agents/todo.md",
            "tasks/evidence/platform-91/before.png",
            "tasks/evidence/task5/health.png",
            ".github/pull_request_template.md",
        ]
        for path in docs:
            self.assertEqual(self.units(path), frozenset(), path)
        self.assertEqual(self.units(*docs), frozenset())

    def test_a_path_no_rule_names_runs_everything(self) -> None:
        for path in [
            "api/app/main.py",
            "api/tests/test_health.py",
            "api/uv.lock",
            "compose.yml",
            "compose.mac.yml",
            "deploy.sh",
            "ops/ci-runners.sh",
            "tests/deploy_test.sh",
            "caddy/Caddyfile",
            ".github/workflows/ci.yml",
            ".github/workflows/deploy.yml",
            ".github/scripts/ci_plan.py",
            ".pre-commit-config.yaml",
            ".env.example",
            ".gitignore",
            "tailscale/serve.json",
            "a-new-folder/anything.txt",
            "NEW-FILE-AT-THE-ROOT",
        ]:
            self.assertEqual(self.units(path), EVERYTHING, path)

    def test_documentation_look_alikes_run_everything(self) -> None:
        # Markdown is documentation only where a rule says so: elsewhere it may be test data.
        for path in [
            "api/README.md",
            "api/tests/profile/fixtures/facts.md",
            "ops/README.md",
            "SPEC-x/inside-a-folder.md",
            "notes.md",
            "docs.md",
            "api/CLAUDE.md",
            ".claude.json",
            "ops/.claude/settings.json",
            "tasksfile",
            ".github/workflows/README.md",
        ]:
            self.assertEqual(self.units(path), EVERYTHING, path)

    def test_the_web_app_alone_skips_the_api_unit_only(self) -> None:
        expected = frozenset({"web", "shell", "e2e"})
        for path in [
            "web/src/App.tsx",
            "web/e2e/jobs.spec.ts",
            "web/package.json",
            "web/pnpm-lock.yaml",
            "web/Dockerfile",
            "web/README.md",
        ]:
            self.assertEqual(self.units(path), expected, path)
        self.assertEqual(self.units("web/src/App.tsx", "README.md"), expected)

    def test_a_runbook_runs_the_shell_unit_alone(self) -> None:
        # tests/deploy_test.sh and tests/migrate_test.sh read the runbooks.
        self.assertEqual(self.units("docs/runbooks/accounts.md"), frozenset({"shell"}))
        self.assertEqual(
            self.units("docs/runbooks/continuous-delivery.md", "SPEC-platform.md"),
            frozenset({"shell"}),
        )
        self.assertEqual(self.units("docs/STATUS.md"), frozenset())

    def test_the_readme_runs_the_shell_unit_alone(self) -> None:
        # tests/deploy_test.sh scans the README for a stale sentence.
        self.assertEqual(self.units("README.md"), frozenset({"shell"}))
        self.assertEqual(
            self.units("README.md", "SPEC-platform.md"), frozenset({"shell"})
        )

    def test_the_evidence_a_shell_test_reads_runs_the_shell_unit(self) -> None:
        self.assertEqual(
            self.units("tasks/evidence/agents-a5/run.json"), frozenset({"shell"})
        )
        self.assertEqual(
            self.units("tasks/evidence/agents-a5/run.json", "web/src/App.tsx"),
            frozenset({"web", "shell", "e2e"}),
        )

    def test_the_plan_and_the_evidence_an_api_test_reads_run_the_api_unit(self) -> None:
        """api/tests/ingestion/test_citation_examples.py reads both: the table of citation
        examples printed in the plan, and the verdicts of the script that first ran them."""
        for path in (
            "tasks/ingestion/todo.md",
            "tasks/evidence/ingestion-g0/citation_rules.txt",
        ):
            self.assertEqual(self.units(path), frozenset({"api"}), path)
        self.assertEqual(
            self.units("tasks/ingestion/todo.md", "SPEC-ingestion.md", "tasks/todo.md"),
            frozenset({"api"}),
        )
        # the other files of that evidence folder, and the other plans, are read by nothing
        self.assertEqual(
            self.units(
                "tasks/evidence/ingestion-g0/citation_rules.py", "tasks/agents/todo.md"
            ),
            frozenset(),
        )

    def test_one_unknown_path_among_documentation_runs_everything(self) -> None:
        self.assertEqual(
            self.units("README.md", "tasks/todo.md", "api/app/main.py"), EVERYTHING
        )
        self.assertEqual(self.units("web/src/App.tsx", "compose.yml"), EVERYTHING)

    def test_every_rule_names_known_units_and_a_reason(self) -> None:
        for pattern, units, why in ci_plan.RULES:
            self.assertTrue(units <= EVERYTHING, pattern)
            self.assertNotEqual(
                units, EVERYTHING, f"{pattern}: a rule for everything is no rule"
            )
            self.assertTrue(why.strip(), pattern)

    def test_encode_is_stable(self) -> None:
        self.assertEqual(ci_plan.encode(EVERYTHING), "api,web,shell,e2e")
        self.assertEqual(ci_plan.encode(frozenset({"e2e", "web"})), "web,e2e")
        self.assertEqual(ci_plan.encode(frozenset()), "none")


# Every git and every script these tests start gets this environment, never os.environ: run as a
# pre-commit hook, the tests inherit GIT_DIR and GIT_INDEX_FILE of the commit in progress, and a
# `git add` in a throwaway folder would then rewrite the index of the real repository (it did,
# once, on 2026-10-10: every file staged as deleted).
CLEAN_ENV = ci_plan.git_env()


def git(root: Path, *args: str) -> str:
    """git in a throwaway repository: no hook, no signing, a fixed author."""
    env = {
        **CLEAN_ENV,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.org",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.org",
    }
    done = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {done.stdout} {done.stderr}")
    return done.stdout.strip()


class DecideTest(unittest.TestCase):
    """On throwaway repositories: main with one commit, a branch, and the merge commit GitHub
    checks a pull request out at."""

    def repo(
        self, changes: dict[str, str | None], renames: dict[str, str] | None = None
    ) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        git(root, "init", "-q", "-b", "main")
        for name in [
            "README.md",
            "api/app/main.py",
            "web/src/App.tsx",
            "tasks/todo.md",
        ]:
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(f"{name}\n" * 30, encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "base")
        git(root, "checkout", "-q", "-b", "feature")
        for name, content in changes.items():
            if content is None:
                (root / name).unlink()
            else:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(content, encoding="utf-8")
        for old, new in (renames or {}).items():
            (root / new).parent.mkdir(parents=True, exist_ok=True)
            git(root, "mv", old, new)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "change")
        # main moves on too: the merge commit must be judged against main, not against the fork.
        git(root, "checkout", "-q", "main")
        (root / "ops").mkdir()
        (root / "ops/other.sh").write_text("other\n", encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "main moved")
        git(root, "merge", "-q", "--no-ff", "-m", "merge", "feature")
        return root

    def test_a_documentation_pull_request_runs_pre_commit_alone(self) -> None:
        root = self.repo({"tasks/todo.md": "new\n", "tasks/evidence/x/shot.png": "png"})
        units, sentence, _ = ci_plan.decide("pull_request", "main", root)
        self.assertEqual(units, frozenset())
        self.assertIn("documentation only", sentence)

    def test_a_readme_pull_request_runs_the_shell_unit(self) -> None:
        root = self.repo({"README.md": "new\n", "tasks/todo.md": "new\n"})
        units, sentence, notes = ci_plan.decide("pull_request", "main", root)
        self.assertEqual(units, frozenset({"shell"}))
        self.assertIn("shell", sentence)
        self.assertEqual(len(notes), 1)
        self.assertIn("README.md: shell", notes[0])

    def test_what_main_gained_meanwhile_does_not_count(self) -> None:
        # main added ops/other.sh after the branch forked: not part of this pull request.
        root = self.repo({"tasks/todo.md": "done\n"})
        self.assertEqual(ci_plan.changed_paths(root), ["tasks/todo.md"])

    def test_a_code_pull_request_runs_everything(self) -> None:
        root = self.repo({"README.md": "new\n", "api/app/main.py": "changed\n"})
        self.assertEqual(ci_plan.decide("pull_request", "main", root)[0], EVERYTHING)

    def test_a_web_pull_request_skips_the_api_unit(self) -> None:
        root = self.repo({"web/src/App.tsx": "changed\n"})
        self.assertEqual(
            ci_plan.decide("pull_request", "main", root)[0],
            frozenset({"web", "shell", "e2e"}),
        )

    def test_a_deleted_file_counts(self) -> None:
        root = self.repo({"api/app/main.py": None})
        self.assertEqual(ci_plan.decide("pull_request", "main", root)[0], EVERYTHING)

    def test_a_file_moved_out_of_the_code_counts_at_its_old_path(self) -> None:
        root = self.repo({}, renames={"api/app/main.py": "docs/main.py.md"})
        self.assertIn("api/app/main.py", ci_plan.changed_paths(root) or [])
        self.assertEqual(ci_plan.decide("pull_request", "main", root)[0], EVERYTHING)

    def test_a_push_and_a_manual_run_run_everything_whatever_changed(self) -> None:
        root = self.repo({"tasks/todo.md": "new\n"})
        for event in ["push", "workflow_dispatch", "schedule", ""]:
            for base in ["", "main"]:
                units, sentence, _ = ci_plan.decide(event, base, root)
                self.assertEqual(units, EVERYTHING, event)
                self.assertIn("every unit runs", sentence)

    def test_a_pull_request_against_another_branch_runs_everything(self) -> None:
        """A stacked pull request: the same merge commit, the same one changed file under
        tasks/, but the base is not main. Its files against that base say nothing about what
        main gets when the base is changed to main, which starts no run (review of #93)."""
        root = self.repo({"tasks/todo.md": "new\n"})
        self.assertEqual(ci_plan.decide("pull_request", "main", root)[0], frozenset())
        for base in [
            "feature",
            "platform/91-ci-fast",
            "",
            "Main",
            "main ",
            "refs/heads/main",
            "main2",
            "maintenance",
        ]:
            units, sentence, notes = ci_plan.decide("pull_request", base, root)
            self.assertEqual(units, EVERYTHING, repr(base))
            self.assertIn("not against main: every unit runs", sentence)
            self.assertEqual(notes, [])
        self.assertIn("an unknown base", ci_plan.decide("pull_request", "", root)[1])

    def test_a_name_with_a_newline_stays_on_one_line(self) -> None:
        """A file name is printed in the job's log: one with a newline must not start a line of
        its own there, where `::error::` and the like are commands to the runner."""
        name = "tasks/evidence/agents-a5/x\n::error::made up"
        _, notes = ci_plan.plan_for([name])
        self.assertEqual(len(notes), 1)
        self.assertNotIn("\n", notes[0])
        self.assertIn("x\\n::error::made up: shell", notes[0])
        sentence = ci_plan.decide("pull_request", "x\n::warning::y", Path("."))[1]
        self.assertNotIn("\n", sentence)

    def test_a_checkout_that_is_no_merge_commit_runs_everything(self) -> None:
        root = self.repo({"tasks/todo.md": "new\n"})
        git(root, "checkout", "-q", "feature")
        self.assertIsNone(ci_plan.changed_paths(root))
        self.assertEqual(ci_plan.decide("pull_request", "main", root)[0], EVERYTHING)

    def test_a_shallow_checkout_without_the_parents_runs_everything(self) -> None:
        root = self.repo({"tasks/todo.md": "new\n"})
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        shallow = Path(tmp.name) / "clone"
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", f"file://{root}", str(shallow)],
            check=True,
            capture_output=True,
            env=CLEAN_ENV,
        )
        self.assertEqual(ci_plan.decide("pull_request", "main", shallow)[0], EVERYTHING)
        # With the depth the workflow asks for, the same clone can be judged.
        deep = Path(tmp.name) / "clone2"
        subprocess.run(
            ["git", "clone", "-q", "--depth", "2", f"file://{root}", str(deep)],
            check=True,
            capture_output=True,
            env=CLEAN_ENV,
        )
        self.assertEqual(ci_plan.decide("pull_request", "main", deep)[0], frozenset())

    def test_no_git_repository_runs_everything(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(
            ci_plan.decide("pull_request", "main", Path(tmp.name))[0], EVERYTHING
        )

    def test_the_command_writes_the_step_outputs(self) -> None:
        root = self.repo({"web/src/App.tsx": "changed\n"})
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out, summary = Path(tmp.name) / "out", Path(tmp.name) / "summary"
        script = root / ".github/scripts/ci_plan.py"
        script.parent.mkdir(parents=True)
        script.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
        env = {
            **CLEAN_ENV,
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_BASE_REF": "main",
            "GITHUB_OUTPUT": str(out),
            "GITHUB_STEP_SUMMARY": str(summary),
        }
        done = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            out.read_text(encoding="utf-8").split(),
            ["api=false", "web=true", "shell=true", "e2e=true", "plan=web,shell,e2e"],
        )
        self.assertIn("web,shell,e2e", summary.read_text(encoding="utf-8"))
        # The copied script is itself an unknown path in that repository, hence not committed.
        # The same pull request against another base, without a base, and as a push: everything.
        everything = "plan=api,web,shell,e2e"
        for change in [
            {"GITHUB_BASE_REF": "platform/91-ci-fast"},
            {"GITHUB_BASE_REF": ""},
            {"GITHUB_BASE_REF": None},
            {"GITHUB_EVENT_NAME": "push", "GITHUB_BASE_REF": None},
            {"GITHUB_EVENT_NAME": "push"},
        ]:
            changed = {**env, **change}
            changed = {k: v for k, v in changed.items() if v is not None}
            out.write_text("", encoding="utf-8")
            subprocess.run(
                [sys.executable, str(script)],
                capture_output=True,
                env=changed,
                check=True,
            )
            self.assertIn(everything, out.read_text(encoding="utf-8"), change)

    def test_an_unknown_argument_is_refused(self) -> None:
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "--frobnicate"],
            capture_output=True,
            check=False,
            env=CLEAN_ENV,
        )
        self.assertEqual(done.returncode, 2)


def outcomes(**by_step: str) -> dict[str, dict[str, object]]:
    return {
        step: {"outcome": outcome, "conclusion": outcome}
        for step, outcome in by_step.items()
    }


def lane_steps(lane: str, plan: str) -> dict[str, dict[str, object]]:
    """The steps of a job as a correct run of that plan leaves them."""
    names = set(plan.split(","))
    result = {step: "success" for step in ci_plan.ALWAYS.get(lane, ())}
    for unit, ids in ci_plan.LANES.get(lane, {}).items():
        for step in ids:
            result[step] = "success" if unit in names else "skipped"
    return outcomes(**result)


class VerifyTest(unittest.TestCase):
    def test_a_correct_run_of_every_plan_passes(self) -> None:
        for plan in ["api,web,shell,e2e", "web,shell,e2e", "shell", "none"]:
            for lane in ["api", "checks", "journeys"]:
                problems = ci_plan.verify_problems(
                    lane, plan, lane_steps(lane, plan), "success"
                )
                self.assertEqual(problems, [], (lane, plan))

    def test_a_needed_step_that_was_skipped_fails_the_job(self) -> None:
        steps = lane_steps("api", "api,web,shell,e2e")
        steps["pytest"] = {"outcome": "skipped"}
        problems = ci_plan.verify_problems("api", "api,web,shell,e2e", steps, "success")
        self.assertEqual(
            problems, ["the plan needs api and its step 'pytest' is skipped"]
        )
        steps = lane_steps("journeys", "web,shell,e2e")
        del steps["e2e_sweep"]
        problems = ci_plan.verify_problems(
            "journeys", "web,shell,e2e", steps, "success"
        )
        self.assertEqual(
            problems, ["the plan needs e2e and its step 'e2e_sweep' is missing"]
        )

    def test_pre_commit_is_needed_whatever_the_plan(self) -> None:
        steps = lane_steps("checks", "none")
        steps["precommit"] = {"outcome": "skipped"}
        problems = ci_plan.verify_problems("checks", "none", steps, "success")
        self.assertEqual(len(problems), 1)
        self.assertIn("precommit", problems[0])

    def test_a_step_that_ran_outside_the_plan_fails_the_job(self) -> None:
        steps = lane_steps("journeys", "none")
        steps["lint"] = {"outcome": "success"}
        problems = ci_plan.verify_problems("journeys", "none", steps, "success")
        self.assertEqual(
            problems, ["the plan leaves web out and its step 'lint' is success"]
        )

    def test_a_failed_or_cancelled_job_is_not_judged_twice(self) -> None:
        steps = lane_steps("api", "api,web,shell,e2e")
        steps["pytest"] = {"outcome": "failure"}
        steps["coverage"] = {"outcome": "skipped"}
        for status in ["failure", "cancelled"]:
            self.assertEqual(
                ci_plan.verify_problems("api", "api,web,shell,e2e", steps, status), []
            )

    def test_any_other_status_is_a_problem_itself(self) -> None:
        """Only the two words above skip the judgement. An empty or misspelled status (the env
        line of the step lost, a typo) must not pass for a job that ran nothing."""
        nothing_ran = {
            step: {"outcome": "skipped"}
            for step in lane_steps("api", "api,web,shell,e2e")
        }
        for status in ["", "Success", "succes", "skipped", "failed", "canceled", " "]:
            for steps in [nothing_ran, lane_steps("api", "api,web,shell,e2e"), {}]:
                problems = ci_plan.verify_problems(
                    "api", "api,web,shell,e2e", steps, status
                )
                self.assertEqual(len(problems), 1, repr(status))
                self.assertIn("CI_JOB_STATUS", problems[0])
        self.assertTrue(
            ci_plan.verify_problems("api", "api,web,shell,e2e", nothing_ran, "success")
        )

    def test_an_unknown_job_or_unit_is_a_problem(self) -> None:
        self.assertTrue(ci_plan.verify_problems("nope", "none", {}, "success"))
        self.assertTrue(ci_plan.verify_problems("api", "api,cake", {}, "success"))

    def run_verify(self, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "verify"],
            capture_output=True,
            text=True,
            check=False,
            env={**CLEAN_ENV, **env},
        )

    def test_the_command_reads_its_environment(self) -> None:
        plan = "web,shell,e2e"
        good = json.dumps(lane_steps("journeys", plan))
        done = self.run_verify(
            CI_LANE="journeys", CI_PLAN=plan, CI_JOB_STATUS="success", CI_STEPS=good
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        bad = lane_steps("journeys", plan)
        bad["vitest"] = {"outcome": "skipped"}
        done = self.run_verify(
            CI_LANE="journeys",
            CI_PLAN=plan,
            CI_JOB_STATUS="success",
            CI_STEPS=json.dumps(bad),
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn("'vitest' is skipped", done.stderr)
        for broken in [
            {
                "CI_LANE": "journeys",
                "CI_PLAN": plan,
                "CI_JOB_STATUS": "",
                "CI_STEPS": json.dumps(bad),
            },
            {
                "CI_LANE": "journeys",
                "CI_PLAN": plan,
                "CI_JOB_STATUS": "",
                "CI_STEPS": good,
            },
            {"CI_LANE": "journeys", "CI_PLAN": plan, "CI_STEPS": good},
            {
                "CI_LANE": "journeys",
                "CI_PLAN": "",
                "CI_JOB_STATUS": "success",
                "CI_STEPS": good,
            },
            {
                "CI_LANE": "journeys",
                "CI_PLAN": plan,
                "CI_JOB_STATUS": "success",
                "CI_STEPS": "{",
            },
        ]:
            self.assertEqual(self.run_verify(**broken).returncode, 1, broken)


# A mention that is no read, each with its reason: (file, what it names as the scan reports
# it). Anything else the scan below finds must be covered by the rule of the path it names.
MENTIONS_ONLY = {
    ("api/tests/test_migration_rules.py", "CONTRIBUTING.md"): "in a failure message",
    ("deploy.sh", "CONTRIBUTING.md"): "in a failure message",
    (".github/workflows/deploy.yml", "CONTRIBUTING.md"): "in a warning",
    (
        ".github/workflows/ci.yml",
        "tasks/evidence/ci",
    ): "what a failed journey wrote, uploaded: written by the run, never read",
    (
        "tests/e2e_jetson.sh",
        "tasks/evidence/identity/index.md",
    ): "written by the script, named in its last message; run by hand, not in CI",
}
# The folder itself, named without a subfolder: where journeys and checks write their
# screenshots and logs, under a subfolder they compute (E2E_EVIDENCE). A named subfolder is
# judged like any other path: one of them is read by a shell test and has a rule of its own.
EVIDENCE_ROOT = "tasks/evidence"


class ReasonsTest(unittest.TestCase):
    """What lets a rule skip a unit, checked on this repository."""

    def strings(self, path: Path) -> list[str]:
        """The string constants of a Python file that are not docstrings."""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                body = node.body
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                ):
                    docstrings.add(id(body[0].value))
        return [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ]

    def test_no_api_file_reads_a_file_of_the_web_app(self) -> None:
        """The rule for web/ skips the api unit. An api test that opens a file under web/ would
        make that wrong: give that file a rule of its own in ci_plan.py first."""
        offending = []
        for path in sorted((ROOT / "api").rglob("*.py")):
            if ".venv" in path.parts:
                continue
            for text in self.strings(path):
                if text == "web" or re.search(r"(^|[\"'/ ])web/", text):
                    offending.append(f"{path.relative_to(ROOT)}: {text[:60]!r}")
        self.assertEqual(offending, [])

    # A path the rules call documentation, as a test or a script would have to write it: a
    # file by its name, or a path under docs/ or tasks/ with all that follows.
    DOCS = re.compile(
        r"README\.md|CONTRIBUTING\.md|CAPABILITY-MAP\.md|SPEC-[a-z]+\.md|HANDOFF\.md"
        r"|(?<![\w.-])docs/[\w./-]*|(?<![\w./-])tasks/[\w./-]*"
    )
    # The same path written in parts (ROOT / "tasks" / name, join(root, "docs")), or found by a
    # pattern over the Markdown files: a line of code holding one of these can only be listed.
    PARTS = re.compile(r"""["'](tasks|docs)["']|\*\.md\b""")

    def code_lines(self, path: Path, hash_comments: bool = False) -> list[str]:
        """The lines of a file that are not whole-line comments, without a trailing `// ...`
        and, in shell, YAML and Dockerfiles, without a trailing ` # ...`."""
        lines = []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("#", "//", "*", "/*")):
                continue
            line = re.sub(r"\s//\s.*$", "", line)
            if hash_comments:
                line = re.sub(r"\s#\s.*$", "", line)
            lines.append(line)
        return lines

    def scanned(self) -> dict[str, list[Path]]:
        """What is read for mentions, by the unit that runs it. "every" is what every unit
        runs under: the workflows."""

        def files(*patterns: str) -> list[Path]:
            found = []
            for pattern in patterns:
                found += [
                    path
                    for path in sorted(ROOT.glob(pattern))
                    if path.is_file()
                    and not {"node_modules", ".venv", "dist"} & set(path.parts)
                ]
            return found

        return {
            "web": files("web/src/**/*.ts", "web/src/**/*.tsx", "web/*.ts"),
            # The journeys, and what their scratch stack is built from and started with.
            "e2e": files(
                "web/e2e/**/*.ts",
                "*/Dockerfile*",
                "ops/*/Dockerfile*",
                "compose*.yml",
                "tests/*.yml",
                "caddy/*",
            ),
            # The shell tests and what they run: deploy.sh and the scripts of ops/.
            "shell": files(
                "tests/*.sh", ".github/scripts/*.sh", "deploy.sh", "ops/**/*.sh"
            ),
            "every": files(".github/workflows/*.yml"),
        }

    def test_nothing_executable_reads_the_documentation(self) -> None:
        """A documentation rule may skip a unit only if nothing of that unit reads the file. So
        every place where something executable names a documentation path is looked up: the
        rule of that path must run the unit the file belongs to (tests/deploy_test.sh reads
        docs/runbooks/, and their rule runs the shell unit; a workflow belongs to every
        unit), or the mention is listed in MENTIONS_ONLY with its reason.

        Read: the Python of api/ and of .github/scripts and tests/ (string constants, docstrings
        aside), the TypeScript of web/src, web/e2e and web/*.ts, the shell of tests/, ops/,
        .github/scripts and deploy.sh, the Dockerfiles, the Compose files, caddy/ and both
        workflows (comments aside).

        Not read, so not guarded: a path put together at run time from anything but the bare
        words "tasks" and "docs" (a variable, an environment variable, a listing of a folder);
        the folder tasks/evidence named without a subfolder; comments and docstrings; files of
        other kinds (web/*.json, web/index.html, api/pyproject.toml and alembic.ini,
        tailscale/, .dockerignore, .rsyncignore); and .pre-commit-config.yaml, whose hooks run
        on every run whatever the plan."""
        offending = []
        listed = set()  # the entries of MENTIONS_ONLY the scan came across

        def judge(path: Path, units: tuple[str, ...], text: str, python: bool) -> None:
            where = str(path.relative_to(ROOT))
            if python:  # one string constant: a bare word, or a pattern inside it
                parts = [text] if text in ("tasks", "docs") else []
                if "*.md" in text:
                    parts.append("*.md")
            else:
                parts = [
                    found.group(1) or "*.md" for found in self.PARTS.finditer(text)
                ]
            for part in parts:
                if (where, part) in MENTIONS_ONLY:
                    listed.add((where, part))
                    continue
                offending.append(
                    f"{where} holds {part!r}: a documentation path written in parts or "
                    f"found by a pattern cannot be judged, list it with its reason: "
                    f"{text.strip()[:70]!r}"
                )
            for found in self.DOCS.finditer(text):
                named = found.group(0).rstrip("/.")
                if (where, named) in MENTIONS_ONLY:
                    listed.add((where, named))
                    continue
                if named == EVIDENCE_ROOT:
                    continue
                target = "docs/HANDOFF.md" if named == "HANDOFF.md" else named
                if "." not in target.rsplit("/", 1)[-1]:
                    target += (
                        "/any-file"  # a folder: judged by the rule of what it holds
                    )
                needed = ci_plan.units_for(target)[0]
                missing = [unit for unit in units if unit not in needed]
                if missing:
                    offending.append(
                        f"{where} names {named}, whose rule does not run "
                        f"{', '.join(missing)}: {text.strip()[:70]!r}"
                    )

        every = tuple(ci_plan.UNITS)
        for path in sorted((ROOT / "api").rglob("*.py")):
            if ".venv" in path.parts:
                continue
            for text in self.strings(path):
                judge(path, ("api",), text, python=True)
        scanned = self.scanned()
        for unit, files in scanned.items():
            self.assertTrue(files, f"nothing found to read for {unit}")
            for path in files:
                hashes = path.suffix not in (".ts", ".tsx")
                for line in self.code_lines(path, hash_comments=hashes):
                    judge(
                        path, every if unit == "every" else (unit,), line, python=False
                    )
        for path in [
            *sorted(ROOT.glob(".github/scripts/*.py")),
            *sorted(ROOT.glob("tests/*.py")),
        ]:
            if path.name.startswith("ci_plan"):
                continue  # the rules themselves and this test name the documentation
            for text in self.strings(path):
                judge(path, ("shell",), text, python=True)
        self.assertEqual(offending, [])
        # Every entry of the list is still a mention the scan finds: a stale entry would let a
        # later read of that path in that file through.
        self.assertEqual(
            sorted(set(MENTIONS_ONLY) - listed), [], "MENTIONS_ONLY lists what is gone"
        )

    def test_the_guard_sees_what_it_is_there_for(self) -> None:
        """The scan on made-up lines: what it must find, and what it must leave."""
        self.assertEqual(
            [found.group(0) for found in self.DOCS.finditer('open("docs/HANDOFF.md")')],
            ["docs/HANDOFF.md"],
        )
        line = (
            'COMMITTED="tasks/evidence/agents-a5"; cmp x tasks/evidence/other/run.json'
        )
        self.assertEqual(
            [found.group(0) for found in self.DOCS.finditer(line)],
            ["tasks/evidence/agents-a5", "tasks/evidence/other/run.json"],
        )
        for line in ['ROOT / "tasks" / name', "join(root, 'docs')", 'glob("*.md")']:
            self.assertTrue(self.PARTS.search(line), line)
        for line in ['"tasksfile"', "web/src/tasks.ts", '"mytasks/x"', "x.md5"]:
            self.assertFalse(self.PARTS.search(line), line)
            self.assertFalse(self.DOCS.search(line), line)
        scanned = self.scanned()
        names = {
            str(path.relative_to(ROOT)) for paths in scanned.values() for path in paths
        }
        for expected in [
            "deploy.sh",
            "ops/ci-runners.sh",
            "ops/ci-runner/job-started.sh",
            "api/Dockerfile",
            "web/Dockerfile",
            "ops/ci-runner/Dockerfile",
            "compose.yml",
            "tests/ats-stub.compose.yml",
            ".github/workflows/ci.yml",
            ".github/workflows/deploy.yml",
            "tests/deploy_test.sh",
            "web/e2e/stack.ts",
        ]:
            self.assertIn(expected, names)

    def test_the_readme_rule_matches_what_the_shell_test_reads(self) -> None:
        text = (ROOT / "tests/deploy_test.sh").read_text(encoding="utf-8")
        self.assertIn("README.md", text)
        self.assertEqual(ci_plan.units_for("README.md")[0], frozenset({"shell"}))
        self.assertIn("docs/runbooks/continuous-delivery.md", text)
        self.assertEqual(
            ci_plan.units_for("docs/runbooks/continuous-delivery.md")[0],
            frozenset({"shell"}),
        )

    def test_the_evidence_rule_matches_what_the_shell_test_reads(self) -> None:
        text = (ROOT / "tests/agents_real_run_test.sh").read_text(encoding="utf-8")
        self.assertIn('COMMITTED="tasks/evidence/agents-a5"', text)
        self.assertEqual(
            ci_plan.units_for("tasks/evidence/agents-a5/anything")[0],
            frozenset({"shell"}),
        )

    def test_the_tailscale_test_still_reads_the_web_dockerfile(self) -> None:
        # Why the web rule keeps the shell unit.
        text = (ROOT / "tests/tailscale_test.sh").read_text(encoding="utf-8")
        self.assertIn("web/Dockerfile", text)
        self.assertIn("shell", ci_plan.units_for("web/Dockerfile")[0])

    def test_every_tracked_file_has_a_decided_plan(self) -> None:
        """Informative: how this repository's files fall under the rules today."""
        done = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"],
            capture_output=True,
            text=True,
            check=False,
            env=CLEAN_ENV,
        )
        if done.returncode != 0:
            self.skipTest("not a git checkout")
        counts: dict[str, int] = {}
        for path in filter(None, done.stdout.split("\0")):
            counts[ci_plan.encode(ci_plan.units_for(path)[0])] = (
                counts.get(ci_plan.encode(ci_plan.units_for(path)[0]), 0) + 1
            )
        self.assertGreater(counts.get("api,web,shell,e2e", 0), 0)
        print(f"\nci_plan: tracked files by plan: {dict(sorted(counts.items()))}")


class WorkflowTest(unittest.TestCase):
    """.github/workflows/ci.yml follows the plan."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.jobs = read_jobs(WORKFLOW.read_text(encoding="utf-8"))

    def test_the_jobs_are_the_lanes(self) -> None:
        lanes = set(ci_plan.LANES) | set(ci_plan.ALWAYS)
        self.assertEqual(set(self.jobs), lanes)

    def test_no_lane_can_be_skipped(self) -> None:
        for name in self.jobs:
            keys = self.jobs[name]["keys"]
            self.assertNotIn(
                "if", keys, f"{name}: a job condition could skip the whole job"
            )
            self.assertNotIn("needs", keys, f"{name}: a job that waits can be skipped")

    def test_every_lane_plans_first_and_verifies_last(self) -> None:
        for name in self.jobs:
            steps = self.jobs[name]["steps"]
            self.assertIn("actions/checkout@", steps[0].get("uses", ""), name)
            # The merge commit and its parents for the plan; the whole history in the job that
            # runs the secret scan, which reads every commit of the run.
            depth = "fetch-depth: 0" if name == "checks" else "fetch-depth: 2"
            self.assertIn(depth, steps[0].get("with", ""), name)
            self.assertEqual(steps[1].get("id"), "plan", name)
            self.assertEqual(
                steps[1].get("run"), "python3 .github/scripts/ci_plan.py", name
            )
            self.assertNotIn("if", steps[1], name)
            last = steps[-1]
            self.assertEqual(
                last.get("run"), "python3 .github/scripts/ci_plan.py verify", name
            )
            self.assertEqual(last.get("if"), "${{ !cancelled() }}", name)
            env = last.get("env", "")
            self.assertIn(f"CI_LANE: {name}", env)
            self.assertIn("CI_PLAN: ${{ steps.plan.outputs.plan }}", env)
            self.assertIn("CI_JOB_STATUS: ${{ job.status }}", env)
            self.assertIn("CI_STEPS: ${{ toJSON(steps) }}", env)

    def test_every_step_of_a_unit_exists_and_runs_under_its_unit(self) -> None:
        for lane, units in ci_plan.LANES.items():
            steps = steps_by_id(self.jobs[lane])
            for unit, ids in units.items():
                for step in ids:
                    self.assertIn(step, steps, f"{lane}: no step with id {step}")
                    self.assertTrue(
                        chain_follows(step, unit, steps),
                        f"{lane}: step {step} does not depend on steps.plan.outputs.{unit}: "
                        f"{steps[step].get('if', 'no condition')}",
                    )
                    self.assertNotIn("continue-on-error", steps[step], step)

    def test_the_steps_that_always_run_have_no_plan_condition(self) -> None:
        for lane, ids in ci_plan.ALWAYS.items():
            steps = steps_by_id(self.jobs[lane])
            for step in ids:
                self.assertIn(step, steps)
                self.assertNotIn("steps.plan.outputs", steps[step].get("if", ""), step)
                self.assertNotIn("outcome", steps[step].get("if", ""), step)

    def test_no_step_is_allowed_to_fail(self) -> None:
        text = "\n".join(
            line
            for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        self.assertNotIn("continue-on-error", text)

    def test_every_unit_has_steps_somewhere(self) -> None:
        placed = {unit for units in ci_plan.LANES.values() for unit in units}
        self.assertEqual(placed, set(ci_plan.UNITS))

    def test_every_step_is_a_check_or_a_listed_support_step(self) -> None:
        """The other direction: the tests above start from LANES and look each id up in ci.yml;
        this one starts from ci.yml. Every step has an id, and every id is a check (LANES), a
        step of every run (ALWAYS) or a support step (SUPPORT), in exactly one of the three. So
        a check added to the workflow and to no table fails here, and one added to LANES is
        held to its unit by the tests above and to its outcome by `verify`."""
        self.assertEqual(set(ci_plan.SUPPORT), set(self.jobs))
        for lane, job in self.jobs.items():
            steps = job["steps"]
            nameless = [
                step.get("name") or step.get("uses") or step.get("run")
                for step in steps
                if "id" not in step
            ]
            self.assertEqual(nameless, [], f"{lane}: steps without an id")
            ids = [step["id"] for step in steps]
            self.assertEqual(len(ids), len(set(ids)), f"{lane}: an id is used twice")
            checks = {
                step for unit in ci_plan.LANES.get(lane, {}).values() for step in unit
            }
            always = set(ci_plan.ALWAYS.get(lane, ()))
            support = set(ci_plan.SUPPORT[lane])
            self.assertEqual(checks & support, set(), lane)
            self.assertEqual(always & support, set(), lane)
            self.assertEqual(checks & always, set(), lane)
            known = checks | always | support
            self.assertEqual(
                sorted(set(ids) - known),
                [],
                f"{lane}: steps of ci.yml that are in no table of ci_plan.py (a check goes "
                "to LANES under its unit, anything else to SUPPORT)",
            )
            self.assertEqual(
                sorted(known - set(ids)),
                [],
                f"{lane}: listed in ci_plan.py, not in ci.yml",
            )

    # What a check runs. A step whose command holds one of these is no support step.
    CHECK_COMMAND = re.compile(
        r"\bpytest\b|\bvitest\b|playwright test|\bruff\b|shellcheck|\beslint\b|\btsc\b"
        r"|pnpm (lint|typecheck|build|test)\b|_test\.(sh|py)\b|e2e_groups\.py"
        r"|pre-commit run|gitleaks|secret_scan|--fail-under=(?!0\b)"
    )

    def test_a_support_step_runs_no_test_command(self) -> None:
        """SUPPORT is a written list: this keeps a check from being filed there, where `verify`
        would not look at it."""
        for sample in [
            "uv run --frozen pytest tests",
            "pnpm exec vitest run",
            "pnpm exec playwright test jobs.spec.ts",
            "tests/deploy_test.sh",
            "python3 .github/scripts/ci_plan_test.py",
            "uv run --frozen coverage report --fail-under=95",
            "pnpm lint",
        ]:
            self.assertTrue(self.CHECK_COMMAND.search(sample), sample)
        for lane, ids in ci_plan.SUPPORT.items():
            steps = steps_by_id(self.jobs[lane])
            for step in ids:
                if step == "verify":
                    continue  # pinned word for word by the test of the first and last steps
                command = steps[step].get("run", "")
                found = self.CHECK_COMMAND.search(command)
                self.assertIsNone(
                    found,
                    f"{lane}: support step {step} runs a check ({found and found.group(0)}): "
                    "it belongs in LANES",
                )

    def test_a_job_is_named_by_its_id(self) -> None:
        """The names of the checks a run reports, and what a ruleset would have to require:
        three stable words, not a list of contents that changes when a unit moves."""
        for lane, job in self.jobs.items():
            self.assertEqual(job["keys"].get("name"), lane)

    def test_the_long_steps_have_a_bound_of_their_own(self) -> None:
        bounded = {
            "api": ("pytest", "docker_tests"),
            "checks": ("e2e_apps", "e2e_ingestion"),
            "journeys": ("vitest", "e2e_identity", "e2e_sweep"),
        }
        for lane, ids in bounded.items():
            steps = steps_by_id(self.jobs[lane])
            whole = int(self.jobs[lane]["keys"]["timeout-minutes"])
            for step in ids:
                minutes = steps[step].get("timeout-minutes", "")
                self.assertRegex(minutes, r"^\d+$", f"{lane}: {step} has no bound")
                self.assertLess(int(minutes), whole, step)

    def test_the_triggers_have_no_filter(self) -> None:
        """Issue #116: the workflow runs by hand only. A pull_request or push trigger put back
        here would start a ten minute run on the Mac runners for every push again, next to
        the batch check (ops/local-check.sh) that a merge waits for now."""
        lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
        block = []
        for line in lines[lines.index("on:") + 1 :]:
            if line and not line.startswith((" ", "#")):
                break
            if line.strip() and not line.strip().startswith("#"):
                block.append(line.rstrip())
        self.assertEqual(
            block,
            [
                "  workflow_dispatch:",
            ],
        )

    def test_the_secret_scan_reads_commits_with_the_pinned_gitleaks(self) -> None:
        """The gitleaks hook of pre-commit reads staged changes, and a checkout has none: run
        with --all-files in CI it passed whatever was committed. So CI runs the second hook
        of the same pinned repository, by name, which reads the commits of the run, in the job
        whose checkout has the whole history; and the first is skipped there, not left to
        report a pass that means nothing."""
        steps = steps_by_id(self.jobs["checks"])
        scan = steps["secrets"]
        # --verbose: the log of the step says which commits were read, also when it passes.
        # env -u SKIP: the hook of the scan has the id gitleaks, as the hook of a commit has,
        # and pre-commit skips by id: a SKIP that reached this command would turn the scan
        # into a green step that read nothing.
        self.assertRegex(
            scan["run"],
            r"^env -u SKIP uvx --from pre-commit==[\d.]+ pre-commit run gitleaks-commits "
            r"--hook-stage manual --verbose$",
        )
        # And SKIP is set in one place of the workflow, the env of the pre-commit step: not
        # on the job, not at the top, where it would be the environment of the scan too.
        code = [
            line
            for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        ]
        self.assertEqual(len([line for line in code if "SKIP:" in line]), 1)
        self.assertIn("SKIP:", steps["precommit"]["env"])
        self.assertNotIn("SKIP", scan.get("env", ""))
        # The pre-commit step prints no diff: one would show the changed lines in clear.
        self.assertNotIn("--show-diff", "\n".join(code))
        self.assertEqual(scan.get("if"), "${{ !cancelled() }}")
        self.assertIn("CI_PUSH_BEFORE: ${{ github.event.before }}", scan.get("env", ""))
        self.assertIn("fetch-depth: 0", steps["checkout"].get("with", ""))
        self.assertIn("gitleaks", steps["precommit"]["env"])
        # Both commands name the same pre-commit.
        versions = set(
            re.findall(r"uvx --from (pre-commit==[\d.]+)", WORKFLOW.read_text("utf-8"))
        )
        self.assertEqual(len(versions), 1, versions)
        config = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        blocks = [
            block
            for block in config.split("  - repo: ")
            if block.startswith("https://")
        ]
        gitleaks = [block for block in blocks if "gitleaks/gitleaks" in block]
        self.assertEqual(
            len(gitleaks), 1, "one pinned gitleaks, for the commit and for CI"
        )
        block = "\n".join(
            line
            for line in gitleaks[0].splitlines()
            if not line.lstrip().startswith("#")
        )
        self.assertEqual(len(re.findall(r"^\s+rev: v[\d.]+$", block, re.MULTILINE)), 1)
        self.assertEqual(block.count("- id: gitleaks"), 2)
        for line in [
            "alias: gitleaks-commits",
            "entry: .github/scripts/secret_scan.sh",
            "stages: [manual]",
            "always_run: true",
            "pass_filenames: false",
        ]:
            self.assertIn(f"        {line}\n", block + "\n", line)
        script = ROOT / ".github/scripts/secret_scan.sh"
        self.assertTrue(os.access(script, os.X_OK), "secret_scan.sh is not executable")
        text = script.read_text(encoding="utf-8")
        self.assertIn("gitleaks git ", text)
        # The lines typed into a merge commit, and the commit messages, are read too.
        self.assertIn('--remerge-diff "$range"', text)
        self.assertIn("gitleaks stdin ", text)
        self.assertNotIn("--staged", text.split("set -euo pipefail", 1)[1])
        self.assertNotIn("--pre-commit", text.split("set -euo pipefail", 1)[1])

    def test_only_the_last_teardown_of_a_job_is_forgiven(self) -> None:
        """Two stacks follow each other in one job under one project name and port. The second
        one's fresh database rests on the teardown of the first: that teardown may not end in
        `|| true`, and the next stack starts only when it succeeded."""
        seen = 0
        for lane, job in self.jobs.items():
            steps = job["steps"]
            downs = [
                index
                for index, step in enumerate(steps)
                if " down -v " in step.get("run", "")
            ]
            for index in downs[:-1]:
                seen += 1
                down = steps[index]
                self.assertNotIn("|| true", down["run"], f"{lane}: {down['id']}")
                following = next(
                    step
                    for step in steps[index + 1 :]
                    if " up -d " in step.get("run", "")
                )
                self.assertIn(
                    f"steps.{down['id']}.outcome == 'success'",
                    following.get("if", ""),
                    f"{lane}: {following['id']} starts whatever {down['id']} left",
                )
                self.assertIn(
                    "com.docker.compose.project=$PROJECT", following["run"], lane
                )
        self.assertEqual(
            seen, 2, "the teardowns between apps and ingestion, identity and sweep"
        )


class HookSafetyTest(unittest.TestCase):
    """These tests run as a pre-commit hook, inside the commit they check."""

    def test_git_env_drops_what_a_hook_inherits(self) -> None:
        inherited = {
            "GIT_DIR": "/x/.git",
            "GIT_INDEX_FILE": "/x/.git/index",
            "GIT_WORK_TREE": "/x",
        }
        saved = {name: os.environ.get(name) for name in inherited}
        os.environ.update(inherited)
        try:
            env = ci_plan.git_env()
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        self.assertFalse([name for name in env if name.startswith("GIT_")])
        self.assertIn("PATH", env)

    def test_every_process_these_tests_start_gets_an_explicit_environment(self) -> None:
        for name in ["ci_plan_test.py", "ci_plan.py"]:
            path = Path(__file__).resolve().parent / name
            tree = ast.parse(path.read_text(encoding="utf-8"))
            calls = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
            ]
            self.assertTrue(calls, name)
            for call in calls:
                self.assertIn(
                    "env",
                    [keyword.arg for keyword in call.keywords],
                    f"{name} line {call.lineno}: subprocess.run without env=",
                )

    def test_a_hook_environment_does_not_reach_the_throwaway_repositories(self) -> None:
        """As pre-commit runs it: GIT_DIR and GIT_INDEX_FILE point at a real repository."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        real = Path(tmp.name) / "real"
        real.mkdir()
        git(real, "init", "-q", "-b", "main")
        (real / "kept.txt").write_text("kept\n", encoding="utf-8")
        git(real, "add", "-A")
        git(real, "commit", "-q", "-m", "kept")
        before = git(real, "ls-files", "--stage")
        hook = {
            **CLEAN_ENV,
            "GIT_DIR": str(real / ".git"),
            "GIT_INDEX_FILE": str(real / ".git/index"),
            "GIT_WORK_TREE": str(real),
        }
        done = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "DecideTest",
                "ReasonsTest",
            ],
            capture_output=True,
            text=True,
            check=False,
            env=hook,
        )
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        self.assertEqual(git(real, "ls-files", "--stage"), before)
        self.assertEqual(git(real, "status", "--porcelain"), "")
        self.assertEqual(git(real, "branch", "--list"), "* main")
        # The symptom of 2026-10-10 by its name: a `git init` that reached the hook's
        # repository rewrote its configuration and left it marked as bare.
        self.assertEqual(git(real, "config", "--bool", "core.bare"), "false")


class ReaderTest(unittest.TestCase):
    """The small reader of ci.yml, on a made-up workflow."""

    TEXT = """\
name: CI
env:
  A: "1"
jobs:
  # a comment
  one:
    name: first
    outputs:
      plan: ${{ steps.plan.outputs.plan }}
    steps:
      - uses: actions/checkout@abc # v1
        with:
          fetch-depth: 2
      - name: Plan
        id: plan
        run: python3 x.py
      - name: Long
        id: long
        if: ${{ !cancelled() && steps.plan.outputs.api == 'true' }}
        run: >-
          one
          two
      - name: After
        id: after
        if: ${{ !cancelled() && steps.long.outcome == 'success' }}
        run: |
          three
  gate:
    needs: [one]
    if: always()
    steps:
      - name: g
        run: true
"""

    def test_jobs_keys_and_steps(self) -> None:
        jobs = read_jobs(self.TEXT)
        self.assertEqual(list(jobs), ["one", "gate"])
        self.assertEqual(
            jobs["one"]["keys"]["outputs"], "plan: ${{ steps.plan.outputs.plan }}"
        )
        self.assertEqual(jobs["gate"]["keys"]["needs"], "[one]")
        steps = jobs["one"]["steps"]
        self.assertEqual(len(steps), 4)
        self.assertEqual(steps[0]["with"], "fetch-depth: 2")
        self.assertEqual(steps[2]["run"], ">- one two")
        by_id = steps_by_id(jobs["one"])
        self.assertTrue(chain_follows("long", "api", by_id))
        self.assertTrue(chain_follows("after", "api", by_id))
        self.assertFalse(chain_follows("after", "web", by_id))
        self.assertFalse(chain_follows("plan", "api", by_id))


class JourneysTest(unittest.TestCase):
    """`ci_plan.py journeys`: the journey groups of the batch check that a set of changed files
    touches (issue #552), on made-up trees and on this repository."""

    GROUPS = (
        ("apps", "jobs.spec.ts jobs-map.spec.ts --workers=1"),
        ("mail", "-c e2e/mail-all.config.ts"),
        ("documents", "-c e2e/documents-all.config.ts"),
        ("apply", "-c e2e/apply-all.config.ts"),
        ("sweep", "jobs-sweep.spec.ts --workers=1"),
    )

    def tree(self, files: dict[str, str]) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
        return root

    BASE: ClassVar[dict[str, str]] = {
        "web/e2e/mail-all.config.ts": 'const SPEC_PREFIX = "mail-";\n',
        "web/e2e/mail-m1.spec.ts": "",
        "web/e2e/documents-all.config.ts": 'const SPEC_PREFIX = "documents-";\n',
        "web/e2e/documents-d1.spec.ts": "",
        "web/e2e/apply-all.config.ts": 'const SPECS = ["apply-a1"] as const;\n',
        "web/e2e/apply-a1.spec.ts": "",
        "web/e2e/apply-helpers.ts": "",
        "web/e2e/jobs.spec.ts": "",
        "api/app/__init__.py": "",
        "api/app/db.py": "",
        "api/app/main.py": "from app.mail import router\nfrom app.documents import store\n",
        "api/app/mail/__init__.py": "",
        "api/app/mail/router.py": "from app import db\n",
        "api/app/mail/rules.py": "",
        "api/app/mail/sync.py": "",
        "api/app/documents/__init__.py": "",
        "api/app/documents/store.py": "from app.mail.rules import Sources\n",
        "web/src/routes.tsx": 'import { Mail } from "@/features/mail/Mail";\n',
        "web/src/lib/api.ts": "",
        "web/src/features/mail/Mail.tsx": 'import { api } from "@/lib/api";\nimport { Row } from "./Row";\n',
        "web/src/features/mail/Row.tsx": "",
        "web/src/features/mail/Badge.tsx": "",
        "web/src/features/mail/Mail.test.tsx": 'import { Badge } from "./Badge";\n',
        "web/src/features/account/Account.tsx": 'import { Badge } from "@/features/mail/Badge";\n',
        # The shared pages the api reader reads (ci_plan.WEB_SHARED_PAGES), calling nothing.
        **dict.fromkeys(ci_plan.WEB_SHARED_PAGES, ""),
    }

    def plan(
        self, paths: list[str], files: dict[str, str] | None = None
    ) -> dict[str, str]:
        root = self.tree({**self.BASE, **(files or {})})
        return {
            group: f"{verdict}: {why}"
            for verdict, group, why in ci_plan.journey_plan(root, paths, self.GROUPS)
        }

    def ran(self, paths: list[str], files: dict[str, str] | None = None) -> list[str]:
        return [g for g, v in self.plan(paths, files).items() if v.startswith("run")]

    def test_api_shared_files_assets_and_undecided_modules_run_every_group(
        self,
    ) -> None:
        paths = [
            "api/app/__init__.py",
            "api/app/db.py",
            "api/app/main.py",
            "api/app/worker.py",
            "api/app/models.py",
            "api/app/mail/__init__.py",
            "api/app/mail/models.py",
            "api/app/mail/sync_models.py",
            "api/app/mail/template.html",
            "api/app/mail/README.md",
            "api/app/mail/assets/nested/value.json",
            "api/app/new_module/consumer.py",
            "api/app/new_module/assets/value.json",
            "api/app/documents/freeze.py",
            "api/app/documents/freeze/nested.py",
        ]
        for path in paths:
            for exists in (True, False):
                files = {
                    p: text for p, text in self.BASE.items() if p.startswith("web/")
                }
                if exists:
                    files[path] = ""
                root = self.tree(files)
                with self.subTest(path=path, exists=exists):
                    self.assertEqual(
                        [
                            v
                            for v, _, _ in ci_plan.journey_plan(
                                root, [path], self.GROUPS
                            )
                        ],
                        ["run"] * len(self.GROUPS),
                    )
        # A file of a decided module that is gone: its consumers may be in the base only.
        self.assertEqual(
            self.ran(["api/app/mail/gone.py"]), [g for g, _ in self.GROUPS]
        )

    def test_documentation_and_operations_run_no_group(self) -> None:
        paths = [
            "CONTRIBUTING.md",
            "SPEC-mail.md",
            ".claude/agents/x.md",
            "ops/card.sh",
        ]
        paths += [
            "tests/deploy_test.sh",
            "api/tests/profile/test_profile.py",
            ".github/workflows/ci.yml",
        ]
        self.assertEqual(self.ran(paths), [])
        self.assertTrue(
            all(
                v == "skip: no changed file touches it"
                for v in self.plan(paths).values()
            )
        )

    def test_one_module_runs_its_groups_only(self) -> None:
        self.assertEqual(self.ran(["web/src/features/mail/Row.tsx"]), ["mail"])
        self.assertEqual(self.ran(["web/e2e/mail-m1.spec.ts"]), ["mail"])
        self.assertEqual(self.ran(["web/e2e/apply-all.config.ts"]), ["apply"])
        self.assertEqual(self.ran(["web/e2e/jobs.spec.ts"]), ["apps"])
        self.assertEqual(self.ran(["tests/gmail-stub.compose.yml"]), ["mail"])
        # The fake feeds server (O7) is started by the opportunities group alone.
        rules = {pattern: groups for pattern, groups, _ in ci_plan.JOURNEY_RULES}
        for path in ("tests/feeds_stub.py", "tests/feeds-stub.compose.yml"):
            self.assertEqual(rules[path], frozenset({"opportunities"}))

    def test_a_file_imported_by_another_module_runs_that_module_s_groups_too(
        self,
    ) -> None:
        # rules.py runs mail's groups and those of documents, whose store imports it; main.py
        # imports both and is wiring, as routes.tsx is for the web.
        self.assertEqual(
            self.ran(["api/app/mail/rules.py"]), ["mail", "documents", "apply"]
        )
        self.assertEqual(self.ran(["api/app/mail/router.py"]), ["mail"])
        # Badge is imported by the account feature, which every journey goes through; a Vitest
        # file that imports it does not count.
        plan = self.plan(["web/src/features/mail/Badge.tsx"])
        self.assertEqual(
            set(plan.values()),
            {
                "run: every group, web/src/features/mail/Badge.tsx: every journey goes through the module account"
            },
        )
        self.assertEqual(self.ran(["web/src/features/mail/Mail.test.tsx"]), [])

    def test_python_static_relative_and_dynamic_consumers_run_every_group(self) -> None:
        forms = [
            "import app.mail.rules",
            "from app.mail.rules import Sources",
            "from ..mail.rules import Sources",
            "from ..mail import rules",
            "import importlib; importlib.import_module('app.mail.rules')",
            "import importlib as il; il.import_module('app.mail.rules')",
            "from importlib import import_module as load; load('app.mail.rules')",
            "__import__('app.mail.rules')",
            "from builtins import __import__ as load; load('app.mail.rules')",
            "import importlib; importlib.import_module('..mail.rules', 'app.documents')",
        ]
        forms += [source.replace("; ", "\n") for source in forms if "; " in source]
        forms += [source.replace("'", '"') for source in forms if "'" in source]
        for source in forms:
            with self.subTest(source=source):
                files = {
                    "api/app/documents/store.py": source,
                    "api/app/opportunities/use.py": "from app.documents.store import value",
                }
                # A static import (absolute or relative) carries the consumer's groups; a
                # dynamic one could load anything and runs every group.
                dynamic = "import_module" in source or "__import__" in source
                self.assertEqual(
                    self.ran(["api/app/mail/rules.py"], files),
                    [g for g, _ in self.GROUPS]
                    if dynamic
                    else ["mail", "documents", "apply"],
                )

    def test_unknown_python_import_targets_force_every_group(self) -> None:
        for source in [
            "import importlib; importlib.import_module(target)",
            "__import__('app.' + target)",
            "from importlib import import_module as load; load(target)",
            "import importlib; importlib.import_module('.rules', package)",
            "import app.mail.missing",
            "from ..missing import value",
            "import importlib; loader = importlib.import_module; loader(target)",
            "from builtins import __import__ as load; load(target)",
        ]:
            with self.subTest(source=source):
                self.assertEqual(
                    self.ran(
                        ["api/app/mail/rules.py"],
                        {"api/app/documents/store.py": source},
                    ),
                    [g for g, _ in self.GROUPS],
                )

    def test_typescript_import_forms_retain_consumers(self) -> None:
        forms = [
            'export {}; import "@/features/mail/Badge";',
            "export const value = import(`@/features/mail/Badge`);",
            'export const value = import("@/features/mail/Badge");',
            "export const value = import('@/features/mail/Badge');",
            "import { value } /* 'comment' */ from \"@/features/mail/Badge\"; export { value };",
            'import type {\n Value\n} from "@/features/mail/Badge";',
            'export { value } from "@/features/mail/Badge";',
            'export * from "@/features/mail/Badge";',
            'import /* comment */ "@/features/mail/Badge";',
        ]
        forms = [
            variant
            for source in forms
            for variant in (
                source,
                "/* before */ " + source + " // after",
                source.replace("import(", "import /* call */ ("),
            )
        ]
        for source in forms:
            with self.subTest(source=source):
                self.assertEqual(
                    self.ran(
                        ["web/src/features/mail/Badge.tsx"],
                        {"web/src/features/account/Account.tsx": source},
                    ),
                    [g for g, _ in self.GROUPS],
                )
                # The graph itself must retain the edge: an unrelated full fallback cannot
                # mask a missing dynamic import, as it did in the original six tests.
                root = self.tree(
                    {**self.BASE, "web/src/features/account/Account.tsx": source}
                )
                self.assertIn(
                    "web/src/features/account/Account.tsx",
                    ci_plan.Imports(root).web()["web/src/features/mail/Badge.tsx"],
                )

    def test_python_fromlist_arguments_run_every_group(self) -> None:
        forms = [
            "__import__('app.mail', fromlist=['rules'])",
            "__import__('app.mail', None, None, ['rules'])",
            "__import__(name='app.mail', fromlist=('rules',))",
            "__import__('app.mail', fromlist=['rules'], level=0)",
            "import builtins; builtins.__import__('app.mail', fromlist=['rules'])",
            "import builtins as b; b.__import__('app.mail', fromlist=['rules'])",
            "from builtins import __import__ as load; load('app.mail', fromlist=['rules'])",
            "from importlib import import_module as load; load(name='app.mail.rules')",
            "import importlib as il; il.import_module(name='..mail.rules', package='app.documents')",
            "__import__('app.mail.rules', fromlist=[])",
        ]
        for form in forms:
            for source in (form, form.replace("'", '"'), form.replace("; ", "\n")):
                with self.subTest(source=source):
                    root = self.tree(
                        {
                            **self.BASE,
                            "api/app/documents/store.py": source,
                            "api/app/opportunities/use.py": "from app.documents.store import value",
                        }
                    )
                    self.assertEqual(
                        [
                            g
                            for v, g, _ in ci_plan.journey_plan(
                                root, ["api/app/mail/rules.py"], self.GROUPS
                            )
                            if v == "run"
                        ],
                        [g for g, _ in self.GROUPS],
                    )

    def test_python_unsupported_loader_families_force_every_group(self) -> None:
        forms = [
            "names = ['rules']; __import__('app.mail', fromlist=names)",
            "__import__('app.mail', fromlist=['*'])",
            "__import__('app.mail', fromlist=[target])",
            "__import__('app.mail', fromlist={'rules'})",
            "__import__('app.mail', fromlist=('rules', target))",
            "__import__('app.mail', globals(), locals(), ['rules'])",
            "__import__('mail.rules', level=1)",
            "__import__('app.mail', **options)",
            "__import__(*args)",
            "__import__('app.mail', extra=['rules'])",
            "import importlib; getattr(importlib, 'import_module')('app.mail.rules')",
            "import importlib; target = 'app.mail.rules'; getattr(importlib, 'import_module')(target)",
            "import importlib as il; getattr(il, attribute)('app.mail.rules')",
            "import importlib; importlib.__dict__['import_module']('app.mail.rules')",
            "import importlib; vars(importlib)['import_module']('app.mail.rules')",
            "import importlib; alias = importlib; alias.import_module('app.mail.rules')",
            "import importlib; invoke(importlib, 'app.mail.rules')",
            "from importlib import *; import_module('app.mail.rules')",
            "from builtins import *; __import__('app.mail.rules')",
            "import builtins as b; getattr(b, '__import__')('app.mail.rules')",
            "__builtins__['__import__']('app.mail.rules')",
            "__import__('importlib').__dict__['import_module']('app.mail.rules')",
            "import importlib; load = importlib.import_module; load('app.mail.rules')",
            "from importlib import import_module as load; invoke(load, 'app.mail.rules')",
            "import importlib; importlib.import_module(name=target)",
            "import importlib; importlib.import_module('app.mail.rules', **options)",
            "import importlib; importlib.import_module('app.mail.rules', package=package)",
            "import importlib; importlib.import_module('app.mail.rules', extra=target)",
            "import importlib; importlib.import_module('app.mail.missing')",
            "__import__('app.mail.missing', fromlist=['rules'])",
            "import importlib; globals()['importlib'].import_module('app.mail.rules')",
            "from importlib import import_module; locals()['import_module']('app.mail.rules')",
            "from builtins import globals as namespace; namespace()['__builtins__']",
            "eval(\"__import__('app.mail.rules')\")",
            "from builtins import exec as execute; execute(source)",
            "from builtins import __dict__ as namespace; module = namespace['__import__']('app.mail', fromlist=['rules']); value = module.rules.value",
            "from builtins import __dict__; __dict__['__import__']('app.mail.rules')",
            "import sys; getattr(sys.modules['importlib'], 'import_module')('app.mail.rules')",
            "from sys import modules; getattr(modules['importlib'], 'import_module')('app.mail.rules')",
            "from builtins import __dict__ as namespace; module = namespace['__import__']('app.mail', fromlist=['value']); VALUE = module.value.VALUE",
            "from builtins import __dict__; module = __dict__['__import__']('app.mail', fromlist=['value']); VALUE = module.value.VALUE",
            "import sys; getattr(sys.modules['importlib'], 'import_module')('app.mail.value')",
            "from sys import modules; getattr(modules['importlib'], 'import_module')('app.mail.value')",
        ]
        for source in forms:
            with self.subTest(source=source):
                root = self.tree({**self.BASE, "api/app/documents/store.py": source})
                self.assertEqual(
                    [
                        g
                        for v, g, _ in ci_plan.journey_plan(
                            root, ["api/app/mail/rules.py"], self.GROUPS
                        )
                        if v == "run"
                    ],
                    [g for g, _ in self.GROUPS],
                )

    def test_typescript_line_terminator_declaration_matrix_retains_edges(self) -> None:
        declarations = [
            "import '@/features/mail/Badge';",
            "import { value } from '@/features/mail/Badge';",
            "import type { Value } from '@/features/mail/Badge';",
            "export * from '@/features/mail/Badge';",
            "export { value } from '@/features/mail/Badge';",
            "const value = import('@/features/mail/Badge');",
            "const value = import(`@/features/mail/Badge`);",
            "const value = require('@/features/mail/Badge');",
        ]
        # ECMAScript 12.3: exactly LF, CR, LS and PS; CRLF is one sequence.
        for terminator in ("\n", "\r", "\r\n", "\u2028", "\u2029"):
            for declaration in declarations:
                for prefix in ("// comment", "export {}; // 'quoted' comment"):
                    source = prefix + terminator + declaration
                    with self.subTest(terminator=repr(terminator), source=source):
                        self.assertEqual(
                            set(ci_plan.ts_imports(source)), {"@/features/mail/Badge"}
                        )
                        root = self.tree(
                            {
                                **self.BASE,
                                "web/src/features/account/Account.tsx": source,
                            }
                        )
                        self.assertIn(
                            "web/src/features/account/Account.tsx",
                            ci_plan.Imports(root).web()[
                                "web/src/features/mail/Badge.tsx"
                            ],
                        )
                        self.assertEqual(
                            [
                                g
                                for v, g, _ in ci_plan.journey_plan(
                                    root,
                                    ["web/src/features/mail/Badge.tsx"],
                                    self.GROUPS,
                                )
                                if v == "run"
                            ],
                            [g for g, _ in self.GROUPS],
                        )
        for whitespace in ("\v", "\f", "\u0085", "\u00a0", "\u2003"):
            for declaration in declarations:
                with self.subTest(whitespace=repr(whitespace), declaration=declaration):
                    self.assertEqual(
                        ci_plan.ts_imports("// comment" + whitespace + declaration), []
                    )

    def test_runtime_fixture_paths_and_neighbors_run_journeys(self) -> None:
        cases = {
            "api/tests/jobs/fake_ats.py": [g for g, _ in self.GROUPS],
            "api/tests/mail/fake_gmail.py": ["mail"],
            "api/tests/mail/fixtures/mailboxes/new.json": ["mail"],
            "api/tests/mail/test_sync.py": ["mail"],
            "api/tests/ingestion/journey_streams.py": [g for g, _ in self.GROUPS],
            "api/tests/ingestion/fixtures/new.txt": [g for g, _ in self.GROUPS],
            "api/tests/documents/fixtures/upload_morgan.txt": [
                g for g, _ in self.GROUPS
            ],
            "api/tests/documents/fixtures/new/nested.txt": [g for g, _ in self.GROUPS],
            "api/tests/jobs/fake_ats_test.py": [],
            "api/tests/mailbox/test_sync.py": [],
            "api/tests/ingestion_extra/fixture.txt": [],
            "api/tests/documents/test_store.py": [],
            "api/tests/documents/fixtures_extra/input.txt": [],
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(self.ran([path]), expected)

    def test_journey_runtime_test_data_has_nonempty_scope(self) -> None:
        # Read paths, never import API code or open the data. Include directory contents
        # because a mounted fake can read a new sibling without changing its compose file.
        paths = set()
        gate = (ROOT / "ops/local-check.sh").read_text()
        compose = {
            ROOT / p for p in re.findall(r"(?:tests/)?[\w.-]*compose[\w.-]*\.yml", gate)
        }
        for source in compose:
            paths.update(
                re.findall(
                    r"^\s*-\s+['\"]?\./(api/tests(?:/[\w./*-]+)?):",
                    source.read_text(),
                    re.MULTILINE,
                )
            )
        for source in (ROOT / "web/e2e").glob("*.ts"):
            for relative in re.findall(r"api/tests/[\w./*-]+", source.read_text()):
                paths.add(relative.rstrip("/."))
        self.assertTrue(compose)
        self.assertIn("api/tests/jobs/fake_ats.py", paths)
        self.assertIn("api/tests/mail", paths)
        self.assertIn("api/tests/ingestion/fixtures", paths)
        self.assertIn("api/tests/documents/fixtures/upload_morgan.txt", paths)
        for relative in sorted(paths):
            target = ROOT / relative
            expanded = (
                list(target.rglob("*"))
                if target.is_dir()
                else list(ROOT.glob(relative))
            )
            self.assertTrue(expanded, relative)
            for path in expanded:
                if path.is_file():
                    relative_path = path.relative_to(ROOT).as_posix()
                    groups, _ = ci_plan.journey_groups_for(
                        relative_path, {}, {}, ci_plan.Imports(ROOT)
                    )
                    self.assertNotEqual(
                        groups, ci_plan.NO_GROUP, f"runtime data: {relative_path}"
                    )

    def test_registration_providers_and_their_dependencies_run_every_group(
        self,
    ) -> None:
        for stem in ("agents", "engine", "freeze", "jobs", "writers"):
            for suffix in (".py", "/__init__.py", "/nested.py"):
                path = f"api/app/documents/{stem}{suffix}"
                with self.subTest(path=path):
                    files = {path: "from app.mail.rules import value"}
                    self.assertEqual(
                        self.ran([path], files), [g for g, _ in self.GROUPS]
                    )
                    self.assertEqual(
                        self.ran(["api/app/mail/rules.py"], files),
                        [g for g, _ in self.GROUPS],
                    )
        # The worker imports <module>.freeze, not a neighbour whose name starts the same.
        for path in ("api/app/documents/freeze_extra.py", "api/app/documents/store.py"):
            with self.subTest(path=path):
                self.assertEqual(self.ran([path], {path: ""}), ["documents", "apply"])

    def test_worker_registration_registry_and_status_callbacks_have_full_scope(
        self,
    ) -> None:
        tree = ast.parse((ROOT / "api/app/worker.py").read_text())
        registries = [
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "REGISTRATION_MODULES"
                for t in n.targets
            )
        ]
        self.assertEqual(len(registries), 1)
        registered = frozenset(ast.literal_eval(registries[0]))
        self.assertEqual(
            registered,
            frozenset({"agents", "engine", "freeze", "jobs", "writers"}),
        )
        providers = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "api/app").rglob("*.py")
            if path.stem in registered
            or path.name == "__init__.py"
            and path.parent.name in registered
        }
        self.assertIn("api/app/documents/writers.py", providers)
        callbacks = []
        for path in (ROOT / "api/app").rglob("*.py"):
            tree = ast.parse(path.read_text())
            if any(
                isinstance(n, ast.Call)
                and (
                    isinstance(n.func, ast.Name)
                    and n.func.id == "register_status_callback"
                    or isinstance(n.func, ast.Attribute)
                    and n.func.attr == "register_status_callback"
                )
                for n in ast.walk(tree)
            ):
                callbacks.append(path.relative_to(ROOT).as_posix())
        self.assertIn("api/app/documents/freeze.py", callbacks)
        for path in (providers | set(callbacks)) - set(ci_plan.SCOPED_REGISTRATIONS):
            # The policy covers registry and callback providers without dependency inference.
            with mock.patch.object(
                ci_plan.Imports,
                "api",
                create=True,
                side_effect=AssertionError("unexpected graph read"),
            ):
                groups, _ = ci_plan.journey_groups_for(
                    path, {}, {}, ci_plan.Imports(ROOT)
                )
            self.assertEqual(groups, ci_plan.EVERY, path)

    def test_typescript_unknown_targets_and_broken_imports_force_every_group(
        self,
    ) -> None:
        for source in [
            "import(target)",
            "import(`@/features/${target}`)",
            'import("@/features/" + target)',
            'import { value } from "@/features/missing/Value"',
            "import { value } from target",
            "import { value",
            'import "unterminated',
            "/* unterminated",
            "const value = require(target)",
            'const value = import.meta.glob("@/features/mail/*")',
            'const value = require.resolve("@/features/mail/Badge")',
            'const value = `${import("@/features/mail/Badge")}`',
        ]:
            with self.subTest(source=source):
                self.assertEqual(
                    self.ran(
                        ["web/src/features/mail/Row.tsx"],
                        {"web/src/features/account/Account.tsx": source},
                    ),
                    [g for g, _ in self.GROUPS],
                )

    def test_graph_read_decode_parse_and_traversal_errors_force_full(self) -> None:
        root = self.tree(self.BASE)
        for target, changed in [
            ("api/app/documents/store.py", "api/app/mail/rules.py"),
            ("web/src/features/account/Account.tsx", "web/src/features/mail/Row.tsx"),
        ]:
            original = Path.read_text
            for error in [
                OSError("unreadable"),
                UnicodeDecodeError("utf8", b"\xff", 0, 1, "invalid"),
            ]:

                def read(
                    path, *args, _target=target, _error=error, _read=original, **kwargs
                ):
                    if path == root / _target:
                        raise _error
                    return _read(path, *args, **kwargs)

                with (
                    self.subTest(target=target, error=error),
                    mock.patch.object(Path, "read_text", read),
                ):
                    self.assertTrue(
                        all(
                            v == "run"
                            for v, _, _ in ci_plan.journey_plan(
                                root, [changed], self.GROUPS
                            )
                        )
                    )
        self.assertEqual(
            self.ran(
                ["api/app/mail/rules.py"],
                {"api/app/documents/store.py": "from broken syntax"},
            ),
            [g for g, _ in self.GROUPS],
        )
        for changed in ["api/app/mail/rules.py", "web/src/features/mail/Row.tsx"]:
            with (
                self.subTest(changed=changed),
                mock.patch.object(
                    os, "scandir", side_effect=OSError("cannot traverse")
                ),
            ):
                self.assertTrue(
                    all(
                        v == "run"
                        for v, _, _ in ci_plan.journey_plan(
                            root, [changed], self.GROUPS
                        )
                    )
                )

    def test_change_digest_distinguishes_path_boundaries(self) -> None:
        self.assertNotEqual(
            ci_plan.changes_digest(["a\nb"]), ci_plan.changes_digest(["a", "b"])
        )
        self.assertEqual(
            ci_plan.changes_digest(["b", "a"]), ci_plan.changes_digest(["a", "b"])
        )
        self.assertEqual(
            ci_plan.changes_digest([os.fsdecode(b"raw-\xff")]),
            hashlib.sha256(b"raw-\xff\0").hexdigest(),
        )

    def test_typescript_literal_json_and_transitive_controls(self) -> None:
        files = {
            "web/src/features/mail/value.json": "{}",
            "web/src/features/documents/Use.tsx": 'import value from "@/features/mail/value.json";',
            "web/src/features/apply/Use.tsx": 'import "@/features/documents/Use";',
        }
        self.assertEqual(
            self.ran(["web/src/features/mail/value.json"], files),
            ["mail", "documents", "apply"],
        )

    def test_cli_tracks_actual_additions_deletions_and_renames(self) -> None:
        for path in ["api/app/mail/rules.py", "web/src/features/mail/Badge.tsx"]:
            for operation in ["add", "delete", "rename"]:
                with self.subTest(path=path, operation=operation):
                    root = self.tree(
                        {
                            **self.BASE,
                            ".github/scripts/ci_plan.py": SCRIPT.read_text(),
                            ".github/scripts/e2e_groups.py": (
                                SCRIPT.parent / "e2e_groups.py"
                            ).read_text(),
                        }
                    )
                    git(root, "init", "-q", "-b", "main")
                    git(root, "add", "-A")
                    git(root, "commit", "-q", "-m", "base")
                    base = git(root, "rev-parse", "HEAD")
                    source = root / path
                    if operation == "delete":
                        source.unlink()
                    elif operation == "rename":
                        source.rename(source.with_name("renamed" + source.suffix))
                    else:
                        source.with_name("added" + source.suffix).write_text("")
                    git(root, "add", "-A")
                    git(root, "commit", "-q", "-m", operation)
                    done = subprocess.run(
                        [
                            sys.executable,
                            str(root / ".github/scripts/ci_plan.py"),
                            "journeys",
                            base,
                            "HEAD",
                        ],
                        input="".join(f"{g}\t{a}\n" for g, a in self.GROUPS),
                        capture_output=True,
                        text=True,
                        check=False,
                        env=CLEAN_ENV,
                    )
                    self.assertEqual(done.returncode, 0, done.stderr)
                    rows = [row.split("\t") for row in done.stdout.splitlines()]
                    self.assertEqual(int(rows[0][1]), 2 if operation == "rename" else 1)
                    expected = (
                        ["mail"] if operation == "add" else [g for g, _ in self.GROUPS]
                    )
                    self.assertEqual(
                        [row[1] for row in rows[1:] if row[0] == "run"], expected
                    )

    def test_cli_preserves_raw_git_pathnames(self) -> None:
        root = self.tree(
            {
                **self.BASE,
                ".github/scripts/ci_plan.py": SCRIPT.read_text(),
                ".github/scripts/e2e_groups.py": (
                    SCRIPT.parent / "e2e_groups.py"
                ).read_text(),
            }
        )
        git(root, "init", "-q", "-b", "main")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "base")
        base = git(root, "rev-parse", "HEAD")
        paths = ['docs/new\nline\t"quoted".md', "docs/unicode-é.md"]
        for path in paths:
            (root / path).parent.mkdir(exist_ok=True)
            (root / path).write_text("fixture")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "unusual paths")
        done = subprocess.run(
            [
                sys.executable,
                str(root / ".github/scripts/ci_plan.py"),
                "journeys",
                base,
                "HEAD",
            ],
            input="",
            capture_output=True,
            text=True,
            check=False,
            env=CLEAN_ENV,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout.strip(), f"changed\t2\t{ci_plan.changes_digest(paths)}"
        )

    def test_shared_or_unknown_paths_run_every_group(self) -> None:
        every = [g for g, _ in self.GROUPS]
        for path in [
            "api/app/db.py",
            "api/app/main.py",
            "api/alembic/versions/0022_x.py",
            "api/uv.lock",
            "web/pnpm-lock.yaml",
            "web/src/lib/api.ts",
            "web/src/routes.tsx",
            "web/e2e/apply-helpers.ts",
            "web/e2e/stack.ts",
            "web/playwright.config.ts",
            "compose.yml",
            "api/Dockerfile",
            "ops/local-check.sh",
            "ops/ship.sh",
            ".github/scripts/ci_plan.py",
            "api/app/newmodule/thing.py",
            "somewhere/new.txt",
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.ran([path]), every)
                self.assertTrue(
                    all(
                        v.startswith(f"run: every group, {path}: ")
                        for v in self.plan([path]).values()
                    )
                )
        # One shared file among module files: every group.
        self.assertEqual(self.ran(["api/app/mail/sync.py", "compose.yml"]), every)

    def test_the_command_prints_the_change_set_and_one_line_per_group(self) -> None:
        for path in ("api/app/mail/sync.py", "web/src/features/mail/Row.tsx"):
            root = self.tree(
                {
                    **self.BASE,
                    ".github/scripts/ci_plan.py": SCRIPT.read_text(),
                    ".github/scripts/e2e_groups.py": (
                        SCRIPT.parent / "e2e_groups.py"
                    ).read_text(),
                }
            )
            git(root, "init", "-q", "-b", "main")
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "base")
            base = git(root, "rev-parse", "HEAD")
            (root / path).write_text("x = 1\n")
            (root / "README.md").write_text("read me\n")
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "change")
            done = subprocess.run(
                [
                    sys.executable,
                    str(root / ".github/scripts/ci_plan.py"),
                    "journeys",
                    base,
                    "HEAD",
                ],
                input="".join(f"{g}\t{a}\n" for g, a in self.GROUPS),
                capture_output=True,
                text=True,
                check=False,
                env=CLEAN_ENV,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            lines = done.stdout.splitlines()
            self.assertEqual(
                lines[0],
                f"changed\t2\t{ci_plan.changes_digest([path, 'README.md'])}",
            )
            self.assertEqual(
                lines[1:],
                [
                    "skip\tapps\tno changed file touches it",
                    f"run\tmail\t{path}: the {'api ' if path.startswith('api/') else ''}module mail",
                    "skip\tdocuments\tno changed file touches it",
                    "skip\tapply\tno changed file touches it",
                    "skip\tsweep\tno changed file touches it",
                ],
            )
            bad = subprocess.run(
                [
                    sys.executable,
                    str(root / ".github/scripts/ci_plan.py"),
                    "journeys",
                    "nope",
                    "HEAD",
                ],
                input="",
                capture_output=True,
                text=True,
                check=False,
                env=CLEAN_ENV,
            )
            self.assertEqual(bad.returncode, 1)

    def test_this_repository_decides_every_module_and_names_real_groups(self) -> None:
        modules = {p.name for p in (ROOT / "web/src/features").iterdir() if p.is_dir()}
        self.assertEqual(
            sorted(modules - set(ci_plan.AREA_GROUPS)),
            [],
            "a module with no journey groups decided",
        )
        groups = {"apps", "sweep"} | {
            p.name.removesuffix("-all.config.ts")
            for p in (ROOT / "web/e2e").glob("*-all.config.ts")
        }
        named = set()
        for decided in list(ci_plan.AREA_GROUPS.values()) + [
            g for _, g, _ in ci_plan.JOURNEY_RULES
        ]:
            if decided != ci_plan.EVERY:
                named |= decided
        self.assertEqual(
            sorted(named - groups), [], "a rule names a group that does not exist"
        )
        # The local check's own groups, as it would pass them.
        plan = ci_plan.journey_plan(
            ROOT,
            ["api/app/mail/parse.py"],
            [
                (g, f"-c e2e/{g}-all.config.ts")
                for g in sorted(groups - {"apps", "sweep"})
            ],
        )
        self.assertEqual([g for v, g, _ in plan if v == "run"], ["mail"])
        for _, decided, _ in ci_plan.API_AREAS:
            if decided != ci_plan.EVERY:
                self.assertEqual(sorted(decided - groups), [], decided)
        modules = {
            p.name
            for p in (ROOT / "api/app").iterdir()
            if (p / "__init__.py").is_file()
        }
        decided = {pattern.split("/")[2] for pattern, _, _ in ci_plan.API_AREAS}
        self.assertEqual(sorted(modules - decided), [], "an api module with no groups")

    API_PINS: ClassVar[dict[str, frozenset[str] | str]] = {
        # Narrowed: an apply submit, the browser registry, a sandbox handler, a mail parser,
        # the deck, an apply agent's fake answers. (A browser source follows its finder, see
        # test_a_browser_source_runs_what_its_finder_runs.)
        "api/app/apply/submit.py": frozenset({"apply", "browser", "opportunities"}),
        "api/app/browser/registry.py": frozenset({"apply", "browser"}),
        "api/app/opportunities/deck.py": frozenset({"opportunities"}),
        "api/app/ingestion/sandbox/pdf_text.py": frozenset(
            {"documents", "ingestion", "opportunities"}
        ),
        "api/app/mail/parse.py": frozenset({"mail"}),
        "api/app/agents/fake_streams/apply_form.json": frozenset(
            {"apply", "browser", "opportunities"}
        ),
        # Imported by ingestion/routers.py, whose middleware main.py runs on every request,
        # but not by the middleware's own code: the module's groups.
        "api/app/ingestion/prompt_router.py": frozenset(
            {"documents", "ingestion", "opportunities"}
        ),
        # The routes a shared web page calls (WEB_SHARED_PAGES): the /account cards, the queue
        # count of the shell, the job page's apply and documents sections, the profile's
        # Search tab, and what imports them (the apply agents through approval_router.py).
        "api/app/browser/router.py": ci_plan.EVERY,
        "api/app/browser/live_router.py": ci_plan.EVERY,
        "api/app/mail/account_router.py": ci_plan.EVERY,
        "api/app/apply/queue_router.py": ci_plan.EVERY,
        "api/app/apply/router.py": ci_plan.EVERY,
        "api/app/apply/agent.py": ci_plan.EVERY,
        "api/app/apply/agents.py": ci_plan.EVERY,
        "api/app/apply/failures.py": ci_plan.EVERY,
        "api/app/documents/router.py": ci_plan.EVERY,
        "api/app/opportunities/feeds_router.py": ci_plan.EVERY,
        "api/app/opportunities/starter_router.py": ci_plan.EVERY,
        # main.py runs their middleware and the 500 handler uses them; the start builds the
        # mail client (accounts.py), which reaches google.py.
        "api/app/mail/routers.py": ci_plan.EVERY,
        "api/app/ingestion/routers.py": ci_plan.EVERY,
        "api/app/mail/accounts.py": ci_plan.EVERY,
        "api/app/mail/google.py": ci_plan.EVERY,
        # Shared core and what every journey passes through.
        "api/app/main.py": ci_plan.EVERY,
        "api/app/worker.py": ci_plan.EVERY,
        "api/app/config.py": ci_plan.EVERY,
        "api/app/apply/models.py": ci_plan.EVERY,
        "api/app/opportunities/onboarding.py": ci_plan.EVERY,
        "api/app/opportunities/pay.py": ci_plan.EVERY,  # jobs' ATS reader imports it
        "api/app/jobs/sweep.py": ci_plan.EVERY,
        "api/app/documents/writers.py": ci_plan.EVERY,
        "api/app/documents/freeze.py": ci_plan.EVERY,
        "api/app/agents/fake_streams/echo.json": ci_plan.EVERY,
    }

    def test_api_files_of_this_repository_run_the_groups_that_reach_them(self) -> None:
        imports = ci_plan.Imports(ROOT)
        self.assertGreater(sum(len(v) for v in imports.api().values()), 1000)
        for path, expected in self.API_PINS.items():
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file())
                groups, why = ci_plan.journey_groups_for(path, {}, {}, imports)
                self.assertEqual(groups, expected, why)

    def test_a_browser_source_runs_what_its_finder_runs(self) -> None:
        # Every sweep runs the sources, so a source plans at least the sweep's groups, and it
        # plans every group as soon as a file every journey uses imports the finder (a lazy
        # import of profile/schemas.py, imported by agents/tools.py, does).
        imports = ci_plan.Imports(ROOT)
        finder, _ = ci_plan.journey_groups_for(
            "api/app/browser/finder.py", {}, {}, imports
        )
        sources = sorted((ROOT / "api/app/browser/sources").glob("*.py"))
        self.assertGreater(len(sources), 2)
        for source in sources:
            path = str(source.relative_to(ROOT))
            with self.subTest(path=path):
                groups, why = ci_plan.journey_groups_for(path, {}, {}, imports)
                self.assertEqual(groups, finder, why)
                if groups != ci_plan.EVERY:
                    self.assertLessEqual(
                        {"apply", "browser", "opportunities", "sweep"}, groups
                    )

    def test_what_the_model_backend_imports_runs_every_group(self) -> None:
        # api/app/agents/backend.py sets the model credential of every journey: no file it
        # reaches, directly or not, may be narrowed.
        imports = ci_plan.Imports(ROOT)
        graph = imports.api()
        backend = "api/app/agents/backend.py"
        reached = [
            path
            for path in sorted(graph)
            if backend
            in ci_plan.importers(path, graph, lambda name: name in ci_plan.API_WIRING)
        ]
        self.assertIn("api/app/agents/tools.py", reached)
        for path in reached:
            with self.subTest(path=path):
                groups, why = ci_plan.journey_groups_for(path, {}, {}, imports)
                self.assertEqual(groups, ci_plan.EVERY, why)

    def test_images_run_the_groups_whose_stacks_start_them(self) -> None:
        # Only the stacks that set COMPOSE_PROFILES=browser start the browser service.
        gate = (ROOT / "ops/local-check.sh").read_text()
        profiled = set()
        for case in re.finditer(r"\n  ([a-z]+)\)\n(.*?)\n    ;;", gate, re.DOTALL):
            if "export COMPOSE_PROFILES=browser" in case.group(2):
                profiled.add(case.group(1))
        self.assertEqual(profiled, {"apply", "browser"})
        compose = (ROOT / "compose.yml").read_text()
        self.assertRegex(
            compose, r"\n  browser:\n(?:    .*\n)*?    profiles: \[browser\]"
        )
        for path, expected in {
            "browser/Dockerfile": frozenset(profiled),
            "renderer/Dockerfile": frozenset({"documents", "apply"}),
            "api/Dockerfile": ci_plan.EVERY,
            "web/Dockerfile": ci_plan.EVERY,
        }.items():
            with self.subTest(path=path):
                groups, _ = ci_plan.journey_groups_for(
                    path, {}, {}, ci_plan.Imports(ROOT)
                )
                self.assertEqual(groups, expected)

    def test_the_reviewed_loaders_and_registrations_hold(self) -> None:
        for path, constant in ci_plan.API_LOADERS.items():
            with self.subTest(path=path):
                self.assertIn(constant, (ROOT / path).read_text())
        # The scoped registration registers internal agents only (GET /api/agents lists none):
        # each register call of its files builds a definition that says internal=True.
        for path in ci_plan.SCOPED_REGISTRATIONS:
            for name in ("agents.py", "form_agent.py", "model_check.py"):
                text = (ROOT / path).with_name(name).read_text()
                tree = ast.parse(text)
                calls = [
                    n
                    for n in ast.walk(tree)
                    if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "register"
                    and getattr(n.func.value, "id", "") == "definitions"
                ]
                self.assertEqual(len(calls), 1, name)
                for call in calls:
                    self.assertTrue(ci_plan.registers_internal(call, tree), name)
        hook = "from app.documents import hooks\nhooks.register_check(print)\n"
        cases = {
            # A hook makes its module a consumer of the file that registers the callback.
            "api/app/documents/hooks.py": (
                "def register_check(f):\n    pass\n",
                ["mail", "documents", "apply"],
            ),
            "api/app/documents/registry.py": (
                "import importlib\nNAMES = ('rules',)\nimportlib.import_module('app.mail.rules')\n",
                [g for g, _ in self.GROUPS],
            ),
        }
        for path, (text, expected) in cases.items():
            with self.subTest(path=path):
                files = {
                    path: text,
                    "api/app/mail/rules.py": hook if "hooks" in path else "",
                }
                self.assertEqual(self.ran(["api/app/mail/rules.py"], files), expected)
        everything = [g for g, _ in self.GROUPS]
        for source in [
            "thing.register_check(print)",  # an object the reader cannot name
            "register_check(print)",  # a function nobody imported
            "from app.agents import definitions\ndefinitions.register(make())",  # a listed agent
            # A listed agent beside an unrelated internal=True, or one whose builder says False.
            (
                "from app.agents import definitions\ndefinitions.register(make())\n"
                "OTHER = thing(internal=True)\n"
            ),
            (
                "from app.agents import definitions\n"
                "def _definition():\n    return make(internal=False)\n"
                "definitions.register(_definition())\n"
            ),
            (
                "from app.agents import definitions\n"
                "def _definition():\n    return make(internal=True)\n"
                "definitions.register(_definition(), extra=1)\n"
            ),
        ]:
            with self.subTest(source=source):
                files = {
                    "api/app/mail/rules.py": source,
                    "api/app/agents/__init__.py": "",
                    "api/app/agents/definitions.py": "",
                }
                self.assertEqual(self.ran(["api/app/mail/rules.py"], files), everything)
        files = {
            "api/app/mail/rules.py": "from app.agents import definitions\n"
            "def _definition():\n    return make(internal=True)\n"
            "ANSWER = definitions.register(_definition())\n"
            "definitions.register(make(internal=True))\n",
            "api/app/agents/__init__.py": "",
            "api/app/agents/definitions.py": "",
        }
        self.assertEqual(
            self.ran(["api/app/mail/rules.py"], files), ["mail", "documents", "apply"]
        )
        # A reviewed loader that names another package, or names computed at run time.
        with mock.patch.dict(
            ci_plan.API_LOADERS, {"api/app/documents/store.py": "NAMES"}
        ):
            for source in (
                "NAMES = ('x',)\nPACKAGE = 'app.mail'\n",
                "NAMES = tuple(sorted(['x']))\n",
                "",
            ):
                with self.subTest(loader=source):
                    self.assertEqual(
                        self.ran(
                            ["api/app/mail/sync.py"],
                            {"api/app/documents/store.py": source},
                        ),
                        everything,
                    )

    def test_routes_of_a_shared_page_and_main_middleware_run_every_group(self) -> None:
        everything = [g for g, _ in self.GROUPS]
        shell = "web/src/features/shell/app-shell.tsx"
        router = (
            "from fastapi import APIRouter\n"
            'router = APIRouter(prefix="/api/mail")\n'
            '@router.get("/thing/{thing_id}/state")\n'
            "def state():\n    pass\n"
        )
        base = {"api/app/mail/thing_router.py": router}
        card = {
            shell: 'import { Card } from "@/features/mail/card";\n',
            "web/src/features/mail/card.tsx": 'import { load } from "./mail-api";\n',
        }
        cases = {
            # The api file serving a request of a shared page, through the files it imports.
            "template": ("request(`/mail/thing/${id}/state`)", everything),
            "constant": (
                'const BASE = "/mail/thing";\nrequest(`${BASE}/${id}/state`)',
                everything,
            ),
            "full path": ('fetch("/api/mail/thing/1/state")', everything),
            "query": ("request(`/mail/thing/${id}/state?full=1`)", everything),
            # A link or a navigation is a page, and another route is another file.
            "link": ('<Link to="/mail/thing/1/state" />', ["mail"]),
            "navigate": ('navigate("/mail/thing/1/state")', ["mail"]),
            "other route": ('request("/mail/thing/1")', ["mail"]),
        }
        for name, (code, expected) in cases.items():
            with self.subTest(name=name):
                files = {**base, **card, "web/src/features/mail/mail-api.ts": code}
                self.assertEqual(
                    self.ran(["api/app/mail/thing_router.py"], files), expected
                )
        # The same call from a page no group shares, or from a test file, narrows.
        files = {
            **base,
            "web/src/features/mail/Mail.tsx": 'request("/mail/thing/1/state")\n',
            "web/src/features/mail/card.test.tsx": 'request("/mail/thing/1/state")\n',
            shell: 'import { x } from "@/features/mail/card.test";\n',
        }
        self.assertEqual(self.ran(["api/app/mail/thing_router.py"], files), ["mail"])
        # What the reader cannot place fails closed: a missing shared page, an unresolved
        # import of one, a route declared on an unknown router or with a computed path.
        for files in (
            {**base, shell: 'import { x } from "./gone";\n'},
            {**base, "api/app/mail/odd.py": '@other.get("/x")\ndef f():\n    pass\n'},
            {
                **base,
                "api/app/mail/odd.py": "from fastapi import APIRouter\n"
                "router = APIRouter()\n@router.get(PATH)\ndef f():\n    pass\n",
            },
        ):
            with self.subTest(files=sorted(files)):
                self.assertEqual(
                    self.ran(["api/app/mail/thing_router.py"], files), everything
                )
        root = self.tree({p: t for p, t in self.BASE.items() if p != shell})
        self.assertEqual(
            [
                g
                for v, g, _ in ci_plan.journey_plan(
                    root, ["api/app/mail/rules.py"], self.GROUPS
                )
                if v == "run"
            ],
            everything,
        )
        # main.py: a middleware or a handler of a module runs every group, and what its code
        # reaches; the routers its module lists for app.include_router stay the module's.
        main = (
            "from app.mail import web as mail_web\n"
            "from app.mail import hub\n"
            "app.middleware('http')(mail_web.no_store)\n"
            "for r in mail_web.load():\n    app.include_router(r)\n"
            "hub.include(app)\n"
        )
        routers = (
            "from app.mail import headers, thing_router\n"
            "ROUTERS = (thing_router.router,)\n"
            "def load():\n    return ROUTERS\n"
            "async def no_store(request, call_next):\n"
            "    return headers.extra(await call_next(request))\n"
        )
        files = {
            **base,
            "api/app/main.py": main,
            "api/app/mail/web.py": routers,
            "api/app/mail/headers.py": "from app.mail import parse\n",
            "api/app/mail/parse.py": "",
            "api/app/mail/hub.py": "",
        }
        for path, expected in {
            "api/app/mail/web.py": everything,
            "api/app/mail/headers.py": everything,
            "api/app/mail/parse.py": everything,  # headers.py imports it
            "api/app/mail/thing_router.py": ["mail"],
            "api/app/mail/hub.py": ["mail"],
        }.items():
            with self.subTest(path=path):
                self.assertEqual(self.ran([path], files), expected)


if __name__ == "__main__":
    unittest.main()
