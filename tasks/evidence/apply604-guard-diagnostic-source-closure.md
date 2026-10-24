# Issue 605 guard diagnostics and blank lifecycle source closure

Status: SOURCE CLOSED for the reviewed diagnostic and blank-lifecycle source slice. All three private harness findings are independently closed. Native execution remains held.

Reviewed candidate: `9358e4eb7f1f55d8e716a8ed81746193cd9d2ffe`.
Reviewed tree: `2b5e8ed0995a025c3ab20e669aea11e5146d88a6`.
Comparison base: `11d1c82084f34b36932277c992fcf85434c62253`.
Blank author: `53b385b2ffac02469dfcdfdd7afb5dd404640c15`.
Diagnostic author: `5b2c85689df4643cd50c714a7cdc57334857b42e`.

This report retains the original independent review of the two browser production files and
two new test files, completed before an interruption, and adds a fresh independent recovery
recheck of the three private harness findings and their exact correction delta. It supplies no public PR verdict, native result, quiet gate approval or capacity
acceptance. The additional three documentation/evidence changes since the base were read for
context. No earlier closed Apply audit was reopened.

## Retained source findings

The following source findings and application test results come from the original reviewer.
The recovery reviewer verified the raw artifacts and exact source identities without rerunning
the 170 previously passing application tests or reopening earlier closed Apply audits.

No required application-source finding was found. Every changed production and test line was
reviewed. `working_set` and `past_guard` have identical ASTs to the exact base. Removing only
the observation capture and diagnostic warning from the new guard yields the original guard
AST, including threshold arithmetic, newest victim, replacement creation before closure,
exact event, and return behavior. Configuration, Compose limits, policy, normal entrypoint,
native browser test, original memory tests and original UX/config files are byte-identical.

The numeric observation comes from the same current/max/stat reads, preserving original v2/v1
read order, tuple-or-None API and fallback behavior. Active file remains counted. Missing,
malformed and unreadable stat conservatively count usage. The original parser skips invalid
matching occurrences and uses the first valid decimal occurrence; literal-space partitioning
does not recognize a tab-only delimiter. Independent differential checks covered those cases,
duplicates, mixed whitespace, Unicode decimal digits and both cgroup versions.

The synchronous ContextVar scope resets in a finally before any guard await; the captured
sample is local across replacement and victim closure. Independent normal, nested and
exception reader probes verify restoration. The concurrency regression retains sample 100
while another task observes sample 200. Logging/parser/observer faults preserve the original
close and exact event. Default INFO still exposes the closure warning; custom readers do not
inherit prior samples.

Diagnostics export fixed labels, numbers and nulls, with 17 allowlisted counters, at most
20 digits per exported counter and a 16 KiB stat inspection prefix. No raw paths, URLs,
profile text, environment, exception text, unknown keys or canary values enter them. The
interval bounds the original sequential reads and parsing; it is not an atomic kernel
snapshot. The observer adds bounded parsing and synchronous sink cost, with no deciding file
reread, wait, retry or timer. DEBUG lifecycle samples are separately labeled, with no extra
lifecycle reads at INFO. The first-navigation boundary may describe an internal blank.

Both tab cleanup paths now retain only the existing exact `about:blank` allowance. Actual
guard, listener and clean-exit watcher regressions cover both listener/return schedules;
settle-only controls also execute. Forbidden popups, lookalike blank URLs and unsafe schemes
still close. Agent URL policy remains unchanged and refuses about:blank. The replacement
does not restore the closed form or establish memory headroom. The four new E402 annotations
are the accepted narrow repository import-bootstrap convention, with no assertion, coverage
or behavioral waiver and no new production formatting suppression.

## Retained independent application evidence

Private raw evidence and runnable probes: `/tmp/codex-605-guard-source-review/`.
All pytest commands used `caffeinate -i bash ops/agent-test.sh`, the owned name
`codex-605-guard-source`, `-m 'not docker' -n 2`, and separate JUnit/log files. The existing
diagnostic builder interpreter was borrowed read-only with UV sync/network disabled and
bytecode writes disabled; no dependencies were installed.

1. `green.log` / `green.xml`: 107 passed, zero skipped, exit 0. Both new files and existing
   memory/guard cases were selected with `-k 'memory or guard or blank or diagnostics'`.
2. `navigation.log` / `navigation.xml`: 63 passed, zero skipped, exit 0. Existing policy,
   off-list read/action, committing redirect and redirect-hop controls passed.
3. `differential-probes.json` / `contract.log`: 38 exact-base/current synthetic cases agree on
   return values and read sequence, with correspondence and numeric privacy assertions.
4. `mutant-attribution.patch` / `.log` / `.xml`: an actual shared last-sample mutant preserved
   log presence but failed at `sample_id=200` versus the deciding 100, 1 failed, exit 1.
