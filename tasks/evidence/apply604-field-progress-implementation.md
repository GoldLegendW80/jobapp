# Apply #604 remaining field ownership and fallback progress fixes

Final source: `d0e72ebae0e9a47bb394729a3d36c8f251509148`.
Branch: `apply/563-field-progress-preservation`.
Worktree: `<checkout>/.claude/worktrees/codex-apply-preservation`.
Base: `664955ac9198661320fb52704dc4a78847cd852c`.

Root already integrated `44bd6e51a921680f0680b12b3c3973bdcac10073`. Remaining commits, in order:

1. `35c9543ed16a9bade7a8fe3355aacf162c175ddb`, reconcile live reads, refuse expired work, preserve strict person field shape including required, real PostgreSQL transaction tests.
2. `31d4c01161fac87547d47e3ce7a88ca840582e0b`, link-only help branches are not independent field branches.
3. `d0e72ebae0e9a47bb394729a3d36c8f251509148`, separate visible field evidence from attempted outcomes so actual no-credential prefill attempts all new resolvable peers.

## B1 ownership

Named local groups keep unsupported choices through transparent wrappers and help, including nested link-only help. Independently named groups with field controls and visibly labelled control-container branches establish ownership boundaries that loose footer actions cannot cross. Submit buttons and help links do not establish independent field ownership. Choice count has no authority over those boundaries.

The 216 strong wrapper cases vary three languages, three action label pairs, footer depth 0/1/2, heading presence, named Office versus labelled upload branch, and branch order. Genuine companion-control and help cases remain unresolved rather than disappearing. The committed native Workable fixture remains asserted as seven fields, submit e26 and zero skipped fields.

Declared ambiguity: a named group with only directly named companion controls and loose buttons can be a local question or a whole form. These snapshots have no structural signal to distinguish the cases after label renaming. They retain unsupported unknown evidence, preventing automatic acceptance. Four old assertions were corrected without deleting or skipping their tests: named Application with a direct textbox, combobox or checkbox, and named Application with only heading plus action. Generic/landmark wrappers and explicit independent field branches remain refused. A paired isomorphic single-chooser table asserts the conservative policy under arbitrary labels.

## B2 progress

Controlled fallback exits build a synchronous report from the last established evidence. No recovery read is started after expiry. Prior answers, exact person values, document text, earlier wizard pages, steps, skips and known shapes survive; designation is empty and persisted answers are unapproved. A completed live read replaces the current page by exact key/label/kind/options/required matches, refreshing refs and leaving changed or new questions unresolved. Closed pages retain their original record. Failed reads retain the earlier shape binding instead of attaching a failed new shape to old answers.

Visible fields and attempted outcomes are separate. The conservative report can include unresolved fields while actual FormSession.prefill still attempts fresh source-resolvable identity, document and profile peers. True unknowns remain Left. External CancelledError propagates. The original wall deadline, token and turn allowances are unchanged.

The existing finish transaction still replaces application rows from the report. Its person-answer shape check now also compares required, preventing a prior person value from being rebound to a changed required field. This was demonstrated RED then GREEN on actual PostgreSQL.

## Verification

Final composed focused run:

`rtk proxy caffeinate -i ops/agent-test.sh --name pres604-final-active tests/apply/test_a8_fallback_progress.py tests/apply/test_a4_field_containers.py tests/apply/test_a8_fallback_deadline.py tests/apply/test_a8_known_fallback.py -m 'not docker' -n 2 --no-cov`

Result: **710 passed in 5.57 seconds**, including eight actual PostgreSQL finish/persistence tests.

Final whole API Ruff check: PASS. Whole API format check: PASS, 784 files formatted. CI plan: PASS, 81 tests. E2E registry: PASS, 29 CI specs, four justified exclusions, 52 local-only specs. All applicable pre-commit hooks, including hardcoded-secret detection, passed. Diff check passed.

Mutation scripts change Python functions in memory only: old 664955 `_drive_generic` causes all 18 expiry/persisted-row regressions to fail; old 664955 `_named_choices` causes all 216 strong-wrapper regressions to fail; old 31d4 fallback session/progress helpers cause six actual no-credential regressions to fail, while two external-cancellation controls pass. Script: `/tmp/pres604-mutation-probe.py`.

The original 36-case budget probe was run at the earlier checkpoint and returned zero empty progress reports. That script wrote its fixed shared JSON output path. Root subsequently restored the original review artifact by rerunning the unmodified probe against frozen 664955, reproducing the original 20 empty-report cases. The historical review artifact is not final-source evidence; final-source evidence here is the final 710-test run and fresh independent closure assigned by root.

## Scope and remaining work

Application source changed only `api/app/apply/ats/base.py` and `api/app/apply/run.py`. Tests changed only `test_a4_field_containers.py` and added `test_a8_fallback_progress.py`. No public verdict, PR, push, merge, deployment, real environment, credential, account, ATS or mail action occurred. No images were created. Own pres604 container query is empty, and all commands/sessions finished. Worktree is clean.

This is a focused implementation handoff. Fresh closure of B1/B2 and final composed whole-Apply/browser/native acceptance remain assigned by root. The eight database tests execute the actual finish replacement transaction. Model execution is represented by an in-process callback, with actual form_agent.fill and FormSession.prefill; no real model or credential service was used.
