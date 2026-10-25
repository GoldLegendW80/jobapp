# Posting pay reader compatibility, issue 550

Tested code: `dc3c368d9e47d02598ee4cc833c1029be7c244b1`, tree `2131e1b5be2440df4649972f68d4e135d77fc9be`.
Dependency: PR #612 at `feb7ca71b8bdf8e2c904bce907b02b36362b4d29`.

This slice prepares the rollback readers before fractional writers and storage. Major currency units stay unchanged. Response models retain exact Decimal values internally and emit bounded numeric JSON, local edit-event encoding handles Decimal, both formatters preserve cents, and the browser decoder reads cents. Saving a changed title with unchanged fractional pay omits pay from PATCH. Typed pay and all backend writers remain whole only.

## Verification

- Behavioral API RED: 17 failed, 27 passed. Focused GREEN: 44 passed.
- Behavioral web RED: 11 failed, 50 passed. After decoder and formatter fixes, only the two actual editor submit regressions failed, with 59 passed. The editor fix guards validation with payChanged.
- Full owning API jobs, opportunities and body-limit: 2,335 passed and 15 existing external-data skips, one worker, 406.25 seconds pytest, wrapper exit 0 in 421.273 seconds.
- Full owning frontend jobs and opportunities: 412 passed in 20 files, one worker. Typecheck, lint, whole API Ruff/format, CI planner 81 tests, E2E group registry and pinned pre-commit checks passed.
- Original opportunities browser group: all 36 passed on its single execution, wrapper exit 0, 67.830331 seconds, one worker, unchanged 180-second test timeout and zero retries/skips/unexpected/flaky.
- Separate final numeric-response fixture: exactly one collected and passed, wrapper exit 0, 4.409219 seconds. Its actual title PATCH was exactly title plus updated_at, without pay. A direct server GET still returned integer pay 4 and 5. Browser warning/error, pageerror and HTTP error lists after fixture setup were empty.

## Disclosed fixture and earlier failures

Only the owned posting's browser GET is intercepted to supply numeric 4.35 and 5.1. PATCH, storage and the direct server read are real. This proves browser read compatibility and omission of unchanged pay; it does not prove stored fractional persistence. The original 36 tests keep their existing error helper and expected-response allowlist.

The separate fixture ran in three configurations. First, its functional checks passed but strict errors found the expected missing model-credential resource. Six setup lines then stored a synthetic credential before listeners, with no verify or model call and every assertion retained. Second, the corrected current fixture passed, but an archived first spec was accidentally collected too, producing one pass and one failure. Third, an exact-path config and a one-test collection guard produced the final clean isolated run. Both earlier logs, JSON results, specs and capture bytes remain in private evidence; hashes are in the manifest.

The first broad API run had 2,333 passes, 15 existing skips and two failures: the unchanged scoring process-CPU check exceeded its 0.2-second budget by 0.55 ms, and a privacy assertion matched `pay` in the original worktree pathname. Moving only the owned worktree proved the privacy cause. The unchanged scoring test passed once alone in the reserved quiet window, followed by the complete passing API run alone at one worker. No assertion, budget, timeout or skip was weakened. Old rewritten test bytecode retains historical co_filename paths; actual app imports and interpreter prefixes were verified in the owned environment.

An initial frontend owning run exposed a leaked Sonner toast from the new successful-save test. Test-local toast.dismiss cleanup fixed it without changing assertions. Setup-only plugin/CLI option errors and lint corrections are retained separately in private evidence.

## Source identity and cleanup

All 3,410 previously tracked regular-file bytes and the tracked symlink target still match the tested code. API/web manifests and locks matched the read-only donor before owned dependency copies. API launchers and web entrypoints were rebased after the neutral worktree move; actual interpreter, app and dependency origins resolved inside that worktree.

The three own images carried the tested code head/tree labels. Changed Python source hashes inside API and worker images matched the source receipt, running image IDs matched the built IDs, and safe runtime settings named only fake ATS, forms and feed hosts. The complete overlay stack was migrated to head 0023. All 11 services were running and healthy with zero restarts before teardown. The image mutex was released after image identity/source receipts.

Every owned container, network, volume, source-check container, image tag, fake settings file, shim and dependency copy was removed. The borrowed renderer stayed unchanged. The source worktree and branch remain for review. No main merge, deployment, public approval or publication occurred.

## Four inspected captures

Each final image was viewed. The pay fields and Save/Cancel controls are visible in both themes and widths; the fixture also asserts zero horizontal document overflow. The long mobile title stays contained in its input.

| Width | Light | Dark |
| --- | --- | --- |
| Desktop 1280 | [Light](numeric-reader-editor-desktop-light.png) | [Dark](numeric-reader-editor-desktop-dark.png) |
| Mobile 375 | [Light](numeric-reader-editor-mobile-light.png) | [Dark](numeric-reader-editor-mobile-dark.png) |

## Remaining issue 550 work

A separate dependent slice must implement Numeric storage and its migration/rollback proof, fractional PayIn/import/stated-pay writers, parser preservation and typed decimal entry. All three owner parser families remain open: French intern indemnity/gratification, leading Monthly stipend wording, and a stated base before OTE's own figure. Profile compensation, scoring, Apply and the later full rename remain outside this slice.
