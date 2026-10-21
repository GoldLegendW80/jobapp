#!/usr/bin/env python3
"""Tests of the pure part of ops/board-sync.py (issue #500) on fixtures: no network, no gh, no git.

Run: python3 tests/board_sync_test.py (tests/dev_setup_test.sh runs it, so the gate does too).
"""

from __future__ import annotations

import csv
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "ops" / "board-sync.py"
spec = importlib.util.spec_from_file_location("board_sync", SCRIPT)
bs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bs)

HEAD = "a" * 40
OLD = "b" * 40
MERGE = "c" * 40


def review(body: str, commit: str = HEAD) -> dict:
    return {
        "body": body,
        "submittedAt": "2026-10-20T10:00:00Z",
        "commit": {"oid": commit},
    }


def pr(number: int, state: str = "OPEN", **kw) -> dict:
    base = {
        "id": f"PR_{number}",
        "number": number,
        "title": kw.pop("title", f"platform: pull request {number}"),
        "state": state,
        "isDraft": kw.pop("draft", False),
        "createdAt": "2026-10-18T09:00:00Z",
        "mergedAt": kw.pop(
            "merged_at", "2026-10-19T09:00:00Z" if state == "MERGED" else None
        ),
        "closedAt": "2026-10-19T09:00:00Z" if state != "OPEN" else None,
        "url": f"https://github.com/example/repo/pull/{number}",
        "body": kw.pop("body", ""),
        "headRefOid": HEAD,
        "mergeCommit": {"oid": kw.pop("merge_commit", MERGE)}
        if state == "MERGED"
        else None,
        "closingIssuesReferences": {
            "nodes": [{"number": n} for n in kw.pop("closes", [])]
        },
        "reviews": {"nodes": kw.pop("reviews", [])},
    }
    assert not kw, kw
    return base


def issue(number: int, state: str = "OPEN", **kw) -> dict:
    base = {
        "id": f"I_{number}",
        "number": number,
        "title": kw.pop("title", f"platform: task {number}"),
        "state": state,
        "stateReason": kw.pop("reason", "COMPLETED" if state == "CLOSED" else None),
        "createdAt": kw.pop("created", "2026-10-17T08:00:00Z"),
        "closedAt": kw.pop(
            "closed", "2026-10-19T09:00:00Z" if state == "CLOSED" else None
        ),
        "url": f"https://github.com/example/repo/issues/{number}",
        "body": kw.pop("body", ""),
        "labels": {"nodes": [{"name": n} for n in kw.pop("labels", [])]},
        "closedByPullRequestsReferences": {
            "nodes": [{"number": n} for n in kw.pop("closed_by", [])]
        },
    }
    assert not kw, kw
    return base


def item(
    item_id: str, kind: str, number: int, status=None, module=None, archived=False
) -> dict:
    return {
        "id": item_id,
        "isArchived": archived,
        "content": {"__typename": kind, "number": number},
        "status": {"name": status} if status else None,
        "module": {"name": module} if module else None,
    }


# The columns of meta.csv, which the dashboard reads.
META_COLUMNS = "generated deployed_commit deployed_at main_commit tasks live in_flight waiting_owner open"

APPROVED = [review("REVIEW VERDICT: APPROVE\nHead: " + HEAD)]


