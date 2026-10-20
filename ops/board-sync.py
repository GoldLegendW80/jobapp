#!/usr/bin/env python3
"""Board sync (issue #500): put every task of the repo in its pipeline stage on the project board.

The stage of a task is computed from GitHub facts only, so the command is safe to repeat:

  Needs you       open, label `owner`, no open pull request
  Paused          open, label `paused`
  Blocked         open, a "Depends on #N" line names an open issue or pull request
  Spec            open, label `spec`
  Ready           open, nothing started
  Building        open, an agent works on it (--building, or the card already said so)
  In review       its pull request has no verdict yet
  Fix & re-check  the latest verdict asks for changes, or a commit came after the approval
  Approved        approved on its current head, waits for the gate
  Gating          in the batch the gate is checking (--gating, and the batch file of ops/ship.sh)
  Merged          on main, not in the deployed commit yet
  Live            in the commit the Jetson runs (JOBAPP-VERIFY commit= of the last deploy)
  Dropped         closed without being done
  Epic            a lane's tracking issue

Usage:
  ops/board-sync.py [--apply [--setup]] [--gating 499,483] [--building 446] [--out DIR]
                    [--deadline SECONDS]

Without --apply nothing is written to the board (a dry run that still writes the snapshot).
--setup (only with --apply) replaces the options of the Status and Module fields (their values
are then set again by this same run). The snapshot (tasks.csv, daily.csv, history.csv, meta.csv) goes to --out,
by default .local-check/board/ of the main checkout (git ignored); a dashboard reads it.
The batch ops/ship.sh gates is read from .local-check/ship.lock/batch of the main checkout
(one pull request number per line) when that file exists and ship.lock/pid names a running
process (the lock of a killed ship is ignored), in addition to --gating.

Time bound: every gh and git call has a timeout (CALL_SECONDS, LOG_SECONDS), a call that times
out is retried by gql like a 502, and no call or retry starts after --deadline seconds (default
DEADLINE_SECONDS): the run then stops with one line and exit 1. Each write is one whole batched
mutation, so a stop leaves no batch half written; the snapshot is written only after every read.

The pure part (the stage of an issue or a pull request from its facts, the module of a title,
the dependencies of a body, the verdict of the reviews, the plan of the board) takes no network
and is tested on fixtures by tests/board_sync_test.py. The GitHub calls are gql, deployed and
apply. It needs gh authenticated with the `project` scope.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

OWNER, REPO, NUMBER = "GoldLegendW80", "jobapp", 1
APP = str(Path(__file__).resolve().parent.parent)  # the checkout this script is in

STAGES = [
    ("Needs you", "RED", "Waits for a decision or an action of the owner"),
    ("Paused", "GRAY", "Paused by the owner"),
    ("Blocked", "ORANGE", "Waits for another task to merge first"),
    ("Spec", "PURPLE", "Spec and plan written or reviewed"),
    ("Ready", "GRAY", "Planned and free to start"),
    ("Building", "BLUE", "An agent writes the code and the tests"),
    ("In review", "YELLOW", "A fresh reviewer reads the pull request"),
    ("Fix & re-check", "ORANGE", "Changes asked, being fixed or checked again"),
    ("Approved", "GREEN", "Approved on its head, waits for the gate"),
    ("Gating", "PURPLE", "In the gate batch: every test on the Mac"),
    ("Merged", "BLUE", "On main, not deployed yet"),
    ("Live", "GREEN", "Running on the Jetson"),
    ("Dropped", "GRAY", "Closed without being done"),
    ("Epic", "PINK", "Tracking issue of a lane"),
]
STAGE_NAMES = [s[0] for s in STAGES]
ORDER = {name: i for i, name in enumerate(STAGE_NAMES)}

MODULES = [
    ("opportunities", "PINK"),
    ("jobs", "BLUE"),
    ("apply", "GREEN"),
    ("browser", "ORANGE"),
    ("documents", "PURPLE"),
    ("ingestion", "YELLOW"),
    ("agents", "RED"),
    ("mail", "GRAY"),
    ("profile", "BLUE"),
    ("identity", "GRAY"),
    ("platform", "GRAY"),
]
MODULE_RULES = [
    (r"^(opportunities|onboarding|ux)\b|^O\d+[a-z]?\b", "opportunities"),
    (r"^(apply|Apply)\b|^A\d+[a-z]?\b", "apply"),
    (r"^(browser|Browser)\b|^B\d+[a-z]?\b", "browser"),
    (r"^(documents|Documents)\b|^D\d+[a-z]?\b", "documents"),
    (r"^(mail|Mail)\b|^M\d+[a-z]?\b", "mail"),
    (r"^(ingestion|Ingestion)\b|^G\d+[a-z]?\b|^P7\b", "ingestion"),
    (r"^(jobs|Jobs)\b|^J\d+[a-z]?\b", "jobs"),
    (r"^(agents|Agents|Chat|Scrub)\b|chat prompt", "agents"),
    (r"^(profile|Profile)\b|profile-history", "profile"),
    (r"^(identity|Sign in)\b", "identity"),
    (
        r"^(platform|Platform|local-check|Local check|ship\.sh|CI|tests|Worker|Keep OPENSSL|T\d+)\b",
        "platform",
    ),
]
LANE_MODULE = {
    "lane:product": "opportunities",
    "lane:apply": "apply",
    "lane:browser": "browser",
    "lane:docs-edit": "documents",
    "lane:docs-design": "documents",
    "lane:mail": "mail",
    "lane:captain": "platform",
}
IN_FLIGHT = ("Building", "In review", "Fix & re-check", "Approved", "Gating")
CALL_SECONDS = 60  # one gh api graphql, gh run list or git call
LOG_SECONDS = 60  # gh run view --log
DEADLINE_SECONDS = (
    240  # the whole run (ops/ship.sh kills it after SHIP_SYNC_SECONDS, 300)
)


# --- the pure part: facts in, stages out ---------------------------------------------------------


def module_of(title: str, labels: set[str], previous: str | None) -> str:
    for pattern, module in MODULE_RULES:
        if re.search(pattern, title):
            return module
    for label in labels:
        if label in LANE_MODULE:
            return LANE_MODULE[label]
    return previous or "platform"


def depends_on(body: str | None) -> set[int]:
    nums: set[int] = set()
    for line in (body or "").splitlines():
        if re.match(r"^\W*(depends on|stacked on)\b", line.strip(), re.IGNORECASE):
            nums |= {int(n) for n in re.findall(r"#(\d+)", line)}
    return nums


def verdict(pr: dict) -> tuple[str, str]:
    """(APPROVE|REQUEST CHANGES|'', commit) of the latest REVIEW VERDICT review."""
    found = ("", "")
    for review in pr["reviews"]["nodes"]:
        body = (review.get("body") or "").lstrip()
        if body.startswith("REVIEW VERDICT:"):
            first = body.splitlines()[0]
            kind = "APPROVE" if "APPROVE" in first else "REQUEST CHANGES"
            found = (kind, (review.get("commit") or {}).get("oid", ""))
    return found


def pr_stage(pr: dict, gating: set[int], live: set[str]) -> str:
    if pr["state"] == "MERGED":
        return "Live" if (pr.get("mergeCommit") or {}).get("oid") in live else "Merged"
    if pr["state"] == "CLOSED":
        return "Dropped"
    if pr["number"] in gating:
        return "Gating"
    if pr["isDraft"]:
        return "Building"
    kind, commit = verdict(pr)
    if not kind:
        return "In review"
    if kind == "APPROVE":
        return "Approved" if commit == pr["headRefOid"] else "Fix & re-check"
    return "Fix & re-check"


def issue_stage(
    issue: dict,
    linked: list[dict],
    prev_status: str | None,
    gating: set[int],
    building: set[int],
    live: set[str],
    open_numbers: set[int],
) -> tuple[str, dict | None]:
    """The stage of an issue and the pull request that decided it (None when none did)."""
    labels = {label["name"] for label in issue["labels"]["nodes"]}
    open_prs = [p for p in linked if p["state"] == "OPEN"]
    merged = [p for p in linked if p["state"] == "MERGED"]
    if issue["state"] == "CLOSED":
        if merged:
            pr = max(merged, key=lambda p: p["mergedAt"])
            return pr_stage(pr, gating, live), pr
        if issue["stateReason"] == "NOT_PLANNED":
            return "Dropped", None
        return "Live", None
    if "epic" in labels:
        return "Epic", None
    if "paused" in labels and not open_prs:
        return "Paused", None
    if open_prs:
        pr = max(open_prs, key=lambda p: ORDER[pr_stage(p, gating, live)])
        return pr_stage(pr, gating, live), pr
    if merged:
        pr = max(merged, key=lambda p: p["mergedAt"])
        return pr_stage(pr, gating, live), pr
    if "owner" in labels:
        return "Needs you", None
    if depends_on(issue["body"]) & open_numbers:
        return "Blocked", None
    if "spec" in labels:
        return "Spec", None
    if issue["number"] in building or prev_status in ("In Progress", "Building"):
        return "Building", None
    return "Ready", None


def link_prs(issues: list[dict], prs: list[dict]) -> dict[int, list[dict]]:
    """Which pull requests belong to which issue: GitHub's own link, else "Closes #N" in the body."""
    by_issue = {i["number"] for i in issues}
    by_pr = {p["number"]: p for p in prs}
    prs_of: dict[int, list[dict]] = defaultdict(list)
    for p in prs:
        nums = {n["number"] for n in p["closingIssuesReferences"]["nodes"]}
        nums |= {
            int(n)
            for n in re.findall(
                r"(?i)\b(?:closes|fixes|resolves)\s+#(\d+)", p["body"] or ""
            )
        }
        for n in nums:
            if n in by_issue:
                prs_of[n].append(p)
    for i in issues:
        for ref in i["closedByPullRequestsReferences"]["nodes"]:
            p = by_pr.get(ref["number"])
            if p and p not in prs_of[i["number"]]:
                prs_of[i["number"]].append(p)
    return prs_of


