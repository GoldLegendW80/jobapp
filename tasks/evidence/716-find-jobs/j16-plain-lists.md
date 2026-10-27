Search: four plain word lists, with a new Unwanted places list

## What

The search is now four plain word lists, Job titles, Unwanted titles, Places and a new Unwanted places, in the profile's Search tab and in the Find jobs setup alike. Each list's regular expression switch moves under the shared Advanced (`web/src/components/advanced-section.tsx`), off by default. The new `search.location_exclude` term list leaves out every posting whose place matches it, on boards, feeds, browser sources and the website searches.

## Why

Owner decision of 2026-10-27 (recorded in `docs/decisions.md`, Opportunities section): plain lists by default (SPEC-opportunities.md "Simple by default", the part of #703 about these lists), and a way to say which places are not wanted (#626).

## Spec and task

SPEC-opportunities.md "Simple by default"; #626 and #703. Branch `jobs/j16-plain-lists` (the orchestrator's id J16; note that tasks/jobs/todo.md already uses J16 for #641, so no task id is written in the code). Refs #626, Refs #703.

## Acceptance, line by line

1. **Four plain lists, labels from wording.ts, switch under Advanced, off by default; a saved regular expression still shows and works.** Done. One shared component `web/src/features/profile/search-lists.tsx` renders the four lists (`WORDING.jobTitles`, `unwantedTitles`, `places`, `unwantedPlaces`) and, under `AdvancedSection`, a "Regular expressions" fieldset with one switch per list. Used by `search-tab.tsx` (group "Titles and places") and `jobs/search-setup.tsx` (the three old `TermChips` replaced by one line). `TermChips` gets `regexSwitch={false}` (no inline switch; a folded list holding expressions says so under its chips) and exports `TermListRegexSwitch`. Advanced opens by itself when a list holds regular expressions at load, or when a reload or reset brings one. Evidence: `web/src/features/profile/search-lists.test.tsx` (folded by default with no switch shown; a saved pattern opens Advanced, can be folded, still saves as regex; a list turned to regex checks its terms that way and saves; an old search reads the new list empty; a reset with a regex list reopens Advanced), `search-tab.test.tsx` (labels, saved switch checked, Unwanted places saved), `search-setup.test.tsx` ("shows the four plain lists and saves the unwanted places before starting"), `lib/wording.test.ts` (the four words pinned, no dash in any value).
2. **`location_exclude` TermList, expand only, saved, exported, imported; old profiles load it empty.** Done. `SearchSection.location_exclude: TermList` right after `location` (JSONB in the profile row: no migration). Evidence: `api/tests/profile/test_unwanted_places.py` (an old section reads it empty, stored order, 8 valid shapes kept, 14 invalid shapes refused with their path, PUT and GET, 422 on a broken regex with the old value kept, export round trip, an old export without the field imports it empty, isolation between two users). A save or an import that leaves the key out keeps the stored list (round 1 review, B1): `location_exclude` joins the interactive search fields in `schemas.KEPT_WHEN_OMITTED`, so an older browser tab, an export made before this change or a History revision restored from before it cannot empty the person's Unwanted places; a list sent, the empty list included, replaces the stored one. Evidence: `test_a_save_from_an_old_tab_keeps_the_stored_list_and_a_sent_empty_list_clears_it`, `test_an_old_export_keeps_the_unwanted_places_already_stored`, `test_an_export_with_an_empty_list_clears_the_stored_one`, and the old export into an empty profile reads empty; three of these fail without the change (with `test_a_search_section_saved_before_the_field_reads_it_empty`, four in all), while the empty list test passes either way, as it pins the other direction. The golden `api/tests/profile/fixtures/owner_export.json` is unchanged against main (the importer's output leaves the key out; regenerated with `UPDATE_GOLDEN=1`, reviewed). Web: `Search.location_exclude?` with `EMPTY.search` filling it, form schema, `toSearchForm` and `fromSearchForm`.
3. **Unwanted places apply everywhere the search filters, same matcher as `title_exclude`.** Done. `SearchFilter` (`api/app/jobs/sweep.py`) compiles `location_exclude` with `matching.compile_terms` like `title_exclude` and returns `location_excluded` after the wanted places; boards, feeds (O7) and browser sources (B8) all store through `store_postings`, so all three apply it. The frozen run scope carries it (raw search), and the owner's criteria summary lists it (`sweep_router.py`). Website searches: PR #715's `interactive.py` reads `location_exclude` defensively; on a scratch merge of this branch with #715 its tests pass (153 passed with `tests/browser/test_multi_search.py`, `test_selected_interactive.py`, `tests/jobs/test_frozen_search_scope.py` and this branch's tests), and a scratch test with a real TermList drops both postings in Paris and keeps them for Lyon (not committed here since it needs #715's code; the file is in the orchestrator's scratchpad as `j16-after-715-test_unwanted_places_interactive.py`).
4. **Tests, including an unwanted place filtering a posting of a fake board.** Done: `api/tests/jobs/test_unwanted_places.py::test_an_unwanted_place_filters_a_posting_of_a_fake_board` (fake-ok over real HTTP: London left out, kept again without the list), plus the frozen scope test, a feed test (`test_o7_feeds.py::test_the_unwanted_places_apply_to_a_feed`) and a browser source test (`test_sweep_browser_sources.py::test_browser_postings_in_an_unwanted_place_are_not_kept`). With the two filter lines removed, 30 of these tests fail.

