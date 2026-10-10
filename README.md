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
./deploy.sh                    # preflight, rsync to jetson:~/jobapp, build there, compose up --wait, migrate, health
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
(`.github/workflows/ci.yml`) runs on each pull request and push to `main`, in parallel jobs: api in
two shards (ruff, pytest against Postgres and Redis), api coverage (both shards combined, at least
95 percent, checked in that job), web checks (eslint, tsc, build), web Vitest in two shards, shell
(shellcheck, `tests/deploy_test.sh`, the guard that every e2e spec is in a group), e2e in three
groups (identity, sweep, apps; each on its own Compose stack built on its runner, the
Playwright journeys with throwaway accounts and a synthetic jobs import, never Harald's data) and
pre-commit. Since 2026-10-10 the jobs are written for self-hosted runners on Harald's Mac (CI
runners on the Mac, below), because the free minutes of GitHub's own machines were used up; the
Jetson's runner keeps the deploys and nothing uses paid minutes. When CI is green for a push to `main`, `.github/workflows/deploy.yml` runs on the
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
it was stopped; newest 10 kept, separate from the nightly ones), `compose up --wait`, migrations,
`tests/jetson_smoke.sh` in quick mode, `~/jobapp/.deployed-commit` written once the smoke passed,
and the public URL in the run's summary. If `up` or a migration fails, the previous files
(`compose.yml`, the Caddyfile and the rest of the tree, `.env` never touched) and the `:previous`
images are put back and started again; a migration runs in one transaction and rolls back, and for data the
log names the predeploy dump to restore with `ops/db.sh restore-live`. Any other failure between
the sync and the migrations (the backup included) puts the previous files and `:latest` tags back
without restarting anything. A smoke that fails after the migrations does not roll back, on
purpose: the run is red, `.deployed-commit` keeps the previous commit, and going back is a human
decision (the previous code may not run on the migrated schema). When the api is not running at
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

### CI runners on the Mac

