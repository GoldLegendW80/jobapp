# Spec: mail

Module of [CAPABILITY-MAP.md](CAPABILITY-MAP.md). Depends on `agents` and `jobs`. Intent: [agent-features.md](../docs/intent/agent-features.md).

## Objective

Every email about a job lives next to that job in the app. Each user connects their own Gmail; the app finds the conversations that concern their jobs, keeps the full history per job, updates the job's status from what the mails say (application received, rejection, interview invitation), and prepares reply drafts. On a job, the user drafts a reply together with an agent ("let's draft a mail, I want to say this"), iterates by chat or edits by hand, and sends it with one click. **Nothing is ever sent without that click.**

User stories:
- As a user, I connect Gmail once from Account settings and can disconnect at any time.
- As a user, I open a job and see its whole mail history, newest first, threads grouped, attachments listed.
- As a user, I trust the status on my board because confirmations, rejections and invitations move it automatically, with the mail that caused each change shown in the job's history; I can undo any automatic change.
- As a user, I see "Waiting for your answer" on jobs where the last message is from the company and needs a reply, with a draft already prepared.
- As a user, I click "Draft a reply", say what I want in the chat, see the draft update, edit it by hand if I like, then click Send; the reply goes out from my Gmail in the same thread.
- As a user, I can link a mail thread to a job by hand, or unlink it, when the automatic matching is wrong.

## Google setup (done 2026-10-10)

