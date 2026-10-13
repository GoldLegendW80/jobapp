# Evidence: issue #100, request bodies from a caller with no session

Measured on a scratch Compose stack on the Mac (`docker compose -p jobapp-p100`), through Caddy on
`http://127.0.0.1:8173`, loopback only, removed afterwards. Memory is `docker stats` of the api and
caddy containers (file cache included) unless a line says cgroup peak; it compares before and after
on this Mac and is not what the Jetson would show. **Every figure is from one run.**

Scripts: `measure.py` (30 connections per scenario, held 8 seconds), `caddy_hold.py` (how long one
stalled connection is held), `concurrency.py` (the uvicorn limit, and sign ins at once),
`legit_use.py` (ordinary use through Caddy).

## What a caller with no session can make the api hold

Before (`before.json`, `main` at b2d3103): 30 connections, each sending 9 MiB of a declared 9.5 MiB
`POST /api/auth/login` body, then stalling. The api went from about 115 MiB at rest to about
375 MiB, about 8.7 MiB per connection; 377 MiB on `POST /api/auth/invites/{token}/accept`. The
later scenarios of that run read 376 to 378 MiB, which is the level the first two left behind:
the api's memory did not come back down within the run. That run used an earlier harness, which
stopped a scenario at the first connection the server ended and lacked the last three scenarios.

After (`after.json`, final code, api and caddy restarted first; rest 112 MiB):

| scenario, no session | attempted | ended by the server while sending | api peak |
|---|---|---|---|
| 9 MiB of a declared 9.5 MiB, `/api/auth/login` | 30 | 18 | 112 MiB |
| same, `/api/auth/invites/{token}/accept` | 30 | 12 | 112 MiB |
| same, `/api/auth/logout` | 30 | 18 | 112 MiB |
| chunked body that never ends, login | 30 | 0 | 115 MiB |
| request headers never finished | 30 | 0 | 115 MiB |
| connections that send nothing | 30 | 0 | 115 MiB |
| 9 MiB of a declared 9.5 MiB, `/api/agents/runs` (needs a session) | 30 | 0 | 115 MiB |
| 990 KB of a declared 1 MB, `/api/agents/runs` | 30 | 0 | 126 MiB |
| 64 KB of a declared 65 KB, `/api/auth/login` | 30 | 0 | 115 MiB |

The row at 126 MiB is the one that held about 0.9 MiB per connection (139 MiB) one commit earlier,
before the session was checked ahead of the body. The 11 MiB it still shows in one sample were
gone in the next row and were not investigated. A sign in with a small body answered 401 in
177 ms before and 85 ms after (one request each).

## How long one stalled connection is held (caddy_hold.py)

| connection | before (main) | after |
|---|---|---|
| declared 9 MB body to sign in, a few bytes, then silence | 504 after 60.0s | closed with no answer after 60.0s |
| request headers left unfinished | 400 after 60.0s | 400 after 10.0s |
| opens, sends nothing | closed after 60.0s | closed after 10.0s |

The first row is unchanged in time: Caddy's own rule for a silent body is left at its default
minute (see the Caddyfile for why). What changed is what is held meanwhile, which the table above
shows. With an earlier setting of this branch (`read_body_idle 15s 1024`, withdrawn) that row read
413 after 15.0s, and Caddy's log showed the request answered by Caddy itself with `bytes_read: 0`;
that log line was not read again on the final configuration.

## The uvicorn limit, and sign ins at once (concurrency.json)

- 260 connections each holding 64 KB of a declared 65 KB sign in body: all 260 held. While they
  were held, a sign in and `GET /api/health` both answered **503**; after they were released, 401
  and 200. The api went from 118 to 160 MiB, caddy from 17 to 52 MiB. So `--limit-concurrency 256`
  bounds what is held, and 256 slow connections are also what it takes to make the app answer 503
  to everyone while they last. The 503 was not traced to uvicorn in this run; the api test
  `test_socket_limit_concurrency_counts_open_connections_and_answers_503` shows uvicorn doing it.
- **4 sign ins sent at once** (unknown addresses, small bodies): all 401 in 0.17 s, and the api's
  cgroup memory peak went from 173 to 378 MiB (rest 116 MiB). The sampler, which asks about once a
  second, saw 118. About 65 MiB per sign in in flight, which matches the password hash settings
  (argon2id, 64 MiB). Not a request body, not changed by this branch, see the pull request.

## Ordinary use through Caddy, signed in (legit-use.json, one request each)

- Sign in with a wrong password: 401.
- An event stream: open 75.6 s, past Caddy's 60 s idle timeout, 5 heartbeats, still open when the
  client left. It is the progress stream of a sweep row left running by hand, which uses the same
  `sse.response` as an agent run's stream; it is not an agent run.
- Profile photo, a valid PNG: 200.
- Document upload (ingestion G1): 4 KB, 201; 12 MiB, 201 in 0.24 s (over the 10 MiB default, so
  the upload's own 20 MiB class at Caddy applied). Paste of 1,000 characters: 201.

## Not measured, not run

- An agent run's own stream past a minute; uploads or pastes that stall (G1's own tests cover
  their 30 s and 60 s rules at the api, and passed with this branch merged, 289 tests with the
  body limit tests; nothing here sent a stalled upload through Caddy).
- More than 260 connections. Reading 800 connections as Caddy's limit from 35 MiB per 260 would
  be an extrapolation.
- A caller with a session on ordinary JSON routes, beyond the tests.
- `tests/compose_smoke.sh` as a whole (it starts its own stack). Of its assertions for this issue, the
  64 KiB limit and the unfinished headers were run by hand on an earlier state of the branch
  and held; the 401 on `POST /api/jobs/import` was written after and not run through Caddy. An assertion that was already in that script, `POST /api/health`
  with exactly 10 MiB expecting 405, answered 403 on the scratch stack (no CSRF header), so the
  script as it stands would stop there.
- The whole api suite on the merged tree, locally: it was started and stopped before it ended.
  The last whole run was on the commit before the merges (3333 passed, 19 skipped).
