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
                    failed_reason text null, failed_kind enum(file, server) null, attempts smallint,
                    pages int null, secret_warning bool default false, secret_confirmed_at null,
                    created_at, unique(user_id, sha256))
ingestion_images(id, document_id fk cascade, user_id fk cascade, position int, jpeg bytea,
                 width int, height int, text text null, unique(document_id, position))
ingestion_runs(id uuid, user_id fk cascade, document_ids uuid[], base_revision int,
               status enum(running, paused, done, failed, cancelled), partial bool,
               paused_reason text null, waiting text null, deadline_at, cancel_requested_at null,
               created_at, finished_at,
               unique(user_id) where status in (running, paused))
ingestion_run_steps(id, ingestion_run_id fk cascade, position int, kind enum(transcribe, extract),
                    document_id uuid, sha256 bytea, part int, range_start int, range_end int,
                    identity_hash, status enum(planned, running, done, failed, cancelled, reused),
                    agent_run_id fk agent_runs null, attempt smallint, refused_count int,
                    items_count int, purge_events bool,
                    unique(ingestion_run_id, position))
ingestion_items(id, user_id fk cascade, document_id fk cascade, step_id fk cascade, section, key,
                action, value jsonb, value_hash, citations jsonb, flags text[],
                unique(step_id, section, key, value_hash))
ingestion_consents(user_id pk fk cascade, text_version int, accepted_at)
```

As planned against the merged runner (tasks/ingestion/todo.md): the runner executes one SDK session per `agent_runs` row with fixed limits and has no sub-runs, so an ingestion run is a chain of steps, each an ordinary agent run, and the merge is code. Steps store checked candidates in `ingestion_items`; the merge turns them into proposals.

- **Accepted types:** PDF, DOCX, TXT, MD, JSON, CSV, PNG, JPEG, ZIP (LinkedIn data export only: the CSV files inside are unpacked as separate documents, other entries ignored), and a pasted text box.
- **Limits:** 20 MB per file (enforced as 20 MiB; the edge limit of `caddy/Caddyfile` is raised to that for the upload route only and stays 10 MiB elsewhere), 100 MB per user (live documents only), 200 pages per PDF read as text and 30 pages per PDF rendered as images, 500,000 characters of text per document, 100,000 characters per paste; per run at most 30 documents, 1,500,000 characters of text and 60 images or rendered pages sent to the model. Over a limit: 413 or 422 with the limit named.
- **Parser safety:** DOCX and ZIP opened with uncompressed-size and entry-count caps (zip bomb guard, 50 MB uncompressed, 200 entries), PDFs parsed with a page cap and a time budget, images opened with the same pixel guard as the profile photo (25 MP) and normalised to a JPEG of at most 1568 px and 300,000 bytes before sending. Every parser and every image decode runs in a resource limited child process of the worker (tasks/ingestion/todo.md, "The parser sandbox"), never in the api.
- **Text extraction:** PDF text layer and DOCX text with pure-Python libraries (`pypdf`, `defusedxml`, recorded in the plan). Images are **transcribed by the model** in the first steps of the run: an app tool hands the agent one stored image as an image block (no built-in file tool; the path through the real Claude Code CLI was verified on 2026-10-10, `tasks/evidence/ingestion-g0/`), the agent stores the transcription through a write tool bound to that image, and it becomes the document's `text` with `text_source = transcribed`. **A PDF page without a text layer is rendered to an image with `pypdfium2`** inside the same child process (decided by Harald on 2026-10-10), then transcribed like a screenshot; pages that have text keep it. A document's `bytes` are set to null once its text or its images are stored, and its images are deleted once transcribed. Citations into transcribed text are checked against the transcription and flagged "transcribed, check it" in the review.
- **Dedupe** by `unique(user_id, sha256)`: per user only, never revealing another user's uploads. An upload that matches a document whose extraction failed replaces it and is processed again.
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
- **Citation check** (server side, before storing; the normative tables and thirty worked examples are in tasks/ingestion/todo.md, "Citations"): both the excerpt and the document text go through NFKC, the removal of invisible characters (soft hyphen, zero width and bidi controls), the folding of curly apostrophes, quotes and dashes, case folding and whitespace collapsing; the match then ignores spaces and hyphens on both sides, which handles hyphenation at line breaks symmetrically. The excerpt must be 20 to 600 characters (or the whole document, if shorter) and found in the text. Values are then checked against the cited text by type:
  - **short fields** (email, URLs, employer organisation, school, language name, link label, board): the value must appear verbatim in an excerpt;
  - **dates** (`YYYY` or `YYYY-MM`): the year must appear; a month is accepted only when the excerpt holds it next to its year, as a number or as an English or French month name or abbreviation (`2023-01` matches "January 2023", "janv. 2023", "01/2023"); otherwise the agent proposes the year alone;
  - **phone numbers**: digits only are compared, as the document writes them;
  - **other numbers** in a value (metrics, counts, sizes): each must appear in an excerpt, read with its separators ("10 000" is 10000, "3,5" is 3.5); a number word does not support a digit;
  - **`skills.years_experience`** is never proposed by the agent: the merge computes it from the employment dates (non-overlapping periods, a year alone read at its shortest, one decimal, never rounded up) and proposes it as a derived item only when the profile has no value.
  Failing items are refused: the tool answers the agent with the item's index and the rule it broke, nothing is stored, and the refusal is logged with ids and the rule only, never the excerpt or value. A budget in code bounds the retries (20 refused items per step, and no third attempt at the same key), and the count of refused items is shown per document.
- **Tokens:** before a run the start dialog shows what will be read, the model, a range of tokens and the bound at which the run stops, and for API key users a cost range; the formulas and their assumptions are in the plan ("Tokens"). Each step has a token budget enforced by the runner. This is a technical bound, not the spending cap SPEC-agents decided against.
- **Consent:** the first start asks for explicit consent (a checkbox, recorded with the date and the version of the text shown) to sending the selected documents to Anthropic through the user's own credential; later starts show a one line reminder; a new version of the text asks again.
- **Failures:** a step that fails on the credential or the provider pauses the run, which the user resumes; a step that fails on its own content is reported and the chain goes on; at 60 minutes what finished is merged and the rest reported (the plan's "Failure rules").
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
| POST /api/ingestion/runs/estimate `{document_ids}` ; POST /api/ingestion/consent `{version}` | what a run would read, the token range and bound, the cost range for an API key; record the consent |
| POST /api/ingestion/runs `{document_ids, reread_document_ids?}` ; GET /api/ingestion/runs/{id} ; POST /api/ingestion/runs/{id}/cancel ; POST /api/ingestion/runs/{id}/resume | start, follow, stop, resume an extraction |
| GET /api/ingestion/runs/{id}/proposals ; POST /api/proposals/{id}/accept `{edited_value?}` ; POST /api/proposals/{id}/reject ; POST /api/ingestion/runs/{id}/accept-section ; POST /api/ingestion/runs/{id}/reject-section | review |

## Frontend

`/profile/fill`: three steps. 1, Prompt: the copyable prompt with Claude Code and Codex tabs. 2, Documents: paste box and drag-and-drop upload, list with type, size, extraction status, preview and delete. 3, Review: per section, the proposed changes as a diff with citations (click to see the excerpt in its document), accept, edit then accept, reject, "Accept all in this section", "Reject the rest of this section"; completeness bar updates as proposals are accepted. Before a run, a start dialog with the estimate and the consent; live progress while the agent runs, with pause, resume and cancel. Mobile friendly.

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