- Google Cloud project `jobapp-gmail-hth`, Gmail API enabled, OAuth consent screen "jobapp" (external), web OAuth client with redirect URIs `https://jobapp.taild83d16.ts.net/api/mail/oauth/callback` and `http://localhost/api/mail/oauth/callback`. Client id and secret are in the Jetson `.env` as `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`.
- Scopes (least privilege, decided at review): `gmail.readonly`, `gmail.send`, `openid`, `userinfo.email`. The consent screen currently lists `gmail.modify`; it is replaced by `gmail.readonly` before the first user connects.
- Publishing status (Production unverified: one warning screen per user, at most 100 users ever; a `ts.net` domain cannot be verified for branding, which is accepted): **Testing** until the app serves `/privacy` and `/terms` on its domain and the branding page lists them; then **Production, unverified** (decided by Harald: one warning screen per user, no 7-day token expiry, up to 100 users). This module ships those two public pages and a short public home page.
- Stable public URL from Tailscale Funnel (PR #13, merged 2026-10-10), required by the redirect URI.

## Connection and sync

```
mail_accounts(user_id pk fk users cascade, email text, refresh_token_encrypted bytea, key_version int,
              scopes text[], history_id text, connected_at, last_sync_at, status enum(ok, needs_reconnect, error), error text)
mail_threads(id uuid, user_id, gmail_thread_id text, job_id uuid null fk jobs set null,
             link_source enum(auto, manual), link_confidence real, subject text, participants text[],
             last_message_at, needs_reply bool, created_at, updated_at, unique(user_id, gmail_thread_id))
mail_messages(id uuid, user_id, thread_id fk cascade, gmail_message_id text, from_addr text, to_addrs text[],
              cc_addrs text[], date, subject, snippet text, body_text text, body_html_sanitized text,
              direction enum(inbound, outbound), labels text[], attachments jsonb, created_at,
              unique(user_id, gmail_message_id))
mail_drafts(id uuid, user_id, thread_id fk, job_id, body text, subject text, to_addrs text[], status enum(draft, sending, sent, failed),
            agent_thread_id uuid null, gmail_message_id text null, created_at, updated_at, sent_at)
```

- OAuth with PKCE and a signed `state` bound to the session; after the callback the connected address is read from `userinfo.email` and stored; one Gmail address can be connected to only one app user (409 otherwise); refresh token encrypted with the same `CREDENTIALS_KEY` mechanism as model credentials; access tokens kept in memory only.
- **Initial sync:** messages from the last 180 days matching a job-related query (sender or recipient domains of the user's job companies and ATS domains, plus keywords such as application, candidature, interview, entretien), limited to 5,000 messages.
- **Incremental sync:** every 5 minutes per connected user in the worker (Gmail `history.list` from the stored `historyId`; full resync when the history id expired). No Pub/Sub (keeps everything free and avoids exposing a webhook).
- Bodies stored as plain text and as HTML sanitized with `nh3` (allow-list of tags and attributes, no scripts, no event handlers, no `style` URLs), capped at 500 KB per message; remote images are blocked by default with a per-message "Show images" choice.
- **Attachments** are listed with name, type and size and never stored. Download (`GET /api/mail/messages/{id}/attachments/{att}`) checks ownership before any Gmail call, streams the bytes with `Content-Disposition: attachment` and a sanitised ASCII filename (plus RFC 5987 UTF-8 name), `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox`, and a content type sniffed from the bytes (unknown types served as `application/octet-stream`); never inline, except PDFs opened through the same blob preview as job files; 25 MB cap.
- Rate limits and backoff on 429 and 5xx; a revoked token sets `needs_reconnect` and shows a banner.

## Automatic changes: an explicit exception

SPEC-agents says nothing changes user data until the user accepts a proposal. The mail module is the one named exception, wanted by Harald: **links between mail threads and jobs** (above the confidence threshold) and **status changes from mail** (the transitions below) are applied automatically, **only under the binding rule** of the task plan (`tasks/mail/todo.md`, "The binding rule", decided 2026-10-13): Gmail's own authentication result passes for the visible sender, that sender's domain is bound to the job, and the verdict, of a rule or of the model, reaches `MAIL_AUTO_APPLY_CONFIDENCE` (default 0.90). A reference id that the mail carries is never enough alone. Anything else is a proposal the user accepts. A status change never triggers a send. They are recorded as `proposals` rows created directly with status `accepted` and `decided_by = system`, plus a job event, so every automatic change is visible, attributable to its mail, and undoable. Drafts are never sent automatically: no exception there. SPEC-agents is amended in this PR to name this exception.

## Retention and privacy

- **Disconnect** revokes the token at Google and deletes the user's `mail_accounts`, `mail_messages`, `mail_threads`, drafts and mail classifier results; job events keep only "status changed from a mail (mail deleted)". **Account deletion** cascades to every mail table.
- Nothing is pruned by age (decided 2026-10-13): the 180 days are the window of the first sync only. Storage is bounded at 20,000 stored messages or 300 MB of mail text per user, and the user can delete one thread from the app. The log of sends, which holds no address and no text, is kept 365 days, also after a disconnect.
- `/privacy` states what is read (the sender, the recipients, the subject and the preview of each new mail, and the whole mail only when it concerns a job; the search terms of the first sync), where it is stored (the user's app instance on the owner's Jetson), that message content is sent to the user's own model provider (Anthropic) only to classify, match and draft, and only after a recorded consent, that nothing is shared with anyone else, retention as above, what stays after a disconnect, how to disconnect, and that the administrator deletes an account on request. It includes the Google API Services User Data Policy **Limited Use** disclosure. `/terms` is a short plain statement.

## Matching mails to jobs

- Deterministic first: sender or recipient domain equal to the job's company domain or ATS domain (ashbyhq.com, greenhouse.io, lever.co, workday, smartrecruiters, teamtailor, welcometothejungle, linkedin jobs), job title or company name in the subject, a reference id from the posting URL in the body. Score with confidence; one job wins when its confidence is clearly above the others.
- Ambiguous threads go to a small **mail triage agent** (agents runtime, model chosen by the credential: `claude-opus-5-5` on a subscription, `claude-sonnet-5-5` on an API key; read-only tools: get_thread, list_candidate_jobs) that proposes a link; a proposal is applied automatically only under the binding rule and at the threshold, otherwise listed in an "Unmatched mail" inbox for the user.
- Manual link and unlink always win and are never overridden by later syncs.

## Status updates from mail

- A **mail classifier** (deterministic rules for common ATS templates first, then the agent for the rest) labels each inbound message: `application_received`, `rejection`, `interview_invitation`, `assessment`, `offer`, `info_request`, `other`.
- **Active statuses** are `found`, `shortlisted`, `cv_ready`, `staged`, `applied`, `interview`. `rejected`, `closed` and `skipped` are final for automation: mail never moves a job out of them.
- Allowed automatic transitions: `found`, `shortlisted`, `cv_ready`, `staged` → `applied` on `application_received` (fills `applied_on` if empty); any active status → `rejected` on `rejection`; `applied` → `interview` on `interview_invitation` or `assessment`. Nothing moves a job backwards.
- **Only newer mail moves a status:** a message changes a status only if its date is later than the job's last status change (manual or automatic). The **initial 180-day sync never applies transitions**: it only links threads and lists "suggested status changes" the user can apply in one click per job or all at once; transitions apply automatically only for messages arriving after the account was connected.
- **Recording:** a `job_events` row of kind `status` with `data = {from, to, via: "mail", message_id, proposal_id}`, the keys the jobs module writes.
- **Undo** (`POST /api/mail/status-changes/{event_id}/undo`, owned by this module): allowed only while the job still has the status that event set (else 409 with the current status); restores `from`, writes a new `status` event with `via: "mail_undo"`, marks the event and its proposal `undone`; an undone message is **never re-applied** by later syncs (the classifier result is kept with `applied = false, undone = true`).
- `needs_reply` is true when the last message of a linked thread is inbound and classified `interview_invitation`, `assessment`, `offer` or `info_request`; the board shows these as "Waiting for your answer".

## Drafting and sending

- When a thread gets `needs_reply`, the **reply agent** prepares a draft in the user's language and tone using the job, the thread, the profile (facts only, same no-invention rule as documents) and the user's answers section (availability, notice, salary policy). The draft is stored in `mail_drafts`, never sent.
- The reply agent cannot change recipients: the draft's recipients are computed by code from the addresses the user himself wrote to in the thread and from a sender bound to the job (reply or reply-all chosen by the user); any other address is flagged, never prefilled, and confirmed one by one in the Send confirmation. Mail content is data for agents, never instructions (stated in every mail agent's system prompt).
- On a job: "Draft a reply" opens the chat panel (agents threads) bound to that mail thread; each message to the agent updates the draft; the user can edit the draft text directly at any time; the latest of either wins and the agent sees manual edits.
- **Send** is a button, enabled only on an explicit click by the signed-in owner, with a confirmation showing recipients and subject; the server sends with `users.messages.send` in the same Gmail thread (`threadId`, `In-Reply-To`, `References`), records the sent message, marks the draft `sent`. Every send passes one function, `mail.gate.send`, which today accepts a single authorisation: the click of the signed-in owner on that exact draft. No worker, agent tool or schedule can build one. The gate writes its audit row before Gmail is called, and a send whose result is unknown is never sent again by the app itself.
- Sending is idempotent per draft (a double click sends once). Send is a POST protected by the app's CSRF rule (`X-Requested-With: jobapp`) and the session.
- **Model spend:** without a model credential, matching falls back to rules only, classification to rules only, and no drafts are prepared (the UI says why). Automatic runs (triage, classification, drafts) need the user's recorded consent, are capped at 100 per user per day, and share the user's four active runs, one at a time: the runner has no priority. No agent drafts for a job the user's never apply rules exclude. There is no sending limit as a product rule (decided 2026-10-13): Gmail's own quota is the ceiling.

## API

| Method and path | Does |
|---|---|
| GET /api/mail/oauth/start ; GET /api/mail/oauth/callback ; DELETE /api/mail/account | connect, disconnect (revokes the token at Google) |
| GET /api/mail/account | status, email, last sync |
| POST /api/mail/sync | sync now (rate limited) |
| GET /api/jobs/{id}/mail | threads and messages for a job |
| GET /api/mail/unmatched ; POST /api/mail/threads/{id}/link `{posting_id}` ; DELETE /api/mail/threads/{id}/link | inbox of unmatched threads, manual link |
| GET /api/mail/messages/{id}/attachments/{att} | stream one attachment from Gmail |
| GET/PUT /api/mail/drafts/{id} ; POST /api/mail/threads/{id}/drafts ; POST /api/mail/drafts/{id}/send | drafts and send |
| POST /api/mail/status-changes/{event_id}/undo | undo an automatic status change |
| GET /api/mail/suggested-changes ; POST /api/mail/suggested-changes/apply `{suggestion_ids? | posting_ids?}` | status changes suggested by the initial sync |
| GET /privacy ; GET /terms ; GET / (public home when signed out) | pages required by Google |

## Frontend

Account: "Gmail" card (connect, status, last sync, sync now, disconnect). Jobs list: "Waiting for your answer" badge and filter. Job detail: Mail tab with threads, messages (sanitized HTML in an iframe with `sandbox="allow-popups allow-popups-to-escape-sandbox"`, since an empty sandbox cannot open a link, and a strict CSP, links opened with `noopener noreferrer`, plain text fallback), attachments, link and unlink; automatic status changes shown with Undo; Draft panel with chat on the side, editable draft, Send with confirmation. `/mail/unmatched` inbox. Public `/privacy`, `/terms`, home. Mobile friendly.

## Testing strategy

- Unit: OAuth state and PKCE, token encryption, query building, history sync (new, changed, deleted messages, expired history id), MIME parsing (multipart, encodings, French accents, inline images, attachments), HTML sanitization (scripts, event handlers, remote images, CSS tricks), matching scores, classifier rules on real-looking ATS templates (Ashby, Greenhouse, Lever, Workday, WTTJ, French and English), allowed transitions, undo, needs_reply.
- Integration: a fake Gmail API server (recorded shapes) in each task's tests and in the local check (`ops/local-check.sh`; there is no CI); isolation between users; send idempotency; no path to Gmail's send call outside `mail.gate.send` (tests assert that the gate is reachable from the send endpoint alone).
- Agents (matching, classification, reply) run on the fake model backend in the same tests.
- End to end on the Jetson with Harald's real Gmail (connected by him through the OAuth screen; read-only checks first, one real reply sent only to his own address as a test, never to a company).

## Boundaries

- **Always:** user click before any send; undo for every automatic status change; owner-only access; sanitized rendering.
- **Ask first:** widening scopes; auto-send of any kind; keeping attachment contents; syncing beyond 180 days.
- **Never:** send mail outside `mail.gate.send`, or from a worker, a schedule or an agent tool; log message bodies or tokens; delete mail in Gmail.

## Success criteria

1. Harald connects Gmail and sees, on his real jobs, the mail threads that belong to them, with few wrong links and an inbox for the rest.
2. Confirmations, rejections and invitations move statuses automatically, each with its source mail and an Undo.
3. "Waiting for your answer" jobs have a prepared draft; drafting with the agent and editing by hand both work; Send replies in the same Gmail thread.
4. No mail is ever sent without the user's click (automated test and code review).
5. Each task's tests and the local check cover sync, matching, classification, drafting and send with a fake Gmail and a fake model; no cross-user access.

## Open questions

The owner answered the plan's questions on 2026-10-13. His decisions, and the questions still open, are at the top of `tasks/mail/todo.md`.
