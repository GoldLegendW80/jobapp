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
docker compose -f compose.yml -f compose.dev.yml up --build    # hot reload for api and web
docker compose exec api alembic upgrade head                   # apply migrations
docker compose exec api pytest                                 # backend tests
pnpm --dir web test                                            # frontend tests
pnpm --dir web lint && uv run --directory api ruff check .     # lint
```

## Deploy to the Jetson

```bash
./deploy.sh                    # rsync to jetson:~/jobapp, compose up, run migrations
./deploy.sh logs api           # tail logs of one service
./deploy.sh url                # print the current trycloudflare URL
./deploy.sh backup             # pg_dump to jetson:~/jobapp-backups/YYYY-MM-DD.sql.gz
./deploy.sh smoke              # health through Caddy, then through the public URL
```

Images are built on the Jetson itself (arm64). Deploys are manual; GitHub Actions only runs lint and tests.
