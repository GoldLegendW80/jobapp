# Apply 604 failure-path test and recovery closure

Status: CLOSED for this independent scoped review. Required or Critical findings: none.
Optional findings: none. This is a local source and module closure, not a whole-PR verdict,
native acceptance, final gate result, or permission to publish, merge or deploy.

Reviewed source candidate: `593d2b919376f0472c37ba79a854707f7e40edfc`.
Source tree: `0aded10fc88777c25f16b91a3fd955183f940859`.
The report is the only tracked change made by this reviewer. The review used its own
`review/apply604-recovery-coverage` branch and isolated worktree.

## Source findings

The scope comprises ten new Apply test files, 2,554 lines, the ten-line production diff in
`api/app/apply/run.py`, and the later 83-byte canonical model-registry import in the Review UX
seed. Earlier closed UI, backend guard and source reviews were not repeated without a new
finding. No production change beyond the passive 403 correction was present relative to the
historical backend. Coverage configuration, shared `api/tests/conftest.py`, exclusions, skips,
assertions in existing protected tests, timeouts and caps were not weakened.

The tests exercise observable boundaries rather than assertion-free parameter padding:

| Cohort | Behavior reviewed |
| --- | --- |
| Profile fill and Save revalidation | Durable failures and person handoffs, no premature typing or Save, ownership, review preservation, concurrent site edits, expiring authority and final external-write checks. |
| Fill, submit, manual and persisted gate outcomes | Lease loss, cancellation during acquire, document failures, no replay after submission, stale approval refusal, command limits, recovery of surviving mail audit/draft records and unfrozen documents before a permitted act. |
| Adapter readback | Realistic refused tools, changing refs, disappeared fields, pending steps, unreadable pages, unmatched readback, empty combobox options and verified clearing. |
| Passive 403 recovery | Usable Ashby and deterministic Workday forms, empty comboboxes, pending/cut/malformed/unavailable pages, adapter posting/scope and frame/anchor guards, cancellation, raced resume, browser loss, original deadlines, lease release and zero submits. |
| Route and answer boundaries | Real TestClient/Postgres ownership and typed refusals, durable approvals through queue outages, application-local answers without changing either person's saved bank, exact personal sources, widget refusals, step authority and cancelled model-run cleanup. |

Fakes remain local to their test files or reuse existing hostile fixtures. Browser refusals use
the real result shapes and codes. Persisted outcomes are re-read after commits; cancellation and
resume probes use separate database sessions. The new tests assert absence of unintended input,
submission, document freezing, model calls or cross-person effects where relevant. A source scan
of all ten files found no skip/xfail, coverage pragma, type/lint suppression or sleep addition.

The 403 correction reads the already captured passive snapshot through `adapter.read`, retaining
adapter-specific posting and scope checks. Missing reads, read errors, pending steps, cut pages
and anchor problems keep the original wait. Active discovery follows browser resume and a
successful locked database resume. Existing fill/attempt/deadline and submit authority stay in
their existing paths. Deterministic shape recording remains in active discovery.

The genuine original traceback reaches interactive combobox discovery on a snapshot-only
`Passive` object and raises `AttributeError` outside `_guarded`. Retained author evidence contains
three actual old-source RED cases. The intermediate 33 PASS / 1 fixture-contract failure is
also preserved: a valid form correctly resumed, so the inherited still-forbidden fixture was
corrected to show an actual Access denied page while retaining its original outcome assertions.
The unrelated 429 case now checks the existing due-time contract at 599 and 600 seconds instead
of a nonexistent property. Author evidence also preserves the helper mail setup errors and API
URL-encoding, sanitized-error and designation-fixture assumptions before their passing runs.

For UX registration, removing only
`from app.models import Base  # register foreign-key targets before fixture commits`
restores the entire previous spec byte for byte, including all five cases and their assertions
and budgets. Original spec SHA256:
`f696269d85c9dff9a5ee12309cbb6d7660e1999e8f78f42ef6ec35a4b69e2063`.
The import uses the existing canonical registry. The author's isolated SQLAlchemy proof uses
fresh processes, real model imports and dependency sorting: old context raises the real missing
`jobs` foreign-key error in three variants; the corrected context resolves all 52 tables. This
is metadata evidence, not native acceptance.

## Independent runtime evidence

Own evidence directory: `/tmp/jobapp-apply604-recovery-review-20261023/`.

| Check | Actual result |
| --- | --- |
| Independent mutation | Invert `found.cut` to `not found.cut` in the passive refusal guard of the reviewer's own copy. The new cut/Ashby case fails on persisted `needs`: `snapshot_cut` instead of the original `forbidden`. One failure in 18.18 seconds, command exit 1. |
| Restore and recovery regressions | Restore production bytes exactly, then all 34 new recovery cases PASS in 11.52 seconds, command exit 0, on `bcd2ced12f74d55d922d8e0b1f8f9d90a906e863`. The recovery file and production inputs are identical in the final candidate. |
| Whole composed Apply folder, once | All 8,573 tests PASS in 457.20 seconds on exact `593d2b91`, no failures or skips. Command elapsed 476.86 seconds. Command exit 1 solely because sparse Apply-only coverage is 49.77%, below the unchanged global 95 floor. |
| Exact final source static checks | Full `ruff check app tests` PASS; `ruff format --check app tests` PASS, 795 files; `git diff --check` PASS. |
| Required fast repository checks | CI plan 81 tests PASS and E2E spec registry PASS with the completed report present. Raw logs and commands are retained in the own evidence directory. |

