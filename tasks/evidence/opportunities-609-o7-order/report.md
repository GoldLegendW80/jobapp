# O7 deterministic source-filter journey, issue 609

The real opportunities group passed all 35 tests on a fresh fake stack, one Playwright worker,
zero retries, wrapper exit 0. Alembic current and heads both reported `0023 (head)`. All 11
services were running, with zero restarts and healthy where a health check exists. The stack,
volumes and three owned image tags were removed; the reviewed renderer and shared cache stayed.

The original #547 whole-group failure is retained: 34 passed, one failed and one serial case
not run. Its unchanged O7 assertion expected Nimbletide but Longform was visible. The original
assertion, `tests/feeds_stub.py` and production deck are byte-identical between #547 and this
branch's main base. Both HN missions have fit 100 and the same discovery date; the deck then
sorts by UUID. The manifest binds those sources and the retained failure screenshot.

The correction adds ten lines before the exact original Nimbletide assertion. Either known HN
mission may be first. When Longform precedes Nimbletide, the journey verifies its Freelance
contract and exact item 4007 link, then uses Pass on that verified fake mission. It reaches the
unchanged Nimbletide, Freelance and item 4001 acceptance checks through the person's controls.
Every original line and timeout remains. Source membership, cybersecurity exclusions and tenant
B isolation remain in the original tests.

Controlled browser-response probes use private copies bound to the source. Both legitimate
orders pass all three O7 tests. The unchanged original assertion fails with Longform first.
Wrong Nimbletide link, wrong Longform link, wrong source, missing Nimbletide and unknown card
fail at their intended assertions. The actual probe wrapper exits 1 for these deliberate
failures: 12 passes, 6 deliberate failures and 6 serial cases not run. An independent audit of
that actual JSON passes with exit 0; no test rerun was used to repair the audit parser.

Strict private probe instrumentation saw zero unexpected browser warnings, errors or page
errors and verified HttpOnly, SameSite=Lax session cookie metadata in 16 fake loopback contexts.
Secure=false is the development HTTP setting. Cookie values were not recorded. This extra
strict console and cookie coverage applies only to the controlled probes. The actual full35
run retains its original console assertions and has no private imports or route control.

A first private probe attempt discovered no tests because its temporary package lacked the
source package's ESM setting. Its failure was retained, then that package metadata was fixed.
A post-run audit parser used the wrong filename suffix; that failure was retained and the
actual result JSON was audited separately without rerunning tests. Neither is a runtime pass.

Typecheck, lint, CI plan, e2e group checks and all applicable pre-commit hooks passed. The
tracked captures are limited to the original O7 failure and the eight native O7 source-filter
views. The manifest binds each PNG to the relevant spec hash. Full raw outputs and 265 PNGs
remain in the temporary handoff artifacts; no unrelated group captures are committed.

Raw local handoff paths:

- `/tmp/jobapp-609-native-evidence`: actual full35 JSON/log, commands, migration and restart
  receipts, all native captures, source bindings, cleanup receipt and overall capture manifest.
- `/tmp/jobapp-609-probes`: controlled source copies, bindings, actual results and failure
  messages, cookie/console metadata, discovery failure and independent audit verdict.
- `/tmp/jobapp-609-evidence`: original/main source equality and exact assertion preservation.
- `/tmp/jobapp-547-native-evidence-rerun`: original failure evidence, preserved unchanged.

Only invented accounts and fake services were used. No publication, merge or deployment was
performed.
