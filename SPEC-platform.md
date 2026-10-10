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
./deploy.sh                    # rsync app/ to jetson:~/jobapp, then on the Jetson:
                               # docker compose up -d --build && docker compose exec api alembic upgrade head
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
- pre-commit hooks: ruff (lint and format), prettier and eslint on `web/`, gitleaks (no secret ever committed), end-of-file and trailing-whitespace fixers, check-yaml.
- Flow ([CONTRIBUTING.md](CONTRIBUTING.md)): one branch per task named `<module>/<task-id>-<slug>`, conventional commits, one pull request per task, CI green plus an approving review by a separate Claude review subagent (`gh pr review`), merged by the orchestrator with a merge commit. Branch protection and rulesets are not available for private repositories on the free plan: the rules hold by convention and by the orchestrator.
- `.github/workflows/ci.yml` on every pull request and push to `main`, on the Mac's self-hosted runners `[self-hosted, mac-ci]` (Linux arm64, tests only; `ubuntu-latest` again by setting the repository variable `CI_RUNS_ON`, see CI runners on the Mac below): `api` in two shards on two runners, `jobs` (`tests/jobs`) and `rest` (everything else), each with pytest-xdist against Postgres 16 and Redis service containers through `TEST_DATABASE_URL`, writing coverage data only (ruff lint and format and the docker-marked tests, in their own step on the runner's Docker daemon, run in `rest`; the scoring timing test runs alone in `jobs`; JUnit summary), `api-coverage` (the data of both shards combined, at least 95 percent: the floor is checked here, by a command that is not piped, and the job fails rather than being skipped when a shard failed), `web` (pnpm from `packageManager`, eslint, tsc, build), `web-tests` (Vitest in two shards), `shell` (shellcheck, `tests/deploy_test.sh` with fakes, `.github/scripts/e2e_groups.py` and its test: every `web/e2e/*.spec.ts` is in an e2e group or in that script's exclusion list with a reason), `e2e` in three groups on three runners, `identity`, `sweep` and `apps` (each builds the images with buildx bake, from the layer cache of its runner's own Docker daemon on the Mac, or from the GitHub Actions layer cache, which only the `identity` group writes, on GitHub's machines; starts its own Compose stack with a generated `.env` and the ATS stubs of `tests/ats-stub.compose.yml`, where the worker runs with `TEST_FAST_TIMERS`, runs the migrations, then its Playwright journeys: the identity chain, the sweeps against the fake ATS, or the jobs, map and agent journeys on a synthetic import from `.github/scripts/synthetic_jobs.py`; report, traces and screenshots uploaded on failure) and `precommit` (file fixers, gitleaks, prettier, the e2e groups guard). Every run step uses bash with `-eo pipefail`. Superseded runs are cancelled, actions are pinned by commit SHA, `permissions: contents: read`.
- `.github/workflows/deploy.yml`: when CI finishes green for a push to `main` (`workflow_run`), or by hand (`workflow_dispatch` with an input `ref`, default `main`; the workflow definition always comes from `main`), on the self-hosted runner of the Jetson, under a `flock` on `~/jobapp-backups/.deploy.lock` shared with `./deploy.sh`: rsync of the commit into `~/jobapp` with `.rsyncignore` (its `.env` and `.deployed-commit` kept), images built before anything restarts (running ones tagged `:previous`), a backup into `~/jobapp-backups/deploys/` whenever the database volume exists (newest 10 kept, apart from the nightly ones), `compose up --wait`, `alembic upgrade head` (either failing puts the `:previous` images back), `tests/jetson_smoke.sh` in quick mode, `.deployed-commit` written, the public URL in the job summary. One concurrency group per ref, a started deploy never cancelled.
- Self-hosted runner (`ops/runner/`, `./deploy.sh runner install|status|remove`): the official arm64 actions runner in a container on the Jetson (no sudo), persistent, labels `self-hosted, jetson, arm64`, host network, the host Docker socket and CLI, `~/jobapp` and `~/jobapp-backups` mounted at their own paths, restart unless-stopped. Outbound connections only. With the Docker socket it is root-equivalent on the Jetson, so a job-started hook baked into the image (`ops/runner/job-guard.sh`) refuses every job except this repository's `deploy.yml` from `refs/heads/main` on `workflow_run` or `workflow_dispatch`; pull requests, branches and other workflows never run there.
- CI runners on the Mac (`ops/ci-runner/`, `ops/ci-runners.sh install|status|stop|start|update|prune|uninstall`; decision of 2026-10-10, issue #83: the free minutes of GitHub's machines for private repositories were used up, CI moves to the owner's Mac, the Jetson keeps the deploys, nothing is paid): four instances `mac-ci-1` to `mac-ci-4` of the official actions runner for Linux arm64 (version and SHA-256 pinned), each in a privileged container of Docker Desktop with a Docker daemon of its own (the Jetson's Engine and Compose versions), 3 CPUs and at most 5 GB, restart unless-stopped, labels `self-hosted, Linux, ARM64, mac-ci`. Every CI job targets `[self-hosted, mac-ci]` through `fromJSON(vars.CI_RUNS_ON || '["self-hosted", "mac-ci"]')`, never `self-hosted` alone (the Jetson's label too). A daemon per runner gives a job what it had on a GitHub machine (`services:`, its own `127.0.0.1`, names and ports), so parallel jobs cannot collide and nothing a job removes is in the Mac's own daemon. Hooks baked into the image: job-started refuses every job but this repository's `ci.yml` and empties the runner's daemon of containers, networks and volumes; job-completed does the same, keeps images and 2 GB of build cache, empties a daemon above 6 GB and deletes Playwright's output. The runner image sets `OPENSSL_armcap=0` and `CI_COMPOSE_OVERLAY=compose.mac.yml` (the e2e jobs add that file to `compose up`); `ci.yml` never names the variable. Caches (uv, pnpm store, Chromium, pre-commit) live in the shared volume `jobapp-ci`. No variable of the Mac's shell, no folder of the Mac and not the Mac's Docker socket reach a runner; registration tokens go from `gh api` to the container's stdin. Four runners are the limit on what runs at once. While the Mac sleeps or Docker Desktop is closed, CI waits (queued jobs are kept 24 hours) and nothing deploys. `tests/ci_runners_test.sh` covers the script and the hooks with fake `docker` and `gh`, and that both workflows keep their runner labels.
- `./deploy.sh` from the Mac stays the way to deploy by hand, to write the Jetson's `.env` the first time, and to deploy when GitHub is unreachable.

## Public access: Tailscale Funnel (decided 2026-10-10)

- Decided by Harald on 2026-10-10, replacing the Cloudflare quick tunnel: the Gmail module needs Google OAuth redirect URIs, and those need a URL that does not change on every restart.
- Tailscale Funnel on Harald's tailnet `taild83d16.ts.net` (free plan, MagicDNS on, HTTPS certificates enabled, the `funnel` node attribute granted to `autogroup:member`). Free, and the URL is stable: `https://jobapp.taild83d16.ts.net`, the node name `TS_HOSTNAME` plus the tailnet. It survives restarts, redeploys and reboots.
- Service `tailscale` in compose.yml, in the `tunnel` profile (Jetson only, never on the Mac): the official image pinned by version and arm64 digest, userspace networking (`TS_USERSPACE=true`, no `/dev/net/tun`, no `NET_ADMIN`, every capability dropped, no sudo on the Jetson), 128m, restart unless-stopped, healthy when `tailscale status` says `Running`. Outbound connections only; no port published.
- Login: a one-time, user-owned auth key in `TS_AUTHKEY` of the Jetson's `.env`, used on the first start only (`TS_AUTH_ONCE`); the login then lives in the `tsstate` volume and the key is removed from `.env`. Without either the service never turns healthy and the deploy fails.
- `tailscale/serve.json` (`TS_SERVE_CONFIG`) terminates TLS on 443 with the node's Let's Encrypt certificate and proxies to `http://caddy:8080`, with Funnel allowed on that port only.
- Client address: Funnel sets `X-Forwarded-For` to the address the visitor connected from, replacing any value sent (checked in the tailscale v1.102.5 source, `ipn/ipnlocal/serve.go`). Caddy's `:8080` trusts private peers and reads `X-Forwarded-For` only, strictly; `Cf-Connecting-Ip` and similar headers pass through Funnel untouched and are never trusted. `./deploy.sh smoke` proves from outside that a visitor cannot spoof its address.
- `./deploy.sh url` reads `Self.DNSName` of `tailscale status --json`; it fails with the reason when the container is down or not logged in.

## Decisions (2026-10-08)

1. Docker group: add `yessai` to the `docker` group once. Harald runs `ssh -t jetson 'sudo usermod -aG docker yessai'` (needs his password), then reconnects.
2. Public access: Cloudflare quick tunnel, everything free; a changing URL is accepted. Superseded on 2026-10-10 by Tailscale Funnel (see Public access).
3. Fixed IP: reserving 192.168.1.47 on the router is recommended, not blocking.
4. Git remote: private GitHub repo with CI and pre-commit, as above. The `gh` token lacks the `workflow` scope, which pushing `.github/workflows/` requires: Harald runs `gh auth refresh -s workflow` once (granted 2026-10-09).
