# jobapp

A self-hosted job-search app for Harald and a few invited friends. It finds postings, scores them against a profile, writes a tailored CV and letter per job, and lets an agent fill application forms in a live browser. It runs as a Docker Compose stack on a Jetson Orin Nano at home and is reached at a stable public URL through Tailscale Funnel.

## Contents

The runbooks are in `docs/runbooks/`, one file per topic. Pick the file by the last column.

| File | What you find | Read it when |
|---|---|---|
| [develop-new-machine.md](docs/runbooks/develop-new-machine.md) | Tools, repository docs, pre-commit and the TLS inspecting proxy bundle | Setting up a laptop to build the app |
| [deploy-from-any-machine.md](docs/runbooks/deploy-from-any-machine.md) | Deploying and operating the Jetson through GitHub Actions, no ssh | Before any deploy or remote operation |
| [development-on-the-mac.md](docs/runbooks/development-on-the-mac.md) | Running the stack on an Apple silicon Mac, `OPENSSL_armcap=0` | Running the app locally on the Mac |
| [deploy-to-the-jetson.md](docs/runbooks/deploy-to-the-jetson.md) | `./deploy.sh` on the Jetson and its commands | Deploying or operating the Jetson by hand |
| [continuous-delivery.md](docs/runbooks/continuous-delivery.md) | The batch check, deploy on demand, migrations, rollback, Docker socket security | Changing the gate, the workflows or a migration |
| [ci-run.md](docs/runbooks/ci-run.md) | The three jobs of a `ci.yml` run, timings, the plan, the secret scan | Changing `ci.yml` or `ci_plan.py`, or reading a CI run |
| [ci-runners-mac.md](docs/runbooks/ci-runners-mac.md) | The optional CI runners on the Mac (`ops/ci-runners.sh`) | Starting, updating or debugging the runners |
| [public-url.md](docs/runbooks/public-url.md) | Tailscale Funnel, headers, request limits, the quick tunnel fallback | Anything about the public URL, Caddy or request limits |
| [accounts.md](docs/runbooks/accounts.md) | First admin, invites, managing accounts, sign in throttling | Creating, inviting or removing a user |
| [profile-and-jobs-data.md](docs/runbooks/profile-and-jobs-data.md) | Profile export and import, board check, jobs import and files | Moving a profile or the owner's job list in or out |
| [sweeps-and-worker.md](docs/runbooks/sweeps-and-worker.md) | Find jobs sweeps and the worker | Working on sweeps or the worker |
| [agent-runs.md](docs/runbooks/agent-runs.md) | Claude agents in the worker, image and credential | Working on agent runs |
| [profile-documents.md](docs/runbooks/profile-documents.md) | Fill my profile with an agent, the documents step | Working on the profile fill page |
| [run-estimate-consent.md](docs/runbooks/run-estimate-consent.md) | The estimate shown before a run reads documents, and the consent | Working on the estimate or consent |
| [parser-service.md](docs/runbooks/parser-service.md) | The sandboxed parser of uploaded documents | Touching document parsing or its sandbox |
| [backups-and-restores.md](docs/runbooks/backups-and-restores.md) | Dumps, schedule, 7 day retention, restore, guards | Backing up, restoring or changing retention |
| [reboot-recovery.md](docs/runbooks/reboot-recovery.md) | What comes back after a reboot or crash | After a power cut or a reboot |
| [updating-pypdfium2.md](docs/runbooks/updating-pypdfium2.md) | Taking a PDFium security release | When pypdfium2 has a security release |

## Documents

- [CLAUDE.md](CLAUDE.md): the entry point for Claude Code and for anyone picking up the build; it links [docs/STATUS.md](docs/STATUS.md), [docs/decisions.md](docs/decisions.md) and the agents' rules in `docs/agents/`.
- [CAPABILITY-MAP.md](CAPABILITY-MAP.md): the modules, their dependencies and the build order.
- [SPEC-platform.md](SPEC-platform.md): the platform module (Compose stack, deploy, public URL through Tailscale Funnel), built first.
- [CONTRIBUTING.md](CONTRIBUTING.md): branches, pull requests, review, the batch check on the Mac and the deploy of a checked tree, started by hand.
- [tasks/plan.md](tasks/plan.md) and [tasks/todo.md](tasks/todo.md): the implementation plan and task list of the current module.

## Setup

```bash
cp .env.example .env           # then fill in real values; .env is never committed
pre-commit install             # ruff, prettier, eslint, gitleaks and file fixers on every commit
```

For a full development setup see [develop-new-machine.md](docs/runbooks/develop-new-machine.md).
