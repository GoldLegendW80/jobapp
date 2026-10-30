# Heuristic code and checkers

Read this when a trigger line sent you here: your diff adds or changes a parser, classifier,
matcher, rule set or regex over words, labels or names that people or pages write, a checker that
decides what passes, or the fix of a review finding on one. A reviewer reads it for the same
diffs. Moved on 2026-10-29 from [implementer.md](../implementer.md) (Code, "Heuristic code") and
checklist item 1 of [review-checklist.md](../review-checklist.md). The cases are in
[lessons/heuristic-rules.md](../lessons/heuristic-rules.md).

## Before you write the rule

Heuristic code (a parser, classifier, matcher, rule set, regex over user or web text): half the
items of later review rounds came from these (42 of 86, 2026-10-14 to 10-28; lessons in
[lessons/heuristic-rules.md](../lessons/heuristic-rules.md)). Before writing the rule, write the
accept and refuse table from the spec as a parametrized test: 30 to 40 neighbour cases around
every rule (near misses, other languages, spacing, case, punctuation, empty, huge) plus a corpus
of true inputs that must stay untouched. Make the default fail closed (when unsure, do the safe
thing the spec names). Prefer an allow list of what is accepted, and refuse or leave what it
does not name. Model the structure (a tree, a period, a vocabulary) instead of adding strings.
Keep it pure and linear in input size. List what it still misses under "Declared open points" in
the pull request body, so it is not re-blocked (residue only: never an acceptance line).

## Checklist item 1, the check

Heuristic rules and checkers fail closed, and a fix closes the family, not the quoted input.
(HEUR: 33 rounds, 20 PRs, half of all later round items.)

Check: before the rule, accept and refuse tables from the spec, 20+ true inputs and 20+ near
misses (negation first, another country, unit, script or language, word order, glued or spaced,
plural, a number or a name inside a longer one, another paragraph). Whatever the rule does not
recognise resolves to Left, refuse or flag: an allow list whose remainder passes is a deny list.
After a review, rerun each quoted input with its variants, keep them all in one regression table,
and after three rounds on one class change the model.