CI runs on two self-hosted runners on the Mac (issue #83), `mac-ci-1` and `mac-ci-2`, labels
`self-hosted, Linux, ARM64, mac-ci`. Every job of `ci.yml` targets `[self-hosted, mac-ci]`, never
`self-hosted` alone, so a CI job cannot land on the Jetson's runner, and `deploy.yml` keeps
`[self-hosted, jetson]`.

```bash
ops/ci-runners.sh install [count]   # build the image, register and start the runners (default 2, at most 3); safe to run again
ops/ci-runners.sh status            # containers, GitHub's view (online, busy), memory, disk used
ops/ci-runners.sh stop              # stop them; queued jobs wait
ops/ci-runners.sh start             # start them again
ops/ci-runners.sh update            # rebuild the image from ops/ci-runner, recreate the containers (registration and caches kept)
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
`jobapp-ci-runner:<version>`, the containers `jobapp-ci-1` and `jobapp-ci-2` (restart
unless-stopped), the volume `jobapp-ci` (each runner's directory, registration and work folder,
plus the caches the runners share: uv, the pnpm store, Playwright's Chromium, pre-commit) and one
volume `jobapp-ci-docker-<n>` per runner (its Docker daemon's images and layer cache). All carry
the label `jobapp.ci=runner`. To add a third runner: `ops/ci-runners.sh install 3` (the script
refuses more).

**Budget.** The Mac is shared: Harald works on it, and agents run scratch stacks and test suites
in the same Docker Desktop (12 CPUs, about 18 GB). On 2026-10-10 three runner trials on top of
four agents took the load average to 82 while the disk filled, and the Mac went down, so CI has a fixed share and
cannot take more:

| | per runner | two runners | left to everything else |
|---|---|---|---|
| CPUs | 3, pinned (runner 1: CPUs 0-2, runner 2: 3-5) | 6 of 12 | CPUs 6-11 never used by CI |
| Memory | 4 GB, swap included | 8 GB of about 18 | about 10 GB |
| Disk | see the disk budget below | about 10 GB, 13 GB at most | |

A runner takes one job at a time, so two runners are the bound on what runs at once, whatever
the number of pushes: the other jobs queue, and a newer push to the same branch cancels the older
run. Inside a job the worker counts are written out in `ci.yml` (`PYTEST_WORKERS` and
`VITEST_WORKERS`, 3 each, never "all cores"; Playwright runs one worker), and the e2e job has
`max-parallel: 2`, so at most two stacks of a run are up together even with a third runner. A
full run therefore takes longer than on GitHub's machines, where eleven jobs ran at once; that is
the price of leaving the Mac usable.

**Disk budget.** Disk is the scarcest resource of this Mac, and a full disk does not fail one
job: it corrupts Docker Desktop's store for every container (2026-10-10: a trial of three e2e
groups at once on a disk with 22 GB free, next to 21.8 GB of dangling volumes left by test runs,
filled it). Everything of CI sits in Docker Desktop's disk image, which grows on the Mac's own
disk:

| | size | bound |
|---|---|---|
| Runner image, stored once | 1.9 GB | fixed |
| Docker data of a runner (images, layer cache) | 2.4 GB measured after the three e2e groups | 4 GB (`CI_DOCKER_MAX_MB`), then emptied; build cache alone 1 GB (`CI_BUILD_CACHE_KEEP`) |
| Runner directory (runner, its Node versions, setup tools) | under 1 GB per runner | fixed |
| Shared tool caches (uv, pnpm store, Chromium, pre-commit) | 1.4 GB measured | grow only when a lock file changes; `prune` empties them |
| Work folder of a job | emptied after every job | 0 between jobs |

About 10 GB in steady state for two runners, 13 GB at the bounds. Three things enforce it.
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
service as before, on the Mac they stay on the runner.

**Updating.** The runner updates itself when GitHub publishes a release (the update lands in the
`jobapp-ci` volume and survives restarts). To move the pinned version, the Docker Engine or the
packages of the image: edit `ops/ci-runner/Dockerfile` (version and the SHA-256 from the release
notes of `actions/runner`), then `ops/ci-runners.sh update`.

**Security.** The runners are registered to this private repository only, and a job-started hook
baked into the image (`ops/ci-runner/job-started.sh`) refuses every job that is not this
repository's `ci.yml`. A container gets no variable of the shell that installs it (no token, no
key, no `.env`), no folder of the Mac and not the Mac's Docker socket; the registration token
goes from `gh api` into the container's stdin and is stored nowhere. Jobs use throwaway secrets
(`.github/scripts/ci_env.sh`) and synthetic data, never the Jetson's database or Harald's files.
This keeps accidents and collisions out; it is not a wall against hostile code: the containers
are privileged (they run a Docker daemon), and a privileged container can reach the Docker
Desktop virtual machine and what Docker Desktop shares with it. Whoever can push a branch to this
repository can run code on the Mac, so the repository stays private and fork pull requests stay
off. The caches are shared by all runs, so a branch can leave a poisoned cache entry for the
next run: `ops/ci-runners.sh prune` empties them.

**Apple silicon.** The runners sit in the same Linux guest as every other container of this Mac,
so they need `OPENSSL_armcap=0` too (Development on the Mac, above). The runner image sets it
for a job's own processes and sets `CI_COMPOSE_OVERLAY=compose.mac.yml`, which the e2e jobs add
to their `compose up`. `ci.yml` never names the variable and `compose.yml` never has it.

To try a CI step by hand in the runner's exact environment, without registering anything:

```bash
docker run --rm -it --privileged -e CI_RUNNER_INDEX=99 -v jobapp-ci-try:/ci \
  -v jobapp-ci-try-docker:/var/lib/docker jobapp-ci-runner:2.338.0 exec bash
```

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
worker) turns that off, and the answer then arrives when it is complete. `echo` is a test agent,
refused when `APP_ENV=production`; outside production `/runs` offers a test run with it. `AGENTS_FAKE_BACKEND=true` (test
stacks and CI only, refused in production) replaces the model with recorded message streams
(`api/app/agents/fake_streams/`); `tests/compose.a2-e2e.yml` with `AGENTS_FAKE_BACKEND=false` runs
the real SDK and CLI against the Anthropic stub (`tests/anthropic_stub.py`), still with no model
call.

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
