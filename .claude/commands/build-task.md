---
description: Start a builder subagent on one task (an issue number or a task id such as D13)
argument-hint: <issue number or task id> [sonnet]
---

Start the build of task $ARGUMENTS, as the orchestrator of `docs/agents/orchestration.md`.

1. Find the issue: `gh issue view <n>`, or for a task id
   `gh issue list --search "<task id> in:title" --state open`. No issue: create it first.
2. Read the task's section in `tasks/<module>/todo.md` by line range (grep the heading) to get its
   owned files, dependencies and acceptance lines. If a dependency is not merged, say so and stop,
   unless the task can be stacked (`Depends on #<n>`).
3. Check the machine: `test -d .local-check/lock` (a gate runs: wait), free disk at least 16 GiB,
   and no more than three other agents running.
4. Move the card: `ops/card.sh <issue url> doing <module> now`.
5. Spawn the `implementer` subagent (Opus; Sonnet when the argument says `sonnet` or the task is
   small) with the builder brief of `docs/agents/orchestration.md`, every placeholder filled, the
   "Decisions:" line included (the headings of docs/decisions.md for the module and for each other
   module the task touches).
6. When it hands back: put its pull request on the board (`ops/card.sh <pr url> doing <module> now`),
   then start a first review with `/review-pr <pr>`.
