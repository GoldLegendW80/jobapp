# Spec: ingestion

Module of [CAPABILITY-MAP.md](CAPABILITY-MAP.md). Depends on `agents` and `profile`. Intent: [agent-features.md](../docs/intent/agent-features.md).

## Objective

Filling a profile by hand is long and repetitive. Ingestion lets an agent do it from what the user already has, while the user keeps the last word: the agent proposes, the user accepts.

User stories:
- As a user, I open "Fill my profile with an agent". The app shows a ready-to-copy prompt for Claude Code or Codex that analyses my laptop (repositories, documents, CVs, project notes, installed tools) and writes a recruiter-oriented dossier as Markdown files (who I am, employment, education, what I built with evidence, skills with proof, positioning, practical answers), the way Harald did for himself (`profile/facts.md`, `profile.md`, `projects-deep-dive.md`).
- As a user, I paste the dossier into the app, or upload it, together with any other documents: CVs and cover letters (PDF, DOCX), plain text and Markdown, LinkedIn exports, screenshots (PNG, JPEG).
- As a user, I start the extraction. The agent reads everything and proposes changes to each profile section (identity, experience, facts, skills, search, answers). I see each proposal as a diff against my current profile, with the source of every claim (file name and excerpt), and accept, edit or reject it per section or per item.
- As a user, I can upload more documents later and run it again; it only proposes what is new or different.

## Rules

- **Nothing beyond the documents.** Every proposed item cites at least one source excerpt; an item without a valid citation is refused to the agent with the reason and never stored (a retry budget per step bounds the attempts, and the refused count is shown to the user). Every number in a proposed value (metric, team size, date, count) must appear in its cited excerpts. A date is proposed as the document writes it: a year alone stays a year, never an assumed month (the profile accepts `YYYY` and `YYYY-MM`). `skills.years_experience` is never proposed by the agent: the merge computes it in code from the employment dates, never rounded up. This generalises hard rule 1 of `AGENTS.md`.
- **Locked facts** are, as in SPEC-profile: identity contact fields and languages, and all of experience (employers, titles, dates, locations, education). An ingestion item that touches one is never part of "Accept all"; it must be accepted on its own. A locked item the user accepts counts as a user edit, which is consistent with SPEC-profile's "never let agents change locked facts": the agent only proposed it.
- **Proposals, never direct writes.** Accepting goes through the normal profile save (revision, 409 rules).
- **Conflicts are shown, not resolved silently**: when two documents disagree (two different start dates), the item shows both values with their sources and the user picks.
- **Documents are data, not instructions.** The extraction agent's system prompt says so explicitly; text inside a document asking the agent to do something is ignored and, if detected, mentioned in the run summary.
- **Owner only.** Documents, their text, runs and proposals are visible only to their owner.

## Laptop-analysis prompt

A versioned prompt template shipped with the app (`api/app/ingestion/prompts/laptop_dossier.md`), filled with the user's name and output folder, copyable in one click, with short instructions for Claude Code and for Codex. It tells the coding agent:
- **Scope:** only the folders the user lists at the top of the prompt (placeholders to fill), read-only except for the output folder, no network, no git push, no installs.
- **Never read:** `~/.ssh`, `~/.aws`, `~/.gnupg`, `.env*` files, key and certificate files, keychains, password managers, browser profiles, mail and messages, photos, other people's personal data.
- **Output:** a fixed set of Markdown files with fixed headings matching the profile sections (identity, experience, facts, skills, search preferences, application answers, open questions), file paths cited as evidence, no invented numbers.
- **Finish:** run `gitleaks detect --no-git --source <output folder>` if available, and remind the user to read the dossier before uploading it.

The page states plainly that Claude Code or Codex sends what it reads to its own model provider, under the user's own account. On upload, text matching common secret patterns (the gitleaks default rules, ported as a small regex set) sets the document's `secret_warning = true`; the user must confirm (`POST /api/ingestion/documents/{id}/confirm`) or delete it, and extraction refuses documents with an unconfirmed warning (422 naming them).

