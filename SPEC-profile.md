# Spec: profile

Module of [CAPABILITY-MAP.md](CAPABILITY-MAP.md). Depends on `identity`. `jobs` scores against it, `documents` and `agents` take every claim from it.

## Objective

Each user keeps, in the app, everything the engine today reads from `profile/` and hard-codes in `engine/assemble.py`. They edit it in the browser, and every later module reads it from here. It generalises the hard rules of `AGENTS.md`: the **locked facts** (employers, titles, dates, education, languages, contact) can only change here, never through a generated CV; the **fact base** is the only permitted source of claims for agents.

User stories:
- As Harald, I import my current `profile/facts.md`, `profile/skills.json`, `engine/boards.json` and the constants of `engine/assemble.py` once, and find everything in the app.
- As a friend, I start from an empty profile and fill it section by section, with a completeness indicator telling me what is still missing before search and CV generation can run.
- As a user, I edit any section and save; the app keeps every previous version, and documents later record which version they were built from.
- As a user, I tune my search: the roles I want, what to exclude, locations, contract types, remote stance, minimum fit score, which job websites to search (Find jobs; the company boards were deleted on 2026-10-28).

## Sections

One row per user in `profiles`, one JSONB document per section, validated by Pydantic models (the API rejects anything else).

| Section | Content | Replaces today |
|---|---|---|
| `identity` | full name, headline, email, phone, city and country, LinkedIn URL, website, photo (PNG or JPEG, max 2 MB, stored in Postgres), languages (name, level) | `assemble.py` NAME, CONTACT, LANGUAGES, `photo.b64` |
| `experience` | employers: key (slug, stable), organisation, title, start and end (a month or a year alone; end empty = present), location, contract (CDI, apprenticeship, freelance, internship); education: school, degree, start and end (a month or a year alone, both optional), location | `assemble.py` EMPLOYERS, EDUCATION |
| `facts` | Markdown fact base: what was built, positioning, approved wording, hard rules for writing; split in named blocks so agents can cite a block | `profile/facts.md` |
| `skills` | scoring model: `years_experience`, categories `have`, `adjacent`, `none` (each: name, terms), `gates` (name, terms, penalty) | `profile/skills.json` |
| `search` | title include terms, title exclude terms, location terms, unwanted location terms (`location_exclude`, owner 2026-10-27), contract types, remote stance (remote, hybrid, office, any), minimum fit to shortlist, posting age window in days, the job websites of Find jobs and the LinkedIn and Free-Work consent rows (`browser_sources`), never-apply rules (free text, the person's own, e.g. "No gambling: casino") ; what the person aims for (opportunities O4): seniority, industries, company types (a capped bonus to the fit, never a filter) and the pay sought | `engine/boards.json` |
| `answers` | standard application answers: work authorisation per country, notice period, earliest start date, salary policy, relocation and travel, motivation in the user's own words, how they heard, what to leave blank (EEO) | `facts.md` PRACTICALITIES |
| `preferences` | the language (`auto`, `en`, `fr`) and tone (`plain`, `formal`, `warm`) of the generated documents, and the first login wizard's `onboarding` dates, written by its routes only (opportunities O4, O5); every field has a default, so it always counts as filled | |

**Dates of employers and schools** (task P7, owner's decision of 2026-10-10). A start or an end is a month, `YYYY-MM`, or a year alone, `YYYY`, when that is all the user or the document says, with years from 1900 to 2100. It is stored as written (surrounding spaces removed, as for every field) and comes back character for character from a save, the history, an export and an import: nothing pads a year into a month, and no agent ever assumes one. Every section the month only model accepted stays valid, value by value and start with end, so no stored profile was migrated. The rules, in `profile.schemas` with a twin in `web/src/features/profile/period.ts`:
- **Refused:** anything else (`2021-3`, `21`, `2021-13`, `1899`, `2101`, a day, another separator, a number instead of text), with the sentence "must be a year or a month, written YYYY or YYYY-MM, between 1900 and 2100". An employer's start stays required.
- **End before start:** judged on periods, not on text, and only what is certain is refused. A start is read at its first possible month and an end at its last, so `2023-05` to `2023` and `2021` to `2021-03` are accepted, `2023` to `2022-12` and `2023-06` to `2023-05` are refused. One exception keeps old data valid: two months that were in order as text, which is how the month only model compared them, stay accepted; it differs from the order of the months only when a year is written in the digits of another script.
- **Order:** `period_key` is (year, month), with month 0 for a year alone. A year alone therefore sorts before every month of its year, and a list read newest first shows it after them. Two different values written in ASCII digits never share a key, so the order is stable (the api also takes a year in the digits of another script, as it always did, and that year shares the key of its ASCII form). Nothing in the app sorts by these dates yet: employers and schools stay in the order the user gave them.
- **Display:** `format_period` gives "2021" for a year alone and "March 2021" for a month. The editors and the History drawer show the stored text. **CV generation, when it exists, prints a year alone as the year, never as January of it.**
- **Durations:** no code computes one from these dates today (`skills.years_experience` is typed by the user). Code that will must not invent precision: it reads a start at its last possible month and an end at its first, and counts a period that comes out negative as zero (`period_span` gives both months).

Search terms are plain words or phrases, matched as whole words, case-insensitive; an "advanced" toggle accepts a regular expression, which is compiled and checked on save (bad pattern: 422 with the error position).

## Job title suggestions

Owner, 2026-11-05: "I want an agent to find the best job titles according to the profile", "The job titles agent refreshes by itself after a profile fill or a skills change, at most once a day", and on the ideas: "uploaded cv, the profile ... or just a chat with the agent to tell what we want to do is enough. ... he can read everything to help the user find best jobs", "More ideas" should "add to them". Code in `api/app/profile/titles.py` (rules, store, triggers), `titles_input.py` (what the agent reads), `agents.py` (the agent), `titles_router.py`; the chips and the chat in `web/src/features/profile/title-suggestions.tsx`.

- **The agent**, `profile_titles`: internal, private (its events hold sizes, never a title), on the model the documents writers use for each kind of credential, 3 turns, 60 s, a token budget of twice the high estimate for its first message, one tool `submit_titles` taking at most 8 `{title, reason, language}`. Its first message is one JSON block of at most 30,000 characters (`REQUEST_MAX`), every angle bracket escaped:
  - **the whole profile** (`titles_input.profile`, at most 10,000 characters): the identity's headline, city, country and languages; the employers (organisation, title, dates, place, contract) and schools; the fact blocks; the skills the person has, has adjacent to and lacks, the gates and the years; what the search aims for (unwanted places, contracts, remote stance, seniority, industries, company types, pay sought); the answers about work (work authorisation, notice, earliest start, salary policy and ranges, relocation, travel, on site or hybrid, the stated years and degree, the motivation). Lists are cut from their end, the fact blocks first and the skills and employers last;
  - **the documents' text** (`titles_input.documents`), as the app keeps it, never a file parsed again: the base CVs (their printed fields and target, `documents.content.text_fields`) and the uploads of Fill my profile whose text is read and not held by an unconfirmed secret warning; the default base CV first, then the uploads, newest first, then the other base CVs, an upload a read base CV was converted from read as that base CV; at most 6,000 characters each, 12,000 together, 4 documents. An upload goes only under the consent of Fill my profile, a base CV only under the documents module's (decision of 2026-10-10, explicit consent before documents go to Anthropic);
  - **the person's last 5 messages** (300 characters each, one line), the wanted and unwanted titles, the places (it answers in their language: French titles for French places), the never apply rules (40 of each list at most), the ideas shown and the ones dismissed.
  - **Left out**, as never helping choose a job and never to steer one: the EEO answers and their policy, every consent flag and row (background and reference checks, the job websites' consents), the settings of where and when to search (websites, feeds, schedules, documents), anything secret, and the name, e-mail, phone, links, photo, preferred name, pronouns, how they heard and what to leave blank (the consent texts promise the profile goes "without your contact details").
  The run executes in the worker (job `profile_titles` of the agents queue), its input holding a random key and the budget only. Tests run it on the fake streams `profile_titles*`.
- **The checks, by code**, before a title is stored and again on every read against the search saved then, first failing wins: a dash used as a separator in the title; not plain words (`plain_terms.PLAIN`), over 80 characters or 6 words; a language that is not a two letter code; a reason that is empty, over 120 characters or not plain (its dashes become commas first); already a wanted title (case, accents, plural aside); caught by an unwanted title (`profile.matching`) or one of the person's own never apply rules (`jobs.never_apply`); dismissed by the person ("Not for me"); twice in the answer or already shown; past 8 kept from one answer, or a full wanted list. The app has no rule of its own about a kind of role (owner, 2026-11-05): a security title is kept for a person without such a rule and refused for one whose rule names it. A search section that no longer validates gets no idea.
- **Runs and bounds.** A run needs a model credential and something to read: anything in the profile (any section), a document's text, or a message; one message alone, with an empty profile and no CV, is enough. A cron of the agents queue (every minute) starts one after a save of a section the agent reads (not the preferences), an accepted Fill my profile item, an upload or a base CV saved, never while an upload is still being read (30 minutes at most), at most once a day per person and only when what the agent reads changed since the stored answer; a trigger inside the day waits for the day to pass. "More ideas" and a message sent to the agent ask again on a click, 3 times a UTC day together (`titles.ASKS_PER_DAY`, one constant: the owner has not decided this number yet). One run at a time per person. The estimate follows what a run reads now: about 19,000 to 29,000 tokens for a short profile without documents, 28,000 to 43,000 for a full profile with a CV, 38,000 to 58,000 at the largest first message (USD 0.03 to 0.07, and 0.06 to 0.13, on an API key at the prices of `ingestion/pricing.py`), shown before the click on an API key; nothing about money on a subscription.
- **The store**, `profile_title_suggestions` (migration 0032): one row per person: the ideas kept, oldest first, at most 20 (`LIST_MAX`): a run's new ideas join after them, repeats removed (case, accents and plural aside), the oldest going past the cap; the titles the person dismissed (the last 30), which never come back; the person's last 5 messages, read by every later run, the automatic ones too. A page never costs a call.
- **The chips** under Job titles in the Search tab, Find jobs "What you want" and the wizard step "What you are looking for": "+ Analytics engineer" with its reason under it and a "Not for me" icon button beside it (hidden at once, kept away for good); a click adds it to the list being edited (the person still saves), and it leaves the suggestions; "More ideas". Under them a short chat: "Tell the assistant what you want to do" ("For example: move into product, stay in data, work remotely") and Send, one ask, its ideas joining the list; Enter sends it, never the page's form; the last message shows under the box with the count of the earlier ones and Clear, which forgets them all. States: nothing yet, in plain words (Add a model key; or "Add your CV, fill your profile or tell the assistant what you want to do", with the box ready; in the Find jobs dialog the same words without a link, which would drop the draft; in the wizard nothing until a key and something to read exist), on its way, failed with its reason and Try again, "No new title to suggest", and "No new idea this time" after a run that added nothing. A list of regular expressions gets no idea.
- **The place chip** under Places (Search tab, Find jobs, the wizard step "Where"): the profile's city, else its country, "+ Lyon, from your profile", while the list does not hold it. No model.

## About you

Owner, 2026-11-05: "except for the general infos, just put informations on a .md should be well rendered and editable", then "No, I want a markdown that a model interpret, not just a view over today's field", and "Always think about navigation and number of click". Code in `api/app/profile/about_text.py` (parts, writer, checks), `about.py` (store, Done, Apply, Undo), `about_agent.py` and `about_tools.py` (the agent), `about_router.py`; the page in `web/src/features/profile/about-document.tsx` and `profile-one-page.tsx`.

- **The document** is one Markdown text per person (at most 300,000 characters), the person's source for their experience, education, skills, facts and the prose of their answers. It is not a section: no revision, not in the export. A line `## Heading` outside a code fence starts a part; Experience, Education, Skills and Answers (and Expérience, Formation, Compétences, Réponses) are the employers, the schools, the skills the fit reads and the prose answers; any other heading is a fact block of that title. It saves as the person types (1 s after the last key, a version per save, 409 with the current text on another version).
- **The conversion, once, by code**: the first read writes it from the stored sections (`about_text.convert`, golden test on the owner's fixture shapes, invented data): a job is `### Title, Organisation` and a line "March 2021 to now · Lyon · CDI", a school `### Degree, School` with its line and details, the skills "Years of experience: 5" then a line "- name: term, term" per category under What I have, Close to what I have, What I do not have and Requirements that lower my fit ("- name, lowers the fit by 25: terms"), the answers one `###` heading each (Notice period, Earliest start, Work authorisation, Relocation, Travel, Motivation, How I hear about jobs), and each fact block a part of its own. The sections are unchanged until the person applies something. Two things are not written: regular expression categories and gates (kept as stored, "edit them in the classic editor"), and a level one or two heading inside a fact block, written level three (it would start a part).
- **Done** on a part asks the agent `profile_interpret` to read it as it was when Done was clicked: internal, private (its events hold sizes), on the documents writers' model for each kind of credential, 3 turns, 120 s, a token budget of twice the high estimate, one tool `submit_interpretation` (the fields of the part's kind, shape only), in the worker (job `profile_interpret` of the agents queue, its input a random key and the budget). Its first message is the part's kind, heading and text and what the profile holds of that kind now, every angle bracket escaped. A part with nothing in it needs no model. One at a time, at most 30 a UTC day (`about.DONES_PER_DAY`, one constant: the owner has not decided this number). Tests run it on the fake stream `profile_interpret`.
- **The checks, by code**, before anything shows; a value that fails is not taken and the stored one stays: a job or a school goes through Fill my profile's citation checker (`ingestion.citations.check_item`) against the lines the model quotes from the part (the organisation or school as whole words, the dates with the month next to the year, the numbers), then its title, degree and place must be written word after word in those lines and its contract named there; every other value (a skills term or name, a fact block, an answer) must be written word after word in the part, and a number (years, a penalty) must be a number of the part. A category whose name the text lacks takes its first term as its name. An employer keeps its `key` while its organisation is unchanged; a new one takes the slug of its organisation (a number added when taken), so CV bullets keep their job.
- **What the app understood**: under the part, the changes in a few lines ("1 job added: Intern at Example Labs, 2017 to 2018", "2 skills added to what I have: dbt, Airflow"), the values not taken and why, then Apply (one click) or Not now. Apply writes each changed section through `service.save_section`, one revision per section, so History and the rescore after a skills change work as for any save; it is refused (409) when the part was edited since Done or a section it writes changed since the reading. Locked facts (employers, schools) change only there. Undo puts back what each section held, while nothing changed them since.
- **In step**: each part keeps the digest of its text and of what the profile held of it when they last agreed. A part edited since says "Your profile does not have this text yet" with one Update my profile button; a part whose fields changed elsewhere (Fill my profile, the classic editor) says so with "Rewrite from my profile"; fact blocks no part holds are listed with "Add them".
- **Without a model key** the text still saves, and a line under Done says the app needs a key to update the profile from it, with a link to Account. On an API key the cost of a Done shows before it (`ingestion/pricing.py`); nothing about money on a subscription.

## Data model

```
profiles(user_id pk fk users, sections jsonb, photo bytea null, photo_mime text null,
         revision int, updated_at)
profile_revisions(id bigserial, user_id fk, revision int, section text, data jsonb,
                  created_at, unique(user_id, revision))
profile_title_suggestions(user_id pk fk users, suggestions jsonb, refreshed_at, basis text,
                          run_id fk agent_runs null, asked_at, auto_at, seen_at, asks_on date,
                          asks int, error text, dismissed jsonb, messages jsonb)
profile_about(user_id pk fk users, text, version int, parts jsonb, interpretation jsonb null,
              undo jsonb null, run_id fk agent_runs null, dones_on date, dones int,
              created_at, updated_at)
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
| GET /api/profile/title-suggestions | the job titles agent's ideas, checked against the saved search, what a run needs, the person's last messages, the clicks left, the estimate for what a run reads now; never calls a model |
| POST /api/profile/title-suggestions | "More ideas", or `{message}` to the assistant (300 characters at most, 422 past it): one run; 409 while one runs, without a key or anything to read; 429 past 3 a day; 503 when the queue is down |
| POST /api/profile/title-suggestions/dismiss | "Not for me" `{title}`: the idea leaves for good; 422 for a title that is not plain words |
| DELETE /api/profile/title-suggestions/messages | the assistant forgets what the person wrote |
| GET /api/profile/about | the About you document (written once from the profile), the state of each part, the estimate of a Done on each, the last reading and the last Apply; never calls a model |
| PUT /api/profile/about | `{version, text}`, the text as typed; 409 with the current document on another version; 422 past the size or for a control character |
| POST /api/profile/about/done | `{heading, version}`: read that part; 409 while one is read, without a key, for a stale version, a heading that is not a part or is twice in the text; 422 for a part too long; 429 past 30 a day; 503 when the queue is down |
| POST /api/profile/about/apply, /undo, /discard | write the last reading (one revision per section), put back what the last Apply replaced, forget the last reading; 409 when the part or the profile changed since |
| POST /api/profile/about/rewrite | `{heading?, version}`: write a part again from the profile, or add the fact blocks no part holds |

Importer for Harald: `./deploy.sh profile import <email>` runs on the Mac, converts `../profile/facts.md`, `../profile/skills.json`, the title and location regexes of `../engine/boards.json` (its boards are no longer imported, 2026-10-28) and the `assemble.py` constants into the export format, and posts it for that user through a CLI in the api container. Conversion is a pure function with its own tests.

## Frontend

`/profile` is one page, no tabs (product audit, chapter 2; owner, 2026-11-05: "Always think about navigation and nimber of click. App must be easy to use, syraight to the point"). Top to bottom: the photo, name and headline with "Ready for" (Scoring postings, Writing CVs, Filling forms, each with the one button that fixes it, from the completeness the api computes) in place of the completeness bar; Your CV (the CV card, its editor entry and actions, and "Add other documents" to Fill my profile); What you look for (the four search lists with the job title ideas, and "Websites and more in Find jobs"); About you (the document above, each part rendered by the safe renderer and edited in place with a live preview, a contents list sticky on desktop and chips on phones); Contact and links (the identity form). History (per section), Export, Import and the classic editor are under More. The safe renderer (`markdown.tsx`) reads tables too, and an underscore inside a word stays a character.

The classic editor, kept for one release so a rollback is a click: `/profile?tab=<section>` and `/profile/cv` show a completeness bar, unsaved-change warning on leave, Save per section, "History" drawer per section with view and restore. Identity: photo upload with preview and crop to square. Experience: sortable lists of employers and schools. Facts: Markdown editor with preview (CodeMirror 6 or a plain textarea with preview; pick the lighter), blocks as collapsible sections. Skills: category editor with term chips. Search: chips for terms, toggles for contract types. The board table and its board check (`POST /api/profile/boards/check`) were deleted on 2026-10-28 (owner); a stored `search.boards`, `search.feeds`, old reader rows or `onboarding.starter_handled` are ignored on read, left out of `GET /api/profile` and dropped on the next save, with no migration. Answers: form fields. Export and Import buttons. Mobile friendly at 375px.

## Commands

As platform, plus `./deploy.sh profile import <email>`.

## Project structure

```
api/app/profile/  models.py schemas.py router.py service.py importer.py cli.py
                  titles.py titles_input.py agents.py titles_tools.py titles_router.py
                  about_text.py about.py about_agent.py about_tools.py about_router.py
                  prompts/titles.md prompts/interpret.md
api/alembic/versions/0003_profile.py 0032_profile_title_suggestions.py 0034_profile_about.py
api/tests/profile/
web/src/features/profile/
web/e2e/profile.spec.ts profile-titles.spec.ts profile-about.spec.ts
```

## Testing strategy

- Unit: every Pydantic section model (valid, missing, wrong types, oversize, unicode), regex validation, completeness rules, the importer against the real files in `../profile` and `../engine` (golden output checked in as a fixture).
- Integration (real Postgres): each endpoint; isolation (user B never reads or writes A's profile, 404); revisions append and restore; 409 on stale revision with two concurrent saves; photo type sniffing (PNG, JPEG, a PNG renamed .txt, an SVG, a polyglot, 2 MB + 1 byte, empty); export then import round trip is identical; a profile that still holds the deleted keys validates, reads without them and saves without them.
- End to end (Playwright, locally and on the Jetson): fill a profile from empty as a new user, each tab, save, reload, history restore, unsaved-change prompt, two tabs editing the same section (409 shown with a reload option), photo upload, export and re-import; screenshots of every tab, light and dark, desktop and 375px.

## Boundaries

- **Always:** validate on the server; keep revisions; user scoping on every query.
- **Ask first:** changing a section's shape after real data exists (needs a data migration), letting agents write to the profile other than through what the person applies (the About you reading changes nothing until Apply, owner 2026-11-05).
- **Never:** let generated documents or agents change locked facts; log profile contents; serve one user's photo to another.

## Success criteria

1. Harald's import reproduces his facts, skills, search terms, employers, education, languages and contact exactly (golden test), and he sees them in the app on the Jetson.
2. A new user goes from an empty profile to 100% complete in the UI without touching JSON.
3. Every save is versioned and restorable; concurrent edits never silently overwrite.
4. No user can read or change another user's profile or photo (automated isolation tests).
5. All profile tests and the Playwright journey pass locally and on the Jetson.

## Open questions

None blocking; defaults above are proposed for approval.
