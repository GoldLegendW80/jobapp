# Implementation Plan: platform

Spec: [SPEC-platform.md](../SPEC-platform.md). Map: [CAPABILITY-MAP.md](../CAPABILITY-MAP.md). Tasks: [todo.md](todo.md).

## Overview

Build the skeleton every module runs on: a private monorepo with pre-commit and CI, a FastAPI service with a database round trip, a React shell with Tailwind and shadcn, Caddy in front, Postgres and Redis behind, and a `deploy.sh` that ships it to the Jetson and exposes it through a Cloudflare quick tunnel. No product feature.

## Architecture decisions

- **Build on the Jetson, test on GitHub.** Images are built natively on the Jetson (arm64), so no buildx or QEMU. GitHub Actions runs lint and tests on x86 only; nothing in CI depends on the architecture.
- **Deploy by rsync, not by registry.** No image registry: `deploy.sh` rsyncs the tree and runs `docker compose up -d --build` remotely. Simplest thing that works for one host.
- **Caddy serves the built frontend.** `web/` is a multi-stage Dockerfile whose output (static files) is copied into the Caddy image; no Node process runs in production.
- **Quick tunnel targets Caddy only.** One public entry point; `api`, `db`, `redis` publish no host port.
- **Deploy to the Jetson early (task 4).** The arm64 build and the RAM budget are the riskiest unknowns, so the first deploy happens as soon as `api` and `db` exist, before the frontend.
- **Migrations from day one.** Alembic with an empty baseline, run by `deploy.sh` after every `up`.

## Dependency graph

```
repo bootstrap (1)
  └── api skeleton (2)
        └── compose core + alembic (3)
              ├── first Jetson deploy (4)  ← needs Harald: docker group
              │     └── quick tunnel + url + smoke (8)
              │           └── backup, reboot, RAM (9)
              └── web skeleton (5)
                    └── caddy (6) ──→ (8)
  └── CI workflow (7)  ← needs Harald: gh workflow scope; runs once 2 and 5 exist
```

## Task list

### Phase 1: Backend foundation
- [x] Task 1: Repository bootstrap
- [x] Task 2: API skeleton with health
- [x] Task 3: Compose core and migrations

### Checkpoint A: local backend
- [x] `docker compose up` locally, `/api/health` returns ok with a DB round trip, pytest green

### Phase 2: Fail fast on the Jetson
- [x] Task 4: First Jetson deploy

### Checkpoint B: arm64 confirmed
- [x] api and db build and run on the Jetson; memory measured; review with Harald

### Phase 3: Frontend and edge
- [x] Task 5: Web skeleton
- [x] Task 6: Caddy in front
- [ ] Task 7: CI workflow

### Checkpoint C: full stack locally and in CI
- [ ] `http://localhost` serves the shell and proxies `/api`; CI green on GitHub

### Phase 4: Public and durable
- [ ] Task 8: Quick tunnel, url and smoke
- [ ] Task 9: Backup, restore, reboot and RAM

### Checkpoint D: spec complete
- [ ] All 6 success criteria of SPEC-platform.md verified; review with Harald before `identity`

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| A dependency has no arm64 wheel or image | High | Deploy in task 4 before the stack grows; slim official images only; uv falls back to source builds |
| Builds on the Jetson are slow or run out of RAM | Med | BuildKit cache kept between deploys; build one service at a time if needed; swap is checked in task 4 |
| Quick tunnel URL changes or the tunnel drops (no SLA, rate limited) | Med | Accepted by Harald; `deploy.sh url` prints the current URL; `restart: unless-stopped` on cloudflared |
| Jetson on Wi-Fi, DHCP IP may change | Med | `deploy.sh` uses the `jetson` ssh alias; Harald reserves 192.168.1.47 on the router |
| Two human steps block deploy and CI | Low | Listed as prerequisites in tasks 4 and 7, asked for when reached |
| `rsync` ships secrets or junk | Med | `.rsyncignore` mirrors `.gitignore` except `.env`, which is copied separately with mode 600 |

## Jetson measurements

Task 4, 2026-10-08, `./deploy.sh` from the Mac over Wi-Fi. Jetson Orin Nano: aarch64, 6 cores,
7.4G RAM, Docker 29.8.0 (BuildKit v0.33.0, runc default, nvidia runtime unused), Compose v5.5.1.
Port 80 was free on the Jetson's loopback, so caddy listens on `127.0.0.1:80` (`HTTP_PORT=80`).

Build and deploy times (logs in `tasks/evidence/task4/`):

| Phase | First deploy, empty Jetson (no image, no cache) | Second deploy, nothing changed |
|---|---|---|
| rsync (76 files, 648K) | 0s | 0s |
| build api (`python:3.12.15-slim-trixie`, uv sync from the lockfile) | 34s | 1s |
| build caddy (node 22 alpine, `pnpm install --frozen-lockfile`, `vite build`, caddy 2.11.7) | 52s | 1s |
| `compose up --wait` (pulls postgres and redis the first time) | 30s | 2s |
| migrations (`alembic upgrade head`) | 4s | 4s |
| whole `./deploy.sh` | 122s | 11s |

The second deploy recreated no container (same ids and start times). Both builds include the base
image pulls the first time. The web build on linux/arm64 musl installed the native bindings it
needs: `@rolldown/binding-linux-arm64-musl` (Vite 8 bundles with rolldown, no rollup or esbuild),
`@tailwindcss/oxide-linux-arm64-musl`, `lightningcss-linux-arm64-musl`.

Images: jobapp-api 244MB, jobapp-caddy 63.9MB, postgres:16.15-alpine 288MB, redis:7.4.11-alpine
39.5MB. BuildKit cache after the deploys: 893MB.

Idle RAM per service (`docker stats --no-stream`, stack idle, no user):

| Service | Idle | Limit |
|---|---|---|
| api | 64.6MiB | 512MiB |
| caddy | 20 to 36MiB | 128MiB |
| db | 19.8MiB | 768MiB |
| redis | 9.1MiB | 256MiB |
| total | about 130MiB | 1664MiB |

Host after deploy: 1.4G used of 7.4G, 5.9G available, swap unused. Well under the 1G idle target
of success criterion 5.

Task 8, 2026-10-08, quick tunnel (`cloudflare/cloudflared:2026.9.3`, arm64, 72MB image):
cloudflared idles at about 21MiB of its 128MiB limit, so the idle stack is about 150MiB. It logs its
`*.trycloudflare.com` URL about 5s after a (re)start and turns healthy (`cloudflared tunnel ready`)
within one healthcheck interval. A fresh hostname answered from the Mac on the first request in
every run; the smoke still backs off up to 90s because the zone's negative DNS TTL is 60s. One
QUIC connection (`ha-connections:1`, location cdg12). cloudflared warns that it cannot raise the
UDP receive buffer (`net.core.rmem_max`, needs sudo on the Jetson); harmless at this traffic.
Through Cloudflare, an origin 502 reaches the browser with Cloudflare's own error page and text.

## Open questions

None blocking. Human prerequisites are tracked in tasks 4 and 7.
