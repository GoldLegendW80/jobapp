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

- **Nothing beyond the documents.** Every proposed claim cites its source document and excerpt; claims without a source are not proposed. Numbers (metrics, team sizes, dates) are copied only when a document states them. This generalises hard rule 1 of `AGENTS.md`.
- **Locked facts stay locked.** Employers, titles, dates and education change only when the user accepts that specific item.
- **Proposals, never direct writes.** Accepting goes through the normal profile save (revision, 409 rules).
- **Conflicts are shown, not resolved silently**: when two documents disagree (two different start dates), the proposal shows both with their sources and the user picks.
- **Personal data:** documents are the user's own; they stay in the app's database, are visible only to their owner, and can be deleted (with their extracted text) at any time.

## Laptop-analysis prompt

A versioned prompt template shipped with the app (`api/app/ingestion/prompts/laptop_dossier.md`), filled with the user's name and output folder, copyable in one click, with short instructions for Claude Code and for Codex. It tells the coding agent: what to look at and what to ignore (secrets, `.env`, keys, private messages, other people's data), to cite file paths as evidence, to write a fixed set of Markdown files with fixed headings matching the profile sections, never to invent numbers, and to end with a list of open questions for the user. The fixed headings make extraction reliable.

## Documents

```
ingestion_documents(id uuid, user_id, name text, mime text, size int, sha256 bytea, bytes bytea,
                    text text null, text_status enum(pending, done, failed), pages int null,
                    created_at, deleted_at null)
ingestion_runs(id uuid, user_id, agent_run_id fk agent_runs, document_ids uuid[], status, created_at)
```

- Accepted types: PDF, DOCX, TXT, MD, JSON, CSV, PNG, JPEG, and a pasted text box. Max 20 MB per file, 100 MB per user.
- Text extraction in the worker: PDF text layer with a pure-Python library, DOCX via python-docx, images and scanned PDFs passed to the model as images (no OCR dependency). Exact libraries recorded in the plan; arm64 wheels required.
- Dedupe by sha256.

## Extraction agent

- Runs on the `agents` runtime with the user's credential, model `claude-opus-5-5`, read-only tools over the user's documents (`list_documents`, `read_document(id, page range)`, `get_profile_section`) and one write tool `propose_profile_changes(section, items[])` with a strict schema: each item has the target path in the section, the proposed value, the action (add, replace, remove), and one or more citations `{document_id, excerpt}`.
- Large inputs are processed per document then merged, so a long dossier never overflows a single call; the merge step dedupes and detects conflicts.
- Output: proposals in the `proposals` table (from `agents`), grouped by section.

## API

| Method and path | Does |
|---|---|
| GET /api/ingestion/prompt | the laptop-analysis prompt filled for the caller |
| POST /api/ingestion/documents (raw body with filename header, or text) ; GET ; DELETE /{id} | manage documents |
| GET /api/ingestion/documents/{id}/text | extracted text preview |
| POST /api/ingestion/runs `{document_ids}` ; GET /api/ingestion/runs/{id} | start and follow an extraction |
| GET /api/ingestion/runs/{id}/proposals ; POST /api/proposals/{id}/accept (with optional edited value) ; /reject | review |

## Frontend

`/profile/fill`: three steps. 1, Prompt: the copyable prompt with Claude Code and Codex tabs. 2, Documents: paste box and drag-and-drop upload, list with type, size, extraction status, preview and delete. 3, Review: per section, the proposed changes as a diff with citations (click to see the excerpt in its document), accept, edit then accept, reject, "Accept all in this section"; completeness bar updates as proposals are accepted. Live progress while the agent runs. Mobile friendly.

## Testing strategy

- Unit: text extraction per type (fixtures generated in tests: PDF with text, scanned-like PDF image, DOCX, Markdown, unicode), limits, dedupe, the proposal schema, citation validation (an excerpt must be found in the cited document's text, else the item is dropped and logged), conflict detection, merge into profile sections with locked facts.
- Integration: extraction runs against the fake model backend from `agents` (recorded tool calls), so CI makes no model calls; isolation between users for documents, runs and proposals.
- Golden: Harald's real dossier (`profile/facts.md`, `profile.md`, `projects-deep-dive.md`, `cv-base.pdf`) extracted with a real model run once on the Jetson; the accepted proposals should reproduce his current profile closely (a report of differences, reviewed by hand, not a strict test).
- End to end (Playwright): new user, copy the prompt, upload a synthetic dossier and a CV, run (fake backend in CI, real on the Jetson with an e2e account and a test credential if available), review, accept some, reject some, profile updated with revisions.

## Boundaries

- **Always:** citations on every proposal; user acceptance before any profile change; owner-only access to documents.
- **Ask first:** OCR or other heavy dependencies; sending documents to any service other than the user's own model credential.
- **Never:** invent claims or numbers; read secrets in the laptop prompt; keep a deleted document's bytes or text.

## Success criteria

1. A new user goes from an empty profile to a mostly complete one by uploading documents and accepting proposals, without typing the content.
2. Every accepted claim traces to a document excerpt.
3. Re-running after adding a document proposes only new or different items.
4. CI covers the whole flow with zero model calls.
5. No cross-user access to documents, runs or proposals.

## Open questions

None blocking; defaults above are proposed for approval.
