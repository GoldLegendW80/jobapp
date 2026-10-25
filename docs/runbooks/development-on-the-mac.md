# Development on the Mac

Run the stack on an Apple silicon Mac with Docker Compose, including the `OPENSSL_armcap=0` fix.

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
compose `db` and runs a few test files there with `TEST_DATABASE_URL` pointing at it (not the suite:
under the api's 512m it ended with status 137 after twenty minutes; the whole suite is CI's).
The smoke never downgrades the dev database: its migration round trip runs on a throwaway project.
`COMPOSE_PROJECT_NAME=jobapp-x` runs its dev stack under another project name, and
`SMOKE_DEV_OVERLAY` and `SMOKE_PROD_OVERLAY` name one more Compose file for each of its two stacks,
for image tags of one's own on a machine where `jobapp-*:dev` and `jobapp-*:latest` are in use.
`pytest -n 4` (pytest-xdist) runs four workers, each on a database of its own created next to
`TEST_DATABASE_URL`'s and dropped at the end; keep the `docker`-marked tests serial (`-m docker`
without `-n`). The suite hashes passwords with a cheap argon2 cost in-process; tests marked
`production_argon2` and a fresh-interpreter check keep the production cost honest.
Migrations live in `api/alembic/versions/`; models register on `app.models_base.Base` and are imported
in `api/app/models.py` so `alembic revision --autogenerate` sees them.

## Apple silicon: `OPENSSL_armcap=0`, on the Mac only

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
