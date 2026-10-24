# Apply #604 parser and fallback deadline review fixes

Issue #563, local review findings B1 and B2. Source checkpoints:

- `917446008f23b1b1916a92407ac026363070b0c2`: named local choices, original fallback deadline,
  regressions, and prior-toast settlement before Saved answers draft screenshots.
- `313507bc0a8c6381b5d4b96dfa0d640beaa1bee0`: transparent-wrapper parser followup only.

## B1: Named local unknown choices, with wrapper recheck still open

An explicitly named local choice remains `unsupported_widget` beside a help link, an inline
heading, or an independently labelled companion input. Generic wrappers around those children
can reach the named local group within the existing three ancestor levels. The parser excludes
independently labelled sibling branches from that choice cluster. It uses tree structure,
accessible labels and the existing submit names, with no question-word filters.

Regression tables cover English, French, Subscribe and Details labels; required and optional
groups; direct and nested choices; accessible names with and without a repeated visible label;
single choices with local labels and help; and upload and yes/no peers. All earlier false page,
form and footer wrapper cases remain asserted, including the native Workable snapshot with
seven real fields, one submit ref and no fabricated footer question.

Initial RED: **86 failed, 74 passed** across the initial parser and deadline regressions.
The nested-wrapper followup separately produced **64 failed, 196 passed** before its source fix.
Final six-file focused run: **950 passed in 1.69 seconds**.

In-memory mutation controls restore actual functions from the source checkpoint without editing
the worktree, suppressing warnings or removing assertions:

- Restore the original parser function from `4be2fe3c`: **190 failed, 70 passed**.
- Restore both parser helpers from `91744600`: **64 failed, 196 passed**.
- The independent 24-case parser probe, copied and repointed to the final source, reports
  **zero regressions**, versus 24 at the reviewed source.

Structural ambiguity remains explicit: a named wrapper with one loose action and an independently
labelled input has the same tree shape as a one-option chooser with a companion input. Existing
false-wrapper assertions require the loose action absent. The positive one-option cases therefore
require a repeated local visible label and no independent input; companion-input cases use a peer
choice cluster. This is a parser boundary, not a claim that all possible custom widgets are known.

The final independent recheck passed its direct and nested local-choice tables and 71 existing
false controls, but found another named whole-form wrapper false positive: two loose actions
(`Details` and `Subscribe`) beside normal inputs and a named Office subgroup can supply a false
Application question. The peer-button condition currently overrides that independent-group
evidence. **B1 remains open for a fresh fix author**; the passing tables above do not close this
newly identified family.

## B2: Original deadline, with progress retention still under review

Preparatory reads, prior-progress refills and the form stage now share the same timeout derived
from the original deadline. The injected clock is checked before each refill and before entering
the form stage. Token and turn limits, lease, retry allowance and deadline remain unchanged.

The copied independent probe permits starts at clocks 99 and 99.5 with deadline 100, and refuses
starts at 100, 100.01 and 101. Regression cases cover preparation reads and refills, just-before,
exact and expired boundaries, timeout handling and external cancellation. Restoring only the
original `_drive_generic` gives **14 failed, 4 passed**.

The subsequent independent recheck found that early-stop results can return an empty report and
erase persisted prior answers when `finish` rebuilds them. That finding remains **open for the
fresh B2 fix author**. `run.py` is unchanged after `91744600`; these deadline results are not a
claim that the later progress-retention finding is closed.

## Verification and remaining acceptance

At frozen source `91744600`, the isolated repository wrapper ran the whole Apply and browser
suites with temporary Postgres and Redis, `caffeinate`, two workers and `-m 'not docker'`:
**10,049 passed in 385.15 seconds**. Its exit status was zero, and its own containers were removed.
This is a source checkpoint before the final nested-wrapper and native-fixture composition.
The fresh final acceptance agent owns the whole composed rerun; this report does not replace it.

Ruff app/tests, formatting of 783 files, web typecheck, web lint, and normal commit hooks pass.
The CI plan has 81 passing tests. The journey registry reports 29 CI specs, four exclusions with
reasons and 51 local-group specs. No assertions, skips, caps, model policies or gate limits changed.

The mechanical journey change waits for the previous `Reusable answer saved.` toast to disappear
before capturing a new Add answer draft. Fresh native screenshots and the exact-head complete
gate remain final acceptance work. No real environment, credential, account, ATS, mail service,
approved branch, main merge or deployment was used.
