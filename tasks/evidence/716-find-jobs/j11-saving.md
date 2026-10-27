Jobs J11 and J11b: save the postings a job website search verified, as they are reported
Depends on #715
Closes #632
Closes #655
Refs #638

## Why

A job website search (jobs J10, mode `llm_browser`) verified postings on their detail pages, then ended with "Postings were read on this website, but saving them comes with a later update." Now each verified batch is saved to the person's list as the finder reports it, and the postings show in Jobs like a board's. This branch stacks on #715 (one native search per title and place pair) and merges it, so both behaviours hold together.

Branch: `jobs/632-save-verified-findings`. It has the previous builder's three commits (a0433c3d J11, 693f255d J11b, e28deb69 J15), then 44089556 (J11b recovery and second cancellation) and 04b1dd85 (merge of `origin/jobs/multi-search-per-site`).

## What was built, per acceptance line

### #632

| Line | Done in | Test |
|---|---|---|
| (1) Each verified batch commits through the existing jobs storage before progress announces it; a later error keeps the earlier jobs | `sweep.commit_verified`: one transaction under the locked sweep row writes the jobs (`_new_job`, the board path's row) and the row's counters; `Commits` publishes the counters to the run only after it | `test_a_failed_batch_stores_nothing_of_itself_and_keeps_the_batches_before`, `test_an_agent_failure_after_a_batch_keeps_what_was_stored`, `test_the_registered_report_tool_stores_its_finding_through_the_run_commits` (the real RuntimeFinder's registered tool, real database) |
| (2) Opened detail provenance, employer evidence, unknown fields and observed facts; no host as employer, no today as missing date | the finder's validation (foundation, on main) plus `_store_finding`: company `Employer not stated` when the page names none, `published_on` only when observed, `event.data.discovery` holds `source_id`, `detail_read_at`, the page lines read as evidence and the list of unknown fields; `legacy` stays `{}` | `test_verified_findings_become_scored_found_jobs_with_their_observed_facts_only` (also asserts the planted model words and instructions markers appear in no job, event or progress) |
| (3) Dedup and rule counts, user isolation, never apply including cybersecurity gives skipped records, no Like, apply, contact or account write | duplicates by address or employer, title and location (an unknown employer is never an identity, only its address); `CYBER_RULE` added to the person's rules; conflicts looked up among the person's own rows only | `test_duplicates_by_address_or_identity_name_the_website_and_keep_the_row` (Bob's row with the same address untouched), `test_same_title_with_unknown_employers_is_two_postings_and_a_second_run_finds_duplicates`, `test_never_apply_rules_and_the_cybersecurity_rule_skip_with_their_note`, `test_nothing_but_found_or_skipped_jobs_is_written` (no Application, no Authorisation) |

### #655

| Line | Done in | Test |
|---|---|---|
| 1. Commit before any count is published; earlier jobs kept; provenance only in the sweep event; unknown stays unknown; dedup and never apply | as #632 above | as above |
| 2. Stats and Known staged on copies, published after the commit; a same address row stored after claim resolves to the person's actual winner, its content, status and notes kept | `Known.copy`, `Stats` snapshot; the insert's conflict reads the person's rows by address or slug and decides again | `test_a_row_stored_meanwhile_by_another_request_is_the_duplicate_it_is` |
| 3. One run owned serialized commit lifecycle: a cancelled caller cannot overlap the next source or publish after the end | `Commits`: a batch is admitted only while its source runs, runs as a task of its own under the run's lock (shared with progress writes), shielded from its caller; `settle` closes admission and waits up to `DRAIN_S` (5 s, injected in tests) before the next source and before the end; a commit still running leaves the row running with its committed counts for `reap_stale`; a failed commit is never retried and `recover_verified` reads the locked row and the person's list again (a lost COMMIT answer); a second cancellation abandons publication and the end | `test_a_commit_whose_tool_call_is_cancelled_finishes_before_the_next_source`, `test_a_commit_that_outlives_the_wait_leaves_the_row_to_recovery_with_its_counts`, `test_a_row_ended_by_another_finalizer_first_refuses_the_batch` (reaper first), `test_a_finalizer_waiting_on_a_held_commit_ends_the_row_with_its_counts` (storage first), `test_a_commit_whose_answer_was_lost_is_read_again_never_stored_twice`, `test_a_second_cancellation_during_the_drain_leaves_the_row_to_recovery`, `test_a_progress_write_and_a_commit_never_overlap`, `test_a_batch_after_its_source_settled_starts_no_commit`, `test_cancelling_the_sweep_mid_source_keeps_the_stored_jobs_and_their_counts` |
| 4. Scope, permission callbacks, pacing, limits, stopped sweep ownership unchanged; no fallback to fixed readers; no extra capability | `run_selected` only adds `admit` and `settle` around each website; the legacy `store_found` is never called for a website | `stored == []` in the registered tool test; every B14 and #715 test unchanged and passing |

Every test that holds a database thread releases it in `finally`.

### Stacked on #715

Each of a website's searches sends its verified batches to the run's commits as they come, through `_Site.verified`: a posting an earlier search of the same website already gave is not stored again (merged once per canonical address), an unwanted title or place and a posting of another contract are counted (`unwanted`, `other_contract`) and never stored. Admission spans the website's searches; its commits settle before the next website. A refusal or an error in a later search keeps what earlier searches saved. Test: `test_the_searches_of_a_website_store_each_posting_once_and_a_refusal_keeps_them` (two searches, a posting given by both stored once, an unwanted title dropped, a browserd refusal in the second search, outcome partial, counts agree with the jobs).

### #638, the saved and listed part only

`web/e2e/browser-j15.spec.ts` now expects the job website's posting saved (found 1, new 1, still `unsupported_interaction` for the refused Send application click) and listed in Jobs with the employer its page names and the website as its source; the fixture page states its employer and location. The rest of #638 (all fake platforms, the complete journey with watch, handoff and cancel) is not in this pull request, hence `Refs`.

## Person facing text

- `unsupported_interaction`: "The website asked for something the app does not do, so the app refused it. Postings saved before are kept."
- `store_failed`: "What this site found could not be saved, so it stopped there. Postings saved before are kept." (was "so nothing was kept from it", untrue once a website's earlier batches are saved)
- `agent_failed`: "The assistant stopped before the end. Postings saved before are kept." (was "Nothing was kept from it", already untrue for B9 careers pages, which store what code kept)
- `finding_persistence_pending` (only sweeps that ran before this change, and callers without the run's commits): "Postings were read on this website, but saving them came with a later update."

## Tests changed

- `api/tests/jobs/test_interactive_findings.py`: its profile now excludes the title "account executive" instead of the helper's default "account executive" and "sales", because #715 drops an unwanted title before the store and this file's sales posting tests the never apply rule. The exclusion of a title is tested by the new two search test.
- `web/src/features/jobs/find-jobs.test.tsx`: the `finding_persistence_pending` sentence.
- `api/tests/browser/test_multi_search.py` and `test_selected_interactive.py` keep `finding_persistence_pending`: they run without the run's commits (not a sweep), where findings are still only counted.

## What I ran

- `ops/agent-test.sh tests/browser tests/jobs -m "not docker" -n 2`: 4727 passed, 15 skipped.
- `ops/agent-test.sh tests/opportunities -m "not docker" -n 2` (every sweep's progress now goes through `Commits`): 1692 passed.
- Focused: `test_interactive_findings.py`, `test_selected_interactive.py`, `test_multi_search.py`: 109 passed.
- Tests can fail: without `_recover` and without `abandon`, the lost answer test and the second cancellation test fail; without the cross search address check, the two search test fails (found 4, not 3).
- `uv run ruff check app tests`, `uv run ruff format --check app tests`: clean.
- `pnpm --dir web run typecheck`, `lint`: clean. `vitest run src/features/jobs`: 427 passed.
- `python3 .github/scripts/e2e_groups.py`: pass.
- `python3 .github/scripts/ci_plan_test.py`: FAILED (1 failure, 3 errors: "a dynamic import in api/app/apply/profile_router.py"). The same result on a clean checkout of origin/main 64fe8b08; this branch does not touch that file or `.github/scripts`.
- `uvx pre-commit run --files <changed files>`: all hooks pass (gitleaks included).
- `git merge-tree --write-tree origin/main HEAD`: no conflict.

## Not run

- The journey `web/e2e/browser-j15.spec.ts` and the browser group: no stack could be built in the sandbox. Every image pull fails: `Error response from daemon: unknown: failed to resolve reference "docker.io/library/python:3.12.15-slim-trixie": unexpected status from HEAD request to https://registry-1.docker.io/v2/library/python/manifests/3.12.15-slim-trixie: 429 Too Many Requests`. The spec typechecks; it needs a run with the browser group before merge.
- Docker marked tests: none in the touched test files.

## Files outside the owned ones

- `web/src/features/jobs/labels.ts` (three sentences above), `web/src/features/jobs/find-jobs.test.tsx` (one string), `web/e2e/browser-j15.spec.ts` and `tests/browser_pages/j15/jobs/robot-engineer.html` (J15's saved and listed assertions).
- Through the merge of #715: its own files (see #715).

## Self review

- 1 Heuristic: no new rule; the after reading filters are #715's, applied before the store unchanged.
- 2 Shared state: the row is locked (`FOR UPDATE`) in `commit_verified` and `recover_verified`; commits and progress writes share one lock; both finalizer orders, progress overlap and repeated cancellation have tests.
- 3 Invariant: "the row's counters are those of the jobs this sweep stored" is `assert_counts_agree`, used after every lifecycle test, including after `reap_stale`.
- 4 Sinks: the planted model words and instructions markers appear in no job, event or progress; logs carry the sweep id only.
- 5 "never" sentences: "never retried" (`calls == [1]`), "stored once" (two search test), "nothing published after the run let go" (`commits.stats.new == 0`).
- 9 Tests can fail: the three RED checks above.
- 11 Merge: clean against origin/main.
- 14 Acceptance: the tables above.

## Declared open points

- The held commit probes call `FinderSession.report_verified`, the function the registered `report_postings` tool awaits; the registered tool with the real RuntimeFinder and database is tested on the normal path, not while abandoned by the SDK.
- `recover_verified` takes the larger of the row's and the run's counters: correct because each counter only grows during a run and every write carries the run's own counts. If the reading fails too, the run lets go of the row (no progress, no end) and `reap_stale` ends it with the counts stored with its jobs.
- An unknown contract on a posting is read by the engine's `kind` as CDI when several contracts are wanted (#715's declared point), so such a posting can be dropped as `other_contract` before the store.
- Pay and work mode of a website posting use the existing rules over the text read; no test asserts them separately.

## Open questions for the owner

None.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_017CqfhHnPbtyZ6jyK1KZS4T
