# Apply 604, Gate 3 scoped source closure

Status: **CLOSED for the scoped source review**. No blocking finding in the reviewed delta.
This is a local source checkpoint, not whole-PR approval, native acceptance, coverage closure,
or permission to merge or deploy.

Reviewed candidate: `67813bac632794cc6c1df3df8dced6d51b750bf0`.
Reviewed tree: `e8db21382650abccb7abe10310be5865dd0e7e28`.
Source baseline: `0b6685190fcb33d52bd8aa2366c709add2b775bd`.
The review used a fresh isolated worktree and the exact candidate, before this report commit.

## Scope and findings

The delta contains four A18 UI/test files, 17 Apply fixture specs and one new document helper,
two durable-UID fixture corrections, one G10 fixture barrier, and three evidence/ledger files.
Production backend source is unchanged. Previously closed backend guard, ownership, provenance,
file, fallback, progress and Saved answers reviews remain closed. The owner-confirmed distinction
between local application edits and explicitly created reusable Saved answers remains intact.

No required or critical source finding remains in these families:

* A18 Replace intent is React-owned at the native event boundary. Other fields, approval and
  Skip stay blocked through the awaited response adoption. The acknowledged target field can
  release the operation despite an unrelated section hash change; a conflicting target requires
  a strictly newer authoritative timestamp. The parser preserves Python's six-digit fractional
  precision, compares timezone offsets as instants, and refuses malformed/unsupported times.
  Operation identity guards success/error cleanup. Rejected edits retain inline errors and
  refresh behavior; unrelated dirty drafts, stale confirmations and run-key resets are preserved.
* A18 live-view production requires a successful post-mount browser observation and the current
  own automation/profile-edit lease in active or waiting-person state. The route run id resets
  observer freshness, and the lease id controls view replacement without remounting on stable
  polls. Cached active/sign-in leases, absent/starting/foreign/unrelated leases, errors, removal
  and late ticket completion are covered by the new real-producer component tests. Shared
  LiveBrowser, handoff, ticket permissions and strict native console expectations are unchanged.
* Apply fixture readiness uses one original 150000 ms shared window, exactly two latest
  versions numbered at least one, idle render state, no render errors and both PDFs. An idle
  terminal error exits the poll and exposes its actual code/message even if the other document
  remains queued. Original API statuses, optimistic versions, CSRF, URLs, negative seeds,
  cases, assertions and budgets are preserved. The editor row keeps the exact Apply region and
  exact label, accepts its Edit/Stop editing states, and still clicks the exact Edit button.
* The two later fresh profiles use unused UIDs 20027 and 20001. Permanent UID ownership guards
  and all original behavioral assertions remain unchanged.
* G10's barrier requires an explicit project, selects exactly one worker by that project's
  Compose labels, verifies running/unpaused state, and resumes the selected worker in `finally`
  after success or action failure. Selection/initial-state failures do not pause a worker.
  The direct argument-array Docker calls avoid Compose environment-file loading. Original
  real-completion polling resumes after unpause, without an alternate status allowance,
  retry, sleep, skip, timeout extension or product change.

## Independent checks

* Composed `pnpm --dir web run typecheck`: PASS. Package and lockfile SHA-256 match the
  integration dependency environment; installed packages were reused read-only with owned
  executable wrappers and mutable output/cache directories. Log: `/tmp/apply604-source-typecheck.log`.
* Actual-source function probes: PASS. A deterministic BigInt oracle checks 1800 timestamp
  comparisons across offsets, fractional widths, equality, and adjacent microseconds, plus 12
  malformed-neighbour refusals. Nine worker scenarios cover missing project, zero/multiple
  workers, stopped/already-paused worker, pause failure, success, action rejection and unpause
  failure. No Docker command is executed by these probes. Ten document observations cover
  cardinality, missing/zero latest version, missing PDF, queued/rendering state, successful
  release, and terminal-error reporting with another document queued. An in-memory removal of
  the version guard produces premature release and is detected. App source was not mutated.
  Script/log: `/tmp/apply604-source-review-probes.cjs` and `.log`.
* Independent syntax-preservation audit: PASS. After normalizing only the 18 readiness calls
  and the reviewed editor helper, every other syntax node in all 17 fixture specs matches
  baseline. Removing the G10 barrier reconstructs the original file byte for byte: 567 lines,
  eight cases and 98 `expect` calls. A18's native spec, the base Apply helper and the canonical
  documents helper are byte-identical to baseline. Script/log:
  `/tmp/apply604-source-review-preservation.cjs` and `.log`.
* Baseline-to-candidate `git diff --check`: PASS.

Author evidence was inspected separately from the independent checks: A18's actual old-source
run fails 40 of 66 focused regressions; the same final focused selection passes 66 of 66,
with the full Apply/Browser suite passing 527. Fixture probes record four intended old-source
failures and eight final passes. UID evidence records the two actual collisions, the same
four ordered corrected cases passing, and both full files plus ownership units passing 52.
These historical results do not substitute for the pending composed native acceptance.

The composed typecheck was the only reserved heavy check. Heavy slot 2 was released immediately
after it completed. This reviewer ran no native journey, Docker command, image build, external
account flow, public review, push, merge or deployment.

## Required remaining gates and limits

The root validator must run all four A18, six A16, five Review UX and eight G10 cases on fresh
whole-file composed stacks, with zero retries, original budgets, strict error watchers and
unchanged browser/resource caps. The UID correction still belongs in the final complete Docker
suite. Coverage closure remains separately owned, with the existing 95 percent requirement
unchanged. A fresh complete quiet exact-tree Gate 4 and final whole-PR independent/public review
remain required before whole-PR acceptance.

Readiness fixes the demonstrated startup, cached-snapshot and route-transition producer cases;
it cannot establish that no later lease-end/ticket-request race is possible. Finished-document
readiness orders setup after the relevant render commit; unrelated future writers can still
cause a legitimate optimistic-version refusal. Malformed timestamps cannot establish
supersession and keep the operation pending until its acknowledged field is observed. These
limits do not weaken the outstanding native or final-gate obligations. Long-lived browser
memory issue 605 and real-site owner checkpoints remain separate from this synthetic source
closure.
