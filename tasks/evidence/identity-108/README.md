# Evidence: issue #108, sign ins at once from a caller with no session

Measured on the development Mac, not the Jetson, and not through Caddy or in a container: the
real api (`uvicorn app.main:create_app`, one process, production argon2 parameters, proxy
headers on) started from a checkout by `measure.py`, on a throwaway Postgres 16 and Redis 7
(`docker run --rm`, loopback ports, removed afterwards). Memory is the resident memory of that
uvicorn process (`ps -o rss`, sampled every 25 ms, so a short peak can be missed); it compares
before and after on this Mac and is not what the Jetson's container would show. Every sign in
came from its own client address (`X-Forwarded-For`), so the per address limit never cut in.
**Every figure is from one run.**

Before is `origin/main` at 0998726 (`before.json`), after is this branch (`after.json`).

| scenario | before: answers | before: peak | after: answers | after: peak |
|---|---|---|---|---|
| at rest, before any sign in | | 145 MiB | | 141 MiB |
| one sign in at a time (unknown email), six in a row | 401, median 144 ms | 202 MiB | 401, median 85 ms | 206 MiB |
| 40 sign ins at once, unknown emails | 40 x 401 in 1.24 s | 656 MiB | 25 x 401, 15 x 503 in 1.12 s | 276 MiB |
| 120 sign ins at once, unknown emails | 120 x 401 in 3.45 s | 782 MiB | 33 x 401, 87 x 503 in 2.46 s | 283 MiB |
| 10 real sign ins at once (right password) | 10 x 200 in 0.50 s | 536 MiB | 10 x 200 in 0.55 s | 283 MiB |

What the figures say:

- Before, nothing bounded the password checks on purpose. What held them near 782 MiB and not
  higher was the database pool: a check ran with a connection checked out, and the pool has 10
  (145 MiB + 10 x 64 MiB = 785 MiB). The api container's limit is 512 MiB.
- After, the peak is the rest level plus two checks (141 MiB + 2 x 64 MiB = 269 MiB; 276 to
  283 MiB seen), whatever the number of callers. Every 503 carried `Retry-After: 2`.
- More than 18 of the 40 were served (25) because slots came free while requests were still
  arriving: the bound is on checks running and callers waiting at one moment, not on a total.
- Ten real sign ins at once all succeeded, the slowest in 0.55 s: five rounds of two.
- The 283 MiB in the last row of the after column is the level the floods left behind, not a
  peak of that scenario: this process did not hand the two buffers back to the system within
  the run (2 seconds later it still read 283 MiB; the before run read 126 MiB at that point).
  Not investigated further. It is the level of two checks, the bound itself.
- The two medians for one sign in (144 ms and 85 ms) are the same code path on the same
  machine in two runs: the difference is noise of this method, not an effect of the change.

Not measured here: the Jetson (the wait bound of 3 seconds assumes a check of at most 375 ms
there with two running), the container's own accounting (`docker stats`, cgroup peak), and the
path through Caddy. The per user bound on request bodies in flight was not measured for memory;
it is tested for behaviour over a real socket in `api/tests/identity/test_body_limit_api.py`.

To run it again (from the repository root, with a Postgres at the head migration and a Redis):

    uv run --native-tls --directory api python ../tasks/evidence/identity-108/measure.py \
        --api-dir "$PWD/api" --database-url postgresql+psycopg://USER:PASSWORD@127.0.0.1:PORT/DB \
        --redis-url redis://127.0.0.1:PORT/0 --out ../tasks/evidence/identity-108/after.json
