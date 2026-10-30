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
sweeps(id uuid, user_id, started_at, finished_at, trigger enum(manual, schedule), status enum(running, done, failed),
       stats jsonb, error text)
```

Slug rule and duplicate detection reuse the engine's (`<company>-<role>`, lowercase, hyphenated; same URL or same company and title is the same posting). Status changes to `applied` fill `applied_on` once.

## Scoring

Port of `engine/jobs.py score_row` against the user's `skills` section, using `api/app/profile/matching.py` for term matching (P2 made it the single source of matching rules). Output: fit 0 to 100, competencies named, gaps. A manual fit override is kept (`fit_overridden`) and never replaced by a rescore unless the user asks. "Rescore all" after a profile change. A golden test proves the port gives the same fit, competencies and gaps as the engine on every row of `../data/jobs.csv` that has a posting.

## Sweep

A sweep runs the ticked job websites of [interactive search](#interactive-search-626) and nothing else. The API board reader (Ashby, Greenhouse, Lever posting APIs, `api/app/jobs/ats.py`), the freelance feeds, the fixed browser readers (Google Careers, Business France VIE, company career pages, LinkedIn's own reader) and the starter boards list were deleted (owner, 2026-10-28: "Delete it"). Old sweep rows keep their board counts, readable in the history; a profile that still stores `search.boards` or `search.feeds` is read without them and saved without them.

- Each website's findings are deduped against existing rows, stored as `found` with the posting text and scored.
- A ticked LinkedIn is searched through [LinkedIn's public job search](#linkedins-public-job-search-owner-2026-10-30) by default, without the browser, or by the browser agent when the person chooses it in setup.
- Runs in the `worker` container (first use of Redis as a queue, with RQ or arq; the choice is recorded in the plan), one sweep per user at a time, progress visible in the UI ("Websites searched": a website counts once its phase is done), a summary at the end.
- Automatic sweeps per user, off by default: the user turns them on and picks weekdays, one to four local times and a time zone (`search.sweep_schedule`); a worker tick every 5 minutes starts each due slot once, daylight saving time handled.
- `never_apply` rules from the profile mark matching rows `skipped` with the rule as the note (hard rule 11 generalised). Sweeps and apply share `jobs/never_apply.py`: accents, case, compatibility forms, hidden characters and lookalike letters are folded, and a term at the start or end of a glued word also matches. The original title, company and rule note stay unchanged.

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

## LinkedIn's public job search (owner, 2026-10-30)

**Owner decisions, 2026-10-30:** "use api to search but keep browser to apply", then, asked again, "Both, switchable in setup". A ticked LinkedIn is searched one of two ways, chosen per person in Find jobs setup under the LinkedIn box (`search.linkedin_search`):

- **"Search with LinkedIn's public job search (no login)"**, `api`, the default for everyone, a search saved before the setting included. Plain HTTP requests to the endpoints LinkedIn's own signed out job pages call, no lease of the person's browser, no model (`api/app/browser/sources/linkedin_guest.py`, mode `api` in the sweep's sources).
- **"Search with the browser, signed in"**, `browser`: the finder agent of [interactive search](#interactive-search-626), unchanged (mode `llm_browser`).

Easy Apply always uses the browser, whatever the choice; the LinkedIn box still gives the one LinkedIn permission (search and Easy Apply), and its risk sentence is unchanged. Starting a sweep freezes the mode with its sources (`sweep.freeze_scope`): a later change of the setting affects the next sweep. The setting is kept when an older editor or an import leaves it out (`KEPT_WHEN_OMITTED`); no migration (it lives in the profile's JSON search section).

What the public job search does, per sweep:

- **Searches.** `GET https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search` returns an HTML fragment of up to 10 job cards. One search per saved title (its plain words, as the browser agent types them) and place: with no saved place, or a place that says no limit ("everywhere", "worldwide", "partout"), `location=Worldwide&geoId=92000000`; any other place as written, without a geoId. `f_TPR=r<days * 86400>` from the saved days. Contract types: one `f_JT` filter with CDI as F (full time), CDD as T (temporary), Freelance as C (contract) and Stage as I (internship). Alternance has no LinkedIn code: with it saved, no filter is sent and each posting read is checked against the saved types. CDD saved without CDI sends no filter either and is checked the same way: LinkedIn lists most fixed term contracts as full time, so "temporary" alone would miss them. VIE has none either: with VIE saved, each title is also searched as "<title> VIE", checked the same way; with VIE alone, only those searches run. At most 30 searches, 3 pages each (`start=0, 10, 20`), in rounds (the first page of every search, then the second); a search ends at a page of fewer than 10 cards or with no new card.
- **Filters before any detail request:** the saved titles to include (LinkedIn's relevance is loose: "genai engineer" also returns "Senior Algorithm Engineer"), the unwanted titles and places, the never apply rules and the app's own rule against cybersecurity, and the person's postings (same LinkedIn id or address: counted found and duplicate, never fetched again). A card a never apply rule matches is dropped there and counted in `unwanted`, never fetched and never stored, unlike a posting of the browser's search, which is stored `skipped` with the rule as its note.
- **Details.** `GET https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/<id>`, only for a new card that passed the filters, 5 after each page and the rest at the end while the budget holds. The description and the criteria (seniority, employment type, job function, industries) are the posting's text, scored as any other; the employment type gives the contract type when the title states none (Internship: Stage, Temporary: CDD, Contract: Freelance). Stored through the run's commits as `https://www.linkedin.com/jobs/view/<id>/`, source `www.linkedin.com`, as the browser's LinkedIn postings are.
- **Pace and caps.** One request of LinkedIn's host at a time across people and workers, 8 to 12 seconds after the previous request or browser page of the host ended (the shared per host pacer, key `www.linkedin.com`). At most 30 requests a sweep, within the source's fair share of the sweep's 900 s. A proof of concept on 2026-10-30 got HTTP 429 for a second request 4 s after the first, and none with 8 s between pages and 6 s between postings; one page was also read from the Jetson.
- **Stops.** HTTP 429 or 999, or a redirect to a sign in or security page, ends LinkedIn for this sweep at once with the reason `linkedin_rate_limited` ("LinkedIn asked the app to slow down, so its search stopped for this time."); what was stored stays, nothing is retried. It also starts a cooldown of 30 minutes shared by every sweep of every person (a Redis key per host, `jobapp:browser:linkedin-guest-cooldown:<host>`, holding the time it ends): before every request the key is read, and while it lives the source ends `linkedin_rate_limited` without a request. It records no LinkedIn checkpoint: the requests carry no account, so the 7 day pause of the person's LinkedIn work (and of Easy Apply) is not started by an anonymous refusal. Another unexpected answer ends it `page_failed`, as does a request that takes more than 30 s in all; a posting gone (404) is skipped; a page of list items with no card ends it `layout_changed`. A second LinkedIn address in the same sweep is not searched twice (`linkedin_searched_once`).
- **Permission, kept conservative.** Before every request: the LinkedIn switch at its accepted sentence and the 7 day pause (`linkedin_guard`, through `platform_policy`), as before every browser page, although the anonymous requests cannot harm the account. Refused before the first request, the source ends `permission_unavailable`; withdrawn after it, `permission_revoked`, as a browser search says. A sweep of the public job search opens no page of the person's browser, so it is not the "last sweep" of the LinkedIn card and does not clear its sign in notice (`linkedin_guard`).
- **Requests.** A desktop browser User-Agent, `Accept-Language: en-US`, `Accept-Encoding: identity`, 20 s timeouts and 30 s in all, no redirect followed, no cookie ever sent back, at most 2 MB read of an answer (a gzip answer is decoded in steps within that bound), from the worker's own network with no proxy.
- **Parsing.** The standard library's HTML parser, in a thread so the worker's other jobs go on, on markup defused first (an unclosed tag, comment or marked section is read as text, since the parser would scan to the end of the answer from each one), within bounds: 50,000 elements, 256 open elements deep, an end tag closes one of the 16 innermost open elements, at most 50 elements with a job id looked into, each field looked for within 400 elements, 10 criteria items. A hostile answer of 2 MB parses in about a second.
- **Summary.** Its entry in `stats.sources`: `kind` `linkedin`, `mode` `api`, `host`, `phase`, `outcome`, `reason`, `found`, `postings`, `duplicates`, `result_pages` (search pages read), `searches` and `searched` (`{title, place}`), `details`, `requests`, `unwanted`, `other_contract`. The history lists it among the job sites.

**Terms of use.** LinkedIn's User Agreement forbids automated access, these endpoints included; the owner accepted that risk in the LinkedIn checkbox. The public job search carries no account and no cookie: the worst case is a block of the Jetson's address, not a restriction of the person's profile, a better risk than the signed in browser search.

**Tests.** Recorded fragments and an `httpx.MockTransport` only (`api/tests/browser/test_linkedin_guest.py`, `test_linkedin_guest_store.py`): no test reaches LinkedIn. Under `BROWSER_SOURCES_BASE_URL` (tests only) requests go to `<base>/linkedin/...` and postings are stored there; a runner without an HTTP transport makes no request.

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
