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

Note, 2026-10-28: the ATS reader (`jobs/ats.py`), its fixtures and the fake ATS server were deleted with the board engine (owner, "Delete it"); every sweep now runs the ticked job websites. The worker, the one sweep per user, progress, summary, schedule and never apply rules stay.

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
- The journey through the public URL with screenshots, for editing (J4) and the sweep (J5): done 2026-10-09 by `tests/e2e_jobs_jetson.sh` and `tests/e2e_jobs_sweep_jetson.sh`, PASS, evidence in `tasks/evidence/jobs-j4-jetson/` (36 screenshots) and `tasks/evidence/jobs-j5-jetson/`. Note, 2026-10-28: `tests/e2e_jobs_sweep_jetson.sh` was deleted with the board sweeps; it cannot be run again.
- The same for the list, the detail with its PDF preview and the map: ready, not run on the Jetson yet. `tests/e2e_jobs_jetson.sh view` runs `web/e2e/jobs.spec.ts` in its E2E_SYNTHETIC mode (433 made-up rows with the CSV counts, posting texts and files, loaded over HTTP) and `web/e2e/jobs-map.spec.ts`, evidence in `tasks/evidence/jobs-j7-jetson/`. Both passed on a local scratch stack on 2026-10-21, 12 tests of 12. Left: the owner's run below.
- The Jetson journeys again on today's `main`: not done. The scripts reach the Jetson over ssh, so they run on the owner's Mac.

## Checkpoint J
- [ ] All 5 success criteria of SPEC-jobs.md verified

One line per criterion, 2026-10-21:
1. Harald's 433 rows, texts and files on the Jetson, counts equal to the CSV, an idempotent import: done 2026-10-09 (the J7 lines above). The owner's runs below print the live counts again.
2. The scoring port matches the engine on every scorable row: done, `api/tests/jobs/test_scoring_golden.py` (395 of 395, J1).
3. Find jobs sweeps the boards from the Jetson and adds only new matching postings, scored: done, `tasks/evidence/jobs-j5-jetson/`, `api/tests/jobs/test_sweep_api.py`, `test_sweep_parity.py`. A schedule, once on, runs by itself: proven locally (`api/tests/jobs/test_schedule.py`, `test_worker.py`, the journey "Schedule: off by default, turned on, fires at its time, turned off" of `web/e2e/jobs-sweep.spec.ts`), not yet checked on the Jetson. Left: the owner's schedule check below. Note, 2026-10-28: the board sweeps were deleted; a sweep now searches the ticked job websites, so this criterion's evidence is history and the schedule check runs a website search.
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

Owner decision of 2026-10-27 ([decisions](../../docs/decisions.md)), replacing #651's separate permission cards. Find jobs shows each job website's risk sentence beside its box; Save records the consent of every ticked website through the platform's consent route on the reviewed revision and takes back the one of a website unticked; "Websites only" searches the ticked websites alone (`POST /api/jobs/sweeps {websites_only}`). The search's permission checks, pause, caps and pacing are unchanged. Status: built on the combined branch, journeys run by the orchestrator. The "Websites only" part is replaced on 2026-10-28: every search runs the websites alone, the setup shows no board, feed, reader or regular expression switch (branch `jobs/websites-only-setup`). The same day the board, feed and fixed reader code itself was deleted (owner, "Delete it", branch `jobs/delete-board-engine`), and `websites_only` went from the API.

### J18: one agent per job website, with all the saved criteria

Owner decision of 2026-10-27 ([decisions](../../docs/decisions.md)), replacing the 2026-10-26 "one search per combination". One lease and one finder run a website a sweep, given every saved criterion as context; browserd's frozen scope lets it type only one saved title in a search box or one saved place in the place box, case and accents aside (or nothing: an empty box is always allowed, owner 2026-10-28), and set a native filter only to its saved value; every criterion is in its prompt, read as the person meant it ("everywhere" leaves the place box empty). No cap of its own on turns, pages, tokens or verified postings: it gets the sweep's time left divided by the websites left, and pacing and the 7 day pause stay (LinkedIn's 5 pages a sweep and every website's one sweep a day were removed by the owner on 2026-10-28). The summary names the searches it ran. Plan: [interactive-search-626.md](interactive-search-626.md). Status: built on the combined branch, journeys run by the orchestrator.