class ModuleOf(unittest.TestCase):
    def test_title_rules(self):
        cases = {
            "O4 profile fields": "opportunities",
            "onboarding: first run": "opportunities",
            "A5 real run": "apply",
            "Browser: egress": "browser",
            "D12 letter": "documents",
            "M3 inbox": "mail",
            "G2 parser": "ingestion",
            "P7 transcribe": "ingestion",
            "J1 sweep": "jobs",
            "Chat prompt cache": "agents",
            "Profile: history": "profile",
            "Sign in with a link": "identity",
            "ship.sh pinned head": "platform",
            "T12 flaky": "platform",
            "local-check: disk floor": "platform",
        }
        for title, module in cases.items():
            self.assertEqual(bs.module_of(title, set(), None), module, title)

    def test_label_then_previous_then_platform(self):
        self.assertEqual(bs.module_of("Board columns", {"lane:mail"}, "jobs"), "mail")
        self.assertEqual(bs.module_of("Board columns", {"bug"}, "jobs"), "jobs")
        self.assertEqual(bs.module_of("Board columns", set(), None), "platform")

    def test_title_wins_over_label(self):
        self.assertEqual(bs.module_of("A7 retry", {"lane:mail"}, None), "apply")

    def test_every_rule_names_a_board_module(self):
        names = {m for m, _ in bs.MODULES}
        self.assertTrue({m for _, m in bs.MODULE_RULES} <= names)
        self.assertTrue(set(bs.LANE_MODULE.values()) <= names)


class DependsOn(unittest.TestCase):
    def test_lines_that_begin_with_depends_or_stacked(self):
        body = "Depends on #12 and #13\n- stacked on #5\n> Depends on: #7\nCloses #9"
        self.assertEqual(bs.depends_on(body), {12, 13, 5, 7})

    def test_mid_line_and_empty(self):
        self.assertEqual(bs.depends_on("this no longer depends on #3"), set())
        self.assertEqual(bs.depends_on(None), set())
        self.assertEqual(bs.depends_on(""), set())


class Verdict(unittest.TestCase):
    def test_latest_verdict_wins_other_reviews_ignored(self):
        p = pr(
            1,
            reviews=[
                review("REVIEW VERDICT: REQUEST CHANGES\nHead: x", OLD),
                review("REVIEW VERDICT: APPROVE\nHead: " + HEAD, HEAD),
                review("a plain comment", OLD),
            ],
        )
        self.assertEqual(bs.verdict(p), ("APPROVE", HEAD))

    def test_none_and_missing_commit(self):
        self.assertEqual(bs.verdict(pr(1)), ("", ""))
        p = pr(
            1, reviews=[{"body": "  REVIEW VERDICT: REQUEST CHANGES", "commit": None}]
        )
        self.assertEqual(bs.verdict(p), ("REQUEST CHANGES", ""))


class PrStage(unittest.TestCase):
    def stage(self, p, gating=(), live=()):
        return bs.pr_stage(p, set(gating), set(live))

    def test_each_stage(self):
        self.assertEqual(self.stage(pr(1, "MERGED"), live={MERGE}), "Live")
        self.assertEqual(self.stage(pr(1, "MERGED")), "Merged")
        self.assertEqual(self.stage(pr(1, "CLOSED")), "Dropped")
        self.assertEqual(self.stage(pr(1, reviews=APPROVED), gating={1}), "Gating")
        self.assertEqual(self.stage(pr(1, draft=True)), "Building")
        self.assertEqual(self.stage(pr(1)), "In review")
        self.assertEqual(self.stage(pr(1, reviews=APPROVED)), "Approved")
        old = [review("REVIEW VERDICT: APPROVE\nHead: " + OLD, OLD)]
        self.assertEqual(self.stage(pr(1, reviews=old)), "Fix & re-check")
        asked = [review("REVIEW VERDICT: REQUEST CHANGES")]
        self.assertEqual(self.stage(pr(1, reviews=asked)), "Fix & re-check")

    def test_merged_beats_gating(self):
        self.assertEqual(self.stage(pr(1, "MERGED"), gating={1}), "Merged")


