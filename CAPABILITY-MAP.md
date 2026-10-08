# Capability Map: Jetson job-search app

Intent: [docs/intent/jetson-job-app.md](../docs/intent/jetson-job-app.md). Approved by Harald on 2026-10-08.

| Module id | Responsibility | Depends on |
|---|---|---|
| platform | Docker Compose, Postgres, Redis, Caddy, cloudflared, deploy script to the Jetson | none |
| identity | Invite-only accounts, sessions, per-user isolation | platform |
| profile | Facts, skills, approved positioning, contact info, search options (titles, locations, contracts, exclusions), all editable in the UI | identity |
| jobs | Postings table, API board sweep (Ashby, Greenhouse, Lever), scoring, statuses, list and filters, multi-select, import of the existing CSV | profile |
| documents | CV and letter per job: generation, manual editing, PDF render, one-page check | profile, jobs |
| browser | Headed Chromium under Xvfb, Playwright MCP endpoint, noVNC live view in the UI, one persistent browser profile per user | platform |
| agents | Claude Agent SDK runtime, task queue, specialized subagents (search, scoring, cv, letter, apply), chat for editing documents, run logs | jobs, documents, browser |
| apply | Apply button, agent fills and submits the form, status update, run report | agents |

Build order: platform → identity → profile → jobs → documents → browser → agents → apply

## Approved assumptions

1. Code lives in `app/` (its own git repo) on the Mac, deployed to the Jetson with rsync and `docker compose`. `engine/` and `profile/` stay as references.
2. Postgres 16 and Redis in Docker, arm64 images only.
3. Invite-only email and password accounts, session cookies.
4. Playwright Chromium for linux-arm64, one persistent browser profile per user.
5. Claude Agent SDK (Python) in its own worker container, separated from the API by a queue; the Claude login is mounted as a volume.
6. CV and letter PDFs rendered from the HTML templates of `engine/assemble.py` and `engine/letter.py`.

## Specs

Each module has its spec next to this file: `SPEC-<module id>.md`.
