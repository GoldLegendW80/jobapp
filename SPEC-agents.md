# Spec: agents

Module of [CAPABILITY-MAP.md](CAPABILITY-MAP.md). Depends on `jobs`. `ingestion`, `mail`, `documents` and `apply` run their agents on it. Intent: [agent-features.md](../docs/intent/agent-features.md).

## Objective

One runtime that every agentic feature uses: a worker that runs Claude agents for a given user, with that user's own model credential, streams progress to the browser, keeps a log of every run, and lets the user chat with an agent about a job or a document. This module ships the runtime, the credentials, the run log and the chat primitives, plus one real agent to prove it end to end: the **job triage agent** (reads a posting against the profile and writes a short assessment and suggested status into the job's notes as a proposal the user accepts).

User stories:
- As Harald, my agents run on my Claude subscription.
- As a friend, I paste my Anthropic API key once in Account settings; it is stored encrypted and only used for my runs. Without a credential, agent buttons explain what is missing.
- As a user, I start an agent on a job ("Assess this posting"), see it work live (steps, tool calls, text), stop it, and find the run later with its cost and duration.
- As a user, I chat with an agent in a side panel on a job; the conversation is kept per job and can be resumed.

## Runtime

- **Claude Agent SDK (Python)** in the `worker` container (the one J5 adds for sweeps; this module adds the agent queue to it). The SDK is the Claude Code harness: it needs the Claude Code CLI available in the image (Node runtime); the image stays arm64.
- **Facts to verify against the official Agent SDK docs before coding** (code.claude.com/docs/en/agent-sdk; never guess the API): the options object and its fields (system prompt, allowed tools, permission mode, MCP servers, subagent definitions, model, max turns, working directory, session resume), streaming message types, how usage and cost are reported, and how authentication is read (an Anthropic API key, or a Claude subscription OAuth token produced by `claude setup-token`). The plan records the verified answers.
- **Tools:** no shell, no filesystem outside a per-run scratch directory, no web by default. Each agent gets an explicit allow list of tools, mostly in-process app tools exposed as an MCP server inside the worker (for example `get_job`, `get_profile_section`, `propose_note`, `propose_status`), which read and write through the same service layer and user scoping as the API. Write tools only create **proposals**; nothing changes user data until the user accepts. One named exception, defined in SPEC-mail: automatic mail-to-job links and status changes from mail are recorded as proposals created already `accepted` (`decided_by = system`) with an undo.
- **Models:** `claude-opus-5-5` for every agent by default, configurable per agent definition in code. Thinking is adaptive; effort set per agent.
- **Isolation:** one run = one SDK session in a fresh scratch directory, user id bound into every tool call server side (the model never chooses whose data it touches), technical limits per run (max turns, wall time) to stop runaway loops, no spending cap (decision below), cancellation from the UI.
- **Concurrency:** at most 2 agent runs at a time on the Jetson (RAM), per-user queue order, the worker keeps within its 1536m budget.
- **As built in A2 (2026-10-10):** SDK facts in `tasks/agents/todo.md`. `claude-agent-sdk==0.2.163` bundles the Claude Code CLI 2.1.286 as a native binary, so the worker needs no Node: the `worker` target of `api/Dockerfile` (image `jobapp-worker`) adds the SDK to the api's code, the api image does not change. Queue `jobapp:agents` in the existing worker (`max_jobs=2`, a run is tried once); a later lane for automatic runs gets its own queue name. A run: claim (queued to running), decrypt the credential, scratch directory on the worker's `/tmp` tmpfs (HOME, Claude Code config, temporary files, working directory; deleted on every path), Claude Code started with `tools=[]`, the agent's app tools as an in-process MCP server bound to the user, every built-in tool also in `disallowed_tools`, `permission_mode="dontAsk"`, no settings sources, strict MCP config, and a PreToolUse hook that denies anything outside the allow list. Credential in the CLI process environment only (`ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`); every other inherited variable is blanked in that process's environment, the SDK's `claude -v` probe (started without env=) is turned off with `CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK`, and the worker drops its secrets from its own environment once its settings are read, so no Claude Code process sees them; the secret is scrubbed from anything stored. Events: `status`, `init`, `text`, `thinking` (content never stored), `tool_call` (name and clipped arguments), `tool_result` (clipped), `tool_denied`, `usage` (tokens, cost, turns), `rate_limit`, `api_error`. Limits: `max_turns` per agent, wall time 10 minutes by default; cancel sets `agent_runs.cancel_requested_at` (a column added to the data model), polled by the runner, which interrupts the SDK. Worker start fails runs left `running`; a cron fails runs running past any limit or queued for 6 hours. At most 4 queued or running runs per user (429 beyond). The first successful run with a subscription token marks it verified (`set_verified_if_unchanged`). Tests run the runner against `FakeBackend`, which replays recorded CLI message streams through the SDK's own parser; `AGENTS_FAKE_BACKEND` is refused in production twice (settings, and the backend factory). The stream (`GET /api/agents/runs/{id}/stream`, A2b) replays `agent_events` after the Last-Event-ID header or `?after=` (`app/sse.py` `replay`; each step's id is its seq, `end` without an id once the run has ended); the web `RunPanel` reconnects by itself (EventSource, then its own retry from the last seq with backoff, ending on a 404) and `/runs` lists the runs, with a test run card only when the server offers a test agent.

## Credentials

```
model_credentials(user_id pk fk, kind enum(claude_subscription, anthropic_api_key),
                  secret_encrypted bytea, last4 text, verified_at, created_at, updated_at)
```

- Secrets encrypted with a key from `.env` (`CREDENTIALS_KEY`, Fernet or AES-GCM via `cryptography`), never logged, never returned by the API (only kind, last4, verified_at).
- Account page: "Model access" card: choose Claude subscription token or Anthropic API key, paste, "Verify" (a minimal call), remove. The subscription option is shown to everyone but labelled for the owner's personal use; friends are guided to API keys (Anthropic's terms do not allow sharing one person's subscription with others).
- As built in A1 (2026-10-10): AES-256-GCM (`cryptography`), `CREDENTIALS_KEY` is 32 random bytes in base64, required in production (api and worker refuse to start without it), `key_version` is a fingerprint of the key that wrote the row. Rotation: the old key goes to `CREDENTIALS_KEY_PREVIOUS`, rows are re-encrypted when read or all at once with `python -m app.agents.cli rotate`. The ciphertext is bound to the user id and the kind (associated data), so a row copied to another user or relabelled does not decrypt.
- Verify (A1 decision): an API key is checked with one `GET /v1/models?limit=1` through the official `anthropic` SDK (no tokens spent); 401 and 403 mean invalid, 429, 503 and 529 mean try again later, network errors and anything else 503. A subscription token is not checked by the server: the Claude Code docs only document it as `CLAUDE_CODE_OAUTH_TOKEN` for the Claude Code CLI ("it can only make model requests"), there is no documented lightweight validation endpoint, calling the API with it directly is not a documented use, and the CLI only arrives in the image with A2. It is stored as unverified with that explanation, and the first real agent run (A2) is its verification (A2 records success with `credentials.set_verified_if_unchanged`). Formats: API keys start with `sk-ant-api`; subscription tokens are today `sk-ant-oat01-`, but the docs do not state the prefix, so any `sk-ant-` value that is not an API key is accepted for that kind.
- The worker decrypts the credential only for the duration of the run and passes it to the SDK through the process environment of that run, never written to disk.

## Data model (runs and chat)

```
agent_runs(id uuid, user_id, agent text, job_id null, status enum(queued, running, done, failed, cancelled),
           input jsonb, output jsonb, error text, model text, input_tokens int, output_tokens int,
           cost_usd numeric null, started_at, finished_at, created_at)
agent_events(id bigserial, run_id fk, seq int, at, kind text, data jsonb)   -- streamed steps, kept for replay
agent_threads(id uuid, user_id, job_id null, title, created_at, updated_at)   -- each message rebuilds the conversation from agent_messages (no SDK session kept on disk)
agent_messages(id bigserial, thread_id fk, role enum(user, assistant), content jsonb, run_id null, at)
proposals(id uuid, user_id, run_id null, target text, target_id uuid null, kind text, payload jsonb,
          status enum(pending, accepted, rejected, superseded, invalid, undone), decided_by enum(user, system) null, decided_at,
          edited_value jsonb null, reason text null, created_at)
```

As built in A3 (2026-10-10, migration 0008): `run_id` is nullable (a rule-based system proposal of SPEC-mail has no run, issue #17 item 1) and set to null when its run is deleted. `decided_by` and `decided_at` are null exactly while a proposal is pending (a check constraint), then `user` when the user accepts or rejects it, `system` when it is superseded (a newer pending proposal of the same kind for the same target replaces it), invalid (its job was deleted when the user accepted it) or created already accepted (SPEC-mail's exception). `edited_value` keeps what the user applied instead of the proposed value, `reason` why the system decided. Kinds of A3, on target `job`: `job_note` {text} (appended to the notes on a new line) and `job_status` {status, from, reason}. Accepting goes through the jobs service on the locked job row: a job event with `via: "agent"` and the proposal id, `applied_on` filled on applied, and the job version checked when the client sends it (409 `stale`). The triage agent (`triage`, `claude-opus-5-5`, effort medium, 12 turns) needs a job, reads it with `get_job` (posting text included) and the profile with `get_profile_section` (contact details and the board list left out), and may propose only `shortlisted` (from found), `skipped` or `closed`, and only while the job is found, shortlisted, cv_ready or staged; refused proposals come back to it as tool errors and nothing is stored. It writes in English whatever the posting's language (the profile has no answer language setting yet). Guards added in review: the posting text, description and notes reach the model only under `untrusted_*` keys and the prompt treats them as third-party data, never instructions; identity is an allow list (headline, city, country, languages); a pending status proposal is superseded whenever the job's status changes by any path, and an unedited one whose `from` is no longer the job's status is marked invalid on accept (409 `status_changed`); creating a proposal takes a transaction advisory lock per (user, target, kind), so two runs at once leave one pending; `input.script` (recorded streams) is refused in production.

## API

| Method and path | Does |
|---|---|
| GET/PUT/DELETE /api/me/model-credential, POST /api/me/model-credential/verify | manage the caller's credential |
| POST /api/agents/runs `{agent, job_id?, input}` | enqueue; 409 if no credential |
| GET /api/agents/runs, GET /api/agents/runs/{id} | list, detail with events |
| GET /api/agents/runs/{id}/stream | server-sent events of a live run (resumable with Last-Event-ID) |
| POST /api/agents/runs/{id}/cancel | stop |
| GET/POST /api/agents/threads, GET /api/agents/threads/{id}, POST /api/agents/threads/{id}/messages | chat |
| GET /api/proposals?status=&kind=&target=&target_id=&run_id=, POST /api/proposals/{id}/accept `{edited_value?, updated_at?}` (validated by the target's schema; 409 not_pending, target_gone or stale), /reject | proposals |

## Frontend

Account: Model access card. Job detail: "Assess with agent" button, live run panel (steps as a timeline, streaming text, cancel), proposals shown inline with Accept and Reject, chat side panel per job. `/runs`: list of the user's runs with status, agent, job, tokens, cost, duration. Mobile friendly.

## Project structure

```
api/app/agents/   models.py schemas.py router.py service.py credentials.py stream.py
api/app/worker/   agents_runner.py tools_mcp.py definitions/ (one module per agent: triage.py first)
api/alembic/versions/000X_agents.py
web/src/features/agents/
```

## Testing strategy

- Unit: credential encryption round trip and key rotation; tool scoping (a tool called for user A can never read user B's rows, even with B's ids in the arguments); proposal application; run state machine; limits.
- Integration: the runner against a **fake model backend** (a stub that replays recorded SDK message streams), so CI needs no credential and costs nothing; SSE stream with reconnect; cancellation mid-run; worker restart marks orphaned runs failed.
- Live (manual, opt-in, small budget): one real triage run with Harald's subscription on the Jetson, one with a test API key if Harald provides one; recorded as evidence with tokens and cost.
- End to end (Playwright): credential card, start a run on a job, watch it stream (fake backend in CI, real on the Jetson), accept a proposal, chat round trip.

## Boundaries

- **Always:** user scoping inside every tool; proposals instead of direct writes; limits on every run; secrets only decrypted in memory.
- **Ask first:** giving any agent web access, shell or filesystem tools; raising concurrency or limits; any agent that sends something outside the app (email, form submission).
- **Never:** log a credential or a full prompt containing personal data; let a model choose the user id; share one user's credential with another user's run.

## Success criteria

1. Harald's subscription token and a friend's API key can each be saved, verified and used; neither is ever readable back.
2. A triage run on a real job streams live, finishes within its limits, records tokens, cost and duration, and produces a proposal that changes nothing until accepted.
3. A chat thread on a job survives a reload and continues the same conversation.
4. CI runs the whole module against the fake backend with zero model calls.
5. No cross-user access through tools, runs, threads or proposals (automated tests).

## Decisions (2026-10-09)

- Approved by Harald.
- No spending cap: users bring their own credential and own their spend. Runs keep only technical limits (max turns, wall time) to stop runaway loops, and every run shows its tokens and cost.
