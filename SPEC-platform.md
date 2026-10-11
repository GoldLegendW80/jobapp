# Spec: platform

Module of [CAPABILITY-MAP.md](CAPABILITY-MAP.md). Depends on nothing; every other module runs on it.

## Objective

A repeatable way to build, run and deploy the app on the Jetson. After this module, a single command on the Mac ships the code to the Jetson and brings up a stack that answers `https://jobapp.taild83d16.ts.net/api/health` from the internet through Tailscale Funnel (a Cloudflare quick tunnel until 2026-10-10, see Public access), with an empty React page at `/`. No product features yet: this is the skeleton every later module plugs into.

Who uses it: Harald, as operator. Friends never see this layer.

## Target host

| | |
|---|---|
| Host | `ssh jetson` (yessai@192.168.1.47, Wi-Fi) |
| Board | Jetson Orin Nano Developer Kit Super, 6 cores, 7.4G RAM, 49G free disk |
| OS | Ubuntu 22.04, L4T R36.4.7 (JetPack 6), aarch64 |
| Docker | 29.8.0, Compose v5.5.1, `nvidia` runtime registered (not used by this app) |

RAM budget for the whole stack, all modules included: 5G, leaving the rest to the OS. Each service declares `mem_limit`.

## Tech stack

| Service | Image or build | Role |
|---|---|---|
| `caddy` | `caddy:2-alpine` | Reverse proxy: `/` to `web`, `/api` to `api`, `/vnc` to `browser` (later) |
| `web` | build `web/` (Node 22 build, served static by Caddy) | React 18, Vite, TypeScript, Tailwind CSS 4, shadcn/ui |
| `api` | build `api/` (`python:3.12-slim`) | FastAPI, SQLAlchemy 2, Alembic, Pydantic 2, uvicorn |
| `db` | `postgres:16-alpine` | Database, volume `pgdata` |
| `redis` | `redis:7-alpine` | Queue between `api` and `worker` (used from the `agents` module on) |
| `tailscale` | `tailscale/tailscale` (pinned by version and arm64 digest) | Tailscale Funnel to Caddy (`tailscale/serve.json`: HTTPS 443 proxied to `http://caddy:8080`, Caddy's unpublished tunnel listener that trusts only Funnel's `X-Forwarded-For`): free, stable URL `https://jobapp.taild83d16.ts.net`, userspace networking, state in the `tsstate` volume (see Public access) |

`worker` and `browser` are added by their own modules; the Compose file reserves their names.

Python dependencies are managed with `uv`; JavaScript with `pnpm`.

## Commands

```bash
# local development on the Mac
docker compose -f compose.yml -f compose.dev.yml up --build    # hot reload for api and web
docker compose exec api alembic upgrade head                   # apply migrations
docker compose exec api pytest                                 # backend tests
pnpm --dir web test                                            # frontend tests
pnpm --dir web lint && uv run --directory api ruff check .     # lint

# deploy to the Jetson
./deploy.sh                    # rsync app/ to jetson:~/jobapp, then on the Jetson: build, ops/migrate.sh
                               # (alembic upgrade head in a one off container of the new image), compose up --wait
./deploy.sh logs api           # tail logs of one service
./deploy.sh url                # print the stable Funnel URL, from tailscale status in the tailscale container
./deploy.sh backup             # pg_dump -Fc to jetson:~/jobapp-backups/jobapp-YYYY-MM-DDTHHMMSS.dump
./deploy.sh restore <file>     # into a scratch database; --into-live replaces the live one
```

Images are built on the Jetson itself (native arm64), so no cross-build is needed.

## Project structure

