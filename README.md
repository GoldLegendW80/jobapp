# jobapp

A self-hosted job-search app for Harald and a few invited friends. It finds postings, scores them against a profile, writes a tailored CV and letter per job, and lets an agent fill application forms in a live browser. It runs as a Docker Compose stack on a Jetson Orin Nano at home and is reached through a Cloudflare quick tunnel.

## Documents

- [CAPABILITY-MAP.md](CAPABILITY-MAP.md): the modules, their dependencies and the build order.
- [SPEC-platform.md](SPEC-platform.md): the platform module (Compose stack, deploy, tunnel), built first.
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
Migrations live in `api/alembic/versions/`; models register on `app.models_base.Base` and are imported
in `api/app/models.py` so `alembic revision --autogenerate` sees them.

## Deploy to the Jetson

```bash
./deploy.sh                    # preflight, rsync to jetson:~/jobapp, build there, compose up --wait, migrate, health
./deploy.sh --env              # same, and replace the Jetson's .env with .env.jetson
./deploy.sh logs api           # follow the logs of one service (last 200 lines first)
./deploy.sh ps                 # docker compose ps on the Jetson
./deploy.sh stats              # memory and CPU per service
./deploy.sh ssh                # shell in the deploy directory
./deploy.sh url                # print the current trycloudflare URL (task 8)
./deploy.sh smoke              # health through Caddy, then through the public URL (task 8)
./deploy.sh backup             # pg_dump to jetson:~/jobapp-backups/YYYY-MM-DD.sql.gz (task 9)
tests/deploy_test.sh           # local tests of deploy.sh: shellcheck, dispatcher, unreachable host, .rsyncignore
tests/jetson_smoke.sh          # smoke of the deployed stack over ssh (stops and starts db once)
```

Images are built on the Jetson itself (arm64) with only `compose.yml`. `.rsyncignore` keeps
secrets, `node_modules`, `.venv`, `dist`, `.git` and evidence off the Jetson, and since `.env` is
excluded, `rsync --delete` never removes the Jetson's `.env`. The Jetson's production `.env` is
generated on the first deploy into the git-ignored `.env.jetson` (random `POSTGRES_PASSWORD` and
`SECRET_KEY`, `APP_ENV=production`, mode 600) and copied only when the Jetson has none or with
`--env`; `--env` refuses to change `POSTGRES_PASSWORD` once the `pgdata` volume exists. If
`.env.jetson` is lost, the next deploy copies the Jetson's `.env` back. Caddy listens on the
Jetson's loopback only; from the Mac, open it with `ssh -N -L 18080:127.0.0.1:80 jetson` and browse
http://127.0.0.1:18080. `DEPLOY_HOST` and `DEPLOY_DIR` override the target. Deploys are manual;
GitHub Actions only runs lint and tests.