class IssueStage(unittest.TestCase):
    def stage(
        self, i, linked=(), prev=None, gating=(), building=(), live=(), open_numbers=()
    ):
        got, _ = bs.issue_stage(
            i,
            list(linked),
            prev,
            set(gating),
            set(building),
            set(live),
            set(open_numbers),
        )
        return got

    def test_closed(self):
        self.assertEqual(
            self.stage(issue(1, "CLOSED", reason="NOT_PLANNED")), "Dropped"
        )
        self.assertEqual(self.stage(issue(1, "CLOSED")), "Live")
        merged = pr(2, "MERGED")
        self.assertEqual(self.stage(issue(1, "CLOSED"), [merged]), "Merged")
        self.assertEqual(self.stage(issue(1, "CLOSED"), [merged], live={MERGE}), "Live")

    def test_closed_takes_the_latest_merge(self):
        first = pr(2, "MERGED", merged_at="2026-10-01T00:00:00Z", merge_commit="d" * 40)
        last = pr(3, "MERGED", merged_at="2026-10-02T00:00:00Z")
        stage, chosen = bs.issue_stage(
            issue(1, "CLOSED"), [first, last], None, set(), set(), set(), set()
        )
        self.assertEqual((stage, chosen["number"]), ("Merged", 3))

    def test_labels(self):
        self.assertEqual(self.stage(issue(1, labels=["epic"]), [pr(2)]), "Epic")
        self.assertEqual(self.stage(issue(1, labels=["paused"])), "Paused")
        self.assertEqual(self.stage(issue(1, labels=["paused"]), [pr(2)]), "In review")
        self.assertEqual(self.stage(issue(1, labels=["owner"])), "Needs you")
        self.assertEqual(self.stage(issue(1, labels=["owner"]), [pr(2)]), "In review")
        self.assertEqual(self.stage(issue(1, labels=["spec"])), "Spec")

    def test_open_pull_requests_furthest_stage_wins(self):
        linked = [pr(2), pr(3, reviews=APPROVED), pr(4, "CLOSED")]
        stage, chosen = bs.issue_stage(
            issue(1), linked, None, set(), set(), set(), set()
        )
        self.assertEqual((stage, chosen["number"]), ("Approved", 3))
        self.assertEqual(self.stage(issue(1), linked, gating={2}), "Gating")

    def test_open_issue_with_a_merged_pull_request(self):
        self.assertEqual(self.stage(issue(1), [pr(2, "MERGED")]), "Merged")

    def test_dependency_does_not_park_the_card(self):
        # Owner rule 2026-10-21: every open card is Ready or Done, plus in-flight steps.
        i = issue(1, body="Depends on #7")
        self.assertEqual(self.stage(i, open_numbers={7}), "Ready")
        self.assertEqual(self.stage(i, open_numbers={8}), "Ready")
        self.assertEqual(
            self.stage(
                issue(1, body="Depends on #7", labels=["owner"]), open_numbers={7}
            ),
            "Needs you",
        )

    def test_building_ready(self):
        self.assertEqual(self.stage(issue(1), building={1}), "Building")
        self.assertEqual(self.stage(issue(1), prev="In Progress"), "Building")
        self.assertEqual(self.stage(issue(1), prev="Building"), "Building")
        self.assertEqual(self.stage(issue(1), prev="Ready"), "Ready")
        self.assertEqual(self.stage(issue(1)), "Ready")


