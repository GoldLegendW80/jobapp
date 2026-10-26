# Issue 605 blank replacement proof and local fix

Local commit: `53b385b2ffac02469dfcdfdd7afb5dd404640c15`
Tree: `73e0fe72c6b19e8e1f4b29e5439547e175c81680`
Base: `11d1c82084f34b36932277c992fcf85434c62253`
Branch: `browser/605-blank-guard-replacement`
Worktree: `<checkout>/.claude/worktrees/codex-605-blank-replacement`
Scope: exactly `browser/browserd/tools/actions.py` and new `api/tests/browser/test_guard_blank_replacement.py`, 221 additions / 4 removals. Working tree clean. No push or public verdict.

## Proof and change

The regression installs the actual `Tab.guard()` page listener, invokes actual `chromium.guard_memory()` at 1006 MiB / 1024 MiB with unchanged 90 percent, drains actual scheduled `close_off_list()` tasks, and invokes the actual `Chromium._watch_exit()` clean-exit path when the modeled final-page closure produces process status zero. Before source changes, both listener-before-new_page-return and listener-after-return schedules remove the exact blank replacement and emit `browser_closed` via the real watcher. The original form's closure and exact original tab_closed/memory/used_mib event are asserted before the failing lease-survival assertion. These are source-backed fake Playwright/process boundaries, not a reconstruction of the refusal predicate and not a native runtime measurement.

Pinned upstream source checked on 2026-10-23:
https://github.com/microsoft/playwright-python/blob/v1.55.0/playwright/_impl/_browser_context.py
`_on_page` appends the page then emits the page event; `new_page` separately awaits a channel response. Listener completion relative to the awaiting caller is scheduled explicitly. Server-side `newPage` also checks whether creation closed before it returns; the modeled early-listener schedule represents completion before the Python awaiting caller resumes, not an assertion that a server creation failure must return a page.

The minimal correction makes `Tab.settle` use existing `Tab.allowed`, and gives `close_off_list` the same exact `about:blank` allowance. Policy remains unchanged and still refuses agent navigation to about:blank. No chromium.py, policy.py, caps, thresholds, formula, cadence, deadlines, fixtures, configs or existing assertions changed. A second actual cleanup inconsistency in settle was independently RED before fixing it.

Controls: 23 forbidden URLs times both listener-first/settle-first cleanups, including denied hosts, lookalike/nonexact about:blank URLs, private hosts, javascript/data/file/chrome schemes. Four allowed URLs times both cleanup orders survive, including exact blank and allowed site pages. Two integrated guard schedules produce the retained replacement. All 56 cases pass after the correction.

## Commands and retained results

All pytest commands below ran from this worktree through `rtk proxy caffeinate -i bash ops/agent-test.sh --name codex-605-blank`, using at most two workers. The wrapper created only owned temporary Postgres/Redis helpers and removed them on exit.

1. `tests/browser/test_guard_blank_replacement.py -n 2 -q`: expected initial RED, exit 1, 3 failed / 49 passed in 12.64 s. `/tmp/jobapp-605-blank-red.log`. Production source unchanged at this point.
2. Same focused arguments after adding an independent settle-first ordering, still before production source edits: expected RED, exit 1, 4 failed / 52 passed in 2.70 s. `/tmp/jobapp-605-blank-red-settle.log`.
3. Same focused arguments after minimal fix: GREEN, exit 0, 56 passed in 2.41 s. `/tmp/jobapp-605-blank-green.log`.
4. `tests/browser -m 'not docker' -n 2 -q --junitxml=/tmp/jobapp-605-blank-browser.xml`: exit 0, 2184 passed in 27.48 s. `/tmp/jobapp-605-blank-browser.log`. This verifies the exact final staged/committed source, including the lint-only fake keyword signature and split assertion adjustments described below.
5. `tests/browser -m 'not docker' --collect-only -q`: exit 0, 2184/2301 selected, 117 docker tests deselected. `/tmp/jobapp-605-blank-selection.log`. No non-native test skips.
6. `rtk proxy .venv/bin/ruff check app tests ../browser/browserd` from api: pass. `rtk proxy .venv/bin/ruff format --check app tests ../browser/browserd`: pass, 817 files.
7. `rtk proxy python3 .github/scripts/ci_plan_test.py`: exit 0, all 81 tests pass, `/tmp/jobapp-605-blank-ci-plan.log`.
8. `rtk proxy python3 .github/scripts/e2e_groups.py`: exit 0, 29 CI specs, 4 exclusions with reasons, 53 local-group specs, `/tmp/jobapp-605-blank-registry.log`.
9. `rtk proxy pre-commit run --files browser/browserd/tools/actions.py api/tests/browser/test_guard_blank_replacement.py`: exit 0, applicable hooks including gitleaks, Ruff and formatting pass; unrelated file-type hooks report no files. `/tmp/jobapp-605-blank-hooks.log`.
10. Normal local `git commit -F /tmp/jobapp-605-blank-commit.txt`: exit 0, normal hooks pass, `/tmp/jobapp-605-blank-commit.log`. `git diff HEAD^ HEAD --check` passes; `git status --short` empty.

## Honest intermediate attempts

Initial `uv run ruff format` could not access the sandboxed uv cache. Used the installed worktree `.venv/bin/ruff` directly instead. First Ruff check found ASYNC109 on the fake Playwright timeout keyword signature and PT018 on a combined assertion. Fixed by accepting the same checked keyword through `**kw` and splitting the assertion; no assertion or timeout weakened.

A temporary test-only edit replaced four conventional import-order annotations with importlib loading to interpret the brief's no-suppressions instruction literally. An additional whole non-native run was started during that edit, with the same selection and `--junitxml=/tmp/jobapp-605-blank-browser-final.xml`. Parent clarified that established sys.path bootstrap with narrow E402 import annotations is required and that this is a routine convention exception. The exact prior staged source was restored while the extra run was in flight, and `git diff --exit-code -- api/tests/browser/test_guard_blank_replacement.py` proved equality to the source already verified in command 4. The extra run passed 2184 in 24.41 s, `/tmp/jobapp-605-blank-browser-final.log`, but is retained as an intermediate attempt rather than substituted for command 4. No further repeat was needed or performed. No dynamic import machinery is committed.

The final new file has exactly four narrow `# noqa: E402` annotations on browserd imports after its established repository sys.path bootstrap. No behavior/type/coverage/assertion suppression or blanket lint bypass is added.

Initial local worktree creation was sandbox-denied on .git metadata and succeeded under the explicitly authorized local Git escalation. No automatic approval rejection occurred. Diagnostic process-name inspection initially hit the sandbox; read-only escalated inspection followed. No production credentials or .env files were read.

## Cleanup and remaining limits

All test sessions completed. `docker ps -a --filter label=jobapp.agent-test=codex-605-blank --format '{{.Names}}'` returned no names after the final session. No browser/native stack, image build, cache removal, runtime experiment, real account, ATS host or mail action was used. Worktree intentionally retained for fresh independent review.

This fixes only replacement-tab lifecycle consistency. The guard still intentionally destroys the original form at the same threshold. No measurement identifies the native memory spike or proves the exact native interleaving; original native logs lack page identities. This is not the memory-pressure remedy and supplies no native all-five UX/all-sixteen-layout acceptance, capacity approval, gate approval or public PR verdict. Those remain separate required work for the parent.
