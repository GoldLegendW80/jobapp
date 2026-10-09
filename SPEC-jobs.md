# Spec: jobs

Module of [CAPABILITY-MAP.md](CAPABILITY-MAP.md). Depends on `profile`. `documents`, `agents` and `apply` act on its rows.

## Objective

Every user has their own job pipeline in the app: the postings found, their fit score against the user's profile, their status from `found` to `applied` and beyond, and notes. It replaces `data/jobs.csv`, the `data/jobs/<id>/` folders and the `jobs.html` dashboard, and the `find` and `score` commands of `engine/jobs.py`.

User stories:
- As Harald, I find my 433 existing rows in the app with their status, fit, competencies, gaps, notes, dates, posting text and the files I already sent (CV and letter PDFs, answers), imported once from `../data`.
- As a user, I see a dashboard: counts per status, a fit histogram, and a list I can search, filter (status, contract type, company, fit range, location, found date) and sort; one row opens a detail view with the posting text and everything attached.
- As a user, I select one or many rows and change their status or add a note in one action; the history of every change is kept.
- As a user, I add a posting by hand (URL, company, title, location, text) when it comes from a site with no API.
- As a user, I press "Find jobs": the app sweeps the company boards of my profile (Ashby, Greenhouse, Lever public APIs), keeps the postings matching my search terms, scores the new ones, and tells me what is new. Once I turn it on, it also runs by itself on my schedule (days, times, time zone); it is off until then.
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

- Backend fetches each enabled board of the user's `search.boards` with httpx (timeouts, retries with backoff, polite concurrency of 4, a User-Agent naming the app), parses Ashby, Greenhouse and Lever exactly as `engine/jobs.py fetch` does, filters with the user's include, exclude, per-board exclude and location terms and the days window, dedupes against existing rows, stores new ones as `found` with the posting text, scores them.
- Runs in the `worker` container (first use of Redis as a queue, with RQ or arq; the choice is recorded in the plan), one sweep per user at a time, progress visible in the UI, a summary at the end (boards ok and failed, postings seen, matches, new).
- Automatic sweeps per user, off by default: the user turns them on and picks weekdays, one to four local times and a time zone (`search.sweep_schedule`); a worker tick every 5 minutes starts each due slot once, daylight saving time handled.
- `never_apply` rules from the profile mark matching rows `skipped` with the rule as the note (hard rule 11 generalised).
- If an ATS blocks plain HTTP from the Jetson, that board is reported as failed; browser-based fetching comes with the `browser` module.

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

- **Always:** user scoping on every query; keep events; respect ATS rate limits and robots-free public APIs only.
- **Ask first:** adding a non-API source (LinkedIn, Welcome to the Jungle, APEC): that comes through the `browser` and `agents` modules.
- **Never:** change or delete a file of an `applied` row; scrape with credentials; log posting texts or files.

## Success criteria

1. Harald's 433 rows, their posting texts and their files are in the app on the Jetson, with status counts identical to the CSV, and the import is idempotent.
2. The scoring port matches the engine on every scorable row (golden test).
3. "Find jobs" sweeps the user's boards from the Jetson and adds only new matching postings, scored; a user's schedule, once turned on, runs by itself.
4. Filters, bulk actions, detail and map work on desktop and phone.
5. No user can see another user's jobs, files or sweeps (automated isolation tests).

## Open questions

None blocking; defaults above are proposed for approval.