## Documents

```
ingestion_documents(id uuid, user_id fk users on delete cascade, name text, mime text, size int,
                    sha256 bytea, bytes bytea null, text text null,
                    text_source enum(parsed, transcribed, pasted),
                    text_status enum(pending, awaiting_transcription, done, failed),
                    note text null, truncated bool default false,
                    pages int null, pages_text int null, pages_image int null,
                    transcribed_pages int[] null,
                    parser_version smallint,
                    failed_reason text null, failed_kind enum(file, server) null, attempts smallint,
                    secret_warning bool default false, secret_confirmed_at null,
                    pending_since null,
                    created_at, unique(user_id, sha256), unique(id, user_id))
ingestion_images(id, document_id, user_id fk cascade, position int, jpeg bytea,
                 width int, height int, text text null, unique(document_id, position),
                 foreign key (document_id, user_id) references ingestion_documents (id, user_id)
                 on delete cascade)
ingestion_consents(user_id pk fk cascade, text_version int, accepted_at)

ingestion_runs(id uuid, user_id fk cascade, document_ids uuid[], base_revision int,
               status enum(running, paused, done, failed, cancelled), partial bool,
               paused_reason text null, waiting text null, deadline_at, cancel_requested_at null,
               estimate_low bigint, estimate_high bigint, token_ceiling bigint,
               tokens_spent bigint, ceiling_history jsonb,
               created_at, finished_at,
               unique(user_id) where status in (running, paused))
ingestion_run_steps(id, ingestion_run_id fk cascade, position int, kind enum(transcribe, extract),
                    document_id uuid, sha256 bytea, part int, range_start int, range_end int,
                    image_positions int[], identity_hash,
                    status enum(planned, running, done, failed, cancelled, reused),
                    agent_run_id fk agent_runs null, attempt smallint, token_budget bigint,
                    refused_count int, two_digit_year_count int, items_count int,
                    purge_events bool, unique(ingestion_run_id, position))
ingestion_items(id, user_id fk cascade, document_id fk cascade, step_id fk cascade, section, key,
                action, value jsonb, value_hash, citations jsonb, flags text[],
                unique(step_id, section, key, value_hash))
```

The first three tables are one migration (task G1), the last three another (task G9); each holds every column a later task stores into, so no other ingestion task adds a migration. **Pages:** `ingestion_documents.text` holds the pages of a PDF in order, joined with a form feed (U+000C); a page that waits for its transcription is the empty string between two form feeds; `ingestion_images.position` is the zero based page index of a PDF page, and 0 for an uploaded image. `note` says what was not read on a document that is not failed ("pages 31 to 44 were not read"), and `pages_text` and `pages_image` are shown per document. `transcribed_pages` keeps the page indexes whose text came from a transcription, since the images themselves are deleted once transcribed. `pending_since` is when the document last started to wait for its reader (upload, replacement, a new try): a replaced row keeps its `created_at`. An image is tied to the user of its document by the foreign key on `(document_id, user_id)`.

As planned against the merged runner (tasks/ingestion/todo.md): the runner executes one SDK session per `agent_runs` row with fixed limits and has no sub-runs, so an ingestion run is a chain of steps, each an ordinary agent run, and the merge is code. Steps store checked candidates in `ingestion_items`; the merge turns them into proposals.

