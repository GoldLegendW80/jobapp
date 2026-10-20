# Proposal: Gmail through an MCP server

Status: **proposal, waiting for the owner's answers.** Nothing here is decided until the owner
answers the questions at the end; `SPEC-mail.md` and `tasks/mail/todo.md` are not changed by this
document and still describe the module as built and planned. Refs #190 (the owner's decision),
#187 (three questions held for this revision), #357 (the mail lane).

## The decision this answers

Owner, 2026-10-21, on #190:

1. Mail goes **through an MCP server** rather than the app calling the Gmail API directly.
2. Each person always uses their **real Gmail account**.
3. **Only the connected address counts as the person.** No alias handling: a thread written from a
   "send as" alias is treated as someone else's mail and fails closed (`user_wrote` misses it, the
   classifier may read it as inbound).

## What exists today (main at 4ddc686d)

Merged: J8, M1, M2, M3, M4, M9. Paused then reopened on 2026-10-20: M5 to M23 (#357).

| Piece | Where | What it does |
|---|---|---|
| M1 Google client | `api/app/mail/google.py`, `errors.py`, `log.py` | Every HTTP call to Google: the OAuth exchange, refresh, revoke, `userinfo`, and the Gmail read calls (`getProfile`, `messages.list`, `messages.get`, `history.list`, `threads.get`, `attachments.get`). Timeouts, size caps, retries, a per user quota budget, access tokens in memory only. No send function, by design. |
| M1 fake | `api/tests/mail/fake_gmail.py`, `tests/gmail-stub.compose.yml` | The fake Google every test uses. |
| M3 parsing | `parse.py`, `auth.py`, `domains.py`, `hosts.py`, `types.py`, `ingestion/sandbox/mail_html.py` | A Gmail `format=full` message into stored text; Gmail's own `Authentication-Results` read for rule A of the binding rule. |
| M4 connection | `oauth.py`, `accounts.py`, `account_router.py`, `locks.py`, `purge.py` | OAuth with PKCE and a signed state, the refresh token encrypted with `CREDENTIALS_KEY`, one Gmail address per app user, consent, the three step disconnect with revocation. |
| M9 fact check rules | `factcheck_rules.py` | Pure checks of a draft against the profile. |
| Agents runtime | `api/app/agents/runner.py`, `tools.py` | Each run gets one in process MCP server (`create_sdk_mcp_server`), built per run and bound to the user, with `strict_mcp_config=True`, an allow list, and a PreToolUse hook that denies anything else. The model never chooses whose data it touches. |

## What changes and what stays, whatever the option

**Still needed by any Gmail access, MCP or not.** Google gives a mailbox to no one without the
person's consent on Google's own screen and an OAuth client of a Google Cloud project. Every Gmail
MCP server we found (below) needs the same three things the app already has: an OAuth client id
and secret, a per user refresh token, and the scopes. So the project `jobapp-gmail-hth`, the
consent screen, the scopes (`gmail.readonly`, `gmail.send`, `openid`, `userinfo.email`), the
publishing to Production unverified, `/privacy` and `/terms` (M2), and the consent of M4 all stay.
An MCP server moves **where the Gmail calls are made**, not the need to hold a token.

**What the MCP server replaces.**

- The Gmail read calls of the M1 client (`messages.list`, `messages.get`, `history.list`,
  `threads.get`, `attachments.get`, `getProfile`), so that no app module calls Gmail itself.
- The calls of the first sync (M7) and of the incremental sync (M10).
- The one send request of the gate (M12, `gate._call_gmail`), in options A and B.

**What stays exactly as planned.**

- Parsing (M3): the server must return Gmail's `format=full` payload with every header, because
  rule A of the binding rule reads the topmost `Authentication-Results`, the `Received` above it
  and `X-Gmail-Fetch-Info`. A tool that returns a summary (subject, sender, text) is not enough
  and would make every mail untrusted.
- The binding rule and linking (M5, M11b), classification (M6, M13), recipients by code (M8).
- The fact check (M9 rules, M20 by model) and the guardrails.
- **The send gate as the one function.** `gate.send` stays the only path to a send, with its audit
  row written before the call, `send_unknown`, no resend by the app. No agent ever has a send tool.
  No auto send: hard rule 3 of `CLAUDE.md` is untouched.
- The fake Google at the HTTP boundary (M1): the MCP server is pointed at it in tests, so no test
  ever reaches a real Google host.
- Aliases (owner, 2026-10-21): the server's `getProfile` address must equal
  `mail_accounts.email`, compared at every call; a mismatch refuses the call with `not_connected`.

## What exists outside: free Gmail MCP servers (read on 2026-10-21)

| Server | Licence, language | How it authenticates | Several users on one Jetson |
|---|---|---|---|
| `taylorwilsdon/google_workspace_mcp` (workspace-mcp) | MIT, Python | Its own Google OAuth client; tokens in a credentials directory, Valkey or memory; or `EXTERNAL_OAUTH21_PROVIDER`, where it only takes a Google bearer access token per request and stores nothing (stateless mode). Flags `--tools gmail`, `--read-only`, `--disabled-tools`. | Yes, it is the only mature one built for it (OAuth 2.1 multi user over streamable HTTP). arm64 image not published as far as we read: built from its Dockerfile. Has a send tool, removable by flag. |
| `GongRzhe/Gmail-MCP-Server` (`@gongrzhe/server-gmail-autoauth-mcp`) | MIT, Node | One `gcp-oauth.keys.json` and one `credentials.json` per process, a browser flow on `localhost:3000`. | No: one process per user, a file per user. `send_email` sends at once. |
| `gmail-multi-user-mcp` (PyPI, v0.1.0) | Python | Multi user OAuth, tokens encrypted in SQLite or Supabase. | Designed for it, but an early release. |
| `flujo-app/mcp-gmail-oauth` | | SQLite, refresh token rotation, one Google account per principal. The author warns about unverified dynamically registered clients. | Yes in principle; early. |
| Google's hosted Gmail MCP (Workspace developer preview, 2026) | Google hosted | Google's OAuth; reported to ask `gmail.compose`. | Not self hosted, preview only, secondary sources disagree on its tools. Ruled out for now. |
| The claude.ai Gmail connector | Anthropic hosted | The claude.ai account of the person. | Bound to a claude.ai account, not to an app user, not callable by the app's worker. Ruled out. |

What this tells us:

1. **Every one still needs an OAuth client and a per user token.** None removes M4.
2. **Most return summaries, not the raw headers.** workspace-mcp's message tools return formatted
   text; whether one returns the full header list that rule A needs has to be checked on its code
   at a pinned version. If not, a third party server cannot feed M3 and the binding rule.
3. **None exposes `history.list`.** Incremental sync through them would become a search every five
   minutes, which costs more quota and loses deletions and label changes (SPAM, TRASH).
4. **The safe multi user shape is stateless:** the server takes a short lived access token per
   request and holds no refresh token. Otherwise a third party process stores every person's
   refresh token in its own format, outside `CREDENTIALS_KEY`, its rotation and its purge.

## Option A: the app's own Gmail MCP server

A small service `gmailmcp` in the stack (Python, the api image, about 100 to 150 MB; measured
before merge against the 5 GB budget of `compose.yml`), built from the merged M1 client. It is the
**only process that talks to Gmail**. It speaks MCP over streamable HTTP on an internal network
only (no route through Caddy, no Funnel), like the parser on its own network.

- **Tools for the sync** (client: the worker): `list_messages(q, page_token)`,
  `get_message(id, format)`, `list_history(start_history_id)`, `get_thread(id)`, `get_profile()`.
  They return Gmail's JSON as is, so M3 parses exactly what it parses today.
- **Tools for agents** (client: the agent runner): agents never see Gmail ids of other threads or a
  search box. Their mail tools stay app tools of the per run in process server, bound to one thread
  by `bind` (M17, M19 as planned), and read the stored, parsed mail. If the owner wants agents to
  read the live mailbox (question 5), a bounded `search_mailbox` app tool proxies to `gmailmcp`
  with the run's user fixed by code, results clipped, `private_io`.
- **Send:** `gmailmcp` has one tool `send_message(capability)`, never in any agent's allow list,
  never in `strict_mcp_config`. The capability is minted by `gate.send` after its decision
  transaction commits the `pending` audit row: an HMAC over `{send_id, user_id, draft_id,
  content_hash, expires_at}` with a key only the gate and `gmailmcp` hold, valid once and for 60 s.
  `gmailmcp` refuses any send without a valid unused capability and rebuilds the MIME from the
  stored draft, not from the request. So the gate stays the one function; the request moves.
- **Tokens:** the refresh token stays where M4 stores it, encrypted with `CREDENTIALS_KEY`. Two
  ways (question 4): (A1) the caller refreshes and passes a short lived access token per call, so
  `gmailmcp` holds no secret at rest; (A2) `gmailmcp` alone holds the decrypt key and the refresh,
  so neither api nor worker ever holds a Gmail token.
- **Isolation between users:** every call carries the user id set by the caller's code, never by a
  model; `gmailmcp` checks the token's address against `mail_accounts.email`; the internal network
  admits only the worker and the api; the canary of M1 runs over its logs.

| | |
|---|---|
| Security | Best of the three: the app's own code, the same scrubber, caps and backoff; send only with the gate's capability; no agent can reach `gmailmcp` directly. |
| Cost | Free. One more container on the Jetson. |
| Effort | M1 reused whole (moved behind the server, plus a thin MCP layer: one new task, "M1b the Gmail MCP server"). M4 unchanged (A1) or its refresh moved (A2). M7 and M10 call MCP tools instead of `google.py` (same shapes, so a small change in tasks not yet built). M12 mints a capability instead of calling Gmail. M15 attachments: streamed through `gmailmcp`. M21 adds the new service to the privacy proof. M3, M5, M6, M8, M9, M11, M13, M14, M16, M17 to M20, M22, M23 unchanged. |
| Risk | Medium low: one more hop and one more service to keep alive; streaming 25 MB attachments over MCP needs a chunked tool or a side channel (measure). |

## Option B: a third party Gmail MCP server, sandboxed

`workspace-mcp` (MIT, the only mature multi user one), pinned to a reviewed version, built for
arm64, `--tools gmail --read-only`, in its own container on an internal network, in stateless
external token mode: the app keeps the refresh token (M4) and passes an access token per request.
One container for all users (a container per user costs about 150 MB each and does not fit five
users in the budget).

| | |
|---|---|
| Security | Third party code reads every person's mail; every upgrade needs a review. In stateless mode it stores no token. Isolation between users rests on its own per request handling, which we cannot prove with our tests as well as our own code. Send: `--read-only` removes it, so the gate needs a second path anyway (keep `gate._call_gmail` or option A's send). |
| Cost | Free. |
| Effort | Unknown until checked: if its tools do not return the raw headers and `history.list`, M3's input changes (rule A becomes impossible: every mail untrusted, so nothing applies by itself) and M7, M10 become search based. M1's client is kept for the token exchange and for the gate. |
| Risk | High: the binding rule may not survive, supply chain, and a tool shape that changes with its releases. |

