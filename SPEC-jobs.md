# Spec: jobs

Module of [CAPABILITY-MAP.md](CAPABILITY-MAP.md). Depends on `profile`. `documents`, `agents` and `apply` act on its rows.

## Objective

Every user has their own job pipeline in the app: the postings found, their fit score against the user's profile, their status from `found` to `applied` and beyond, and notes. It replaces `data/jobs.csv`, the `data/jobs/<id>/` folders and the `jobs.html` dashboard, and the `find` and `score` commands of `engine/jobs.py`.

User stories:
- As Harald, I find my 433 existing rows in the app with their status, fit, competencies, gaps, notes, dates, posting text and the files I already sent (CV and letter PDFs, answers), imported once from `../data`.
- As a user, I see a dashboard: counts per status, a fit histogram, and a list I can search, filter (status, contract type, company, fit range, location, found date) and sort; one row opens a detail view with the posting text and everything attached.
- As a user, I select one or many rows and change their status or add a note in one action; the history of every change is kept.
- As a user, I add a posting by hand (URL, company, title, location, text) when it comes from a site with no API.
- As a user, I press "Find jobs" and choose my websites and criteria for [interactive search](#interactive-search-626). Every search, manual or scheduled, runs the ticked websites; the automatic schedule (days, times, time zone) stays opt in, off until enabled. The API board sweeps (Ashby, Greenhouse, Lever) and the freelance feeds were deleted (owner, 2026-10-28).
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
job_feed_keys(user_id fk users cascade, feed text in ('adzuna'), secret_encrypted bytea, key_version int,
              last4 text, created_at, updated_at, primary key(user_id, feed))
sweeps(id uuid, user_id, started_at, finished_at, trigger enum(manual, schedule), status enum(running, done, failed),
       stats jsonb, error text)
```

Slug rule and duplicate detection reuse the engine's (`<company>-<role>`, lowercase, hyphenated; same URL or same company and title is the same posting). Status changes to `applied` fill `applied_on` once.

## Scoring

Port of `engine/jobs.py score_row` against the user's `skills` section, using `api/app/profile/matching.py` for term matching (P2 made it the single source of matching rules). Output: fit 0 to 100, competencies named, gaps. A manual fit override is kept (`fit_overridden`) and never replaced by a rescore unless the user asks. "Rescore all" after a profile change. A golden test proves the port gives the same fit, competencies and gaps as the engine on every row of `../data/jobs.csv` that has a posting.

## Sweep

A sweep reads the ticked alert e-mails ([SPEC-mail, "Job alert e-mails: a narrow exception"](SPEC-mail.md#job-alert-e-mails-a-narrow-exception-owner-2026-10-30), owner, 2026-10-30; their postings take the same commit path, filters, duplicates and scoring), then the ticked [job feeds](#job-feeds-2026-10-30), then the person's [company job boards](#company-job-boards), then the ticked job websites of [interactive search](#interactive-search-626) with the time left, and nothing else. The four share the sweep's one 15 minute deadline in that order: the alert e-mails stop at 120 s, the feeds at 180 s, the boards at 300 s after they start and never closer than 30 s to the deadline, the websites take the rest. The freelance feeds and the fixed browser readers (Google Careers, Business France VIE, company career pages, LinkedIn's own reader) stay deleted (owner, 2026-10-28: "Delete it"); the old board reader of that date (`api/app/jobs/ats.py`) and its starter list were rebuilt as the company job boards below (owner, 2026-10-30). Old sweep rows keep their board counts, readable in the history; a profile that still stores the old `search.boards` or `search.feeds` is read without them and saved without them.

- Each website's findings are deduped against existing rows, stored as `found` with the posting text and scored.
- Runs in the `worker` container (first use of Redis as a queue, with RQ or arq; the choice is recorded in the plan), one sweep per user at a time, progress visible in the UI ("Websites searched": a website counts once its phase is done), a summary at the end.
- Automatic sweeps per user, off by default: the user turns them on and picks weekdays, one to four local times and a time zone (`search.sweep_schedule`); a worker tick every 5 minutes starts each due slot once, daylight saving time handled.
- `never_apply` rules from the profile mark matching rows `skipped` with the rule as the note (hard rule 11 generalised). Sweeps and apply share `jobs/never_apply.py`: accents, case, compatibility forms, hidden characters and lookalike letters are folded, and a term at the start or end of a glued word also matches. The original title, company and rule note stay unchanged.

## Job feeds (2026-10-30)

**Owner decision, 2026-10-30 (asked through the orchestrator):** build Arbeitnow, Himalayas and Adzuna to replace Indeed, whose Cloudflare check blocks any automated access and which has no official search API. They are official job APIs read by the api, never a browser (`api/app/jobs/feeds/`, one module per feed and a small `FeedSource` protocol). The website search of Indeed stays as it is.

- **Setting.** Find jobs lists them under "Job feeds", ticked like the websites and saved with the search (`search.job_feeds`, at most the three, each once; an editor that omits the key keeps the stored value; stored only once one is ticked, as the release before refuses the key): "Arbeitnow (jobs in Europe)", "Himalayas (remote jobs open to France)", "Adzuna (jobs in France, needs a free key)". A sweep starts with a feed ticked and no website.
- **What each reads.** Arbeitnow `GET https://www.arbeitnow.com/api/job-board-api?page=N` (no key, newest first, 300 a page, no keyword search): pages back to the previous sweep's cursor (the newest `created_at` read, kept in its `stats.sources` entry, less an hour of overlap) or to `search.days`, at most 10 pages; the cursor is recorded only when the run ended with no error, every batch stored and the commits settled, so a 429, a failed page, the time budget or a refused store leave it where it was; reaching the 10 pages before the window's end ends the entry `page_cap` (outcome `partial`) and the cursor does not move either, so the next sweep pages back to the same point rather than skipping what this one could not reach. Himalayas `GET https://himalayas.app/jobs/api/search?q=<title>&country=FR&sort=recent&page=N` (no key): the first 3 saved titles (plain words, as the websites type them), up to 2 pages each, 6 requests at most. Adzuna `GET https://api.adzuna.com/v1/api/jobs/fr/search/<page>?app_id&app_key&what=<title>&max_days_old=<days>&results_per_page=50`: the person's own free key, the first 3 saved titles, up to 2 pages each, 6 requests at most; without a stored key it is skipped (`no_key`, outcome `not_attempted`, never an error); a refused key ends it `key_refused`.
- **Politeness.** One request at a time per feed, 3 seconds apart, at most 120 seconds per feed and 180 seconds for all the ticked feeds together (the company job boards and the websites read after them keep the rest of the 15 minutes), a User-Agent naming the app, `Accept-Encoding: identity` (a compressed answer is decoded anyway); a feed stopped by its time ends `time_budget` and keeps what it read before; a 429 stops that feed for the sweep (`rate_limited`), whatever Retry-After says; answers over 8 MB are refused. Requests bypass httpx's Client logging, as Adzuna's key is in the query.
- **Mapping.** Source (`arbeitnow`, `himalayas`, `adzuna`), external id (Arbeitnow's slug, Himalayas' guid, Adzuna's id, kept in the sweep event's evidence), title, company, location (Himalayas: "Remote" and its countries; Adzuna: its place and areas), remote flag (work mode `remote`), published date, address (https on the feed's own hosts only: Arbeitnow's page on one of its country sites, Himalayas' `applicationLink` unchanged, Adzuna's `redirect_url` without its tracking query), text (HTML read as lines; Adzuna gives a snippet), contract type when stated (Arbeitnow's job types, Himalayas' employment type, Adzuna's contract type), else read from the title as for every source.
- **Filtering**, after reading, as for the websites: a wanted title must match (a feed without one refuses, `unsupported_criteria`), no unwanted title or place may match, a wanted place must match for Arbeitnow and Adzuna (Himalayas is scoped to France), a saved place such as "everywhere", "worldwide" or "partout" means no place filter, a contract type outside the saved ones and a posting older than `search.days` are left out (unknown is kept). The counts left out per reason are in the entry (`left_out`).
- **Storing.** Through the run's commits as the websites' findings: deduplicated by address, then by employer, title and location, against the person's rows and the sweep's own; scored; never apply rules (the app's own against cybersecurity included) mark the row `skipped`. Each feed has one `stats.sources` entry: `kind` the feed, `mode` "api", `host`, `outcome`, `reason`, `found` (kept), `postings` (new), `duplicates`, `searches` and `searched`, `result_pages` (requests), `read`, `left_out`, `cursor` (Arbeitnow).
- **Adzuna's key.** Stored per person, encrypted like the model credentials (`job_feed_keys`, AES-GCM bound to the person and the feed), pasted on the Account page ("Adzuna key"); `GET /api/jobs/feeds` says whether one is stored (last four characters only), `PUT` (at most 10 saves per person in 15 minutes, as the model credential) and `DELETE /api/jobs/feeds/adzuna/key` save and remove it; it is never returned nor logged; `python -m app.agents.cli rotate` re-encrypts it with the new key, with the model credentials.
- **Attribution.** A posting's source shows "via Arbeitnow", "via Himalayas" or "via Adzuna" linking to its page there, as their terms ask; the sweep summary lists the feeds under "Job feeds".

## Company job boards

Owner, 2026-10-30 (reversing "Delete it" of 2026-10-28, asked through a question, jobs.ashbyhq.com named): the person's target companies, read through each ATS's official public job board API (`api/app/jobs/boards/`).

- **List.** `search.company_boards`: `{company, ats, board, enabled}`, at most 100, one per ATS and id (case aside). `ats` is `ashby`, `greenhouse`, `lever`, `smartrecruiters`, `workable` or `recruitee`; `board` is the id on that ATS (letters, digits, dot, dash, underscore; Recruitee's a lowercase DNS label). Never proposable from a document (`setting`). A save that leaves the key out keeps it (`KEPT_WHEN_OMITTED`). Absent means never saved: Find jobs shows the starter list ticked as a proposal (33 boards, `boards/seed.json`, each answered on 2026-10-30), marked "A starter list, not saved yet", and Save stores it only once the person chose it: changed it (tick, untick, Remove, add) or pressed "Keep these boards". A Save of other settings leaves the key out, so the profile keeps none. A sweep reads only a saved list, so no account calls an ATS before its person chose its boards. A sweep's frozen scope holds `company_boards` only when the list saved holds a board (no key for none or an empty list).
- **Setup.** Find jobs, "Company job boards": tick or untick a board, Remove, paste a board address (`https://jobs.ashbyhq.com/<org>`, `https://boards.greenhouse.io/<token>`, `https://jobs.lever.co/<company>`, a posting address under them, SmartRecruiters, Workable and Recruitee pages) and the api names its ATS and id (POST /api/jobs/boards/detect, no request to the ATS). Boards the person's own postings point to (their address, or an apply link in their text) are suggested (GET /api/jobs/boards/suggestions), never added by themselves. The EU hosts of Greenhouse and Lever and Teamtailor (whose API needs the employer's key) are not read.
- **Reading.** One request per board and sweep (two for SmartRecruiters' paging, 100 postings a page: a larger SmartRecruiters board is read up to its newest 200 postings, which the setup says), no retry; one request at a time per ATS with a 1.5 s pause after the previous one; a 429 stops that ATS for the sweep (later boards end `host_rate_limited`); 20 s per request, 25 MB per answer read into one buffer, one answer parsed at a time and let go once its board is stored; redirects only to the same host over https, with no port and no credentials (anything else, a malformed address included, ends the board `upstream_error`); the boards use at most 300 s of the sweep's 900 s. SmartRecruiters' list holds no description, so its postings are stored without text and get no fit.
- **Filters**, applied by code before the store: wanted titles (none saved: no board is read, `no_titles`), unwanted titles, wanted places (a saved "everywhere", "worldwide" or "partout" removes the check), unwanted places, the days window (an unknown date is kept), the saved contract types. A posting has one contract, the one filtered and the one stored: a word of its title, location or address naming a contract other than CDI ("Internship", "Intern", "Stage", "CDD", "Freelance", ...) wins; else the ATS's contract word when it maps ("permanent" is CDI; "Full time" and "Part time" are a schedule and map to nothing); else CDI. So an internship an ATS lists as FullTime or permanent is a Stage. Never apply rules and the app's rule against cybersecurity store a matching posting as `skipped`, as for every source. The remote stance is not checked (declared gap).
- **Store and stats.** Through the run's commits like the websites' findings: deduped by address or by company, title and location against the person's list and the sweep, the board's company name, the ATS's date, the API's text; `source` `<ats>:<board>` (joined with any other source that found the same posting). Each board is one entry of `stats.sources` frozen with the scope: `mode` "api", `kind` "board", `host` the API host, `query` `<ats>:<board>`, `company` the board's company name as saved (the summary names the board by it, by `query` for a sweep without it), `result_pages`, `listed`, `left_out` by reason, `found`, `postings`, `duplicates`, `skipped_by_rule`, `outcome`, `reason` (not_found, blocked, rate_limited, host_rate_limited, upstream_error, timeout, network, too_large, invalid_response, time_budget, no_titles). A search with boards and no website is a search.
- **Rollback (one way door).** The release before this one refuses a search section holding `company_boards` (`extra="forbid"`): after a rollback it answers 500 on that person's profile, and a queued sweep whose frozen scope holds the key fails `SEARCH_INVALID` when claimed. Exposure is kept small: the key is stored only when a person chose boards, and frozen only when the list holds one. Before a rollback by hand, name who would break with the read only queries `SELECT user_id FROM profiles WHERE sections->'search' ? 'company_boards';` and `SELECT id FROM sweeps WHERE stats->'search_scope'->'search' ? 'company_boards';`, then strip the key (the person's list is lost, so note it first): `UPDATE profiles SET sections = jsonb_set(sections, '{search}', (sections->'search') - 'company_boards') WHERE sections->'search' ? 'company_boards';` and, for sweeps still queued or running, `UPDATE sweeps SET stats = jsonb_set(stats, '{search_scope,search}', (stats->'search_scope'->'search') - 'company_boards') WHERE status = 'running' AND stats->'search_scope'->'search' ? 'company_boards';`.
- **Discovery.** No search engine: Google's Custom Search JSON API is closed to new customers and ends on 2027-01-01, Bing's Search API was retired, Brave's needs a payment card and bills by the request, and scraping result pages or a reseller of them is never done. No ATS publishes a directory of its boards. The suggestions above are the discovery.

## Interactive search (#626)

**Owner intent, 2026-10-24:** Find jobs should reproduce the owner's Claude Code workflow: the person chooses websites and search parameters, then an actual LLM uses Playwright to search those websites. The owner confirmed LinkedIn, Welcome to the Jungle, Indeed and Free-Work as the first targets, plus adding custom URLs. This is a changed product contract, not a claim that the deployed app implements it. Issue [#626](https://github.com/GoldLegendW80/jobapp/issues/626) records the request.

**Verified source baseline** (history: the fixed readers and `site.py` were deleted on 2026-10-28): at deployed commit `8ca71e67c3a2f205bb347bf7affa00e25af1d5bb`, `api/app/browser/finder.py` runs an internal model with navigate, snapshot, wait and report tools only. `api/app/browser/sources/site.py` supplies no search criteria. Google Careers, VIE and LinkedIn use fixed readers; the generic Search editor enables only Google Careers and VIE (`web/src/features/profile/editor-forms.ts`). This source audit does not explain any particular empty search.

**Approved behavior (owner, 2026-10-24):**

- Find jobs opens setup from Jobs before starting. The person selects websites or enters their URLs, sees which use model-driven browsing, and saves the selection per person. Every search runs the ticked websites alone (owner, 2026-10-28): the board, feed and fixed reader code was deleted, and their old saved settings are ignored on read and dropped on the next save. The summary counts the websites searched.
- Ticking a job website and saving is the person's permission to search it (owner, 2026-10-27, jobs J17): its risk sentence shows beside its box, the save records the consent through the platform's consent route at that sentence's version and the reviewed revision, and unticking it and saving takes it back. Its "Websites only" choice is replaced: every search, the automatic schedule included, runs the websites alone (2026-10-28).
- The person types the titles and places she wants and does not want as words, never as regular expressions (owner, 2026-10-28). A list saved as regular expressions before keeps filtering as it is; a website search types the plain words of each expression that converts safely (`api/app/profile/plain_terms.py`) and counts the others as left out.
- Setup reuses saved role or keyword terms, locations and remote preference, contract types, seniority, recency, optional pay and custom instructions. The person can review and edit them there. Criteria guide the model's queries, filters and reading, rather than merely filtering rows after browsing. Existing seniority scoring and pay rules remain those of [opportunities](SPEC-opportunities.md#owner-decisions-never-decide-them-again); no new hard exclusion is implied.
- Starting freezes the selected sources, their mode and the saved criteria for that run. Later profile edits affect the next run. The run shows which criteria each site applied and which were unavailable or could not be verified. Unknown pay, date, seniority or remote status is labelled unknown, not asserted to match. Found date and observed publication date remain distinct in this new path.
- The configured model runs through the existing agents credential/runtime boundary and the bound [browser tools](SPEC-browser.md#interactive-discovery-626). It navigates, types, clicks, selects and waits to use actual search boxes, filters and pagination, then opens posting detail pages. A fixed parser, API response or prerecorded result list alone cannot satisfy model-driven mode. Without a usable model or browser, that mode visibly refuses to start or reports the affected source blocked, with no substitute mode.
- Scope stays within the selected websites and code-enforced source/host boundaries, including observed, source-attributable posting destinations. Each picked site is attempted or gets a visible reason for refusal or deferral, including absent opt in, unsupported interaction or exhausted budget; a blocked posting destination also gets a reason. Normal posting-page browsing needs no repeated owner approval. No automatic general-web expansion or new source activation.
- One agent per website (owner, 2026-10-27, jobs J18): each chosen website is searched by one model run with all the saved criteria, the way the person would search it, running the searches it judges useful (usually one per wanted title, and per wanted place when the website has a place field) in one lease. It can type only the person's own wanted titles and places and set only the saved remote stance and contract type, never an unwanted title or place. Its budget per website is "no limit" (owner); the sweep's time is shared fairly among the websites. Postings merge once per address, and the unwanted titles and places and several contract types are applied after reading, before anything is saved. The summary names the searches it ran (each a saved title, in the saved place it chose) and counts them. A website's search takes at most 100 saved titles and 100 saved places (and 32 KiB in all): with more, the first ones that fit are searched, in saved order, and the summary says how many were left out.
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
| GET /api/jobs/boards/starter, POST /api/jobs/boards/detect, GET /api/jobs/boards/suggestions | the starter list of company job boards, the ATS and id of a pasted board address, boards the caller's postings point to |
| GET /api/jobs/places | coordinates for the map, from `engine/places.json` ported as a bundled gazetteer |
| POST /api/jobs/import | the import document used by the deploy command |

## Frontend

`/jobs` becomes the home page after sign in: KPI tiles per status, fit histogram, filters bar (chips, saved in the URL so a view can be bookmarked), virtualised table on desktop and cards on mobile, multi-select with a bulk action bar (status, note), "Find jobs" button with live progress and the summary, "Add a posting" dialog. `/jobs/:id` detail: header (company, title, location, contract, fit badge with competencies and gaps), status select, notes editor, posting text, files with preview for PDFs and download, events timeline. `/jobs/map`: world map with pins per city or country, a side list of unplaced locations (port of the Map view of `engine/dashboard.html`, its land outlines and gazetteer bundled). Design follows the existing shadcn shell; light and dark; 375px.

## Commands

As before, plus `./deploy.sh jobs import <email>`.

## Project structure

```
api/app/jobs/  models.py schemas.py router.py service.py scoring.py sweep.py importer.py places.json world.json
api/app/worker.py            queue worker entry point (new `worker` service in compose.yml, 1536m budget already reserved)
api/alembic/versions/0004_jobs.py
api/tests/jobs/
web/src/features/jobs/
web/e2e/jobs.spec.ts
```

## Testing strategy

- Unit: slug and duplicate rules; scoring port golden test against the engine on the real CSV; filter terms; never_apply rules; importer mapping of every CSV column and every file type.
- Integration (real Postgres and Redis): every endpoint, isolation (user B sees none of A's rows, files, sweeps), bulk in one transaction, concurrent edits, sweep against a fake website search (`api/tests/jobs/sweep_helpers.py`), one sweep per user at a time, idempotent import, applied files immutable, a scheduled slot enqueues once (daylight saving time included).
- End to end (Playwright, locally and on the Jetson): Harald's import shows 433 rows with the same status counts as the CSV; filters and URL state; bulk status change; detail with PDF preview; add by hand; a website search against fake pages locally; map pins; screenshots light and dark, desktop and 375px.

## Boundaries

- **Always:** user scoping on every query; keep privacy-safe events; respect browser pacing, consent and checkpoint controls for interactive search.
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