```
app/
  CAPABILITY-MAP.md  SPEC-*.md
  compose.yml           production stack
  compose.dev.yml       dev overrides (bind mounts, reload, no tailscale)
  .env.example          every variable, no secrets; .env is git-ignored
  deploy.sh             rsync + remote compose + migrations + backup
  Caddyfile
  tailscale/serve.json  Funnel config of the tailscale service: HTTPS 443 to http://caddy:8080
  api/
    pyproject.toml  uv.lock  Dockerfile
    alembic/            migrations
    app/
      main.py           FastAPI app, routers mounted per module
      config.py         settings from env (pydantic-settings)
      db.py             engine, session dependency
      health.py         GET /api/health
      <module>/         one package per module: models.py, schemas.py, router.py, service.py
    tests/
  web/
    package.json  Dockerfile  vite.config.ts  components.json
    src/
      main.tsx  App.tsx
      components/ui/    shadcn components
      features/<module>/  one folder per module
      lib/api.ts        typed fetch client
```

## Code style

Backend: type hints everywhere, routers thin, logic in `service.py`, one module per capability.

```python
# api/app/health.py
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter()


@router.get("/api/health")
def health(session: Session = Depends(get_session)) -> dict[str, str]:
    session.execute(text("select 1"))
    return {"status": "ok"}
```

Frontend: function components, shadcn primitives, data through TanStack Query, no global state library until one is needed. Ruff and Prettier with default settings. Names in English.

## Testing strategy

- Backend: pytest with a throwaway Postgres (the dev `db` service, one database per test session). `/api/health` has a test from day one.
- Frontend: Vitest and Testing Library for components; Playwright end-to-end tests arrive with the `identity` module.
- Platform itself is verified by a smoke script, `deploy.sh smoke`: health through Caddy on the Jetson, then through the public Funnel URL.

## Boundaries

- **Always:** pin image tags and dependency versions; arm64 images only; `mem_limit` on every service; secrets only in `.env`; run tests before deploying.
- **Ask first:** adding a service to Compose, exposing a port on the Jetson's network interface, changing the public access setup (Tailscale Funnel), anything run with `sudo` on the Jetson.
- **Never:** commit `.env` or any token; open a port on the home router; touch the Jetson's existing Ollama or system services; run `docker system prune` or delete volumes without asking.

## Success criteria

1. `./deploy.sh` from a clean clone brings the stack up on the Jetson with no manual step beyond writing `.env`.
2. `curl $(./deploy.sh url)/api/health` from outside the home network returns `{"status":"ok"}`, with a database round trip.
3. The public URL serves the React shell with Tailwind and one shadcn component rendered.
4. A reboot of the Jetson brings everything back (`restart: unless-stopped`) within 2 minutes.
5. The idle stack uses under 1G of RAM (`docker stats`).
6. `./deploy.sh backup` produces a restorable dump; a restore into a fresh database is tested once.

## Repository and automation