## Edge cases covered

- An unwanted place wins over a wanted one ("Remote, United States" with Places Remote and Unwanted places United States is left out); the title decisions come first, the contract step still runs after.
- Matcher table (`test_unwanted_places.py`): 20 locations left out (case, punctuation, parentheses, slash, semicolon, trailing dot, hyphen and no-break space as word edges, plural, a 100,000 character location) and 16 kept (empty, longer words such as "Parisian", glued words, other countries, and the declared limits below); 7 rows for a regular expression list; an empty list leaves nothing out.
- An old search (no key) filters as before and loads in the web as an empty list; the web always sends the key, so saving from the Search tab or Find jobs writes the empty or typed list (Clear works), while an older tab, which does not send it, keeps the stored list.
- The ingestion items read an omitted list as empty (`items.canonical`), so a proposal for the unwanted places applies to, and is taken back from, a search saved without the key.
- Size: the list inherits every term list cap (300 terms, 200 characters per word, 500 per expression, the joined compile check), tested at the boundary.

## Tests run (results)

- `ops/agent-test.sh --name j16 tests/profile -m "not docker" -n 2`: 1613 passed, 17 skipped.
- `ops/agent-test.sh --name j16 tests/jobs -m "not docker" -n 2`: 1293 passed, 15 skipped.
- `ops/agent-test.sh --name j16 tests/opportunities -m "not docker" -n 2`: 1706 passed.
- `tests/browser` (whole folder, `-m "not docker" -n 2`): 3762 passed (run with the interactive.py change that was then left out; `test_unwanted_places_interactive.py`, `test_multi_search.py`, `test_selected_interactive.py` rerun on the final tree: 109 passed).
- Ingestion: `tests/ingestion/test_items.py`, `test_item_proposals.py`, `test_item_proposals_api.py`, `tests/test_config.py`: 252 passed; `test_merge.py`, `test_review_api.py`, `test_review_g13_api.py`, the four `test_ingestion_locks*.py` and `test_engine.py`: 248 passed. Apply tests that read the full search fixture (7 files): 611 passed (before round 1).
- `uv run ruff check app tests` and `uv run ruff format --check app tests`: clean.
- `pnpm --dir web run typecheck`, `pnpm --dir web run lint`: clean. Prettier clean.
- `vitest run src/features/profile src/features/jobs src/lib`: 1250 tests, all pass with a 30 s timeout; with the default 5 s timeout two older Search tab tests ("refuses a days window", "sorts by company") ran out at 5.3 s on this shared machine (load average 5 to 9). Measured alone, twice each: 3.3 to 4.2 s on this branch against 3.0 to 3.4 s on main, the cost of the fourth list.
- `vitest run src/features/onboarding src/features/opportunities src/components`: all pass except `welcome-page.test.tsx` "keeps a deep link opened at first sign in and opens it at the end", which fails the same way on main.
- `python3 .github/scripts/e2e_groups.py`: OK. `python3 .github/scripts/ci_plan_test.py`: 1 failure and 3 errors, identical on origin/main (a dynamic import in `api/app/apply/profile_router.py`).
- `uvx pre-commit run --files <the 30 changed files>`: every hook passed (gitleaks included).
- `git merge-tree` with origin/main and with PR #715's branch: no conflict.

## Not run

