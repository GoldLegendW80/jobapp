# Orchestration

How the build runs. The main Claude Code session is the **orchestrator**: it never writes app
code itself. It turns the owner's requests into issues, gives each task to a fresh subagent with a
short brief, has every pull request reviewed by another fresh subagent, ships approved batches
with `ops/ship.sh`, and keeps the board and [../STATUS.md](../STATUS.md) current. The owner
decides what gets built and answers questions; the orchestrator asks him through Claude Code's
question tool (AskUserQuestion), with concrete options and the recommended one first, never as
plain text at the end of a message.

Rules for the subagents: [implementer.md](implementer.md), [reviewer.md](reviewer.md),
[review-checklist.md](review-checklist.md). Merge and deploy mechanics: CONTRIBUTING.md, steps 4
to 7. Owner decisions: [../decisions.md](../decisions.md).

## Task lifecycle

1. **Issue.** Every task is a GitHub issue, from a module plan (`tasks/<module>/todo.md`, one
   section per task id such as D12 or A5), a review follow up, or an owner request. No issue, no
   work: create it first (`gh issue create`).
2. **Card.** The agent that starts a task runs `ops/card.sh <issue url> doing <module> now`
   (Building); every other move is made by `ops/board-sync.py` from GitHub facts (The board,
   below), so nobody moves cards by hand. After each agent hands back, a dry run of the sync
   (`ops/board-sync.py`, no `--apply`) prints how many tasks sit in each column. The owner judges
   progress by the Live column: prefer shipping approved work early to starting new tasks.
3. **Builder.** One fresh subagent per task (`.claude/agents/implementer.md`, or the Agent tool
   with the builder brief below), on its own branch `<module>/<task-id>-<slug>` in its own
   worktree `.claude/worktrees/<name>`. It hands back a pull request number and head sha.
4. **Pull request.** The body says `Closes #<issue>` (or `Refs #<issue>` for a slice) and, when
   it needs another open pull request first, `Depends on #<n>` as line 1. `ops/ship.sh` reads
   both.
5. **First review.** A fresh reviewer (`.claude/agents/reviewer.md`, on Opus), with the reviewer
   brief. It posts `REVIEW VERDICT: APPROVE` or `REQUEST CHANGES` with a `Head:` line, and pushes
   mechanical fixups itself.
6. **Fix agent.** On REQUEST CHANGES, a FRESH agent (never the builder resumed) with the fix
   brief: the branch, where the review is, the exact list of blocking items.
7. **Re-check.** A fresh reviewer, usually on Sonnet, with the re-check brief: only the previous
   blocking items and the diff since the reviewed head (reviewer rule 5). Repeat 6 and 7 until
   APPROVE on the current head.
8. **Orchestrator note.** When the orchestrator itself changes an approved head mechanically (a
   merge of main with additive conflict resolutions, a reviewer's uncommitted lint diff applied
   unchanged), it reruns the affected tests and posts a review comment in the verdict format,
   `REVIEW VERDICT: APPROVE` and `Head: <new sha>`, followed by "Orchestrator note." and exactly
   what changed since the reviewed head and what was run. Anything beyond mechanical goes back to
   a re-check.
9. **Ship.** `ops/ship.sh --plan --approved` shows the batch, the order and the gate;
   `ops/ship.sh <prs in order>` (or `--approved`) runs the gate on the merged batch, merges,
   deploys the checked tree and checks the Jetson. The last line is `SHIP: OK` or
   `SHIP: FAIL <reason>`. Ship when few agents run tests (see Parallelism). No ssh anywhere in
   this (owner, 2026-10-15): see "Deploy from any machine" below.
10. **After.** Follow ups from the review files become issues with cards; the merged branch's
    worktree is removed (`git worktree remove .claude/worktrees/<name>`), its scratch images
    removed by tag; [../STATUS.md](../STATUS.md) gets the new state.

## The board (issue #500)

Project 1 of GoldLegendW80 has one column (Status option) per step of the pipeline, in this
order, and one card per task: the issue. A pull request that closes an issue has no card of its
own (the sync archives it); one that closes no issue keeps its card.