def plan(
    issues: list[dict],
    prs: list[dict],
    items: list[dict],
    gating: set[int],
    building: set[int],
    live: set[str],
) -> tuple[list[dict], list[str]]:
    """The rows of the board (one per task) and the ids of the cards to archive."""
    open_numbers = {i["number"] for i in issues if i["state"] == "OPEN"} | {
        p["number"] for p in prs if p["state"] == "OPEN"
    }
    prs_of = link_prs(issues, prs)
    card = {}  # (kind, number) -> item
    for it in items:
        c = it.get("content") or {}
        if c.get("__typename") in ("Issue", "PullRequest"):
            card[(c["__typename"], c["number"])] = it

    rows = []
    for i in issues:
        labels = {label["name"] for label in i["labels"]["nodes"]}
        item = card.get(("Issue", i["number"]))
        prev_status = ((item or {}).get("status") or {}).get("name")
        prev_module = ((item or {}).get("module") or {}).get("name")
        stage, pr = issue_stage(
            i,
            prs_of.get(i["number"], []),
            prev_status,
            gating,
            building,
            live,
            open_numbers,
        )
        blocked_by = depends_on(i["body"]) & open_numbers
        rows.append(
            {
                "kind": "issue",
                "number": i["number"],
                "title": i["title"],
                "url": i["url"],
                "module": module_of(i["title"], labels, prev_module),
                "stage": stage,
                "stage_order": ORDER[stage],
                "pr": pr["number"] if pr else "",
                "pr_url": pr["url"] if pr else "",
                "verdict": verdict(pr)[0] if pr else "",
                "labels": " ".join(sorted(labels)),
                "created": i["createdAt"][:10],
                "closed": (i["closedAt"] or "")[:10],
                "merged": ((pr or {}).get("mergedAt") or "")[:10],
                "open": i["state"] == "OPEN",
                "item": (item or {}).get("id", ""),
                "node": i["id"],
                "blocked_by": " ".join(f"#{n}" for n in sorted(blocked_by)),
            }
        )

    # Pull requests that close no issue stay cards of their own; the others leave the board.
    linked_prs = {p["number"] for ps in prs_of.values() for p in ps}
    to_archive = []
    for p in prs:
        item = card.get(("PullRequest", p["number"]))
        if p["number"] in linked_prs:
            if item and not item["isArchived"]:
                to_archive.append(item["id"])
            continue
        if not item and p["state"] != "OPEN":
            continue  # old pull requests never on the board stay off it
        stage = pr_stage(p, gating, live)
        rows.append(
            {
                "kind": "pr",
                "number": p["number"],
                "title": p["title"],
                "url": p["url"],
                "module": module_of(
                    p["title"], set(), ((item or {}).get("module") or {}).get("name")
                ),
                "stage": stage,
                "stage_order": ORDER[stage],
                "pr": p["number"],
                "pr_url": p["url"],
                "verdict": verdict(p)[0],
                "labels": "",
                "created": p["createdAt"][:10],
                "closed": (p["closedAt"] or "")[:10],
                "merged": (p["mergedAt"] or "")[:10],
                "open": p["state"] == "OPEN",
                "item": (item or {}).get("id", ""),
                "node": p["id"],
                "blocked_by": "",
            }
        )
    return rows, to_archive


