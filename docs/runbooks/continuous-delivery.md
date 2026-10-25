# Continuous delivery

The delivery model since issue #116: one batch check on the Mac, then a deploy on demand; migrations, rollback and the Docker socket security note.

**Since 2026-10-13 (issue #116): one batch check on the Mac, then a deploy on demand.** No CI
run per pull request and none per push to `main` any more: a run took about ten minutes on the
Mac's runners and runs queued behind each other, so five approved pull requests meant an hour of
waiting. Approved pull requests now merge back to back, after one full check of the tree they
produce together, run natively on the Mac with all its cores:

```bash
ops/local-check.sh --merge 121 --merge 118     # origin/main, then each pull request merged in that order, then every check
gh pr merge 121 --merge --delete-branch        # the same order
gh pr merge 118 --merge --delete-branch
git fetch origin && git rev-parse 'origin/main^{tree}'    # must print the tree sha of the line LOCAL CHECK: PASS <tree sha> <seconds>s
ops/deploy-checked.sh                          # deploys origin/main on the Jetson's runner, only if that tree passed
ops/deploy-checked.sh --dry-run                # what it would start
```

`ops/local-check.sh` makes a throwaway worktree of `origin/main` under a temporary folder, merges
the refs it is given in order (a conflict is exit 2 and names the ref), and runs there every step
of the three jobs of `.github/workflows/ci.yml`, always the full set: the api lint, the whole
pytest suite with the 95 percent coverage floor, the docker marked tests, the scoring timing
test; pre-commit, the secret scan, shellcheck, the guards and every `tests/*_test.sh` that
`ci.yml` runs; the web lint, type check, Vitest and build; and the Playwright journey groups
(apps, sweep and one per `web/e2e/<group>-all.config.ts`), each on a scratch Compose stack of
its own. Four lanes start together (api with its own
Postgres and Redis containers, web, the image build, the checks), and the journey groups start
when the images and the web install are done, side by side on their own ports. When every lane
has ended, the tests that measure time run in a quiet phase: Vitest next to the docker marked
tests, then alone the one test of the suite that measures its own speed, the coverage floor and
the scoring test.

Three rules keep the set whole. Each step runs in a bash of its own with errexit and pipefail, so
a step fails when any of its commands fails. Before the result, the steps that ran are compared
with the list of the set, and one that did not run fails the check. Every `tests/*_test.sh` of
the tree is either run (a line `run: tests/<name>_test.sh` in `ci.yml`) or named with its reason
in `shell_tests_left_out` of the script (`tests/migrate_test.sh` and `tests/backup_test.sh`
today); a file that is neither fails the step `shell-tests`.

The secret scan reads every commit that no passed check has read, up to the merged head. It
starts after the newest commit on the first parent line of the base whose tree has a PASS record
(the base itself when it was checked, and the range is then the merged pull requests). When none
of the last 200 commits of the base has a record it starts 50 commits before the base. So a
check of `origin/main` with no `--merge` reads what reached `main` since the last checked tree.
The range is printed at the start, on a line that begins with `secret scan:`.

It prints one line per step with its seconds, the end of the log of a
failed step, the folder that keeps the full logs, and as its last line
`LOCAL CHECK: PASS <tree sha> <seconds>s` or `LOCAL CHECK: FAIL <tree sha> <failed steps>`.
Exit 0 every step that ran passed, 1 a check failed, 2 a merge conflict, 3 the lock or the disk.

Only a last line that begins with exactly `LOCAL CHECK: PASS ` is the proof for a merge, with the
record below: it is printed by a run of the full set with the real steps and by nothing else. A
run with `--only` ends with `LOCAL CHECK: PARTIAL PASS <tree sha> <seconds>s, only <jobs>, no
record: not a proof for a merge or a deploy`, and the script's own tests, which replace the steps
with stubs, end with `LOCAL CHECK: STUB PASS ...`. Both exit 0, so the exit status alone proves
nothing.

On a pass of the full set it writes `.local-check/<tree sha>.json` in the main checkout (git
ignored): the tree, the base, the merged refs with their commits, the steps and their seconds,
the versions of the tools, the date. The tree sha is the key: after the pull requests are merged
on GitHub in the same order, `git rev-parse 'origin/main^{tree}'` equals it exactly when what
was checked is what is on `main`. `ops/deploy-checked.sh` refuses a commit whose tree has no such
record, or one that is not on `origin/main`; then it runs
`gh workflow run deploy.yml -f ref=<full sha>`, waits for the run on the Jetson's runner, prints
its conclusion and asks `https://jobapp.taild83d16.ts.net/api/health`.

The machine is protected: one check at a time (a lock in `.local-check/`; a second call says who
holds it and exits 3); no start under 12 GiB free on `/System/Volumes/Data`; every container,
volume, network and image of a check carries the check's own id in its name and is removed by a
trap on success, on failure and on Ctrl+C, with the temporary worktree; the images are built
under the check's own names, never over the shared `jobapp-*:latest` tags; containers of anything
else, the `jobapp-ci-*` runners included, are never touched. `--only api`, `--only checks` or
`--only journeys` runs one job's steps and writes no record; `--jobs N` sets the pytest
processes (default: the cores minus 2); `LOCAL_CHECK_STACKS` the journey stacks side by side
(default: one per 4 GiB of Docker memory, at most 4).

What it does not give: no check per pull request (a failure belongs to the batch, and is found by
running again with fewer `--merge` refs), nothing while the Mac is asleep, and no status on
GitHub (the record and the last line are the proof). `tests/compose_smoke.sh`,
`tests/migrate_test.sh` and the live start of `tests/tailscale_test.sh` stay tests run by hand,
as they were under CI.

`ci.yml` keeps its three jobs as the definition of the steps and starts by hand only
(`gh workflow enable ci.yml` once, then `gh workflow run ci.yml --ref <branch>`, with a runner
started: CI runners on the Mac, ci-runners-mac.md). The rest of this section describes such a run, and the
deploy workflow, which nothing starts by itself any more.

Every change goes through a branch and a pull request ([CONTRIBUTING.md](../../CONTRIBUTING.md)). A run of CI
(`.github/workflows/ci.yml`), started by hand since issue #116, has three jobs of about
the same length (What a CI run does, ci-run.md): `api` (ruff, the whole pytest suite against
Postgres and Redis in one run, the 95 percent coverage floor on that run's data, the docker
marked tests), `checks` (pre-commit, shellcheck and the shell test scripts, then the e2e groups
apps, ingestion and mail) and `journeys` (eslint, tsc, build, Vitest, then the e2e groups identity and
sweep); each
e2e group runs on a Compose stack of its own built on its runner, the Playwright journeys with
throwaway accounts and a synthetic jobs import, never Harald's data. The run is green when the
three jobs are. A pull request that changes documentation only runs pre-commit alone (and the
shell tests when it touches the README or a runbook, which they check); that narrowing needs a pull
request event, which no longer starts a run, so a run started by hand runs everything. Since 2026-10-10 the jobs run on self-hosted
runners on Harald's Mac (CI runners on the Mac, ci-runners-mac.md), because the free minutes of GitHub's own
machines were used up; the Jetson's runner keeps the deploys and nothing uses paid minutes. When
`ops/deploy-checked.sh` dispatches it for a checked commit, `.github/workflows/deploy.yml` runs on
the Jetson's self-hosted runner, under the Jetson's deploy lock (`flock` on
`~/jobapp-backups/.deploy.lock`, which `./deploy.sh` takes too, so the two never overlap; the Jetson
lets go of the Mac's hold after `DEPLOY_LOCK_MAX_S`, an hour by default, so a Mac that sleeps mid
deploy cannot block GitHub's deploys for hours): rsync of
that commit into `~/jobapp` (same `.rsyncignore`, `.env` kept; the tree it replaces is copied to
`~/jobapp-backups/previous-tree` first), images built first (a failed build leaves the running
stack untouched and puts those files back; beforehand, `ops/previous.sh tag-running` tags as
`:previous` the image each running api and caddy container was started from, read with
`docker inspect`, so `:previous` is always an image that ran), a
backup into `~/jobapp-backups/deploys/` whenever the database volume exists (db started first if
it was stopped; apart from the nightly ones and under the same retention, the newest 7 and none
7 days old: backups-and-restores.md, Retention), the migrations (next
paragraph), `compose up --wait`,
`tests/jetson_smoke.sh` in quick mode, `~/jobapp/.deployed-commit` written once the smoke passed,
and the public URL in the run's summary. If `up` fails, the previous files
(`compose.yml`, the Caddyfile and the rest of the tree, `.env` never touched) and the `:previous`
images are put back and started again, on the migrated schema; for data the
log names the predeploy dump to restore with `ops/db.sh restore-live`. Any other failure between
the sync and `up` (the backup and the migrations included) puts the previous files and `:latest`
tags back without restarting anything. A smoke that fails once the new stack is up does not roll
back, on purpose: the run is red, `.deployed-commit` keeps the previous commit, and going back is
a human decision.

Migrations run before the new code starts (issue #81), in the workflow and in `./deploy.sh`
alike: after the build, `ops/migrate.sh` runs `alembic upgrade head` in a one off container of
the new api image (`docker compose run --rm --no-deps`, the api's environment and network, no
published port, removed afterwards) against the live database, while the containers of the
previous release keep serving. New code never queries a column that is not there yet. This asks
one thing of every migration: the previous release must be able to run on the new schema, so a
change expands first and contracts in a later release ([CONTRIBUTING.md](../../CONTRIBUTING.md),
Migrations; `api/tests/test_migration_rules.py` flags a drop, a rename or a new `NOT NULL`). What
happens when it goes wrong:

- The migration fails: the upgrade is one transaction, Postgres rolls it back, the deploy stops
  with a non zero exit before anything restarts, and the previous release keeps serving. The log
  shows the step it failed in and the exception's class; alembic's whole output (SQL, Postgres'
  DETAIL) is in `~/jobapp-backups/logs/migrate-<time>.log` on the Jetson (`MIGRATE_LOG_DIR`, the
  newest 20 kept). Fix the migration and deploy again. A lock timeout (SQLSTATE 55P03: a statement
  waited 5 seconds for a lock the running release holds) is not a fault of the migration:
  `ops/migrate.sh` runs the upgrade again, three attempts in all (`MIGRATE_ATTEMPTS`,
  `MIGRATE_RETRY_WAIT_S` apart), then stops with "lock timeout: run the deploy again".
- The migration hangs (usually waiting for a lock the running release holds): it is stopped after
  `MIGRATE_TIMEOUT_S` (300 seconds by default), its session ended in Postgres so no lock stays
  behind, and the deploy stops the same way.
- The migration's container is killed before that limit (exit 137, for instance by the kernel when
  it runs out of the api's memory limit): the log says killed, not timed out, its session is
  ended in Postgres too, and the deploy stops the same way.
- The migration applies and the new stack does not come up: the workflow starts the previous
  images again on the migrated schema, which the expand rule makes safe. The schema is never
  downgraded automatically; `alembic downgrade` by hand or the predeploy dump are a human's call.
- At head it is a no-op of a couple of seconds. On a first install it starts `db` alone, creates
  the schema, and only then does the stack start.

Recovery by hand, from the Mac in `app/`, if the migration step itself misbehaves on the Jetson:

```bash
./deploy.sh ps                                   # the previous release should still be Up and healthy
ssh jetson 'docker ps -a --filter name=jobapp-migrate'    # a leftover one off container
ssh jetson 'docker rm -f jobapp-migrate'                  # remove it; this alone does not end its transaction
# Postgres notices a client that went away only when its running statement ends, and keeps the
# migration's locks until then. End the sessions of this database that hold or wait for the
# migration's advisory lock (ops/migrate.sh does it itself after a timeout or a kill); the
# statement prints the pids it ended, and nothing when there was none.
ssh jetson 'cd ~/jobapp && docker compose -f compose.yml --profile tunnel exec -T db sh -c "psql -X -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\""' <<'SQL'
select pid, pg_terminate_backend(pid) as ended from pg_locks
where locktype = 'advisory' and objsubid = 1
  and ((classid::bigint << 32) | objid::bigint) = 7406202610
  and database = (select oid from pg_database where datname = current_database())
  and pid <> pg_backend_pid();
SQL
ssh jetson 'cd ~/jobapp && docker compose -f compose.yml --profile tunnel exec -T api alembic current'   # the revision the database is at
ssh jetson 'cd ~/jobapp && bash ops/migrate.sh'           # run the step alone, safe to repeat
ssh jetson 'ls -t ~/jobapp-backups/logs | head -n 3'       # the kept outputs of the last migrations and smokes
./deploy.sh                                      # or deploy again by hand once the cause is fixed
```

`tests/migrate_test.sh` plays all of this with real Docker, Postgres and alembic on a scratch
stack of its own (first install, a failing, a hanging and a killed migration, the statement
above, an expanding migration under the old api, then `compose up`). It is run by hand on the
Mac, not in CI: it builds the api image four times, and the CI runners have a tight disk and CPU
budget. Run it when `ops/migrate.sh`, `api/alembic/env.py` or the api's Dockerfile change, with
no other scratch stack up (`docker ps`); it removes its containers, volumes and image at the end.
CI covers the same paths with fakes in `tests/deploy_test.sh`.

When the api is not running at
deploy time, the log warns that `jobapp-api:previous` is older than the saved tree. Before any of
this, `ops/env_check.sh` checks the syntax of `~/jobapp/.env` (`KEY=VALUE` with a shell variable
name, comments and blank lines only; a value with spaces or quotes in quotes, closed on its line),
naming each bad line by its number and never printing it; `./deploy.sh` runs the same check on
`.env.jetson` and on the Jetson's `.env` in its preflight, and `tests/jetson_smoke.sh` runs it
too. A started deploy is never cancelled. `gh workflow run deploy.yml -f ref=<sha of main>`
deploys a commit of main by hand; a branch is refused unless it is `hotfix/<name>` dispatched with
`-f allow_branch=true` (Only main is deployed, above; the next merge redeploys `main`). The
workflow itself always comes from `main`.
Each ref has its own concurrency group: GitHub keeps one pending run per group and replaces an
older pending one, so a branch dispatch never displaces a pending `main` deploy.

```bash
./deploy.sh runner install     # build ops/runner on the Jetson, register it (labels self-hosted, jetson, arm64), start it
./deploy.sh runner status      # the container on the Jetson and the runner as GitHub sees it (online, busy)
./deploy.sh runner remove      # unregister, remove the container and its volume
gh run list --workflow deploy.yml   # recent deploys; gh run view <id> --log for one
```

The runner is the official actions runner (arm64, pinned version and checksum) in a container,
so it needs no sudo: persistent (registered once, its credentials in the `jobapp-runner` volume,
self-updates kept), restart unless-stopped, host network, outbound connections only. It sees
`~/jobapp` and `~/jobapp-backups` at their own paths and drives the host's Docker through its
socket with the host's own Docker CLI. Registration and removal tokens come from `gh api` on the
Mac, reach the container on stdin and are never stored. Persistent rather than ephemeral: an
ephemeral runner must be registered again for every job, which needs a long-lived GitHub admin
token on the Jetson, a larger secret than the runner's own repository-scoped credential.

**Security.** Access to the Docker socket is equivalent to root on the Jetson, so this runner is
root on the Jetson for whatever job runs on it. It is registered to this private repository only,
and its job-started hook (`ops/runner/job-guard.sh`, set in the image as
`ACTIONS_RUNNER_HOOK_JOB_STARTED`, so no commit can turn it off) fails every job before its first
step unless it is this repository's `deploy.yml` from `refs/heads/main`, run by
`workflow_dispatch` (the runner on the Jetson takes that stricter rule at its next
`./deploy.sh runner install`; the image it runs today also accepts `workflow_run`, an event
`deploy.yml` no longer has). A pull request, a push to a branch or another workflow that targets
`[self-hosted, jetson]` is refused. Never add the runner to a public repository or allow fork
workflows. Dispatching a branch deploys and runs that branch's code on the Jetson (its compose
file and images), so dispatch only reviewed branches. Anyone with write access to the repository can run code on the Jetson.
