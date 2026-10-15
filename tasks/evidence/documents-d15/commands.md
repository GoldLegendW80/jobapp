# Documents D15: commands run (2026-10-15, on the laptop, throwaway Postgres and Redis of ops/agent-test.sh)

- `ops/agent-test.sh --name d15 tests/documents -m "not docker" -n 2`: 1486 passed.
- `ops/agent-test.sh --name d15 tests/documents/test_legacy.py`: 17 passed; with `api/app/documents/router.py` put back as on main, `test_the_api_serves_the_legacy_pdf_byte_for_byte_to_its_owner_only` fails (the test can fail).
- `uv run pytest tests/documents/test_legacy_read.py`: 107 passed (pure, no database).
- `uv run ruff check app tests`, `uv run ruff format --check app tests`: clean.
- `tests/deploy_test.sh`: all 171 checks passed (shellcheck through the pinned image included).
- `pnpm --dir web run typecheck`, `pnpm --dir web run lint`, prettier on the new spec: clean.
- `python3 .github/scripts/ci_plan_test.py`, `python3 .github/scripts/e2e_groups.py`: pass.
- `git merge-tree` of this branch with origin/main and with the heads of #342, #345 and #346: no conflict.
- Playwright `documents-d15.spec.ts`: written and typechecked, NOT run (Docker image builds fail on this laptop's network until #359).

The reader's worst shapes at the size cap (`legacy.read`, one run each, on the laptop):

| Input | Bytes | Left out | Time |
|---|---|---|---|
| invalid list items, 3,405 | 11,948 | 3,404 | 35 ms |
| invalid list items, 6,810 | 23,869 | 6,810 | 70 ms |
| invalid list items, 13,620 | 47,704 | 13,620 | 131 ms |
| 9,000 unknown keys (over the cap) | 106,890 | 1 | 0 ms |
| 40,000 nested lists | 80,000 | 1 | 1 ms |
| the invented valid CV | 968 | 0 | 1 ms |

The time doubles when the size doubles: linear.
