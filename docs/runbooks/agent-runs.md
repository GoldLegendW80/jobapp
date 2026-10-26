# Agent runs

Claude agents in the worker: image, credential and how a run executes.

Claude agents run in the `worker` (SPEC-agents.md, agents A2), on the user's own model credential
(Account, Model access). The worker image `jobapp-worker` is the `worker` target of
`api/Dockerfile`: the api's code plus the Claude Agent SDK, whose wheel bundles the Claude Code CLI
it drives (no Node runtime); the api image (`runtime` target) stays without it. At most two runs at
once (a third waits in the queue); each run gets a scratch directory on the worker's `/tmp`
tmpfs, deleted when it ends, its credential only in the environment of its own Claude Code
process, no built-in tool (no shell, files or web), and only the app tools of its agent, bound to
its user. A run stops at its turn limit, after its wall time (10 minutes by default) or when the
user cancels it; a worker restart fails the runs it was running.

```bash
curl -s -b cookies http://localhost/api/agents                                                    # agents available here
curl -s -X POST -H 'X-Requested-With: jobapp' -H 'Content-Type: application/json' -b cookies \
  -d '{"agent":"echo","input":{"message":"hi"}}' http://localhost/api/agents/runs                 # 202, queued
curl -s -b cookies http://localhost/api/agents/runs/<id>                                          # status, events, tokens, cost
curl -s -N -b cookies -H 'Last-Event-ID: 12' http://localhost/api/agents/runs/<id>/stream         # live steps after step 12
curl -s -X POST -H 'X-Requested-With: jobapp' -b cookies http://localhost/api/agents/runs/<id>/cancel
```

The `/runs` page lists the runs; `/runs/<id>` is the live run panel (steps, tool calls,
tokens, cost, Cancel), which resumes its stream after a reload or a network loss without losing
or repeating a step (`app/sse.py` `replay`, keyed by the step's seq). The answer appears while
it is written: the worker publishes the text of the block being written to Redis
(`app/agents/live.py`, nothing per token in Postgres) and the stream relays it as `partial`
events until the stored `text` step replaces it; `AGENTS_LIVE_TEXT=false` in `.env` (api and
worker) turns that off, and the answer then arrives when it is complete; an agent definition can
turn it off for its own runs (`live_text=False`). `echo` is a test agent,
refused when `APP_ENV=production`; outside production `/runs` offers a test run with it. `AGENTS_FAKE_BACKEND=true` (test
stacks and CI only, refused in production) replaces the model with recorded message streams
(`api/app/agents/fake_streams/`); `tests/compose.a2-e2e.yml` with `AGENTS_FAKE_BACKEND=false` runs
the real SDK and CLI against the Anthropic stub (`tests/anthropic_stub.py`), still with no model
call.
