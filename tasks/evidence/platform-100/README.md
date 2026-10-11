# Evidence: issue #100, public request body limits

All measured on a scratch Compose stack on the Mac (`docker compose -p jobapp-p100`), through Caddy
on `http://127.0.0.1:8173`, loopback only. `measure.py` is the harness; `caddy_hold.py` times how
long one stalled connection is held. Memory is `docker stats` of the api and caddy containers (file
cache included), read on this Mac: the numbers compare before and after on the same machine and are
not what the Jetson would show. Each figure comes from one run.

## What a caller with no session can make the api hold

`measure.py` opens 30 connections per scenario and holds them 8 seconds.

Before (`before.json`, `main` at b2d3103): each connection declares 9.5 MiB and sends 9 MiB of a
`POST /api/auth/login` body, then stalls. The api went from about 115 MiB at rest to about 375 MiB,
about 8.7 MiB per connection. The same on `POST /api/auth/invites/{token}/accept` (377 MiB).
The later scenarios of that run (logout, chunked, slow headers, idle) read 376 to 378 MiB, which
is the level the first two left behind, not something those scenarios added: the api's memory did
not come back down within the run. That run used an earlier version of the harness, which stopped
a scenario at the first connection the server ended, and did not have the last three scenarios
below.

After (`after.json`, the api restarted first):

| scenario, no session | attempted | ended by the server while sending | api peak |
|---|---|---|---|
| 9 MiB of a declared 9.5 MiB, `/api/auth/login` | 30 | 15 | 109 MiB (rest: 109) |
| same, `/api/auth/invites/{token}/accept` | 30 | 20 | 109 MiB |
| same, `/api/auth/logout` | 30 | 14 | 109 MiB |
| chunked body that never ends, login | 30 | 0 | 112 MiB |
| slow headers | 30 | 0 | 112 MiB |
| connections that send nothing | 30 | 0 | 123 MiB |
| 9 MiB of a declared 9.5 MiB, `/api/agents/runs` (needs a session) | 30 | 0 | 113 MiB |
| 990 KB of a declared 1 MB, `/api/agents/runs` (under the api's 1 MiB default) | 30 | 0 | 139 MiB |
| 64 KB of a declared 65 KB, `/api/auth/login` (under the public limit) | 30 | 0 | 140 MiB |

Read with care: the api's memory does not come back down within a run, so a later row shows at
least the level of the rows before it. The row that adds memory is the one under the 1 MiB
default: about 27 MiB for 30 connections, close to 0.9 MiB each. That is what a caller with no
session could still make the api hold at this commit, on any JSON route that answers 401, because
FastAPI reads the body before the 401. The 123 MiB on the idle row was not investigated.

A sign in with a small body answered 401 in 177 ms before and in 112 ms after (one request each).

## How long one stalled connection is held (caddy_hold.py)

| connection | before (main) | after |
|---|---|---|
| declared 9 MB body to login, a few bytes, then silence | answered 504 after 60.0s | answered 413 after 15.0s |
| request headers left unfinished | answered 400 after 60.0s | answered 400 after 10.0s |
| opens, sends nothing | closed after 60.0s | closed after 10.0s |

For the first row after the change, Caddy's access log shows the request handled in 0.28 ms with
status 413 and `bytes_read: 0`: Caddy answered it itself and did not proxy it. The client still
only saw the answer when the connection ended, 15 seconds later.

## Bodies sent whole, through Caddy (one request each)

- `POST /api/auth/login`, 200 KB: 413 in 5 ms.
- `POST /api/auth/login`, 1 KB that is not JSON: 422 in 10 ms (the route answered).
- `POST /api/agents/runs`, 11 MiB, no session: 413 in 511 ms.

## Legitimate use, through Caddy, signed in (one request each)

- A valid 1x1 PNG to `PUT /api/profile/photo`: 200 in 32 ms.
- 1.5 MiB of zero bytes to the same route: 415 in 86 ms. The route read the body under its own
  2 MiB limit and refused it as not an image; the api's 1 MiB default did not apply to it.

## Not measured

- An event stream held open past Caddy's 60 second idle timeout. What is known: the stream sends
  a heartbeat every 15 seconds (`api/app/sse.py`), Caddy's `write_idle` was left at its default of
  one minute, and the api's stream tests pass with the middleware in place. That is an argument,
  not a run of a stream through Caddy for more than a minute.
- `--limit-concurrency 256`: no test and no measurement of what it does under these scenarios.
- A caller with a session on ordinary JSON routes.
- `tests/compose_smoke.sh` was not run as a whole: it starts its own stack on the port the
  scratch stack used. The assertions added to it were run by hand against the scratch stack and
  passed. An assertion that was already in that script, `POST /api/health` with exactly 10 MiB
  expecting 405, answered 403 here (no CSRF header), so the script as it stands would stop there
  before reaching the new block.
