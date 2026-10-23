# Apply final guard round 2

Private checkpoint for #563 and draft PR #604, based on d8261e1d.

The consumed authority's effective deadline is captured by arm only after its exact
owner, authority, answers, documents and mutable guards pass under the gate locks.
The internal Plan carries that deadline while the displayed host pace wait is cleared.
The final guard checks the same consumed authority and all mutable guards, then commits.
After that commit returns, the driver awaits the live administrator pause, fails closed
on a read error, samples the clock and checks the captured deadline before the external
click. There is no successful-path database work between this last read and the click.
A proven stop keeps the spent approval and committed click audit, returns to a fresh
review, and freezes no document. The seven-day paced Review ceiling remains exact;
ordinary unpaced Review and Full auto deadlines retain their existing boundaries.

Mail already retains its pacing eligibility through its final post-commit checks and
has no later successful-path commit before mint/provider call. Its production code is
unchanged. The prior real-DB mail regression incorrectly identified after-commit by
lock call count, overlooking prepare's additional lock. Its phase trigger now observes
the actual application state and durable send binding; all original assertions remain.

Validation at this source checkpoint:

* Original actual-function pure reproductions are red on d826: two extended Review
  positive controls refused, and six post-commit invalid external clicks.
* Both pure reproductions pass after this fix, including all original controls.
* Three in-memory mutants are caught: losing the paced deadline, removing the final
  live pause, and removing the final deadline comparison.
* Real database red run reproduced three paced Review failures before the fix.
* The final focused command through ops/agent-test.sh with -n 2 and not docker passed
  252 cases in 132.30 seconds: all 162 prior freshness cases, 18 host-expiry cases and
  72 new browser/mail pacing and actual Session.commit boundary cases. The new
  commit tests include pause-read failure, submit disabled, clock advancement during
  the final pause read, ordinary deadlines and the seven-day extended deadline.
* The first focused run had 234 passes and six failures from the mail phase fixture
  described above. It is retained here so the corrected trigger is reviewable.
* Whole Apply/mail and the three composed ingestion/renderer contract files are still
  pending at this checkpoint. The final composed gate and independent review remain
  the root agent's responsibility.

Only invented accounts and local fakes were used. The owned Postgres/Redis test
containers each have NanoCpus 1000000000 and Memory 0 (the existing agent-test
configuration), with OOMKilled false and RestartCount 0. They were removed by the
wrapper. No app service memory setting, cap, budget, timeout, retry, renderer image,
approved source branch, production service, credential or external account changed.

## Whole-suite integration follow-up

The first whole Apply/mail plus the three composed contract files finished with 8,739
passes and five failures in 559.92 seconds. All five were in test_a14_lifecycle:
Review/Full auto cancel and company-cap variants of
`test_local_stop_after_mime_never_becomes_a_provider_refusal`, and
`test_new_approval_during_mime_supersedes_the_old_send_authority`.
Their live pause callbacks also injected database events. Repeating a pause read repeated
those events while the final locks were held: the cancel assertions failed, the cap row
was inserted again, or a second reapproval waited on those locks. The production pause
reader correctly failed closed, so the tests saw paused instead of their intended code.
The injected event now happens once. The callback still answers each subsequent live
switch read, and both tests additionally require at least two switch reads. All original
refusal, zero-mint/provider, atomic audit, one-use and superseding-authority assertions
remain. Production mail is unchanged.

The corrected lifecycle file, all 72 new boundary cases and the requested ingestion
worker/parser/renderer contract files passed together: 179 tests in 63.23 seconds, through
ops/agent-test.sh with -n 2 and not docker. The full repeat then passed all 8,744 tests in
596.84 seconds, exit 0: whole Apply/mail plus ingestion/test_engine_worker.py,
ingestion/parser/test_compose_parser.py and documents/test_compose_renderer.py. All owned
agent-guard604r2 services were removed and the builder's worktree was clean at handoff.
This is the backend guard checkpoint before the subsequent generic-runtime/fallback slices;
their regression runs and the final composed gate remain separate requirements.
