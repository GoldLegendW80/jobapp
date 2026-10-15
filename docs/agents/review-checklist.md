# Review checklist

The pre pull request self check for implementers and the minimum for reviewers
([implementer.md](implementer.md), [reviewer.md](reviewer.md)). Each item is one imperative plus
a way to check it.

Built on 2026-10-14 from 56 REQUEST CHANGES reviews on 40 of 61 pull requests (152 blocking
findings), summarised under "Where the numbers come from" at the end. Run this list before you
ask for review.

Things reviewers looked for and found clean every time (keep doing them, they never blocked): no
dashes in user text, migrations expand only with a working downgrade, ruff and format clean, no
secrets in the diff, no real personal data in fixtures.

## The 5 items that caused the most review rounds

1. **Heuristic rules: probe the neighbours of every case, and make the default fail closed.** (66 findings, 16 review rounds, 7 PRs; 79% of all findings in rounds 2 and later.)
   How to check: for each rule write two tables before pushing, 20+ true inputs that must pass untouched and 20+ near misses that must be refused or flagged. Near misses come from these axes: negation and inversion, another country, unit, script or language, other word order, glued versus spaced, singular versus plural, a number inside a longer number, a name inside a longer name. Anything the rule does not recognise must resolve to Left, refuse or flag, never to accept. See the "Heuristic code" section below.

2. **Shared state: lock, re-read under the lock, compare with what the user saw, then write.** (11 findings, 11 PRs, 11 review rounds.)
   How to check: `git diff | grep -nE "FOR UPDATE|advisory|lock_|create_task|to_thread"` and look at every read-then-write path. Write a two session test (two tabs, two workers) for each. For a click that spends or deletes, send the figure the page showed and answer 409 on mismatch. For shutdown, assert no job starts after the stop began and that engine disposal waits for threads. For UI state, reset flags wherever the draft is replaced (`setDraft("")`, examples, errors).

3. **State the invariant of the business rule as a property, and test it with an injected clock.** (10 findings, 8 PRs.)
   How to check: write the sentence ("the key built is the fixed point of `parse_key`", "no step restarts on a smaller budget than the one that cut it", "a rejected value never returns, in any of its forms") as a property or table test. Pass `now` and `as_of` in, never read the clock inside the rule. Test the boundary: at the deadline, one second after, after a Resume, after an edit, after a replace.

4. **Trace every sink of a secret, personal datum or foreign user id.** (9 findings, 8 PRs.)
   How to check: grep the diff for each sink: logs and the failure path of logging itself (a record that cannot be encoded), proxy and access log headers (`Referer`, `Location`, query strings), public pages (`grep -rn "@" web/src/features/public`), stored payloads and dumps after a delete, cross-user foreign keys. Test with a canary string and assert it appears nowhere (stdout, stderr, log files, every table). Any new reference to another user's row gets a composite key (id, user_id). Network egress from a sandbox: test with a real listener.

5. **Every sentence that says "always", "never", "every", "no X can" or "within N days" needs the test or measurement next to it.** (13 findings, 6 PRs, 7 review rounds; 10 of the 13 were pure text fixes.)
   How to check: `git diff | grep -nEi "never|always|every|no .* can|within [0-9]+|cannot|guarantee"` in code comments, docstrings, README, plan, PR body, UI text. For each hit put the proving test name in the PR body, or soften the sentence. Then grep the whole repo for the old value of anything you changed (old status code, old number, old name in plan and spec: `grep -rn "415\|old string" tasks SPEC* README*`). Dates and clocks: say which clock ("duration_s", not SDK ms) and which day.

## Other items, ordered by frequency

6. **Give every input a size cap and a time and memory budget, and measure the worst shape.** (12 findings, 5 PRs: parsers, sandbox, mail HTML, uploads.)
   How to check: one hostile fixture per parser at the largest accepted size (500 kB of `<li><dd>`, 5,000 EXIF tags, 5,000 progressive scans, 30 headers of 16,000 quotes), assert under 1 s and under the memory limit in the real container. Look for quadratic loops (`any(... for ... in stack)` per token). Check that the cap of the route matches the schema maximum and the form `maxLength` (a 1 MiB body class against a 1,000,000 character field). Shared slots need a per-user bound.

