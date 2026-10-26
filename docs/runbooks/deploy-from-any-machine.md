# Deploy from any machine

Deploy and operate the Jetson from any laptop through GitHub Actions, with no ssh.

Owner decision of 2026-10-15: no ssh to deploy or operate, GitHub Actions instead. Any laptop
whose gh account has write access to the repository (the owner's, or a collaborator's with
write) deploys and operates the Jetson with gh alone:

```bash
ops/ship.sh <pr> <pr>             # gate on this laptop, merge, deploy the checked tree, the Jetson's figures: SHIP: OK
ops/deploy-checked.sh             # deploy origin/main, only if this checkout holds a PASS record of its tree
ops/remote.sh status              # services, health, alembic, memory, error lines, browser, deployed commit
ops/remote.sh backup              # a verified dump into ~/jobapp-backups on the Jetson, with its retention
ops/remote.sh backups             # how many dumps, the newest name
ops/remote.sh apply-status        # apply-pause and apply-resume stop or allow every application submission at once
ops/remote.sh egress-host-check   # the browser's host firewall is installed and exact
ops/remote.sh --list              # the actions
```

How it works:

- **The gate stays local.** Each machine gates its own batch: `ops/local-check.sh` runs with
  Docker on that laptop and writes its PASS record in `.local-check/` of that checkout, and
  `ops/deploy-checked.sh` deploys only a tree with a record there. `ops/ship.sh` does all of it
  in order (gate, merge, deploy). A record made on another laptop does not count here: run the
  gate on main first (`ops/local-check.sh`).
- **The deploy** is `.github/workflows/deploy.yml` on the Jetson's self-hosted runner, dispatched
  by `ops/deploy-checked.sh`. Its last step, `ops/jetson-verify.sh`, prints the figures of the
  stack as one line and in the job summary, and fails the run when the stack is not well:
  `JOBAPP-VERIFY services=8 healthy=8 missing=0 unhealthy=0 alembic=0018 head=1 mem_used=2300
  mem_total=7620 errors=0 browser=off commit=<12 hex> unwell=- absent=- result=ok`.
  `ops/deploy-checked.sh` reads that line from the run's log and prints it; `ops/ship.sh` checks
  it (8 services running and healthy with the `tunnel` profile, alembic at head, the memory read,
  0 error lines in 10 minutes, a running browser healthy) instead of asking the Jetson over ssh.
- **Operations** are `.github/workflows/ops.yml`, dispatched by `ops/remote.sh <action>`, which
  waits for the run and prints its `JOBAPP-OPS` (and for `status`, `JOBAPP-VERIFY`) line, then
  `REMOTE: OK <action>` or `REMOTE: FAIL <action> <why>`. The action is a closed choice (GitHub
  refuses any other value and `ops/jetson-ops.sh` refuses it again); there is no free argument,
  so nothing typed in a dispatch becomes a command.
- **Figures only.** The run logs of this public repository are readable by anyone: the scripts
  print counts, states, revisions and dump names, never a log line, a path of the Jetson's
  disk beyond a dump's name, or an environment value. The secrets stay in `~/jobapp/.env` on the
  Jetson. Every path of `deploy.yml` holds to it, a failure included: its failure step prints
  each service's state, health and exit code and the names of the newest predeploy dumps, never
  a log (`./deploy.sh logs <service>`, over ssh, reads them); the rollback prints the same
  states; the migrations print each step, the revision reached and, on a failure, the step, the
  exit code and the exception's class; the smoke prints its step names, ok counts and, on a
  failure, the step and the last HTTP status code. Their whole output (alembic's lines, the
  smoke's addresses and bodies, the predeploy backup's report) is kept on the Jetson in
  `~/jobapp-backups/logs/` (mode 700, files 600, kept 30 days), never in the checkout.
  `tests/remote_ops_test.sh` fails on a `logs`, a `tee`, an address or a printed body in any
  step.
- **Only main is deployed.** The first step after the checkout refuses a ref whose commit is not
  on `origin/main`, before any script of that checkout runs. A hot fix that cannot wait for main
  is a branch `hotfix/<name>`, deployed by its name with `allow_branch`:
  `ops/deploy-checked.sh --allow-branch origin/hotfix/<name>` (it still needs a PASS record of
  its tree) dispatches `-f ref=hotfix/<name> -f allow_branch=true`, and the workflow checks that
  the branch's tip is the commit it checked out. A feature branch, a sha, or a tag is refused.
- **The runner** only runs `deploy.yml` and `ops.yml` from `main`, on `workflow_dispatch`: its
  job-started hook (`ops/runner/job-guard.sh`, baked into its image) refuses every other job, a
  pull request, a fork, a branch's workflow, before its first step. Dispatching needs write access
  to the repository and the gh scope `workflow` (`ops/dev-setup.sh` checks the scope).

The owner's steps that stay manual on the Jetson, over ssh, because they need sudo, a password or
a free argument:

```bash
./deploy.sh runner install        # once after this change: rebuilds the runner image so its guard admits ops.yml
ssh jetson                        # then, once: the browser's host firewall (sudo)
cd ~/jobapp && sudo bash ops/browser-egress.sh install jobapp_egress
tests/reboot_check.sh             # a reboot (sudo) and its recovery check
# and the commands of ./deploy.sh that take an argument: restore <file> --into-live, admin create <email>,
# admin unlock, profile import, jobs import, documents import-legacy, ingestion close-run,
# backup-schedule install
./deploy.sh                       # the first deploy and a new .env
```

After `./deploy.sh runner install`, `ops/remote.sh status` (or Actions, Ops, Run workflow,
`status`) proves the path: a run that fails with `runner-job-guard: refused` in its log means the
runner's image predates `ops.yml`.
