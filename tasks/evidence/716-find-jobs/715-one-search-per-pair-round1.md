## Round 1 fixes

Branch `fix/715-r1` in `.claude/worktrees/fix715`, made from `claude/serene-gates-xxs89r`. The B17 merge that landed on the combined branch meanwhile is merged in, so the branch fast forwards. Head 7897c8ff4855f7caf6989e2f54f988b13d55a63e.

1. Reviewer fixup `fbbf4048` cherry-picked as 69d07706. It conflicted in `find-jobs.test.tsx`. I kept J11's "saving them came with a later update" line and the fixup's "At most 6 of your 9 ... in the order you saved them" line.

2. B1: the 40 model turns now hold per website, across all its searches.
   - `finder.FinderSession` has `turn_budget` (None for a careers page, where the runtime's 40 apply alone) and `turns`. Each tool call of the model counts one turn: navigate, interact, snapshot, wait_for, report and report_verified (one verified report is one turn, not two). A call past the budget is refused with `turn_limit` and nothing reaches the browser.
   - `RuntimeFinder` sends `turn_budget` in the run's input for interactive runs. `FinderInput.turn_budget` must be between 1 and MAX_TURNS. `runner.TURN_BUDGETED` (apply_form, browser_discovery) lowers the SDK's max_turns to that budget and never raises it. `Ran.turns` returns the runtime's count, read from the private run's output counts. A spent budget starts no model.
   - `interactive._Site.turns` adds up, per search, the greater of code's count and the runtime's count; a runtime `turn_limit` spends what was left. Each search gets `MAX_TURNS - site.turns`, and no later search runs once the website has no turn left (`turn_limit`).
   - Time: `_one` bounds the website by `min(sweep deadline, start + WALL_TIME_S)`. The 840 s are the website's, counted from its first search. Before, each search got its own 840 s, up to the sweep's 900 s limit.
   - Tests: `test_the_website_turn_cap_is_shared_by_its_searches` (6 searches of 9 calls get budgets 40, 31, 22, 13, 4 and stop at 40 in total, the sixth asks for no lease; and the reviewer's probe of 30 calls per search, 40 then 10, the third never asks). `test_the_runtime_s_turn_count_wins_when_it_is_higher_and_its_stop_ends_the_website`, `test_each_tool_call_is_a_turn_and_none_runs_past_the_budget`, `test_the_website_finder_time_is_shared_by_its_searches` (two searches of 500 s, both bounded at 840 s; the third gets `time_budget` and no lease), and `test_b13_runtime::test_a_search_s_run_gets_only_the_turns_its_website_has_left` (real runner: SDK max_turns 9, turns measured, a spent budget starts nothing, 41 refused). All were RED before the fix and GREEN after.

3. Follow ups:
   - 2a: already true in the combined code. `_Site.verified` filters before `commits.save`. The new DB test `test_unwanted_places_other_contracts_and_an_address_read_two_ways_never_reach_the_store` covers an unwanted place, another contract and an unwanted title through the real sweep: 1 stored, unwanted 2, other_contract 1. I checked it can fail: with the filter disabled it goes red.
   - 2b: this was a real bug in the count path, where one address could count in both `found` and `unwanted`. The new `_Site.place` keeps each address in one set only: unwanted wins over another contract, which wins over found. The store path gives the same order. Once an address is stored, a later reading neither stores it again nor counts it again. Tests: `test_an_address_read_two_ways_counts_once_and_the_refusal_wins` (7 rows, both orders), `test_a_stored_address_stays_stored_once_whatever_a_later_search_reads` (3 rows), and the DB test above (`jobs/flip`).
   - 5: the LinkedIn test now asserts 3 reservations. New DB test `tests/opportunities/test_b14_policy.py::test_one_linkedin_sweep_a_day_holds_across_the_searches_of_the_website`: through the real PlatformPolicy and guard, both searches of sweep 1 reserve and open LinkedIn, and sweep 2 the same day gets `linkedin_daily` with no lease.
   - 7: `page_budget` and `token_budget` now say "... this time, shared by all its searches. What was read is kept." I added a `turn_limit` sentence, which used to show "It stopped early." The labels test pins all three: no dashes, "shared by all its searches", never "in one search".

4. Spec: SPEC-browser.md:113 now says that a job website is one source and that its searches share the 840 s (from its first search), the 20 pages, the 40 model turns and the 600,000 tokens. The 100 verified postings hold per search, and the shared pages bound them further. The 2026-10-26 line of docs/decisions.md and tasks/jobs/interactive-search-626.md:129 say the same.

Changed outside the browser module: `api/app/agents/runner.py` (`TURN_BUDGETED`, one line plus its constant), and one assertion in `tests/browser/test_selected_interactive.py` (the run's input now holds `turn_budget`).

Tests run (after the merge of the combined branch):
- `ops/agent-test.sh tests/browser -m "not docker" -n 2`: 3650 passed.
- `tests/jobs`: 1224 passed, 15 skipped.
- `tests/opportunities`: 1693 passed.
- `tests/agents` with `tests/apply/test_a18_budget.py` (before the merge; the merge does not touch the runner): 1407 passed, 19 skipped.
- ruff check and format: clean. `pnpm typecheck` and `lint`: clean.
- vitest `src/features/jobs`: 428 passed. One earlier full run failed the long "starts a sweep, shows live progress" test, which passed alone and in the next full run (reviewer follow up 11, flaky under load).
- `e2e_groups.py`: OK. `uvx pre-commit run --files` on every changed file: all passed.
- `ci_plan_test.py`: fails as on origin/main (dynamic import in api/app/apply/profile_router.py: 3 errors, 1 failure). Not fixed.
