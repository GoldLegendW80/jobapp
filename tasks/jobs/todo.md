# Tasks: jobs

Spec: [SPEC-jobs.md](../../SPEC-jobs.md). Depends on profile (matching rules in `api/app/profile/matching.py`).

## Architecture decisions
- Rows, events and files in Postgres; files as bytea (20 MB total for Harald today), so backups cover them.
- Scoring is a pure function `(posting_text, skills_section) -> (fit, competencies, gaps)`, golden-tested against `engine/jobs.py`.
- Sweeps run in a new `worker` container fed by Redis (queue library chosen in J5 and recorded here).
- Queue library (J5, 2026-10-09): **arq 0.28.0** (redis-py 5.3, Redis 7.4). Why: asyncio native, so
  the sweep's concurrent httpx fetches and, later, the Claude Agent SDK (async) run in the job's own
  event loop with no thread or fork per job; jobs are named functions and cron jobs (with a time
  zone) are built in, so the schedule tick needs no extra scheduler; one small pure
  Python package on top of redis-py (arm64 needs nothing compiled), one process for every queue
  (about 90 MB idle), where RQ forks a work horse per job and needs rq-scheduler for cron, and
  Celery or Dramatiq bring brokers and processes this budget does not need. Shape: one arq Worker
  per queue in one process (`app/worker.py`, `QueueSpec`), each with its own functions, concurrency
  and timeout; agents add their queue as one more spec. Messages are JSON, never pickle; job ids
  (`sweep:<id>`, `schedule:<user>:<slot>`) make enqueueing idempotent and the cron job ids make
  each tick fire once across workers.
- Live progress (J5): server-sent events through one helper, `app/sse.py` (state read every 0.5 s,
  an event on change, heartbeat comments, `Cache-Control: no-transform` so Caddy neither compresses
  nor buffers), with polling of the same JSON as the fallback in the browser. The agents module
  streams runs through the same helper.
