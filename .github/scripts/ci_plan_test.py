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
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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

    def test_the_readme_runs_the_shell_unit_alone(self) -> None:
        # tests/deploy_test.sh checks statements of the README.
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
        README.md, and the README's rule runs the shell unit; a workflow belongs to every
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
            "checks": ("e2e_apps",),
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
        """A `paths` or `branches` filter on pull_request would skip the whole run for some
        pull requests, with every check a merge waits for; `types` with `edited` would start
        and cancel runs on every edit of a title. What narrows a run is the plan, inside it."""
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
                "  pull_request:",
                "  push:",
                "    branches: [main]",
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
        self.assertRegex(
            scan["run"],
            r"^uvx --from pre-commit==[\d.]+ pre-commit run gitleaks-commits "
            r"--hook-stage manual$",
        )
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
        self.assertEqual(seen, 1, "the teardown between identity and sweep")


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


if __name__ == "__main__":
    unittest.main()