## Option C: the Gmail API for sync and send, MCP for the agents only

Nothing about Google changes: M1 stays the client of the worker and the api, M7 and M10 sync
through it, the gate sends through `_call_gmail`. Agents read and draft only through MCP tools:
the app tools of the per run in process server, bound to one thread (`get_thread`,
`list_candidate_jobs`, `write_draft`), as the plan already has them for M17 and M19.

| | |
|---|---|
| Security | As planned today, already reviewed four times on M1, M3, M4. Agents never hold a token or reach Google. |
| Cost | Free. No new container. |
| Effort | None beyond the plan: M5 to M23 as written. |
| Risk | Low technically; it may not be what the owner meant by "through an MCP server" (question 2). |

## Recommendation

**Option A, variant A1**, if the owner means that no app module should call Gmail itself: one own
Gmail MCP server built from the merged M1 client keeps every header the binding rule needs,
`history.list`, the caps and the scrubber, adds one container and one task (M1b), and keeps the
gate as the only path to a send through a one time capability. Option B is not recommended: the
third party servers return summaries, have no history, and would put every person's mail in code we
do not review. If the owner meant only that the **agents** work through MCP, option C is already
the plan and nothing needs rebuilding.

## Questions for the owner

1. **Which architecture?**
   (a) A, the app's own Gmail MCP server, the only process that talks to Gmail (recommended);
   (b) B, a reviewed third party server (`workspace-mcp`) in a sandbox;
   (c) C, keep the Gmail API for sync and send, MCP only for the agents (the plan as it is).