| Column | A task is there when |
|---|---|
| Needs you | open, label `owner`, no open pull request |
| Paused | open, label `paused`, no open pull request |
| Spec | open, label `spec` |
| Ready | open, nothing started; also when a `Depends on #N` line names an open issue or pull request (the snapshot column `blocked_by` names it) |
| Building | an agent started it (`ops/card.sh <issue url> doing <module> now`), or its pull request is a draft |
| In review | its pull request has no verdict yet |
| Fix & re-check | the latest verdict asks for changes, or a commit came after the approval |
| Approved | approved on its current head, waits for the gate |
| Gating | in the batch `ops/ship.sh` is checking (`.local-check/ship.lock/batch`) |
| Merged | on main, not in the deployed commit yet |
| Live | in the commit the Jetson runs (the `JOBAPP-VERIFY commit=` of the last deploy) |
| Dropped | closed as not planned, or its pull request closed unmerged |
| Epic | a lane's tracking issue (label `epic`) |

What moves a card: `ops/board-sync.py --apply`, which computes every column from GitHub facts
only (labels, bodies, pull request states, `REVIEW VERDICT` reviews and their heads, the last
deploy run), so it is safe to repeat. `ops/ship.sh` runs it at the start of the gate, after the
merges and after the deploy; a failed sync prints one line and never fails the ship. Building is
the one fact GitHub does not hold, which is why agents run `ops/card.sh ... doing` when they
start: the sync keeps Building until a pull request exists. The Module field comes from the title
(`A5 ...` is apply, `O4 ...` opportunities), else the lane label. Every sync, dry or not, writes
a snapshot to `.local-check/board/` (`tasks.csv`, `daily.csv`, `history.csv`, `meta.csv`, git
ignored) that the dashboard with the charts reads. `--setup` replaces the Status and Module
options with the list of the script, once, when that list changes.

## Deploy from any machine (owner, 2026-10-15)

- The orchestrator may run on any laptop with Docker and a gh account with write access and the
  scope `workflow` (the owner's or a collaborator's with write). Nothing needs ssh to the Jetson.
- The gate is local: each machine gates its own batch, and its PASS record lives in
  `.local-check/` of that checkout only. `ops/ship.sh` gates, merges and deploys in one go; a
  laptop that did not run the gate cannot `ops/deploy-checked.sh` that tree until it runs
  `ops/local-check.sh` on main itself.
- The Jetson's state after a deploy is the run's `JOBAPP-VERIFY` line (`ops/jetson-verify.sh`, the
  last step of `deploy.yml`), which `ops/ship.sh` reads through `ops/deploy-checked.sh`. At any
  other time: `ops/remote.sh status`. A `SHIP: FAIL` that names figures is read from that line.
