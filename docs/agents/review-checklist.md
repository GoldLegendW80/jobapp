# Review checklist

The pre pull request self check for implementers and the minimum for reviewers
([implementer.md](implementer.md), [reviewer.md](reviewer.md)). Each item is one imperative plus
a way to check it. Run every item that applies to your diff before you ask for review, and name
each one you applied under "Self review" in the pull request body.

Ranked by the REQUEST CHANGES rounds each family caused on the 242 reviewed pull requests merged
from 2026-10-14 to 2026-10-28 (151 rounds, 322 blocking items; the list of 2026-10-14 came from
61). The code after each title names the family's file in [lessons/INDEX.md](lessons/INDEX.md),
with its cases: open it when the item applies. Never blocked in either corpus, keep doing them:
no dashes in user text, migrations expand only with a working downgrade, ruff and format clean
(once broken by a lint config main gained, #302), no secrets in the diff, no real personal data in
fixtures. No block since 2026-10-14, moved to their lessons files: the shell traps of the old item
8, the external facts of the old item 15.

1. **Heuristic rules and checkers fail closed, and a fix closes the family, not the quoted
   input.** (HEUR: 33 rounds, 20 PRs, half of all later round items.)
   Check: before the rule, accept and refuse tables from the spec, 20+ true inputs and 20+ near
   misses (negation first, another country, unit, script or language, word order, glued or
   spaced, plural, a number or a name inside a longer one, another paragraph). Whatever the rule
   does not recognise resolves to Left, refuse or flag: an allow list whose remainder passes is a
   deny list. After a review, rerun each quoted input with its variants, keep them all in one
   regression table, and after three rounds on one class change the model.

2. **Trace every sink of personal data, secrets and model input, and every way page code can pass
   a guard.** (TRUST: 22 rounds, 22 PRs.)
   Check: a random canary through each sink the diff adds: the JSON log with its `extra` fields,
   access log headers (`Referer`, `Location`), the public Actions log, every table after a delete,
   the model's context (only the fields its task needs). Composite keys (id, user_id) for a
   reference to another user's row. A new secret file joins the deny list in the same pull
   request. Browser guards: try a service worker, WebRTC, a sandboxed iframe, a redirect and a
   steered model in a real Chromium.

3. **Merge `origin/main` last, then rerun the typecheck and the whole module on the merged tree.**
   (MERGE: 15 rounds, 14 PRs, 10 of them in later rounds.)
   Check: `git merge-tree --write-tree origin/main HEAD` prints no CONFLICT, and a clean merge is
   not a green one (#427, #551, #642). Line 1 `Depends on` names every open branch the head
   contains.

4. **Walk every unhappy path to the state it leaves.** (FAIL: 14 rounds, 13 PRs.)
   Check: for each state the change writes, its exit on a cancel, a timeout, a dead worker, a
   raise in `finish` or `stop`, a full queue, a retry and the person's step in between, one test
   each; nothing stays `running`, `filling` or owned for good; a check that guards an act runs
   before it on every path; a failure is classed by what the page showed.

5. **Gate and ops tools: bound every external call, widen when unsure, survive a kill.** (OPS: 13
   rounds, 9 PRs.)
   Check: a `timeout` on every `gh`, `git` and `docker` call, and a watchdog test with a real
   minimum; what the scope mapper cannot read (dynamic imports, deletions, parse errors, routes
   called over HTTP) widens the run; a gate killed without its trap leaves no stack, slot or lock;
   `shellcheck` and the script's `tests/*_test.sh`.

6. **Bound every untrusted input, and let one bad item fail alone.** (HOST: 12 rounds, 9 PRs.)
   Check: one hostile fixture per parser at the largest accepted size (nested tags, 16,000 quotes
   in a header, `&#` and 4,301 digits, `NaN`, 1,000 nested `minmax(`), counted by work in the real
   container; no CPU bound parse on the event loop; a per user share of shared slots; one poisoned
   posting, comment or item beside a good one, and the loop goes on.

7. **Read addresses, HTML and CSS the way their consumer does.** (DIFF: 12 rounds, 10 PRs.)
   Check: hosts after WHATWG decoding (percent encoding, IPv4 forms, case, IDNA 2008), a
   `Location` resolved as Chromium does, every spelling of one platform's host; CSS tokenised as
   CSS reads it and the tree the browser builds; normalise (NFKC) before escaping, never after.

8. **Deliver every acceptance line, named failure path and owner decision, with its evidence.**
   (ACC: 11 rounds, 11 PRs, and 5 of the 13 items on the open branch of 2026-10-27.)
   Check: each line, each failure path the plan or spec names and each decision posted on the
   issue or the pull request is in the body with its test; a declared open point is for a
   heuristic's residue, never an acceptance line; the spec's words and named functions verbatim;
   `Closes` only when all of it is delivered, else `Refs`.

9. **A new field or a changed contract reaches every reader.** (DRIFT: 11 rounds, 11 PRs.)
   Check: a save, an import or a restore that omits the new key keeps the stored value (a test
   with an old payload); the field is in `FIELD_TYPES` and the schemas where it belongs; grep the
   callers in `api/app`, `browser/` and `web/src` and run their modules' tests; a slice stands
   alone.

10. **Make fakes and samples as hard as the real thing.** (FAKE: 11 rounds, 9 PRs.)
    Check: the fake checked once against the real service, tool or version (legacy iptables, a
    real PDF, the real renderer); content as people write it (a long unbreakable string, a long
    name, two pages, an emoji with U+FE0F); a widget that ignores the click; a stream split at
    every position; the real status codes.

11. **Shared state: lock, read again under the lock, compare with what the person saw, then
    write.** (RACE: 10 rounds, 10 PRs.)
    Check: `git diff | grep -nE "FOR UPDATE|advisory|lock_|create_task|to_thread|refresh"`; a two
    session test per read then write path (a click and the tick, two workers); one lock order; a
    fix that moves a read reopens it.

12. **Every claim in text or in a recorded outcome has its test next to it.** (CLAIM: 10 rounds,
    10 PRs.)
    Check: `git diff | grep -nEi "never|always|every|no .* can|within [0-9]+|cannot|guarantee"`
    in comments, docstrings, docs, plan, the body and UI text: name the proving test or soften. A
    label or an outcome ("Accept all", "searched", "read") counts only what happened. Then
    `grep -rn` the old value of anything you changed.

13. **Bind every click, consent and submission to what the person saw and approved.** (APPR: 9
    rounds, 8 PRs.)
    Check: the approval carries what the page showed (answers, text version, figures) and the act
    checks it again at the act and after its time limit, 409 on a mismatch; one Like gives one
    click; a check before submit fails closed on what it did not see; a destructive button asks.

14. **Run every journey of the group and the module's suite on the head; say what did not run.**
    (RUN: 8 rounds, 8 PRs.)
    Check: implementer.md, Tests. When the gate's lock, the disk or the sandbox stops a run, write
    "not run" and why in the body, so that the reviewer runs it.

15. **UI state: show the last successful read and the saved value, and act on the item shown.**
    (UI: 8 rounds, 8 PRs.)
    Check: a failed read never counts as fresh (`isFetchedAfterMount` is true after an error); a
    poll, a toast or a swipe acts on its own item; a summary reads what was saved; unsaved typing
    survives a refetch; reset flags wherever the draft is replaced.

16. **A test must be able to fail, and must not depend on the machine.** (BLIND: 8 rounds, 8 PRs.)
    Check: remove each layer of the fix alone and watch the test go red; assert on what the system
    writes (the JSON log with `extra`, every route class); an absence is proved only when the
    fixture tries the forbidden act; no wall time bounds.

17. **Plan and spec PRs: dependencies, one owner per shared file, one run per task, sources.**
    (PLAN: 6 rounds, 5 PRs, 3.6 items each.)
    Check: per task "depends on", "owns" and "touches", compared across parallel tasks; migration
    numbers chain on main's head; contracts cited with the right task id and the real signature;
    split past about 5 deliverables; each decision row cites the owner's words.

18. **A person's decision survives every later run; time rules take an injected clock.** (DEC: 3
    rounds, 3 PRs.)
    Check: a rejected, removed, replaced or picked value comes back flagged or not at all after a
    rerun; pass `now` and `as_of` in; test at the limit, one second after, after a Resume, an edit
    or a replace.
