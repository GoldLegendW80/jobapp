# Documents D9: commands run (2026-10-14, on the Mac, throwaway postgres:16.15-alpine and redis:7.4.11-alpine)

- `nice -n 15 uv --native-tls run pytest -n 2 -q tests/documents tests/ingestion/test_pricing.py -m "not docker"`: 1091 passed.
- `pytest tests/identity/test_guard_api.py tests/identity/test_body_limit_api.py tests/agents/test_g6_internal.py tests/agents/test_api.py tests/documents/test_base_api.py`: all passed after `script` got its `max_length`.
- `tests/documents/test_base_api.py -k claim_added` with D6's `render_job._base_of` restored: 1 failed (the test can fail), with the change: passed.
- `uv --native-tls run ruff check app tests`, `ruff format --check app tests`: clean.
- `pnpm --dir web run typecheck`, `pnpm --dir web run lint`: clean.
- `python3 .github/scripts/ci_plan_test.py`, `python3 .github/scripts/e2e_groups.py`: pass.
- Playwright `documents-d9.spec.ts`: written, NOT run (the local check held `.local-check/lock` throughout).
