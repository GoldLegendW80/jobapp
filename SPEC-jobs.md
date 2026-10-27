# Spec: jobs

Module of [CAPABILITY-MAP.md](CAPABILITY-MAP.md). Depends on `profile`. `documents`, `agents` and `apply` act on its rows.

## Objective

Every user has their own job pipeline in the app: the postings found, their fit score against the user's profile, their status from `found` to `applied` and beyond, and notes. It replaces `data/jobs.csv`, the `data/jobs/<id>/` folders and the `jobs.html` dashboard, and the `find` and `score` commands of `engine/jobs.py`.

User stories:
- As Harald, I find my 433 existing rows in the app with their status, fit, competencies, gaps, notes, dates, posting text and the files I already sent (CV and letter PDFs, answers), imported once from `../data`.
- As a user, I see a dashboard: counts per status, a fit histogram, and a list I can search, filter (status, contract type, company, fit range, location, found date) and sort; one row opens a detail view with the posting text and everything attached.
- As a user, I select one or many rows and change their status or add a note in one action; the history of every change is kept.
- As a user, I add a posting by hand (URL, company, title, location, text) when it comes from a site with no API.
- As a user, I press "Find jobs" and choose my websites and criteria for [interactive search](#interactive-search-626). Explicit API boards and feeds remain available beside that mode. Existing board sweeps (Ashby, Greenhouse, Lever public APIs) keep their matching, scoring and opt in schedule (days, times, time zone), off until enabled.
- As a user, I see my postings on a map, as the current dashboard does.

## Data model

```
jobs(id uuid pk, user_id fk users cascade, slug text, company text, title text, location text,
     contract_type enum(CDI, CDD, VIE, Stage, Alternance, Freelance), url text, source text,
     status enum(found, shortlisted, cv_ready, staged, applied, interview, rejected, closed, skipped),
     fit int null, fit_overridden bool, competencies text[], gaps text[], description text,
     posting_text text, published_on date null, found_on date, applied_on date null,
     notes text, legacy jsonb (unmapped CSV columns), created_at, updated_at,
     unique(user_id, slug), unique(user_id, url) where url is not null)
job_events(id bigserial, job_id fk cascade, user_id, at, kind enum(created, status, fit, note, edit, import, sweep),
           data jsonb)
job_files(id uuid, job_id fk cascade, name text, mime text, bytes bytea, size int, sha256 bytea,
          origin enum(legacy, generated, upload), created_at)
sweeps(id uuid, user_id, started_at, finished_at, trigger enum(manual, schedule), status enum(running, done, failed),
       stats jsonb, error text)
```

Slug rule and duplicate detection reuse the engine's (`<company>-<role>`, lowercase, hyphenated; same URL or same company and title is the same posting). Status changes to `applied` fill `applied_on` once.

## Scoring

Port of `engine/jobs.py score_row` against the user's `skills` section, using `api/app/profile/matching.py` for term matching (P2 made it the single source of matching rules). Output: fit 0 to 100, competencies named, gaps. A manual fit override is kept (`fit_overridden`) and never replaced by a rescore unless the user asks. "Rescore all" after a profile change. A golden test proves the port gives the same fit, competencies and gaps as the engine on every row of `../data/jobs.csv` that has a posting.

## Sweep

These are the existing API sweep rules; model-driven discovery has the separate approved contract below. Selecting that mode does not silently fall back to this sweep or to a fixed browser reader.

- Backend fetches each enabled board of the user's `search.boards` with httpx (timeouts, retries with backoff, polite concurrency of 4, a User-Agent naming the app), parses Ashby, Greenhouse and Lever exactly as `engine/jobs.py fetch` does, filters with the user's include, exclude, per-board exclude, location and unwanted place terms and the days window, dedupes against existing rows, stores new ones as `found` with the posting text, scores them.
- Runs in the `worker` container (first use of Redis as a queue, with RQ or arq; the choice is recorded in the plan), one sweep per user at a time, progress visible in the UI, a summary at the end (boards ok and failed, postings seen, matches, new).
- Automatic sweeps per user, off by default: the user turns them on and picks weekdays, one to four local times and a time zone (`search.sweep_schedule`); a worker tick every 5 minutes starts each due slot once, daylight saving time handled.
- `never_apply` rules from the profile mark matching rows `skipped` with the rule as the note (hard rule 11 generalised). Sweeps and apply share `jobs/never_apply.py`: accents, case, compatibility forms, hidden characters and lookalike letters are folded, and a term at the start or end of a glued word also matches. The original title, company and rule note stay unchanged.
- If an ATS blocks plain HTTP from the Jetson, that board is reported as failed; browser-based fetching comes with the `browser` module.

## Interactive search (#626)

**Owner intent, 2026-10-24:** Find jobs should reproduce the owner's Claude Code workflow: the person chooses websites and search parameters, then an actual LLM uses Playwright to search those websites. The owner confirmed LinkedIn, Welcome to the Jungle, Indeed and Free-Work as the first targets, plus adding custom URLs. This is a changed product contract, not a claim that the deployed app implements it. Issue [#626](https://github.com/GoldLegendW80/jobapp/issues/626) records the request.

**Verified source baseline:** at deployed commit `8ca71e67c3a2f205bb347bf7affa00e25af1d5bb`, `api/app/browser/finder.py` runs an internal model with navigate, snapshot, wait and report tools only. `api/app/browser/sources/site.py` supplies no search criteria. Google Careers, VIE and LinkedIn use fixed readers; the generic Search editor enables only Google Careers and VIE (`web/src/features/profile/editor-forms.ts`). This source audit does not explain any particular empty search.

**Approved behavior (owner, 2026-10-24):**

- Find jobs opens setup from Jobs before starting. The person selects websites or enters their URLs, sees which use model-driven browsing, and saves the selection per person. API boards and feeds are separately labelled choices, with their existing support retained.
- Ticking a job website and saving is the person's permission to search it (owner, 2026-10-27, jobs J17): its risk sentence shows beside its box, the save records the consent through the platform's consent route at that sentence's version and the reviewed revision, and unticking it and saving takes it back. "Websites only" runs the ticked websites and no board, feed or fixed reader for that search, and its summary says so.
- Setup reuses saved role or keyword terms, locations and remote preference, contract types, seniority, recency, optional pay and custom instructions. The person can review and edit them there. Criteria guide the model's queries, filters and reading, rather than merely filtering rows after browsing. Existing seniority scoring and pay rules remain those of [opportunities](SPEC-opportunities.md#owner-decisions-never-decide-them-again); no new hard exclusion is implied.
- Starting freezes the selected sources, their mode and the saved criteria for that run. Later profile edits affect the next run. The run shows which criteria each site applied and which were unavailable or could not be verified. Unknown pay, date, seniority or remote status is labelled unknown, not asserted to match. Found date and observed publication date remain distinct in this new path.
- The configured model runs through the existing agents credential/runtime boundary and the bound [browser tools](SPEC-browser.md#interactive-discovery-626). It navigates, types, clicks, selects and waits to use actual search boxes, filters and pagination, then opens posting detail pages. A fixed parser, API response or prerecorded result list alone cannot satisfy model-driven mode. Without a usable model or browser, that mode visibly refuses to start or reports the affected source blocked, with no substitute mode.
- Scope stays within the selected websites and code-enforced source/host boundaries, including observed, source-attributable posting destinations. Each picked site is attempted or gets a visible reason for refusal or deferral, including absent opt in, unsupported interaction or exhausted budget; a blocked posting destination also gets a reason. Normal posting-page browsing needs no repeated owner approval. No automatic general-web expansion or new source activation.
- Jobs shows a live run panel: selected sites, current site and action, queue or waiting state, browser watch, manual sign in and takeover, incremental saved results, and Cancel. Closing and reopening Jobs restores the run view. Actions describe observable work, without exposing model reasoning. Cancellation stops further automatic actions after an in-flight tool call and preserves findings already saved; it does not roll back those jobs.
- A saved finding has a source-bound URL observed in the browser and posting text read from its detail page. Its title and employer must be supported by that text; an unnamed employer stays explicitly unknown, not the job board's name. Matching rationale cites observed facts and marks uncertain criteria. Invalid or invented findings are refused. Existing duplicate and global never-apply rules apply, including the cybersecurity exclusion; search creates `found` postings or rule-marked `skipped` postings, never a Like, application, submission, contact or account edit.
- The final and live summaries distinguish per source: no matching postings observed, partial coverage, blocked or failed, cancelled, budget stopped, duplicates, rule exclusions and newly saved findings. "No postings above your display threshold" includes that threshold and counts separately from "no jobs found", linking to saved results and Search options.

**Bounded defaults:** keep at most 10 selected websites, existing structured field limits and at most 2,000 characters of custom instructions, enforced consistently in setup and the server. Browser execution keeps the existing [limits and guardrails](SPEC-browser.md#interactive-discovery-626); choosing a site promises an attempt or an explanation, not universal site support or completion. The owner approved proceeding with this contract and new draft PR work on 2026-10-24.

**Acceptance evidence required before this is described as implemented:**

1. With deterministic fake pages representing each confirmed target and a custom URL, a person chooses sites and criteria from Jobs; a recorded model tool-call stream through the real agents runtime invokes actual Playwright typing, search submission, filter selection, pagination and detail-page reading. A tool trace plus resulting query/filter state proves these actions executed, and the test fails with parser-only execution. No real site, account or external model call is needed for this test.
2. The fixture exposes a relevant job only after those interactions, plus a nonmatch, duplicate, missing date/pay/employer, never-apply role and hostile page instructions. Only observed, rule-checked findings are saved with truthful provenance and rationale. Unsupported criteria, duplicate and rule counts and the display threshold explanation are asserted.
3. Integration tests cover the immutable run scope despite profile edits; missing credential or browser without fallback; each selected site's outcome; blocked/checkpoint and manual login/resume with the same person's persistent profile; cancellation preserving saved findings and stopping later actions; budget stops and two-person isolation for settings, runs, events, results and browser viewing.
4. A real browser journey verifies setup, progress, watch/handoff, incremental results, cancellation and useful empty/partial states from Jobs on desktop and at 375px, using keyboard access and light/dark views. Evidence records runtime invocation and bounded tool actions, not only screenshots or a stubbed list of jobs.
5. After an authorized deployment, the owner runs the confirmed sites with their configured real model and criteria, showing an actual model/tool run and observed saved jobs or honest source-specific zero/blocked reasons. Fake model tests prove integration, not real model search effectiveness. Development agents do not access real sites, accounts or credentials for this acceptance; no matching job or universal site success is promised.

This stays in the existing jobs, profile, agents and browser source/test directories below and in [SPEC-browser.md](SPEC-browser.md); it creates no new module. Use the current stack, style and verification commands in [CLAUDE.md](CLAUDE.md#daily-commands) and [implementer.md](docs/agents/implementer.md#tests), with fake pages under `tests/browser_pages/`, API coverage in `api/tests/jobs/` and `api/tests/browser/`, and journeys in `web/e2e/`. For example: `rtk proxy ops/agent-test.sh --name jobs-626 tests/jobs tests/browser -m "not docker" -n 1`. The implementation contract and ordered task issues are in the [interactive search plan](tasks/jobs/interactive-search-626.md); specification drafting alone changes no running stack or API.

## Import of Harald's data

`./deploy.sh jobs import <email>` reads `../data/jobs.csv` and `../data/jobs/<id>/` on the Mac and posts them to the api for that user: every CSV column mapped (unmapped ones into `legacy`), posting text from `posting.txt` (header fields parsed), files attached as `job_files` with origin `legacy`: `cv.pdf`, `letter.pdf`, `cv.json`, `letter.json`, `answers.json`, `review.json`, `apply.py` (HTML renders skipped: they are rebuilt from JSON). Idempotent: running it twice changes nothing. Rows whose status is `applied` keep their files byte for byte (hard rule 9).

## API

All require a session and act on the caller's rows only (404 otherwise).

| Method and path | Does |
|---|---|
| GET /api/jobs | list with filters (status[], type[], company, q full text on company, title, location, notes; fit_min, fit_max; found_from, found_to), sort, cursor pagination; returns counts per status and a fit histogram for the filtered set |
| GET /api/jobs/{id} | detail with posting text, files list, events |
| POST /api/jobs | add by hand; scored immediately when text is given; 409 on duplicate with the existing id |
| PATCH /api/jobs/{id} | edit fields, status, notes, fit override |
| POST /api/jobs/bulk | `{ids[], set: {status?, notes_append?}}` in one transaction |
| POST /api/jobs/rescore | `{ids?[], overwrite_manual: bool}` |
| GET /api/jobs/{id}/files/{file_id} | download with the stored mime |
| POST /api/jobs/sweeps, GET /api/jobs/sweeps, GET /api/jobs/sweeps/{id}, GET /api/jobs/sweeps/{id}/events | start (409 when one runs), list (with the user's schedule and its next run), progress and the jobs added, progress as server-sent events |
| GET /api/jobs/places | coordinates for the map, from `engine/places.json` ported as a bundled gazetteer |
| POST /api/jobs/import | the import document used by the deploy command |

## Frontend

`/jobs` becomes the home page after sign in: KPI tiles per status, fit histogram, filters bar (chips, saved in the URL so a view can be bookmarked), virtualised table on desktop and cards on mobile, multi-select with a bulk action bar (status, note), "Find jobs" button with live progress and the summary, "Add a posting" dialog. `/jobs/:id` detail: header (company, title, location, contract, fit badge with competencies and gaps), status select, notes editor, posting text, files with preview for PDFs and download, events timeline. `/jobs/map`: world map with pins per city or country, a side list of unplaced locations (port of the Map view of `engine/dashboard.html`, its land outlines and gazetteer bundled). Design follows the existing shadcn shell; light and dark; 375px.

## Commands

As before, plus `./deploy.sh jobs import <email>`.

## Project structure

```
api/app/jobs/  models.py schemas.py router.py service.py scoring.py sweep.py ats.py importer.py places.json world.json
api/app/worker.py            queue worker entry point (new `worker` service in compose.yml, 1536m budget already reserved)
api/alembic/versions/0004_jobs.py
api/tests/jobs/
web/src/features/jobs/
web/e2e/jobs.spec.ts
```

## Testing strategy

- Unit: slug and duplicate rules; scoring port golden test against the engine on the real CSV; ATS parsers against recorded fixtures of each API (captured once, checked in, personal data free); filter terms; never_apply rules; importer mapping of every CSV column and every file type.
- Integration (real Postgres and Redis): every endpoint, isolation (user B sees none of A's rows, files, sweeps), bulk in one transaction, concurrent edits, sweep against a fake ATS server (ok, 404, 429, timeout, malformed JSON, 5000 postings), one sweep per user at a time, idempotent import, applied files immutable, a scheduled slot enqueues once (daylight saving time included).
- End to end (Playwright, locally and on the Jetson): Harald's import shows 433 rows with the same status counts as the CSV; filters and URL state; bulk status change; detail with PDF preview; add by hand; a sweep against the fake ATS locally and one real sweep on the Jetson for an e2e user with two boards; map pins; screenshots light and dark, desktop and 375px.

## Boundaries

- **Always:** user scoping on every query; keep privacy-safe events; respect ATS rate limits for public API sweeps and browser pacing, consent and checkpoint controls for interactive search.
- **Ask first:** source-specific capabilities beyond this approved interactive contract; non-API support comes through the `browser` and `agents` modules.
- **Never:** change or delete a file of an `applied` row; read or collect site passwords or bypass per-person consent; log posting texts or files. A person's existing signed in browser is used only through the browser module's guarded profile and handoff path.

## Success criteria

1. Harald's 433 rows, their posting texts and their files are in the app on the Jetson, with status counts identical to the CSV, and the import is idempotent.
2. The scoring port matches the engine on every scorable row (golden test).
3. Explicit API sweeps add only new matching postings, scored; a user's schedule, once turned on, runs by itself. The model-driven Find jobs flow additionally requires the interactive search acceptance evidence above.
4. Filters, bulk actions, detail and map work on desktop and phone.
5. No user can see another user's jobs, files or sweeps (automated isolation tests).

## Open questions

None on the first website targets: the owner confirmed LinkedIn, Welcome to the Jungle, Indeed, Free-Work and custom URLs on 2026-10-24. The owner approved continuing implementation with fresh subagents and new draft PR work on 2026-10-24. Implementation and validation remain outstanding.