class LinkAndPlan(unittest.TestCase):
    def test_link_by_reference_body_and_closed_by(self):
        issues = [issue(1), issue(2), issue(3, "CLOSED", closed_by=[12])]
        prs = [
            pr(10, closes=[1]),
            pr(11, body="Some text\nfixes #2 and resolves #99"),
            pr(12, "MERGED"),
        ]
        prs_of = bs.link_prs(issues, prs)
        self.assertEqual(
            {k: [p["number"] for p in v] for k, v in prs_of.items()},
            {1: [10], 2: [11], 3: [12]},
        )

    def test_plan_rows_archive_and_off_board(self):
        issues = [
            issue(1, title="A5 retry", body="Depends on #2\nDepends on #3"),
            issue(2, title="Board columns", labels=["lane:mail"]),
            issue(3, "CLOSED"),
        ]
        prs = [
            pr(10, closes=[2], reviews=APPROVED),  # linked: its card is archived
            pr(
                11, title="CI: one more step"
            ),  # open, closes nothing: a card of its own
            pr(12, "MERGED"),  # old, never on the board: stays off it
            pr(
                13, "CLOSED", title="Board tweak"
            ),  # closed, on the board: kept as Dropped
        ]
        items = [
            item("IT1", "Issue", 1, status="Ready", module="apply"),
            item("IT10", "PullRequest", 10),
            item("IT13", "PullRequest", 13, module="jobs"),
        ]
        rows, to_archive = bs.plan(issues, prs, items, {10}, set(), set())
        by = {(r["kind"], r["number"]): r for r in rows}
        self.assertEqual(to_archive, ["IT10"])
        self.assertEqual(
            set(by), {("issue", 1), ("issue", 2), ("issue", 3), ("pr", 11), ("pr", 13)}
        )
        one, two = by[("issue", 1)], by[("issue", 2)]
        self.assertEqual(
            (one["stage"], one["blocked_by"], one["module"], one["item"]),
            ("Ready", "#2", "apply", "IT1"),
        )
        self.assertEqual(
            (two["stage"], two["pr"], two["verdict"], two["module"], two["item"]),
            ("Gating", 10, "APPROVE", "mail", ""),
        )
        self.assertEqual(by[("issue", 3)]["stage"], "Live")
        self.assertEqual(
            (by[("pr", 11)]["stage"], by[("pr", 11)]["module"]),
            ("In review", "platform"),
        )
        self.assertEqual(
            (by[("pr", 13)]["stage"], by[("pr", 13)]["module"]), ("Dropped", "jobs")
        )
        self.assertEqual(one["stage_order"], bs.ORDER["Ready"])

    def test_archived_linked_card_is_not_archived_again(self):
        rows, to_archive = bs.plan(
            [issue(1)],
            [pr(10, closes=[1])],
            [item("IT10", "PullRequest", 10, archived=True)],
            set(),
            set(),
            set(),
        )
        self.assertEqual(to_archive, [])
        self.assertEqual([r["stage"] for r in rows], ["In review"])

    def test_field_ops_only_what_differs(self):
        rows = [
            {"item": "A", "stage": "Ready", "module": "jobs"},
            {"item": "B", "stage": "Live", "module": "nowhere"},
            {"item": "", "stage": "Ready", "module": "jobs"},
        ]
        current = {"A": ("Ready", "mail"), "B": ("Merged", None)}
        ops = bs.field_ops(
            rows, current, "S", "M", {"Ready": "s1", "Live": "s2"}, {"jobs": "m1"}
        )
        self.assertEqual(ops, [("A", "M", "m1"), ("B", "S", "s2")])