5. `mutant-blank.patch` / `.log` / `.xml`: replacing actions.py with exact base source removed
   both fixes and yielded 4 failed / 52 passed, exit 1. Both guard schedules and both exact
   blank cleanup controls failed.
6. `mutations.jsonl` records exact commands and byte restoration. Chromium SHA-256 restored
   to `c8bbde9e1b932852490a77d54e7295db3f9b94fe8d444ac44ef7ead72b1ab386`; actions SHA-256 restored
   to `772f9c350893aa21def35025996141dca104583eac188192f62071e3bad952a6`.
7. `source-preservation.json`: guard AST, ContextVar restoration and unchanged contract files
   verified. Ruff check and format check passed for all four changed source/test files;
   `git diff --check` passed. No coverage output or accepted evidence was overwritten.

Author raw RED/GREEN and module/hook evidence was inspected. Diagnostic original-source RED
failed after its unchanged event/read assertions; lifecycle RED had 2 failures. Its final
31 focused cases passed; its 2155-case non-native module run preceded only four added test
rows, which are covered independently here. Blank RED had 4 failures / 52 passes, GREEN had
56 passes, and its whole module had 2184 passes with no selected skips. Those counts describe
each author's candidate, not a new combined whole-module run.

## Recovery recheck and exact identities

The recovery reviewer used an isolated worktree at the reviewed candidate above. Root's clean
integration `96033dbf8750a47aa436f598df97ddd0ff1e33d6`, tree
`3cfe8f3be2b285e2727056f58029cfdf266b02ac`, differs only in the completion document and two
author evidence reports. All 15 checked source, contract and helper files match the immutable
candidate git objects, the retained review worktree and the integration worktree byte for byte.

| Reviewed file | SHA-256 |
| --- | --- |
| `browser/browserd/chromium.py` | `c8bbde9e1b932852490a77d54e7295db3f9b94fe8d444ac44ef7ead72b1ab386` |
| `browser/browserd/tools/actions.py` | `772f9c350893aa21def35025996141dca104583eac188192f62071e3bad952a6` |
| `api/tests/browser/test_guard_blank_replacement.py` | `d51367b825d1169d66a824069125b77696b19071bea85285159c47dd1e3dd916` |
| `api/tests/browser/test_memory_diagnostics.py` | `bab3da79eea4c7023e2abd0793d8a29ff3afdb0654aa0a51c6d88263810e8169` |

The fresh preparation owner confirmed that the controller and sampler already contained the
three corrections. Its recovery changed only stale plan metadata and added separate private
evidence. The independent recheck was frozen against these exact final artifacts:

| Private artifact | SHA-256 |
| --- | --- |
| `/tmp/codex-605-diagnostic-native-run.py` | `3e328a05fb0b97db2811113cbeb1b14ea8c170bc8d109d2982e41154058d79cd` |
| `/tmp/codex-605-diagnostic-native-evidence/numeric-sampler.py` | `ddde8aebbe15b9418fa8ae75211af0a60eb47b6f4d1856e535a4a0c336746d7f` |
| `/tmp/codex-605-diagnostic-native-evidence/measurement-plan.json` | `774a96bfea48db0074508ab6e65a3396be9ecb3eaf5e8a315a3e394d0773b0a1` |

The embedded and external samplers are byte-identical. Original archived controller, sampler
and plan hashes were verified as `ad71cde7922edd26eb5cf30f34fefaee98900eb3f0d04046cea3db15bcaa7d65`,
`89eadfe16bd2c7e193b8208e633d517b12916df077b03e0bc6e25ca815d006a7`, and
`e3a0e7931e6a5a2054341701c0fec3290015f1681478fc6700178e3e07c97945` respectively. The original
RED evidence and all 62 received artifacts other than the intentionally refreshed plan remain
unchanged. The preparation owner's separate replay also reproduced its earlier 4 RSS, 7 output
and 7 cleanup GREEN cases exactly.

## Three private harness findings closed

The original actual-function synthetic evidence reproduced RSS zero when SELF was excluded
from 64 PID rows, a TypeError from unavailable worker state that skipped cleanup, and an output
write of 33554479 bytes against the 33554432-byte bound. Those failures remain retained in
`harness-correction-probes/runtime-contract-failures.json` under the private evidence root.

1. Own observer RSS now comes from a separate bounded `/proc/self/status` read, before PID
   scanning. Missing, invalid, truncated and denied self status stops conservatively. Nine
   independent cases executed the actual complete sampler loop and its terminal finally,
   including 32767, 32768 and 32769 KiB and a 64-row scan excluding SELF. Unavailable or
   over-budget cases exited before PID or expensive sampling and retained terminal status.
