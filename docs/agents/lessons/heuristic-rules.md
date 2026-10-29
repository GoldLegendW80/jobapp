# Heuristic rules and checkers that fail open (HEUR)

Cue: a rule decides from words, labels or names that people or pages write (form answers, fact
checks of a CV or a mail, negation in a posting, topic gates for the answer agent, button names,
scoring words, folding of saved terms), or a checker passes what it does not recognise.

Counts, corpus 2026-10-14 to 10-28: 33 rounds, 20 pull requests, 82 items, 42 of the 86 items of
later rounds. Open branch: 1 (B17). Rank 1. Backs review-checklist.md item 1 and implementer.md,
"Heuristic code".

## Edit rows

component: review-checklist.md item 1 and its "Heuristic code" section
candidates: the checklist of 2026-10-14
change: fail closed default, accept and refuse tables, 20+ near misses per rule
observed: the share of later round items fell from 79% (61 pull requests) to 49% (42 of 86), yet
heuristic pull requests still took three or four rounds: #687 (4), #259 (3), #312 (3)
verdict: confounded (the tasks moved from answers and fact checks to browser and gate work)

component: review-checklist.md items 12 and 16 (rerun the quoted probes with variants, one
regression table) and the fix brief of orchestration.md ("fix the family, not the quoted input")
candidates: the checklist of 2026-10-14, orchestration.md
change: after a review, probe the neighbours of each quoted input
observed: fixes kept closing the quoted input only: #177 round 2 ("closed for the quoted strings
but everyday forms of the same two leaks still pass"), #613 ("#575 item 3's failure still passes
both checks; only its spelling changed"), #687 rounds 2 to 4, #312 rounds 2 and 3, #661 round 2
verdict: no observed effect

component: implementer.md, "Heuristic code" ("Prefer an allow list")
candidates: implementer.md since 2026-10-14
change: an allow list of what is accepted rather than a deny list
observed: allow lists were written and still failed open, because what they did not name was
accepted: #661 round 2 ("the credential rule is the wall's whole label allow list, so it fails
open"), #720 ("the rule for the other names of a decline button fails open, against the PR's own
claim"), #477 (bonus allow lists and glued hyphen neighbours)
verdict: no observed effect

## Entry HEUR-1

- **cue**: a rule over human or page text, or a fix round of one.
- **lesson**: in review-checklist.md item 1, the unknown side of every rule refuses, flags or
  leaves the field (an allow list whose remainder is accepted is a deny list); before each push,
  generate the neighbours of every quoted input on the axes below and add them to one regression
  table; after three rounds on one class, change the model instead of the strings.
- **evidence**: #260, 5 rounds and 27 items ("Do you NOT require sponsorship?" answered No,
  "Europe" read as the EU, month options read as years); #259, 3 rounds ("with an MSc" deletes a
  true degree, "Top 1%" passes); #177, 2 rounds ("the B1 fix now reports the job title as an
  employer"); #312, 3 rounds ("Closed topics phrased with an open opener still reach the agent");
  #592 ("opens the topic gate on main's fail closed side"); #589 (a level word added for one band
  changes the person's level; "Baccalauréat" is a bachelor's degree in Quebec); #719 ("stripping
  every combining mark lets through values that are not a saved term"); #438 (location filtered by
  substring, title and place read by position).
- **confidence**: high: 20 pull requests, the same shape in every round.
- **supports**: review-checklist.md item 1, which merges items 1, 12 and 16 of 2026-10-14.

## Entry HEUR-2

- **cue**: a keyword rule meets a sentence that may negate, scope or redirect it.
- **lesson**: decide the scope first (the clause, the sentence, the paragraph, the whole page) and
  treat a negation, a condition or a second question in that scope as Left; test each rule with
  its negated, inverted and two clause forms (review-checklist.md item 1, axis "negation").
- **evidence**: #260 round 1 ("Negated and inverted sponsorship phrasings get the opposite
  answer"), round 2 ("Two questions in one label are answered as one"), round 3 ("A sentence answer
  matched to an option it contains, even when the sentence negates it"); #394 (`since` or `depuis`
  read as ongoing work, "including a negated employment statement"); #687 rounds 1 to 4 ("Negation
  is read as an instruction", then scoped to one sentence, then a prohibition in another paragraph
  never read).
- **confidence**: medium to high: 3 pull requests, 8 rounds.
- **supports**: review-checklist.md item 1 (negation named first among the axes).

## What made heuristic pull requests converge (moved from the checklist of 2026-10-14)

In the order it helped, on #260, #259, #177, #179 and #97:

1. **Flip the default to fail closed.** #260 round 6 approved once an option list counted as a
   time span only when nothing was left after removing numbers and the allowed words.
2. **Model the thing, not the surface strings.** #179 round 4 approved once the scan tracked the
   elements the way the tree builder does; #259 round 4 once dates were anchored to the stored
   period of the one organisation the sentence names, with `as_of` passed in.
3. **Write the rule as examples from the spec, in both directions, and generate neighbours.** #97
   and #177 closed with accept and refuse tables and a corpus of 40 true replies with 0 false
   positives. Include true inputs written the other way (`12,3456` for `12.3456`).
4. **Keep it pure, linear and regex light.** Timings at 50k, 100k and 200k characters that double
   when the size doubles.
5. **State the residue.** A declared open point with an example stops being re-blocked, unless an
   ordinary input reaches it (#286: "the declared open point is reachable").
6. **Count both error rates in the body:** true inputs untouched, N of N; false inputs caught, M
   of M.

Axes for near misses: negation and inversion, another country, unit, script or language, other
word order, glued or spaced, singular or plural, a number inside a longer number, a name inside a
longer name, case and diacritics, another paragraph.
