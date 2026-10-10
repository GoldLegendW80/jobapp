# Brief for implementation subagents

You implement exactly one task of `tasks/todo.md` or of your module's `tasks/<module>/todo.md`. The orchestrator verifies your work before ticking it.

## Read first
- `CAPABILITY-MAP.md`, the `SPEC-<module>.md` of your module, `tasks/plan.md`, and your task block in `tasks/todo.md` or `tasks/<module>/todo.md`.
- The existing code you build on. Match its style.

## Environment
- Repo: `/Users/harald-thorvaldhautin/Desktop/job/app`, git, default branch `main` (protected by convention: changes arrive only through PRs), remote `GoldLegendW80/jobapp` (private, HTTPS with a repo-local gh credential helper). CI: `.github/workflows/ci.yml`; deploy: `.github/workflows/deploy.yml` on the Jetson's self-hosted runner. Never touch files outside `app/` except reading `../engine`, `../profile` and `../data` when the task ports them.
- Mac: arm64, Docker Desktop running, uv, pnpm, node 22, pre-commit, gitleaks, Playwright (Python via `uv run --native-tls --with playwright`, CLI via `npx -y @playwright/cli@latest`).
- The Mac is an M4: a stack started from `compose.yml` alone needs `-f compose.mac.yml` on its `up` (`OPENSSL_armcap=0`), or the api and the worker die with SIGILL, exit status 132 (README, Apple silicon). Never put that variable in `compose.yml`.
- `uv` needs `--native-tls` on this network (the old `--system-certs` flag does not exist in uv 0.9).
- Jetson: `ssh jetson` (Ubuntu 22.04, arm64, 7.4G RAM, Docker 29, Compose v5). Ask the orchestrator before running anything with sudo there.

## Bar
- Tests are not optional: unit tests for logic, integration tests against real Postgres and Redis (Docker), and edge cases written down and tested (empty input, invalid input, duplicates, concurrency, service down, timeouts, unicode, very long values).
- Every task with a UI or an HTTP surface ends with a real-world Playwright check against the running stack (headless Chromium is fine for our own app), with a screenshot saved under `tasks/evidence/<task>/`.
- `pre-commit run --all-files` passes; tests pass; the build passes.
- Pin versions. arm64-compatible only. No secrets in git.
- Outward and user-facing text uses no dashes as separators (commas, colons, new sentences instead).

## Delivery (GitHub flow, see CONTRIBUTING.md)
- Work on your own branch named `<module>/<task-id>-<slug>` (in a worktree when given one), never on `main`.
- Commit with clear conventional messages ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, push the branch, open a PR with `gh pr create` using the template, and make CI green on it.
- Never merge, never push to `main`, never deploy the app to the Jetson: the orchestrator merges after the review subagent's `REVIEW VERDICT: APPROVE`, and every merge to `main` deploys automatically. Real-world checks that change the Jetson's state happen after the merge, run by the orchestrator or a follow-up task; read-only checks on the Jetson (SQL selects, logs, status) are fine before merge.
- Do not tick boxes in `tasks/todo.md` or `tasks/<module>/todo.md`; the orchestrator does.
- Report: files changed, commands run with key output, tests (count, what edge cases), Playwright evidence paths, what failed or was skipped and why, follow-ups for later tasks.
