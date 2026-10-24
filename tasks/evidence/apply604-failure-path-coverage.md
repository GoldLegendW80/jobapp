# Apply 604 failure-path coverage checkpoints

Status: scoped source, regression cohorts and composed Apply module CLOSED. Final fresh complete quiet Gate4 and whole-PR acceptance remain OPEN. The coverage floor remains95percent; no exclusion, skip, timeout, cap or assertion was relaxed.

## Measured baseline

Backend source `0b6685190fcb33d52bd8aa2366c709add2b775bd`, after current main1244322 composition. The full non-Docker suite passes28,118 tests with57 existing personal-source skips in1836.21seconds; the exact excluded timing case then passes alone in5.87seconds on a quiet machine. The immutable externally retained SQLite passes integrity_check.

Actual branch coverage is94.31503846909624percent:48,785 of51,091 statements and13,611 of15,066 branches, with2,306 missed statements,1,455 missed branches and1,253 partial branches. Reaching true95 on that source needs454 additional unique covered statement/branch units; the displayed rounded percentage is not used to waive the floor.

Evidence directory: `/tmp/jobapp-apply604-cov-0b668519-20261023a/`. Complete `suite.log`, `alone.log`, their exits, `baseline.sqlite`, `baseline-coverage.json`, `baseline-report.txt` and source/command manifests survive worktree cleanup. Baseline data is immutable after the singleton.

## Profile and fill cohort

Local author checkpoint `6da85bacbf15411a94a36a2ed4998d43a16d603b`, three new isolated test files (548 lines); root merge496228fb. They exercise durable profile-fill errors, sign-in/callback ownership, page changes, consumed/expired/stale/cancelled Save authority, final external-write checks and application-fill failures. Production/shared fixtures/configuration were unchanged.

All39 profile fill/save cases pass in the final combined focused selection. That47-case selection reports45PASS and two failures in17.03seconds. One is a new HTTP429 test's incorrect assumption of an Application.retry_at field; the checkpoint instead asserts the actual due boundary at599/600seconds. The HTTP403 case exposes a real production error: `_wait_for_person` calls interactive `adapter.discover` using a snapshot-only Passive object; empty combobox discovery attempts `click` and raises AttributeError outside `_guarded`. A fresh author corrects that production defect below. Both final regressions now pass in the composed module. Earlier two profile-save fake protocol/race-setup failures were corrected and retained in the raw logs.

Full Ruff, formatting809files, CI planner81checks, e2e registry, secrets and normal commit hooks pass. No whole Apply rerun was claimed. Whole composed Apply is assigned once after all authors integrate. Principal's historical copied-data diagnostic adds117 unique units; it includes failed cases and is not acceptance.

## Submit, manual, adapter and persisted-gate cohort

Final local author checkpoint `2b7e8614de9d45c6aa1a762ba64a3e6872df2b99`, four new isolated test files (641 lines); root mergecaf9831a. They cover unavailable/tokenless/crashed/release-failed submit and manual sessions, persisted document/answer staleness, safe adapter refusals and readback failures, restart recovery and email retry history. Production/shared fixtures/configuration were unchanged.

All76 focused cases pass in19.90seconds, with zero failures or skips. The command exits1 solely because this sparse test selection measures28.26percent against the unchanged95percent floor; test PASS and command failure are reported separately. Two earlier runs report72PASS/four email setup failures: first an invalid approval helper prerequisite, then a deliberately unknown-submit fixture missing the required click timestamp. The corrected historical fixture supplies that timestamp and freezes both sent versions; the retry assertions verify those freezes survive. Both failed attempts remain retained.

Full Ruff/format and normal commit hooks pass; CI planner81checks and registry pass. Own helper services are removed and heavy slot released. Evidence: `/tmp/jobapp-apply604-cov-submit-20261023a/`, including `focused-final.log`, `focused-final.json`, SQLite, prior failed runs and diagnostic input copies/manifests.

## HTTP403 recovery correction

