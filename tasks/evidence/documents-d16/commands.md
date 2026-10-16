# Documents D16: commands run (2026-10-16, on the laptop, throwaway Postgres and Redis of `ops/agent-test.sh`)

- `ops/agent-test.sh --name d16 tests/documents -m "not docker" -n 2`: 1363 passed.
- `uv --native-tls run ruff check app tests`, `ruff format --check app tests`: clean.
- `pnpm --dir web run typecheck`, `pnpm --dir web run lint`: clean.
- `pnpm --dir web exec vitest run src/features/documents`: 7 files, 145 passed (before the counter fix below; the two D16 files rerun after it: 21 passed).
- `edit-panel.test.tsx -t "typed during a save"` with the in flight guard of `edit-autosave.ts` disabled: 1 failed (the test can fail); restored: passed.
- `edit-content.test.ts` counter cases `Zoe` with a combining diaeresis and a line with trailing spaces: 4 and 10 characters with the old count, 3 and 7 (as `content._tidy` leaves them) with the new one.
- `python3 .github/scripts/ci_plan_test.py`, `python3 .github/scripts/e2e_groups.py`: pass.
- `git merge-tree --write-tree` against `origin/main` and the open pull requests that touch documents files (#342, #345, #363, #365, #368): no conflict.
- Playwright `documents-d16.spec.ts`: written and type checked, NOT run: this laptop cannot build the stack's images until #359 (pull request #366) lands.
