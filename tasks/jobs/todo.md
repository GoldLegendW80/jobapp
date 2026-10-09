# Tasks: jobs

Spec: [SPEC-jobs.md](../../SPEC-jobs.md). Depends on profile (matching rules in `api/app/profile/matching.py`).

## Architecture decisions
- Rows, events and files in Postgres; files as bytea (20 MB total for Harald today), so backups cover them.
- Scoring is a pure function `(posting_text, skills_section) -> (fit, competencies, gaps)`, golden-tested against `engine/jobs.py`.
- Sweeps run in a new `worker` container fed by Redis (queue library chosen in J5 and recorded here).
- List endpoint returns counts and histogram for the filtered set in the same response, so the dashboard needs one request.
- Filters live in the URL query so views are shareable and survive reloads.

## J1: Jobs schema and scoring port (done 2026-10-09: golden test 395/395 identical to the engine)
**Acceptance:** migration 0004 (jobs, job_events, job_files, sweeps, enums, indexes for list filters and full text); `scoring.py` port; golden test equal to the engine on every scorable CSV row; isolation-ready service skeleton.
**Files:** `api/app/jobs/{models,scoring,service}.py`, `api/alembic/versions/0004_jobs.py`, tests. **Scope:** M

## J2: Import of Harald's data, list, detail and files API (done 2026-10-09; Harald's 433 rows and 330 files imported on the Jetson)
**Acceptance:** importer maps every CSV column and file; POST /api/jobs/import idempotent; `./deploy.sh jobs import <email>`; GET list with filters, counts, histogram, cursor pagination; GET detail; GET file download; isolation.
**Files:** `api/app/jobs/{importer,router,schemas}.py`, `deploy.sh`, tests. **Scope:** M

## J3: Jobs dashboard and detail pages
**Acceptance:** `/jobs` home with KPIs, histogram, filter chips in the URL, virtualised table and mobile cards; `/jobs/:id` with posting, fit, files with PDF preview, events.
**Files:** `web/src/features/jobs/*`, routes. **Scope:** M

## J4: Editing, bulk actions, add by hand, rescore
**Acceptance:** PATCH, bulk, POST add (409 duplicate), rescore with manual override kept; UI for each with optimistic updates and undo for bulk status.
**Files:** `api/app/jobs/*`, `web/src/features/jobs/*`. **Scope:** M

## J5: Worker, sweep and nightly schedule
**Acceptance:** `worker` service in compose (mem 1536m reserved), queue on Redis; ATS fetch and parse ported with recorded fixtures; sweep against a fake ATS server with every failure mode; one sweep per user; progress and summary in the UI; nightly run; never_apply rules.
**Files:** `api/app/{worker.py,jobs/sweep.py,jobs/ats.py}`, `compose.yml`, `deploy.sh`, `web/src/features/jobs/*`. **Scope:** L, split if needed

## J6: Map view
**Acceptance:** `/jobs/map` with pins per city or country from the ported gazetteer, unplaced list, filters shared with the list.
**Files:** `api/app/jobs/{places.json,world.json}`, `web/src/features/jobs/map*`. **Scope:** M

## J7: Harald's real data and the Jetson journey
**Acceptance:** Harald's data imported on the Jetson for hthautin@gmail.com, counts equal to the CSV; one real sweep for an e2e user; Playwright journey through the public URL; screenshots.
**Scope:** S

## Checkpoint J
- [ ] All 5 success criteria of SPEC-jobs.md verified
