#!/usr/bin/env python3
"""Tests of the pure part of ops/board-sync.py (issue #500) on fixtures: no network, no gh, no git.

Run: python3 tests/board_sync_test.py (tests/dev_setup_test.sh runs it, so the gate does too).
"""

from __future__ import annotations

import csv
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

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

    def test_blocked_only_by_open_numbers(self):
        i = issue(1, body="Depends on #7")
        self.assertEqual(self.stage(i, open_numbers={7}), "Blocked")
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
            ("Blocked", "#2", "apply", "IT1"),
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
        self.assertEqual(one["stage_order"], bs.ORDER["Blocked"])

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
            self.assertEqual(bs.read_batch(path), {499, 483})
        self.assertEqual(bs.numbers("499,483 #12"), {499, 483, 12})

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


class Wiring(unittest.TestCase):
    def test_app_is_this_checkout(self):
        self.assertEqual(bs.APP, str(SCRIPT.parent.parent))

    def test_fourteen_stages_in_pipeline_order(self):
        self.assertEqual(bs.STAGE_NAMES[0], "Needs you")
        self.assertEqual(bs.STAGE_NAMES[-3:], ["Live", "Dropped", "Epic"])
        self.assertEqual(len(set(bs.STAGE_NAMES)), 14)


if __name__ == "__main__":
    unittest.main(verbosity=1)
