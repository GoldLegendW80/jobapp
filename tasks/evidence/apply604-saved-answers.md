# Saved answers completion, 2026-10-23

Refs #563 and draft PR #604. The owner confirmed that application edits stay local and reusable
answers are saved separately in Settings. This slice adds the missing creation path using the
existing answer-bank table and restores the specified source order.

## Behaviour verified

- A fresh account sees an empty bank and adds answers through the visible Add answer form.
  POST `/api/apply/answer-bank` accepts only question and answer. The server derives the owner
  and normalized key. Concurrent equivalent questions produce one 201 and one 409, without an
  overwrite. Existing PUT Edit and Forget retain their absent-key 404 semantics.
- Question and answer limits are 200 and 5000 code points. The request body limit is 63424
  bytes, including JSON surrogate escapes. Empty values, unsupported normalized keys,
  control and format characters are refused; LF answer line breaks and other Unicode are kept.
  Tests cover the maximum escaped Unicode payload, foreign owners, signed-out requests and CSRF.
- Current identity, documents and profile answers precede saved answers. An authoritative value
  that cannot fit the offered choices stays left. Missing profile answers can use the bank.
  EEO, salary, forbidden-field and free-text guards remain before bank lookup.
- A verified bank fill increments the owner-bound counter once per application field. An
  existing event carrying only the field key provides the refill marker. No marker is written
  when the saved row has been forgotten. Unverified fills and explicit application edits do not
  count. Ordinary fills without an eligible bank row perform no usage-marker query.
- The form labels, pending read-only inputs, validation focus, restored Add-button focus,
  duplicate draft retention and request-error retry state are covered by UI tests.

## Test results

- RED before implementation: fresh creation returned 405; a stale bank email overrode identity.
  The verified-use counter regression independently failed with 0 instead of 1.
- `ops/agent-test.sh --name bank604-final tests/apply tests/identity/test_body_limit_api.py -m 'not docker' -n 2 -q`:
  **7653 passed**, at the Saved answers checkpoint before composing generic recovery.
- Composed source, `test_saved_answers.py`, `test_a3_answers.py` and the complete
  `test_generic_recovery.py`, through the same wrapper with `-n 2`: **170 passed**. This covers
  the integration regression where no-bank generic fills use a minimal database interface.
- `pnpm --dir web exec vitest run src/features/apply --maxWorkers=1`: **402 passed in 14 files**.
- Typecheck, lint, whole API Ruff check and format, CI plan's 81 tests, e2e registration and
  repository commit hooks passed.
- The real `apply-saved-answers.spec.ts` journey: **1 passed in 4.3 minutes**, one worker,
  through Caddy, the real worker and the native browser against the local Greenhouse fixture.
  It proves fresh-user Add without a bank database seed, verified reuse with count 1, current
  identity over a stale saved email, a local application edit leaving the bank unchanged,
  updated profile answers over the bank on a second form, and **zero submissions**.

The browser build composed the current integration baseline `963dc518` with Saved answers
checkpoints `c85d07ac`, `5d06cb32` and the verified UI test correction `49300d81`.
The root's final composed gate remains its separate responsibility.

## Layout evidence

The journey asserts no horizontal overflow in each layout. Desktop light and mobile dark were
also inspected visually.

- [Desktop light](apply-saved-answers/add-answer-desktop-light.png)
- [Desktop dark](apply-saved-answers/add-answer-desktop-dark.png)
- [Mobile light](apply-saved-answers/add-answer-mobile-light.png)
- [Mobile dark](apply-saved-answers/add-answer-mobile-dark.png)

## Isolation and cleanup

Only invented `e2e-*@example.org` accounts, the fake model backend and local fake ATS services
were used. No real environment file, credential, account, mail service or ATS was accessed.
The stack used its own `bank604` image tags, synthetic temporary settings and loopback ports.
Its containers, networks, volumes and four owned image tags were removed after the journey.
The borrowed renderer image was left untouched. Disk space after cleanup was 45 GiB.

No migration was needed. Small supporting changes outside the bank and resolver files are the
request-body registry, route privacy matrix, human timeline label, the existing Lever isolation
fixture's absent profile answer, the stale application-edit comment, and the specification/task
contract text. No application edit writes to the bank.
