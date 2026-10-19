# Tasks: platform

Plan: [plan.md](plan.md). Spec: [SPEC-platform.md](../SPEC-platform.md).

## Task 1: Repository bootstrap

**Description:** Turn `app/` into a git repo with ignore rules, an env template and pre-commit hooks, and push it to a new private GitHub repo.

**Acceptance criteria:**
- [x] `git init`, `.gitignore` (env, node_modules, .venv, dist, caches), `.env.example` listing every variable of the spec
- [x] `.pre-commit-config.yaml` with ruff, ruff-format, prettier, eslint, gitleaks, end-of-file-fixer, trailing-whitespace, check-yaml; `pre-commit install` done
- [x] Private repo `GoldLegendW80/jobapp` created with `gh repo create --private` and first commit pushed

**Verification:**
- [x] `pre-commit run --all-files` passes
- [x] `gh repo view GoldLegendW80/jobapp --json visibility` shows PRIVATE
- [x] A dummy file containing a fake AWS key is refused by gitleaks

**Dependencies:** None

**Files:** `.gitignore`, `.env.example`, `.pre-commit-config.yaml`, `README.md`

**Scope:** S

## Task 2: API skeleton with health

**Description:** FastAPI app managed by uv, with settings from env, a SQLAlchemy session dependency and `GET /api/health` doing a `select 1`, plus its Dockerfile.

**Acceptance criteria:**
- [x] `api/pyproject.toml` with pinned FastAPI, uvicorn, SQLAlchemy 2, psycopg 3, pydantic-settings, alembic; dev: pytest, httpx, ruff
- [x] `/api/health` returns `{"status":"ok"}` and fails (503) when the database is down
- [x] `api/Dockerfile` on `python:3.12-slim`, non-root user, uv sync from lockfile

**Verification:**
- [x] `uv run --directory api pytest` passes (health test against a test database)
- [x] `uv run --directory api ruff check .` clean

**Dependencies:** Task 1

**Files:** `api/pyproject.toml`, `api/Dockerfile`, `api/app/main.py`, `api/app/config.py`, `api/app/db.py`, `api/app/health.py`, `api/tests/test_health.py`

**Scope:** M

## Task 3: Compose core and migrations

**Description:** `compose.yml` with `api`, `db`, `redis`, healthchecks and memory limits; `compose.dev.yml` with bind mounts and reload; Alembic initialised with an empty baseline revision.

**Acceptance criteria:**
- [x] `docker compose up` brings api, db, redis to healthy; only dev publishes ports
- [x] Every service has `mem_limit` and `restart: unless-stopped`; named volume `pgdata`
- [x] `alembic upgrade head` succeeds against the compose database

**Verification:**
- [x] `docker compose -f compose.yml -f compose.dev.yml up -d --build` then `curl localhost:8000/api/health` returns ok
- [x] `docker compose exec api pytest` passes

**Dependencies:** Task 2

**Files:** `compose.yml`, `compose.dev.yml`, `api/alembic.ini`, `api/alembic/env.py`, `api/alembic/versions/0001_baseline.py`

**Scope:** M

## Checkpoint A: local backend
- [x] Stack healthy locally, pytest green, pre-commit clean

## Task 4: First Jetson deploy

**Description:** `deploy.sh` with `deploy` (default) and `logs <service>`: rsync to `jetson:~/jobapp`, copy `.env` with mode 600, remote `docker compose up -d --build`, then migrations. Fail fast on arm64 and memory.

**Prerequisite (Harald):** `ssh -t jetson 'sudo usermod -aG docker $USER'`, then reconnect.

**Acceptance criteria:**
- [x] `./deploy.sh` from the Mac builds api on the Jetson and brings api, db, redis to healthy
- [x] `ssh jetson curl -s localhost/api/health` returns ok through Caddy (port 80 on loopback since task 6)
- [x] Build time and idle RAM per service recorded in `tasks/plan.md`

**Verification:**
- [x] `./deploy.sh` exits 0; a second run is idempotent
- [x] `ssh jetson docker stats --no-stream` shows every service under its limit

**Dependencies:** Task 3

**Files:** `deploy.sh`, `.rsyncignore`

**Scope:** S

## Checkpoint B: arm64 confirmed
- [x] Results of task 4 reviewed with Harald before building the frontend

## Task 5: Web skeleton

**Description:** Vite, React 18, TypeScript, Tailwind CSS 4, shadcn initialised; the home page renders a shadcn Card that shows the API health through TanStack Query; multi-stage Dockerfile producing static files.

**Acceptance criteria:**
- [x] `pnpm --dir web build` produces `dist/`; one shadcn component (Card, Button) renders styled
- [x] The page shows "API ok" from `/api/health` (Vite proxy in dev)
- [x] ESLint, Prettier and `tsc --noEmit` clean

**Verification:**
- [x] `pnpm --dir web test` (Vitest: health card renders the status from a mocked fetch)
- [x] `pnpm --dir web dev` and open the page: card shows "API ok"

**Dependencies:** Task 3

**Files:** `web/package.json`, `web/vite.config.ts`, `web/components.json`, `web/src/App.tsx`, `web/src/lib/api.ts`, `web/src/App.test.tsx`, `web/Dockerfile`

