# jobapp

A self-hosted job search app for its owner and a few invited friends: it finds postings, scores
them against a profile, writes a tailored CV and cover letter per posting, and applies in one
click: an agent fills the application form in a live browser and submits only after the person
approves. It runs as a Docker Compose stack on a Jetson Orin Nano (arm64) at home, reached through
Tailscale Funnel. The repository is PUBLIC.

Start here, in this order: this file, then the doc of your role (below). The orchestrator also
reads [docs/STATUS.md](docs/STATUS.md) (what is live, open, next). Owner decisions
([docs/decisions.md](docs/decisions.md)) are never decided again: read "Standing rules" and the
section of your module (`grep -n '^## '`), never the whole file.

## Map

| Path | What |
|---|---|
| `api/app/<module>/` | FastAPI + SQLAlchemy + arq worker, one folder per module: identity, profile, jobs, agents, ingestion, mail, documents, browser, apply |
| `api/tests/<module>/` | pytest, against real Postgres and Redis (`ops/agent-test.sh` starts throwaway ones) |
| `api/alembic/versions/` | migrations, expand first, contract later (CONTRIBUTING.md) |
| `web/src/features/<feature>/` | React + Tailwind + shadcn + TanStack Query, Vitest next to the code |
| `web/e2e/` | Playwright journeys; a group per `<group>-all.config.ts` |
| `browser/`, `renderer/`, `caddy/`, `tailscale/` | the browser service (Chromium, browserd), the PDF renderer, the proxy, the public URL |
| `compose.yml`, `compose.mac.yml`, `compose.dev.yml` | the stack; add `compose.mac.yml` on an Apple silicon Mac (docs/runbooks/development-on-the-mac.md, Apple silicon) |
| `ops/` | the gate, ship, deploy and helper scripts (below) |
| `deploy.sh`, `.github/workflows/deploy.yml`, `ops.yml` | deploy to and operate the Jetson (`ops/deploy-checked.sh` and `ops/remote.sh` dispatch the workflows) |
| `tests/*.sh` | shell tests of the scripts, each run by a step of `.github/workflows/ci.yml` |
| `CAPABILITY-MAP.md`, `SPEC-<module>.md` | the modules and their specs |
| `tasks/<module>/todo.md` | the plan of each module: decisions, one section per task id (D12, A5, ...) |
| `docs/agents/` | rules and workflow for Claude Code agents (below) |
| `.claude/` | versioned Claude Code config: `agents/`, `commands/`, `settings.json` |

## Hard rules

1. **No dashes as separators** in any text a person reads (UI, e-mails, generated CVs and
   letters, error messages, pull request bodies): no em dash, no en dash, no " - ". Use commas,
   colons or a new sentence. Hyphenated words are fine.
2. **Never apply to a cybersecurity role**, and build no cybersecurity specific feature.
3. **Never send mail by itself.** The person clicks Send.
4. **Nothing is submitted without the person's approval.** In Full auto, the Like the person
   gives while Full auto is on is that approval (owner, 2026-10-10). No model ever clicks Submit;
   the one click path is the apply gate.
5. **Tests use only `e2e-*@example.org` accounts**, never real user data, never a real Google
   host, never a real job site (fakes: `tests/gmail-stub.compose.yml`,
   `tests/ats-forms-stub.compose.yml`).
6. **Never read `.env` files** (`.env`, `.env.jetson`, `.env.local`, `.env.*.local`, `.env.production`, `.env.prod`) or production credentials;
   never print, grep or log a secret. `.env.example` is the tracked template: read and edit it freely.
7. **The owner types sudo, passwords and 2FA himself.** Write the exact command and stop.
8. **The repository is public:** never commit personal data, e-mail addresses, phone numbers,
   tokens, secrets or private URLs. Fixtures and evidence use invented data.
9. **Git:** work in your own worktree under `.claude/worktrees/<name>`; never edit or switch the
   main checkout; never push to `main`, never force push, never bypass hooks (`--no-verify`).
10. **Machine:** no background processes or loops that outlive a command; no `docker * prune`
    beyond your own names; remove what you create.
11. **Everything stays free:** no paid service.

## Daily commands

```bash
ops/agent-test.sh --name <task> tests/<module> -m "not docker" -n 2   # pytest, throwaway db and redis
ops/agent-test.sh --name <task> --playwright <args>                   # a journey, your own stack
(cd api && uv run ruff check app tests && uv run ruff format --check app tests)
pnpm --dir web run typecheck && pnpm --dir web run lint && pnpm --dir web exec vitest run src/features/<f>
python3 .github/scripts/ci_plan_test.py && python3 .github/scripts/e2e_groups.py
pre-commit run --files <changed files>              # with the secret scan
ops/card.sh <issue url> doing <module> now           # the card of a task you start
```

`uv` needs `--native-tls` on a network that intercepts TLS. To gate, ship, deploy, operate the
Jetson or set up a laptop, or when your diff touches `ops/`, `deploy.sh` or the workflows: read
[docs/agents/when/gate-and-ship.md](docs/agents/when/gate-and-ship.md) first.

## How work flows

There is no CI per pull request. One task = one issue = one branch `<module>/<task-id>-<slug>` =
one pull request with `Closes #<n>` (and `Depends on #<n>` on line 1 when needed). A fresh
reviewer posts `REVIEW VERDICT: APPROVE|REQUEST CHANGES` with a `Head: <sha>` line as a review
comment; a fresh fix agent addresses blockers; a re-check confirms. `ops/ship.sh` gates the
approved batch, merges and deploys.

Docs: `docs/agents/` (`orchestration.md` for the orchestrator, with the subagents and slash
commands; `implementer.md`, `reviewer.md`, `shared-rules.md` and `review-checklist.md`, mined from
512 reviews of 242 pull requests, for the agents; `when/`, read only when a trigger line says so;
`lessons/`), [CONTRIBUTING.md](CONTRIBUTING.md) (the merge rule, the gate, migrations),
[README.md](README.md) (what the app is, the runbooks of `docs/runbooks/`).
