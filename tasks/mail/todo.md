# Tasks: mail

Spec: [SPEC-mail.md](../../SPEC-mail.md). Depends on agents (credentials and their key, the runner with `internal`, `bind`, `private_io`, `model_by_credential` and `token_budget`, the proposals service, the fake model backend), on jobs (statuses, events, `set_status`) and on ingestion for three things it reuses and never copies: the parser service, `estimate.py` and `pricing.py`. Umbrella issue: #64. Follow-ups of the spec's review: #17, answered at the end of this file.

Task ids use the prefix `M`. Branches are `mail/m<n>-<slug>`. One task of another module is planned here because mail needs it first: `J8` (jobs, branch `jobs/j8-status-row-lock`). Each block names its issue. New code calls a job a **posting** (`posting_id`, `posting`): Job becomes Opportunity in issue #73, and a neutral name survives the rename. The foreign keys still point at `jobs(id)`.

Where this plan and the spec differ, this plan wins and says so under "Where the spec is contradicted or silent". The spec is amended by the task that builds the point, in the same pull request.

## Decisions of the owner (Harald, up to 2026-10-13)
1. **Gmail only, per user, through Google OAuth.** Project `jobapp-gmail-hth`, scopes `gmail.readonly` and `gmail.send` (plus `openid` and `userinfo.email` to know which address was connected). Today in Testing mode with the owner as the only test user. Published to Production, unverified, once `/privacy` and `/terms` are served (M2, issue #63).
2. **Mails are linked to postings, with a history.** A mail can propose a status change of a posting, always undoable. The agent drafts replies.
3. **One send gate.** By default the user clicks Send (Review first). A later mode, Full auto (product intent of 2026-10-10), makes a Like on an opportunity the click for forms and for emails. So every send passes one function, `mail.gate.send`, which takes an authorisation. Today the only authorisation it accepts is an explicit click by the signed in owner on that exact draft. Later it can accept a recorded Full auto authorisation without any route changing. No route, worker or tool checks a button by itself.
4. **Guardrails that stay in every mode:** no cybersecurity role, caps per company, the fact check of claims against the profile, a daily limit. They live inside the gate, after the authorisation, so no mode can skip them.
5. **Everything free.** No paid mail service, no Pub/Sub. Polling inside Gmail's free quota.
6. **Model by credential.** `claude-opus-5-5` on a subscription credential, `claude-sonnet-5-5` on an API key (`model_by_credential`). A run carries an estimate and stops at twice that estimate (`token_budget`). The estimate and the prices come from `app/ingestion/estimate.py` and `app/ingestion/pricing.py`.
7. **Privacy.** A mail holds personal data of third parties. Stored text is kept until the user disconnects or deletes it: no expiry. Nothing of a mail's content goes into logs, errors, job arguments or arq results. Backups keep 7 nights. Isolation between users is proven by a test in every task. The refresh token is encrypted with the existing credentials key and revoked at Google on disconnect.
8. **No CI.** Each task's own tests are its evidence (commands and output under `tasks/evidence/mail-m<n>/`). One full local check gates a deploy (`ops/local-check.sh`, PR #121).
9. **Never a real Gmail account in a test.** Tests use `e2e-*@example.org` accounts and a fake Google at the HTTP boundary (M1). The owner does one manual check with his own account per checkpoint stage, listed as a human step.

**Defaults decided by the planner (2026-10-13)**, which the owner may change. The first five are also the open questions below.
- A message is fetched from Gmail as `format=full` (Google has already split the MIME tree), never `format=raw`: attachment bytes are then never downloaded during a sync.
- The time of a message is Gmail's `internalDate` (when Gmail received it), never the `Date` header, which the sender writes.
- A message labelled `SPAM` or `TRASH` is never stored, and a stored one that gains such a label or is deleted in Gmail is deleted from the app at the next sync.
- A sender is trusted as its domain only when Gmail itself recorded a pass: the first `Authentication-Results` header, written by `mx.google.com`, with `dmarc=pass`, or `dkim=pass` for the domain of `From`. Everything automatic needs that pass.
- `gmail.readonly` is required to connect. `gmail.send` is optional: a user who unticks it at Google gets everything but Send, and the gate answers `no_send_scope`.
- The drafting conversation is the list of instructions the user typed, kept in `mail_draft_instructions`. It is not an agents chat thread: a run that reads mail is `private_io`, so its answer is never stored or shown, and the result the user sees is the draft itself.
- The message frame is `sandbox="allow-popups allow-popups-to-escape-sandbox"` (no script, no form, no same origin, no top navigation), not `sandbox=""`: with an empty sandbox a link in a mail cannot open at all.
- The undo route keeps the spec's path. Job events keep the keys the jobs module really writes: `{from, to, via}`, with `via: "mail"` and `via: "mail_undo"`.

## Open questions for the owner
The work starts on the recommended answers. None blocks group 1.
1. **May a verdict of the model apply by itself?** The spec applies a link or a status change above a confidence threshold. A confidence stated by a model that has just read a hostile mail is the attacker's number. **Recommended: no.** A rule verdict on a mail Gmail authenticated applies by itself (recorded as an accepted system proposal, undoable). A model verdict is always a pending proposal the user accepts in one click.
2. **The numbers of the limits.** **Recommended:** 20 sends per user per rolling 24 hours, 3 sends per company per rolling 24 hours, 100 automatic model runs per user per day (the spec's figure). Constants in one file (`mail/limits.py`), shown in the page when reached.
3. **Does the cybersecurity guardrail also refuse a reply the user clicks himself?** **Recommended: yes**, as decision 4 says "every mode". The refusal says "This posting is a cybersecurity role, which the app never applies to. Answer from Gmail itself."
4. **No expiry, so is there a ceiling?** **Recommended:** nothing is pruned by age (the 180 days of the spec become the window of the first sync only). Storage is bounded by a cap of 20,000 stored messages or 300 MB of mail text per user. At the cap, unlinked threads stop being stored and the Gmail card says so. The user can delete one thread from the app (it stays in Gmail).
5. **Consent before the assistant reads mail.** A subscription credential is a consumer plan, where the user's own setting decides whether conversations train models, and a mail carries a third party's words. **Recommended:** a checkbox in the connect dialog, recorded with its date and the version of the text, saying that job related mail is sent to Anthropic through the user's own credential and asking subscription users to check their training setting. Without it the module runs on rules only and prepares no draft.

## Architecture decisions

### Google at the HTTP boundary
- **One client, `api/app/mail/google.py`** (M1): the token exchange, the refresh, the revocation, `userinfo`, and the Gmail read calls (`getProfile`, `messages.list`, `messages.get`, `history.list`, `attachments.get`). It has no send function. The only code that builds a `messages.send` request is `gate.py` (M12).
- **Three settings**, refused when `APP_ENV` is production, as `MODEL_API_BASE_URL` is: `GOOGLE_AUTH_URL` (where the browser is sent), `GOOGLE_TOKEN_BASE_URL` and `GOOGLE_API_BASE_URL` (what the server calls). Unset, the real Google addresses apply.
- **The fake, `api/tests/mail/fake_gmail.py`** (M1): one implementation, run in process by pytest and as the service `gmailstub` by `tests/gmail-stub.compose.yml`, the way `fake_ats.py` and `tests/anthropic_stub.py` are. It serves a consent page (an account field, one checkbox per scope, Allow, Deny), the token, revoke and userinfo endpoints, and the Gmail calls above plus `messages.send`. A control API (`/__control/...`) seeds mailboxes from JSON fixtures, makes the next calls of an endpoint fail (429 with `Retry-After`, 500, 401, `invalid_grant`, an expired history id), revokes a grant, and returns what was sent and every call received. Fixtures are written by hand to look like real ATS mail; no real mail is ever copied in.
- **Access tokens live in memory only**, per process, until they expire. A 401 refreshes once. `invalid_grant` sets `needs_reconnect`.
- **Backoff.** 429, 403 with a rate limit reason, and 5xx: up to three tries inside a job (1 s, 2 s, 4 s with jitter, `Retry-After` honoured), then `mail_accounts.backoff_until` doubles from 5 minutes up to 6 hours and resets on the first success. Errors are stored as a code of a fixed list (`quota`, `google_down`, `revoked`, `scope_missing`, `history_expired`, `too_large`), never as Google's message.

### A mail into stored text
- **The worker walks the tree Google returns, with the standard library only**: at most 20 levels and 200 parts, the first `text/plain` and the first `text/html` that are not attachments, each decoded from base64url and from its declared charset (a fixed list; anything else is read as UTF-8 with replacement), each capped at 500 KB with `truncated` set. Every other part with a file name is listed as an attachment (`part_id`, name, type, size): its bytes are not fetched.
- **HTML goes through the parser service: yes.** One new kind, `mail_html`, handled by `api/app/ingestion/sandbox/mail_html.py`: `nh3` sanitises against an allow list and a small standard library walker extracts the text. The reason is the rule of the house: content written by a stranger is parsed by a library only in the process that holds no key, no token, no database and no network, and the syncing worker holds a Gmail access token. `nh3` joins the worker image; the parser runs the same image. One message per parser job. M3 measures the time per message on the Jetson and, above 300 ms, lets one job carry up to 10 messages.
- **The allow list:** `p br div span a b strong i em u ul ol li blockquote pre code table thead tbody tr td th h1 h2 h3 h4 hr img`. `a` keeps `href` with the schemes `http`, `https` and `mailto`. `img` keeps `alt`, `width`, `height`, and its `http` or `https` source moved to `data-src`, so no remote image loads until "Show images". No `style`, `class`, `id`, `form`, `svg`, `math`, `base`, `meta`, `link`, `cid:` or `data:` address.
- **Headers are parsed in the worker by pure functions** (`mail/parse.py`): addresses with `email.headerregistry` and a strict check, the trust rule above, `Reply-To`, `Message-ID`, `References`. Two `From` headers, a `From` with several addresses, or a display name that contains an address of another domain set `suspicious`, and a suspicious message is never trusted.
- **Display:** the page builds the frame's `srcdoc` from the stored HTML with a CSP `<meta>` (`default-src 'none'; img-src 'none'`, or `img-src https:` after "Show images" for that message), links with `rel="noopener noreferrer nofollow"` and `target="_blank"`. Plain text is the fallback and what every agent reads.

### Sync
- **A queue of its own**, `jobapp:mail`, concurrency 1, in the existing worker (no new service in production). A job's only argument is a user id. Its result is counts. The job id is `mail_sync:<user_id>`, so one sync runs per user.
- **First sync:** the job related query of the spec, built from the user's postings (hosts of posting addresses that are not an ATS or a job board, the ATS domains, the keywords in French and English), split into queries under 1,500 characters, `newer_than:180d`, at most 5,000 message ids. It runs as a chain of bounded jobs (100 messages each, the page token in `mail_accounts.sync_cursor`), so progress shows and a restart resumes. It links threads and creates pending suggestions; it never applies a status change.
- **Incremental sync:** a cron every minute queues the connected users whose last sync is older than 5 minutes and whose backoff has passed. A run reads `history.list` from the stored history id, at most 200 new messages and 60 s. A new message is first fetched as `format=metadata`; it is stored in full only when its thread is already stored or it passes the same job related filter, evaluated locally. An expired history id starts a bounded resync: the first sync's query since the last sync minus one day.
- **After storing**, `mail/pipeline.py` runs the steps that exist, in order: link, classify, needs reply, draft. A step is a module of a fixed list, skipped while its file is not there yet, so later tasks add a file and edit nothing shared.

### Changes a mail may cause
- **Linking, rules first** (`link_rules.py`, pure): signals with weights, combined as one minus the product of one minus each weight. A posting's reference id found in the body (0.9). The trusted sender's domain equal to the host of the posting's address when that host is the company's own (0.7). The company name in the subject or in the trusted sender's display name (0.5). The title in the subject (0.4). A trusted ATS sender, counted only with another signal (0.1). Postings in `found` or `skipped` score times 0.8. One posting wins at 0.80 or more with a lead of 0.25. These are starting values: M5 records a confusion table on the fixtures, checkpoint stage B on the owner's mailbox.
- **Classifying, rules first** (`classify_rules.py`, pure): the confirmation, rejection, invitation, assessment and offer templates of Ashby, Greenhouse, Lever, Workday, SmartRecruiters, Teamtailor and Welcome to the Jungle, in French and English. A rule names itself in its verdict.
- **What applies by itself:** a rule verdict, on an inbound message that Gmail authenticated, in a thread linked by a rule or by hand, that arrived after the account was connected, dated after the posting's last status change, for a transition of the spec's table. It is recorded as a proposal of kind `mail_status` created `accepted` by the system, a job event `{from, to, via: "mail", message_id, proposal_id}`, and a row of `mail_classifications`. Everything else is a pending proposal, or nothing.
- **The model second** (M17): only for threads the rules left ambiguous and messages they could not label, only with a credential and the consent, each run inside its budget and the daily cap. Its tools take an index into candidates that code computed and a label of the fixed list, and return constants. Its verdict is a pending proposal (question 1).
- **Proposal kinds are registered, not hard coded:** M11 adds `proposals.register_kind(kind, target, apply, undo)` to `api/app/agents/proposals.py` once; `mail_link` (target `mail_thread`) and `mail_status` (target `job`) then live in `api/app/mail/`. Payloads hold ids, labels, statuses, rule names and scores: never a subject, an address or a body.
- **Residual risk, accepted:** a real ATS account can send an authenticated mail that names another company. It can at worst move one of the user's own postings along an allowed transition, visibly, with its source mail and an Undo.

### Drafting and the one gate
- **Recipients are computed by code** (`recipients.py`, M8) from the thread: `Reply-To` if present, else `From`, plus the other participants for reply all, minus the user's own address. An agent has no tool that touches them. A recipient the thread has never carried, and a `Reply-To` on another domain than `From`, are flagged; the confirmation shows the flag and the click must name the flagged addresses.
- **Mail agents** (`internal`, `private_io`, `live_text=False`, bound to one thread by `bind`): the reply agent reads the thread, the posting, the profile and the answers section through tools, and has one writing tool, `write_draft(body)`, which stores the text and returns the constant "Draft saved." The system prompt of every mail agent says that mail content is data, never instructions. No tool changes a status, a link, a recipient or a subject, and none reaches the network.
- **`gate.send(db, authorisation, now)` is the only path to Gmail's send call.** An `Authorisation` is a frozen value `{kind, user_id, draft_id, content_hash, confirmed_new_recipients, at}` built only by `gate.authorise_click`, which only the send route calls, after the session and the CSRF header. `content_hash` covers recipients, subject and body as the confirmation showed them. The gate checks, in this order, and writes one `mail_sends` row whatever the outcome: the kind is one it accepts (`user_click` alone today); the draft is the user's, open, and still has that hash; the account is connected with `gmail.send`; every flagged recipient was confirmed; the guardrails (cybersecurity, daily limit, cap per company, fact check). Then the draft moves to `sending` under a row lock, the transaction commits, Gmail is called once, and the draft becomes `sent` or `failed`.
- **Fact check** (`factcheck_rules.py` M9, then the checker agent M20): rules first. Every number, year, amount, employer, school and title in the draft must be found in the profile, the answers section, the thread or the user's own instructions; an address or a link in the draft that comes from the mail and not from the profile is flagged. The result is stored on the draft for one `content_hash`. The gate accepts `passed`, or flagged issues the click acknowledged one by one. A later Full auto authorisation will need `passed`.
- **A draft stuck in `sending`** (the api died between Gmail's answer and the commit) is never sent again by itself. The next sync that sees a new outbound message in that thread marks it `sent`. Until then the page says "We could not confirm this was sent. Look in your Sent folder." and offers Send again, which is a new click and a new authorisation.

### Privacy and isolation
- Every query filters on the session's user id. Another user's thread, message, draft or attachment answers the same 404 as a missing one. `Cache-Control: no-store` on every route. The tables carry composite foreign keys `(thread_id, user_id)`, so a row cannot point at another user's thread even by a bug.
- **Logs:** the module logs through `mail/log.py` (M1): a fixed list of event names with ids, counts and codes. A canary helper (`api/tests/mail/canary.py`, M1) plants a marker in the subject, body, sender name, attachment name and draft of a fixture, then searches the container logs, Redis (job arguments, arq results, live buffers), `agent_events`, `agent_runs.output` and the api's error answers. Every task runs it over what it adds; M21 runs it over the whole module.
- **Disconnect** revokes at Google, then deletes in one transaction: the account, threads, messages, classifications, drafts and instructions; `mail_link` proposals; the payload of `mail_status` proposals reduced to `{from, to, mail_deleted: true}`; `message_id` removed from job events, which keep `{from, to, via, mail_deleted: true}`. `mail_sends` stays: it holds no address and no text, and the limits must survive a reconnect. If Google cannot be reached the local deletion still happens and the page tells the user to remove the access at Google. Deleting the app account cascades to every mail table.
- **Backups** hold deleted mail for at most 7 nights (platform T10, already merged). `/privacy` says so.

## Data model
The spec's DDL with what it lacked. Two migrations, reserved here: **0013 (M4)** and **0014 (M8)**. 0011 and 0012 belong to ingestion.

```
-- 0013, M4: everything the reading side stores
mail_accounts(user_id pk fk users cascade, email text, google_sub text unique,
              refresh_token_encrypted bytea, key_version int,           -- sealed as model_credentials is
              scopes text[], history_id text null, connected_at, initial_sync_done_at null,
              last_sync_at null, sync_cursor jsonb null, backoff_until null,
              status enum(ok, needs_reconnect, error), error_code text null,
              assistant_consent_at null, assistant_consent_version int null)
mail_threads(id uuid pk, user_id fk users cascade, gmail_thread_id text,
             posting_id uuid null fk jobs set null, link_source enum(rule, model, manual) null,
             link_confidence real null, link_locked bool default false,     -- a manual link or unlink
             subject text, participants text[], last_message_at, needs_reply bool default false,
             deleted_at null,                                               -- tombstone: text gone, never synced back
             created_at, updated_at, unique(user_id, gmail_thread_id), unique(id, user_id))
mail_messages(id uuid pk, user_id, thread_id, fk (thread_id, user_id) -> mail_threads(id, user_id) cascade,
              gmail_message_id text, message_id_header text, references_header text,
              from_addr text, from_name text, reply_to text null, to_addrs text[], cc_addrs text[],
              received_at, subject text, snippet text, body_text text, body_html_sanitized text null,
              truncated bool, has_remote_images bool, trusted bool, suspicious bool,
              direction enum(inbound, outbound), labels text[], attachments jsonb,
              before_connection bool,                                       -- never applies a status change
              created_at, unique(user_id, gmail_message_id), unique(id, user_id))
mail_classifications(message_id uuid pk, user_id, fk (message_id, user_id) -> mail_messages cascade,
              label enum(application_received, rejection, interview_invitation, assessment, offer,
                         info_request, other),
              source enum(rule, model), rule text null, confidence real,
              proposal_id uuid null fk proposals set null, applied bool, undone bool, created_at)

-- 0014, M8: everything the writing side stores
mail_drafts(id uuid pk, user_id, thread_id, fk (thread_id, user_id) -> mail_threads cascade,
            posting_id uuid null, reply_mode enum(reply, reply_all), to_addrs text[], cc_addrs text[],
            subject text, body text, written_by enum(user, agent), version int default 1,
            status enum(draft, sending, sent, failed), fact_check jsonb null,
            gmail_message_id text null, created_at, updated_at, sent_at null)
            -- one open draft per thread: partial unique index on thread_id where status <> 'sent'
mail_draft_instructions(id bigserial pk, draft_id fk cascade, user_id, text, at, run_id uuid null)
mail_sends(id uuid pk, user_id fk users cascade, draft_id uuid null, posting_id uuid null,
           company_key text null, authorisation_kind enum(user_click), authorised_at,
           content_hash text, recipients_count int, new_recipients bool,
           result enum(sent, refused, failed), refusal_code text null,
           gmail_message_id text null, at)                                  -- no address, no subject, no body
```

`refresh_token_encrypted` uses `crypto.Keyring` with the associated data of `(user_id, "gmail_refresh_token")`, and `./deploy.sh` key rotation (`agents/cli.py`, `credentials.rotate_all`) gains the mail accounts in M4. Both migrations only add tables and types, so the expand and contract rule of CONTRIBUTING has nothing to contract.

## API, as planned
The spec's table with four changes: `posting_id` in bodies, `suggestion_ids` (issue #17 item 3), a route to delete a thread from the app, and a route for instructions.

| Method and path | Task |
|---|---|
| GET /api/mail/oauth/start ; GET /api/mail/oauth/callback ; GET, DELETE /api/mail/account ; PUT /api/mail/account/consent | M4 |
| POST /api/mail/sync | M10 |
| GET /api/jobs/{id}/mail ; GET /api/mail/unmatched ; POST, DELETE /api/mail/threads/{id}/link `{posting_id}` ; DELETE /api/mail/threads/{id} | M11 |
| GET /api/mail/messages/{id}/attachments/{part} | M15 |
| POST /api/mail/status-changes/{event_id}/undo ; GET /api/mail/suggested-changes ; POST /api/mail/suggested-changes/apply `{suggestion_ids? or posting_ids?}` | M13 |
| POST /api/mail/threads/{id}/drafts ; GET, PUT /api/mail/drafts/{id} ; DELETE /api/mail/drafts/{id} | M8 |
| POST /api/mail/drafts/{id}/send `{content_hash, confirmed_new_recipients, acknowledged_issues}` | M12 |
| GET /api/mail/assist/estimate ; POST /api/mail/assist/start | M17 |
| POST /api/mail/drafts/{id}/instructions `{text}` | M19 |
| GET /privacy ; GET /terms ; GET / signed out | M2 |

## Who owns which shared file
Tasks of one group never edit the same file. Where two tasks touch one file they are in different groups, in the order given.
- `api/alembic/versions/`: **M4 takes 0013** (group 2), **M8 takes 0014** (group 3). No other task has a migration.
- `api/app/mail/__init__.py`: empty; the first of M1 and M3 to merge adds it.
- `api/app/mail/routers.py`, `registry.py`, `purge.py`: **M4**. `routers.py` lists every router module of this plan from the start (`account_router`, `sync_router`, `threads_router`, `attachments_router`, `status_router`, `drafts_router`, `send_router`, `assist_router`, `instruct_router`) and includes those whose file exists; a file that exists and fails to import fails the start. `registry.py` does the same for the modules that register agents and tools (`assist`, `reply_agent`, `checker_agent`), and `pipeline.py` (M7) for the steps (`linking`, `classifying`, `needs_reply`, `drafting`). So a later task adds its file and no line. `purge.py` is complete for 0013 in M4; **M8** (group 3) adds the 0014 tables.
- `api/app/main.py`, `api/app/config.py`, `.env.example`: `config.py` and `.env.example` **M1** (group 1), `main.py` **M4** (group 2: the routers and the registry import).
- `api/app/worker.py`, `api/app/queue.py`: **M7 only** (the `jobapp:mail` queue, its tick cron, the registry import). Ingestion G10 (#55) edits `worker.py` too: the orchestrator does not run M7 while G10 is open.
- `api/app/mail/google.py`, `errors.py`, `log.py`, `api/tests/mail/fake_gmail.py`, `canary.py`, `tests/gmail-stub.compose.yml`, `web/e2e/mail-all.config.ts` (runs every `mail-*.spec.ts`), the line that adds the mail group to `ops/local-check.sh`: **M1**. The fake is complete in M1, send included, so no later task edits it; a task that finds a missing shape adds it in its own group only if no other task of that group does.
- `api/app/ingestion/sandbox/mail_html.py`, two lines of `sandbox/main.py` (the kind and its limit), `api/pyproject.toml`, `api/uv.lock`, `api/Dockerfile` (`nh3`): **M3**. Ingestion G8 (#53) edits `main.py`, `pyproject.toml` and the lock too: the orchestrator does not run M3 while G8 is open.
- `api/app/mail/sync.py`: **M7** (group 3), then **M10** (group 4).
- `api/app/agents/proposals.py`: **J8** (group 1: the row lock inside `accept`), then **M11** (group 4: `register_kind`). No other task of this plan edits `api/app/agents/`.
- `api/app/jobs/service.py`, `router.py`: **J8** (group 1), then **M16** (group 6: the `needs_reply` filter, through a registered subquery so jobs imports nothing of mail).
- `api/app/ingestion/estimate.py`, `pricing.py`: read only. **M17** imports public names; if it needs a private one it makes it public in a one line change, alone in its group to do so.
- `web/src/routes.tsx`: **M2** (group 1), then **M14** (group 5). `web/src/features/account/account-page.tsx`: **M4**. `web/src/features/jobs/job-detail-page.tsx`: **M14**. The jobs list files: **M16**.
- `web/src/features/mail/`: `gmail-card.tsx` and `account-api.ts` **M4**, then **M10** (Sync now, the banner). **M14** creates `mail-tab.tsx`, `thread-view.tsx`, `message-frame.tsx`, `unmatched-page.tsx`, `link-dialog.tsx`, `threads-api.ts`, and four placeholders it composes: `status-change-row.tsx` and `suggested-changes.tsx` (filled by **M16**), `assist-card.tsx` (**M17**), `draft-panel.tsx` (**M18**). M18 creates the placeholders `draft-chat.tsx` (**M19**) and `fact-check-note.tsx` (**M20**).
- `web/e2e/`: one spec per task, `mail-m<n>.spec.ts`.
- `SPEC-mail.md`, `SPEC-agents.md`: each task amends the sentences it contradicts; two tasks of one group never amend the same section.
- This file: every task adds one paragraph under "Measurements", headed by its id, and changes no other line; the orchestrator ticks boxes.

<!-- TASKS -->
