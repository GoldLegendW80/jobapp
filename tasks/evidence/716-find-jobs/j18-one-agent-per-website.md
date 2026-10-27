J18: one agent per job website, with all the saved criteria and no budget cap

Part of the combined pull request of branch `claude/serene-gates-xxs89r` (no issue: this session cannot create issues). Branch `jobs/j18-one-agent`, merged with `claude/serene-gates-xxs89r` (J16 round 1 included) before the final test run.

## Owner's decisions (2026-10-27)

- "An LLM agent for each website that will search intelligently, just like a human, using all the parameters provided to it."
- Its budget per website: "no limit".

This replaces the 2026-10-26 decision "One search per combination" (one native search per title and place pair, at most 6 a website, each on its own lease and frozen scope) and the round 1 fix of #715 that shared 40 turns and 840 s across those searches.

## What and why, per acceptance line

1. **One lease and one finder session per website per sweep, all criteria as context.** `interactive._one` now runs `_website` once: one lease, one `FinderSession`, one `browser_discovery` run. `searches_for` returns one `InteractiveScope` holding every saved title and place; `CRITERIA` (the model's context) now holds wanted and unwanted titles and places, contract types, remote stance, seniority, industries, company types, pay and days, plus the custom instructions (private, never typed). The new prompt asks the model to choose its searches, their order and when the website is covered. Tests: `test_multi_search.py::test_one_agent_runs_every_saved_search_in_one_session_on_one_lease` (2 titles × 2 places: one session, one lease, 4 searches, one frozen scope on every call, the canary instructions never in it), `test_the_agent_gets_all_the_person_s_criteria_as_context_never_to_type`.
2. **What it may type stays the person's own words.** browserd's frozen scope (`discovery.Scope`) now carries `titles`, `places` and `filters` instead of one `query`. A search box (q, query, search, keyword, keywords) takes exactly one saved title, the `location` box exactly one saved place, either one nothing to clear it; a select takes only its one saved value (the location select: one saved place; remote: the saved stance; contract: the one saved contract type). The comparison is exact string equality against an allow list, so it fails closed. A search request still needs one saved title, a saved place when places are saved, and every saved filter. `FinderSession.interact` refuses a non saved typed value before the browser, which is the first check; browserd is the authority. Every other guard is unchanged: no apply words, no form but the search, GET and HEAD only, sign out refused, B17's signed in rules. `own_words` now reads every saved title and place. Tests: `test_j18_scope.py` (377 cases). The accept table covers every saved value in its own field and "" in each box. The refuse tables cover 38 near titles in each of the 5 search box names (case, spacing, tab, newline, glued, plural, inside a longer value, word order, full width, Cyrillic, zero width, soft hyphen, accent dropped, NFD, typographic apostrophe, regex, unwanted title, injection, 5,000 characters), 19 near places, titles and places swapped between fields, every other field, non text values, and 19 select cases. A seeded independent generator makes 200 neighbours of each of the 9 saved values: all refused. The request table has 20 accepted and 13 refused requests. The malformed scope table has 16 rows, including the old one query shape. Mutation probe: with case folding and trimming in browserd, 48 of these tests fail. `test_multi_search.py::test_a_value_the_person_did_not_save_is_refused_before_the_browser` has 17 rows; the refused text never reaches the browser and the agent goes on.
3. **No cap of its own; what still bounds it.** The interactive session has no page cap (`PAGES_MAX` applies to careers pages only), no verified postings cap (`POSTINGS_MAX` stays a careers page cap and the size limit of one report call), and no turn count. Its run input has no `token_budget` (`FinderInput.token_budget` is optional, and only careers runs send it). `browser_discovery` is out of `runner.TURN_BUDGETED` (apply_form keeps it). Its definition's `max_turns` is raised to 10,000 and its `wall_time_s` to 900: these are the runtime ceilings, and the time ends the run first. The platform policy keeps LinkedIn's 5 pages a sweep and one sweep a day, but no longer gives other platforms the finder's 20 pages. What still bounds a website:
   - the sweep's 900 s, shared fairly: `run_selected` gives each website the sweep time left divided by the websites left;
   - the lease's own maximum;
   - LinkedIn's caps, pacing and the 7 day pause.

   The model is told `seconds_left` in its first message and in every page it reads. Tests:
   - `test_an_agent_run_of_more_than_40_turns_and_20_pages_and_100_postings_is_allowed`: 302 tool calls, 153 pages, 150 verified postings saved, twice the old token budget;
   - `test_the_sweep_time_is_shared_fairly_between_the_websites`: 2 sites get [500, 1000]; 3 sites get [333.3, 550, 1000];
   - `test_a_website_past_its_share_stops_there_and_the_next_one_gets_its_own`;
   - `test_the_agent_is_told_the_seconds_it_has_left`;
   - `test_linkedin_pages_of_the_sweep_still_hold_for_its_agent`;
   - `test_another_platform_has_no_page_cap_of_the_policy`;
   - the DB test `test_b14_policy.py::test_one_linkedin_sweep_a_day_holds_for_the_agent_of_the_website`: one lease and both searches in sweep 1, then `linkedin_daily` with no lease the same day;
   - `test_b13_runtime.py::test_a_website_s_run_has_no_turn_or_token_cap_and_is_told_its_time`: the real runner gives SDK max_turns 10,000; the run input is `{"request"}` only; `TURN_BUDGETED == {"apply_form"}`; a careers page keeps 40;
   - `test_selected_interactive.py::test_a_website_has_no_page_cap_and_a_careers_page_keeps_its_ceilings`.
4. **Merge and filters before the store.** Postings merge per canonical address: within the run, a second report of an address is refused as `duplicate`, and `_Site.verified` stores an address once. A merge no longer makes the website `unsupported_interaction`, because `duplicate` is left out of that check. J11's post-read filters still run before the store (unwanted titles and places, several contract types), and so does cancellation safety. B17 and J17 are unchanged. Tests:
   - `test_postings_merge_once_per_address_across_the_agent_s_searches`;
   - the DB tests `test_interactive_findings.py::test_the_searches_of_a_website_store_each_posting_once_and_a_refusal_keeps_them` and `test_unwanted_places_other_contracts_and_an_address_read_two_ways_never_reach_the_store`, now one run with two searches;
   - `test_a_stored_address_stays_stored_once_whatever_a_later_reading_says`;
   - `test_unwanted_titles_and_places_are_context_and_dropped_after_reading`, which uses the real `location_exclude` TermList, per the orchestrator's note.
5. **Removed:** the per combination searches and their leases, `SEARCHES_MAX`, `Searches.scopes/total`, `searches_planned` and `searches_total` (api schema, router, web schema), `_combination`, the shared website turn budget (`FinderSession.turn_budget/turns/_turn`, `FinderInput.turn_budget`, `Ran.turns`, `finder._turns`, the `browser_discovery` entry of `TURN_BUDGETED`), the "At most 6 of your N combinations" sentence and its tests, the "shared by all its searches" sentences, and the `turn_limit` sentence. **The summary names the searches the agent ran**, from its own tool calls: `FinderSession.searches` records a successful `browser_click` after the agent typed a saved title, together with the saved place it typed or chose. The entry keeps `searches` (the count) and `searched` (the first 50, each `{title, place}`), and the web shows "3 searches ran: robot engineer in Paris; robot engineer in Lyon; drone pilot." Tests: `test_a_saved_value_or_nothing_is_typed_and_only_a_submitted_search_is_listed`, `test_the_summary_lists_at_most_fifty_searches_and_counts_all`, `test_the_summary_names_the_searches_that_ran_and_counts_them` (the router reads bad stored shapes defensively), `labels.test.ts`, `find-jobs.test.tsx`, and the J15 journey's line "1 search ran: engineer in Paris.".
6. **Spec and decisions:** `SPEC-browser.md` (the caps bullet rewritten), `SPEC-jobs.md` (a new bullet on one agent per website), `docs/decisions.md` (the 2026-10-26 bullet replaced, with the owner's words quoted), and `tasks/jobs/interactive-search-626.md` (the J10 acceptance ceilings, the several titles paragraph, and the typed input rule: the summary names only the person's own saved values).
7. **Tests:** see above. Journeys: `web/e2e/browser-j15.spec.ts` asserts `searches: 1`, `searched: [{engineer, Paris}]` and the summary sentence, and no longer bounds the website's pages by 20. `web/e2e/browser-b17.spec.ts` asserts `searches: 1`. Both are typechecked; neither has been run (see below).

## Tests run (worktree `.claude/worktrees/j18`, after the merge of the combined branch)

- `ops/agent-test.sh --name j18 tests/browser -m "not docker" -n 2`: 4196 passed.
- `tests/jobs`: 1293 passed, 15 skipped (the owner's engine data).
- `tests/opportunities`: 1706 passed.
- `tests/agents tests/apply/test_a18_budget.py tests/apply/test_a8_known_fallback.py`: 1493 passed, 19 skipped (live model tests). After the last test addition, `tests/browser/test_b13_runtime.py` was run again: 5 passed.
- `uv run ruff check app tests` and `ruff format --check app tests` (api), and `ruff check .` and `ruff format --check .` (browser): clean.
- `pnpm --dir web run typecheck` and `lint`: clean. `vitest run src/features/jobs --maxWorkers=2`: 501 passed (17 files).
- `python3 .github/scripts/e2e_groups.py`: OK. `ci_plan_test.py`: 3 errors and 1 failure, the same as on origin/main (known).
- `uvx pre-commit run --files <the 33 changed files>`: all passed, after prettier's line breaks in the B17 journey.

## Not run

- The Playwright journeys (`browser-j15`, `browser-b17` and the rest of the browser group): the orchestrator runs them on the sandbox stand in stack. No image was built.
- `tests/browser/test_b13_native.py` (docker marked, a real Chromium): only its scope constructions changed.

## Changed outside the browser and jobs interactive files

- `api/app/agents/runner.py`: `TURN_BUDGETED` back to `{"apply_form"}`, plus its comment.
- `api/app/browser/platform_policy.py`: only LinkedIn keeps a page cap.
- `api/app/jobs/schemas.py`, `api/app/jobs/sweep_router.py`: `SearchRanOut`, `SourceOut.searched`, `SEARCHES_LISTED`; `searches_planned` and `searches_total` removed.
- `browser/browserd/tools/discovery.py`: the scope with sets of saved values.
- Tests in `tests/opportunities` (`test_b14_policy.py`, `test_o8_summary.py`).

Size: 33 files, 1391 insertions and 764 deletions, mostly tests (two test files are rewritten or new). That is above the 1500 line guideline; the task is one coherent change.

## Declared open points

1. A search is listed in the summary when a `browser_click` succeeds after the agent typed a saved title. A search reached only by opening a search address (no typing) is not listed. A pagination click after the agent typed another title without submitting it would list that title.
2. A value saved both as a title and as a place is listed as a title in the summary. browserd still binds each field to its own set.
3. With no saved place, a form whose `location` box is empty is still refused when submitted, as before J18 (the request rule is unchanged). Typing "" clears a box, but the request needs saved values only.
4. The run has no token cap. Its cost is bounded by its share of the sweep's 900 s and by the lease's 900 s maximum. With the 5 to 15 s pacing, a website reads roughly 60 to 90 pages a sweep. This is the owner's "no limit", bounded by time as the spec says. The person's own model credential pays for it.
5. Indeed, Welcome to the Jungle and Free-Work no longer have a page cap from the platform policy. Their pacing, one sweep a day and the 7 day pause remain.
6. The summary lists only the person's own saved titles and places. The same row already holds them in `search_scope`. Code records only values found in the frozen sets, and browserd refuses any other typed value.

## Self review (docs/agents/review-checklist.md)

1. Heuristic rule: the typing allow list is exact equality and fails closed. It has accept and refuse tables across every axis, 200 generated neighbours per saved value from an independent generator, and a mutation probe that turns 48 tests red.
2. Shared state: no new read then write. `_SCOPES` now holds the session and is read under `_LOCK`; `seconds_left` does not await.
3. Invariant with an injected clock: "a website gets the time left divided by the websites left" is tested with 2 and 3 websites and with a website that runs past its share, all on the injected clock.
4. Sinks: the summary stores only saved values. Instructions never reach the tool scope (canary test). No new log.
5. "Never" and "every" sentences: "never typed" (unwanted values) has the refuse rows `sales` and `Lyon`. "One website never starves the next" has the share tests. The no cap claims have the 302 call, 153 page and 150 posting test. Stale values were swept: `SEARCHES_MAX`, "combination", `searches_planned`, "shared by all its searches" and `turn_budget` remain only in apply's own code.
6. Size caps: the scope is at most 100 titles and 100 places of 300 characters, and 32 KiB as sent with each call (browserd takes 64 KiB). A larger scope is `unsupported_criteria` before a lease (3 row test).
9. Tests can fail: mutation probe above. The time tests use the injected clock, with no sleeps.
11. Merge: the combined branch is merged in (J16 round 1); there was no conflict.
14. Every acceptance line is listed above with its test.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_017CqfhHnPbtyZ6jyK1KZS4T