def field_ops(
    rows: list[dict],
    current: dict[str, tuple[str | None, str | None]],
    status_field: str,
    module_field: str,
    status: dict[str, str],
    module: dict[str, str],
) -> list[tuple[str, str, str]]:
    """(item, field, option) for every card whose Status or Module differs from its row."""
    ops = []
    for r in rows:
        if not r["item"]:
            continue
        have_status, have_module = current.get(r["item"], (None, None))
        if have_status != r["stage"]:
            ops.append((r["item"], status_field, status[r["stage"]]))
        if have_module != r["module"] and r["module"] in module:
            ops.append((r["item"], module_field, module[r["module"]]))
    return ops


def numbers(text: str) -> set[int]:
    return {int(n) for n in re.findall(r"\d+", text or "")}


def running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, owned by someone else
    except OSError:
        return False
    return True


def read_batch(path: str) -> set[int]:
    """The pull request numbers of the batch file ops/ship.sh writes (none when it is absent, or
    when the pid file next to it names no running process: the lock of a killed ship)."""
    try:
        with open(os.path.join(os.path.dirname(path), "pid")) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return set()
    if pid <= 0 or not running(pid):
        return set()
    try:
        with open(path) as f:
            return numbers(f.read())
    except OSError:
        return set()


def write_snapshot(
    out: str, rows, prs, issues, commit, deployed_at, main_tip, now=None
) -> None:
    now = now or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    cols = [
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
    ]
    with open(os.path.join(out, "tasks.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["stage_order"], -r["number"])):
            w.writerow(r)
    # Per day: issues opened and closed, pull requests merged, over the whole history.
    days: dict[str, Counter] = defaultdict(Counter)
    for i in issues:
        days[i["createdAt"][:10]]["opened"] += 1
        if i["closedAt"]:
            days[i["closedAt"][:10]]["closed"] += 1
    for p in prs:
        if p["mergedAt"]:
            days[p["mergedAt"][:10]]["merged"] += 1
    with open(os.path.join(out, "daily.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["day", "opened", "closed", "merged", "open_total"])
        open_total = 0
        for day in sorted(days):
            c = days[day]
            open_total += c["opened"] - c["closed"]
            w.writerow([day, c["opened"], c["closed"], c["merged"], open_total])
    # One line per sync: how many tasks sit in each stage (the history the board itself lacks).
    tasks = [r for r in rows if r["kind"] == "issue" and r["stage"] not in ("Epic",)]
    counts = Counter(r["stage"] for r in tasks)
    hist = os.path.join(out, "history.csv")
    new = not os.path.exists(hist)
    with open(hist, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["at", *STAGE_NAMES])
        w.writerow([now, *[counts.get(s, 0) for s in STAGE_NAMES]])
    open_tasks = [r for r in tasks if r["open"]]
    with open(os.path.join(out, "meta.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "generated",
                "deployed_commit",
                "deployed_at",
                "main_commit",
                "tasks",
                "live",
                "in_flight",
                "waiting_owner",
                "open",
            ]
        )
        in_flight = sum(1 for r in open_tasks if r["stage"] in IN_FLIGHT)
        w.writerow(
            [
                now,
                commit[:12],
                deployed_at,
                main_tip[:12],
                len(tasks),
                counts.get("Live", 0),
                in_flight,
                counts.get("Needs you", 0),
                len(open_tasks),
            ]
        )


# --- the GitHub and git calls ----------------------------------------------------------------------


class OutOfTime(Exception):
    """The deadline passed: no call and no retry starts any more."""


DEADLINE: float | None = (
    None  # time.monotonic() after which nothing starts (None: no bound)
)


def left() -> float:
    return float("inf") if DEADLINE is None else DEADLINE - time.monotonic()


def call(
    cmd: list[str], timeout: float = CALL_SECONDS, **kw
) -> subprocess.CompletedProcess:
    """subprocess.run with a timeout, never past the deadline (OutOfTime when it has passed)."""
    remaining = left()
    if remaining <= 0:
        raise OutOfTime(named(cmd))
    check = kw.pop("check", False)
    return subprocess.run(cmd, timeout=min(timeout, remaining), check=check, **kw)


def named(cmd: list[str]) -> str:
    """The program and its subcommand, without the -C <path> of git: "git fetch", "gh run"."""
    words = [os.path.basename(cmd[0])] + list(cmd[1:])
    if words[1:2] == ["-C"]:
        del words[1:3]
    return " ".join(words[:2])


RETRY = r"HTTP 50[234]|timeout|unexpected end of JSON|EOF|stream error"


def gql(query: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}})
    attempts = 6
    for attempt in range(
        attempts
    ):  # GitHub answers 502 (or resets the stream) now and then
        try:
            out = call(
                ["gh", "api", "graphql", "--input", "-"],
                input=body,
                capture_output=True,
                text=True,
                check=False,
            )
        except subprocess.TimeoutExpired:
            out = subprocess.CompletedProcess(
                [], 1, "", "timeout: no answer from gh api graphql"
            )
        if out.returncode == 0 or not re.search(RETRY, out.stderr):
            break
        if attempt == attempts - 1:
            break
        pause = 3 * (attempt + 1)
        if left() <= pause:
            raise OutOfTime("gh api graphql, while retrying")
        time.sleep(pause)
    if out.returncode:
        sys.exit(f"gh api graphql failed: {out.stderr.strip()[:2000]}")
    data = json.loads(out.stdout)
    if data.get("errors"):
        sys.exit(f"graphql errors: {json.dumps(data['errors'])[:2000]}")
    return data["data"]


def paged(query: str, path: list[str]) -> list[dict]:
    nodes, after = [], None
    while True:
        data = gql(query, {"after": after})
        conn = data
        for key in path:
            conn = conn[key]
        nodes += conn["nodes"]
        if not conn["pageInfo"]["hasNextPage"]:
            return nodes
        after = conn["pageInfo"]["endCursor"]


def _query(text: str) -> str:
    """A query with the board's owner, repository and project number in place."""
    return (
        text.replace("@OWNER", OWNER)
        .replace("@REPO", REPO)
        .replace("@NUMBER", str(NUMBER))
    )


ISSUES_Q = _query("""query($after:String){repository(owner:"@OWNER",name:"@REPO"){issues(first:50,
after:$after,orderBy:{field:CREATED_AT,direction:ASC}){pageInfo{hasNextPage endCursor} nodes{id
number title state stateReason createdAt closedAt url body labels(first:20){nodes{name}}
closedByPullRequestsReferences(first:5,includeClosedPrs:true){nodes{number}}}}}}""")

PRS_Q = _query("""query($after:String){repository(owner:"@OWNER",name:"@REPO"){pullRequests(first:40,
after:$after,orderBy:{field:CREATED_AT,direction:ASC}){pageInfo{hasNextPage endCursor} nodes{id
number title state isDraft createdAt mergedAt closedAt url body headRefOid mergeCommit{oid}
closingIssuesReferences(first:5){nodes{number}}
reviews(last:30){nodes{body submittedAt commit{oid}}}}}}}""")

ITEMS_Q = _query("""query($after:String){user(login:"@OWNER"){projectV2(number:@NUMBER){
items(first:100,after:$after){pageInfo{hasNextPage endCursor} nodes{id isArchived
content{__typename ... on Issue{number} ... on PullRequest{number}}
status:fieldValueByName(name:"Status"){... on ProjectV2ItemFieldSingleSelectValue{name}}
module:fieldValueByName(name:"Module"){... on ProjectV2ItemFieldSingleSelectValue{name}}}}}}}""")

FIELDS_Q = _query("""query{user(login:"@OWNER"){projectV2(number:@NUMBER){id fields(first:40){nodes{
... on ProjectV2SingleSelectField{id name options{id name}}}}}}}""")


def git(*args: str) -> str:
    return call(
        ["/usr/bin/git", "-C", APP, *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def main_checkout() -> str:
    """The main checkout of the repository (where .local-check lives, as for ops/ship.sh)."""
    try:
        common = git("rev-parse", "--path-format=absolute", "--git-common-dir")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return APP
    return os.path.dirname(common)


def deployed(cache_dir: str) -> tuple[str, str]:
    """The commit and time of the last successful deploy, from its JOBAPP-VERIFY line."""
    runs = json.loads(
        call(
            [
                "gh",
                "run",
                "list",
                "-R",
                f"{OWNER}/{REPO}",
                "-w",
                "deploy.yml",
                "--status",
                "success",
                "-L",
                "1",
                "--json",
                "databaseId,createdAt",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    if not runs:
        return "", ""
    run = runs[0]
    cache = os.path.join(cache_dir, f"deploy-{run['databaseId']}.txt")
    if os.path.exists(cache):
        with open(cache) as f:
            return f.read().strip(), run["createdAt"]
    log = call(
        ["gh", "run", "view", str(run["databaseId"]), "-R", f"{OWNER}/{REPO}", "--log"],
        timeout=LOG_SECONDS,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    found = re.findall(r"JOBAPP-VERIFY .*?commit=([0-9a-f]{7,40})", log)
    if not found:
        return "", run["createdAt"]
    full = git("rev-parse", found[-1])
    with open(cache, "w") as f:
        f.write(full)
    return full, run["createdAt"]


def apply(rows: list[dict], to_archive: list[str], setup: bool) -> None:
    data = gql(FIELDS_Q)["user"]["projectV2"]
    project = data["id"]
    fields = {f["name"]: f for f in data["fields"]["nodes"] if f}
    if setup:
        for name, opts in (
            ("Status", STAGES),
            ("Module", [(m, c, "") for m, c in MODULES]),
        ):
            got = gql(
                """mutation($id:ID!,$opts:[ProjectV2SingleSelectFieldOptionInput!]){
              updateProjectV2Field(input:{fieldId:$id,singleSelectOptions:$opts}){projectV2Field{
              ... on ProjectV2SingleSelectField{id name options{id name}}}}}""",
                {
                    "id": fields[name]["id"],
                    "opts": [
                        {"name": n, "color": c, "description": d} for n, c, d in opts
                    ],
                },
            )
            fields[name] = got["updateProjectV2Field"]["projectV2Field"]
        print("setup: Status and Module options replaced")
    status = {o["name"]: o["id"] for o in fields["Status"]["options"]}
    module = {o["name"]: o["id"] for o in fields["Module"]["options"]}
    missing = [s for s in STAGE_NAMES if s not in status]
    if missing:
        sys.exit(f"the Status field lacks {missing}: run once with --setup")

    # Add the issues that are not on the board yet.
    for r in rows:
        if not r["item"] and r["kind"] == "issue":
            got = gql(
                """mutation($p:ID!,$c:ID!){addProjectV2ItemById(input:{projectId:$p,contentId:$c}){item{id}}}""",
                {"p": project, "c": r["node"]},
            )
            r["item"] = got["addProjectV2ItemById"]["item"]["id"]
            r["_fresh"] = True

    current = {}
    for it in paged(ITEMS_Q, ["user", "projectV2", "items"]):
        current[it["id"]] = (
            (it.get("status") or {}).get("name"),
            (it.get("module") or {}).get("name"),
        )
    ops = field_ops(
        rows, current, fields["Status"]["id"], fields["Module"]["id"], status, module
    )
    for start in range(0, len(ops), 10):
        chunk = ops[start : start + 10]
        body = " ".join(
            f'u{n}: updateProjectV2ItemFieldValue(input:{{projectId:"{project}",itemId:"{item}",'
            f'fieldId:"{field}",value:{{singleSelectOptionId:"{opt}"}}}}){{clientMutationId}}'
            for n, (item, field, opt) in enumerate(chunk)
        )
        gql("mutation{" + body + "}")
    for start in range(0, len(to_archive), 10):
        chunk = to_archive[start : start + 10]
        body = " ".join(
            f'a{n}: archiveProjectV2Item(input:{{projectId:"{project}",itemId:"{item}"}}){{clientMutationId}}'
            for n, item in enumerate(chunk)
        )
        gql("mutation{" + body + "}")
    print(
        f"applied: {len(ops)} field values, {len(to_archive)} cards archived, "
        f"{sum(1 for r in rows if r.get('_fresh'))} issues added"
    )


def main(argv: list[str] | None = None) -> None:
    state = os.path.join(main_checkout(), ".local-check")
    ap = argparse.ArgumentParser(
        description="Put every task in its pipeline stage on the board."
    )
    ap.add_argument(
        "--apply", action="store_true", help="write the board (else a dry run)"
    )
    ap.add_argument(
        "--setup", action="store_true", help="replace the Status and Module options"
    )
    ap.add_argument(
        "--gating", default="", help="pull request numbers in the gate batch"
    )
    ap.add_argument("--building", default="", help="issue numbers an agent works on")
    ap.add_argument(
        "--out", default=os.path.join(state, "board"), help="the snapshot folder"
    )
    ap.add_argument(
        "--deadline",
        type=float,
        default=DEADLINE_SECONDS,
        help=f"seconds after which no call starts and the run stops (default {DEADLINE_SECONDS})",
    )
    args = ap.parse_args(argv)
    if args.setup and not args.apply:
        sys.exit(
            "board-sync: --setup writes the board, so it needs --apply (nothing done)"
        )
    global DEADLINE
    DEADLINE = time.monotonic() + args.deadline
    try:
        sync(args, state)
    except OutOfTime as e:
        sys.exit(
            f"board-sync: stopped at the deadline of {args.deadline:g} s ({e}): "
            "every batch written so far is whole, the rest waits for the next sync"
        )
    except subprocess.TimeoutExpired as e:
        sys.exit(f"board-sync: {named(e.cmd)} gave no answer in {e.timeout:g} s")


def sync(args: argparse.Namespace, state: str) -> None:
    os.makedirs(args.out, exist_ok=True)
    gating = numbers(args.gating) | read_batch(
        os.path.join(state, "ship.lock", "batch")
    )
    building = numbers(args.building)

    try:
        call(["/usr/bin/git", "-C", APP, "fetch", "-q", "origin"], check=False)
    except subprocess.TimeoutExpired:
        print("git fetch gave no answer: origin/main may be behind", file=sys.stderr)
    commit, deployed_at = deployed(args.out)
    live = set(git("rev-list", commit).split()) if commit else set()
    main_tip = git("rev-parse", "origin/main")

    issues = paged(ISSUES_Q, ["repository", "issues"])
    prs = paged(PRS_Q, ["repository", "pullRequests"])
    items = paged(ITEMS_Q, ["user", "projectV2", "items"])
    rows, to_archive = plan(issues, prs, items, gating, building, live)

    write_snapshot(args.out, rows, prs, issues, commit, deployed_at, main_tip)
    counts = Counter(r["stage"] for r in rows)
    print("stages:", ", ".join(f"{s} {counts.get(s, 0)}" for s in STAGE_NAMES))
    print(
        f"deployed {commit[:12]} at {deployed_at}, main {main_tip[:12]}, "
        f"{len(to_archive)} linked pull request cards to archive"
    )
    if args.apply:
        apply(rows, to_archive, args.setup)


if __name__ == "__main__":
    main()
