# Apply 604, third full gate

Status: COMPLETE, FAIL, exit 1 after 5154 seconds. Cleanup completed; gate locks and own containers are gone. This is a retained failure record, not final acceptance.

Checked head: `57c17cc030cb4421224f5cce0a03d282cab5bac1`. Tree: `61a4162bfaa5b1b1d1632771fafaeb46077b736d`. Base: `8d9c8c68d1459cfc4262c6e7ebcb1467263d05dc`. Started 2026-10-23 03:58:56 UTC. Prefix: `lc1023055856-15137`.

The machine was reserved for the gate. All nine native journey groups ran. Apply used a pre-proved static partition: 82 cases in 21 nonempty whole-file shards, 61 empty shards, no case fragmentation or new skips. Original case budgets, strict browser-console checks, browser 1024 MiB cap and 90 percent memory guard remained.

## Completed checks

API: 28,107 passed, 57 existing personal-source skips, 1,569.37 seconds. No API retry. Frontend: 2,748 passed in 117 files, 111.54 seconds. All eight non-Apply groups passed. Nineteen of 21 nonempty Apply files passed, including all six known-fallback cases, all five A15 cases and the A17 two-user flow. The gate-environment harness regression passed in the real gate.

## Apply failures retained

A18 part 44 failed both its original and fresh-stack attempts: Playwright checked Replace, the server accepted the PUT, but synchronous controlled-input restoration briefly restored the old value before deferred query notification. The first case failed all four native attempts across two stacks. The fresh-stack attempt also exposed premature live-view ticket requests while the owned profile-edit lease was still starting. Autosave and Workday functional assertions passed, but the strict console check caught the 403; each passed its internal retry. No errors were ignored.

Review UX part 75 failed both original and fresh-stack attempts: its row locator required an exact closed-state Edit button, which becomes Stop editing when the editor opens. Actual retained DOM showed the intended textarea with maxlength 10000. Three later serial cases did not run, so their assertions and screenshots are still required.

A16 passed only after one internal retry: latest-document presence released setup while rendering was unfinished; the subsequent versioned posting address PATCH correctly returned 409 after a renderer write. The proposed repair covers the 17 finished-document fixture predicates in 17 files, preserving one original 150000 ms readiness window. It does not retry 409 or weaken version guards.

Ingestion G10 passed its serial retry after the first duplicate-start assertion received estimate_changed rather than active_run. This remains explicit flaky evidence and is outside the Apply fixture patch.

## Docker and final phases

Docker: 2366 passed, two failed, eight existing no-photo-slot skips in 1317.70 seconds. Both failures occur before behavior assertions when lease creation correctly returns 409 uid_in_use: test_b7_container.py::test_save_page_as_is_off_in_a_persons_session (uid 20026), and test_live_ownership_container.py::test_live_takeover_owns_native_navigation_and_tab_mutations (uid 20000). Read-only diagnosis proves earlier tests permanently bind both reused UIDs to different profile UUIDs in their same module stacks. Ending the lease correctly preserves ownership. The proposed repair assigns unused UIDs 20027 and 20001 to the later fixtures, preserving every guard/assertion.

Standalone timing PASS 23 seconds; scoring PASS 3 seconds. Coverage FAIL: 51090 statements, 2306 missing, 15066 branches, 1251 partial, reported 94 percent against unchanged required 95. The ephemeral coverage database was already removed at gate cleanup; a fresh full backend measurement must retain per-file/JSON/database evidence. The future supervisor additionally retains coverage files, without changing checks or decisions. No phase retry or waiver closes these failures.

## Retained evidence and next work

Root log: `/tmp/jobapp-apply604-gate-3/gate.log`. Detailed logs: `.local-check/logs/lc1023055856-15137/`. Native artifacts: `/tmp/jobapp-apply604-gate-3/artifacts/`. Docker pytest fixtures remain under the sibling scratch pytest directory outside gate-worktree cleanup.

Root visually inspected all 20 emitted A15 images, all 12 emitted A16 images and all eight emitted A17 images. A16 emits review, held and checkpoint-review quartets; A17 emits owner-review and other-user-denied quartets. No additional recovery or Applied captures are claimed. Review UX editor/render-wait captures are pending because their cases did not run.

Two authors prepared changes only under their own /tmp directories while the gate ran. They have not changed source or Git or run checks. After gate completion and cleanup, current main 1244322c was composed into isolated commit 0b668519. Its focused merge checks pass: 20 backend cases, 143 frontend cases, typecheck, Ruff, CI planner, registry and all applicable hooks. This merge leaves Apply source/tests unchanged. That exact commit is the fix authors' released base. New source requires scoped independent closure, original native whole-file acceptance and a complete quiet final exact-tree gate. PR 604 remains a draft. Approved source branches are untouched. No deployment or main merge is authorized here.

Sustained browser memory remains open as issue 605. Functional per-file isolation does not establish long-lived memory or physical Jetson capacity. Real-site owner checkpoints and credentials remain separate from local synthetic acceptance.
