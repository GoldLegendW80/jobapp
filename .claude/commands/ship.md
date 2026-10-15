---
description: Ship approved pull requests to production with ops/ship.sh (plan first, then the run)
argument-hint: [pr numbers in order]
---

Ship $ARGUMENTS (every approved pull request when empty), as `docs/agents/orchestration.md` and
CONTRIBUTING.md (steps 6 and 7) describe.

1. Make sure the machine is quiet: no agent running pytest, Playwright or image builds,
   `.local-check/lock` absent, at least 16 GiB free. If agents are running tests, wait for their
   hand back first.
2. `ops/ship.sh --plan --approved` (or `ops/ship.sh --plan <prs>`): show the batch, its order, the
   gate script and what waits, and why.
3. Run it from the main checkout, clean and on `main`: `ops/ship.sh <prs>` or
   `ops/ship.sh --approved`. It takes 15 to 20 minutes; run it once, in the foreground or as one
   background command whose end you wait for, and do not poll it with tool calls.
4. Report the last line (`SHIP: OK` or `SHIP: FAIL <reason>`) and the summary: merged, dropped
   with the blamed step, the Jetson figures. On a drop, the pull request got a comment: start
   `/fix-pr <pr>`.
5. After `SHIP: OK`: remove the merged branches' worktrees (`git worktree remove
   .claude/worktrees/<name>`), check the board, and update `docs/STATUS.md` (in a small pull
   request of its own, or in the next one the orchestrator opens).
