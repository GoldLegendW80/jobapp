# Apply #604, local final-click guard closure review

Head: 97c650d83faaac49c7c3506f508eb954e81aab6c
Tree: 8490ff61b14fe29cb966f8badafaaa7a1810d910
Review checkout: `.claude/worktrees/rv604-closure-guards`, detached.
Previous narrow review: d8261e1dff6d006c7b0172ed07da4656d2f925d2.

Scope: only the two remaining blockers in `/tmp/jobapp-review604-final-guards-recheck.md`: preservation of an already host-paced Review approval through arm, and live pause/deadline checks after the final guard transaction commits. The source fix is e1ec8eff, integrated as eb078456. The other review owns new saved answers, fallback, parser and native file behavior. This report is local only, not a whole-PR approval or a release claim.

## Findings

**Local scope verdict: both previous blockers closed.** No remaining blocker found in the reviewed source, independent pure probes and focused real-DB checks. This is not a public approval.

1. **Already paced Review authority survives clearing the displayed wait.** `arm` obtains the spent authority under the existing locks, retains its effective deadline in the internal Plan, then clears `needs`. `_after_arm` requires that same spent authority id, a kind matching the current mode, unchanged application and current answer/document hash, unchanged rehearsal status and the captured deadline. It runs the mutable guards separately, avoiding recomputation from the now cleared wait. Unpaced Review keeps its own expiry; Full auto retains its exact Like deadline. The extension is bounded by the original creation time plus seven days. A late wait or a later expiry change does not renew the admitted deadline.

2. **The final live check follows the last guard commit.** After `before_click` and its transaction return, `_after_arm` awaits the live pause reader, treats an exception as paused, samples the clock and compares against that captured deadline. There is no successful-path DB transaction between this refresh and the external click. A demonstrated refusal follows the existing `click_refused=True` transition back to ready, retaining the consumed approval and committed click event. Full auto hands over to Review. Nothing on this no-click path calls the outcome recorder or freezes a sent version.

## Independent evidence

The untouched original probes were rerun against their original detached d826 tree. The paced probe still reproduces both invalid refusals of valid extended approvals. The post-commit probe still reproduces six invalid clicks, expiry/pause/pause-store error in Review and Full auto. Each exits 1 as expected. Thus the reproductions remain capable of detecting the original defects.

Current-source adaptations change only the pinned checkout and production helper dependencies introduced by the fix (`deadline`, `gate_kind`, `_checks`). All original 16 cases pass:

- `rtk proxy python3 /tmp/jobapp-guard604closure-original-pace-extension.py`
- `rtk proxy python3 /tmp/jobapp-guard604closure-original-post-guard-commit.py`

A separate independent table executes AST copies of the current actual `arm`, `_bound`, `recheck`, `_checks`, `_stop`, `_after_arm`, `full_auto.expired`, `deadline`, `gate_kind`, `hand_over`, and the actual state-transition implementation. DB queries and external services are explicit pure doubles; this does not claim native browser or SQL concurrency proof.

`rtk proxy python3 /tmp/jobapp-guard604closure-probe.py` passes **152 cases: 36 permitted clicks and 116 safe refusals**. Machine-readable output is `/tmp/jobapp-guard604closure-probe.json`. Coverage includes:

- Review's arbitrary one-hour authority and normal 24-hour authority, Full auto, and paced/unpaced variants; one microsecond before, exactly at and after ordinary and seven-day deadlines, plus later days.
- A deadline reached after the final callback returns but before its transaction returns; one microsecond later; administrator pause; Redis read failure; disabled submits; and time advancing during the final pause await.
- Changes to the spent authority id/kind/use status, mode, application or live hash, rehearsal state and missing captured deadline before the final locked recheck.
- An expiry extended by 30 days and a fresh `host_pace` set only after admission. Neither renews the captured deadline.
- Unchanged authority id/kind/hash/timestamps in unmodified controls, exactly one click audit after arming, a definite no-click transition and no outcome-record call on refusal. An assertion at the external boundary ensures the last successful-path operations are the live pause read and fresh clock, with no intervening commit.

Six in-memory broken variants are independently killed: capturing the ordinary expiry instead of the paced deadline, removing the final live pause, removing the final deadline comparison, ignoring the admitted authority id, ignoring authority kind, and removing definite no-click proof. The last mutant is rejected by the actual state-transition implementation. Mutations never modify the checkout.

`git diff --check d8261e1d 97c650d8` passes. No source edits, fixups, commits, pushes or public review were made.

## Focused runtime checkpoint

`rtk proxy caffeinate -i ops/agent-test.sh --name rv604guard-closure tests/apply/test_final_guard_commit_boundary.py tests/apply/test_a14_host_expiry.py -m 'not docker' -n 2 -q --tb=short`: **90 passed in 60.74 seconds, exit 0**.

The 72 boundary tests use the real Postgres persistence path and actual `Session.commit` interception. They cover browser and mail pacing, valid controls, pause/error/expiry/disabled changes after commit and a clock change during the live pause await; refusals retain spent authority and one click event and freeze no document. The 18 mail host-expiry neighbors use a second actual Postgres session and observe the sender blocked on the host advisory lock before advancing the injected clock.

The wrapper removed its owned `agent-rv604guard-closure-49486-pg` and `-redis` containers; an exact-prefix `docker ps -a` check returned no rows. Both were inspected while running: NanoCpus 1000000000, Memory 0 (the existing test-wrapper setting), OOMKilled false and RestartCount 0. No images or native browser services were created. The detached checkout is clean and retains the exact head and tree above.

## Limits and handoff

The earlier builder's 252-case and 8,744-case runs are contextual checkpoints, not this review's proof. This is a closure review of two blockers, not a repeat of the entire integration review. Final composed gate, native fallback/saved-answer acceptance, screenshots, other source reviews and whole-PR closure remain with the orchestrator. No production/real ATS, personal data, secret environment files, credentials, mail or owner hardware were accessed. No optional cross-model review was requested.

## Probe integrity

- `/tmp/jobapp-guard604closure-probe.py`: SHA256 `93e1b4bd472744553d92f60ea41fc19eeadb1ea0aff498d2ef638a0e99015261`.
- `/tmp/jobapp-guard604closure-probe.json`: SHA256 `583d500369d16723e5fca6f6cf67693a23e188d704a2d1ff14b2f177dd3dcf67`.
- `/tmp/jobapp-guard604closure-original-pace-extension.py`: SHA256 `58392087935068fe2613e8855322f31b93616ba8addc891e307a6d1bdd990e61`.
- `/tmp/jobapp-guard604closure-original-post-guard-commit.py`: SHA256 `9ad637f10e6082ffc47698ed9560081288552373616d1ad859587b7bed1851ed`.
