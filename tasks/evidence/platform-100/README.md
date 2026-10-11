# Evidence: issue #100, public request body limits

All measured on a scratch Compose stack on the Mac (`docker compose -p jobapp-p100`), through Caddy
on `http://127.0.0.1:8173`, loopback only. `measure.py` is the harness; `caddy_hold.py` times how
long a stalled connection is held. Memory is `docker stats` of the api and caddy containers (file
cache included), so the numbers are relative before/after on the same machine, not absolute.

## Attack: a caller WITH NO session, memory held

`measure.py` opens 30 connections, each holding a roughly 9 MiB stalled login or acceptance body
under Caddy's 10 MiB edge limit (`before.json`, `after.json`).

| | api RSS at rest | api RSS under the attack | legit login |
|---|---|---|---|
| before (main) | ~115 MiB | ~376 MiB (about 8.7 MiB per connection; ~50 connections reach the 512 MiB limit) | 401 in 177 ms |
| after | ~115 MiB | ~115 to 117 MiB (flat) | 401 in 77 ms |

After the fix the declared 9 MiB bodies to `/api/auth/*` are refused by Caddy itself (413,
`bytes_read: 0`, never proxied to the api); the chunked and slow variants are let through only up
to 64 KiB, so the api buffers nothing of note. Caddy stayed around 17 to 22 MiB throughout.

## How long a stalled connection is held (caddy_hold.py)

| connection | before (main) | after |
|---|---|---|
| declared large body, then silence | 60.0s, 504 | 15.0s, 413 (read_body_idle 15s; body never reaches the api) |
| slow request headers | 60.0s, 400 | 10.0s, 400 (read_header 10s) |
| opens, sends nothing | 60.0s | 10.0s (read_header 10s) |

## Delivered over-limit bodies are cut promptly (through Caddy)

- `POST /api/auth/login` 200 KB pushed (> 64 KiB): 413 in 5 ms.
- `POST /api/auth/login` 1 KB (< 64 KiB): 422 in 10 ms (the handler ran: login works).
- `POST /api/agents/runs` 11 MiB pushed (> 10 MiB edge): 413 in 511 ms.

## Legitimate use still works (through Caddy, signed in)

- A valid 1x1 PNG to `PUT /api/profile/photo`: 200 in 32 ms (upload works).
- 1.5 MiB (over the 1 MiB api default) to the same self-limited route: 415 in 86 ms, so the body
  reached the route and its own 2 MiB limit governs, the body-limit middleware did not cap it.
- Event streams: production SSE sends a heartbeat every 15s and Caddy's write_idle stays at the
  1m default (only read and idle timeouts were shortened), so an active stream is never cut by the
  60s idle timeout, which applies to idle keep-alive connections between requests. Real streaming
  through this Caddyfile is exercised in CI by web/e2e/agents-streaming.spec.ts.