2. **What must go through MCP?**
   (a) everything: sync, agents and the send request (recommended with A);
   (b) sync and agents, the gate keeps its direct send call;
   (c) agents only (that is C).
3. **The send request, if it moves.**
   (a) `gmailmcp` sends only with a one time capability minted by the gate after the audit row is
   committed, never listed for any agent (recommended);
   (b) the gate keeps its own direct Gmail call and the MCP server has no send tool at all.
4. **Where the refresh token lives.**
   (a) where M4 stores it; the worker and the api refresh and pass a short lived access token per
   call, so the MCP server holds no secret (recommended, no change to merged M4);
   (b) the MCP server alone holds the decrypt key and refreshes, so api and worker never hold a
   Gmail token (moves part of M4);
   (c) the third party server stores the tokens in its own store (only with B, not recommended).
5. **May an agent read the live mailbox?**
   (a) no, agents read only the stored mail of the thread they are bound to (recommended, the plan);
   (b) yes, one bounded `search_mailbox` tool fixed to the person by code, results clipped and never
   stored or shown;
   (c) yes, the full read tools of the server.
6. **CSRF on the OAuth start (held from #187, item 8).** With A or C the app still owns the OAuth
   flow, so the question is unchanged; with B and its own OAuth it would move to that server.
   (a) start becomes a POST with the app's CSRF header, and the page shows the connected address on
   return (recommended);
   (b) keep the GET, show the connected address on return, and ask the person to confirm it before
   any sync starts;
   (c) keep it as merged.
7. **The `http://localhost` redirect URI (#187, owner note 1).**
   (a) remove it from the production OAuth client and create a second OAuth client for local
   development (recommended);
   (b) remove it, and develop only against the fake Google;
   (c) keep it on the production client.
8. **The callback without a session (#187, owner note 4).**
   (a) redirect to `/login` with nothing consumed and the code dropped from the address, plus the
   Caddy log filter on any `oauth/` query (recommended);
   (b) keep the 401 JSON and fix only the Caddy Referer and log filters;
   (c) redirect to `/login` and resume the connection after sign in, with the state still valid.

## Sources

- [taylorwilsdon/google_workspace_mcp](https://github.com/taylorwilsdon/google_workspace_mcp), and its listing on [Glama](https://glama.ai/mcp/servers/%40taylorwilsdon/google_workspace_mcp)
- [GongRzhe Gmail MCP Server docs](https://docsearch.algolia.com/mcp/docs/repo/gongrzhe/gmail-mcp-server), [npm mirror](https://npmjs.com/package/@mjamei/gmail-mcp)
- [gmail-multi-user-mcp on PyPI](https://pypi.org/project/gmail-multi-user-mcp/)
- [flujo-app/mcp-gmail-oauth on Glama](https://glama.ai/mcp/servers/flujo-app/mcp-gmail-oauth)
- [Google Workspace Updates, agent tools for developers (May 2026)](https://workspaceupdates.googleblog.com/2026/05/agent-tools-and-security-updates-for-workspace-developers.html), [Gmail MCP comparison](https://www.dragapp.com/blog/gmail-mcp/)

Read on 2026-10-21. Tool shapes and header coverage of the third party servers are not verified
on their code: the first step of option B would be to check them at a pinned version.
