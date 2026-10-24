# Source provenance at the answer sink

Issue #563, local follow up for draft #604. All examples and fixtures are invented.

Every proposed sentence now needs a complete personal source sentence. The
permitted sources are the profile and stored answers, including the prepared
letter that `sources_of` places in the personal sources. A company, title, skill,
employer or school does not prove a personal assertion, even when its text happens
to contain the complete proposed sentence.

This rule also covers motivation and preferences. A past achievement does not
prove a desire. The former achievement-only positive intent controls remain in
the tests as explicit refusals, each with a genuine positive sourced companion.
Negative, qualified, historic, uncertain and other-actor preferences remain exact
sources; the sink cannot reverse them into an unsourced positive intent. There
is no separate negator list or neutral-intent fallback to maintain.

Technical predicate equivalence retains the existing build and test verb groups.
The first finite action can map while its whole object stays unchanged, including
noun lists and quoted or unquoted names. Quoted names are opaque tokens, including
sentence punctuation inside them. A coordinated action maps only after a complete
bounded technical nominal and with a complete bounded object of its own. Unknown
coordination keeps exact wording. Actor, tense, negation, modality, qualifiers,
prepositions and nominal words must still agree with the source.

The shared synthetic apply person fixture now explicitly includes the three
claims returned by existing fake agent helpers. The Why questions still reach
the agent, and the existing model-call assertions remain. Recorded model streams
are unchanged. The runtime preference controls overwrite that synthetic source,
so the negative and achievement-only cases still prove refusal at the real sink.

## Validation

The first focused RED run had 74 failures and 5 passes: 32 changed names were
accepted, 12 valid noun-list paraphrases were left, and 30 intent/provenance cases
were accepted incorrectly. The real runtime RED had 4 failures and 1 pass. Its
unchanged recorded positive intent was accepted against three negative sources
and an achievement-only source. The genuinely sourced positive companion passed.

An additional structural probe produced 12 failures and 48 passes for lowercase
names whose embedded action had an incomplete object. Requiring a complete
coordinated object closed that family without adding name prefixes or negators.

The final seven-file focused A5 run passed 1,915 tests, including pure source and
name families, original factual/paraphrase controls, runtime model runs, form
fills and refusal filters. One inherited timer assertion was deselected because
the parent integration already fixes it in `753735d9`; this work does not change
timers. A separate focused run passed all 66 original factual and coordinated
review refusals, each with a genuinely sourced positive companion. The focused
accept/refuse families have zero false accepts and zero false-left expectations.

Whole-tree Ruff checks and formatting checks pass. The CI plan tests pass all 81
tests, and the journey registry check passes. The parent performs the final
composed API and actual A5 journey gates.

## Declared limits

This is a bounded source comparison, not general natural-language entailment.
New preference paraphrases and unknown coordinated grammar are left for the
person unless their complete wording is sourced. Coordinated equivalence has a
small technical nominal vocabulary and accepts direct pronoun objects, a single
word prepositional qualifier, or a determiner phrase ending in a known technical
noun. More complex coordination can retain its exact source wording; the sink
does not expand its grammar to guess at ambiguous names or predicates.