- Automatic sweeps are per user and OFF by default (Harald, 2026-10-09): `search.sweep_schedule`
  = `{enabled: false, days: ["monday" ... "sunday"] (lowercase English names, at least one),
  times: ["06:17"] ("HH:MM", 1 to 4), timezone: "Europe/Paris" (IANA, validated)}`; a section
  without it reads as off (the draft's `sweep_nightly` key is dropped on read). A worker cron
  ticks every 5 minutes and starts every (user, slot) whose instant fell in the 15 minutes before
  the tick (`app/jobs/schedule.py`): a slot fires once (advisory lock on user and slot, the slot
  key recorded in the sweep's stats, queue job id `schedule:<user>:<slot>`), a user already
  sweeping is retried by the next ticks of the window, a missed tick is caught by the next one.
  Daylight saving time: a local time skipped in spring fires when the clock jumps over it, a
  repeated autumn time fires at its first occurrence. Set from "Schedule" next to Find jobs (PUT
  /api/profile/search with the base revision, a 409 merged once) and from the profile Search tab
  (profile P6, same field and shape).
- List endpoint returns counts and histogram for the filtered set in the same response, so the dashboard needs one request.
- Filters live in the URL query so views are shareable and survive reloads.

## J1: Jobs schema and scoring port (done 2026-10-09: golden test 395/395 identical to the engine)
**Acceptance:** migration 0004 (jobs, job_events, job_files, sweeps, enums, indexes for list filters and full text); `scoring.py` port; golden test equal to the engine on every scorable CSV row; isolation-ready service skeleton.
**Files:** `api/app/jobs/{models,scoring,service}.py`, `api/alembic/versions/0004_jobs.py`, tests. **Scope:** M

## J2: Import of Harald's data, list, detail and files API (done 2026-10-09; Harald's 433 rows and 330 files imported on the Jetson)
**Acceptance:** importer maps every CSV column and file; POST /api/jobs/import idempotent; `./deploy.sh jobs import <email>`; GET list with filters, counts, histogram, cursor pagination; GET detail; GET file download; isolation.
**Files:** `api/app/jobs/{importer,router,schemas}.py`, `deploy.sh`, tests. **Scope:** M

## J3: Jobs dashboard and detail pages (done 2026-10-09; PDF preview uses a same-origin blob, Caddy keeps frame-ancestors none)
**Acceptance:** `/jobs` home with KPIs, histogram, filter chips in the URL, virtualised table and mobile cards; `/jobs/:id` with posting, fit, files with PDF preview, events.
**Files:** `web/src/features/jobs/*`, routes. **Scope:** M

## J4: Editing, bulk actions, add by hand, rescore (done 2026-10-09)
**Acceptance:** PATCH, bulk, POST add (409 duplicate), rescore with manual override kept; UI for each with optimistic updates and undo for bulk status.
**Files:** `api/app/jobs/*`, `web/src/features/jobs/*`. **Scope:** M

## J5: Worker, sweep and nightly schedule
**Acceptance:** `worker` service in compose (mem 1536m reserved), queue on Redis; ATS fetch and parse ported with recorded fixtures; sweep against a fake ATS server with every failure mode; one sweep per user; progress and summary in the UI; nightly run; never_apply rules.
**Files:** `api/app/{worker.py,jobs/sweep.py,jobs/ats.py}`, `compose.yml`, `deploy.sh`, `web/src/features/jobs/*`. **Scope:** L, split if needed

## J6: Map view (done 2026-10-09, PR #8)
**Acceptance:** `/jobs/map` with pins per city or country from the ported gazetteer, unplaced list, filters shared with the list.
**Files:** `api/app/jobs/{places.json,world.json}`, `web/src/features/jobs/map*`. **Scope:** M

## J7: Harald's real data and the Jetson journey
**Acceptance:** Harald's data imported on the Jetson for his own account, counts equal to the CSV; one real sweep for an e2e user; Playwright journey through the public URL; screenshots.
**Scope:** S

State on 2026-10-21, one line per step (the triage of #42, then the pull request of the view journey):
- Harald's data on the Jetson, counts equal to the CSV: done 2026-10-09 (J2). `tasks/evidence/jobs-j4-jetson/run.log` reads jobs=433: found 158, shortlisted 146, cv_ready 3, staged 29, applied 29, interview 2, closed 6, skipped 60.
- The import is idempotent: done in tests, `api/tests/jobs/test_import.py`, `test_importer.py`, `test_importer_real.py`.
- One real sweep for an e2e user: done 2026-10-09 (PR #6). `tasks/evidence/jobs-j5-jetson/run.log`: two real boards, 138 new, 11 skipped by rule, PASS.
- The journey through the public URL with screenshots, for editing (J4) and the sweep (J5): done 2026-10-09 by `tests/e2e_jobs_jetson.sh` and `tests/e2e_jobs_sweep_jetson.sh`, PASS, evidence in `tasks/evidence/jobs-j4-jetson/` (36 screenshots) and `tasks/evidence/jobs-j5-jetson/`.
- The same for the list, the detail with its PDF preview and the map: ready, not run on the Jetson yet. `tests/e2e_jobs_jetson.sh view` runs `web/e2e/jobs.spec.ts` in its E2E_SYNTHETIC mode (433 made-up rows with the CSV counts, posting texts and files, loaded over HTTP) and `web/e2e/jobs-map.spec.ts`, evidence in `tasks/evidence/jobs-j7-jetson/`. Both passed on a local scratch stack on 2026-10-21, 12 tests of 12. Left: the owner's run below.
- The Jetson journeys again on today's `main`: not done. The scripts reach the Jetson over ssh, so they run on the owner's Mac.

## Checkpoint J
- [ ] All 5 success criteria of SPEC-jobs.md verified

One line per criterion, 2026-10-21:
1. Harald's 433 rows, texts and files on the Jetson, counts equal to the CSV, an idempotent import: done 2026-10-09 (the J7 lines above). The owner's runs below print the live counts again.
2. The scoring port matches the engine on every scorable row: done, `api/tests/jobs/test_scoring_golden.py` (395 of 395, J1).
3. Find jobs sweeps the boards from the Jetson and adds only new matching postings, scored: done, `tasks/evidence/jobs-j5-jetson/`, `api/tests/jobs/test_sweep_api.py`, `test_sweep_parity.py`. A schedule, once on, runs by itself: proven locally (`api/tests/jobs/test_schedule.py`, `test_worker.py`, the journey "Schedule: off by default, turned on, fires at its time, turned off" of `web/e2e/jobs-sweep.spec.ts`), not yet checked on the Jetson. Left: the owner's schedule check below.
4. Filters, bulk actions, detail and map on desktop and phone: done locally with 375 px screenshots (`tasks/evidence/jobs-j3/`, `jobs-j4/`, `jobs-j6/`), and on the Jetson for editing and the sweep (`jobs-j4-jetson/`, `jobs-j5-jetson/`). Left: the view run below.
5. No user can see another user's jobs, files or sweeps: done, `api/tests/jobs/test_api.py::test_isolation`, `api/tests/jobs/test_sweep_api.py`.

Left, all of it the owner's. On his Mac, in the app's checkout, where `ssh jetson` works:

```bash
OWNER=<the owner's account e-mail> tests/e2e_jobs_jetson.sh
OWNER=<the owner's account e-mail> tests/e2e_jobs_jetson.sh view
```

Each prints his jobs before and after, "identical before and after" and PASS, and removes its e2e accounts. Then, in his browser, signed in as himself: Jobs, Schedule, turn it on with today's weekday and a time 5 to 10 minutes ahead, save, wait for that time plus up to 5 minutes, and open Sweep history: the new sweep reads Automatic. Keep it on as the nightly run or turn it off. When all four passed: tick the box above, give J7 its done date and update the jobs row of `docs/STATUS.md`.


## Interactive job search (#626, owner approved 2026-10-24)

Contract and dependency order: [interactive search plan](../jobs/interactive-search-626.md). Tasks are tracked in GitHub; existing module tasks remain intact.

### J9: save chosen websites and freeze search criteria at Start

Task [#629](https://github.com/GoldLegendW80/jobapp/issues/629). Acceptance, owned files and verification are in that issue and the linked plan. Status: [draft #639](https://github.com/GoldLegendW80/jobapp/pull/639), not merged or deployed. J16 compatibility must precede merging the status expansion.

### J10: run selected websites through the interactive finder

Task [#630](https://github.com/GoldLegendW80/jobapp/issues/630). Acceptance, owned files and verification are in that issue and the linked plan. Status: [draft #652](https://github.com/GoldLegendW80/jobapp/pull/652), not merged or deployed. Truthful incremental persistence remains J11.

### J11: save verified search findings incrementally

Task [#632](https://github.com/GoldLegendW80/jobapp/issues/632). Acceptance, ownership and verification are in that issue and the linked plan. The observed-finding foundation and storage lifecycle follow-up [#655](https://github.com/GoldLegendW80/jobapp/issues/655) are separate reviewable slices, preserving all tests. Status: implementation and verification in progress; full #632 remains open until both slices compose and integration passes.

### J12: cancel searches and restore persisted progress

Task [#633](https://github.com/GoldLegendW80/jobapp/issues/633). Acceptance, owned files and verification are in that issue and the linked plan. Status: open, implementation pending.

### J13: choose websites and criteria from Find jobs

Task [#636](https://github.com/GoldLegendW80/jobapp/issues/636). The core setup and explicit permission follow-up [#651](https://github.com/GoldLegendW80/jobapp/issues/651) form separate reviewable slices. Acceptance, ownership transfer and verification are in the linked plan. Status: core [draft #654](https://github.com/GoldLegendW80/jobapp/pull/654) is open; #651 and integration remain pending for full J13 acceptance.

### J14: show live browsing progress and partial search results

Task [#637](https://github.com/GoldLegendW80/jobapp/issues/637). Acceptance, owned files and verification are in that issue and the linked plan. Status: open, implementation pending.

### J15: verify the complete interactive search journey on fake sites

Task [#638](https://github.com/GoldLegendW80/jobapp/issues/638). Acceptance, owned files and verification are in that issue and the linked plan. Status: open, implementation pending.

### J16: keep source modes and pending outcomes truthful in the current summary

Task [#641](https://github.com/GoldLegendW80/jobapp/issues/641). Small client compatibility follow up for J9's expanded source statuses. Acceptance and owned files are in that issue and the linked plan. Status: implementation and checks in progress; no deployment claimed.

### J17: a ticked website is its permission, and Websites only

Owner decision of 2026-10-27 ([decisions](../../docs/decisions.md)), replacing #651's separate permission cards. Find jobs shows each job website's risk sentence beside its box; Save records the consent of every ticked website through the platform's consent route on the reviewed revision and takes back the one of a website unticked; "Websites only" searches the ticked websites alone (`POST /api/jobs/sweeps {websites_only}`). The search's permission checks, pause, caps and pacing are unchanged. Status: built on the combined branch, journeys run by the orchestrator. The "Websites only" part is replaced on 2026-10-28: every search runs the websites alone, the setup shows no board, feed, reader or regular expression switch (branch `jobs/websites-only-setup`).

### J18: one agent per job website, with all the saved criteria

Owner decision of 2026-10-27 ([decisions](../../docs/decisions.md)), replacing the 2026-10-26 "one search per combination". One lease and one finder run a website a sweep, given every saved criterion as context; browserd's frozen scope lets it type only one saved title in a search box or one saved place in the place box, case and accents aside (or nothing: an empty box is always allowed, owner 2026-10-28), and set a native filter only to its saved value; every criterion is in its prompt, read as the person meant it ("everywhere" leaves the place box empty). No cap of its own on turns, pages, tokens or verified postings: it gets the sweep's time left divided by the websites left, and pacing and the 7 day pause stay (LinkedIn's 5 pages a sweep and every website's one sweep a day were removed by the owner on 2026-10-28). The summary names the searches it ran. Plan: [interactive-search-626.md](interactive-search-626.md). Status: built on the combined branch, journeys run by the orchestrator.
