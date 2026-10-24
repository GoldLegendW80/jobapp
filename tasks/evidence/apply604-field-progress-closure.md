# Apply #604, scoped field/progress closure

Local conclusion: **B1 and B2 CLOSED at the source/pure-probe level. No remaining blocker found in the assigned findings or their fix delta.** This is not a public review verdict, whole-PR approval, native acceptance or batch-gate result.

Final reviewed head: `0070e6202cfe983ab2b6136f29e154c87c16a67c`.
Final tree: `602166af9b30a4cde220d9ac38ac4a0fcdbfffd8`.
Detached checkout: `/Users/h.hautin/Documents/Discoveries/AI/jobapp/.claude/worktrees/rv604-field-progress-closure`, clean at handoff.
Previous reviewed head: `664955ac9198661320fb52704dc4a78847cd852c`.

The first source checkpoint in this recheck was `58a1351195d67dd0a27aad0784275987c05b57ec`. The review then followed the supplied final deltas through `1c385ede`, `3676b77c` and the final head above. All final acceptance counts below were rerun against the final detached checkout. No application source or committed tests were edited by this reviewer.

## B1, independent field branches and retained unknown choices

The earlier `buttons > 1` override is gone. Named-group authority now stops at an independently named field branch or a branch with its own visible label and field-bearing descendants. Help-only link branches and gate-submit-only branches do not become independent fields. The helpers use tree/role structure and existing submit names, without question-word filters or a button-count permission.

Independent final-source generator results:

| Family | Result |
|---|---:|
| Real local named choices retained as `unsupported_widget`, direct and nested | 3,360 / 3,360 |
| Explicit landmark/form and independent labelled-branch wrapper controls clear | 495 / 495 |
| Ambiguous named groups retained as unresolved evidence | 48 / 48 |
| Original independent multi-action footer family clear | 108 / 108 |
| Native committed Workable snapshot | 7 fields, submit `e26`, 0 skipped |

The copied original parser corpus also passes at the final head: 192 direct and 192 nested local cases retained, 69 false-wrapper controls clear and the two formerly contradictory named direct-companion fixtures retained as unknown. Its original disappearing-label mutation loses all 192 direct positives while the 69 actual false controls remain clear. All three legacy probes use this detached checkout and reviewer-owned output paths.

The local family varies five names including French/Japanese and action-like labels, required/optional names, visible-label repetition, one/two/three option buttons, order, transparent nesting, links/headings/direct companion controls, and named/generic link-only or submit-only branches. The false family includes one/two/three loose actions, independent named chooser/text branches, visible-labelled upload/choice branches, heading presence, order and footer depth. The committed focused tests also pass the exact 216-case new footer matrix, including Japanese action pairs.

The semantic fixture correction is appropriate: a named local chooser plus a direct companion input is structurally isomorphic to the alleged named footer wrapper. Changing its words cannot justify opposite expectations. Those fixtures remain tested and unresolved, including heading-only named groups; explicit landmarks/forms/generic wrappers and independently labelled field branches still remain clear. The 48 independent ambiguity cases vary the group name across Application, Formulaire and Japanese while preserving structure, repeated-label presence, companion role and option count. Unknown questions are retained rather than silently omitted. No Full auto rule was weakened.

Five in-memory parser mutations were detected, with no source edits:

- Original disappearing-label function: 3,360 true cases lost.
- Previous multi-button authority function: 288 false whole-form groups created.
- Previous help-branch taxonomy: 1,440 genuine local cases lost.
- Remove independent named-branch barrier: 216 false groups created.
- Remove independent visible-label branch barrier: 216 false groups created.

Artifacts: `/tmp/jobapp-apply604-closure-parser-probe.py`, `/tmp/jobapp-apply604-closure-parser-results.json`, `/tmp/jobapp-apply604-closure-legacy-parser-probe.py`, `/tmp/jobapp-apply604-closure-legacy-parser-results.json`, `/tmp/jobapp-apply604-closure-legacy-footer-probe.py`, `/tmp/jobapp-apply604-closure-legacy-footer-results.json`.

## B2, conservative deadline/timeout reports and persistence inputs

The synchronous stop report retains closed wizard progress, applicable current outcomes, unresolved live fields, exact person/document values, skipped evidence, steps and shapes. It removes Submit designation/ref and does not need a browser/model read. Current refs are refreshed only when key, label, kind, options and required flag match. Completed changed live reads discard stale current values; closed earlier-step evidence remains. The original deadline, remaining token budget and turn limit are retained. External cancellation propagates.

