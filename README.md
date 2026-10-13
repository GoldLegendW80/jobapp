# jobapp

A self-hosted job-search app for Harald and a few invited friends. It finds postings, scores them against a profile, writes a tailored CV and letter per job, and lets an agent fill application forms in a live browser. It runs as a Docker Compose stack on a Jetson Orin Nano at home and is reached at a stable public URL through Tailscale Funnel.

## Documents

- [CAPABILITY-MAP.md](CAPABILITY-MAP.md): the modules, their dependencies and the build order.
- [SPEC-platform.md](SPEC-platform.md): the platform module (Compose stack, deploy, public URL through Tailscale Funnel), built first.
- [CONTRIBUTING.md](CONTRIBUTING.md): branches, pull requests, CI, review and automatic deploys.
- [tasks/plan.md](tasks/plan.md) and [tasks/todo.md](tasks/todo.md): the implementation plan and task list of the current module.

## Setup

```bash
cp .env.example .env           # then fill in real values; .env is never committed
pre-commit install             # ruff, prettier, eslint, gitleaks and file fixers on every commit
```

## Development on the Mac

```bash
docker compose -f compose.yml -f compose.dev.yml up --build    # api reloads; http://localhost via caddy
pnpm --dir web dev                                             # Vite with hot reload on :5173, /api proxied to :8000
docker compose exec api alembic upgrade head                   # apply migrations
docker compose exec api pytest                                 # backend tests
pnpm --dir web test                                            # frontend tests
pnpm --dir web lint && uv run --directory api ruff check .     # lint
tests/compose_smoke.sh                                         # whole-stack smoke test, then down
E2E_CADDY_URL=http://127.0.0.1 pnpm --dir web exec playwright test caddy.spec.ts   # browser through caddy
```

Caddy (`caddy/Caddyfile`) is the only service with a host port, `${HTTP_BIND:-127.0.0.1}:${HTTP_PORT:-80}`.
It serves the React build baked into its image by the `caddy` target of `web/Dockerfile`, falls back
to `index.html` for client routes, and proxies `/api/*` to `api:8000` with the prefix kept. Set
`HTTP_PORT=8080` in `.env` if port 80 is taken on the Mac.

`docker compose exec api pytest` needs a test database: the smoke script creates `jobapp_test` in the
compose `db` and runs `pytest -m "not docker"` with `TEST_DATABASE_URL` pointing at it.
`pytest -n 4` (pytest-xdist) runs four workers, each on a database of its own created next to
`TEST_DATABASE_URL`'s and dropped at the end; keep the `docker`-marked tests serial (`-m docker`
without `-n`). The suite hashes passwords with a cheap argon2 cost in-process; tests marked
`production_argon2` and a fresh-interpreter check keep the production cost honest.
Migrations live in `api/alembic/versions/`; models register on `app.models_base.Base` and are imported
in `api/app/models.py` so `alembic revision --autogenerate` sees them.

### Apple silicon: `OPENSSL_armcap=0`, on the Mac only

