# Final round on claude/serene-gates-xxs89r (base 4b547a0a)

Worktree: .claude/worktrees/final, branch fix/final, head 53706c359e19380e7ff5fbd321ab00891dae2b3f (6 commits on 4b547a0a, not pushed). Diff: 30 files, +1072 / -69. Tests are about 60% of that.

## A. J18 plan section
- tasks/jobs/todo.md: appended the "### J18: one agent per job website, with all the saved criteria" section after J17. The text is exactly the one in verdict-j18.md "Fixups for the fix agent".

## B. Free-Work keeps no page cap (owner, 2026-10-27)
- SPEC-opportunities.md decision 12: "human pacing and small page caps" is now "human pacing". Added: J18 (owner, 2026-10-27, "no limit") replaced the small page caps of this decision. Such a website (Free-Work among them) has no page cap of its own and is bounded by its pacing, one sweep a day, the 7 day pause and its share of the sweep's time. LinkedIn keeps its own caps (5 result pages a sweep, one sweep a day, SPEC-browser decision 2).
- docs/decisions.md: a new bullet right after the J18 bullet, "No page cap for Free-Work and the other opt in websites (owner, 2026-10-27, jobs J18)", saying the same and naming platform_policy.py.
- Checked against api/app/browser/platform_policy.py: `PAGES = {"linkedin": ...}` only, and one sweep a day applies to every platform.

## C. Shared detector: mixed case spellings (verdict-b17-r3 follow up 3)
- browser/browserd/detect.py:
  - `words(text, *, camel=True)`.
  - New `match_words(text)`: the camel split, plus the words of the text with its case folded first, added only when the split cut a word. So a text written in one case reads exactly as before.
  - `_glued` (says_submit, says_danger, used by every lease, apply included) reads through it.
- browser/browserd/tools/discovery.py: read_url's and destructive's path tokens and `_applies` (query keys and values) read through `match_words`. ends_session already joined the words, so it is unchanged. own_words and the own words skip are unchanged too.
- Measured before the change, now refused: `SubMit`, `sUbmit`, `AppLy`, `ApPly` buttons; `https://x.test/jobs/42/AppLy`, `/EasyAppLy`, `?action=AppLy`, `?op=SubMitApplication`. browserd stays strict and only gains refusals.
- Tests, generated from the rule. New helper api/tests/browser/case_support.py builds the case spellings of a word: lower, upper, title, swapped, the two alternating cases, each letter flipped, and 20 seeded random ones.
  - Apply side, the click detector (test_b5_unit.py):
    - SUBMIT_NAMES × spellings are marked submit.
    - DANGER_NAMES × spellings are refused danger.
    - The profile's Save and Update × spellings are submit for profile_edit.
    - The reviewer's 4 rows plus controls.
    - TRUE_BUTTONS in lower, upper, title and capitalised case stay ordinary, and match_words equals words for them.
    - The one name whose lower case was already not refused is listed: CAMEL_ONLY = ContacterLeRecruteur.
  - Discovery side (test_b17_browserd.py):
    - ACTION_SPELLINGS plus 13 sign out, delete, pay, save and update words × spellings × path places (/jobs/42/{}, /{}, /{}.html, /api/v2/{}/42) and a query place. Each is destructive and refused by signed_request, document or not.
    - 16 reads stay allowed in one case.
    - The reviewer's 7 URL rows.
    - The 4 glued spellings the path rule already missed in lower case are listed (PATH_GLUED) and checked in the query.
- Mutation (match_words returning the old split): 154 tests red.
- Declared (detect.py, discovery.py, B17 plan): a glued verb and noun written in one case is one word. So `/jobs/42/confirmapplication` leaves the path rule (the query rule reads it), and a `contacterlerecruteur` button is ordinary. Both behaved the same before this change.

## D. The leftover percent cost (verdict-b17-r3 follow up 2)
- Declared in tasks/browser/todo.md (B17 open points) and in the discovery.py module docstring's "Declared open points": a subresource whose path still holds a `%` after one decode (`/static/img/100%25.png`, `/api/search/50%25-remote`) is refused; a document with a `%` in its path already was.

## E. Journey fixes (verified by the runner on 4b547a0a)
- web/e2e/browser-b4-helpers.ts: the compose call gets `maxBuffer: 64 * 1024 * 1024`. Prettier puts one key per line.
- web/e2e/browser-b7-helpers.ts: DEBUGGING skips a missing /proc/net/tcp or tcp6 table.
- web/e2e/browser-j15.spec.ts: the wait for "Reading <site host>" is now 300_000. A comment gives the reason: her website comes after the four job websites, about 245 s measured.
- Prettier clean. No journey was run (the orchestrator reruns the browser group).

## F. A model's slip no longer ends the website (verdict-j18 follow up 2)
- api/app/browser/finder.py:
  - `box_of(role, name)` tells a text entry apart from the snapshot. It returns "title" when the accessible name holds a title word and no place word, "place" the other way, and None for both, neither, or no text entry. Words are NFKC, case folded and accent stripped. "où" is read with its accent. A hyphenated word is also read glued.
  - `FinderSession.boxes` is rebuilt on every page read.
  - `FinderSession._slip`:
    - browser_type: a text this box does not take. A title box takes saved titles or "". A place box takes saved places or "". A box it cannot tell takes any saved title or place, as before.
    - browser_select_option: not exactly one value that is a saved place or a saved filter value. This check is exact: every value browserd could accept passes it.
  - A slip answers TITLE_BOX_REFUSED, PLACE_BOX_REFUSED, TYPE_REFUSED or CHOICE_REFUSED as a tool error and counts `refused["model_value"]`. It reads the website's time the way Pages._act does (time_budget, lease_budget), so a model that only slips stops with its share.
