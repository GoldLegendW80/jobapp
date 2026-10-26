# Apply composed test contracts

Refs #563 and draft PR #604. Base: `73ee000f99468a48f31cac7fcca03e306e9841d4`.

The first composed gate completed its API lane with 26,521 passing tests, 57 existing
opt-in, platform or personal-source skips, and three failures in 2,180.95 seconds.
Those failures were stale ingestion integration expectations about already registered
Apply agents and the optional Gmail MCP service. This change updates two test files only;
it changes no application code, Compose configuration, limit, target or deadline.

## Contracts retained

- Ingestion extraction and transcription retain 900 seconds each. Generic Apply retains
  900 seconds of active work, a bounded 1,800 second sign-in pause and a bounded 900 second
  live takeover pause. Its registered transport allowance is 3,600 seconds. The shared
  queue allows two concurrent jobs, gives `run_agent` one try and a 3,720 second timeout,
  and reaps stale running jobs after 3,900 seconds. A synthetic longer registration must
  still reject an already built queue, update the next queue and stale threshold, and
  leave both ingestion agents at 900 seconds. Expected baseline values are literals,
  rather than another copy of the production maximum calculation.
- Parser remains on internal `parsing`, shared only with worker. Renderer remains on
  internal `rendering`, shared only with worker. API and worker reach Gmail MCP over
  internal `mailmcp`; only `gmailmcp` joins `gmail_egress`. Its exact network list excludes
  `default`, parser, renderer and browser networks. API and worker have no direct
  `gmail_egress` membership. Exact service lists and network membership sets remain
  assertions, and all services reject `network_mode` overrides.
- The nine original core limits remain an exact dictionary totaling 5,248 MiB, including
  the existing B2 allowance of 128 MiB above the unchanged 5,120 MiB target. Gmail MCP is
  separately asserted at 128 MiB behind `profiles: [mail]`. The declared total is
  5,376 MiB, or 256 MiB above target. This accounting matches
  `tests/documents/test_compose_renderer.py`; it does not establish physical Jetson
  capacity. Physical capacity acceptance remains an owner step after checked deployment.

## Validation

Runtime tests are deliberately deferred while the first full gate owns the machine.
The failing gate is the initial reproduction. Static validation covers Python syntax,
Ruff checks and formatting, the CI plan, journey registration and normal commit hooks.
The handback records their actual results; a fresh independent review and the next
composed gate must verify this test-only change.

Focused runtime command after the gate releases the machine, from this worktree:

```sh
rtk proxy caffeinate -i bash ops/agent-test.sh --name apply604-contracts tests/ingestion/test_engine_worker.py tests/ingestion/parser/test_compose_parser.py tests/documents/test_compose_renderer.py -m 'not docker' -n 2
```

The orchestrator then runs its full gate on the final composed head. No pytest, database,
Docker, browser, build or full gate command was started by this fix agent before release.

Self review: checklist items 3, 5 and 9 apply. Assertions preserve the active ceilings,
exact network isolation and unchanged resource accounting. No test is skipped or deleted,
and no suppression or exception is introduced. Files outside the assigned tests: this
evidence note only.
