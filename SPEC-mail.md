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
- Scopes: `gmail.modify` (read, label, no permanent delete), `gmail.send`, `openid`, `userinfo.email`.
- Publishing status: **Testing** until the app serves `/privacy` and `/terms` on its domain and the branding page lists them; then **Production, unverified** (decided by Harald: one warning screen per user, no 7-day token expiry, up to 100 users). This module ships those two public pages and a short public home page.
- Stable public URL from Tailscale Funnel (platform PR in progress).

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

- OAuth with PKCE and a signed `state` bound to the session; refresh token encrypted with the same `CREDENTIALS_KEY` mechanism as model credentials; access tokens kept in memory only.
- **Initial sync:** messages from the last 180 days matching a job-related query (sender or recipient domains of the user's job companies and ATS domains, plus keywords such as application, candidature, interview, entretien), limited to 5,000 messages.
- **Incremental sync:** every 5 minutes per connected user in the worker (Gmail `history.list` from the stored `historyId`; full resync when the history id expired). No Pub/Sub (keeps everything free and avoids exposing a webhook).
- Bodies stored as plain text and as sanitized HTML (no scripts, no remote images by default); attachments listed with name, type and size, downloaded on demand through the API, never stored.
- Rate limits and backoff on 429 and 5xx; a revoked token sets `needs_reconnect` and shows a banner.

## Matching mails to jobs

- Deterministic first: sender or recipient domain equal to the job's company domain or ATS domain (ashbyhq.com, greenhouse.io, lever.co, workday, smartrecruiters, teamtailor, welcometothejungle, linkedin jobs), job title or company name in the subject, a reference id from the posting URL in the body. Score with confidence; one job wins when its confidence is clearly above the others.
- Ambiguous threads go to a small **mail triage agent** (agents runtime, model claude-opus-5-5, read-only tools: get_thread, list_candidate_jobs) that proposes a link; proposals are applied automatically only above a confidence threshold, otherwise listed in an "Unmatched mail" inbox for the user.
- Manual link and unlink always win and are never overridden by later syncs.

## Status updates from mail

- A **mail classifier** (deterministic rules for common ATS templates first, then the agent for the rest) labels each inbound message: `application_received`, `rejection`, `interview_invitation`, `assessment`, `offer`, `info_request`, `other`.
- Allowed automatic transitions: staged or applied → applied (`application_received`, fills `applied_on` if empty), any active status → rejected (`rejection`), applied → interview (`interview_invitation` or `assessment`). Every automatic change writes a job event with the message id and can be undone in one click; nothing moves a job backwards or out of `closed` or `skipped`.
- `needs_reply` is true when the last message of a linked thread is inbound and classified `interview_invitation`, `assessment`, `offer` or `info_request`; the board shows these as "Waiting for your answer".

## Drafting and sending

- When a thread gets `needs_reply`, the **reply agent** prepares a draft in the user's language and tone using the job, the thread, the profile (facts only, same no-invention rule as documents) and the user's answers section (availability, notice, salary policy). The draft is stored in `mail_drafts`, never sent.
- On a job: "Draft a reply" opens the chat panel (agents threads) bound to that mail thread; each message to the agent updates the draft; the user can edit the draft text directly at any time; the latest of either wins and the agent sees manual edits.
- **Send** is a button, enabled only on an explicit click by the signed-in owner, with a confirmation showing recipients and subject; the server sends with `users.messages.send` in the same Gmail thread (`threadId`, `In-Reply-To`, `References`), records the sent message, marks the draft `sent`. A draft is never sent by a worker, an agent tool or a schedule: there is no code path from agents to `messages.send`.
- Sending is idempotent per draft (a double click sends once).

## API

| Method and path | Does |
|---|---|
| GET /api/mail/oauth/start ; GET /api/mail/oauth/callback ; DELETE /api/mail/account | connect, disconnect (revokes the token at Google) |
| GET /api/mail/account | status, email, last sync |
| POST /api/mail/sync | sync now (rate limited) |
| GET /api/jobs/{id}/mail | threads and messages for a job |
| GET /api/mail/unmatched ; POST /api/mail/threads/{id}/link `{job_id}` ; DELETE /api/mail/threads/{id}/link | inbox of unmatched threads, manual link |
| GET /api/mail/messages/{id}/attachments/{att} | stream one attachment from Gmail |
| GET/PUT /api/mail/drafts/{id} ; POST /api/mail/threads/{id}/drafts ; POST /api/mail/drafts/{id}/send | drafts and send |
| POST /api/jobs/{id}/events/{event_id}/undo | undo an automatic status change |
| GET /privacy ; GET /terms ; GET / (public home when signed out) | pages required by Google |

## Frontend

Account: "Gmail" card (connect, status, last sync, sync now, disconnect). Jobs list: "Waiting for your answer" badge and filter. Job detail: Mail tab with threads, messages (sanitized HTML in a sandboxed frame, plain text fallback), attachments, link and unlink; automatic status changes shown with Undo; Draft panel with chat on the side, editable draft, Send with confirmation. `/mail/unmatched` inbox. Public `/privacy`, `/terms`, home. Mobile friendly.

## Testing strategy

- Unit: OAuth state and PKCE, token encryption, query building, history sync (new, changed, deleted messages, expired history id), MIME parsing (multipart, encodings, French accents, inline images, attachments), HTML sanitization (scripts, event handlers, remote images, CSS tricks), matching scores, classifier rules on real-looking ATS templates (Ashby, Greenhouse, Lever, Workday, WTTJ, French and English), allowed transitions, undo, needs_reply.
- Integration: a fake Gmail API server (recorded shapes) in CI; isolation between users; send idempotency; no code path from the worker or agents to send (a test asserts the send function is only reachable from the send endpoint).
- Agents (matching, classification, reply) run on the fake model backend in CI.
- End to end on the Jetson with Harald's real Gmail (connected by him through the OAuth screen; read-only checks first, one real reply sent only to his own address as a test, never to a company).

## Boundaries

- **Always:** user click before any send; undo for every automatic status change; owner-only access; sanitized rendering.
- **Ask first:** widening scopes; auto-send of any kind; keeping attachment contents; syncing beyond 180 days.
- **Never:** send mail from a worker, a schedule or an agent tool; log message bodies or tokens; delete mail in Gmail.

## Success criteria

1. Harald connects Gmail and sees, on his real jobs, the mail threads that belong to them, with few wrong links and an inbox for the rest.
2. Confirmations, rejections and invitations move statuses automatically, each with its source mail and an Undo.
3. "Waiting for your answer" jobs have a prepared draft; drafting with the agent and editing by hand both work; Send replies in the same Gmail thread.
4. No mail is ever sent without the user's click (automated test and code review).
5. CI covers sync, matching, classification, drafting and send with a fake Gmail and a fake model; no cross-user access.

## Open questions

None blocking; defaults above are proposed for approval.