class BatchAndSnapshot(unittest.TestCase):
    def test_read_batch(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "batch")
            self.assertEqual(bs.read_batch(path), set())
            Path(path).write_text("499\n483\n\n")
            self.assertEqual(bs.read_batch(path), set())  # no pid: not a ship's batch
            Path(d, "pid").write_text(f"{os.getpid()}\n")
            self.assertEqual(bs.read_batch(path), {499, 483})
        self.assertEqual(bs.numbers("499,483 #12"), {499, 483, 12})

    def test_batch_of_a_killed_ship_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "batch")
            Path(path).write_text("499\n")
            dead = subprocess.Popen([sys.executable, "-c", "pass"])
            dead.wait()  # reaped: its pid names no process now
            for pid in (str(dead.pid), "garbage", "0"):
                Path(d, "pid").write_text(pid + "\n")
                self.assertEqual(bs.read_batch(path), set(), pid)

    def test_snapshot_files_and_columns(self):
        issues = [
            issue(1),
            issue(
                2,
                "CLOSED",
                created="2026-10-17T08:00:00Z",
                closed="2026-10-19T01:00:00Z",
            ),
            issue(3, labels=["epic"]),
        ]
        prs = [pr(10, "MERGED", closes=[2], merged_at="2026-10-19T00:30:00Z")]
        rows, _ = bs.plan(issues, prs, [], set(), set(), {MERGE})
        with tempfile.TemporaryDirectory() as d:
            bs.write_snapshot(
                d, rows, prs, issues, MERGE, "2026-10-19T02:00:00Z", HEAD, now="T1"
            )
            bs.write_snapshot(
                d, rows, prs, issues, MERGE, "2026-10-19T02:00:00Z", HEAD, now="T2"
            )

            def read(name):
                with open(os.path.join(d, name)) as f:
                    return list(csv.reader(f))

            task_rows = read("tasks.csv")
            self.assertEqual(
                task_rows[0],
                [
                    "number",
                    "kind",
                    "title",
                    "url",
                    "module",
                    "stage",
                    "stage_order",
                    "pr",
                    "pr_url",
                    "verdict",
                    "labels",
                    "created",
                    "closed",
                    "merged",
                    "open",
                    "blocked_by",
                ],
            )
            self.assertEqual(
                [(t[0], t[5]) for t in task_rows[1:]],
                [("1", "Ready"), ("2", "Live"), ("3", "Epic")],
            )
            self.assertEqual(
                read("daily.csv"),
                [
                    ["day", "opened", "closed", "merged", "open_total"],
                    ["2026-10-17", "3", "0", "0", "3"],
                    ["2026-10-19", "0", "1", "1", "2"],
                ],
            )
            hist = read("history.csv")
            self.assertEqual(hist[0], ["at", *bs.STAGE_NAMES])
            self.assertEqual(
                [h[0] for h in hist[1:]], ["T1", "T2"]
            )  # appended, one line per sync
            self.assertEqual(hist[1][1 + bs.STAGE_NAMES.index("Live")], "1")
            self.assertEqual(
                hist[1][1 + bs.STAGE_NAMES.index("Epic")], "0"
            )  # epics are not tasks
            self.assertEqual(
                read("meta.csv"),
                [
                    META_COLUMNS.split(),
                    [
                        "T2",
                        MERGE[:12],
                        "2026-10-19T02:00:00Z",
                        HEAD[:12],
                        "2",
                        "1",
                        "0",
                        "0",
                        "1",
                    ],
                ],
            )


def done(code: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["gh"], code, out, err)


