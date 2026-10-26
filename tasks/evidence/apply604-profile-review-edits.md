# Profile section approval and displayed edits

Refs #563 and draft PR #604, whole integration review blocker 4.
Base: `73ee000f99468a48f31cac7fcca03e306e9841d4`.

## Problem and contract

SPEC-apply, “Complete my profile on a site”, requires a one-use section approval
bound to the hash of the values the person reviewed. Field-local drafts and PUT
mutations previously left Approve section available while the visible text or
Replace choice differed from the acknowledged server section.

## Change

- The section owns field drafts and one field mutation. Field PUTs are serialized
  within the section, and all field controls wait for an acknowledgement.
- Both opening confirmation and confirming approval refuse unsaved text or a
  pending field PUT. Approval continues to send the acknowledged `values_hash`.
- Keep edit preserves a failed text draft, while Undo restores the server value.
  A failed Replace request restores the acknowledged checkbox value.
- A field acknowledgement clears only drafts whose server value changed. Another
  field's unsaved draft survives the returned section and continues to block Save.
- Changed hashes invalidate confirmation; an earlier hash returning does not
  reopen it. A different run or section remounts the local editing state.
- Null and empty values both display an empty editor; the literal text `Empty`
  remains a real value. Unchanged fields need no PUT before approval.
- Existing protected fields, file handling, autosave consent, explicit Skip and
  backend authorization/hash checks retain their existing behavior.

## Behavioral regressions

The focused component file now contains 18 cases: unchanged null/empty/literal
values, normal and empty unsaved edits, manual reversion, Undo, held Keep edit and
Replace PUTs, the acknowledged approval hash, failed text and Replace saves,
preservation of unrelated drafts, stale confirmation after a hash change or local
edit, superseded draft non-resurrection, different-run state reset, explicit
confirmation and stale approval recovery. The old stale-approval fixture now
returns the edited value and new hash after a successful PUT.

## Verification status

Prettier on the two changed component files and `git diff --check` passed. Normal
commit hooks also passed, including ESLint for changed web files and secret
checks. No tests, full-project lint, typecheck, browser, Docker or database commands
ran because the orchestrator's composed gate owns the machine. Red proof
against the old component, green execution, integrated frontend checks and fresh
independent review are explicitly deferred to the orchestrator after that gate.

Self review: scoped UI state only; no API or backend guard changes; both approval
entry points use the same guard; pending and failed writes remain visible;
unrelated drafts and run identity were considered; no skipped assertions, relaxed
limits, real data, source approved branch changes or public PR writes.
