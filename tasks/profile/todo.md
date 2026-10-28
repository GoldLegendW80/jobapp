# Tasks: profile

Spec: [SPEC-profile.md](../../SPEC-profile.md). Depends on identity (I1 to I3 at least).

## Architecture decisions
- One `profiles` row per user, sections in JSONB validated by Pydantic v2 models; `profile_revisions` append-only.
- Optimistic concurrency with `base_revision`; 409 returns current data.
- Photo in Postgres (bytea), so backups cover it; type sniffed from magic bytes, re-encoded server-side with Pillow to strip metadata.
- Importer is a pure function `files -> export JSON`, CLI posts it; golden fixture from the real `../profile` and `../engine` files.
- The board check calls the ATS public posting API from the backend with a 5s timeout (httpx), never from the browser. (Deleted 2026-10-28 with the boards, owner: `POST /api/profile/boards/check` is gone.)

## P1: Profile schema, section models, read and save with revisions (done 2026-10-08, 737 api tests)

**Acceptance:** migration 0003; GET /api/profile with completeness; PUT /api/profile/{section} with 422, 409; revisions list and get; isolation tests.
**Verify:** pytest; curl through Caddy.
**Files:** `api/app/profile/{models,schemas,service,router}.py`, `api/alembic/versions/0003_profile.py`, tests. **Scope:** M

## P2: Export, import and Harald's importer (done 2026-10-08, 910 api tests; deploy.sh wrapper still to add)

**Acceptance:** export and import endpoints with identical round trip; `importer.py` converts facts.md, skills.json, boards.json and assemble.py constants; golden test; CLI and `./deploy.sh profile import <email>`.
**Verify:** pytest golden; dry run against the real files.
**Files:** `api/app/profile/{importer,cli}.py`, `deploy.sh`, tests. **Scope:** M

## P3: Photo upload (done 2026-10-09, 1112 api tests; raw body PUT, 415/413/422, ETag 304)

**Acceptance:** PUT, GET, DELETE photo; PNG and JPEG only by magic bytes; 2 MB limit; re-encoded; isolation.
**Verify:** pytest with real image fixtures and hostile files.
**Files:** `api/app/profile/{router,service}.py`, tests. **Scope:** S

## P4: Profile page shell and form sections (identity, experience, answers) (done 2026-10-09)

**Acceptance:** /profile with tabs and completeness bar; three form tabs with validation, save, 409 handling with reload, unsaved-change prompt; photo upload with square crop.
**Verify:** Vitest; Playwright locally; screenshots.
**Files:** `web/src/features/profile/*`. **Scope:** M

## P5: Editors for facts, skills and search, with the board check (done 2026-10-09)
Note, 2026-10-28: the board table and the board check (`api/app/profile/boards.py`) were deleted (owner, "Delete it"); a stored `search.boards` is ignored on read and dropped on the next save. The importer reads only the regexes of `boards.json`.

**Acceptance:** Markdown editor with preview and blocks; skills category and term chips; search terms chips, contract toggles, regex advanced mode with server error shown; board table with enable switches and add company verified through `POST /api/profile/boards/check`.
**Verify:** pytest for the board check (mocked ATS: exists, 404, timeout); Vitest; Playwright.
**Files:** `api/app/profile/boards.py`, `web/src/features/profile/*`. **Scope:** M

## P6: History, export and import UI, Jetson journey, Harald's real import

**Acceptance:** history drawer view and restore; export download and import upload; Playwright journey from empty to 100% on the Jetson through the public URL; screenshots of every tab light and dark, desktop and 375px; Harald's profile imported on the Jetson for his account once it exists (orchestrator confirms the email).
**Verify:** Playwright logs and screenshots; `./deploy.sh smoke`.
**Files:** `web/src/features/profile/*`, `web/e2e/profile.spec.ts`. **Scope:** M

## Checkpoint P
- [ ] All 5 success criteria of SPEC-profile.md verified