class TimeBound(unittest.TestCase):
    """The calls of gql and the deadline, on a fake subprocess.run and a fake clock."""

    def setUp(self):
        self.now = [1000.0]
        self.calls: list[tuple[list[str], float]] = []
        self.sleeps: list[float] = []
        self.answers: list = []

        def run(cmd, timeout=None, **kw):
            self.calls.append((cmd, timeout))
            answer = self.answers.pop(0)
            if isinstance(answer, BaseException):
                self.now[0] += timeout
                raise answer
            return answer

        def sleep(seconds):
            self.sleeps.append(seconds)
            self.now[0] += seconds

        patches = [
            mock.patch.object(bs.subprocess, "run", side_effect=run),
            mock.patch.object(bs.time, "sleep", side_effect=sleep),
            mock.patch.object(bs.time, "monotonic", side_effect=lambda: self.now[0]),
            mock.patch.object(bs, "DEADLINE", None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def timeout(self):
        return subprocess.TimeoutExpired(["gh", "api", "graphql"], 60)

    def test_every_call_has_a_timeout(self):
        self.answers = [done(0, '{"data": {"x": 1}}')]
        self.assertEqual(bs.gql("query{x}"), {"x": 1})
        self.assertEqual(self.calls[0][1], bs.CALL_SECONDS)
        self.answers = [done(0, "abc\n")]
        self.assertEqual(bs.git("rev-parse", "HEAD"), "abc")
        self.assertEqual(self.calls[1][1], bs.CALL_SECONDS)

    def test_a_timeout_is_retried_like_a_502(self):
        self.answers = [
            self.timeout(),
            done(1, "", "HTTP 502: Bad Gateway"),
            done(0, '{"data": {"ok": true}}'),
        ]
        self.assertEqual(bs.gql("query{ok}"), {"ok": True})
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.sleeps, [3, 6])

    def test_timeouts_every_time_exit_with_one_line(self):
        self.answers = [self.timeout() for _ in range(6)]
        with self.assertRaises(SystemExit) as e:
            bs.gql("query{x}")
        self.assertIn("gh api graphql failed: timeout", str(e.exception.code))
        self.assertEqual(len(self.calls), 6)

    def test_other_errors_are_not_retried(self):
        self.answers = [done(1, "", "HTTP 401: Bad credentials")]
        with self.assertRaises(SystemExit):
            bs.gql("query{x}")
        self.assertEqual(len(self.calls), 1)

    def test_no_call_starts_after_the_deadline(self):
        bs.DEADLINE = self.now[0] + 100
        self.now[0] += 101
        with self.assertRaises(bs.OutOfTime):
            bs.gql("query{x}")
        self.assertEqual(self.calls, [])

    def test_a_call_never_runs_past_the_deadline(self):
        bs.DEADLINE = self.now[0] + 20
        self.answers = [done(0, '{"data": {}}')]
        bs.gql("query{x}")
        self.assertEqual(self.calls[0][1], 20)

    def test_no_retry_sleeps_past_the_deadline(self):
        bs.DEADLINE = self.now[0] + 70
        self.answers = [self.timeout(), self.timeout()]
        with self.assertRaises(bs.OutOfTime):
            bs.gql("query{x}")
        # the first call takes its full 60 s, then a 3 s pause fits; the second call gets the
        # 7 s left, times out, and the 6 s pause would cross the deadline
        self.assertEqual([t for _, t in self.calls], [60, 7])
        self.assertEqual(self.sleeps, [3])

    def test_main_stops_at_the_deadline_with_one_line(self):
        self.answers = [done(0, "/repo/.git\n")]  # main_checkout
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch.object(bs, "sync", side_effect=bs.OutOfTime("gh api")),
            self.assertRaises(SystemExit) as e,
        ):
            bs.main(["--out", d, "--deadline", "5"])
        self.assertTrue(
            str(e.exception.code).startswith(
                "board-sync: stopped at the deadline of 5 s"
            )
        )
        self.assertNotIn("\n", str(e.exception.code))

    def test_a_git_timeout_exits_with_one_line(self):
        self.answers = [done(0, "/repo/.git\n")]
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch.object(
                bs,
                "sync",
                side_effect=subprocess.TimeoutExpired(
                    ["/usr/bin/git", "-C", "x", "fetch"], 60
                ),
            ),
            self.assertRaises(SystemExit) as e,
        ):
            bs.main(["--out", d])
        self.assertEqual(
            str(e.exception.code), "board-sync: git fetch gave no answer in 60 s"
        )

    def test_setup_without_apply_refuses(self):
        self.answers = [done(0, "/repo/.git\n")]
        with mock.patch.object(bs, "sync") as sync, self.assertRaises(SystemExit) as e:
            bs.main(["--setup"])
        self.assertIn(
            "--setup writes the board, so it needs --apply", str(e.exception.code)
        )
        sync.assert_not_called()


class Wiring(unittest.TestCase):
    def test_app_is_this_checkout(self):
        self.assertEqual(bs.APP, str(SCRIPT.parent.parent))

    def test_fourteen_stages_in_pipeline_order(self):
        self.assertEqual(bs.STAGE_NAMES[0], "Needs you")
        self.assertEqual(bs.STAGE_NAMES[-3:], ["Live", "Dropped", "Epic"])
        self.assertEqual(len(set(bs.STAGE_NAMES)), 14)


if __name__ == "__main__":
    unittest.main(verbosity=1)