The single composed module command was:

```sh
rtk proxy env COVERAGE_FILE=/tmp/jobapp-apply604-recovery-review-20261023/.coverage-whole-apply caffeinate -i ./ops/agent-test.sh --name apply604-review-whole tests/apply -n 2 -q --cov=app --cov-branch --cov-report=json:/tmp/jobapp-apply604-recovery-review-20261023/whole-apply-coverage.json --cov-report=term-missing --durations=10
```

There are no Docker-marked Apply cases, so the whole folder ran without a marker filter or
deselection. `whole-apply-command.json`, `whole-apply.log`, `whole-apply.exit` and the fresh
SQLite/JSON retain the exact source, command, outcome and coverage. Mutation commands and
failure logs are separate from coverage data. Original and restored `run.py` SHA256 are both
`6b04d1d304913c9b1f558bc9efb38f386d528140d7e0aeff048f3d0dddf96157`.

The author's earlier 167-case PASS preceded the mechanical `current=shot` lint binding. The
independent 34-case run and whole composed run above execute that exact final binding.

## Coverage diagnostic, complete source universe

This is a pre-gate diagnostic only. It does not replace fresh full-suite coverage on the final
tree. The original baseline remains immutable; no old `run.py` arcs were reused on changed
production source.

Official `CoverageData` operations on own copies reconstruct the historical gains on identical
old production input tree `288ec39021840afc25940bcc2c6be337923a71ac`:

| Historical measurement | Additional unique statement/branch units |
| --- | ---: |
| Helper over baseline | 125 |
| Principal over baseline | 117 |
| Their actual union over baseline | 242 |
| Final API cohort net over that union | 216 |
| All historical cohorts over baseline | 458 |

The historical union is 95.00733104584549% on backend `0b668519`; it is not final-source
acceptance. `historical-diagnostic/summary.json` retains the independent reconstruction, source
and input hashes. Direct raw-arc intersection was rejected as a substitute for official
coverage analysis because multiline condition arcs are normalized by the reporter.

The corrected final-source diagnostic uses the fresh whole Apply data, adding baseline data only
for 300 files whose old/new Git blobs and source SHA256 match. The sole changed production file,
`api/app/apply/run.py`, uses fresh module data exclusively. All 301 current Python source files
are explicitly retained with `CoverageData.touch_files`, and the rendered file set is asserted
equal to that complete Git source universe. All eleven baseline-only ingestion sandbox files
and both zero-covered Gmail MCP files remain present.

Result: **95.0224466042898%**, 62,864 covered / 66,157 total statement and branch units. This is
14 covered units above `ceil(0.95 * 66157)`. There are 51,091 statements, 1,997 missed statements,
15,066 branches, 1,296 missed branches, 1,126 partial branches and 247 excluded lines, with the
exclusion count unchanged. The normal configured `coverage report` and `coverage json` both
exit 0 at the unchanged 95 floor.

`coverage-diagnostic-v2/source-manifest.json`, `summary.json`, `source-matched-union.sqlite`,
`source-matched-union.json` and `report.log` retain the per-file old/new blob and SHA manifest,
data sources, counts and actual report. Fresh module SQLite SHA256:
`a7bc977a0459acddbd175c32e782671e5a7883dcbb0f8f27e23618ae74f14097`.

The first diagnostic attempt is retained in `coverage-diagnostic/` and is rejected. Constructing
only from measured raw paths lost two never-executed Gmail MCP files, omitting 39 statements and
8 branches. The corrected v2 explicitly retains the complete source universe. No module rerun,
source change, omit, exclusion or threshold change was used to correct this analysis.

The following original SHA256 values matched before and after review:

| Immutable original | SHA256 |
| --- | --- |
| `baseline.sqlite` | `a6ee961e08e0250ca1062fc6f8a0b57807fce0b304e807281374b889cf824c63` |
| `baseline-coverage.json` | `2a170e64b70f400402d067f7f99d10289ec515916939517d161809ed4832325b` |
| `baseline-report.txt` | `f59e1db8a4ed234d48d01dec39cf52adfd57bcba84f36f4390349c6a9932b7e1` |

## Cleanup and remaining acceptance

All own pytest and caffeinate commands exited. Docker checks filtered by the review's own
`agent-apply604-review-` names and `jobapp.agent-test` label were empty after the runs. The
production mutation was restored byte-exact, the source worktree was clean before this report,
and the heavy slot was explicitly released. No real accounts, ATS/mail services, secret/env
files, protected branches, public reviews, pushes, merges or deployment were used.

Native acceptance remains OPEN. A separate fresh UX validator passed the corrected registry
path but exposed a later duplicate-posting setup conflict; a fresh fixer and a separate review
own that fixture follow-up. This review does not claim all five native UX cases passed.
The final quiet exact-tree Gate 4 and a fresh whole-PR review remain separate and required.