Author checkpoint `d23f88119aba3aa91f4482048ebb8bb49f6a80c2` changes only ten production lines in run.py, adds34 persisted safety cases, and corrects the blocked-page fake while retaining its original assertions. Passive inspection reads the already captured snapshot through the adapter and refuses errors, pending steps, cuts and bad anchors. Existing browser and database resume checks remain before active fill. Frame/posting checks and all original budgets/authority are preserved.

Actual old-source RED is three Passive.click failures in18.35seconds. The next run passes33 and exposes one inherited fake's inconsistent expected outcome: its valid usable form legitimately resumes. The corrected fake supplies Access denied, keeping the original needs_person/forbidden/lease assertions. All167 focused recovery cases then pass in35.63seconds. The final lint-only captured-snapshot binding follows that focused measurement; independent exact-source testing subsequently passes all34 new cases in11.52seconds and an inverted cut guard fails the intended persisted assertion. The original source is restored byte for byte. The complete module below also uses that final binding.

Evidence: `/tmp/jobapp-apply604-forbidden-496228fb-20261023/` and `/tmp/jobapp-apply604-recovery-review-20261023/`. All author/static/normal-hook checks pass and owned helper services are removed.

## API and answer-boundary cohort

Author checkpoint `5d94fce6bbbd756d124e1b4babd3b35cf9b4313b`, root merge `593d2b919376f0472c37ba79a854707f7e40edfc`, adds only two isolated files (1037 lines). All84 cases pass in25.84seconds. Command exit1 is solely the sparse34.67percent measurement against the unchanged95floor. Earlier harness startup, URL fragment-encoding and sanitized-error expectation failures remain retained as setup/contract errors; none required a production change. The cases exercise route ownership/refusals, durable queue outcomes, local application-edit/Saved answers separation, answer/widget/source boundaries, and model/form runtime cancellation cleanup.

Full Ruff, formatting, registry and normal hooks pass; final committed-tree CI planner passes81 in37.162seconds. Evidence: `/tmp/jobapp-apply604-cov-boundaries-20261023a/`, final `focused-fifth.{sqlite,json,log}` and `diagnostic-union/contribution.json`. Owned services are removed.

## Complete composed module and coverage diagnostic

An independent fresh reviewer runs the WHOLE Apply module once on exact source `593d2b919376f0472c37ba79a854707f7e40edfc`, tree `0aded10fc88777c25f16b91a3fd955183f940859`: **8573 PASS, zero FAIL or SKIP in457.20seconds**. The command takes476.86seconds and exits1 solely because this sparse module measures49.77percent of the configured application-wide coverage requirement. No rerun is claimed. Raw log, command, exit, fresh SQLite and JSON are retained in `/tmp/jobapp-apply604-recovery-review-20261023/`; all own services/caffeinate exit and cleanup is verified.

Historical official copied-data reproduction confirms helper125/principal117 units, their242-unit union, then API cohort216 further unique units: old-source95.00733104584549percent. Those historical data include old failed cases and cannot establish final-source acceptance.

For the corrected source, the independent diagnostic retains baseline coverage only for300 files whose old/current Git blob AND SHA match. Changed run.py receives data solely from the fresh8573-pass module. All301 current Python source paths are explicitly present, including11 ingestion sandbox files and two zero-covered Gmail MCP files. The first diagnostic omitted those two zero-data files from its universe; it is preserved and explicitly rejected. The corrected official report and JSON both exit0 with the unchanged95configuration: **95.0224466042898percent, 62864 covered of66157 statement/branch units**,14 units above the minimum integer count for true95. Baseline SQLite/JSON/report hashes remain unchanged. Mutation data are separate.

This complete-universe hash-safe composition is a pre-gate diagnostic, not a fresh full-suite verdict. The source/cohort/module scoped review is CLOSED with no Required/Critical findings; its local report records exact independent evidence. Canonical-posting Review UX fixture repair and native acceptance remain separately open. The final complete quiet exact-tree Gate4 must freshly run all API/coverage, Docker, frontend, migrations/scripts/secrets and all nine journey groups. No diagnostic or failed native attempt substitutes for it.
