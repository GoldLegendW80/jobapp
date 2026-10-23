# Memory guard diagnostic builder handback

Local commit: `5b2c85689df4643cd50c714a7cdc57334857b42e`.
Tree: `c9c29f3ad3289c9ddd134ba71591d1ac6b29723e`.
Parent/base: `11d1c82084f34b36932277c992fcf85434c62253`.
Branch: `browser/605-guard-diagnostics`.
Worktree: `/Users/h.hautin/Documents/Discoveries/AI/jobapp/.claude/worktrees/codex-605-guard-diagnostics`.
Clean after normal hooked commit. No push or public mutation. The two owned files are `browser/browserd/chromium.py` and new `api/tests/browser/test_memory_diagnostics.py`: 574 insertions,16 deletions total (418 new test lines; source156 added,16 replaced). No helper module, dependency, config, public schema, existing test, native fixture or containment change.

## What is measured

A separate JSON log message with `event="browser_memory"` records the original memory() sampling interval (`sample_id=read_started_ns`, `read_finished_ns`, both `time.monotonic_ns()`), numeric current/limit/working-set, cgroup version, bounded accounting/stat/fallback statuses and allowlisted numeric memory.stat fields. Deciding current/max/stat files are not re-read. v1 uses its original hierarchical `total_inactive_file` key. `working_set`, `past_guard`, cadence, cap/pct, victim selection, blank replacement, public timing budgets and the exact `tab_closed` event remain unchanged.

The private ContextVar is scoped only to synchronous reader execution and reset before any guard await. A local captured record survives replacement/close/listener work. Interleaving proof runs a second memory() sample in another task during awaited victim close, then verifies the original sample_id/raw values and exact event in the closure warning. Default/custom reader compatibility is preserved, and custom readers without a real cgroup sample do not inherit old attribution.

Routine samples are DEBUG; an actual closure emits the entire same numeric sample at WARNING with phase `guard_close` and unchanged pct. Optional lifecycle boundaries are `startup` before prepare/spawn, `cdp_ready` after browser CDP attachment, `first_navigation` for the first main-frame framenavigated callback after listener attachment, and `end` before cleanup. Lifecycle samples are separately labeled and add no reads when DEBUG is disabled. The first-navigation label carries no URL, and may represent about:blank; it does not identify the form or fix the separate blank-listener interaction.

No ancillary peak/events/pressure or process/mapping/PSS reads are added. A later validator owns those observations. The logged interval bounds sequential reads, not an atomic kernel snapshot, and includes ordinary parsing before observer work. Stat diagnostics inspect at most16KiB, retain at most17 allowlisted counters, each at most20 numeric digits, and label truncation; guard parsing still uses the original full body. Missing/invalid/unreadable stat preserves conservative usage. Active file is retained. Original v2 unlimited behavior and original numeric v1 sentinel behavior are unchanged.

## Privacy and observer limits

Only numeric counters, nulls and bounded internal status/phase labels are exported. No root path, URL, profile/user/lease id, environment, raw stat text, unknown counter, raw exception or document content enters a diagnostic. Canary tests cover raw stat text, unknown names/invalid values, observer exceptions and output capture. Parsing/serialization/logger exceptions cannot replace memory()'s return or guard event/close action. Recoverable counter/parser or DEBUG logger faults set observer_failed=1 on a later available warning. If every sink fails, or the complete observer is deliberately replaced with a raising function, no reliable self-report can be emitted; tests prove the original guard still closes and emits its exact event.

Guard observer overhead is bounded in work/record size, with no added file reads, await, sleep, retry or dwell. Existing synchronous logging still has its sink cost; no native latency/footprint claim is made. Optional DEBUG lifecycle observations add one ordinary sample (three accounting reads on v2) per named boundary. Later native measurement must include that observer footprint.

## Validation and retained failures

`commands.jsonl` retains exact executed test commands, exit codes and result/log mapping. `patch.diff` is the exact committed two-file diff. Initial sandbox Docker denial exit4 is separate from test failure. Authorized RED: original source1 failed at absence of deciding diagnostic after exact event and one-time reads passed. First implementation1 failed at JSON event field ordering; corrected stable event-first framing without weakening assertions. Expanded core24 passed. Lifecycle RED2 failed/25 passed; after instrumentation27 passed. Whole browser non-native2155 passed in27.91s. Final focused file31 passed in2.50s after adding four whitespace/Unicode rows and mechanical formatter cleanup. The complete module run preceded those four additional new rows; all original module tests and all31 final new cases have passed. `-m 'not docker' -n2` excluded native/docker tests; xdist reports selected item counts but no deselected total. No global coverage override was used and no coverage claim is made.

Full API Ruff and format passed; final owned source/tests Ruff and format passed; CI-plan81 passed; E2E registry29 CI/4 reason-excluded/53 local-only; explicit pre-commit and normal commit hooks passed, including secret scan. Initial Ruff formatting failures (12 E501, then1 E501) were resolved without suppressions; raw initial lint outputs remain in the tool transcript. Both newly introduced fmt:skip directives were removed at root's request. Final staged diff check passed. Own Docker helper filter is empty after every completed run and after the final focused run.

`parsing-correspondence.log` compares AST-extracted working_set from the exact base to current working_set and the diagnostic counter parser. The existing formula skips malformed inactive-file occurrences and selects the first valid duplicate; the new table already covers malformed,then100,then200 yielding900. Tab-only key/value delimiters are unrecognized by both literal-space partition parsers; space+tab, repeated spaces and Unicode decimal values agree. No original parser change was needed.

## Enabling the later diagnostic experiment

Existing `browser/browserd/__main__.py` has INFO basicConfig, without a DEBUG env knob. An experiment-only Compose command override can be:

```json
["python", "-c", "import logging, runpy; logging.getLogger('browserd').setLevel(logging.DEBUG); runpy.run_module('browserd', run_name='__main__')"]
```

Keep the image ENTRYPOINT `["/usr/bin/tini", "--"]` intact: effective process is `/usr/bin/tini -- python -c ...`. This executes the same immutable browserd module and changes only its existing logger's verbosity. It adds no application config/guard wrapper/source override. `verify_logging.py` synthetically executes the actual AST-extracted basicConfig statement, real memory()/guard code, synthetic numeric files, and no browser. `logging-smoke.log` proves INFO emits guard_close WARNING; the per-logger wrapper also emits routine guard DEBUG. Final acceptance/gate uses normal ENTRYPOINT/CMD.

Fresh independent source review of this commit and separate review of the validator/controller/logger wrapper are required before any native diagnostic experiment. No browser/native/Playwright runtime, image build, cache operation, memory policy remedy, limits change or public verdict occurred here. Issue605 remains a measurement question; this slice does not accept the failed UX run.