- api/app/browser/sources/interactive.py `_search`: `model_value` is no longer a reason for `unsupported_interaction`, the same as `duplicate`. A refusal of browserd or of the website still is. browserd is unchanged.
- Fail closed: an unknown box or ref falls back to today's union check, and browserd judges the field's own name.
- Tests in the new api/tests/browser/test_j18_slips.py. The fake form judges by field name the way browserd does.
  - 17 slips, each followed by good searches: both searches run and both postings are kept. The error is finding_persistence_pending, never unsupported_interaction. refused is {model_value: 1}. The slip never reached the browser.
  - All slips together, on the start page and on a result page, are counted 34 times.
  - Repeated slips with the injected clock end `time_budget` after 55 slips at a 600 s share, and only the first navigation reached the browser. Slips past the lease's time end `lease_budget`.
  - 5 refusals of browserd or the website (an unnamed box, Salary, an unknown ref, the Apply now click, a select on a non select) still end `unsupported_interaction` after good searches.
  - A slip and a website refusal are counted apart.
  - A run of slips with no search is still unsupported.
  - Boxes are read from the page last read.
  - box_of tables: 29 title names, 27 place names and 26 names it cannot tell, each × 3 roles. Other roles and non text names. Every word of both lists alone, and every title word × place word pair, which gives None. A huge name.
- Mutations: dropping the per field box gives 13 red. Dropping the time check gives 2 red (the bounded loop fails instead of hanging). Dropping the model_value exclusion gives 18 red.
- Updated, on purpose:
  - test_multi_search: the "Query" box is a title box. One test types Paris into the box that cannot be told apart.
  - test_b13_runtime hostile test: the off scope filter `any` is now refused by the api. tools.calls is only the click. model_value is 2.
  - b13_runtime.Recording reads STEPS when it is built. The monkeypatch in 3 tests of test_selected_interactive never reached it, so their model chose `1` where the saved remote stance is `remote`, which real browserd would refuse. Those tests now build Recording after the monkeypatch.
- Declared (finder.py): a name in another language is not told apart. A site that names its search box with a place word gets the right value refused at the api, which is safe and costs only that website. Refs are read on the page last read.
- Docs: SPEC-browser J18 bullet, interactive-search-626.md, the interactive.py docstring.

## G. More saved values than the scope carries (verdict-j18 follow up 4)
- interactive.py `_carried`: the first titles and places that fit, in saved order, a title then a place in turn. A value fits if it has at most 300 characters, its list holds fewer than 100 values, and the scope as sent stays within 32 KiB with the signed in flag counted. The rest is counted in `Searches.titles_left_out` and `places_left_out`, then in entry `titles_left_out` and `places_left_out` (only when above 0).
- When no title fits, the website is still `unsupported_criteria` before a lease.
- Summary:
  - api: SourceOut gets `titles_left_out` and `places_left_out`, read defensively by sweep_router.
  - web: sweeps-api schema, plus `leftOutSentence` in labels.ts, rendered by sweep-sources.tsx between the searches and the counts. Example: "20 saved titles and 1 saved place were left out: a website's search takes the first ones that fit." No dashes.
- Tests in test_multi_search.py, which replace the old "unsupported before a lease" table:
  - 150 titles and 120 places run with the first 100 of each, left out (50, 20).
  - 101 titles: 1 left out.
  - Over 32 KiB of accented values: a prefix of each list, balanced, still within 32 KiB when signed in.
  - A value over 300 characters is left out and the next ones are carried.
  - No title that fits stays unsupported_criteria.
  - 40 seeded generated lists are checked against the rule: saved order, bounds, and that no value left out could have been added.
  - A scope that fits says nothing.
  - The router reads the counts defensively.
- Web tests: labels.test.ts (leftOutSentence) and the find-jobs summary row.
- Mutation (`_carried` returning the lists whole): 37 red.
- Declared: the agent's context (InteractiveScope.criteria, 100,000 characters, every saved criterion) is a separate, older bound. A person whose criteria exceed it still gets unsupported_criteria.
- Docs: SPEC-browser, SPEC-jobs, interactive-search-626.md.

## Tests run (all through ops/agent-test.sh, -m "not docker", -n 2, one folder at a time)
- tests/browser: 4610 passed.
- tests/jobs: 1293 passed, 15 skipped.
- tests/opportunities: 1704 passed and 2 failed (test_o8_summary pinned the source's key set). Pins updated, file rerun: 19 passed.
- tests/apply (whole folder, 106 files): 9920 passed.
- tests/agents: 1396 passed, 19 skipped.
- ruff check and ruff format --check on app, tests and ../browser: clean (881 files).
- pnpm --dir web run typecheck: clean. lint: clean.
- vitest src/features/jobs --maxWorkers=2: 17 files, 503 passed.
- e2e_groups.py: OK. ci_plan_test.py: 1 failure and 3 errors, the same as origin/main (known).
- uvx pre-commit run --files on the 30 changed files, gitleaks included: passed.
- git merge-tree --write-tree origin/main HEAD: no conflict.
- Not run: images, journeys, Docker marked tests (as asked).