- Operations are `ops/remote.sh <action>` (`ops.yml` on the Jetson's runner): status, backup,
  backups, apply-status, apply-pause, apply-resume, egress-host-check. Pause the apply gate with
  `ops/remote.sh apply-pause` before a risky deploy instead of ssh.
- The self-hosted runner runs only `deploy.yml` and `ops.yml` from main on `workflow_dispatch`
  (job guard in its image). Never add a trigger that a pull request or a fork can fire.
- What stays with the owner, by hand and over ssh: sudo (the browser egress install, a reboot),
  `./deploy.sh runner install` when the guard changes, restores into the live database, admin
  links, imports. Write the exact command for him and stop.

## Permission model

`.claude/settings.json` is versioned and has three tiers. **Allow** holds the read only and test
commands, `worktree add .claude/worktrees/*`, and read only `gh api` GETs on
this repository's pulls and issues (the review verdict reads). **Ask** holds what changes shared
state: a merge, the gate, a deploy, `ssh jetson`, and the full `ops/dev-setup.sh` run (with `--jetson`
it calls `ssh jetson true`; only `--skip-jetson` is allowed). **Deny** wins over allow: force pushes, hook
bypasses, `gh api` with a write method or body (`-X`, `--method`, `-f`, `-F`, `--field`,
`--input`), and every way to read or change the secret files `.env`, `.env.jetson`, `.env.local`,
`.env.*.local`, `.env.production`, `.env.prod`, `.env.mailsend`, `.env.redis` and `.secrets/` (the Read, Edit and Write tools, and Bash `cat`,
`grep`, `head`, `sed`, `awk`, `cp` and similar). The names are listed one by one on purpose: a
`.env.*` pattern would also block `.env.example`, the tracked template that an agent edits when it
adds a setting. A new secret file name is added to the deny list in the same pull request that
introduces it. The Bash patterns are text matches, a guard against slips and not a sandbox. That
is why no `git -C <path> <subcommand>` form is allowed: the `*` of `git -C *` spans the
subcommand, so an allow for status or log would also match `git -C x push origin log` or
`git -C x reset --hard diff`; those commands go through the normal prompt instead. A pattern that
blocks legitimate work is fixed in this file by a pull request, never bypassed.

## Model policy (owner, 2026-10-13)

- **Opus:** M sized tasks, security sensitive code (sandbox, chain, send gate, submit gate, auth,
  egress), and every FIRST review.
- **Sonnet:** small work (review follow ups, flaky tests, docs and plan edits, S tickets), every
  re-check, and reviews of docs only pull requests.

## Parallelism and the gate

- Three to four agents at once, reviewers included (owner, 2026-10-13). More agents do not give
  more tickets a day: they reach the session limit sooner and then nothing runs.
- At most two agents that run heavy test stacks or build images at the same time.
- The gate (`ops/local-check.sh`, through `ops/ship.sh`) needs a quiet machine: start it when the
  last pytest running agent hands back, and start the next re-checks after it, not before. Agents
  that only read (reviews of docs, planners) are fine beside it. `ops/agent-test.sh` lowers what
  agents run, but a gate beside two `pytest -n 4` runs still doubled in time and failed on load.
- To see whether a gate runs, test for the folder `.local-check/lock` in the main checkout.
  `ps` and `pgrep -f` gave agents wrong answers (a filtering shell proxy, the caller's own shell).
- Before a gate, free disk must stay above 12 GiB for the whole run (it uses about 3): start
  with 16 or more. Free your own leftovers first (merged worktrees, scratch image tags,
  `uv cache prune`, `pnpm store prune`); clearing shared caches or anything not created by the
  build needs the owner's yes each time.

## Token economy (measured 2026-10-13)

Cost is context size times number of turns: output tokens are negligible. 90% of input came from
turns whose context was over 200k tokens.
- Never resume a subagent for a new round: spawn a fresh one with a compact brief. A fresh Opus
  reviewer cost 2 to 20 M tokens; long lived resumed agents cost 180 to 290 M each.
- Keep every agent under about 200k of context: one small task per agent, a hand back after
  about 90 tool calls.
- Agents never wait or poll after a push and never leave a watcher: they hand back once.
  Waiting is done by scripts (`ops/ship.sh`), which cost no tokens.
- The orchestrator restarts (or compacts) its own session when its context passes about 200k,
  after writing the state into [../STATUS.md](../STATUS.md). Batch work so that one
  notification leads to one turn.

## Brief templates

Briefs are short: the rules live in the docs, the brief names the task and what is special. Fill
the `<placeholders>`.

### Builder

```
You implement task <task id> of the jobapp repo, issue #<n>: <one line goal>.
Read first: CLAUDE.md, docs/agents/implementer.md (follow it exactly), docs/agents/review-checklist.md,
the section "<task id>" of tasks/<module>/todo.md (grep the heading, read by line range) and the
spec lines it cites in SPEC-<module>.md.
Branch <module>/<task-id>-<slug>, worktree .claude/worktrees/<name> from origin/main.
Owns: <files>. May touch, one line each: <shared registries>.
Depends on: <#n or none> (if the pull request needs it merged first, line 1 of the body).
Acceptance: <the acceptance lines, or "as in the plan section">.
Tests: the whole tests/<module> folder through ops/agent-test.sh, ruff, <web checks>,
ci_plan_test.py, e2e_groups.py<, the journeys of group <group> on your own stack>.
Special: <owner decisions to respect, known traps, what not to decide again>.
Model: <opus|sonnet>. Hand back as implementer.md says.
```

### First review

```
Review pull request #<pr> (<title>), branch <branch>, head <sha>. First review.
Read first: docs/agents/reviewer.md (follow it exactly) and docs/agents/review-checklist.md.
Spec: the section "<task id>" of tasks/<module>/todo.md and SPEC-<module>.md <sections>.
Known points: <files changed outside the owned ones, gate changes, what the author left open>.
Post the verdict as a review comment in the format of reviewer.md. Hand back as reviewer.md says.
```

### Fix agent

```
Fix the blocking items of the review of pull request #<pr>, branch <branch>, reviewed head <sha>.
Read first: docs/agents/implementer.md (follow it exactly). The review is the latest comment that
begins with "REVIEW VERDICT:" (gh pr view <pr> --comments, or
gh api repos/GoldLegendW80/jobapp/pulls/<pr>/reviews).
Fix exactly: <B1 ...>, <B2 ...>, <the "Fixups for the fix agent" list, if any>.
For a heuristic item, fix the family, not the quoted input (implementer.md, Heuristic code).
Worktree: a fresh one of the branch under .claude/worktrees/<name>. Push plainly, then add a
short "Round <n> fixes" section to the pull request body. Hand back as implementer.md says.
```

### Re-check

```
Re-check pull request #<pr> after round <n>. Previous verdict on <reviewed sha>: the latest
"REVIEW VERDICT:" comment of the pull request.
Read first: docs/agents/reviewer.md, rule 5 (later rounds) applies.
Check only: each previous blocking item, and the diff <reviewed sha>..<new head>.
Post the verdict in the format of reviewer.md, with the Head line of the current head.
```

## Lessons learned

- **Load flakes.** A gate run beside agents running pytest took twice as long and failed on load
  only tests (pool timeouts, hook time limits). The gate retries a failed batch once
  (`ops/ship.sh` names the first failure flaky when the rerun passes), and flaky tests are made
  deterministic rather than retried forever (issue #293). Before blaming load, check `uptime`
  for leftover processes: orphaned CPU loops of one reviewer once broke two gates.
- **A journey that has never run hides most of its bugs.** The first runs of D11, D12 and A7 each
  found real issues (a fake stream written for another profile, a browser profile missing in the
  gate, apply state bugs). Run every new journey on its own stack before the batch gate, and run
  `pnpm --dir web run typecheck` on every changed journey (21 type errors once reached main).
- **Run the whole module, not your own files.** With no CI per pull request, an approved change
  broke older tests and journeys that still stated the previous behaviour. Every brief demands
  the whole test folder of the module and a grep for assertions of the changed behaviour.
- **Heuristic code converges with allow lists and structure, not deny lists.** Pull requests
  #260, #259, #177 and #179 took 2 to 5 rounds while reviewers found the next neighbour of each
  quoted input. What ended them: a fail closed default, a model of the thing (periods, scopes,
  the tree builder's elements), accept and refuse tables generated from the spec. For an "the
  model may answer this form question" filter, start from an allow list of topics; after three
  rounds of patterns for one class, change the model or stop and let the person answer.
- **PDFium is not thread safe**, even across documents: two concurrent page image requests
  crashed the api natively (SIGSEGV, no traceback). One process wide lock guards every pypdfium2
  call (#320). A native crash shows as a restart count of 1 with no traceback; reproduce in the
  image with `python -X faulthandler`.
- **bash 3.2 (macOS `/bin/bash`):** no associative arrays or `mapfile`; `set -e` is ignored
  inside a function called from `if`, `||` or `&&`; `grep -q` in a pipe under `pipefail` can
  fail a healthy check (SIGPIPE of the writer); an EXIT trap must remove what background jobs of
  the script created, and a script must never leave a background job behind it.
- **Docker address pools.** Each journey stack has 4 to 5 networks, and Docker Desktop's default
  pools ran out at about 30 networks ("all predefined address pools have been fully
  subnetted"). Gate stacks use /27 subnets of their own range (#292), and every stack is taken
  down with `down -v` as soon as its group ends.
- **The session limit cuts every running agent at once.** Their worktrees survive. After the
  reset, check each worktree for uncommitted or unpushed work, then start fresh agents from the
  pushed state (never resume). Briefs demand a work in progress push within the first hour.
- **A reviewer's push can be refused** by the session's permission classifier. The reviewer then
  lists the exact edit under "Fixups for the fix agent", and the fix agent or the orchestrator
  (as an orchestrator note) applies it.
- **A change of the gate runs with its own script.** `ops/local-check.sh` from the main checkout
  is main's version, so a pull request that changes it is gated with its own copy from a detached
  worktree; `ops/ship.sh` does this by itself and ships such a pull request first in its batch.
- **Never merge while a real run is in progress on the Jetson:** the deploy restarts the worker.
- **Shared files:** an agent once deleted the orchestrator's board helper. Agents work only in
  their own worktree and files; the helpers now live in the repository (`ops/card.sh`).