- Private GitHub repo `GoldLegendW80/jobapp` (created with `gh`), monorepo: `api/` and `web/` side by side.
- pre-commit hooks: ruff (lint and format), prettier and eslint on `web/`, gitleaks (no secret ever committed), end-of-file and trailing-whitespace fixers, check-yaml. At a commit gitleaks reads the staged changes. In CI nothing is staged, so that hook reads nothing there (found on 2026-10-11: a committed example secret passed); CI runs a second hook of the same pinned gitleaks, `gitleaks-commits` (stage manual, `.github/scripts/secret_scan.sh`), which reads the commits of the run with the same rules and allowlist: each commit on its own, each merge commit for the lines typed into it (`git log --remerge-diff`), and the commit messages; a binary file is not read, at a commit either.
- Flow ([CONTRIBUTING.md](CONTRIBUTING.md)): one branch per task named `<module>/<task-id>-<slug>`, conventional commits, one pull request per task, reviewed by a separate Claude review subagent (`gh pr review`), merged by the orchestrator with a merge commit when it is mergeable: the latest pull request run of CI for its current head commit, started against the base it merges into, concluded success, and the latest review verdict on that head is APPROVE (not "all checks green", which is true before any check registers and after a change of base, which starts no run). Branch protection and rulesets are not available for private repositories on the free plan: the rules hold by convention and by the orchestrator.
- `.github/workflows/ci.yml` on every pull request and push to `main`, on the Mac's self-hosted runners `[self-hosted, mac-ci]` (Linux arm64, tests only; `ubuntu-latest` again by setting the repository variable `CI_RUNS_ON`, see CI runners on the Mac below), in three jobs of about the same length, one per runner (issue #91; before: eleven jobs, 18 to 20 minutes of runner time and 13 to 14 minutes from push to result on two runners, each job paying about 25 s before its first test and started by GitHub in no particular order; the first run of the three jobs: 13.9 minutes of runner time): `api` (ruff lint and format; the whole pytest suite in one run with pytest-xdist on 3 workers, `--dist worksteal`, against Postgres 16 without fsync and Redis service containers through `TEST_DATABASE_URL`; the coverage floor, at least 95 percent of that run's data, checked by a command that is not piped; the docker-marked tests on the runner's Docker daemon; the scoring timing test alone), `checks` (pre-commit with prettier and the file fixers; the secret scan on every run, whatever the plan: gitleaks on the commits of the pull request and on the pull request as a whole, on the commits of a push to `main`, on the whole history for a manual run, in the one job whose checkout fetches the whole history, under `env -u SKIP` (pre-commit would skip it by its hook id), after a pre-commit step that prints no diff; a pull request is judged by the rules of its own tree, so a change to them is reviewed as a change to the gate; the shell unit: shellcheck, `tests/deploy_test.sh` with fakes and the other `tests/*.sh` of CI, `.github/scripts/e2e_groups.py` and its test, `.github/scripts/ci_plan_test.py`, `tests/secret_scan_test.sh`, which plants an example secret in throwaway repositories and expects the scan to fail; then the e2e group apps) and `journeys` (pnpm from `packageManager`, eslint, tsc, build, Vitest in one run; then the e2e groups identity and sweep). Each e2e group builds the images with buildx bake (from the layer cache of its runner's own Docker daemon on the Mac, 22 s cold and under 2 s warm; from the GitHub Actions layer cache, which only `journeys` writes, on GitHub's machines), starts its own Compose stack with a generated `.env` and the CI overlay `tests/ats-stub.compose.yml` (the ATS stubs, the worker with `TEST_FAST_TIMERS`, health probes every second while a service starts), runs the migrations, then its Playwright journeys: the identity chain, the sweeps against the fake ATS, or the jobs, map and agent journeys on a synthetic import from `.github/scripts/synthetic_jobs.py`; the stack is removed with its volumes and no stop timeout; report, traces and screenshots are uploaded on failure. The run is green when the three jobs are: none has a condition or waits for another, so none can be skipped, and there is no job that sums them up (a gate job created when the three end queued 8 minutes behind other runs' jobs in the first run). The plan: the first step of every job runs `.github/scripts/ci_plan.py`, which on a pull request against `main` reads the files of the merge commit against `main` and decides the units to run (`api`, `web`, `shell`, `e2e`) by a short ordered list of path rules: documentation alone (`tasks/`, `docs/`, `CONTRIBUTING.md`, `CAPABILITY-MAP.md`, the `SPEC-*.md` files, the pull request template) runs pre-commit alone, `README.md` also the shell unit (a shell test checks statements in it), `web/` alone skips the api unit, any path no rule names runs everything, and so do a push to `main`, a manual run, a run whose changed files cannot be read, and a pull request against any other branch than `main` (a stacked one: changing its base to `main` later starts no run, so its checks must already stand for everything). Every step carries an `id` and is listed in `ci_plan.py` exactly once: as a check of a unit (`LANES`), as a step of every run (`ALWAYS`) or as a support step (`SUPPORT`: checkout, the plan, tools, installs, stack start, logs, teardown, summaries, uploads); a check carries a condition on its unit's output or on the success of an earlier step of its unit. The last step of every job (`ci_plan.py verify`) fails it when a step the plan needed did not succeed, when one it left out ran, or when the job's status is not one it knows (only `failure` and `cancelled` skip the judgement). `ci_plan_test.py` tests the rules, the reasons that let a rule skip a unit (no api file reads a file of `web/`; no test, script, Dockerfile, Compose file or workflow names a documentation path whose rule does not run its unit) and that `ci.yml` and the plan agree in both directions (no job can be skipped, every check under its unit, every step of the workflow in one of the three lists, no test command among the support steps, no `continue-on-error`, no filter on the triggers). The jobs are named by their ids, `api`, `checks`, `journeys`, the three names a ruleset would require. In `journeys` the teardown between the two stacks must succeed before the second stack starts, which also refuses to start on leftover containers or volumes; only the last teardown of a job is allowed to fail. Pytest, Vitest and each Playwright group have a time bound of their own under the job's 30 minutes. Within a job a failed check does not stop the checks of other units. Every run step uses bash with `-eo pipefail`. Superseded runs are cancelled, actions are pinned by commit SHA, `permissions: contents: read`. `ops/ci-rerun.sh <number>` gives an open pull request a fresh run against the current `main` (it waits for GitHub's merge commit to be current, then closes and reopens).
- `.github/workflows/deploy.yml`: when CI finishes green for a push to `main` (`workflow_run`), or by hand (`workflow_dispatch` with an input `ref`, default `main`; the workflow definition always comes from `main`), on the self-hosted runner of the Jetson, under a `flock` on `~/jobapp-backups/.deploy.lock` shared with `./deploy.sh`: rsync of the commit into `~/jobapp` with `.rsyncignore` (its `.env` and `.deployed-commit` kept), images built before anything restarts (running ones tagged `:previous`), a backup into `~/jobapp-backups/deploys/` whenever the database volume exists (newest 10 kept, apart from the nightly ones), the migrations before the new code starts (`ops/migrate.sh`, see Migrations on deploy), `compose up --wait` (failing puts the `:previous` images back and starts them on the migrated schema), `tests/jetson_smoke.sh` in quick mode, `.deployed-commit` written, the public URL in the job summary. One concurrency group per ref, a started deploy never cancelled.
- Self-hosted runner (`ops/runner/`, `./deploy.sh runner install|status|remove`): the official arm64 actions runner in a container on the Jetson (no sudo), persistent, labels `self-hosted, jetson, arm64`, host network, the host Docker socket and CLI, `~/jobapp` and `~/jobapp-backups` mounted at their own paths, restart unless-stopped. Outbound connections only. With the Docker socket it is root-equivalent on the Jetson, so a job-started hook baked into the image (`ops/runner/job-guard.sh`) refuses every job except this repository's `deploy.yml` from `refs/heads/main` on `workflow_run` or `workflow_dispatch`; pull requests, branches and other workflows never run there.
- CI runners on the Mac (`ops/ci-runner/`, `ops/ci-runners.sh install|status|stop|start|update|prune|try|uninstall`; decision of 2026-10-10, issue #83: the free minutes of GitHub's machines for private repositories were used up, CI moves to the owner's Mac, the Jetson keeps the deploys, nothing is paid): three instances `mac-ci-1` to `mac-ci-3` (the most the script installs; one per job of a run, fewer only queue the jobs) of the official actions runner for Linux arm64 (version and SHA-256 pinned), each in a privileged container of Docker Desktop with a Docker daemon of its own (the Jetson's Engine and Compose versions), pinned to 3 CPUs of its own and limited to 4 GB with no further swap, restart unless-stopped, labels `self-hosted, Linux, ARM64, mac-ci`. Every CI job targets `[self-hosted, mac-ci]` through `fromJSON(vars.CI_RUNS_ON || '["self-hosted", "mac-ci"]')`, never `self-hosted` alone (the Jetson's label too). A daemon per runner gives a job what it had on a GitHub machine (`services:`, its own `127.0.0.1`, names and ports), so parallel jobs cannot collide and nothing a job removes is in the Mac's own daemon. Hooks baked into the image: job-started refuses every job but this repository's `ci.yml` and empties the runner's daemon of containers, networks and volumes; job-started also fails the job when the Mac has less than 10 GB free (read through an empty folder of the Mac mounted read only, the only host mount); job-completed removes the job's containers with their volumes, networks and unused volumes, keeps images and 1 GB of build cache, empties a daemon above 4 GB and empties the job's work folder. Disk budget: about 14 GB for three runners, 20 GB at the bounds (README); `ops/ci-runners.sh try` runs a CI step by hand in an unregistered container of the same shape and removes it and its two scratch volumes when the command ends or the script is stopped; `status`, `prune` and `uninstall` know what a trial left behind, by its three exact names. `uninstall` asks GitHub about every runner before it touches the first container. The runner image sets `OPENSSL_armcap=0` and `CI_COMPOSE_OVERLAY=compose.mac.yml` (the e2e jobs add that file to `compose up`); `ci.yml` never names the variable. Caches (uv, pnpm store, Chromium, pre-commit) live in the shared volume `jobapp-ci`. No variable of the Mac's shell, no folder of the Mac and not the Mac's Docker socket reach a runner; registration tokens go from `gh api` to the container's stdin. Budget (the Mac is shared with its owner and with agents; on 2026-10-10 unbounded trials next to four agents took the load to 82 and the Mac went down): CI takes at most 9 of Docker's 12 CPUs (CPUs 0-8) and 12 GB of about 18, three jobs at once whatever the number of pushes, with worker counts written in `ci.yml` (`PYTEST_WORKERS`, `VITEST_WORKERS`, 3 each); measured, a whole run uses 4 to 6 cores' worth and about 7 GB (the api tests fill their 3 CPUs, an e2e group about 0.6 of them); `install` refuses more than three runners and a disk with less free space than 4 GB per runner plus 6. A container is recreated (`update`, `install` again) only when GitHub reports its runner idle, with a call GitHub does not answer read as unknown and stopping the command, one runner at a time, stopped before it is removed, and the command waits for "Listening for Jobs" in the new container's log (read to its end: a reader that left at the first match once made every runner with a long log look deaf): a run queued while a listener had no session was never assigned (2026-10-10). While the Mac sleeps or Docker Desktop is closed, CI waits (queued jobs are kept 24 hours) and nothing deploys. After a reboot the containers come back with the Docker daemon; Docker Desktop itself must be set to start at login (its own setting, read and reported by the script, never changed by it). `tests/ci_runners_test.sh` covers the script and the hooks with fake `docker` and `gh`, and that both workflows keep their runner labels.
- `./deploy.sh` from the Mac stays the way to deploy by hand, to write the Jetson's `.env` the first time, and to deploy when GitHub is unreachable.

## Migrations on deploy (issue #81, 2026-10-10)

New code never serves on a schema that lacks its columns: the schema moves first.

- Order, the same in `.github/workflows/deploy.yml` and in `./deploy.sh`: build the images, take the predeploy backup (workflow only, as before; a manual `./deploy.sh` takes none, run `./deploy.sh backup` first when the release carries a migration), run `ops/migrate.sh`, then `compose up --wait`, then health and smoke.
- `ops/migrate.sh` runs `alembic upgrade head` with `docker compose run --rm --no-deps -T --name jobapp-migrate api`: a one off container of the api service as the new `compose.yml` defines it, so the image just built, the api's `env_file` and network, no published port, no restart policy, removed when it ends. The api and worker containers of the previous release are not touched and keep serving. A db that is not running is started alone first (a first install creates the empty database there and applies every migration); a running one is left as it is.
- The rule this needs: the previous release must run on the migrated schema, expand first and contract in a later release ([CONTRIBUTING.md](CONTRIBUTING.md), Migrations). `api/tests/test_migration_rules.py` flags the obvious breaches in CI.
- A migration fails: alembic runs the whole upgrade in one transaction, Postgres rolls it back, the script exits non zero and the deploy stops before anything restarts. The previous release keeps serving. The workflow puts the previous files and `:latest` tags back; `./deploy.sh` says that the Jetson's tree and `:latest` images already hold the new release.
- A migration hangs: it is stopped after `MIGRATE_TIMEOUT_S` (default 300), the container removed, and the Postgres sessions of this database that hold or wait for the migration's advisory lock are terminated (`pg_locks` is cluster wide, so the statement names the current database), because Postgres would otherwise keep a vanished client's locks until its statement ends. Same stop as a failure. A container killed before the limit (exit 137, for instance out of the api's `mem_limit`) is reported as killed, not as a timeout, and its session is ended the same way. Removing a leftover container by hand does not end its session: the README's recovery block gives the statement.
- The migration succeeds and the new stack does not become healthy: the workflow's rollback starts the previous files and images again, on the migrated schema. Nothing downgrades the schema automatically, ever: `downgrade()` may drop data, and the expand rule makes it unnecessary. Going back in schema is a human decision: `alembic downgrade` by hand, or the predeploy dump with `ops/db.sh restore-live`.
- At head it is a no-op: one container start, a couple of seconds, "already at head, nothing to apply" in the log.
- Two deploys never migrate at once: the Jetson's deploy lock (`flock`, shared by the workflow and `./deploy.sh`) and the workflow's concurrency group serialize deploys, `ops/migrate.sh` refuses to start while a `jobapp-migrate` container runs, and `alembic/env.py` takes `pg_advisory_xact_lock` so two upgrades that still meet queue instead of racing.
- The log holds alembic's own lines and the revision reached, never a value of `.env`: the settings keep the database URL as a secret, and the script masks the credentials of anything shaped like a URL on the way out.
- Tests: `tests/deploy_test.sh` (CI, fakes) runs `ops/migrate.sh`, `cmd_deploy` and the deploy step cut out of `deploy.yml`, and asserts the order, the stop on a failing or hanging migration with the stack untouched, the no-op, the first install, and a rollback that neither reruns alembic nor downgrades. `tests/migrate_test.sh` (by hand on the Mac, real Docker, never a CI step: it builds the api image four times and the runners' disk and CPU budget is tight; CI only shellchecks it) plays the same paths on a scratch stack with real Postgres and alembic, a killed container and the README's recovery statement included.

## Public access: Tailscale Funnel (decided 2026-10-10)

- Decided by Harald on 2026-10-10, replacing the Cloudflare quick tunnel: the Gmail module needs Google OAuth redirect URIs, and those need a URL that does not change on every restart.
- Tailscale Funnel on Harald's tailnet `taild83d16.ts.net` (free plan, MagicDNS on, HTTPS certificates enabled, the `funnel` node attribute granted to `autogroup:member`). Free, and the URL is stable: `https://jobapp.taild83d16.ts.net`, the node name `TS_HOSTNAME` plus the tailnet. It survives restarts, redeploys and reboots.
- Service `tailscale` in compose.yml, in the `tunnel` profile (Jetson only, never on the Mac): the official image pinned by version and arm64 digest, userspace networking (`TS_USERSPACE=true`, no `/dev/net/tun`, no `NET_ADMIN`, every capability dropped, no sudo on the Jetson), 128m, restart unless-stopped, healthy when `tailscale status` says `Running`. Outbound connections only; no port published.
- Login: a one-time, user-owned auth key in `TS_AUTHKEY` of the Jetson's `.env`, used on the first start only (`TS_AUTH_ONCE`); the login then lives in the `tsstate` volume and the key is removed from `.env`. Without either the service never turns healthy and the deploy fails.
- `tailscale/serve.json` (`TS_SERVE_CONFIG`) terminates TLS on 443 with the node's Let's Encrypt certificate and proxies to `http://caddy:8080`, with Funnel allowed on that port only.
- Client address: Funnel sets `X-Forwarded-For` to the address the visitor connected from, replacing any value sent (checked in the tailscale v1.102.5 source, `ipn/ipnlocal/serve.go`). Caddy's `:8080` trusts private peers and reads `X-Forwarded-For` only, strictly; `Cf-Connecting-Ip` and similar headers pass through Funnel untouched and are never trusted. `./deploy.sh smoke` proves from outside that a visitor cannot spoof its address.
- `./deploy.sh url` reads `Self.DNSName` of `tailscale status --json`; it fails with the reason when the container is down or not logged in.

## Request limits by layer (issue #100, 2026-10-11)

The app is reachable from the internet through Funnel, so a caller with no session must not be able to make the api hold memory with large or slow request bodies. FastAPI reads a JSON body before a route's dependencies run, so even a route that answers 401 reads the body first: measured on `main`, fifty stalled 9 MiB bodies to `POST /api/auth/login` reached the api's 512 MiB limit. Request size and time are bounded in three layers, each able to stand without the others; each layer has its own test.

- **Caddy** (`caddy/Caddyfile`), the edge: the public routes under `/api/auth/` take at most **64 KiB** (a sign in or an invite acceptance is a few hundred bytes, with wide margin); every other route keeps **10 MiB**, raised per route for an upload (the jobs import; the document upload of pull request #98 raises it to 20 MiB on its own path). The two tiers are mutually exclusive expression matchers, kept in one block to compose with #98's upload exception. A declared `Content-Length` over the limit is answered 413 by Caddy itself, nothing read, nothing proxied; a body streamed past the limit is cut. Timeouts, shortened from Caddy's defaults: `read_header` 10s, `read_body_idle` 15s with a 1024 bytes/second minimum rate (a trickle that never fully stalls is still cut), `idle` 60s. Writes keep their defaults (`write_idle` 1m, no rate), so an event stream, whose heartbeat is 15s, is never cut; the idle timeout applies to idle keep-alive connections between requests, not to an active response.
- **api** (`app/body_limit.py`), a second line that does not depend on the proxy: an ASGI middleware before the body is parsed. Public routes 64 KiB, other JSON routes **1 MiB**; the routes that read their own raw body with their own limit (profile photo and import, jobs import, the ingestion upload and paste of #98) pass through untouched, so nothing is read twice. Over the limit is 413 (a declared length before a byte is read, a streamed body cut at the limit and the answer replaced), a body that stalls past a generous idle window is 408. It is the innermost user middleware, so its refusal is still logged, and it bounds the body even on routes that answer 401.
- **uvicorn** (`api/Dockerfile`): `--limit-concurrency 256` bounds how many requests pile up at once on the 512 MiB api container (503 beyond it, ample for a few users with several tabs and long lived streams), `--timeout-keep-alive 5` keeps idle connections short, and h11's 64 KiB header limit bounds a slow or oversized header.

What remains possible, stated honestly for a small private app: an attacker with many client addresses can still open many connections, each now holding at most 64 KiB on a public route and cut within about 15s, so the cost is bounded but not zero. Caddy on the free, open-source build has no per-client connection or request-rate cap without a paid or third-party module, so that is left to the layers above and to the identity module's existing rate limit on sign in (per email and per address). Before and after measurements and the hold-time probe are in `tasks/evidence/platform-100/`.

## Decisions (2026-10-08)

1. Docker group: add `yessai` to the `docker` group once. Harald runs `ssh -t jetson 'sudo usermod -aG docker yessai'` (needs his password), then reconnects.
2. Public access: Cloudflare quick tunnel, everything free; a changing URL is accepted. Superseded on 2026-10-10 by Tailscale Funnel (see Public access).
3. Fixed IP: reserving 192.168.1.47 on the router is recommended, not blocking.
4. Git remote: private GitHub repo with CI and pre-commit, as above. The `gh` token lacks the `workflow` scope, which pushing `.github/workflows/` requires: Harald runs `gh auth refresh -s workflow` once (granted 2026-10-09).
