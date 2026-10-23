# Apply #604 parser and fallback budget, independent local recheck

Local result: **REQUEST CHANGES**. Two findings remain in the reviewed fix delta. This is a scoped local result, not a public PR verdict or whole-Apply approval.

Final reviewed head: `664955ac9198661320fb52704dc4a78847cd852c`.
Final tree: `d92548bedac15b385bfcddb6d10522a77d876a32`.
Detached review checkout: `/Users/h.hautin/Documents/Discoveries/AI/jobapp/.claude/worktrees/rv604-parser-budget-recheck`, clean at handoff.

The initial checkpoint was `9980001238cf6e14ed9c87372a1c236c8ebf5836`, tree `5ea457801d9f8e15be175957b4b792a64eeb0195`. Root then supplied the final composed checkpoint above, including B1 followup `313507bc0a8c6381b5d4b96dfa0d640beaa1bee0`. Final probes and the focused test run were repeated at that checkpoint. No application source was edited by this reviewer.

## Blockers

### P1, B2 preservation: expiry now stops the model but discards prior answers and wizard progress

`api/app/apply/run.py:1082`, `:1100`, `:1103` return a `Result` without a report on clock expiry or timeout. Its default report is empty. The actual persistence path deletes existing application answers at `run.py:789` and rebuilds from that empty report at `:794`. The existing person-answer preservation loop can only preserve answers that occur in rebuilt rows, so it cannot preserve a person's row here either.

Reproduction using the actual `_drive_generic`, actual `form_agent.fill`, and actual `_answer_rows`:

- Original deadline is 100, initial clock 99. Prior report contains two filled answers, including one with source `person`. The current page has the same field keys and shapes.
- A preparatory read advances the injected clock to exactly 100. Model callback is correctly never called, but the result is `needs_person/form_changed`, `report.outcomes=[]`, and `_answer_rows(...)=[]`.
- Moving the clock at a prior-progress refill instead produces the same empty result, even after the matching answer was refilled successfully.
- A wizard variant starts on step 2 with a closed step-1 person answer, a current-step answer, one earlier skipped field and `steps=['Next']`. Live controls retain all these. Expiry during the read or current-step refill loses all outcomes, skips and step metadata. This includes progress from an earlier page that cannot be recovered by rereading the current page.
- An actual `asyncio.timeout` cancels suspended preparation in the probe and also reaches this empty-report result. External `CancelledError` propagates, rather than being swallowed.

The blocking data-loss conclusion combines actual returned rows with inspection of the real delete/rebuild transaction. No fake database or real database was used, so this is not claimed as a database integration test.

Required remedy: every fallback stop must return a conservative report that retains safe prior/closed progress and person answers, including wizard metadata needed to explain or resume it, without a submit designation or approval allowance surviving expiry. Merge only progress that the live preparation has established as applicable, retain unresolved evidence, and do not start extra browser/model work after expiry to recover it. Preserve the original deadline and remaining token/turn budget. Add regression tests for exact/after expiry and actual preparation cancellation, including an earlier completed step and a person answer. Recheck the persistence result, not only state and cost.

### P2, B1 boundary: two footer actions turn a named whole-form wrapper into a fake field

`api/app/apply/ats/base.py:427` accepts `buttons > 1` even when `_named_choices` has identified independently labelled fields. The authority is then used at `base.py:364` to cross a structural boundary. The fix therefore fabricates a whole-form question when that wrapper contains two loose footer actions.

Minimal snapshot:

```text
- group "Application":
  - textbox "First name" [ref=e1]
  - textbox "Email" [ref=e2]
  - group "Office":
    - button "Paris" [ref=e3]
  - button "Details" [ref=e8]
  - button "Subscribe" [ref=e9]
  - button "Submit application" [ref=e10]
```

Actual final parser: First name and Email text fields, skipped `Office/unsupported_widget` **and spurious `Application/unsupported_widget`**, submit `e10`. Installing only the previous reviewed `_group_label` in memory leaves the real fields and Office skip intact, removes the fake Application skip, and preserves submit `e10`.

The same regression occurs in **108/108** independently generated neighbors: three wrapper names including French and Japanese, three action-label pairs, zero/one/two generic wrappers around the footer, presence/absence of a page heading, and either an independently named Office group or an independently labelled Resume/upload branch. The prior reviewed function fabricates zero whole-form fields on those 108 inputs. No lexical interpretation of Application, Details or Subscribe is needed for the failure.