2. Observation, worker inspection and log-write failures are isolated by `capture`. Unavailable
   worker state receives a safe label. Ten independent controller `file_run` cases exercised
   worker-state/log failures, unavailable error logs, observation failures, stdin faults,
   child poll/terminate/wait faults with kill fallback, failed stack-down/inventory/manifest,
   unpause failure, combined faults and migration failure. Each preserved the exact original
   workflow exception object, attempted stack down and all three resource inventories, and
   stopped every synthetic child. Error records contain exception types, not the planted
   exception-message canary. These prove cleanup attempts and synthetic failure isolation,
   not removal of live runtime resources.
3. `emit` checks encoded bytes before each write. Ordinary records leave a 4096-byte terminal
   reserve, and rejected records stop sampling without writing partial or excess data. Nine
   independent cases checked just below, at and above the exact encoded boundary for ordinary
   and terminal records, including a Unicode payload. The actual terminal finally with all
   allowlisted KiB fields at a uint64 boundary emitted 965 bytes, within the reserve. Every
   actual emitted byte plus the seeded prior byte count remained within 33554432 bytes.

The complete correction delta was reviewed. All controller functions except `file_run`,
`owned_runtime_inventory` and `cleanup` have unchanged ASTs; only `capture` and `finish_child`
were added. Nonfunction controller ASTs are unchanged except for the embedded sampler. The
workflow before `file_run`'s finally is AST-identical. The sampler delta adds independent own
status and byte-before-write enforcement with terminal reserve; the rest of its numeric,
privacy and sampling policy is unchanged. The plan's whole-file, privacy, interpretation,
grant schema and pre-existing sampler budgets are unchanged. No application source, assertion,
timeout, cap, decision formula or containment policy changed in this correction.

New independent raw evidence and runnable probes are under
`/tmp/codex-605-harness-recovery-recheck/`: `recheck.py`, `contract.py`,
`independent-recheck.json`, `contract-and-identities.json`, and `command-receipt.json`.
The actual successful commands were `rtk proxy python3 /tmp/codex-605-harness-recovery-recheck.py`
and `rtk proxy python3 /tmp/codex-605-harness-recovery-contract.py`, both exit 0. An initial
independent probe attempt exited 1 because its synthetic stub injected an observation fault
before the intended workflow failure; only that stub was corrected. The receipt retains this
attempt, command and reason. No controller or application edit resulted.

## Runtime contract and continuing holds

One original all-five Review UX file must run in one uninterrupted fresh stack, worker 1,
retries 0, one attempt. Original 600000/150000/180000 ms budgets, assertions, 1 GiB memory and
memswap caps, and the 90 percent deciding formula remain. All four real layout quartets,
16 screenshots, remain required if emitted; strict console, zero submission, cookie and
restart checks remain unchanged. This recheck supplies no native result.

The diagnostic CMD sets only `logging.getLogger('browserd').setLevel(logging.DEBUG)` and
launches `runpy.run_module('browserd', run_name='__main__')`. The existing tini entrypoint is
preserved. The final gate must use the normal CMD. Fixed binary hashes and package/version
reads remain deferred until after the workflow, with no deliberate prewarm, cache eviction
or reset. Process/PSS/map-class sampling remains finite: 64 PIDs, one-hour deadline, 100 PSS
attempts including observer start/end, 20 mapping attempts, 16 MiB mapping reads, 1 CPU second
for expensive sampling and 5 CPU seconds total. The collector exports no raw process names,
command lines, environment values or mapping paths and requests no added privileges. Denials,
truncation, footprint and attribution limits must be reported.

A PASS without discriminating startup evidence leaves issue 605 unresolved. This diagnostic
and the retained blank fix do not establish memory-pressure remediation. The original first-fill
1006 MiB failure remains failed; lost form restoration, physical capacity and owner real-site
acceptance remain unproved. Gate 4 and the final whole-PR verdict remain separate and held.

## Cleanup and execution boundary

The original review's four non-native helper commands finished, with an empty exact owned
helper-label inventory retained in its evidence. Its two mutations were restored byte for byte.
The recovery recheck created only private synthetic evidence and this report in its own review
worktree; it created no live child, socket, container, dependency environment or grant. All
new probes are bounded AST execution with synthetic OS/process/socket/tool boundaries, with
no application imports or test collection. No Docker command, native run, image build,
dependency install, cache operation, real account, job host or mail action occurred during
recovery. No source file was mutated, and no watcher or polling job remains.

Both native grant files, real build-complete marker and real UX runtime outputs were absent
at the final check. The validator remains at its clean preparation base; root must bind the
reviewed candidate and freeze the exact input manifest before any separately granted runtime.
SOURCE CLOSED closes this diagnostic/lifecycle source slice and its prepared harness contract;
it does not authorize native execution. Only this report is committed locally, with normal
hooks, no push and no public PR verdict.
