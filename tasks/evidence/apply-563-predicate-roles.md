# Apply #563 predicate roles and preference polarity

Synthetic fixtures only. The fix starts from integration head
`7acade0b8460f428e481b0a0aeb6af535c539fe4` and changes the answer judge plus a dedicated
regression table. Recorded model streams and the browser source fixtures are unchanged.

The predicate matcher canonicalises the finite technical equivalences only at recognised
personal, work and nominal relative predicate positions. Object tokens retain their original
roles. Clear coordinated predicates retain their subject. An ambiguous bare coordinated
phrase such as `I build robots and design systems` remains exact-only.

The neutral intent fallback reads sourced preference polarity, including contractions,
coordinated preferences and the supported action grammar. A token trie preserves qualifiers:
a positive intent cannot bypass a refusal by adding or removing a trailing qualifier. Exact
sourced positive and negative sentences remain usable subject to the existing answer guards.

The first regression run against the original source returned 66 failed and 18 passed.
Subsequent RED checks reproduced `never`, `don't ever`, gerund/base action changes and trailing
qualifier bypasses before their fixes. The final table contains 106 parametrised tests:
116 supported verdicts must retain the exact proposed value and 90 unsupported verdicts must
be refused. These counts describe assertions, not independent test functions.

Final focused wrapper command:

```sh
ops/agent-test.sh --name predicate563-scoped \
  tests/apply/test_a5_predicate_roles.py tests/apply/test_a5_claim_contracts.py \
  tests/apply/test_a5_agent_filters.py tests/apply/test_a5_run.py \
  tests/apply/test_a5_answer_agent.py tests/apply/test_a5_guards.py -m 'not docker' -n 2
```

The parent integration performs the whole API gate and actual A5 browser journey. This local
check uses throwaway Postgres and Redis only. No real account, environment file, credential,
mail provider or job site is used. No public review, push, merge or deployment is part of this fix.

Results: 1,805 focused A5 tests passed. The new table reports zero false acceptances out of
90 unsupported assertions and zero false refusals out of 116 supported assertions. The original
forms recheck script retained all 9 valid answers, refused all 66 factual inversions and all
9 changed objects, and refused both quoted preference inversions. Its generic saved-letter and
history-query probes also passed. API-wide Ruff lint and formatting passed, the CI plan ran
81 passing tests, the E2E registry passed, and pre-commit passed including the secret scan.

Declared boundary: this is a bounded English grammar, not a general parser. Unrecognised
predicate structures and ambiguous coordination gain no action substitution. Exact sourced
text continues through the existing privacy, practical-answer and fact-check guards.
