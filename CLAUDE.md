# jobapp

A self-hosted job search app for its owner and a few invited friends: it finds postings, scores
them against a profile, writes a tailored CV and cover letter per posting, and applies in one
click: an agent fills the application form in a live browser and submits only after the person
approves. It runs as a Docker Compose stack on a Jetson Orin Nano (arm64) at home, reached through
Tailscale Funnel. The repository is PUBLIC.

Start here, in this order: this file, [docs/STATUS.md](docs/STATUS.md) (what is live, open,
next), [docs/decisions.md](docs/decisions.md) (what the owner decided), then the doc of your role
below.

## Map

| Path | What |
|---|---|
| `api/app/<module>/` | FastAPI + SQLAlchemy + arq worker, one folder per module: identity, profile, jobs, agents, ingestion, mail, documents, browser, apply |
| `api/tests/<module>/` | pytest, against real Postgres and Redis (`ops/agent-test.sh` starts throwaway ones) |
| `api/alembic/versions/` | migrations, expand first, contract later (CONTRIBUTING.md) |
| `web/src/features/<feature>/` | React + Tailwind + shadcn + TanStack Query, Vitest next to the code |
| `web/e2e/` | Playwright journeys; a group per `<group>-all.config.ts` |
| `browser/`, `renderer/`, `caddy/`, `tailscale/` | the browser service (Chromium, browserd), the PDF renderer, the proxy, the public URL |
| `compose.yml`, `compose.mac.yml`, `compose.dev.yml` | the stack; add `compose.mac.yml` on an Apple silicon Mac (README, Apple silicon) |
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
4. **Nothing is submitted without the person's approval.** No model clicks Submit; the one click
   path is the apply gate.
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

Owner decisions per module are in [docs/decisions.md](docs/decisions.md): never decide them again.

## Daily commands

```bash
ops/dev-setup.sh                                   # a new laptop: what is missing and how to fix it (read only)
ops/agent-test.sh --name <task> tests/<module> -m "not docker" -n 2   # pytest beside the gate (throwaway db and redis)
ops/agent-test.sh --name <task> --playwright <args>                   # a journey, one worker, against your own stack
(cd api && uv run ruff check app tests && uv run ruff format --check app tests)
pnpm --dir web run typecheck && pnpm --dir web run lint && pnpm --dir web exec vitest run src/features/<f>
python3 .github/scripts/ci_plan_test.py && python3 .github/scripts/e2e_groups.py   # always, fast
pre-commit run --files <changed files>              # includes the secret scan (gitleaks)

ops/local-check.sh --merge <pr> --merge <pr>        # the gate on a merged batch (the orchestrator, quiet machine)
ops/ship.sh --plan --approved                       # what would ship, changes nothing
ops/ship.sh <pr> <pr>                               # gate, merge, deploy the checked tree, check the Jetson: SHIP: OK
ops/deploy-checked.sh                               # deploy the current checked main only
ops/remote.sh status                                # the Jetson's figures through GitHub Actions (ops.yml), no ssh; --list for the actions
ops/card.sh <issue or pr url> <todo|doing|done> <module> <now|next|later>   # the board
```

`uv` needs `--native-tls` on a network that intercepts TLS. Deploy from any machine, no ssh
(README, "Deploy from any machine"): the gate runs on the laptop that ships and writes its PASS
record in that checkout; `ops/ship.sh` (or `ops/deploy-checked.sh`) dispatches
`.github/workflows/deploy.yml` on the Jetson's runner for a tree that gate passed and reads the
run's `JOBAPP-VERIFY` figures; `ops/remote.sh <action>` dispatches `.github/workflows/ops.yml`
(status, backup, apply pause and resume, ...). Dispatching needs write access and the gh scope
`workflow`. `./deploy.sh` and ssh stay for the owner: the first deploy, a new `.env`, sudo steps.

## How work flows

There is no CI per pull request. One task = one issue = one branch `<module>/<task-id>-<slug>` =
one pull request with `Closes #<n>` (and `Depends on #<n>` on line 1 when needed). A fresh
reviewer posts `REVIEW VERDICT: APPROVE|REQUEST CHANGES` with a `Head: <sha>` line as a review
comment; a fresh fix agent addresses blockers; a re-check confirms. `ops/ship.sh` gates the
approved batch, merges and deploys.

- [docs/agents/orchestration.md](docs/agents/orchestration.md): the orchestrator: lifecycle,
  model policy, parallelism, brief templates, lessons learned.
- [docs/agents/implementer.md](docs/agents/implementer.md): rules for whoever builds a task.
- [docs/agents/reviewer.md](docs/agents/reviewer.md): rules for whoever reviews one.
- [docs/agents/review-checklist.md](docs/agents/review-checklist.md): the self review list,
  mined from 130 reviews.
- [CONTRIBUTING.md](CONTRIBUTING.md): the merge rule, the gate, migrations.
- [README.md](README.md): setup ("Develop on a new machine"), operations, every runbook.

Subagents: `.claude/agents/implementer.md` and `.claude/agents/reviewer.md`. Slash commands:
`/build-task`, `/review-pr`, `/fix-pr`, `/ship`, `/status` (`.claude/commands/`).
