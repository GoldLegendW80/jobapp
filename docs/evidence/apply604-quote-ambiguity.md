# Apply quote ambiguity, issue 563

The standalone word-final apostrophe in a single-quoted name can be either a
possessive or the closing delimiter. Sentence extraction now requires one complete
quote interpretation. A small finite state pass counts interpretations, capped at
two per state, and retains persistent span links for the unique surviving parse.
The second token pass follows those spans, including a late closing delimiter when
the first possible close cannot complete the parse. Each token is visited at most
twice, with at most five quote states.

When a personal prose source is ambiguous, a complete verbatim copy retains its
context and remains usable. This equality permits only the existing separator
normalization and outside whitespace trim. It does not add metadata as personal
provenance or bypass question, practical, legal, length, or independent fact checks.
An extracted sentence or an edited copy still needs supported, unambiguous source
sentences. No claim word lists or linguistic exception patterns were added.

## Regression proof

- Before the fix, the expanded boundary file had 18 failures and 414 passes:
  10 unsupported inner claims accepted with straight or curly single delimiters,
  and eight variants across space, LF, CRLF and CR separators.
- The count-only interim fix reproduced two additional positive failures: the sole
  complete parse closed late, and the actual assertion after the name was lost.
  Persistent span reconstruction closes both failures.
- Final focused run: `ops/agent-test.sh --name apply563-quotes-final
  tests/apply/test_a5_provenance_boundaries.py
  tests/apply/test_a5_source_provenance.py
  tests/apply/test_a5_claim_contracts.py -m 'not docker' -n 2 -q`:
  **1,152 passed** in 5.52 seconds.
- The new 16-case delimiter/apostrophe table is repeated for five inner claims:
  80 of 80 inner claims refused, 80 of 80 complete source copies usable,
  80 of 80 edited name copies refused, and 80 of 80 genuinely explicit source
  companions usable. Existing quote, source metadata, predicate and claim controls
  stay in the same focused run.
- Ten ordinary possessive, contraction and clear quote controls retain their source;
  all eight past-action controls also retain the allowed main predicate equivalent.
- Both single quote forms with one viable late close refuse the inner assertion
  and preserve the actual outside assertion and the full source copy.
- An in-memory mutation replaced the final sentence lexer with the actual lexer
  from base `3482ea19`. Both exact straight and curly regressions failed, as required.
  The function was restored in `finally`; both regressions and both late-close
  positives then passed. No source file was mutated for this probe.
- The two maximum-size body shapes instrument token visits and assert at most two
  visits per token, without wall clock assertions. The facts body limit stays
  200,000 bytes. Informational local measurements at about 50k, 100k and 200k
  characters were 0.011, 0.022 and 0.044 seconds for ordinary possessives, and
  0.010, 0.019 and 0.039 seconds for repeated ambiguous spans.

## Whole module and static verification

- `caffeinate -i ops/agent-test.sh --name apply563-quotes-whole tests/apply
  -m 'not docker' -n 2 -q`: **7,092 passed** in 406.27 seconds.
- `ruff check api/app api/tests`: passed. `ruff format --check api/app api/tests`:
  all 749 files already formatted.
- `.github/scripts/ci_plan_test.py`: all 81 tests passed.
- `.github/scripts/e2e_groups.py`: passed, registry unchanged.
- Changed-file pre-commit hooks: passed, including secret scans and Ruff.
- `git diff --check`: passed. `git merge-tree --write-tree origin/main HEAD`:
  no conflicts at the composed fix base.

The earlier whole-module run was intentionally interrupted to add the late-closing
positive control, and is not counted as proof. Every agent-owned throwaway service
from that interrupted run was removed before the final whole-module run started.
No browser or image build was required for this pure provenance boundary.

## Declared boundary

A source with competing quote interpretations can supply its complete original
context, but cannot supply fragments or a guessed paraphrase. A person can give a
separate explicit prose assertion to support that assertion independently. This is
a conservative structural provenance rule, not a general natural language parser.
Typed profile metadata remains separate and available only to independent fact
checking. Tests use invented text and isolated throwaway Postgres and Redis.
