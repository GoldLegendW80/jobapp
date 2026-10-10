# Handoff (2026-10-10)

State of the jobapp build at the end of session 1e1a4e51, for the next orchestrator session.

## Where things are

- Repo: private GitHub `GoldLegendW80/jobapp`, local checkout `~/Desktop/job/app` (on `main`).
- Live app: https://jobapp.taild83d16.ts.net (Tailscale Funnel), Jetson `ssh jetson` (yessai@192.168.1.47), stack in `~/jobapp`: api, worker, db, redis, caddy, tailscale, GitHub runner.
- Intent: `../docs/intent/jetson-job-app.md`, `../docs/intent/agent-features.md`.
- Map and specs: `CAPABILITY-MAP.md`, `SPEC-<module>.md` (platform, identity, profile, jobs, agents, ingestion, mail).
- Tasks: `tasks/<module>/todo.md`; subagent rules: `tasks/AGENT-BRIEF.md`; contribution flow: `CONTRIBUTING.md`.

## Done (merged)

PRs #1 to #26: platform (GitHub flow, CI, self-hosted runner deploy of main, Tailscale Funnel, env check), identity, profile, jobs J1 to J6 (import of Harald's 433 rows, scoring, sweeps, schedule, map), agents A1 (encrypted credentials), A2 (runtime and live stream), A3 (triage and proposals), A4 (chat per job), specs for ingestion and mail.

## Open right now

- **PR #28 `platform/ci-speed`**: CI is green; wall time is 409 s, down from about 862 s. Changes: api tests split into 2 shards with combined coverage of at least 95 percent, vitest split into 2 shards, e2e split into 3 groups with images built by buildx bake using the GitHub Actions cache, and `TEST_FAST_TIMERS` in the e2e worker, which production refuses. It still needs an independent review before merge, because the orchestrator may not merge its own PR. Then confirm the deploy on the Jetson.
- Issues: #17 (mail follow-ups), #19 (cryptography 50 upgrade), #27 (chat delete or finish deadlock; escape `<` in rebuilt prompts).

## Next, in order

1. Review and merge PR #28, then commit this file through a PR.
2. **A5 (real runs on the Jetson).** Harald pastes his `claude setup-token` token in Account > Model access. Then run a first real triage and a first real chat, measure the Jetson's memory during the runs, and tick Checkpoint A in `tasks/agents/todo.md`.
3. Fix #27, then #19.
4. **Ingestion module** (`SPEC-ingestion.md`): the laptop-analysis prompt, uploads of any document, and cited proposals that the user accepts.
5. **Mail module** (`SPEC-mail.md`): add the /privacy and /terms pages, publish Google project `jobapp-gmail-hth` to Production unverified, then build the Gmail connector. Mails are linked to jobs with their history, status updates can be undone, the agent drafts automatically, and nothing is ever sent without the user's click. Then #17.
6. Then the documents, browser and apply modules, plus the 5000-row performance test seeding.

## Rules that must survive

- Ask Harald questions only through AskUserQuestion.
- Orchestrate. Use one Opus subagent per task, each on its own branch with its own PR. Write many tests covering edge cases and real Playwright journeys. A review subagent checks every PR, and the orchestrator merges only when CI is green. Subagents never push to main and never deploy; only main deploys, through the self-hosted runner.
- Never print, grep or log secrets: rtk echoes matching lines. Harald types sudo, passwords and 2FA himself.
- Tests never touch Harald's data. Use e2e-*@example.org accounts and delete them afterwards.
- Never auto-send mail. Never apply to cybersecurity roles. No dashes in outward text. Never regenerate cv.pdf or letter.pdf of applied rows.
- Everything stays free.
