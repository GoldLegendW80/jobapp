# Spec: profile

Module of [CAPABILITY-MAP.md](CAPABILITY-MAP.md). Depends on `identity`. `jobs` scores against it, `documents` and `agents` take every claim from it.

## Objective

Each user keeps, in the app, everything the engine today reads from `profile/` and hard-codes in `engine/assemble.py`. They edit it in the browser, and every later module reads it from here. It generalises the hard rules of `AGENTS.md`: the **locked facts** (employers, titles, dates, education, languages, contact) can only change here, never through a generated CV; the **fact base** is the only permitted source of claims for agents.

User stories:
- As Harald, I import my current `profile/facts.md`, `profile/skills.json`, `engine/boards.json` and the constants of `engine/assemble.py` once, and find everything in the app.
- As a friend, I start from an empty profile and fill it section by section, with a completeness indicator telling me what is still missing before search and CV generation can run.
- As a user, I edit any section and save; the app keeps every previous version, and documents later record which version they were built from.
- As a user, I tune my search: the roles I want, what to exclude, locations, contract types, remote stance, minimum fit score, which company boards to sweep.

## Sections

One row per user in `profiles`, one JSONB document per section, validated by Pydantic models (the API rejects anything else).

| Section | Content | Replaces today |
|---|---|---|
| `identity` | full name, headline, email, phone, city and country, LinkedIn URL, website, photo (PNG or JPEG, max 2 MB, stored in Postgres), languages (name, level) | `assemble.py` NAME, CONTACT, LANGUAGES, `photo.b64` |
| `experience` | employers: key (slug, stable), organisation, title, start and end month (end empty = present), location, contract (CDI, apprenticeship, freelance, internship); education: school, degree, start and end, location | `assemble.py` EMPLOYERS, EDUCATION |
| `facts` | Markdown fact base: what was built, positioning, approved wording, hard rules for writing; split in named blocks so agents can cite a block | `profile/facts.md` |
| `skills` | scoring model: `years_experience`, categories `have`, `adjacent`, `none` (each: name, terms), `gates` (name, terms, penalty) | `profile/skills.json` |
| `search` | title include terms, title exclude terms, location terms, contract types, remote stance (remote, hybrid, office, any), minimum fit to shortlist, posting age window in days, boards (company, ATS ashby/greenhouse/lever, org slug, enabled, optional extra exclude terms), never-apply rules (free text, e.g. "no cybersecurity roles") | `engine/boards.json`, hard rule 11 |
| `answers` | standard application answers: work authorisation per country, notice period, earliest start date, salary policy, relocation and travel, motivation in the user's own words, how they heard, what to leave blank (EEO) | `facts.md` PRACTICALITIES |

Search terms are plain words or phrases, matched as whole words, case-insensitive; an "advanced" toggle accepts a regular expression, which is compiled and checked on save (bad pattern: 422 with the error position).

## Data model

```
profiles(user_id pk fk users, sections jsonb, photo bytea null, photo_mime text null,
         revision int, updated_at)
profile_revisions(id bigserial, user_id fk, revision int, section text, data jsonb,
                  created_at, unique(user_id, revision))
```

Every save of a section increments `revision` and appends one `profile_revisions` row; restoring an old version is a save of its data. Optimistic concurrency: a save sends the `revision` it was based on, and a stale one gets 409 with the current data (two tabs editing at once).

## API

All require a session and act on the caller's own profile only.

| Method and path | Does |
|---|---|
| GET /api/profile | all sections, revision, completeness per section |
| PUT /api/profile/{section} | replace one section; body `{base_revision, data}`; 422 on validation, 409 on stale revision |
| GET /api/profile/revisions?section= | list (revision, section, created_at) |
| GET /api/profile/revisions/{revision} | one past version |
| PUT /api/profile/photo, DELETE /api/profile/photo | upload or remove; type sniffed from bytes, not from the name |
| GET /api/profile/photo | the image, `Cache-Control: private` |
| GET /api/profile/export | the whole profile as one JSON file |
| POST /api/profile/import | that same JSON format back (all sections replaced, one revision each) |

Importer for Harald: `./deploy.sh profile import <email>` runs on the Mac, converts `../profile/facts.md`, `../profile/skills.json`, `../engine/boards.json` and the `assemble.py` constants into the export format, and posts it for that user through a CLI in the api container. Conversion is a pure function with its own tests.

## Frontend

`/profile` with one tab per section, a completeness bar, unsaved-change warning on leave, Save per section, "History" drawer per section with view and restore. Identity: photo upload with preview and crop to square. Experience: sortable lists of employers and schools. Facts: Markdown editor with preview (CodeMirror 6 or a plain textarea with preview; pick the lighter), blocks as collapsible sections. Skills: category editor with term chips. Search: chips for terms, toggles for contract types, board table with enable switches and an "add company" row that checks the board exists by calling the ATS public API from the backend. Answers: form fields. Export and Import buttons. Mobile friendly at 375px.

## Commands

As platform, plus `./deploy.sh profile import <email>`.

## Project structure

```
api/app/profile/  models.py schemas.py router.py service.py importer.py cli.py
api/alembic/versions/0003_profile.py
api/tests/profile/
web/src/features/profile/
web/e2e/profile.spec.ts
```

## Testing strategy

- Unit: every Pydantic section model (valid, missing, wrong types, oversize, unicode), regex validation, completeness rules, the importer against the real files in `../profile` and `../engine` (golden output checked in as a fixture).
- Integration (real Postgres): each endpoint; isolation (user B never reads or writes A's profile, 404); revisions append and restore; 409 on stale revision with two concurrent saves; photo type sniffing (PNG, JPEG, a PNG renamed .txt, an SVG, a polyglot, 2 MB + 1 byte, empty); export then import round trip is identical; board check against a mocked ATS (exists, 404, timeout).
- End to end (Playwright, locally and on the Jetson): fill a profile from empty as a new user, each tab, save, reload, history restore, unsaved-change prompt, two tabs editing the same section (409 shown with a reload option), photo upload, export and re-import; screenshots of every tab, light and dark, desktop and 375px.

## Boundaries

- **Always:** validate on the server; keep revisions; user scoping on every query.
- **Ask first:** changing a section's shape after real data exists (needs a data migration), letting agents write to the profile.
- **Never:** let generated documents or agents change locked facts; log profile contents; serve one user's photo to another.

## Success criteria

1. Harald's import reproduces his facts, skills, boards, employers, education, languages and contact exactly (golden test), and he sees them in the app on the Jetson.
2. A new user goes from an empty profile to 100% complete in the UI without touching JSON.
3. Every save is versioned and restorable; concurrent edits never silently overwrite.
4. No user can read or change another user's profile or photo (automated isolation tests).
5. All profile tests and the Playwright journey pass locally and on the Jetson.

## Open questions

None blocking; defaults above are proposed for approval.
