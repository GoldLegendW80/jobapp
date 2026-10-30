# Journeys and Docker stacks

Read this when a trigger line sent you here: your diff touches a Playwright journey (`web/e2e`),
a Compose file, a Docker image, a fake stream a journey replays, or a page a journey drives; or
you run a stack to test. A reviewer reads it to run or judge the journeys of a pull request. Moved
on 2026-10-29 from [implementer.md](../implementer.md) (Tests, "Playwright journeys"),
orchestration.md (Lessons learned: journeys never run, Docker address pools) and checklist item 14
of [review-checklist.md](../review-checklist.md). The cases are in
[lessons/never-run-checks.md](../lessons/never-run-checks.md).

## Run the journeys on your own stack

Playwright journeys, when the task has one: grep `ops/local-check.sh` for `stack_up` and
`s_journey` to see how the gate starts a Compose stack for a journey group, and do the same
with your own project name (`-p <task>-$RANDOM`), `-f compose.yml -f compose.mac.yml` on an
Apple silicon Mac as the gate does, and your OWN image tags through an override file (never
retag or rebuild `jobapp-*:latest`). Run ALL journeys of your module's group, not only your new
file. A journey that has never run hides most of its bugs: run every new journey on its own
stack before you push; if the gate's lock, the disk or the sandbox stops a run, write "not run"
and why in the body. Take the stack down with its volumes and `docker rmi` your tags after.

## Lessons of the orchestrator

- **A journey that has never run hides most of its bugs.** The first runs of D11, D12 and A7 each
  found real issues (a fake stream written for another profile, a browser profile missing in the
  gate, apply state bugs). Run every new journey on its own stack before the batch gate, and run
  `pnpm --dir web run typecheck` on every changed journey (21 type errors once reached main).
- **Docker address pools.** Each journey stack has 4 to 5 networks, and Docker Desktop's default
  pools ran out at about 30 networks ("all predefined address pools have been fully
  subnetted"). Gate stacks use /27 subnets of their own range (#292), and every stack is taken
  down with `down -v` as soon as its group ends.

## Checklist item 14, the check

Run every journey of the group and the module's suite on the head; say what did not run. (RUN: 8
rounds, 8 PRs.)

Check: implementer.md, Tests, and this file. When the gate's lock, the disk or the sandbox stops a
run, write "not run" and why in the body, so that the reviewer runs it.