- Playwright journeys. Two were updated for the new accessible names only (`web/e2e/browser-j15.spec.ts`: "Terms of Job titles", "New term for Places", "Terms of Places"; `web/e2e/profile-history.spec.ts`: `addTerm` on "Job titles" and "Places"); they were not run on a stack (four cores shared with three other builders). Their groups should run in the gate.
- The ingestion folder as a whole (only the registry tests above), the apply folder as a whole.

## Files changed outside the owned ones

- `api/app/ingestion/items.py`: two lines, the field type and slot of `search.location_exclude` (the registry test requires every section path to have a type; same kind as `title_exclude`).
- `api/app/ingestion/items.py` (round 1): `canonical` reads the omitted `location_exclude` as an empty list (four lines), covered by `test_a_search_saved_without_the_unwanted_places_takes_an_item_for_them_and_gives_it_back`.
- `SPEC-profile.md`, `SPEC-jobs.md`, `SPEC-opportunities.md` (round 1): one sentence each names the unwanted place terms.
- `api/app/jobs/sweep_router.py`: one line, `location_exclude` in the owner's criteria summary allowlist.
- `api/app/opportunities/sources/__init__.py`: docstring names the unwanted place terms.
- `tasks/jobs/interactive-search-626.md`: the reused criteria list names `location_exclude`.
- `web/src/features/profile/o4-fields.test.tsx`: its Advanced lookup is scoped to "What you aim for" (it took the first Advanced of the tab, which is now the lists').
- `web/src/features/profile/diff.ts` and its test: revision labels "Job titles", "Unwanted titles", "Unwanted places".
- Shared registries, one line each: `web/src/lib/wording.ts` (8 entries), `docs/decisions.md` (one bullet).

## Self review

- 1 Heuristics: no new rule; the shared matcher is reused as the spec asks. Accept and refuse table of 43 rows plus 6 order rows written before the code; residue declared below.
- 2 Shared state: no new read then write; saves go through the existing revision check (409), and the stored list is read under the profile row lock (`_keep_consent`, in `save_section` and the import). UI state: the draft's problem is cleared when the mode changes from the switch under Advanced (tested).
- 3 Invariant: "a posting whose place matches an unwanted place is never kept as new", tested on the table, a fake board, a feed and a browser source, and with the scope frozen at Start.
- 4 Sinks: the list is the owner's own data; it reaches the frozen scope and the owner's summary only, no log.
- 5 Absolute claims: "wherever the search filters" in decisions.md is backed by the board, feed and browser source tests here and by #715's tests on the merge; a comment that said "never hidden" was softened.
- 6 Caps: inherited term list caps, tested at the limit; a 100,000 character location.
- 9 Tests fail without the change: checked (30 API tests red without the filter; the reset test red without the reopen logic; 4 profile tests and 1 ingestion test red without the round 1 keep).
- 12 Reviewer probes rerun with variants: an old tab save, a sent empty list, an old export over a stored list and into an empty profile, a restored older revision (the web saves its data with the same PUT, not tested separately), an item for the unwanted places on a search saved without the key.
- 11 Merge: no conflict with main or #715.
- 13 Unicode: accents, full width letters and a no-break space in the table; the web adds terms NFC.
- 14 Every acceptance line above with its test.

## Declared open points

- Same matcher as the unwanted titles, so the same limits: no accent folding ("Pâris" is not "Paris"), no synonyms or translations ("USA", "États-Unis" are not "United States"), words as typed ("United  States" with two spaces is another text), full width letters are other characters, and a hyphen is a word edge ("Paris-Saclay" is left out by "Paris").
- The other search fields added before the interactive ones (targets, feeds) still have the exposure that `location_exclude` no longer has: an older tab that does not send them empties them. Widening `KEPT_WHEN_OMITTED` to them is a later task.
- Rollback: this release is a one way door for the search section: a search saved by it holds `location_exclude`, which the previous release refuses, so its sweeps fail SEARCH_INVALID and its profile reads that validate answer 500. Before a rollback by hand, who would break: `SELECT user_id FROM profiles WHERE sections->'search' ? 'location_exclude';` and the runs in flight: `SELECT id, user_id FROM sweeps WHERE status = 'running' AND stats->'search_scope'->'search' ? 'location_exclude';`

## Open questions for the owner

None.

https://claude.ai/code/session_017CqfhHnPbtyZ6jyK1KZS4T

🤖 Generated with [Claude Code](https://claude.com/claude-code)