7. **Plan and spec PRs: list dependencies, one owner per shared file, and keep each task to one run.** (16 findings, 3 plan PRs.)
   How to check: for every task write "depends on", "owns", "touches" and compare the touched files across tasks that run in parallel (`grep -n "api/app/.*\.py" tasks/*/todo.md | sort`). Migration numbers chain on the head of main. Cite contracts with the right task id and the real signature (`grep -n "def freeze" ...`). Split any task with more than about 5 deliverables. Say what state each undefined word ("stuck", "sending", "per user lock") means.

8. **Shell scripts: test `set -e` in the way the function is called, and avoid `grep -q` in a pipe under pipefail.** (8 findings, 4 PRs, plus link and fsync cases.)
   How to check: `shellcheck`; call each step as the lanes do (`step || true`, `if step`) with a stub whose first command fails; fake commands that print hundreds of lines after the match; path variants `dir`, `dir/`, `dir/.`, a symlinked dir; `sync` the new file and its folder before deleting anything; a partial or stubbed run must not print the same last line as a full pass.

9. **A test must be able to fail, and must not depend on the machine.** (5 findings, 5 PRs.)
   How to check: revert the fix and watch the new test go red; delete assertions about wall time under 1 s; run the file twice side by side in the foreground of one command (never a background CPU burner, see implementer.md). New settings or env names go into `ENV_NAMES` in `api/tests/conftest.py` (or derive it from `Settings.model_fields`); verify with `DOCUMENTS_X=0 ops/agent-test.sh tests/test_config.py`. A "killed at the cap" test must assert the kill, not just the answer. Pin text that is tied to a version with a hash test.

10. **Make fakes as hostile as the real service.** (seen in 171, 106, 112, 156.)
    How to check: the fake Gmail pads base64 like Google; the fake model answers a tool it does not have; the stub returns the real status (401, not 405). Test a stream parser at every split position: `for i in range(len(data)): feed(data[:i], data[i:])`.

11. **Rebase on `main` and check the merge before you ask.** (2 findings, 2 PRs, always a one line fix.)
    How to check: `git fetch && git merge-tree --write-tree origin/main HEAD` prints no CONFLICT; PR state is not `CONFLICTING`.

12. **After a fix, re-run the reviewer's probes with variants.** (fixes reopened in 97, 101, 177, 260.)
    How to check: for each quoted input add the same input with a trailing slash, a different script or digits, plural, French, upper case, a neighbouring country or unit. The first fix usually closes the quoted case and leaves its family.

13. **Unicode: decide fold, normalise and strip per field, and test the lookalikes.** (97, 179, 259, 265.)
    How to check: casefold merges `ß` and `ss`; NFKC turns full width `＜` back into `<` after escaping; IDNA 2003 maps `straße` to `strasse`; some format characters have glyphs; emoji with U+FE0F; Arabic-Indic digits in `\d`. Test email, URL host, name and date fields with these, and run NFKC before escaping, never after.

14. **Deliver every line of the acceptance list, with evidence.** (169, 178, 119.)
    How to check: copy each acceptance line into the PR body with the test or screenshot that shows it. A button that acts on a subset says so in its label ("Accept 3 of 7 shown"). A panel that shows stored events shows what is stored (sizes when content is private), not an empty box.

15. **Verify external facts at the source and date them.** (112.)
    How to check: fetch the live page, write the figures as literals in the test, set the `AS_OF` constant to that day. Do not assert only an ordering (`0 < cache_read < input`).

16. **Keep a regression table of every quoted input across rounds.** (260 `ROUND6`, 177, 97.)
    How to check: a single parametrised table in the test file, one row per reviewer input with the expected outcome, never deleted.

## Heuristic code (parsers and classifiers that took 2 to 5 change rounds)

PRs (REQUEST CHANGES rounds, findings): #260 answer table (5 rounds, 27 findings), #259 fact checks (3 rounds, 15), #177 mail fact check (2 rounds, 11), #97 citation checker (2 rounds, 9), #179 mail HTML depth scan (3 rounds, 7, mostly unbounded cost), #265 render characters (2 rounds, 2).

