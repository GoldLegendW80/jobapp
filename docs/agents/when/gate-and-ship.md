# The gate, ship and deploy

Read this when a trigger line sent you here: you are the orchestrator or the owner and you gate,
ship, deploy or operate the Jetson; or your diff touches `ops/local-check.sh`, `ops/ship.sh`,
`ops/deploy-checked.sh`, `ops/remote.sh`, the board or setup scripts, `deploy.sh`,
`.github/workflows/`, `.github/scripts/` or the scope mapper of the gate. A reviewer reads it for
the same diffs (reviewer.md rule 7 names the files that change the gate itself). Moved on
2026-10-29 from CLAUDE.md (Daily commands, the gate, ship and deploy lines and the paragraph on
deploying from any machine) and the gate part of checklist item 5 of
[review-checklist.md](../review-checklist.md). The cases are in
[lessons/gate-and-ops-tools.md](../lessons/gate-and-ops-tools.md). The mechanics are in
[orchestration.md](../orchestration.md) (Task lifecycle, Deploy from any machine, Parallelism and
the gate) and CONTRIBUTING.md, steps 4 to 7. A builder never runs the gate. To set up a laptop:
`ops/dev-setup.sh` and the runbook `docs/runbooks/develop-new-machine.md`. The stack side of the
gate (journey stacks, Docker address pools) is in [journeys.md](journeys.md).

## Commands

```bash
ops/dev-setup.sh                                   # a new laptop: what is missing and how to fix it (read only)
ops/local-check.sh --merge <pr> --merge <pr>        # the gate on a merged batch (the orchestrator, quiet machine)
ops/ship.sh --plan --approved                       # what would ship, changes nothing
ops/ship.sh <pr> <pr>                               # gate, merge, deploy the checked tree, check the Jetson: SHIP: OK
ops/deploy-checked.sh                               # deploy the current checked main only
ops/remote.sh status                                # the Jetson's figures through GitHub Actions (ops.yml), no ssh; --list for the actions
ops/board-sync.py                                   # every column from GitHub facts, dry run (ops/ship.sh runs --apply)
```

Deploy from any machine, no ssh (docs/runbooks/deploy-from-any-machine.md): the gate runs on the
laptop that ships and writes its PASS record in that checkout; `ops/ship.sh` (or
`ops/deploy-checked.sh`) dispatches `.github/workflows/deploy.yml` on the Jetson's runner for a
tree that gate passed and reads the run's `JOBAPP-VERIFY` figures; `ops/remote.sh <action>`
dispatches `.github/workflows/ops.yml` (status, backup, apply pause and resume, ...). Dispatching
needs write access and the gh scope `workflow`. `./deploy.sh` and ssh stay for the owner: the
first deploy, a new `.env`, sudo steps.

## Checklist item 5, the check

Gate and ops tools: bound every external call, widen when unsure, survive a kill. (OPS: 13 rounds,
9 PRs.)

Check: a `timeout` on every `gh`, `git` and `docker` call, and a watchdog test with a real
minimum; what the scope mapper cannot read (dynamic imports, deletions, parse errors, routes
called over HTTP) widens the run; a gate killed without its trap leaves no stack, slot or lock;
`shellcheck` and the script's `tests/*_test.sh`.