### J19: a reader agent fills every posting's fields from its text

Owner decision of 2026-11-09 ([decisions](../../docs/decisions.md), issue #764). Depends on: none. Owns: `api/app/jobs/reader.py`, `reader_tools.py`, `prompts/reader.md`, the fake stream `posting_reader.json`, the sweep's reading phase (`sweep.py`), migration 0047 (`jobs.read_at`, `jobs.read_attempts`, `jobs.read_run_id`), `tests/jobs/test_reader.py` and `test_migration_0047.py`, the History label of the reader's `edit` event (`web/src/features/jobs/helpers/text.ts`). Touches: `api/app/worker.py` (the sweep's reader hook, the backlog cron), `api/app/jobs/agents.py` and `api/app/agents/catalog.py` (the registration, the category "Finding jobs"), `api/app/browser/words.py` and `api/app/browser/nature.py` (the finder's word fold and the VIE contract map in modules that register no agent, imported by the finder and the website search too). Spec: [SPEC-jobs, "Posting reader"](../../SPEC-jobs.md#posting-reader-owner-2026-11-09-764). Acceptance: an invented posting shaped like the VIE example is stored by a sweep on the fake agent backend with its location, monthly pay, published date and a description without the page chrome; a value whose quote is not in the text is dropped; a field set by hand stays; a sweep without a credential stores its postings as before; the backlog reads a saved posting once and stops after 3 attempts failed for their own content, a failure of the credential, the provider or the worker giving the attempt back.

### J20: LinkedIn's public job search finds what a person would find

Owner request of 2026-11-17 ([decisions](../../docs/decisions.md), issue [#793](https://github.com/GoldLegendW80/jobapp/issues/793)): "It must work, even if i'm not logged in". Spec: [SPEC-jobs, "LinkedIn's public job search"](../../SPEC-jobs.md#linkedins-public-job-search-owner-2026-10-30) (Searches, Filters before any detail request, Details, Pace and caps, Summary, Fallback of the browser search) and the Sweep section's time caps.

| Id | Issue | What | Scope | Depends on | Migration | Group |
|---|---|---|---|---|---|---|
| J20 | #793 | Title rule after reading, places as LinkedIn names them and judged against the saved places, a budget for 12 titles in 3 places, fallback of the browser search | M | none (B25 #789 edits sweep status beside it) | none | 1 |

Owns: `api/app/browser/sources/linkedin_guest.py`, `linkedin_places.py` (the alias table and `WantedPlaces`), the fallback's scheduling in `api/app/browser/sources/__init__.py` (`Runner.websites`) and `api/app/jobs/sweep.py` (`_failed` leaves a fallback out of B24's retry), the summary's fields (`api/app/jobs/schemas.py`, `sweep_router.py`, `web/src/features/jobs/api/sweeps-api.ts`, `helpers/labels.ts` `guestSentence`), Activity's note (`api/app/agents/activity_steps.py`), the LinkedIn guest fake and tests (`api/tests/browser/test_linkedin_guest.py`, `test_linkedin_guest_store.py`, `test_j20_linkedin_guest_finds.py`). Acceptance: a card titled outside the saved terms is read and stored; a saved place goes to LinkedIn by the alias table ("Tahiti" as "French Polynesia", "La Réunion" as "Reunion"), unknown places as written; a posting of a place none of the saved ones (LinkedIn's answer for a place it does not know, "Miami, FL") is not stored; 12 titles in 3 places are each searched once with details of new cards within one sweep, the pairs not searched named when it ends early; with the browser search, no LinkedIn sign in or a failed browser search runs the public job search in the same sweep (`fallback` "guest" in stats, shown in Activity), with no action of the person.

### J21: Anywhere on the web, a search source that works like Claude Code's web search

Owner request of 2026-11-18 ([decisions](../../docs/decisions.md), issue [#807](https://github.com/GoldLegendW80/jobapp/issues/807)). Branch `jobs/J21-anywhere-on-the-web`.

| Id | Issue | What | Scope | Depends on | Migration | Group |
|---|---|---|---|---|---|---|
| J21 | #807 | "Anywhere on the web": a sweep source (`web_search`, on by default) run by `web_discovery`, a sibling of `browser_discovery` sharing its tools and verification, WebSearch with no cap of the app's (owner: "No cap, like the Mac"; Claude Code's own default of 200 a session holds), queries and result counts in Activity | M | none (J22 #808 edits sweep.py beside it) | none | 1 |

Owns: `api/app/browser/sources/interactive.py` (`run_web`, `_web`, the web branch of `_search`), `api/app/browser/finder.py` (`WEB_NAME`, `WEB_START`, `WEB_PROMPT`, `InteractiveScope.web`), `api/app/agents/runner.py` (the WebSearch query preview and result count, the per definition cap, none for the web), `api/tests/browser/test_j21_web_source.py`. Touches: `sdk_options.py`, `definitions.py`, `catalog.py`, `workflows.py`, `activity_steps.py`, `profile/schemas.py` (`web_search`), `jobs/sweep.py` (`has_web`, `web_entry`, the frozen source), `browser/sources/__init__.py` (`Runner.websites` runs it last). Status: first slice in pull request, see its "Left to do".

### J22: LinkedIn's public job search page in the browser when the public API refuses

Owner decision of 2026-11-18 ([decisions](../../docs/decisions.md), issue [#808](https://github.com/GoldLegendW80/jobapp/issues/808)): "Yes, fall back to browser". Spec: [SPEC-jobs, "LinkedIn's public job search"](../../SPEC-jobs.md#linkedins-public-job-search-owner-2026-10-30), Fallback to the public page.

| Id | Issue | What | Scope | Depends on | Migration | Group |
|---|---|---|---|---|---|---|
| J22 | #808 | LinkedIn's public job search page read signed out in the browser by code for the pairs the refused public API did not search | M | J20 (#793, merged) | none | 1 |

Owns: `api/app/browser/sources/linkedin_page.py`, its call in `api/app/browser/sources/__init__.py` (`Runner.websites`, `runner_for`), the fallback's planning in `api/app/jobs/sweep.py` (`linkedin_page_due`, `_failed`, `NOT_RETRIED`), Activity's note (`api/app/agents/activity_steps.py`), the history's sentence and reason (`web/src/features/jobs/helpers/labels.ts`), the fake pages `tests/browser_pages/j22/`, `api/tests/browser/test_j22_linkedin_page.py` and `test_j22_linkedin_page_container.py`. Acceptance: after a 429 or a failure before every pair, the page is read signed out (a fresh context) for the pairs left, addresses and cards by code, posting pages read, J20's rules applied; paced 10 to 30 s; a sign in wall, a checkpoint or a 429 page stops it `linkedin_page_refused`; `fallback` "page" in stats with searches done and not searched, cards and postings; Activity and the summary say "LinkedIn's public job search page, as the public API refused" ("failed" after a failure); at most once a sweep, never beside B24's retry nor after J20's fallback; tests on fake pages only.

### J23: the web source searches as widely as Claude Code before it gives up

Owner request of 2026-11-18 ([decisions](../../docs/decisions.md), issue [#813](https://github.com/GoldLegendW80/jobapp/issues/813)), after the production sweep b2c8a077 (five narrow queries, no France Travail, APEC or Hellowork search, postings of "about a month ago" dropped). Spec: [SPEC-jobs, Sweep](../../SPEC-jobs.md#sweep), "How widely the web source searches". Branch `jobs/J23-web-breadth`.

| Id | Issue | What | Scope | Depends on | Migration | Group |
|---|---|---|---|---|---|---|
| J23 | #813 | A plan per wanted place (saved titles, three roles next to the profile, national boards by site: query) shown in the first message, counted from the WebSearch queries, `report_no_postings` refused until it is searched; approximate or missing dates kept and flagged | M | J21 (#809, merged) | none | 1 |

Owns: `api/app/browser/web_plan.py` (ROLES, FALLBACK, BOARDS, the matching), the web branch of `FinderSession.report_none` and `routes_left` (`web_left`, `web_queries`, `web_search_answered`), `WEB_PROMPT`'s Method, `date_shown` (`finder.py`, `finder_tools.py`), `sweep.discovery_date`, the timeline line (`web/src/features/jobs/helpers/text.ts`), `api/tests/browser/test_j23_web_breadth.py`. Touches: `agents/definitions.py` and `runner.py` (`web_search_answered`), `agents/fake_backend.py` and `fake_streams/web_discovery.json` (`web_search_error`). Acceptance: an early `report_no_postings` is refused with what is left and accepted once every family of every place (with no place, of every search language) is searched; the accept and refuse table of the family matching; an approximate date is kept and flagged; a recorded La Réunion session, replayed by the fake Claude Code through the runner, stores a niche board posting and a France Travail posting.

### J24: the search agents report what fits the profile, and let scoring judge

Owner decision of 2026-11-04 (B21, [decisions](../../docs/decisions.md)), issue [#815](https://github.com/GoldLegendW80/jobapp/issues/815), after the production sweep b95ff4f7 (2026-11-18: full stack, data engineer and a 5 years data scientist posting seen in La Réunion, none reported, "Aucun n'est un poste d'ingénieur IA"). Spec: [SPEC-jobs, Sweep](../../SPEC-jobs.md#sweep), "What the search agents report"; [SPEC-browser](../../SPEC-browser.md#interactive-discovery-626), B21. Branch `jobs/J24-report-what-fits`.

| Id | Issue | What | Scope | Depends on | Migration | Group |
|---|---|---|---|---|---|---|
| J24 | #815 | Both search prompts report every open posting in a wanted place whose role matches a saved title OR the profile's skills or adjacent roles, whatever its title, seniority or years; never left for years, seniority, pay or a partial skill match; a posting known only from a search result or a blocking aggregator looked for on the employer's site or a public board; code checked to drop no posting for its title | S | J23 (#813, merged) | none | 1 |

Owns: `INTERACTIVE_PROMPT` and `WEB_PROMPT`'s reporting rule (`api/app/browser/finder.py`), the plan's `report` line (`web_plan.shown`), `api/tests/jobs/test_j24_report_what_fits.py`. Acceptance: the prompts say what fits and what alone is left (prompt test); a full stack posting and a data engineer posting in the wanted place, with AI titles saved, are reported and stored, by the web source and by a website; a posting asking 5 years of a 3.5 years profile is stored with its "5+ years" gap from scoring; a posting of an excluded place (Paris) is not stored. A place merely not saved is left by the agent (its prompt), not by code: web pages write places freely ("Saint-Denis" alone), and a code judge would drop fits.

### J25: clean titles, one row per job across boards, and the town and AI board searches

Production acceptance of J24 (2026-11-19, sweep 81ab9533, [decisions](../../docs/decisions.md), issue [#817](https://github.com/GoldLegendW80/jobapp/issues/817)): the same LHH job stored twice (Hellowork "Data Scientist H/F LHH Recruitment Solutions", Jobijoba "Data scientist h/f"), and the Saint-Denis AI and data CDIs Claude Code found on a niche board the day before never surfaced. Spec: [SPEC-jobs, Sweep](../../SPEC-jobs.md#sweep), "The town and AI boards searches of each place" and "Clean titles and one row per job across boards". Branch `jobs/J25-titles-dedup-towns`.

| Id | Issue | What | Scope | Depends on | Migration | Group |
|---|---|---|---|---|---|---|
| J25 | #817 | Titles cleaned before store and dedup (gender markers, board boilerplate, a trailing company or board; the company a title names when none is stated, recruiters flagged); one row per job across boards (employer or recruiter, cleaned title and place within 60 days); the J23 plan's `town` and `ai_boards` families, each a query of its own per place | M | J24 (#815, merged) | none | 1 |

Owns: `api/app/jobs/posting_titles.py` (the cleaning, the recruiter table, the place and cross-board keys), the store's use of them (`sweep.Known.cross`, `_store_finding`), the families in `api/app/browser/web_plan.py` (`main_town`, NICHE, OFFER_PHRASES, `own_families`, `covering_queries`), `api/tests/jobs/test_j25_titles_dedup.py`, `api/tests/browser/test_j25_town_ai_boards.py`. Touches: `finder.py` (`WEB_PROMPT`'s Method, `stream_variables`), `agents/fake_backend.py` (an `each` step: one copy per item of a JSON list variable) and `fake_streams/web_discovery.json` (each town and AI boards query searched after the covering one). Acceptance: the accept and refuse tables of titles, recruiters, companies, places and the two families; the LHH pair and the Emploi DomTom pair (invented copies) stored as one row with two sources, another job of the same company apart, an existing row matched and left untouched, a copy past the window another posting; the default fake web stream still ends clean with every family searched. Not in it: merging the duplicates already stored (a reversible follow up if wanted).

### J26: the AI boards searched by site:, and contract markers out of titles

Production acceptance of J25 (2026-11-19, sweep f769661a, [decisions](../../docs/decisions.md), issue [#819](https://github.com/GoldLegendW80/jobapp/issues/819)): the `ai_boards` family was met by "offre emploi genai engineer Saint-Denis La Réunion" and the four Saint-Denis AI and data CDIs of the-intelligence-academy.com were never searched; Jobijoba's "Data scientist - (cdi)" of LHH was stored beside the LHH job already stored. Spec: [SPEC-jobs, Sweep](../../SPEC-jobs.md#sweep), "The town and AI boards searches of each place" and "Clean titles and one row per job across boards". Branch `jobs/J26-niche-site-contract-markers`.

| Id | Issue | What | Scope | Depends on | Migration | Group |
|---|---|---|---|---|---|---|
| J26 | #819 | `ai_boards` met only by a `site:` query of a niche board (OR of several fine) naming the place or its main town, "offre emploi" no longer counts; contract markers ("(CDI)", "- CDI", "CDI -", "CDD 6 mois", "(Stage)", "(VIE)", ...) out of the stored title and the cross-board key, the contract type kept | S | J25 (#817, merged) | none | 1 |

Owns: the `ai_boards` matching, `ai_boards_query`, the refusal and the shown plan in `api/app/browser/web_plan.py` (OFFER_PHRASES removed), the contract markers of `api/app/jobs/posting_titles.py` (`_contracts`, `Cleaned.contract`), the contract type in `sweep._store_finding`, `api/tests/browser/test_j26_niche_site.py`, `api/tests/jobs/test_j26_contract_markers.py`. Touches: `finder.py` (`WEB_PROMPT`'s Method), the J23 and J25 tests whose offer phrase queries counted. Acceptance: the accept and refuse tables of the `site:` forms per place and town (the offer phrase form refused) and of the contract markers (real titles kept: "Juriste contrats", "Gestionnaire CDD", "Actuaire (Vie)"); "Data scientist - (cdi)" and "Data Scientist H/F" at LHH in Saint-Denis 974 stored as one posting; a title's lost "(CDD)" or "(Stage)" still the row's contract type; the refusal and the shown plan name the niche boards and the example query; the default fake web stream still ends clean.

### J27: the web search finishes its plan even after it found something

Production acceptance of J26 (2026-11-19, sweep a6c1fe73, [decisions](../../docs/decisions.md), issue [#821](https://github.com/GoldLegendW80/jobapp/issues/821)): the web agent ran four WebSearch queries, reported one posting (a duplicate) and ended; the saved titles, the adjacent roles, the national boards of Tahiti and Guadeloupe and the niche AI boards by `site:` were never searched, as the plan was enforced only on `report_no_postings`. Spec: [SPEC-jobs, Sweep](../../SPEC-jobs.md#sweep), "The web search finishes its plan". Branch `jobs/J27-finish-the-plan`.

| Id | Issue | What | Scope | Depends on | Migration | Group |
|---|---|---|---|---|---|---|
| J27 | #821 | A posting kept spares nothing of the web plan; every `report_postings` answer names what is left and says to go on; the run ends only through `report_no_postings`; a turn ended without it while the plan is left is continued in the same session by a follow up message, at most twice, then the source ends `plan_unfinished` (not retried, labelled in Activity and the Jobs summary) | M | J26 (#819, merged) | none | 1 |

Owns: `FinderSession.web_left` (no longer lifted by a posting), `finished`, `go_on`, `plan_unfinished`, `_with_plan`, `FinderDefinition.follow_up` and `WEB_PROMPT`'s Method 8 and 9 (`api/app/browser/finder.py`), `interactive.outcome`'s `plan_unfinished`, `api/tests/browser/test_j27_finish_the_plan.py`. Touches: `agents/definitions.py` (`AgentDefinition.follow_up`, None by default), `agents/backend.py` (`BackendRequest.follow_up`, `SdkBackend` queries the same client again), `agents/runner.py` (`FOLLOW_UPS_MAX` 2, `_follow_up`, the `follow_up` and `unfinished` events, numbers only), `agents/fake_backend.py` (the steps after a result are the follow up's turn, `seen_follow_ups`), `jobs/sweep.py` (`NOT_RETRIED`), `web/src/features/agents/helpers/run-steps.ts` (the two events), `web/src/features/jobs/helpers/labels.ts` (`plan_unfinished`), the J23 La Réunion runner test (now ends `plan_unfinished`: its AI boards query is never made). Acceptance: a stream that reports one posting then ends is continued once, its first give up refused with what is left, its remaining families searched, then finished; a stream that covers the plan ends with no follow up; a stream that never goes on gets two follow ups, then `unfinished`, the source `plan_unfinished`, partial with its posting kept; a stopped session is never continued and may still end; the default fake web stream still ends clean. Not applied to the website search (B27): its routes are needed only before a website ends with nothing, a posting kept proves its route works. A run with a token budget is never continued (its events are handled behind the stream). Measured on the pinned CLI (2.1.286, bundled with claude-agent-sdk 0.2.163; review of #822): each result's `usage` and `num_turns` are its own query's, while `total_cost_usd` is the session's total so far. So a continued run sums the tokens and turns of every result and keeps the last result's cost (`_State.results`, `_tokens`); the Admin run list sums the turns of the usage events; a follow up's usage step reads "This query: ... Total so far: ..."; the fake's follow up results do the same. `SdkBackend.interrupt()` also stops any further follow up (a Cancel between two queries finds the CLI idle).

### J28: the web search opens every niche AI board result in a wanted place before it finishes

Production acceptance of J27 (2026-11-19, sweep b9ca8a4b, [decisions](../../docs/decisions.md), issue [#823](https://github.com/GoldLegendW80/jobapp/issues/823)): the web agent searched its whole plan, `site:the-intelligence-academy.com` queries naming La Réunion included, stored EDF, Tahiti Numérique and WinDev, and never opened one of that board's pages, where the three Saint-Denis CDIs Claude Code finds with the same query were online. The owner chose "One more fix, then stop". Spec: [SPEC-jobs, Sweep](../../SPEC-jobs.md#sweep), "The web search opens the niche board results in a wanted place". Branch `jobs/J28-open-niche-results`.

| Id | Issue | What | Scope | Depends on | Migration | Group |
|---|---|---|---|---|---|---|
| J28 | #823 | The niche board results in a wanted place of each WebSearch answer (at most 15 a run, answer order) opened or reported before `report_no_postings` holds; the refusal, every `report_postings` answer and the J27 follow up name those left; the prompt says to open them | S | J27 (#821, merged) | none | 1 |

Owns: `web_plan.niche_board`, `niche_link` and NICHE_LINKS_MAX (`api/app/browser/web_plan.py`), `FinderSession.note_niche_links`, `niche_left`, the J28 checks of `report_none`, `go_on`, `plan_unfinished`, `_with_plan`, `navigate`, `web_fetch` and the reports, `WEB_PROMPT`'s Method 10 (`api/app/browser/finder.py`), `api/tests/browser/test_j28_open_niche_results.py`. Touches: `agents/runner.py` (`web_search_links`, the results told with each answer), `agents/definitions.py` (`web_search_answered`'s `links`). Acceptance: the accept and refuse table of the results (boards, subdomains and look alikes, places in the title or the address, the shared town rule); two niche results for La Réunion and one for Paris in a WebSearch answer require opening the two, through the runner on the fake Claude Code; a run that opens them finishes with no follow up; one that never does is continued twice, then `plan_unfinished`; the cap of 15; the default fake web stream lists no niche result and still ends clean. Not in it: a result titled with a shared town alone ("Data scientist, Saint-Denis") is not required (the shared town rule), nor one whose place is only in its host.
