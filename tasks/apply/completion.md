# Apply specification completion

Requested on 2026-10-22: implement the whole apply specification. The contract is
[SPEC-apply.md](../../SPEC-apply.md), the existing [task plan](todo.md), and the recorded owner
decisions. This audit supplements that plan; an item is complete only with implementation and
passing evidence. Tracking issue: #70. Existing approved pull requests remain unchanged.

## Integration baseline

The separate `apply/spec-completion` branch combines immutable snapshots of existing pull
requests #465, #521, #517, #516, #470, #598, #583, #588, #589, #590, #591, #592, #595, #600 and
#601. Their source branches and GitHub states are not changed. This makes their dependencies
available for development without merging anything into main or deploying it.

The integration preserves both sides of the A9/A10a live-view and interrupted-run changes and
the A13c model-check services and worker. The combined tree requires fresh regression checks;
an approval of an original snapshot is not an approval of the combined tree.

## Capability and evidence checklist

| Capability | Existing implementation | Completion work | Evidence required |
|---|---|---|---|
| A1 to A8: forms, answers, fill, approval, reports, base adapters | Main plus existing apply snapshots | Audit interactions and refusal paths; fix specification gaps | Complete apply tests and fake ATS journeys |
| A9: Apply myself | #517 snapshot | Check manual/session recovery and file chooser follow ups | Manual journey, no app submit click, isolation |
| A10a: retries and needs you | #516 snapshot | Owner's 429 retry, 403 needs-you and 410 closed rules | Injected-clock failure and recovery tests |
| A10b: sign-in recovery | Missing | #578: attended and unattended handoff, later callback, expired sessions and lease survival | Fake login walls, idempotent resume, Full auto becomes review |
| A11 to A13c: caps, Like queue and Full auto | Main plus #591, #598, #470 | Verify composed decision, hold, rehearsals, model check, pauses and final rechecks | Counted fake submissions, clock/cap tests and journeys |
| A14: email applications | Missing; required mail send components also missing | #251: draft, exact authorisation, provider boundary, send gate, uncertainty resolution and UI | Fake Gmail only, immutable attachments, no duplicate send |
| A15: Workday, Google Careers and VIE | Missing | #252: bounded wizards, sign-in resume, labelled controls and file choosers | Fake multi-step applications; Full auto hands over to review |
| A16: LinkedIn Easy Apply | Missing | #253: opted-in modal flow, company-site fallback and LinkedIn pacing | Fake modal, consent revocation/checkpoint, cap and isolation tests |
| A18a to A18c: complete external profiles | Only initial tables exist | #254, #255, #256: persisted sections, factual fill, protected-field policy, review/save gate and UI | Fake portal, one-use hash approvals, current values/Replace, autosave consent |
| A17: privacy and retention | Storage ownership/cascades exist; final proof missing | #257: prune temporary screenshots, route isolation, privacy text and click/send boundary proof | Two-user route suite, account/posting deletion, retention boundaries and canaries |

## Dependency and ownership decisions

- Sign-in recovery supplies a user-owned, typed callback registry shared with external profile
  completion. A callback cannot name another user's run or an arbitrary queue job.
- Profile completion needs persisted addresses, section states, locators and audit metadata
  that migration 0017 did not create. An additive migration is required despite the original
  plan's "no migration" estimate. PR #591 supplies revision 0023; profile completion takes
  0024, and required mail send storage follows as 0025.
- The worker prepares email drafts; the process allowed to hold the mail send key owns the
  actual send gate. An uncertain provider result never causes an automatic resend.
- Testing uses invented `e2e-*@example.org` accounts and local fake sites/providers. At most
  two heavy test jobs run at once. Browser verification uses the repository's Playwright
  tooling because Chrome DevTools MCP is not configured in this session.
- Real-site Checkpoint A remains a human acceptance step after a checked deployment. It is
  not simulated by a fixture and is not claimed as completed by implementation tests.

## Final verification

- [ ] All capabilities above have implementation, including their provider dependencies.
- [ ] The complete affected API modules pass, with fixture-only skips identified.
- [ ] Frontend typecheck, lint, component tests and build pass.
- [ ] All apply/browser journeys needed by the completed capabilities run on isolated stacks.
- [ ] Migration upgrade and downgrade checks pass with one Alembic head.
- [ ] Independent reviews cover the submission, profile-save and email-send gates.
- [ ] Approved PR snapshots retain their original heads and GitHub states.
- [ ] Each remaining human step or external configuration requirement is named explicitly.
