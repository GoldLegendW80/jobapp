# Capability Map: Jetson job-search app

Intent: [docs/intent/jetson-job-app.md](../docs/intent/jetson-job-app.md). Approved by Harald on 2026-10-08.

| Module id | Responsibility | Depends on |
|---|---|---|
| platform | Docker Compose, Postgres, Redis, Caddy, Tailscale Funnel, deploy script to the Jetson; GitHub delivery: branches, PRs, Actions CI, review subagent, self-hosted runner deploying main | none |
| identity | Invite-only accounts, sessions, per-user isolation | platform |
| profile | Facts, skills, approved positioning, contact info, search options (titles, locations, contracts, exclusions), all editable in the UI | identity |
| jobs | Postings table, API board sweep (Ashby, Greenhouse, Lever), scoring, statuses, list and filters, multi-select, import of the existing CSV | profile |
| agents | Claude Agent SDK runtime in a worker, task queue, run logs, chat UI primitives; model credentials per user (Harald's Claude subscription, others' Anthropic API keys stored encrypted) | jobs |
| ingestion | Profile from documents: copyable laptop-analysis prompt, paste or upload any document (text, PDF, images), extraction agent proposing per-section additions the user accepts | agents, profile |
| mail | Gmail connector per user (Google OAuth, Production unverified, up to 100 users): mails linked to jobs, history per job, status updates from mail, reply drafts, draft-with-agent chat, send on click only | agents, jobs |
| documents | CV and letter per job: generation, manual editing, PDF render, one-page check | agents, profile, jobs |
| browser | Headed Chromium under Xvfb, Playwright MCP endpoint, noVNC live view in the UI, one persistent browser profile per user | platform |
| apply | Apply button, agent fills and submits the form, status update, run report | agents, documents, browser |
| opportunities | Swipe deck (Like, Pass, undo), first login wizard, freelance and project sources, simple by default; at the end, the rename of jobs to opportunities | jobs, profile, apply, ingestion, documents, browser |

Build order: platform → identity → profile → jobs → agents → ingestion → mail → documents → browser → apply

Updated 2026-10-09 with [docs/intent/agent-features.md](../docs/intent/agent-features.md): GitHub delivery joins platform; `agents` comes earlier and owns model credentials; new modules `ingestion` and `mail`.

## Approved assumptions

1. Code lives in `app/` (its own git repo) on the Mac, deployed to the Jetson with rsync and `docker compose`. `engine/` and `profile/` stay as references.
2. Postgres 16 and Redis in Docker, arm64 images only.
3. Invite-only email and password accounts, session cookies.
4. Playwright Chromium for linux-arm64, one persistent browser profile per user.
5. Claude Agent SDK (Python) in its own worker container, separated from the API by a queue; the Claude login is mounted as a volume.
6. CV and letter PDFs rendered from the HTML templates of `engine/assemble.py` and `engine/letter.py`.

## Specs

Each module has its spec next to this file: `SPEC-<module id>.md`.
