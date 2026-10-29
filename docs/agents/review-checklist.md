# Review checklist

The pre pull request self check for implementers and the minimum for reviewers
([implementer.md](implementer.md), [reviewer.md](reviewer.md)). Run every item that applies to
your diff before you ask for review, and name each one you applied under "Self review" in the
pull request body. An item is the rule; its check is in the `docs/agents/when/` file named after
it: read that file when the item applies to your diff (items 3, 8 and 12 apply to every diff).

Ranked by the REQUEST CHANGES rounds each family caused on the 242 reviewed pull requests merged
from 2026-10-14 to 2026-10-28 (151 rounds, 322 blocking items). The code after a title is the
family's file in [lessons/INDEX.md](lessons/INDEX.md), with its cases. Never blocked, keep doing
them: no dashes in user text, migrations expand only with a working downgrade
(`when/migrations.md` when a revision changes), ruff and format clean (once broken by a lint
config main gained, #302), no secrets in the diff, no real personal data in fixtures.

1. **Heuristic rules and checkers fail closed, and a fix closes the family, not the quoted input.**
   (HEUR) `when/heuristic-code.md`.
2. **Trace every sink of personal data, secrets and model input, and every way page code can pass a
   guard.** (TRUST) `when/untrusted-input.md`.
3. **Merge `origin/main` last, then rerun the typecheck and the whole module on the merged tree.**
   (MERGE) `when/every-pull-request.md`.
4. **Walk every unhappy path to the state it leaves.** (FAIL) `when/state-and-approval.md`.
5. **Gate and ops tools: bound every external call, widen when unsure, survive a kill.** (OPS)
   `when/gate-and-ship.md` and `when/shell-scripts.md`.
6. **Bound every untrusted input, and let one bad item fail alone.** (HOST)
   `when/untrusted-input.md`.
7. **Read addresses, HTML and CSS the way their consumer does.** (DIFF) `when/untrusted-input.md`.
8. **Deliver every acceptance line, named failure path and owner decision, with its evidence.**
   (ACC) `when/every-pull-request.md`.
9. **A new field or a changed contract reaches every reader.** (DRIFT)
   `when/contracts-and-plans.md`.
10. **Make fakes and samples as hard as the real thing.** (FAKE) `when/tests-and-fakes.md`.
11. **Shared state: lock, read again under the lock, compare with what the person saw, then write.**
    (RACE) `when/state-and-approval.md`.
12. **Every claim in text or in a recorded outcome has its test next to it.** (CLAIM)
    `when/every-pull-request.md`.
13. **Bind every click, consent and submission to what the person saw and approved.** (APPR)
    `when/state-and-approval.md`.
14. **Run every journey of the group and the module's suite on the head; say what did not run.**
    (RUN) `when/journeys.md`.
15. **UI state: show the last successful read and the saved value, and act on the item shown.** (UI)
    `when/web-ui.md`.
16. **A test must be able to fail, and must not depend on the machine.** (BLIND)
    `when/tests-and-fakes.md`.
17. **Plan and spec PRs: dependencies, one owner per shared file, one run per task, sources.**
    (PLAN) `when/contracts-and-plans.md`.
18. **A person's decision survives every later run; time rules take an injected clock.** (DEC)
    `when/state-and-approval.md`.