**Scope:** M

## Task 6: Caddy in front

**Description:** Caddy image built from the web dist output, serving `/` with SPA fallback and proxying `/api` to `api:8000`. Only Caddy publishes a port; remove the temporary api port.

**Acceptance criteria:**
- [x] `http://localhost/` serves the shell, `http://localhost/api/health` returns ok
- [x] Unknown frontend routes fall back to `index.html`; `api`, `db`, `redis` publish no port in `compose.yml`

**Verification:**
- [x] `curl -s localhost/api/health` and `curl -s localhost/some/route | grep '<div id="root">'`
- [x] Deployed to the Jetson with `./deploy.sh`, same two curls over ssh

**Dependencies:** Tasks 4, 5

**Files:** `Caddyfile`, `compose.yml`, `web/Dockerfile`

**Scope:** S

## Task 7: GitHub delivery: CI, pull request flow, self-hosted runner deploy

**Description:** The pull request flow of CONTRIBUTING.md; GitHub Actions CI on every pull request and push to `main` (api, web, shell, e2e, pre-commit); a self-hosted runner in a container on the Jetson; a deploy workflow that ships every green `main` to the Jetson (SPEC-platform.md, Repository and automation).

**Prerequisite (Harald):** `gh auth refresh -s workflow` (granted 2026-10-09).

**Acceptance criteria:**
- [ ] `.github/workflows/ci.yml` with jobs `api` (Postgres and Redis services, ruff, pytest, coverage at least 95 percent, JUnit summary), `web` (lint, typecheck, Vitest, build), `shell` (shellcheck, `tests/deploy_test.sh`), `e2e` (scratch Compose stack, Playwright journeys, synthetic jobs import, artifacts on failure) and `precommit`; cached uv and pnpm; actions pinned by SHA; minimal permissions; superseded runs cancelled
- [ ] `.github/pull_request_template.md` and `CONTRIBUTING.md` (branch naming, conventional commits, review subagent, merge commits, enforcement by convention on the free plan)
- [ ] `ops/runner/` and `./deploy.sh runner install|status|remove`: the runner online on GitHub with labels `self-hosted, jetson, arm64`, no sudo, no token stored
- [ ] `.github/workflows/deploy.yml`: on green CI of a push to `main` and by `workflow_dispatch`; build before restart, backup before migrations, smoke, public URL in the summary; one deploy at a time
- [ ] Green CI on the pull request and on `main`

**Verification:**
- [ ] `gh run watch` on the pull request's run ends in success
- [ ] A deliberately failing test on a throwaway branch turns the run red; the branch is deleted
- [ ] `gh api repos/GoldLegendW80/jobapp/actions/runners` lists `jetson` online
- [ ] `deploy.yml` dispatched on the task branch deploys that commit to the Jetson, smoke passes, and Harald's user row, profile revision and job counts per status are identical before and after
- [ ] After the merge, the push to `main` deploys by itself

**Dependencies:** Tasks 2, 5, 9

**Files:** `.github/`, `CONTRIBUTING.md`, `ops/runner/`, `deploy.sh`, `tests/jetson_smoke.sh`, `tests/deploy_test.sh`, `README.md`, `SPEC-platform.md`

**Scope:** M

## Checkpoint C: full stack locally and in CI
- [ ] Localhost through Caddy works, CI green, Jetson serves through Caddy on the LAN

## Task 8: Quick tunnel, url and smoke

**Description:** `cloudflared` service running a quick tunnel to `caddy:80`; `deploy.sh url` extracts the current `*.trycloudflare.com` URL from its logs; `deploy.sh smoke` checks health through Caddy on the Jetson, then through the tunnel.

**Acceptance criteria:**
- [x] After deploy, `./deploy.sh url` prints an https URL
- [x] `curl $(./deploy.sh url)/api/health` returns ok from the Mac on mobile data or any outside network
- [x] `./deploy.sh smoke` exits 0 and non-zero when api is stopped

**Verification:**
- [x] Run smoke twice: once normal, once after `docker compose stop api` on the Jetson

**Dependencies:** Task 6

**Files:** `compose.yml`, `deploy.sh`

**Scope:** S

## Task 9: Backup, restore, reboot and RAM

**Description:** `deploy.sh backup` (pg_dump gzip to `jetson:~/jobapp-backups/`) and `deploy.sh restore <file>` into a fresh database; prove reboot recovery and the idle RAM budget.

**Acceptance criteria:**
- [x] Backup file created; restore into a scratch database succeeds and a seed row survives the round trip
- [x] After `sudo reboot` of the Jetson, `./deploy.sh smoke` passes within 2 minutes (new tunnel URL printed)
- [x] Idle stack under 1G RAM total in `docker stats`

**Verification:**
- [x] Commands above run once and their output pasted in the commit message of this task

**Dependencies:** Task 8

**Files:** `deploy.sh`, `tasks/plan.md`

**Scope:** S

## Checkpoint D: spec complete
- [x] All 6 success criteria of SPEC-platform.md checked
- [x] Review with Harald before writing SPEC-identity.md (identity spec approved 2026-10-08)