The api and the worker import `cryptography` (AES-GCM for the model credentials,
`api/app/agents/crypto.py`). Since 47.0.0 its wheels bundle OpenSSL 4, and inside Docker on an Apple
silicon Mac with SME (the M4) that OpenSSL dies with SIGILL the moment it is imported: the
containers exit with status 132 and restart forever. Reproduced with 50.0.2 on 2026-10-10
(issue #19); 46.0.7 did not crash, which is why it stayed pinned until then. `OPENSSL_armcap=0`
turns off OpenSSL's CPU detection, so it runs portable code: slower, same results.

- The dev stack has it: `compose.dev.yml` sets it for `api` and `worker`.
- A stack started from `compose.yml` alone (every scratch stack and e2e recipe below and at the top
  of `web/e2e/*.spec.ts`) adds `compose.mac.yml` to its `up`:
  `docker compose -p <project> -f compose.yml -f compose.mac.yml up -d --build --wait`. Only `up`
  and `run` need the file; `exec`, `logs`, `ps` and `down` work on the containers that exist.
  `tests/compose_smoke.sh` adds it by itself.
- pytest outside Docker (`uv run pytest` in `api/`) needs nothing: the macOS wheel does not crash,
  and the `docker`-marked tests only start Postgres containers.
- Never on the Jetson and never in `compose.yml`: its Cortex-A78AE has no SVE or SME, runs
  `cryptography` as it is and keeps hardware AES. Deploys use `compose.yml` alone, and
  `api/tests/test_openssl_armcap.py` fails if the variable appears in `compose.yml`, the Dockerfile,
  `.env.example`, the deploy scripts or the CI stack. CI runs on x86, where OpenSSL does not read it.

Symptom when the file is forgotten: `docker compose ps` shows `api` and `worker` restarting, and
`docker inspect -f '{{.State.ExitCode}}' <container>` prints 132.

## Deploy to the Jetson

```bash
./deploy.sh                    # preflight, rsync to jetson:~/jobapp, build there, migrate (before the new code starts), compose up --wait, health
./deploy.sh --env              # same, and replace the Jetson's .env with .env.jetson
./deploy.sh logs api           # follow the logs of one service (last 200 lines first)
./deploy.sh ps                 # docker compose ps on the Jetson
./deploy.sh stats              # memory and CPU per service
./deploy.sh ssh                # shell in the deploy directory
./deploy.sh url                # the stable public URL, https://jobapp.taild83d16.ts.net, alone on stdout
./deploy.sh smoke              # checks on the Jetson, then through the public URL; exit 1 if any fails
./deploy.sh smoke --full       # same, plus a database outage and recovery (stops and starts db once)
./deploy.sh backup [--pull]    # verified pg_dump -Fc to jetson:~/jobapp-backups/, --pull copies it to backups/
./deploy.sh backups            # list the dumps on the Jetson
./deploy.sh restore latest     # restore into the scratch database jobapp_restore_check, print row counts
./deploy.sh restore <file> --into-live   # replace the live database (type RESTORE jobapp to confirm)
./deploy.sh backup-schedule install|remove|status   # nightly backup in the Jetson user's crontab
./deploy.sh admin create <email>   # one-time admin setup link on the public URL (a reset link if that admin exists)
tests/deploy_test.sh           # local tests of deploy.sh: shellcheck, dispatcher, unreachable host, .rsyncignore, url, restore prompt, admin create
tests/migrate_test.sh          # by hand on the Mac, not in CI (real Docker, builds the api image four times): migrate with the new image while the old api serves, failing, hanging and killed migrations, then compose up
tests/jetson_smoke.sh          # smoke of the deployed stack over ssh (stops and starts db once)
tests/backup_test.sh           # backup and restore round trip and refusals on the Jetson (BACKUP_TEST_LIVE=1: live restore too)
tests/crash_check.sh           # kill each service's main process, check the daemon restarts it, then smoke
tests/reboot_check.sh          # run by a human: reboots the Jetson (sudo), checks the stack is back within 2 minutes
```

Images are built on the Jetson itself (arm64) with only `compose.yml`. `.rsyncignore` keeps
secrets, `node_modules`, `.venv`, `dist`, `.git` and evidence off the Jetson, and since `.env` is
excluded, `rsync --delete` never removes the Jetson's `.env`. The Jetson's production `.env` is
generated on the first deploy into the git-ignored `.env.jetson` (random `POSTGRES_PASSWORD` and
`SECRET_KEY`, `APP_ENV=production`, mode 600) and copied only when the Jetson has none or with
`--env`; `--env` refuses to change `POSTGRES_PASSWORD` once the `pgdata` volume exists. If
`.env.jetson` is lost, the next deploy copies the Jetson's `.env` back. Caddy listens on the
Jetson's loopback only; from the Mac, open it with `ssh -N -L 18080:127.0.0.1:80 jetson` and browse
http://127.0.0.1:18080. `DEPLOY_HOST` and `DEPLOY_DIR` override the target. Every green push to
`main` also deploys by itself (next section); `./deploy.sh` stays for the first deploy, a new
`.env`, and deploying by hand.

## Continuous delivery

Every change goes through a branch and a pull request ([CONTRIBUTING.md](CONTRIBUTING.md)). CI
(`.github/workflows/ci.yml`) runs on each pull request and push to `main`, in three jobs of about
the same length (What a CI run does, below): `api` (ruff, the whole pytest suite against
Postgres and Redis in one run, the 95 percent coverage floor on that run's data, the docker
marked tests), `checks` (pre-commit, shellcheck and the shell test scripts, then the e2e groups
apps and ingestion) and `journeys` (eslint, tsc, build, Vitest, then the e2e groups identity and
sweep); each
e2e group runs on a Compose stack of its own built on its runner, the Playwright journeys with
throwaway accounts and a synthetic jobs import, never Harald's data. The run is green when the
three jobs are. A pull request that changes documentation only runs pre-commit alone (and the
shell tests when it touches this README, which they check); every push to `main` runs
everything. Since 2026-10-10 the jobs run on self-hosted
runners on Harald's Mac (CI runners on the Mac, below), because the free minutes of GitHub's own
machines were used up; the Jetson's runner keeps the deploys and nothing uses paid minutes. When
CI is green for a push to `main`, `.github/workflows/deploy.yml` runs on the
Jetson's self-hosted runner, under the Jetson's deploy lock (`flock` on
`~/jobapp-backups/.deploy.lock`, which `./deploy.sh` takes too, so the two never overlap; the Jetson
lets go of the Mac's hold after `DEPLOY_LOCK_MAX_S`, an hour by default, so a Mac that sleeps mid
deploy cannot block GitHub's deploys for hours): rsync of
that commit into `~/jobapp` (same `.rsyncignore`, `.env` kept; the tree it replaces is copied to
`~/jobapp-backups/previous-tree` first), images built first (a failed build leaves the running
stack untouched and puts those files back; beforehand, `ops/previous.sh tag-running` tags as
`:previous` the image each running api and caddy container was started from, read with
`docker inspect`, so `:previous` is always an image that ran), a
backup into `~/jobapp-backups/deploys/` whenever the database volume exists (db started first if
it was stopped; newest 10 kept, separate from the nightly ones), the migrations (next
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
change expands first and contracts in a later release ([CONTRIBUTING.md](CONTRIBUTING.md),
Migrations; `api/tests/test_migration_rules.py` flags a drop, a rename or a new `NOT NULL`). What
happens when it goes wrong:

- The migration fails: the upgrade is one transaction, Postgres rolls it back, the deploy stops
  with a non zero exit before anything restarts, and the previous release keeps serving. The log
  shows alembic's error. Fix the migration and deploy again.
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
too. A started deploy is never cancelled. `gh workflow run deploy.yml -f ref=<branch or sha>` deploys a branch by hand to test it
before the merge (the next merge redeploys `main`); the workflow itself always comes from `main`.
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
step unless it is this repository's `deploy.yml` from `refs/heads/main`, run by `workflow_run` or
`workflow_dispatch`. A pull request, a push to a branch or another workflow that targets
`[self-hosted, jetson]` is refused. Never add the runner to a public repository or allow fork
workflows. Dispatching a branch deploys and runs that branch's code on the Jetson (its compose
file and images), so dispatch only reviewed branches. Anyone with write access to the repository can run code on the Jetson.

### What a CI run does

Three jobs (issue #91), and the run is green when the three are. The split follows what was
measured on the Mac's runners on 2026-10-10 with the eleven jobs CI had until then, on two
runners: 18.1 to 20.0 minutes of runner time in four runs (22.8 in a fifth, with a first
download of Chromium that stalled for 220 s), and 13.3 and 14.3 minutes from push to result
for the two runs that had the runners to themselves (38072303967, 38076641000). Three findings
shaped the new layout.

- **A job costs about 25 s before its first test**: set up 3 s, checkout 4 s, pnpm and node 5 to
  14 s, service containers 10 s, the hooks and post steps 5 s. Eleven jobs paid it eleven times,
  for jobs as short as 8 s of pre-commit. Three jobs pay it three times.
- **GitHub starts queued jobs in no particular order.** With more jobs than runners a 20 s job
  took a runner while a 4 minute job waited, so the run ended minutes after it had to. Three
  jobs for three runners all start at once; there is no order left to get wrong.
- **The e2e groups wait, the api tests compute.** A Playwright group used about 0.6 of its 3
  CPUs, the api tests 2.1 to 3. So the fast, CPU bound checks sit in front of the e2e groups in
  two of the jobs, and the api suite has the third to itself.

| job | what runs, in this order | the same steps in the old jobs | now (run 38098708076) |
|---|---|---|---|
| `api` | ruff; the whole pytest suite in one run on 3 workers; the coverage floor (95 percent of that run's data); the docker marked tests; the scoring timing test | pytest 101 s and 146 s in two shards, docker tests 18 s, service containers 10 s | 285 s: pytest 242 s, docker tests 18 s, service containers 5 s |
| `checks` | pre-commit and the secret scan of the run's commits; shellcheck and every `tests/*.sh` of CI, the e2e groups guard, the plan's own test; then the e2e group apps, then the e2e group ingestion (since 2026-10-11), each on a scratch stack of its own | pre-commit 8 s, shell 46 s, apps 173 s of journeys, stack up in 12 s and down in 11 s | 301 s: pre-commit 11 s, the secret scan under 1 s, shell 66 s, apps 185 s, stack up and migrated in 8 s, down in 1 s |
| `journeys` | eslint, tsc, build; Vitest in one run; the e2e group identity, then the e2e group sweep, each on a scratch stack of its own | web checks 29 s, Vitest 34 s and 25 s in two shards, identity 114 s, sweep 68 s | 311 s: web checks 32 s, Vitest 63 s, identity 114 s, sweep 63 s, each stack up in 5 to 6 s |

Which job holds which unit follows from its lengths: the api tests fill their job, the shell
unit sits in `checks`, and the web checks moved from `checks` to `journeys` after the first run
(255, 320 and 262 s there). Moving a unit is a cut and paste of its steps and one line of
`LANES` in `ci_plan.py`.

The e2e group ingestion (the documents of the profile, `web/e2e/ingestion-*.spec.ts`) joined on
2026-10-11, after the table above was measured, so its seconds are not in it. It is a block of
steps of the e2e unit at the end of `checks`, on a second scratch stack that starts once the
apps stack is gone: of the two jobs that already build the images, `checks` was the shorter in
two of the three runs above. As a job of its own in the old layout it took 79 s, set up
included. Its step names a Playwright config, not spec files: the config runs every
`ingestion-*.spec.ts`, so a task of that module adds a spec without touching the workflow.

**Measured, before and after** (2026-10-10 and 11, two runners online, each run started when no
other run was queued unless said; runner time is the sum of the jobs):

| case | before: eleven jobs | now: three jobs |
|---|---|---|
| a code pull request, everything runs | 13.3 and 14.3 min from start to result; 18.1 to 20.0 min of runner time in four runs | 8.7, 9.4 and 10.0 min; 13.3, 14.1 and 14.9 min of runner time (38080515377 twice, 38098708076). A fourth run took 11.9 and 22.8 min: GitHub's downloads stalled for 9 of them (a checkout of 394 s, a set up of 154 s) |
| documentation only (`tasks/`) | everything: 19.1 min of runner time (#38, run 38073436439) | 44, 44 and 45 s; 0.9 min of runner time (38080574728, three times) |
| the README alone | everything | 1.7 min; 1.9 min of runner time (38096293562) |
| `web/` alone | everything | 5.7 min; 10.8 min of runner time (38096406836) |
| a push to `main` (now: a manual run on the branch, the same plan) | 14.3 min; 20.0 min of runner time (38076641000) | 9.1, 9.2 and 9.4 min; 13.4, 13.9 and 14.7 min of runner time (38102580794, 38102061062, 38101529093) |
| a pull request against another branch than `main` | everything | everything, by rule: 9.1 min; 13.8 min of runner time (38103107022, the probe #94 that ran pre-commit alone before the rule: one file under `tasks/`) |

Per job, in seconds, the same steps in their old jobs and in their new one: `api` 336 (api
(jobs) 129, api (rest) 190, api coverage 17) against 260, 266 and 285; `checks` 299 (pre-commit
20, shell 57, e2e (apps) 222) against 281, 284 and 301; `journeys` 430 to 510 (web checks 52,
Vitest 66 and 47, e2e (identity) 170 to 200, e2e (sweep) 92 to 144) against 258, 294 and 311.
With two runners two jobs start at once and the third waits for the first to end, 258 to 289
s, which is the whole queue time of a run; with the third runner all three start together and
a run takes as long as its longest job, about 5 minutes (not measured: the third runner is not
installed yet). During the full runs the load average of the Mac peaked between 4.0 and 8.0
with agents at work beside CI (11.6 once, during a 44 s documentation run, so not CI's doing),
a runner's memory between 2.0 and 3.5 GB, each runner used 0.3 to 1.6 of its 3 CPUs on average
over a run, and the free disk space did not move by more than 1 GB across any run. One more
manual run of the eleven jobs, on a branch at `main`'s head with the
runners to itself, took 20.1 min and 34.6 min of runner time (38081748125): 11 of those minutes
were two stalled downloads from GitHub, the kind of delay the layout cannot remove and three
jobs meet three times instead of eleven.

There is no fourth job that sums the three up. The first version had one, a gate that needed
the three and checked their results: created when they ended, it queued behind every job other
runs had queued meanwhile and waited 8 minutes for a runner to do 7 seconds of work, so the run
that was done after 9.7 minutes reported after 17.9. Nothing is lost without it: no job has a
condition or waits for another, so none can be skipped (`ci_plan_test.py` and
`tests/ci_runners_test.sh` fail if one gets either), a failed job fails the run, and each job
checks its own steps against the plan (below).

What changed inside the jobs, each with its measured effect:

- **One pytest run instead of two shards and a combining job.** Every test is collected once by
  construction (`tests`, split only by the docker marker), the floor is read from the run's own
  data, and no artifact travels between runners. Sharding by measured duration was the other
  option: it needs a second set of service containers, a durations file to maintain, a partition
  test and the combining job (17 s), and the api job is not the longest of the three, so it
  would not end the run a second earlier.
- **The api tests themselves** (api/tests/identity/conftest.py, api/tests/pg.py). Of 139 identity
  tests, 80 percent of the time was the fixture around each test, not the test. `alembic upgrade
  head` ran before every test although the schema was already at head: now the version table is
  asked first. The TRUNCATE after every test took 87 ms on a Postgres that syncs to disk: the
  test server runs without fsync (its data is thrown away), 38 ms. The fixture opened two
  connections per test: it keeps one. On the Mac the 139 tests went from 33.1 s to 20.1 s. The
  whole suite on a runner is still about four minutes of 3 CPUs when the other runners are
  busy: what is left is mostly the next item.
- **Scratch stacks start and stop faster.** `up --wait` waited for the first health probe of
  each service, one interval after its start (5 to 15 s); the CI overlay
  (`tests/ats-stub.compose.yml`) probes every second while a service starts: on the runners a
  stack was up in 11 to 13 s before and is up in 5 s now. `down` waited for containers to stop
  that are thrown away with their volumes, 11 s per stack: with `--timeout 0`, 1 s. The same
  probing on the service containers of the api job: 10 s before, 4 s now.
- **Images are built in each runner's own Docker daemon, not once per run.** Measured: 22 s on
  a runner that never built them, under 2 s after. Sharing them through a registry or a saved
  archive would cost more than the 2 s it replaces.
- **Not done, with the reason.** Two e2e groups at once in one job (they would fit the CPUs):
  about 25 s saved for two stacks in a 4 GB runner and interleaved logs. One CPU set shared by
  the runners, so that the api tests could take what the e2e groups leave idle: about 25 s
  again, and the journeys would compete with pytest for the same CPUs. One FastAPI app shared
  by the api tests instead of one per test: FastAPI builds the state of its 55 routes per app,
  about 73 ms of the roughly 130 ms an identity test takes on the Mac after the changes above,
  the largest cost left; but sharing an app means resetting every piece of its state between
  tests: a change to the tests themselves, for an issue of its own.

**The plan: what a pull request has to run.** The first step of each job
(`.github/scripts/ci_plan.py`) reads the files the pull request changes (its merge commit against
`main`) and decides which units run: `api`, `web`, `shell`, `e2e`. Only a pull request against
`main` is narrowed. One against another branch (a stacked pull request) runs everything: its
files against that branch say nothing about what `main` receives, and changing its base to
`main` later starts no run, so narrowed checks would stay green on code no run had tested. The
rules are a short list in that script, first match wins:

| the pull request touches only | units | why the rest may be skipped |
|---|---|---|
| `tasks/`, `docs/`, `CONTRIBUTING.md`, `CAPABILITY-MAP.md`, the `SPEC-*.md` files, the pull request template (a Markdown file at the root that is none of these runs everything) | none: pre-commit alone | nothing executable reads them (a test fails when something starts to) |
| `README.md` | `shell` | `tests/deploy_test.sh` checks that the recovery statements it tests are the ones written here |
| `tasks/evidence/agents-a5/` | `shell` | `tests/agents_real_run_test.sh` compares against it |
| `web/` | `web`, `shell`, `e2e` | no api file reads a file of `web/` (a test fails when one starts to) |
| anything else, or a mix with anything else | all four | a path no rule names runs everything |

A push to `main`, a manual run, a pull request whose base is not `main` and any run where the
changed files cannot be read for certain run everything: a green run on `main` is what deploys.
Each job ends with `ci_plan.py verify`,
which fails it when a step the plan needed was skipped, or one it left out ran, so a wrong
condition in the workflow cannot pass for a test that did not run (and it fails on a job status
it does not know: only `failure` and `cancelled` skip the judgement). `ci_plan_test.py` checks
the rules, their reasons on this repository (it found the README check above the day it was
written), and that the workflow and the plan agree in both directions. From the plan: every
check listed under a unit in `LANES` exists in its job and runs under that unit's output. From
the workflow: every step of `ci.yml` has an `id` and is listed in `ci_plan.py` exactly once,
as a check of a unit (`LANES`), as a step of every run (`ALWAYS`: pre-commit and the secret
scan) or as a support step (`SUPPORT`: checkout, the plan, tools and installs, the start of a
stack, logs, teardown, summaries, uploads), and a support step runs no test command. What that
does not check: whether a new command that is a check was written as one; a reviewer reads a
change of `SUPPORT` as a change of the gate.
Within a job a failed check does not stop the checks after it: each unit reports for itself, and
the job fails if any step failed.

What the documentation rules rest on is itself a scan with limits. It reads the Python of
`api/`, `tests/` and `.github/scripts`, the TypeScript of `web/`, the shell of `tests/`, `ops/`
and `deploy.sh`, the Dockerfiles, the Compose files, `caddy/` and both workflows, and fails
when one of them names a documentation path whose rule does not run its unit (also when it
names `tasks` or `docs` as a bare word, or looks for `*.md`). It cannot see a path put
together at run time from variables, and it leaves the folder `tasks/evidence` alone when no
subfolder is named, because the journeys write their screenshots there.

**The secret scan.** Every run, whatever the plan, reads the commits it brings with gitleaks
(`.github/scripts/secret_scan.sh`, the step after pre-commit in `checks`): for a pull request
its commits and then the pull request as a whole (its merge commit against `main`), for
a push to `main` the commits of the push, for a manual run the whole history of the branch.
The commits are read three ways: each one on its own, so a secret added by one commit and
removed by the next is found; each merge commit for the lines somebody typed into it
(`git log --remerge-diff`, git 2.36 or later, refused loudly on an older one), because plain
`git log` prints no patch for a merge commit; and the commit messages. Not read, here or at a
commit: a binary file, for which git prints no patch. It
is the gitleaks that `.pre-commit-config.yaml` pins, started through pre-commit
(`pre-commit run gitleaks-commits --hook-stage manual`, which works locally too), with the
same rules and the same allowlist as at a commit: the default rules, a `gitleaks:allow` comment
on a line, and `.gitleaks.toml` or `.gitleaksignore` should the repository ever get one. Until
2026-10-11 CI had no secret scan in effect: the gitleaks hook reads the staged changes, a
checkout has none, and `pre-commit run --all-files` passed with "0 commits scanned" whatever
had been committed (shown in a throwaway clone with the example secret of gitleaks' own
README; `tests/secret_scan_test.sh` keeps showing it, and plants that example as a pull
request, as a push, in a commit that a later one reverts, in a merge inside the branch that
the next commit cleans again, in a commit message: the scan must fail each time). One scan of
the whole history on that day, every branch on GitHub included, found nothing; the reviewer
read the merge commits' own lines of every ref the same day, with the same result. The job that scans fetches the whole history
(`fetch-depth: 0`); the script refuses a shallow checkout, where it would read a history with
holes. When it fails on a real secret: revoke the secret where it is valid first, whatever
happens to the commit afterwards. The same holds for a secret that was pushed and then removed
by a forced push: if the next push cancelled its run, no scan ever read it, and it stays on
GitHub although it never reaches `main`. When it fails on a false alarm: a `gitleaks:allow`
comment on the line, in the same pull request. That is also the limit of the scan: a pull
request is judged by the rules of its own tree, so it can bring the allowance that lets its
own secret through (a comment, a `.gitleaks.toml`, a `.gitleaksignore`). The scan says so in
its log when a pull request touches any of them, and a reviewer reads them as a change to the
gate (CONTRIBUTING.md, step 5). Two things keep the step from passing without having read: it
runs under `env -u SKIP`, since pre-commit would skip it on a `SKIP` that names gitleaks, and
the pre-commit step before it prints no diff of what a fixer changed, which would show the
lines in clear in the log.

**A fresh run for an open pull request** after `main` moved: `ops/ci-rerun.sh <number>`. CI
tests a pull request at its merge commit with the base, which GitHub recomputes lazily, so
`gh run rerun` (the old commit), `gh workflow run` (the branch alone) and a quick close and
reopen (the stale merge commit) all test the wrong thing. The script waits until GitHub's merge
commit is the base as it is now plus the branch, then closes and reopens; `--check` only tells.

### CI runners on the Mac

CI runs on self-hosted runners on the Mac (issue #83), `mac-ci-1` to `mac-ci-3`, labels
`self-hosted, Linux, ARM64, mac-ci`: three, one per job of a run (the workflow is the same with
one or two, the jobs then queue). Every job of `ci.yml` targets `[self-hosted, mac-ci]`, never
`self-hosted` alone, so a CI job cannot land on the Jetson's runner, and `deploy.yml` keeps
`[self-hosted, jetson]`.

```bash
ops/ci-runners.sh install [count]   # build the image, register and start the runners (default 3, the most; as many as exist when there are some); safe to run again
ops/ci-runners.sh status            # containers, whether each listens for jobs, GitHub's view (online, busy), memory, disk used
ops/ci-runners.sh stop              # stop them; queued jobs wait
ops/ci-runners.sh start             # start them again, back when each listens for jobs
ops/ci-runners.sh update            # rebuild the image from ops/ci-runner, recreate each container when its runner is idle (registration and caches kept)
ops/ci-runners.sh prune             # empty the tool caches and every runner's Docker daemon
ops/ci-runners.sh uninstall         # unregister, remove containers, volumes and image
```

**Shape.** Each runner is the official actions runner for Linux arm64 (version and SHA-256
pinned in `ops/ci-runner/Dockerfile`, checked at build) in a container of Docker Desktop, with a
Docker daemon of its own inside (the same Engine and Compose versions as the Jetson). Chosen over
runners installed natively on macOS for three reasons. It is the Jetson's platform, Linux arm64,
so tests run on what production runs on. GitHub's `services:` and container features need a Linux
runner with Docker, so the workflow is the one that ran on GitHub's machines, and one variable
sends it back there. And a job sees a machine of its own: its own `127.0.0.1`, container names,
Compose project and ports, so parallel jobs cannot collide, and nothing a job starts, stops or
prunes is in the Mac's own Docker daemon, where Harald's containers and the agents' scratch
stacks live. A native runner would run job code as Harald's macOS user, next to his keys.

**Where things live.** Nothing in the repository, and in the home folder only the empty folder
`~/actions-runners/jobapp-ci/disk` (Disk budget, below): the image
`jobapp-ci-runner:<version>`, the containers `jobapp-ci-1` to `jobapp-ci-3` (restart
unless-stopped), the volume `jobapp-ci` (each runner's directory, registration and work folder,
plus the caches the runners share: uv, the pnpm store, Playwright's Chromium, pre-commit) and one
volume `jobapp-ci-docker-<n>` per runner (its Docker daemon's images and layer cache). All carry
the label `jobapp.ci=runner`. To go from two runners to three: `ops/ci-runners.sh install 3`
(the script refuses more, and refuses to go down: `uninstall` first).

**Budget.** The Mac is shared: Harald works on it, and agents run scratch stacks and test suites
in the same Docker Desktop (12 CPUs, about 18 GB). On 2026-10-10 three runner trials on top of
four agents took the load average to 82 while the disk filled, and the Mac went down, so CI has a fixed share and
cannot take more:

| | per runner | three runners | what was measured |
|---|---|---|---|
| CPUs | 3, pinned (runner n: CPUs 3(n-1) to 3n-1) | 9 of Docker's 12 at most (macOS places Docker's virtual CPUs on the cores it likes) | the api tests used 2.1 to 3 CPUs, a Playwright group about 0.6, Vitest 1.2 to 2.3 for under a minute; so a whole run of the three jobs is expected at 4 to 6 cores' worth |
| Memory | 4 GB, swap included | 12 GB of about 18 at most | in the samples of `docker stats`, every 10 s over the measured runs of the three jobs: up to 3.5 GB on a runner, and the peaks of two runners in one run add up to 5.2 to 6.3 GB; the kernel's own count (`memory.peak` of the runner's container, file cache included) stands at the 4 GB limit on both runners, which was reached 1,641 and 3,092 times in their first 3 hours and about 68 jobs each (`memory.events`, `max`) and answered by giving cache back each time: `oom` 0, `oom_kill` 0. Every job writes these counters to its summary |
| Disk | see the disk budget below | about 14 GB, 20 GB at most | 11.1 GB for two runners after eight full runs |

That leaves Harald's own work and one agent's stack 3 of Docker's CPUs at all times and 6 or
more in practice, and about 10 GB of Docker's memory. A runner takes one job at a time, so three
runners are the bound on what runs at once, whatever the number of pushes: the other jobs queue,
and a newer push to the same branch cancels the older run. Inside a job the worker counts are
written out in `ci.yml` (`PYTEST_WORKERS` and `VITEST_WORKERS`, 3 each, never "all cores";
Playwright runs one worker). Why not more: a fourth runner would take the last 3 CPUs for a
workflow that has three jobs, and 4 CPUs per runner would speed up only the api job, which is not
the one the run waits for. Why not less memory: the samples reach 3.5 GB of the 4, and the
limit is a ceiling, not a reservation, so lowering it frees nothing. Why not more: nothing was
killed at 4 GB, and what fills it is file cache.

**Disk budget.** Disk is the scarcest resource of this Mac, and a full disk does not fail one
job: it corrupts Docker Desktop's store for every container (2026-10-10: a trial of three e2e
groups at once on a disk with 22 GB free, next to 21.8 GB of dangling volumes left by test runs,
filled it). Everything of CI sits in Docker Desktop's disk image, which grows on the Mac's own
disk:

| | size | bound |
|---|---|---|
| Runner image, stored once | 1.9 GB | fixed |
| Docker data of a runner (images, layer cache): volume `jobapp-ci-docker-<n>` | 2.4 and 2.5 GB after eight full runs | 4 GB (`CI_DOCKER_MAX_MB`), then emptied; build cache alone 1 GB (`CI_BUILD_CACHE_KEEP`) |
| Runner directories and shared tool caches (uv, pnpm store, Chromium, pre-commit): volume `jobapp-ci` | 4.3 GB for two runners after eight full runs | the caches grow only when a lock file changes; `prune` empties them |
| Work folder of a job | emptied after every job | 0 between jobs |

Measured after eight full runs on two runners: 11.1 GB in all, with the Docker data of a
runner where a single run leaves it (2.4 GB after the three e2e groups of a trial). For three
runners that is about 14 GB, 20 GB at the bounds. Three things enforce it.
`install` refuses a Mac with less free space than 4 GB per runner plus 6
(`CI_RUNNER_MIN_FREE_GB`). Before every job, the job-started hook reads the Mac's free space
and fails the job with the figure when it is under 10 GB (`CI_MIN_FREE_GB`): the hook sees the
Mac's disk through `~/actions-runners/jobapp-ci/disk`, an empty folder mounted read only, the
only thing of the Mac a runner sees. After every job, the job-completed hook removes the job's
containers with their volumes, its networks, every unused volume, dangling images and build
cache beyond 1 GB, and empties the work folder (checkout, `node_modules`, virtualenv,
Playwright's reports and traces, which are uploaded as artifacts by then, kept 14 days by
GitHub). Runs do not add up: the caches are keyed by content, so a second run of the same
commit adds nothing (2,404 MB of Docker data after the first e2e group, 2,407 MB after the
third).

The leak behind the dangling volumes: the Postgres, Redis and caddy images declare anonymous
volumes, and the test helpers removed their throwaway containers without them. Fixed at the
source: `api/tests/pg.py` and `api/tests/redis_server.py` (every local `pytest` run without
`TEST_DATABASE_URL`, and the docker-marked tests), `tests/tailscale_test.sh`,
`tests/compose_smoke.sh` and the three `tests/e2e_*jetson.sh` now remove with `-v`, and
`tests/ci_runners_test.sh` fails if one stops doing so. On a scratch stack, `docker compose down`
without `-v` keeps its named volumes on purpose; finish with `down -v`.

**When the Mac is off, asleep or Docker Desktop is closed.** The runners are offline and CI
waits: jobs stay queued (GitHub keeps a queued job for 24 hours, then fails it) and start when
the Mac is back. A job that was running when the Mac went to sleep can fail with a lost runner:
rerun it (`gh run rerun <id> --failed`). Nothing deploys meanwhile, since the deploy waits for a
green CI on `main`. No power setting was changed; for a long unattended session, `caffeinate -s`
in a terminal keeps a Mac on mains power awake while it runs.

**When a runner is recreated** (`update`, `install` on an existing set): one at a time, each
only when GitHub reports it idle (a call GitHub does not answer stops the command: unknown is
not idle), stopped so that its listener ends its session, and the command does not go on before
the new container's log says "Listening for Jobs". That last wait exists because GitHub shows
a runner as online before its listener has a session: on 2026-10-10 a run queued in that gap
(38071312967) sat queued for 14 minutes and was never assigned, with both runners online. If a
run stays queued while `ops/ci-runners.sh status` shows the runners listening and idle, GitHub
will not hand it over any more: cancel it and start it again (`gh run cancel <id>`, then
`gh run rerun <id>` for a push or a manual run, `ops/ci-rerun.sh <number>` for a pull request).

**After a reboot.** The runner containers have `restart: unless-stopped`: they start with the
Docker daemon, register as online within a minute and take the queued jobs, with nothing to
type. The daemon is Docker Desktop, and whether it starts when Harald logs in is its own
setting, off on 2026-10-10: Docker Desktop, Settings, General, tick "Start Docker Desktop when
you sign in to your computer". Until that box is ticked, a reboot leaves CI off until Docker
Desktop is opened by hand. `ops/ci-runners.sh install` and `status` read the setting and say
which case applies; they never change it. The Mac must also reach the login session: with
FileVault a reboot stops at the login window until someone signs in. A job cut by the reboot
is rerun as above; its leftovers are removed when the container starts and again before the next
job.

**Back to GitHub's machines.** One repository variable, no edit of the workflow:

```bash
gh variable set CI_RUNS_ON --body '"ubuntu-latest"'    # CI on GitHub's runners (uses the free minutes)
gh variable delete CI_RUNS_ON                          # back on the Mac
```

`CI_RUNS_ON` is the JSON of a `runs-on` value; unset, it is `["self-hosted", "mac-ci"]`. The
steps that differ test `runner.environment`: on GitHub's machines the caches go to GitHub's cache
service as before, on the Mac they stay on the runner. The three jobs and the worker counts
(`PYTEST_WORKERS`, `VITEST_WORKERS`) apply there too: correct, and slower than the eleven
parallel jobs `main` once had on those machines, where runners were not the scarce thing.

**Updating.** `ops/ci-runners.sh update`, run from a checkout that holds the wanted
`ops/ci-runner/`, rebuilds the image and recreates the containers that do not run it yet, one at
a time, each when its runner is idle: it waits for a running job to end (up to an hour,
`CI_UPDATE_WAIT_S`), so it is safe while jobs are queued, and running it again changes nothing.
A job that GitHub hands to a runner in the very second its container is recreated is lost and
shows as failed: `gh run rerun <id> --failed`. `update` changes the Docker Engine, the packages
and the hooks of the image. It does not move the runner itself: its binaries live in the
`jobapp-ci` volume, seeded once from the image, and update themselves when GitHub publishes a
release. `RUNNER_VERSION` and its SHA-256 in the Dockerfile therefore only set what a new
runner starts from; to start again from the pinned version, `uninstall` then `install`.
`install` on an existing set follows the same rule (idle runners only, containers already on the
image left alone) and refuses a lower count than what exists.

**Security.** Read this as guard rails against mistakes, not as a boundary against hostile code.

What the setup does: the runners are registered to this private repository only, and a
job-started hook in the image (`ops/ci-runner/job-started.sh`) refuses every job that is not
this repository's `ci.yml`. A container gets no variable of the shell that installs it (no
token, no key, no `.env`) and not the Mac's Docker socket; the only thing of the Mac it sees is
the empty probe folder, read only. The registration token goes from `gh api` into the
container's stdin and is stored nowhere. Jobs use throwaway secrets (`.github/scripts/ci_env.sh`)
and synthetic data, never the Jetson's database or Harald's files. `ci.yml` uses no repository
secret and its token can only read the repository.

What it does not do: `--privileged` is not a wall. A job is root in a privileged container (it
has passwordless sudo and a Docker daemon), so code pushed to any branch of this repository can,
on purpose, read the runners' long lived credentials (they sit in the `jobapp-ci` volume, which
both runners share), rewrite the hooks or the runner's files so that later jobs skip them, reach
Docker Desktop's virtual machine with every other container and volume in it, and through Docker
Desktop's file sharing reach `/Users`, so Harald's home folder. It can also leave a poisoned
entry in the shared caches for the next run (`ops/ci-runners.sh prune` empties them).

This is acceptable today for one reason: the repository is private, its only collaborator is its
owner, and the same agents already run code as his macOS user. It stops being acceptable, and the
runners must be uninstalled or moved first, as soon as any of these is true:

- the repository becomes public, or fork pull requests are allowed to run workflows;
- a second collaborator, or a bot that opens pull requests (Dependabot, Renovate), is added;
- a workflow that targets `mac-ci` gets a `pull_request_target`, `workflow_run`, `issue_comment`
  or `issues` trigger;
- a repository secret is given to `ci.yml`.

The upgrade then is a virtual machine of its own for CI (Lima or Colima), or ephemeral runners
with a fresh volume per job.

**Apple silicon.** The runners sit in the same Linux guest as every other container of this Mac,
so they need `OPENSSL_armcap=0` too (Development on the Mac, above). The runner image sets it
for a job's own processes and sets `CI_COMPOSE_OVERLAY=compose.mac.yml`, which the e2e jobs add
to their `compose up`. `ci.yml` never names the variable and `compose.yml` never has it.

To try a CI step by hand in the runner's exact environment, without registering anything:

```bash
ops/ci-runners.sh try                         # a shell in a container shaped like a runner
ops/ci-runners.sh try tests/deploy_test.sh    # or one command
```

It is a privileged container too (it runs a Docker daemon), on Docker's last 3 CPUs with the
runner's memory limit, and it starts empty: copy the checkout in with `docker cp . jobapp-ci-try:/ci/src`.
With three runners busy a trial is a fourth consumer: it takes those last 3 CPUs and brings CI
to 16 of about 18 GB at the limits, the share the budget leaves to Harald's own work, so run
one when CI is quiet.
Its container and its two scratch volumes (`jobapp-ci-try`, `jobapp-ci-try-docker`, a few GB
once images are built) are removed when the command ends and when the script is stopped
(Ctrl C, a closed terminal, a kill). A trial ended with `kill -9`, cut by a crash, or started
with a plain `docker run`, leaves them behind: `status` names them, `prune` and `uninstall`
remove them, these three names and no look-alike.

## Public URL

The service `tailscale` puts the Jetson on Harald's tailnet (`taild83d16.ts.net`, free plan) and
opens Caddy to the internet with [Tailscale Funnel](https://tailscale.com/kb/1223/funnel). The URL
is stable, `https://jobapp.taild83d16.ts.net` (the node name `TS_HOSTNAME` plus the tailnet), so
OAuth redirect URIs can point at it. It survives restarts, redeploys and reboots. The container only
makes outbound connections (no port opened on the router, none published on the Jetson) and runs
with userspace networking: no `/dev/net/tun`, no `NET_ADMIN`, every capability dropped, no sudo.

```bash
./deploy.sh url                          # https://jobapp.taild83d16.ts.net
curl "$(./deploy.sh url)/api/health"     # {"status":"ok"} from anywhere
```

`./deploy.sh url` asks the running container (`tailscale status --json`, `Self.DNSName`) and waits
up to 30s for it to connect. A missing or stopped container, a node that is not logged in (it then
shows the login link) or one without a ts.net name is an exit 1 with the reason, nothing on stdout.
If another device of the tailnet already holds the name, the node becomes `jobapp-1`: `url` prints
the real name with a warning, and the old device must be removed in the admin console (Machines).
`./deploy.sh` prints the URL at the end; `.github/workflows/deploy.yml` puts it in the job summary.

**Login.** The first start needs a one-time auth key from the admin console (Settings, Keys), as
`TS_AUTHKEY=` in the Jetson's `~/jobapp/.env` next to `TS_HOSTNAME=jobapp`. It is read once
(`TS_AUTH_ONCE`): the node's login, key and HTTPS certificate then live in the `tsstate` volume, so
**remove `TS_AUTHKEY` from the Jetson's `.env` (and from `.env.jetson`) once `./deploy.sh url`
answers**. A new key is needed only if `tsstate` is deleted or the node is removed from the
tailnet. Without a key and without a saved login the container never turns healthy (its logs say
`To authenticate, visit: ...`, then `failed to auth tailscale`), so a deploy fails loudly. The api
reads the same `.env`, so the key is in its environment until it is removed: another reason to
remove it. `./deploy.sh` warns at its end, once the node is logged in, when `TS_AUTHKEY` still has
a value in `.env.jetson` or in the Jetson's `.env` (it never edits either file), and
`tests/jetson_smoke.sh` warns the same way; the key was removed from the Jetson on 2026-10-10. Funnel needs the `funnel` node attribute in the tailnet policy (granted to
`autogroup:member`) and HTTPS certificates enabled in the tailnet, both set on 2026-10-10.

**Headers.** `tailscale/serve.json` (its folder mounted read only, `TS_SERVE_CONFIG`) terminates TLS for
`${TS_CERT_DOMAIN}:443`, proxies everything to Caddy's own listener `caddy:8080` (never
published) and allows Funnel on that port only. Funnel's proxy sets `X-Forwarded-For` to the
address the visitor connected from, replacing anything the visitor sent, and `X-Forwarded-Proto:
https`. The `:8080` listener trusts the Compose network as a proxy and reads the client from
`X-Forwarded-For` only (strictly right to left); `Cf-Connecting-Ip`, `X-Real-Ip` and the like pass
through Funnel untouched, so they are never trusted. The published `:80` trusts nobody. Either way
the api receives exactly one `X-Forwarded-For` and logs it as `client`. `./deploy.sh smoke` proves
it from outside: one request forging every client address header, and the api must log this
machine's public address. `tests/tailscale_test.sh` checks the Compose definition, Caddy's trust
rules and a start without a key, locally and without joining the tailnet.

The service sits in the Compose profile `tunnel`, which `deploy.sh` enables on the Jetson. Neither
the dev stack nor a plain `docker compose -f compose.yml up` on the Mac joins the tailnet.

### Request limits (issue #100)

The app is on the internet through Funnel, so a caller with no session must not be able to make
the api hold memory with large or slow request bodies. FastAPI reads a JSON body before a route's
dependencies run, so even a route that answers 401 reads the body first. The limits are set in
three layers, each able to stand without the others.

- **Caddy** (`caddy/Caddyfile`). The public routes under `/api/auth/` take at most **64 KiB** (a
  sign in or an invite acceptance is a few hundred bytes); every other route keeps **10 MiB** (the
  jobs import and, in pull request #98, the document upload raise it for themselves). A declared
  `Content-Length` over the limit is answered 413 by Caddy itself, with no body read and nothing
  proxied; a body streamed past the limit is cut. Timeouts against slow clients: `read_header`
  **10s**, `read_body_idle` **15s** with a 1024 bytes/second minimum rate (so a trickle that never
  fully stalls is still cut), `idle` **60s**. Writes keep their defaults (`write_idle` 1m, no rate),
  so a long lived event stream, whose heartbeat is well inside a minute, is never cut.
- **api** (`app/body_limit.py`, an ASGI middleware before the body is parsed). Public routes
  64 KiB, other JSON routes **1 MiB**; the routes that read their own raw body with their own limit
  (the profile photo and import, the jobs import, the ingestion upload and paste of #98) pass
  through untouched, so nothing is read twice. Over the limit is 413 (a declared length before a
  byte is read, a streamed body cut at the limit); a body that stalls past a generous idle window
  is 408. This is the second line, for when a request reaches the api without the proxy in front.
- **uvicorn** (`api/Dockerfile`). `--limit-concurrency 256` bounds how many requests pile up at
  once on the 512 MiB api container (503 beyond it) and `--timeout-keep-alive 5` keeps idle
  connections short. Request headers are bounded by Caddy in front (16 KiB, its default).

Residual risk, stated honestly for a small private app: an attacker with many client addresses can
still open many connections, each now holding at most 64 KiB (public) and cut within about 15s, so
the memory and connection cost is bounded but not zero. Caddy on the free, open-source build has no
per-client connection or request-rate cap without a third-party module, so this is left to the
layers above; a rate limit on sign in by email and address already exists in the identity module.

**Bringing the Cloudflare quick tunnel back** (it was the public entry point until 2026-10-10:
free, no account, but a random `*.trycloudflare.com` URL on every start): restore the
`cloudflared` service of compose.yml (`cloudflare/cloudflared` running `tunnel --no-autoupdate
--url http://caddy:8080`, profile `tunnel`), add `Cf-Connecting-Ip` back to the `:8080`
`client_ip_headers` of caddy/Caddyfile only if tailscale is removed at the same time (Funnel
does not strip it), and take `tunnel_url` and its tests from the history of deploy.sh (git log
`-S trycloudflare`).

## Accounts

Nobody signs up alone. The first admin comes from the Jetson's command line:

```bash
./deploy.sh admin create you@example.org    # prints https://jobapp.taild83d16.ts.net/invite/<token>, valid 7 days, single use
```

Open the link, choose a name and a password (10 to 128 characters, not the email, not a common
password) and you land signed in. Run it again for an active admin to get a 1 hour password reset
link instead. The link holds a secret: it is printed once, never logged, and Caddy masks tokens in
its access log. Every page except `/login`, `/invite/<token>` and `/reset/<token>` needs a session;
any api answer 401 sends the browser to `/login?next=<page>`.

The identity journey runs in a real browser against the real stack, locally on a scratch project:

```bash
HTTP_PORT=8093 docker compose -p jobapp-i3 -f compose.yml -f compose.mac.yml up -d --build --wait
docker compose -p jobapp-i3 -f compose.yml exec -T api alembic upgrade head
E2E_CADDY_URL=http://127.0.0.1:8093 E2E_COMPOSE_PROJECT=jobapp-i3 pnpm --dir web exec playwright test identity.spec.ts
docker compose -p jobapp-i3 -f compose.yml down -v       # that scratch project only
```

and on the Jetson through the public URL (the spec runs `./deploy.sh admin create` itself), then
the test account is removed:

```bash
E2E_CADDY_URL=$(./deploy.sh url) E2E_ADMIN_VIA_DEPLOY=1 E2E_ADMIN_EMAIL=e2e-admin@example.org \
  E2E_SHOT_PREFIX=jetson- pnpm --dir web exec playwright test identity.spec.ts
ssh jetson "cd jobapp && docker compose -f compose.yml exec -T db sh -c 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"'" \
  <<< "delete from invites where email = 'e2e-admin@example.org'; delete from users where email = 'e2e-admin@example.org';"
```

Do not run `identity.spec.ts` in parallel with `caddy.spec.ts`: the latter stops the database.

The whole identity surface runs as one journey on the Jetson through the public URL, with
throwaway `e2e-*@example.org` accounts only:

```bash
./deploy.sh && tests/e2e_jetson.sh
```

It runs `web/e2e/identity-all.config.ts` (identity, invites, users, account, profile and throttle
specs in that order, one after the other; each unlocks its own failed attempts with `./deploy.sh
admin unlock`, so the run never reaches the 20 failures per address), snapshots Harald's account
before and after and fails if anything changed, deletes every e2e row (users with their sessions
and profile, invites, attempts), checks that no failed sign in is left in the window for his
email or this Mac's addresses, and writes every screenshot with an index to
`tasks/evidence/identity/`. It refuses to start while e2e rows of an earlier run are left
(`E2E_CLEAN_FIRST=1` removes them first).
Locally the same config runs on a scratch project (one shared admin, so the last admin rule runs
too):

```bash
HTTP_PORT=8098 docker compose -p jobapp-i8 -f compose.yml -f compose.mac.yml up -d --build --wait
docker compose -p jobapp-i8 -f compose.yml exec -T api alembic upgrade head
E2E_CADDY_URL=http://127.0.0.1:8098 E2E_COMPOSE_PROJECT=jobapp-i8 E2E_ADMIN_EMAIL=e2e-admin@example.org \
  E2E_PASSWORD_THROTTLE=1 pnpm --dir web exec playwright test -c e2e/identity-all.config.ts
docker compose -p jobapp-i8 -f compose.yml down -v
```

The specs' own runs below remain for one area at a time.

### Inviting people

Signed in as an admin, open **Admin** (`/admin/users`), then **Invite someone**: an email, the kind
of link (an invite creates an account, valid 7 days; a password reset is for an existing active
account, valid 1 hour) and, for an invite, the role. The dialog shows the full link once, built
from the address you are on, with a Copy button: send it yourself. It is never shown again; a new
link for the same email and kind revokes the previous unused one. The Invites table lists every
link with its status (active, used, revoked, expired) and lets you revoke an active one. An invite
for an email that already has an account is refused: send a reset link instead.

The API behind it, admin only (a member gets 404, nobody signed in 401):
`GET /api/admin/invites?limit=&offset=` (newest first, never a token), `POST /api/admin/invites`
`{email, kind, role}` (201 with `path` once), `DELETE /api/admin/invites/{id}` (204, or 404 for
anything not active).

The invites journey (`web/e2e/invites.spec.ts`) runs like the identity one, on its own scratch
project:

```bash
HTTP_PORT=8094 docker compose -p jobapp-i4 -f compose.yml -f compose.mac.yml up -d --build --wait
docker compose -p jobapp-i4 -f compose.yml exec -T api alembic upgrade head
E2E_CADDY_URL=http://127.0.0.1:8094 E2E_COMPOSE_PROJECT=jobapp-i4 E2E_FRIEND_EMAIL=friend@example.org \
  pnpm --dir web exec playwright test invites.spec.ts
docker compose -p jobapp-i4 -f compose.yml down -v
```

On the Jetson it uses two throwaway accounts, removed afterwards with everything they own
(sessions and profile rows cascade with the user):

```bash
E2E_CADDY_URL=$(./deploy.sh url) E2E_ADMIN_VIA_DEPLOY=1 E2E_ADMIN_EMAIL=e2e-admin@example.org \
  E2E_FRIEND_EMAIL=e2e-friend@example.org E2E_SHOT_PREFIX=jetson- \
  pnpm --dir web exec playwright test invites.spec.ts
ssh jetson "cd jobapp && docker compose -f compose.yml exec -T db sh -c 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"'" <<< "
  delete from invites where email in ('e2e-admin@example.org', 'e2e-friend@example.org');
  delete from login_attempts where email in ('e2e-admin@example.org', 'e2e-friend@example.org');
  delete from users where email in ('e2e-admin@example.org', 'e2e-friend@example.org');"
```

### Managing accounts

The **Accounts** table above the invites lists everyone who can sign in, by name: email, role,
status, last sign in and how many browsers are signed in. Your own row says **You**. Each row's
actions menu offers **Make admin** or **Make member**, and **Disable** or **Enable**, each behind a
confirmation:

- Disabling signs the account out of every browser at once (its sessions are deleted, its next
  request answers 401 and lands on `/login`) and refuses its sign in until it is enabled again.
  Enabling never brings an old session back: the person signs in again. Their data stays.
- A role change applies from the account's next request; a demoted admin loses the Admin entry
  on their next page load.
- The last active admin can be neither disabled nor made a member (the menu greys those out
  and the API answers 409), even when two admins act at the same moment: every change takes one
  database lock and rechecks under it. You can make yourself a member while another admin
  exists (the dialog asks first, then the app reloads who you are and leaves the admin area).
  Your own row has no Disable: another admin does that.

The API, admin only like the invites: `GET /api/admin/users?limit=&offset=` (sorted by name, each
with `sessions`, plus `active_admins`), `PATCH /api/admin/users/{id}` `{is_active?, role?}` (200
with the account, also when nothing changes; 404 for an unknown id, 409 for the last admin, 422
for an empty or invalid body).

The accounts journey (`web/e2e/users.spec.ts`) runs like the invites one, on its own scratch
project (`-p jobapp-i5`, `HTTP_PORT=8095`, `E2E_COMPOSE_PROJECT=jobapp-i5`), and on the Jetson with
the same two throwaway accounts and the same cleanup. It only ever acts on the rows of its two
emails, and checks the last admin rule in the browser only where its admin is the only active one.

### Your account

Everyone has an **Account** page (`/account`, from the user menu at the top right) with two cards:

- **Profile name**: the name shown in the header menu and to the admin. Same rules as on the
  invite page: trimmed, inner spaces collapsed, 1 to 100 characters, no control characters.
- **Change password**: the current password, then the new one twice (10 to 128 characters, not
  your email, not a common password, not the current one). Each field has a show toggle. On
  success every other browser signed in to the account is signed out at its next request; the
  browser you used stays signed in.

Forgot your password? Ask the admin for a reset link (Admin, Invite someone, kind "Password
reset"): it is valid 1 hour, opens `/reset/<token>`, and setting a new password there signs out
every browser of the account.

The API, for the signed in user only (401 without a session, 403 without the CSRF header):
`PATCH /api/auth/me` `{name}` (200 with the user, 422 `name: <rule>`) and `POST /api/auth/password`
`{current_password, new_password}` (204). A wrong current password is a 400
`current_password: The current password is incorrect.`, not a 401: the session is fine and stays
signed in. A broken rule is a 422 `new_password: <the invite page's message>`. Errors name their
field first so the page can put each message under its input. Under two simultaneous changes from
two browsers, the first one wins and ends the other's session, which gets a 401.

The account journey (`web/e2e/account.spec.ts`) runs like the others, on its own scratch project
(`-p jobapp-i6`, `HTTP_PORT=8096`, `E2E_COMPOSE_PROJECT=jobapp-i6`), and on the Jetson with the
throwaway admin `e2e-admin@example.org` only, removed afterwards with the same cleanup as above.

### Sign in throttling

Every sign in attempt is recorded in `login_attempts` (the normalised email, or the trimmed
lowercase text when it is not an address; the client address the api sees, which is
Funnel's `X-Forwarded-For` through the public URL and the TCP peer on loopback; the time; success or not),
unknown and disabled accounts included. Before any password is checked:

- 5 failures for one email in 15 minutes, or 20 from one address, answer **429**
  `{"detail": "Too many sign in attempts. Try again in N minutes."}` with `Retry-After` in whole
  seconds, counted from the oldest failure still in the window. The window slides: a failure
  stops counting exactly 15 minutes after it happened.
- A successful sign in resets the email's count (the person proved they hold the password); it
  does not reset the address's count, so one account someone owns cannot launder guesses on
  others.
- A 429 runs no argon2 and is not recorded, so retrying does not extend the wait. Its speed
  reveals nothing: the same refusal comes for any email once the limit is reached.
- The count and the record happen under one Postgres advisory lock per email and per address,
  before argon2 runs, so parallel guesses never overshoot: 20 at once on one email give exactly
  5 checks and 15 refusals.
- A wrong current password on the Account page counts as a failed sign in of that account (both
  guess the same password): after 5 the card answers 429 too, and so does the sign in of that
  email. Invite and reset links are not throttled: the token is the credential.
- Rows older than 30 days are deleted on write, at most once an hour per api process.

The sign in page and the password card show the server's message and keep their button disabled
with a countdown until `Retry-After` has passed. The journey `web/e2e/throttle.spec.ts` runs on a
scratch project (`-p jobapp-i7`, `HTTP_PORT=8097`, `E2E_COMPOSE_PROJECT=jobapp-i7`,
`E2E_PASSWORD_THROTTLE=1`) and deletes its own attempts afterwards; on the Jetson with
`e2e-admin@example.org` only. Every local browser reaches the api from one address (Caddy's
peer), so all specs of one run share the 20 failure budget per 15 minutes.

To unlock someone on the Jetson, delete their failed attempts still in the window (also those of
one address with `--ip`, whatever the email); it prints how many rows went:

```bash
./deploy.sh admin unlock someone@example.org                   # that email, case insensitive
./deploy.sh admin unlock someone@example.org --ip 203.0.113.9  # and every failure from that address
```

Successful attempts and failures older than 15 minutes are kept. The email and the address reach
psql as bound variables, and Postgres refuses anything that is not an address.

## Profile export and import

`GET /api/profile/export` downloads the whole profile as `profile-YYYY-MM-DD.json`;
`POST /api/profile/import` takes that file back (2 MB at most) and replaces all six sections in
one transaction, one revision each. Harald's engine files convert into the same format on the
Mac, then load into one account inside the api container:

```bash
uv run --native-tls --directory api python -m app.profile.cli convert --source-dir "$PWD/.." --out /tmp/profile.json
docker compose exec -T api python -m app.profile.cli import --email you@example.org --file - < /tmp/profile.json
```

The import prints the new revision and the completeness, never the profile itself. Exit codes:
0 imported, 2 invalid arguments or file, 1 no such account or a database error.
`./deploy.sh profile import <email>` does both, the import on the Jetson.

## Board check

The Search tab checks a company board before adding it: `POST /api/profile/boards/check` with
`{"ats": "ashby" | "greenhouse" | "lever", "org": "<slug>"}` answers
`{exists, postings, company_name, error}`, `error` being null, `not_found`, `timeout`, `blocked`
(the ATS refused this server) or `upstream_error`. The api calls the ATS public posting API itself
(fixed hosts, org checked against the slug pattern first, no redirect off the host, 5 s for the
whole exchange, one retry on a connection error), caches found and not found answers for 10
minutes in the process, and allows 10 checks per user per minute (429 with Retry-After).

## Jobs import, list and files

Harald's `../data` (`jobs.csv` and `jobs/<id>/`: posting.txt, cv.pdf, letter.pdf, cv.json,
letter.json, answers.json, review.json, apply.py; the HTML renders are skipped) converts on the Mac
into one `jobapp-jobs` document (about 18 MB, files in base64) and loads into one account inside
the api container, from stdin:

```bash
./deploy.sh jobs import you@example.org      # both steps, the import on the Jetson
uv run --native-tls --directory api python -m app.jobs.cli convert --source-dir "$PWD/../data" --out /tmp/jobs.json
docker compose exec -T api python -m app.jobs.cli import --email you@example.org --file - < /tmp/jobs.json
```

The CLI takes documents up to 64 MiB. `POST /api/jobs/import` takes the same document over HTTP
but only up to 10 MiB, Caddy's body limit: it is for small imports, Harald's data always goes
through the CLI. The import is idempotent: rows are matched by slug, then by the duplicate rules
(same URL, or same company, title and location); only changed fields are written, with one
`import` event naming them; files are deduped by SHA-256; nothing is ever deleted; and a row that
was sent (status applied, or an applied_on date) keeps its files byte for byte, a different file
under the same name being reported as a warning. It prints created, updated, unchanged, skipped,
files added, replaced, unchanged and kept, and the warnings (slugs and file names only).

`GET /api/jobs` lists the caller's jobs with filters (`status` and `type` repeated, `company`,
`q` full text, `fit_min`, `fit_max`, `found_from`, `found_to`), `sort` (found_on, fit, company,
status, updated), `limit` and an opaque `cursor`; the answer carries `total`, `counts_by_status`
(every filter but status) and `fit_histogram` (10 buckets and unscored). `q` uses Postgres'
'simple' configuration: case is ignored but accents are not ("developpeur" does not find
"développeur"), and words match whole. `GET /api/jobs/{id}` gives the posting text, legacy columns,
files and the newest 50 events; `GET /api/jobs/{id}/files/{file_id}` downloads a file (PDFs inline,
the rest as attachments, ETag = SHA-256). Another user's ids answer 404.

## Sweeps and the worker

"Find jobs" on `/jobs` sweeps the enabled boards of the profile's search section (Ashby,
Greenhouse and Lever public APIs, four boards at a time), keeps the postings the search terms
match (title include and exclude, the board's own exclude, location, days window, contract
types), skips the ones already in the list, stores the new ones as `found` and scores them; a
posting matching a never apply rule ("Label: term, term") is stored as `skipped` with the rule as
its note. The work happens in the `worker` service (`python -m app.worker`, arq on Redis; its
own image `jobapp-worker`, see Agent runs), one sweep per user at a time. The page follows it live (server-sent
events, polling as the fallback) and shows a summary: the new postings and the boards that could
not be read (not found, blocked, rate limited, timeout...). Automatic search is off by default:
"Schedule" next to Find jobs (or the Search tab of the profile) turns it on for weekdays, one to
four local times and a time zone (`search.sweep_schedule`), and shows when it runs next. The
worker ticks every 5 minutes and starts each due slot once; a time skipped by daylight saving
time runs when the clock jumps, a repeated one runs once.

```bash
curl -s -X POST -H 'X-Requested-With: jobapp' -b cookies http://localhost/api/jobs/sweeps      # 202, 409 if one runs
curl -s -b cookies http://localhost/api/jobs/sweeps                                            # newest first, schedule
curl -s -N -b cookies http://localhost/api/jobs/sweeps/<id>/events                             # live progress
docker compose logs -f worker                                                                  # what the worker does
docker compose exec -T worker python -m app.worker --check                                     # its healthcheck
```

A sweep whose worker died is failed after two minutes without a heartbeat, so the next one can
start. `ATS_BASE_URL` (test stacks only, refused when `APP_ENV=production`) sends the board check
and the sweeps to a stub of the three APIs; with `ATS_BASE_URL_ORG_PREFIX=fake-` only the orgs
starting with it go there (`tests/compose.j5-e2e.yml`), the CI stack sends every org
(`tests/ats-stub.compose.yml`).

## Agent runs

Claude agents run in the `worker` (SPEC-agents.md, agents A2), on the user's own model credential
(Account, Model access). The worker image `jobapp-worker` is the `worker` target of
`api/Dockerfile`: the api's code plus the Claude Agent SDK, whose wheel bundles the Claude Code CLI
it drives (no Node runtime); the api image (`runtime` target) stays without it. At most two runs at
once (a third waits in the queue); each run gets a scratch directory on the worker's `/tmp`
tmpfs, deleted when it ends, its credential only in the environment of its own Claude Code
process, no built-in tool (no shell, files or web), and only the app tools of its agent, bound to
its user. A run stops at its turn limit, after its wall time (10 minutes by default) or when the
user cancels it; a worker restart fails the runs it was running.

```bash
curl -s -b cookies http://localhost/api/agents                                                    # agents available here
curl -s -X POST -H 'X-Requested-With: jobapp' -H 'Content-Type: application/json' -b cookies \
  -d '{"agent":"echo","input":{"message":"hi"}}' http://localhost/api/agents/runs                 # 202, queued
curl -s -b cookies http://localhost/api/agents/runs/<id>                                          # status, events, tokens, cost
curl -s -N -b cookies -H 'Last-Event-ID: 12' http://localhost/api/agents/runs/<id>/stream         # live steps after step 12
curl -s -X POST -H 'X-Requested-With: jobapp' -b cookies http://localhost/api/agents/runs/<id>/cancel
```

The `/runs` page lists the runs; `/runs/<id>` is the live run panel (steps, tool calls,
tokens, cost, Cancel), which resumes its stream after a reload or a network loss without losing
or repeating a step (`app/sse.py` `replay`, keyed by the step's seq). The answer appears while
it is written: the worker publishes the text of the block being written to Redis
(`app/agents/live.py`, nothing per token in Postgres) and the stream relays it as `partial`
events until the stored `text` step replaces it; `AGENTS_LIVE_TEXT=false` in `.env` (api and
worker) turns that off, and the answer then arrives when it is complete; an agent definition can
turn it off for its own runs (`live_text=False`). `echo` is a test agent,
refused when `APP_ENV=production`; outside production `/runs` offers a test run with it. `AGENTS_FAKE_BACKEND=true` (test
stacks and CI only, refused in production) replaces the model with recorded message streams
(`api/app/agents/fake_streams/`); `tests/compose.a2-e2e.yml` with `AGENTS_FAKE_BACKEND=false` runs
the real SDK and CLI against the Anthropic stub (`tests/anthropic_stub.py`), still with no model
call.

## Documents for the profile

"Fill my profile with an agent" on the Profile page opens `/profile/fill` (SPEC-ingestion.md,
ingestion G1): three steps, of which the second, Documents, is built; the two others are listed as
"Soon" until their tasks land. A user adds files (dropped or chosen, sent one at a time with their
progress) or pastes a text, and sees each document with its type, size and state, the text kept
from it, and a Delete that asks first. Documents live in Postgres (`ingestion_documents`), no
other user of the app can see them (whoever runs the server can read its database and its
backups), and deleting one is a hard delete: gone from the app at once, and from the backups when
newer ones have replaced those that hold it.

- **Type:** decided from the first bytes of the file, never from its name or a declared type.
  Text (UTF-8, or UTF-16 with a BOM) is read at once and its bytes are not kept. A PDF, a ZIP
  (a DOCX or a LinkedIn export), a PNG or a JPEG is kept as it is with the state "Waiting": the
  parsers arrive with later tasks (`READABLE_KINDS` in `api/app/ingestion/documents.py`). Legacy
  Office, RTF, OpenDocument, HEIC, GIF, WebP and programs are refused with 415 and one sentence.
- **Limits:** 20 MiB per file, 100,000 characters per paste, 500,000 characters of text kept per
  document, 100 MB and 200 documents per user. The same file twice is one document (per user).
- **The upload route:** `POST /api/ingestion/documents` takes the file as the raw body, its name
  in `X-Filename` (RFC 5987), and is the one route on which Caddy accepts 20 MiB; every other
  route keeps 10 MiB (`caddy/Caddyfile`). The api reads the body into one buffer, counted and
  hashed as it arrives, and sends it to Postgres in pieces.
- **Who may send:** one body per user at a time (a file or a paste; a second one waits 35 s, then
  429; beyond two that wait, 429 at once), and two files at a time for the whole api process
  (then 503), so one account holds one of the two places at most. The wait is longer than the
  30 s below, so the file sent after an upload that a change of network cut off is taken, not
  refused. A body has to keep arriving: 256 KiB every 30 s and 10 minutes in
  all for a file, 60 s in all for a paste, else 408. The quota is checked before the bytes go to
  Postgres, and again under the user's lock.
- **Secrets:** a text that matches the shape of a password or an access key
  (`api/app/ingestion/secrets.py`) is flagged; the user checks it, keeps it or deletes it.
- **Logs:** ids, sizes, types and rule names only. Caddy drops `X-Filename` from its access log.

```bash
curl -s -X POST -H 'X-Requested-With: jobapp' -H "X-Filename: UTF-8''cv.pdf" -b cookies \
  --data-binary @cv.pdf http://localhost/api/ingestion/documents                                  # 201, or 200 when already there
curl -s -X POST -H 'X-Requested-With: jobapp' -H 'Content-Type: application/json' -b cookies \
  -d '{"text":"About me"}' http://localhost/api/ingestion/documents/text                          # a pasted text
curl -s -b cookies http://localhost/api/ingestion/documents                                       # the list, what it takes, the limits
curl -s -b cookies 'http://localhost/api/ingestion/documents/<id>/text?offset=0&limit=20000'      # the text kept
curl -s -X DELETE -H 'X-Requested-With: jobapp' -b cookies http://localhost/api/ingestion/documents/<id>
```

## Backups and restores

`./deploy.sh backup` runs `pg_dump -Fc` inside the `db` container and streams it to
`jetson:~/jobapp-backups/jobapp-YYYY-MM-DDTHHMMSS.dump` (UTC time). The dump is written to a hidden
`.partial` file, checked with `pg_restore --list` and a full read into `/dev/null` (which is what
catches a truncated or damaged file), then renamed; the directory is mode 700 and every dump mode
600. The newest `BACKUP_KEEP` dumps (default 14) are kept and older ones deleted. A backup is
refused when the free disk would drop under the database size plus `BACKUP_MIN_FREE_MB` (100);
a disk that fills up during `pg_dump` makes it fail, and the partial file is removed. Every backup
and restore holds `flock` on `~/jobapp-backups/.lock`, so a second one at the same time is refused
with a clear message. The backup directory lives outside `~/jobapp`, so `rsync --delete` never
touches it; `backups/` and `*.dump` are git-ignored and never rsynced.

`./deploy.sh backup --pull` also copies the dump to `backups/` on the Mac (dir 700, file 600,
sha256 compared on both sides). That copy is the only one off the Jetson's SD card.

`./deploy.sh restore <file>` takes a name in `~/jobapp-backups`, a path on the Jetson, `latest`,
or a file on the Mac (uploaded to `~/jobapp-backups/incoming/` first). By default it restores into
the scratch database `jobapp_restore_check`, prints the row count of every table and drops it
(`--keep` leaves it, `./deploy.sh restore --drop-scratch` drops it later). A missing, empty,
foreign, truncated or damaged file is refused before any database is touched.

`./deploy.sh restore <file> --into-live` replaces the live database. It verifies the file, asks you
to type `RESTORE jobapp` (`--yes` skips the question, for scripts; without a terminal and without
`--yes` it refuses), takes a pre-restore backup (`jobapp-...-pre-restore.dump`), stops `api`,
drops and recreates the database, restores (rolling back to the pre-restore dump if `pg_restore`
fails), runs `alembic upgrade head`, starts `api` and checks health. About 15 seconds of 502 on
`/api` for a small database.

Nightly backups: `./deploy.sh backup-schedule install` adds one line to the crontab of the Jetson
user (no sudo; idempotent; other lines kept), `remove` takes it out, `status` shows it and the end
of `~/jobapp-backups/backup.log`. It runs the deployed `~/jobapp/ops/db.sh`, so deploy first. The
line, at `BACKUP_CRON` (default `17 3 * * *`, the Jetson's local time):

```
17 3 * * * JOBAPP_DIR=jobapp BACKUP_DIR=jobapp-backups BACKUP_KEEP=14 "$HOME/jobapp/ops/db.sh" backup >>"$HOME/jobapp-backups/backup.log" 2>&1 # jobapp-backup (./deploy.sh backup-schedule)
```

## Reboot and crash recovery

Every service has `restart: unless-stopped` and `docker.service` is enabled at boot, so the daemon
starts the stack again after a reboot; `tailscale` logs in again from its saved state, with the same URL.
`tests/crash_check.sh` proves the restart half without sudo: it kills each container's main process
with SIGKILL from the host PID namespace (what the OOM killer or a segfault looks like) and waits
for the daemon to restart it healthy. `docker kill` and `docker compose kill` count as a manual
stop, which `unless-stopped` deliberately leaves down. The reboot itself needs sudo, so it is run by
hand:

```bash
tests/reboot_check.sh 2>&1 | tee tasks/evidence/task9/reboot.log   # asks for the sudo password once
```

It reboots the Jetson, waits for ssh with a new boot id, waits for the five services to be healthy,
runs `./deploy.sh smoke` and prints the new URL and the seconds since boot; it passes when the
stack is healthy within 120 seconds of boot.
