# Brief for implementation subagents

You implement exactly one task of `tasks/todo.md`. The orchestrator verifies your work before ticking it.

## Read first
- `CAPABILITY-MAP.md`, the `SPEC-<module>.md` of your module, `tasks/plan.md`, and your task block in `tasks/todo.md`.
- The existing code you build on. Match its style.

## Environment
- Repo: `/Users/harald-thorvaldhautin/Desktop/job/app`, git, branch `main`, remote `GoldLegendW80/jobapp` (private, HTTPS with a repo-local gh credential helper). Never touch files outside `app/` except reading `../engine`, `../profile` and `../data` when the task ports them.
- Mac: arm64, Docker Desktop running, uv, pnpm, node 22, pre-commit, gitleaks, Playwright (Python via `uv run --native-tls --with playwright`, CLI via `npx -y @playwright/cli@latest`).
- `uv` needs `--native-tls` on this network (the old `--system-certs` flag does not exist in uv 0.9).
- Jetson: `ssh jetson` (Ubuntu 22.04, arm64, 7.4G RAM, Docker 29, Compose v5). Ask the orchestrator before running anything with sudo there.

## Bar
- Tests are not optional: unit tests for logic, integration tests against real Postgres and Redis (Docker), and edge cases written down and tested (empty input, invalid input, duplicates, concurrency, service down, timeouts, unicode, very long values).
- Every task with a UI or an HTTP surface ends with a real-world Playwright check against the running stack (headless Chromium is fine for our own app), with a screenshot saved under `tasks/evidence/<task>/`.
- `pre-commit run --all-files` passes; tests pass; the build passes.
- Pin versions. arm64-compatible only. No secrets in git.
- Outward and user-facing text uses no dashes as separators (commas, colons, new sentences instead).

## Finish
- Commit on `main` with clear messages ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and push.
- Do not tick boxes in `tasks/todo.md`; the orchestrator does.
- Report: files changed, commands run with key output, tests (count, what edge cases), Playwright evidence paths, what failed or was skipped and why, follow-ups for later tasks.
