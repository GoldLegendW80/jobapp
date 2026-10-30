# The person's approval and the submit gate (APPR)

Cue: a click, a consent or a submission is made for the person: the apply gate, Full auto, a
Like, a consent box, a destructive button, the model check before a submit.

Counts, corpus 2026-10-14 to 10-28: 9 rounds, 8 pull requests, 15 items, 2 in later rounds.
Rank 13. Backs review-checklist.md item 13 (CLAUDE.md hard rule 4 is the rule; this is how it
leaked).

## Edit rows

component: review-checklist.md item 2 ("For a click that spends or deletes, send the figure the
page showed and answer 409 on mismatch")
candidates: the checklist of 2026-10-14
change: bind a spending click to what was shown
observed: the approval paths of apply blocked on the same binding: a click not bound to the
decision (#457, #315), a consent not bound to its text (#169, #459), a check that fails open
(#470), a field never recorded on a submitted page (#661)
verdict: no observed effect (the sentence sat inside the race item; no item named approval)

## Entry APPR-1

- **cue**: the code acts on the person's approval, Like or consent at a later time than the
  person gave it.
- **lesson**: the approval carries what the page showed (answers, text version, figures), the act
  checks it again at the moment of the act and after its time limit, answering 409 on a mismatch;
  one Like gives one click; a check before submit fails closed on what it did not see
  (review-checklist.md item 13).
- **evidence**: #457 ("one Like gives a second click with no approval"; "the click is not bound to
  the decision `prepare` read, so it can submit answers the person did not approve"); #315
  ("Nothing is checked again between the decision and the click except the state and the kill
  switches"); #169 ("A new consent text arrives under a box that is already ticked, and one more
  click records the consent to it"); #459 ("Consent can be recorded without the person's accept";
  "Apply runs LinkedIn work with no guard"); #470 ("this path fails open": only the first 20 free
  texts are sent to the check); #661 round 3 (a field the app never records can sit on a page that
  is submitted); #316 ("Destructive buttons act on one click"; "A long answer is not fully visible,
  so the page does not show everything that will be submitted").
- **confidence**: high: 7 pull requests, all on the path of hard rule 4.
- **supports**: review-checklist.md item 13 (new).

## Single cases (NO_ENTRY)

- #297, 5 items over 2 rounds, one pull request: the submit guard judged the wrong element
  (`browser_type` clicks without the click verdict, Enter typed as a control character, focus in
  an iframe or a shadow root, a click on a container lands on the submit inside it). One case,
  though a severe one: a second pull request with the same shape makes it an entry.