- **Accepted types:** PDF, DOCX, TXT, MD, JSON, CSV, PNG, JPEG, ZIP (LinkedIn data export only: the CSV files inside are unpacked as separate documents, other entries ignored), and a pasted text box.
- **Limits:** 20 MB per file (enforced as 20 MiB; the edge limit of `caddy/Caddyfile` is raised to that for the upload route only and stays 10 MiB elsewhere), 100 MB per user (live documents only), 200 pages per PDF read as text and 30 pages per PDF rendered as images, 500,000 characters of text per document, 100,000 characters per paste; per run at most 30 documents, 1,500,000 characters of text and 60 images or rendered pages sent to the model. Over a limit: 413 or 422 with the limit named.
- **Parser safety:** DOCX and ZIP opened with uncompressed-size and entry-count caps (zip bomb guard, 50 MB uncompressed, 200 entries), PDFs parsed with a page cap and a time budget, images opened with the same pixel guard as the profile photo (25 MP) and normalised to a JPEG of at most 1568 px and 300,000 bytes before sending. Every parser and every image decode runs in a **separate `parser` compose service** (decided by Harald on 2026-10-10; tasks/ingestion/todo.md, "The parser service"): the worker's image under its own unprivileged uid, in its own PID namespace, with no secret in its environment, on an internal network it shares with the worker alone, so it reaches neither Postgres nor Redis nor the internet. The worker sends it the bytes and writes the result to the database itself. Inside it, each file is parsed by a child process under resource limits and a seccomp deny list. Nothing is parsed in the api or in the worker.
- **Text extraction:** PDF text layer and DOCX text with pure-Python libraries (`pypdf`, `defusedxml`, recorded in the plan). Images are **transcribed by the model** in the first steps of the run: an app tool hands the agent one stored image as an image block (no built-in file tool; the path through the real Claude Code CLI was verified on 2026-10-10, `tasks/evidence/ingestion-g0/`), the agent stores the transcription through a write tool bound to that image, and it becomes the document's `text` with `text_source = transcribed`. **A PDF page without a text layer is rendered to an image with `pypdfium2`** inside the parser service (decided by Harald on 2026-10-10), then transcribed like a screenshot; pages that have text keep it. A document's `bytes` are set to null once its text or its images are stored, and its images are deleted once transcribed. Citations into transcribed text are checked against the transcription and flagged "transcribed, check it" in the review.
- **Dedupe** by `unique(user_id, sha256)`: per user only, never revealing another user's uploads. An upload that matches a document whose extraction failed replaces it and is processed again, and so does one that matches a document an older parser version left with a note.
- **Retention:** a document's text is kept until the user deletes it (decided 2026-10-10). The server's backups keep 7 nightly dumps, so a deleted document leaves them within 7 days; the delete dialog says so.
- **Deletion is a hard delete:** removing a document deletes its row (bytes and text gone at once) and, in the same transaction:
  - strips its excerpts from proposals of **every** status; a pending item left without any citation is deleted; accepted items keep only "source deleted" in place of the excerpt (the accepted value is the user's own profile now); rejected and superseded items keep only their section, key and value hash (needed so re-runs do not re-propose them);
  - deletes every `agent_events` row of the steps that read the document (the model's own words can quote it; tool arguments and results are never stored for these runs); a step still running is cancelled and its events are deleted again once it has ended; the run and step rows stay with ids, status, tokens and cost;
  - runs' `input` and `output` only ever hold document ids, never text; the UI shows ids of deleted documents as "deleted".
- **No transcripts outside the database:** as built in A2, every agent run has its Claude Code home and configuration directory inside its scratch directory on the worker's tmpfs, `CLAUDE_CODE_SKIP_PROMPT_HISTORY` stops transcripts and prompt history (the Python SDK has no session persistence option), and the scratch directory is deleted when the run ends. Ingestion writes no document into it. Nothing about a run is left in the worker's home directory.
- Deleting the account cascades to documents, runs, events and proposals.

## Extraction agent

- Runs on the `agents` runtime with the user's credential. **Model by credential** (decided 2026-10-10): `claude-opus-5-5` on a subscription credential, `claude-sonnet-5-5` on an API key credential, chosen automatically. One agent run per step: a transcription step per range of at most 8 images, an extraction step per document part of at most 100,000 characters (40 turns and 15 minutes each, 60 minutes for the whole ingestion run), then a merge done in code. No built-in tool at all. Each step is bound in code to one document part, so no tool takes a document id: the extraction agent has `read_document(offset)`, `get_profile_section` and one write tool `propose_profile_changes(section, items[])` with a strict schema; the transcription agent has `view_image(position)` and `store_transcription(position, text)`. Both agents are internal: they cannot be started through `POST /api/agents/runs`. Each item has:
  - a **stable key**: the employer `key`, the school name and start, the facts block `title`, the skills category `name`, the board `org`, the answers field name; never an array index;
  - the action (`add`, `replace`, `remove`), the proposed value (validated against the section's Pydantic model before it is stored), and one or more citations `{excerpt}` into the step's document (code attaches the document id).
- **Citation check** (server side, before storing; the normative tables are in tasks/ingestion/todo.md, "Citations", and were executed on 108 examples, `tasks/evidence/ingestion-g0/citation_rules.py`): the excerpt and the document text go through the folding of curly apostrophes, quotes and dashes, the removal of invisible characters (soft hyphen, zero width and bidi controls), NFKC, case folding and whitespace collapsing, in that order; the match then ignores spaces and hyphens on both sides, which handles hyphenation at line breaks, and the cited span is extended so that it never cuts a word or a number. The excerpt must be 20 to 600 characters (or the whole document, if shorter). Values are then checked against the cited text by type:
  - **emails and URLs**: the value must be written in an excerpt exactly, hyphens included, and bounded on both sides, so a part of an address or a neighbouring address never passes;
  - **names** (employer organisation, school, language name, link label, board): the value must appear in an excerpt as whole words;
  - **dates** (`YYYY` or `YYYY-MM`): the four digit year must appear; a month is accepted only when the excerpt holds it next to its year, as a number or as an English or French month name or abbreviation, whole words only (`2023-01` matches "January 2023", "janv. 2023", "01/2023", "Jan to Mar 2023"); a month belongs to the year written after it, never to one written before it, and nothing pairs across a line break; a day and month that cannot be told apart ("03/04/2023") give the year alone; **a two digit year is never read** ("09/21", "Jan '23"): the date is refused and the user is told, unless another citation of the same item gives the four digit year;
  - **phone numbers**: the digits must be those of a whole phone number of the excerpt, as the document writes it;
  - **other numbers** in a value (metrics, counts, sizes): each must appear in an excerpt, read with its separators ("10 000" is 10000, "3,5" is 3.5); a number word does not support a digit;
  - **settings** (a gate's penalty, the minimum fit, the posting age window, a board's enabled switch, the sweep schedule, regular expressions) are never proposable by the agent, and search terms are not checked for numbers;
  - **`skills.years_experience`** is never proposed by the agent: the merge computes it from the employment dates (non-overlapping periods, a year alone read at its shortest, one decimal, never rounded up) and proposes it as a derived item only when the profile has no value.
  Failing items are refused: the tool answers the agent with the item's index and the rule it broke, nothing is stored, and the refusal is logged with ids and the rule only, never the excerpt or value. A budget in code bounds the retries (20 refused items per step, and no third attempt at the same key), and the count of refused items is shown per document.
- **Tokens:** before a run the start dialog shows what will be read, the model, a range of tokens from a low to a high estimate, and for API key users a cost range; the formulas, their assumptions and worked figures are in the plan ("Tokens"). **A run pauses when it has used twice the high estimate shown** (decided by Harald on 2026-10-10); an explicit Continue by the user raises that ceiling by one more high estimate and is recorded. This applies to every user, subscription included, and is enforced in code between steps and inside a step (each step's budget is what remains of the ceiling). It is a technical bound in tokens with the user's hand on it, not the spending cap SPEC-agents decided against.
- **Consent:** the first start asks for explicit consent (a checkbox, recorded with the date and the version of the text shown) to sending the selected documents to Anthropic through the user's own credential; later starts show a one line reminder; a new version of the text asks again.
- **Failures:** a step that fails on the credential or the provider pauses the run, which the user resumes; a step that fails on its own content (a request the API refuses with 400, 413 or 422 included) is reported and the chain goes on; at 60 minutes what finished is merged and the rest reported (the plan's "Failure rules").
- **Large inputs:** documents are read in chunks of at most 30,000 characters; transcription, then extraction per document part, then a merge in code that dedupes and detects conflicts.
- **Skills and search:** proposed advanced terms must pass `check_regex`; the `none` categories, gates and search preferences are proposed only when a document states them.
- **Re-runs:** the run compares against the current profile and against items the user rejected before (by section, key and value hash): rejected items are not proposed again unless their value changed. Pending items from an older run are marked superseded when a newer run starts.
- At most **one active ingestion run per user**, enforced by a partial unique index (409 with the active run's id otherwise). A paused run is active.

## Proposals mapping

Ingestion uses the `proposals` table of SPEC-agents as built in A3 (migration 0008: nullable `target_id` and `run_id`, statuses `superseded`, `invalid` and `undone`, `decided_by`, `reason`, optional `edited_value` on accept) with `target = "profile"`, `target_id = null`, `run_id = null` (the merge is not an agent run), `kind = "profile_item"`, one row per item, and `payload = {ingestion_run_id, section, key, action, value, value_hash, citations[], base_revision, flags[], conflict?}`. As merged, `proposals.accept` only applies the two job kinds and `proposals.create` supersedes only when `target_id` is set: ingestion adds the dispatch on `target` and supersedes its own older items. Applying items:
- `POST /api/proposals/{id}/accept` takes an optional `edited_value` (validated by the section schema; SPEC-agents' accept endpoint gains this optional field).
- `POST /api/ingestion/runs/{id}/accept-section {section, proposal_ids[]}` applies several items of one section in **one profile save, so one revision**; locked items and flagged items are refused in this call.
- `POST /api/ingestion/runs/{id}/reject-section {section, proposal_ids[]}` rejects several items at once, with no exclusion.
- **Invalid:** an item whose value no longer validates against its section's schema when it is read or accepted (the schema changed, or the stored value was edited elsewhere) is set to `invalid` and shown greyed out with the reason; it cannot be accepted.
- **Stale** is computed when proposals are read, never stored: if the profile section changed since the run's `base_revision` and the item's key changed meanwhile, the item is shown as stale and needs a fresh look; others apply normally.
- The review page lists only `kind = "profile_item"` proposals of the run; the job pages list only job proposals (`GET /api/proposals?kind=`).

## API

| Method and path | Does |
|---|---|
| GET /api/ingestion/prompt | the laptop-analysis prompt filled for the caller |
| POST /api/ingestion/documents (raw body; filename in `X-Filename` as RFC 5987 UTF-8, sanitised to a safe display name), POST /api/ingestion/documents/text (pasted text) ; GET ; DELETE /{id} ; POST /{id}/confirm | manage documents |
| GET /api/ingestion/documents/{id}/text?offset=&limit= ; GET /api/ingestion/documents/{id}/images/{position} | extracted text preview, paginated; a stored page image, while it exists |
| POST /api/ingestion/runs/estimate `{document_ids}` ; GET /api/ingestion/consent ; POST /api/ingestion/consent `{version}` | what a run would read, the token range and ceiling, the cost range for an API key; read and record the consent |
| POST /api/ingestion/runs `{document_ids, reread_document_ids?}` ; GET /api/ingestion/runs/{id} ; POST /api/ingestion/runs/{id}/cancel ; POST /api/ingestion/runs/{id}/resume ; POST /api/ingestion/runs/{id}/continue | start, follow, stop, resume an extraction, and raise its token ceiling after a pause on it |
| GET /api/ingestion/runs/{id}/proposals ; POST /api/proposals/{id}/accept `{edited_value?}` ; POST /api/proposals/{id}/reject ; POST /api/ingestion/runs/{id}/accept-section ; POST /api/ingestion/runs/{id}/reject-section | review |

## Frontend

`/profile/fill`: three steps. 1, Prompt: the copyable prompt with Claude Code and Codex tabs. 2, Documents: paste box and drag-and-drop upload, list with type, size, extraction status, preview and delete. 3, Review: per section, the proposed changes as a diff with citations (click to see the excerpt in its document), accept, edit then accept, reject, "Accept all in this section", "Reject the rest of this section"; completeness bar updates as proposals are accepted. Before a run, a start dialog with the estimate and the consent; live progress while the agent runs, with pause, resume, continue past the token ceiling, and cancel. Mobile friendly.

## Testing strategy

- Unit: text extraction per type (fixtures generated in tests: PDF with text, PDF without a text layer (pages rendered to images), DOCX, Markdown, LinkedIn-like ZIP, unicode), limits and every guard (zip bomb, page cap, pixel bomb), per-user dedupe, the item schema and stable keys, citation normalisation and checks (including numbers), conflict detection, re-run filtering of rejected items, stale detection, accept-section as one revision, locked items refused in bulk, hard delete cascading into proposals and events, logs free of excerpts and values.
- Integration: transcription and extraction run against the fake model backend from `agents` (recorded tool calls), so CI makes no model calls; one active run per user; isolation between users for documents, runs and proposals; secret-pattern warning on upload.
- Golden: Harald's real dossier (`profile/facts.md`, `profile.md`, `projects-deep-dive.md`, `cv-base.pdf`) extracted with a real model run once on the Jetson; the accepted proposals should reproduce his current profile closely (a report of differences, reviewed by hand, not a strict test).
- End to end (Playwright): new user, copy the prompt, upload a synthetic dossier, a CV and a screenshot, run (fake backend in CI, real on the Jetson with an e2e account and a test credential if available), review with citations, accept some, edit and accept one, reject some, accept a whole section in one revision, delete a document and see its pending items disappear.

## Boundaries

- **Always:** valid citations on every proposal; user acceptance before any profile change; owner-only access; hard delete on request.
- **Ask first:** any OCR or PDF rendering dependency other than `pypdfium2` (approved on 2026-10-10); sending documents anywhere other than the user's own model credential.
- **Never:** invent claims or numbers; read secrets in the laptop prompt; keep a deleted document's bytes or text; log excerpts or document text.

## Success criteria

1. A new user goes from an empty profile to a mostly complete one by uploading documents and accepting proposals, without typing the content.
2. Every proposed item carries a citation that was checked against its document's text when proposed (a later document deletion marks the citation "source deleted").
3. Re-running after adding a document proposes only new or different items, never ones the user rejected.
4. CI covers the whole flow with zero model calls.
5. No cross-user access to documents, runs or proposals, and deletion leaves no copy of the document's content.

## Review history

- PR #5 review (2026-10-09): blocking points on image citations, deletion, proposals mapping, locked facts and limits addressed; second pass: deletion of all copies, typed citation checks for dates, phones and computed years, SPEC-agents amended for proposals.
- Plan (2026-10-10, tasks/ingestion/todo.md): aligned with the code as merged after A1 to A4: no built-in `Read` (images through an app tool), session persistence as built, steps instead of sub-runs, the proposals table as built, failing items refused instead of dropped silently.
- Review of PR #38 (2026-10-10) and decisions of the owner the same day: scanned PDF pages rendered with `pypdfium2` and transcribed; text kept until deleted; backups keep 7 nightly dumps; year only dates accepted by the profile; `claude-sonnet-5-5` for API key credentials with a token range, a cost range and a hard token bound; explicit consent at the first run; 20 MB per file again; a failing citation refused to the agent with a retry budget. The citation check is specified as tables in the plan, steps are bound to one document part in code, and the image path through the real CLI was verified by a spike.
- Second review of PR #38 (2026-10-10) and two more decisions of the owner: the parsers run in a separate `parser` service with no credential and no route out; a run pauses at twice the high token estimate until the user continues. The citation rules were rewritten and executed (108 examples after the third review) (token boundaries, exact addresses, two digit years never read, settings not proposable); the documents migration holds every column the later tasks store.
- Third review of PR #38 (2026-10-10): a month word belongs to the year that follows it; line breaks end numbers and phone numbers; the parser task is split in two (the service, then the queue and PDF text); the examples use a fictitious identity.
