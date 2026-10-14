# Documents D6: what was run (2026-10-14)

## The journey group `documents`

On a scratch Compose project of its own (`compose.yml`, `tests/ats-stub.compose.yml`,
`compose.mac.yml`, images built under their own tags, as `ops/local-check.sh` does), migrated
with `alembic upgrade head`, one Playwright worker:

    E2E_CADDY_URL=http://127.0.0.1:<port> E2E_COMPOSE_PROJECT=<project> E2E_EVIDENCE=documents-d6 \
      nice -n 15 pnpm exec playwright test -c e2e/documents-all.config.ts --workers=1

    ✓ an invented person with a profile, a base CV and a posting
    ✓ a hand written draft renders to a one page PDF with the person's facts
    ✓ switching to sidebar makes a new one page version, the first unchanged
    ✓ the posting is cv_ready
    4 passed (5.2s)

The first run failed at the render with `render_failed`: the worker logged `ConfigError`. The
worker drops its secrets from the environment before it serves, and `render_client` and
`store` read `get_settings()`, which was not cached yet. Fixed in `app/worker.py` (`main` loads
the settings through `get_settings()`), with a test that was red before the fix
(`tests/documents/test_worker_writers.py::test_a_render_reads_its_settings_after_the_secrets_left_the_environment`).

Files here, written by the journey: `classic.pdf` and `classic-page1.png` (version 1),
`sidebar.pdf` and `sidebar-page1.png` (version 2). Every name, employer and address in them is
invented.