Required remedy: peer-button count must not override independent field-group boundaries. Distinguish local choice peers from multiple unrelated loose actions within a whole-form group. Preserve the real local choice family with a help link, heading or companion textbox. Do not solve this with word filters or by discarding unresolved local controls. Retain native footer assertions and add this multi-action whole-form family beside them.

## Status of the original two blockers

- **B1 original disappearance is fixed at the final checkpoint.** All 192 direct named-group neighbors and all 192 nested equivalents retain the real unsupported local choice. The known nested-wrapper case was treated as already identified by the builder, not rediscovered as a separate finding. Its final followup was verified. The new whole-form false-field family above prevents closure of the parser boundary as a whole.
- **B2 original stale timeout/model-start bug is fixed at the final checkpoint.** Preparation is inside one original timeout, and no model callback starts at or after the original deadline in the independent probes. The new empty-progress exit above prevents closure of B2.

## Independent probes, controls and mutations

All probe scripts and detailed outputs are in `/tmp`, outside application source:

- `/tmp/jobapp-apply604-independent-parser-probe.py` and `...-parser-results.json`: **384/384** real named local choices preserved across required/optional labels, repeated/absent visible labels, help before/after choices, links/headings/companion textboxes and direct/nested peers. **71/71** existing false-wrapper controls remain clear. Native committed Workable snapshot remains **7 fields, submit e26, zero skipped fields**. Installing the original buggy `_group_label` only in memory kills all **192 direct positive cases** while all 71 false-wrapper controls stay green. The final nested family also passes 192/192.
- `/tmp/jobapp-apply604-independent-footer-probe.py` and `...-footer-results.json`: **108/108 new false whole-form fields**, versus zero with the previous reviewed function. This probe asserts the observed failure, not a passing acceptance claim.
- `/tmp/jobapp-apply604-independent-budget-probe.py` and `...-budget-results.json`: **36 cases** inspect actual run/helper behavior. Read, first refill and second refill use clock deltas 0, 0.5, 0.999999, 1, 1.000001 and 2 against deadline 100. Live cases keep deadline 100, token budget 33863 after 137 spent tokens and turn budget 30. Exact/after expiry cases start no model, and expiry after the first refill starts no second refill. The timeout is installed once with the original remaining second. Actual suspended-await timeout cancellation and external cancellation are exercised at read, both refills, model callback and simulated no-credential preparation using the actual `FormSession.prefill`. Two exhausted-token controls start no preparation or model. Six wizard cases establish the earlier-step preservation regression. **20 early-stop results are empty**, including the token exhaustion controls; the finding specifically includes newly added expiry branches and newly bounded preparation.
- The deadline probe's `mutate` mode installs only the old `_drive_generic` in memory: **12 violations detected**, nine expiry model starts and three unbounded-preparation cases. **Nine live controls stay green**. Mutation processes do not persist source changes.

Focused pure tests at the final checkpoint:

```text
rtk proxy env PYTHONPATH=<review>/api <integration-api-venv>/bin/python -m pytest -q --no-cov -o addopts= tests/apply/test_a4_field_containers.py tests/apply/test_a8_fallback_deadline.py tests/apply/test_a8_known_fallback.py
364 passed in 0.61s
```

These green tests do not cover the two findings above. An earlier 300-test run completed while the reviewer advanced the worktree checkpoint; it is superseded by the final frozen 364-test run and is not used as final-head evidence.

## Scope and limits

Read the full assigned brief, previous source review, repo CLAUDE/reviewer/implementer/checklist, review/security skill instructions, relevant Apply contract lines and scoped source/tests. This is the later-round pass over B1/B2 and their fix delta. It does not reopen the already closed final guard findings, bank/counter review or the separately assigned native/journey proof delta.

No Docker, DB/Redis, Playwright, native browser, images, build, gate, network service, model backend, real environment, credential, account, real ATS or mail access was used. The runtime model/credential path was represented by a callback; the no-credential branch used a callback and actual prefill, not the database-backed RuntimeFormAgent. Finish was inspected, and its real row-building helper was executed; no database commit was attempted. Existing lease acquisition/active budgets were inspected as unchanged, not experimentally re-proven. Browser cleanup and whole-Apply acceptance remain assigned to root's separate final-source acceptance agent.

No public review, approval, push, main checkout edit, PR change, merge or deploy was performed. No watcher or background process remains. No application source, test or branch commit was changed. Root must assign a fresh fix author and an independent recheck for the findings before final composed acceptance and any public whole-PR verdict.
