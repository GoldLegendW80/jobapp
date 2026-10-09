# Spec: platform

Module of [CAPABILITY-MAP.md](CAPABILITY-MAP.md). Depends on nothing; every other module runs on it.

## Objective

A repeatable way to build, run and deploy the app on the Jetson. After this module, a single command on the Mac ships the code to the Jetson and brings up a stack that answers `https://<random>.trycloudflare.com/api/health` from the internet through a Cloudflare quick tunnel, with an empty React page at `/`. No product features yet: this is the skeleton every later module plugs into.

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
| `cloudflared` | `cloudflare/cloudflared` | Quick tunnel to Caddy (`tunnel --no-autoupdate --url http://caddy:8080`, Caddy's unpublished tunnel listener that trusts `Cf-Connecting-Ip`): free, no account, no domain; the `*.trycloudflare.com` URL changes on every restart of the container |

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
./deploy.sh url                # print the current trycloudflare URL from the cloudflared logs
./deploy.sh backup             # pg_dump -Fc to jetson:~/jobapp-backups/jobapp-YYYY-MM-DDTHHMMSS.dump
./deploy.sh restore <file>     # into a scratch database; --into-live replaces the live one
```

Images are built on the Jetson itself (native arm64), so no cross-build is needed.

## Project structure

```
app/
  CAPABILITY-MAP.md  SPEC-*.md
  compose.yml           production stack
  compose.dev.yml       dev overrides (bind mounts, reload, no cloudflared)
  .env.example          every variable, no secrets; .env is git-ignored
  deploy.sh             rsync + remote compose + migrations + backup
  Caddyfile
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
- Platform itself is verified by a smoke script, `deploy.sh smoke`: health through Caddy on the Jetson, then through the public tunnel URL.

## Boundaries

- **Always:** pin image tags and dependency versions; arm64 images only; `mem_limit` on every service; secrets only in `.env`; run tests before deploying.
- **Ask first:** adding a service to Compose, exposing a port on the Jetson's network interface, changing the tunnel setup, anything run with `sudo` on the Jetson.
- **Never:** commit `.env` or any token; open a port on the home router; touch the Jetson's existing Ollama or system services; run `docker system prune` or delete volumes without asking.

## Success criteria

1. `./deploy.sh` from a clean clone brings the stack up on the Jetson with no manual step beyond writing `.env`.
2. `curl $(./deploy.sh url)/api/health` from outside the home network returns `{"status":"ok"}`, with a database round trip.
3. The tunnel URL serves the React shell with Tailwind and one shadcn component rendered.
4. A reboot of the Jetson brings everything back (`restart: unless-stopped`) within 2 minutes.
5. The idle stack uses under 1G of RAM (`docker stats`).
6. `./deploy.sh backup` produces a restorable dump; a restore into a fresh database is tested once.

## Repository and automation

- Private GitHub repo `GoldLegendW80/jobapp` (created with `gh`), monorepo: `api/` and `web/` side by side.
- pre-commit hooks: ruff (lint and format), prettier and eslint on `web/`, gitleaks (no secret ever committed), end-of-file and trailing-whitespace fixers, check-yaml.
- Flow ([CONTRIBUTING.md](CONTRIBUTING.md)): one branch per task named `<module>/<task-id>-<slug>`, conventional commits, one pull request per task, CI green plus an approving review by a separate Claude review subagent (`gh pr review`), merged by the orchestrator with a merge commit. Branch protection and rulesets are not available for private repositories on the free plan: the rules hold by convention and by the orchestrator.
- `.github/workflows/ci.yml` on every pull request and push to `main`, on `ubuntu-latest` (x86, tests only): `api` (ruff lint and format, pytest against Postgres 16 and Redis service containers through `TEST_DATABASE_URL`, docker-marked tests deselected, coverage at least 95 percent, JUnit summary), `web` (pnpm from `packageManager`, eslint, tsc, Vitest, build), `shell` (shellcheck, `tests/deploy_test.sh` with fakes), `e2e` (the Compose stack built on the runner with a generated `.env`, migrations, the identity, invites, users, account, profile, throttle and jobs journeys in Playwright, the jobs one on a synthetic import from `.github/scripts/synthetic_jobs.py`; report, traces and screenshots uploaded on failure) and `precommit` (file fixers, gitleaks, prettier). Superseded runs are cancelled, actions are pinned by commit SHA, `permissions: contents: read`.
- `.github/workflows/deploy.yml`: when CI finishes green for a push to `main` (`workflow_run`), or by hand (`workflow_dispatch`, any ref), on the self-hosted runner of the Jetson: rsync of the commit into `~/jobapp` with `.rsyncignore` (its `.env` kept), images built before anything restarts, a backup into `~/jobapp-backups/deploys/` (newest 10 kept, apart from the nightly ones), `compose up --wait`, `alembic upgrade head`, `tests/jetson_smoke.sh` in quick mode, the public URL in the job summary. One deploy at a time, never cancelled.
- Self-hosted runner (`ops/runner/`, `./deploy.sh runner install|status|remove`): the official arm64 actions runner in a container on the Jetson (no sudo), persistent, labels `self-hosted, jetson, arm64`, host network, the host Docker socket and CLI, `~/jobapp` and `~/jobapp-backups` mounted at their own paths, restart unless-stopped. Outbound connections only. With the Docker socket it is root-equivalent on the Jetson: it serves this private repository only, never forks.
- `./deploy.sh` from the Mac stays the way to deploy by hand, to write the Jetson's `.env` the first time, and to deploy when GitHub is unreachable.

## Decisions (2026-10-08)

1. Docker group: add `yessai` to the `docker` group once. Harald runs `ssh -t jetson 'sudo usermod -aG docker yessai'` (needs his password), then reconnects.
2. Public access: Cloudflare quick tunnel, everything free; a changing URL is accepted.
3. Fixed IP: reserving 192.168.1.47 on the router is recommended, not blocking.
4. Git remote: private GitHub repo with CI and pre-commit, as above. The `gh` token lacks the `workflow` scope, which pushing `.github/workflows/` requires: Harald runs `gh auth refresh -s workflow` once (granted 2026-10-09).
