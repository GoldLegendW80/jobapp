# Tasks: agents

Spec: [SPEC-agents.md](../../SPEC-agents.md). Depends on jobs (J5 worker and arq queue, SSE helper `app/sse.py`).

## Architecture decisions
- The agents queue is a second arq `QueueSpec` in the existing `worker` process (J5 design), not a new container.
- Credentials encrypted with `CREDENTIALS_KEY` (AES-GCM or Fernet via `cryptography`), decrypted only in the worker for the duration of a run.
- Every SDK fact (options, auth env vars, streaming message types, usage and cost fields, session persistence, file reading) is verified against the official Agent SDK docs before use and recorded here.
- CI never calls a model: a fake backend replays recorded SDK message streams.

## A1: Model credentials
**Acceptance:** `model_credentials` table and migration; encryption with key rotation support; GET/PUT/DELETE/verify endpoints (never return the secret); Account page "Model access" card (subscription token or Anthropic API key, last4, verified date, remove); verify does a minimal real call only when the user clicks Verify (mocked in CI).
**Scope:** M

## A2: Agent runtime in the worker
**Acceptance:** Agent SDK and the Claude Code CLI in the api image (arm64); agents queue in the worker; `agent_runs` and `agent_events` tables; run lifecycle (queued, running, done, failed, cancelled), limits (turns, wall time), cancel; SSE stream with Last-Event-ID resume; fake model backend for tests; in-process MCP tools with user scoping enforced server side; scratch dir per run deleted at the end; worker restart marks orphaned runs failed.
**Scope:** L, split if needed

## A3: Proposals and the job triage agent
**Acceptance:** `proposals` table per SPEC-agents (nullable target_id, statuses incl. superseded and invalid, accept with edited_value, kind filter); triage agent definition (model claude-opus-5-5) with get_job, get_profile_section, propose_note, propose_status; job detail "Assess with agent" with live run panel and inline proposals Accept and Reject; `/runs` page.
**Scope:** M

## A4: Chat per job
**Acceptance:** `agent_threads` and `agent_messages`; side panel chat on job detail; resume the same SDK session; survives reload.
**Scope:** M

## A5: Real runs on the Jetson
**Acceptance:** Harald's subscription token saved through the Account card; one real triage run and one chat on a real job of his (proposals left pending for him); tokens, cost, duration recorded; evidence.
**Scope:** S

## Checkpoint A
- [ ] All 5 success criteria of SPEC-agents.md verified