The final delta separates visible field evidence from attempted outcomes. This closes a regression found during this review at `1c385ede`/`3676b77c`: inserting every unresolved live field into `outcomes` caused actual `FormSession.prefill` to skip source-resolvable fields. With prior progress it omitted the new Extra field; with no prior report it filled only the first resolvable peer. The independent probe reproduced six affected controls. At the final head all six pass, alongside the author-added live/timeout/cancellation and true-unknown regressions.

The final **108 / 108 independent cases pass**, using actual `_drive_generic`, `forms.fill`, session read/fill/Next/prefill/finish helpers and actual `_answer_rows`, with a controlled adapter/model callback and injected clock. Cases include:

- Before/exact/after expiry and exhausted tokens; read, first refill and second refill boundaries with six clock deltas. Live starts retain deadline 100, token budget 33,863 after 137 spent tokens and turn limit 30.
- Actual `asyncio.timeout` rescheduling/cancellation during preparation, model, partial progress and credential-free prefill; external `CancelledError` neighbors.
- Exact person and document values, closed-step person rows, earlier skips and wizard step metadata; partial current progress; real session Next transitions with and without a subsequent live read.
- Changed label/kind/options/required/key on successful preparation or model-stage reads, exact ref refresh, no rebinding of stale current values and preservation of earlier closed rows.
- Actual credential-free prefill with prior two answers plus Extra and fresh fallback with several resolvable peers; no-agent stop; model completion at expiry without another read/shot or Submit designation.

The copied legacy deadline matrix additionally passes **36 / 36**, with **zero empty progress returns**. Its existing mocked finish is retained only as a compatibility control; the 108-case matrix above uses the real form-session finish helpers.

Six independent in-memory progress mutations are detected:

| Regression mutation | Cases detecting it |
|---|---:|
| Restore previous empty-report `_drive_generic` | 8 |
| Remove exact-shape retention condition | 4 |
| Remove post-expiry I/O guard | 1 |
| Swallow external cancellation with timeout | 2 |
| Restore session Submit designation in stop report | 1 |
| Restore visible-evidence/attempted-outcome conflation | 6 |

The real persistence transaction was read: `run.finish` still deletes and rebuilds application-answer rows. This fix supplies the preserved rows through the report rather than bypassing that transaction. Its person-answer shape comparison now also includes `required`, preventing that transaction from restoring a value onto a changed-required field. The eight committed real-Postgres tests exercise actual `run.finish` deletion/rebuilding and this negative control; their source was reviewed. Per the source-only assignment, this reviewer did **not** start a database or independently rerun those eight cases. Root reports the author's final 710-case focused run, including all eight real-Postgres cases, passed; that remains separate author/runtime evidence.

Artifacts: `/tmp/jobapp-apply604-closure-progress-probe.py`, `/tmp/jobapp-apply604-closure-progress-results.json`, `/tmp/jobapp-apply604-closure-progress-mutants.py`, `/tmp/jobapp-apply604-closure-progress-mutants.json`, `/tmp/jobapp-apply604-closure-legacy-budget-probe.py`, `/tmp/jobapp-apply604-closure-legacy-budget-results.json`.

## Focused tests, exact source and limits

Final-source command, using the existing integration venv:

```text
rtk proxy env PYTHONPATH=<review>/api <integration>/api/.venv/bin/python -m pytest -q --no-cov -o addopts= tests/apply/test_a4_field_containers.py tests/apply/test_a8_fallback_deadline.py tests/apply/test_a8_fallback_progress.py tests/apply/test_a8_known_fallback.py -k 'not real_finish'
702 passed, 8 deselected in 0.94s
```

The eight deselections are the real database tests, intentionally outside this source/pure assignment; no test was deleted, skipped in source or weakened by this reviewer. The initial 626-pass and intermediate 630-pass pure checkpoints were superseded by this final run.

Read the complete assigned previous report/brief, root completion ledger, repository CLAUDE/reviewer/implementer/checklist, review/security skills, scoped specification and full source/test delta. The skills' optional `.agents/references/security-checklist.md` and `definition-of-done.md` were absent; repository checklists and the actual skill instructions were used.

No Docker, DB/Redis, Playwright, native browser, build, gate, network/model backend, real environment, credential, account, ATS or mail service was used. No public review/approval, push, approved-source edit, main checkout edit, merge or deployment was performed. There are no watcher/background processes or containers from this review. The detached checkout is left clean. Probe mutations existed only in individual Python processes.

Closed final-guard, Saved answers/counter and native-proof assertion findings were not reopened. Final composed whole Apply/browser tests, actual known-fallback Review/Full auto/frame/no-credential and A4/A8 acceptance, screenshots, quiet exact-tree gate and whole-PR review remain assigned to root/native acceptance. This report closes only the assigned B1/B2 findings and their examined source delta.
