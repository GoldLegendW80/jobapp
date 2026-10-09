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

- **Nothing beyond the documents.** Every proposed item cites at least one source excerpt; items without a valid citation are dropped. Every number in a proposed value (metric, team size, date, count) must appear in its cited excerpts. `skills.years_experience` is arithmetic from cited dates, never rounded up. This generalises hard rule 1 of `AGENTS.md`.
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
                    text_source enum(parsed, transcribed, pasted), text_status enum(pending, done, failed),
                    pages int null, secret_warning bool default false, secret_confirmed_at null,
                    created_at, unique(user_id, sha256))
ingestion_runs(id uuid, user_id fk cascade, agent_run_id fk agent_runs, document_ids uuid[],
               base_revision int, status enum(queued, transcribing, extracting, merging, done, failed, cancelled),
               created_at, finished_at)
```

- **Accepted types:** PDF, DOCX, TXT, MD, JSON, CSV, PNG, JPEG, ZIP (LinkedIn data export only: the CSV files inside are unpacked as separate documents, other entries ignored), and a pasted text box.
- **Limits:** 20 MB per file, 100 MB per user (live documents only), 200 pages per PDF, 500,000 characters of text per document, 100,000 characters per paste; per run at most 30 documents, 1,500,000 characters of text and 60 images or rendered pages sent to the model. Over a limit: 413 or 422 with the limit named.
- **Parser safety:** DOCX and ZIP opened with uncompressed-size and entry-count caps (zip bomb guard, 50 MB uncompressed, 200 entries), PDFs parsed with a page cap and a time budget, images opened with the same pixel guard as the profile photo (25 MP) and downscaled to the model's image limits before sending.
- **Text extraction:** PDF text layer and DOCX text with pure-Python libraries (arm64 wheels, recorded in the plan). Images, and PDF pages without a text layer, are **transcribed by the model** in a first step of the run: the worker gives the file to the agent (the Agent SDK's file reading handles PDFs and images; to be verified against the SDK docs at implementation, before considering any rendering dependency such as pypdfium2, which would be "ask first"), the agent returns a plain transcription, and it is stored as the document's `text` with `text_source = transcribed`. Citations for those documents are checked against the transcription and flagged "transcribed, check it" in the review.
- **Dedupe** by `unique(user_id, sha256)`: per user only, never revealing another user's uploads.
- **Deletion is a hard delete:** removing a document deletes its row (bytes and text gone at once) and, in the same transaction:
  - strips its excerpts from proposals of **every** status; a pending item left without any citation is deleted; accepted items keep only "source deleted" in place of the excerpt (the accepted value is the user's own profile now); rejected and superseded items keep only their section, key and value hash (needed so re-runs do not re-propose them);
  - deletes every `agent_events` row of the ingestion runs that read the document (tool arguments, tool results and transcription output can hold its text); the run row stays with ids, status, tokens and cost;
  - runs' `input` and `output` only ever hold document ids, never text; the UI shows ids of deleted documents as "deleted".
- **No transcripts outside the database:** each ingestion run gives the Agent SDK a configuration directory inside its scratch directory, with session persistence turned off if the SDK allows it (to verify against the SDK docs at implementation), and the scratch directory is deleted when the run ends. Nothing about a run is left in the worker's home directory.
- Deleting the account cascades to documents, runs, events and proposals.

## Extraction agent

- Runs on the `agents` runtime with the user's credential, model `claude-opus-5-5`, turn limit sized from the input (20 + 10 per document, at most 400) and 60 minutes per run, with one sub-run per document for transcription and extraction then one merge sub-run, with the only built-in tool being file reading scoped to the run's scratch directory (for transcription), and read-only app tools over the run's documents (`list_documents`, `read_document(id, offset, limit)`, `get_profile_section`) and one write tool `propose_profile_changes(section, items[])` with a strict schema. Each item has:
  - a **stable key**: the employer `key`, the school name and start, the facts block `title`, the skills category `name`, the board `org`, the answers field name; never an array index;
  - the action (`add`, `replace`, `remove`), the proposed value (validated against the section's Pydantic model before it is stored), and one or more citations `{document_id, excerpt}`.
- **Citation check** (server side, before storing): Unicode NFKC, case folding, whitespace collapsed and hyphenation at line breaks removed on both the excerpt and the document text; the excerpt must be at least 20 characters (or the whole value, if shorter) and found in the text. Values are then checked against their excerpts by type:
  - **dates** (`YYYY-MM` months, years): the year must appear, and the month as a number or as an English or French month name or abbreviation (`2023-01` matches "January 2023", "janv. 2023", "01/2023");
  - **phone numbers**: digits only are compared;
  - **other numbers** in a value (metrics, counts, sizes): each must appear in an excerpt;
  - **`skills.years_experience`** is exempt from appearing literally, but must equal the span computed from the cited employment dates (sum of non-overlapping periods, in years with one decimal, never rounded up).
  Failing items are dropped; the drop is logged with ids and the reason only, never the excerpt or value.
- **Large inputs:** documents are read in pages or chunks; transcription, then extraction per document, then a merge step that dedupes and detects conflicts.
- **Skills and search:** proposed advanced terms must pass `check_regex`; the `none` categories, gates and search preferences are proposed only when a document states them.
- **Re-runs:** the run compares against the current profile and against items the user rejected before (by section, key and value hash): rejected items are not proposed again unless their value changed. Pending items from an older run are marked superseded when a newer run starts.
- At most **one active ingestion run per user** (409 otherwise).

## Proposals mapping

Ingestion uses the `proposals` table of SPEC-agents (amended in this PR: nullable `target_id`, statuses `superseded` and `invalid`, optional `edited_value` on accept) with `target = "profile"`, `target_id = null`, `kind = "profile_item"`, one row per item, and `payload = {section, key, action, value, citations[], base_revision, conflict?}`. Applying items:
- `POST /api/proposals/{id}/accept` takes an optional `edited_value` (validated by the section schema; SPEC-agents' accept endpoint gains this optional field).
- `POST /api/ingestion/runs/{id}/accept-section {section, proposal_ids[]}` applies several items of one section in **one profile save, so one revision**; locked items are refused in this call.
- **Invalid:** an item whose value no longer validates against its section's schema when it is read or accepted (the schema changed, or the stored value was edited elsewhere) is set to `invalid` and shown greyed out with the reason; it cannot be accepted.
- **Stale** is computed when proposals are read, never stored: if the profile section changed since the run's `base_revision` and the item's key changed meanwhile, the item is shown as stale and needs a fresh look; others apply normally.
- The review page lists only `kind = "profile_item"` proposals of the run; the job pages list only job proposals (`GET /api/proposals?kind=`).

## API

| Method and path | Does |
|---|---|
| GET /api/ingestion/prompt | the laptop-analysis prompt filled for the caller |
| POST /api/ingestion/documents (raw body; filename in `X-Filename` as RFC 5987 UTF-8, sanitised to a safe display name), POST /api/ingestion/documents/text (pasted text) ; GET ; DELETE /{id} ; POST /{id}/confirm | manage documents |
| GET /api/ingestion/documents/{id}/text?offset=&limit= | extracted text preview, paginated |
| POST /api/ingestion/runs `{document_ids}` ; GET /api/ingestion/runs/{id} ; POST /api/ingestion/runs/{id}/cancel | start, follow, stop an extraction |
| GET /api/ingestion/runs/{id}/proposals ; POST /api/proposals/{id}/accept `{edited_value?}` ; POST /api/proposals/{id}/reject ; POST /api/ingestion/runs/{id}/accept-section | review |

## Frontend

`/profile/fill`: three steps. 1, Prompt: the copyable prompt with Claude Code and Codex tabs. 2, Documents: paste box and drag-and-drop upload, list with type, size, extraction status, preview and delete. 3, Review: per section, the proposed changes as a diff with citations (click to see the excerpt in its document), accept, edit then accept, reject, "Accept all in this section"; completeness bar updates as proposals are accepted. Live progress while the agent runs. Mobile friendly.

## Testing strategy

- Unit: text extraction per type (fixtures generated in tests: PDF with text, image-only PDF, DOCX, Markdown, LinkedIn-like ZIP, unicode), limits and every guard (zip bomb, page cap, pixel bomb), per-user dedupe, the item schema and stable keys, citation normalisation and checks (including numbers), conflict detection, re-run filtering of rejected items, stale detection, accept-section as one revision, locked items refused in bulk, hard delete cascading into proposals and events, logs free of excerpts and values.
- Integration: transcription and extraction run against the fake model backend from `agents` (recorded tool calls), so CI makes no model calls; one active run per user; isolation between users for documents, runs and proposals; secret-pattern warning on upload.
- Golden: Harald's real dossier (`profile/facts.md`, `profile.md`, `projects-deep-dive.md`, `cv-base.pdf`) extracted with a real model run once on the Jetson; the accepted proposals should reproduce his current profile closely (a report of differences, reviewed by hand, not a strict test).
- End to end (Playwright): new user, copy the prompt, upload a synthetic dossier, a CV and a screenshot, run (fake backend in CI, real on the Jetson with an e2e account and a test credential if available), review with citations, accept some, edit and accept one, reject some, accept a whole section in one revision, delete a document and see its pending items disappear.

## Boundaries

- **Always:** valid citations on every proposal; user acceptance before any profile change; owner-only access; hard delete on request.
- **Ask first:** OCR or PDF rendering dependencies; sending documents anywhere other than the user's own model credential.
- **Never:** invent claims or numbers; read secrets in the laptop prompt; keep a deleted document's bytes or text; log excerpts or document text.

## Success criteria

1. A new user goes from an empty profile to a mostly complete one by uploading documents and accepting proposals, without typing the content.
2. Every proposed item carries a citation that was checked against its document's text when proposed (a later document deletion marks the citation "source deleted").
3. Re-running after adding a document proposes only new or different items, never ones the user rejected.
4. CI covers the whole flow with zero model calls.
5. No cross-user access to documents, runs or proposals, and deletion leaves no copy of the document's content.

## Review history

- PR #5 review (2026-10-09): blocking points on image citations, deletion, proposals mapping, locked facts and limits addressed; second pass: deletion of all copies, typed citation checks for dates, phones and computed years, SPEC-agents amended for proposals.