What went wrong every time: each round the author fixed the inputs the reviewer quoted, and the reviewer then found the next neighbours of the same family (work authorisation for another country, then negation, then "Europe"; salary, then past pay, then current pay in other wording; a year in a phone number written with dots, then with hyphens). Round 2 and later held 47 findings, 37 of them in heuristics. Findings come in two directions and both block: a false claim survives (invented degree, moved start date) and a true sentence is deleted (MSc written as Master of Science, "8 years" for 8.5, "then Software Engineer").

What made them converge, in the order it helped:

1. **Flip the default to fail closed.** #260 round 6 approved after the rule became: an option list is a time span only if, once numbers and the allowed words (units, bounds, level words) are removed, nothing is left; otherwise the whole list is Left. Before that, round 5 was still finding currency and head count lists read as years. A rule that names what it accepts needs no list of what to refuse.
2. **Model the thing, not the surface strings.** #179 round 4 approved after the scan tracked where the special elements stand (the way the tree builder does) instead of adding one more tag name per round. #259 round 4 approved after dates were anchored to the stored period of the one organisation named in the sentence, with `as_of` passed in as data. Three rounds of patterns for the same class is the signal to change the model.
3. **Write the rule as examples from the spec, in both directions, and generate neighbours.** #97 and #177 closed with accept and refuse tables per rule, 30 to 40 new everyday forms in the reviewer's final probe, and 0 false positives on a corpus of 40 true replies. Include true inputs written the other way (`12,3456` for `12.3456`).
4. **Keep it pure, linear and regex light.** #177 round 3: no new regular expressions, index and slice code over tokens, timings at 50k, 100k and 200k characters that double when the size doubles. #179 round 4: 400 random tag units plus 36 hand shapes, nothing over 1.6 s; ordinary mails (newsletters, Outlook, Gmail quotes) still whole.
5. **State the residue instead of hiding it.** Reviewers accepted "declared open point" and "stated cost" with an example (`9123-4567` read as a year range, `3 Mio users`). Write them in the module docstring and the plan; an open point that is named stops being re-blocked.
6. **Count both error rates in the PR body.** Report "true inputs untouched: N of N" next to "false inputs caught: M of M". #259 spent rounds 2 and 3 largely on true sentences being deleted.

Quick start for a new heuristic: write the spec table (accept, refuse), pick the default (refuse or Left), write the model (periods, scopes, spans), then fuzz 200 neighbours per rule with an independent generator and fix the family, not the input.

## Where the numbers come from

Source: 130 review files on 61 pull requests (2026-10-08 to 2026-10-14). Every REQUEST CHANGES
review (56) and the blocking sections of every APPROVE review (74) were read.

| Category | Findings | Rounds | Example pull requests |
|---|---|---|---|
| Heuristic rule or parser misses a case (false pass or false removal) | 66 | 16 | #260 (27 findings over 5 rounds), #259, #177, #97 |
| Plan or spec defect: missing dependency, file without owner, task too big | 16 | 7 | #144, #189, #191 |
| A written claim is not true: docs, plan, pull request body, UI text | 13 | 7 | #102, #98, #121 |
| Hostile or oversized input without a cap or a measured worst case | 12 | 7 | #179, #157 |
| Race, lock order, shutdown ordering, stale state in a click | 11 | 11 | #172, #266, #276, #160 |
| Business logic or state error | 10 | 8 | #148, #165, #170, #109 |
| Secret, personal data or trust boundary leak | 9 | 8 | #153, #185, #188, #267 |
| Ops script correctness (`set -e`, pipefail with `grep -q`, symlinks, fsync) | 8 | 6 | #93, #101, #121 |
| The test cannot see the bug, or is flaky | 5 | 5 | #162, #259, #156 |
| Merge conflict with main | 2 | 2 | #148, #156 |

- 66% of pull requests had at least one REQUEST CHANGES round; 21 of 61 had none.
- Round 2 and later held 47 findings, 37 of them (79%) heuristic. Everything else is mostly fixed
  in one round.
- 18% of findings were mechanical (a one line fix a reviewer can push), 82% needed judgement. So
  the gain is in the heuristic and shared state classes, not in lint or imports, which were
  already clean.
- Pull requests above 3000 changed lines averaged 1.32 REQUEST CHANGES rounds, against 0.66 below.
