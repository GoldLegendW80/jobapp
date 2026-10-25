# O10 implementation and pending evidence

Status: draft. Source, unit and API checks ran on 2026-10-25 after merging main (Round 2,
below). No O10 screenshots have been captured, no 375px walkthrough has run, and no follow-up
issue is filed yet: those need an owned fake stack, which waits for the merge gate's lock.

## Round 2 checks (2026-10-25, after merging main at 0c069f85)

- Behavioral RED: with main's page source and the O10 tests, 18 tests fail in 4 files (the
  label regression for all eight product files, the four simple wizard panel tests, the folded
  starter choices, and five wizard flows that now open Advanced). GREEN: the same tests pass on
  the branch.
- Vitest, onboarding, opportunities, profile and lib folders: 35 files, 1024 tests passed.
- Web typecheck (web and e2e) and lint: passed.
- API: `ops/agent-test.sh` on `tests/opportunities` (with `test_o10_begin.py`): 1132 passed;
  ruff check and format on the new test: passed.
- `ci_plan_test.py` and `e2e_groups.py`: passed.

## Product control inventory

This is a source inventory for the runtime check, not a claim that the journey passed.

| Page or card | Up front | Under Advanced or otherwise still reachable |
|---|---|---|
| Discover | Like, Pass, Undo last, More, mode link | Six contract types, remote/on-site selection and source, unchanged |
| Wizard profile | CV upload, proposal review link | LinkedIn export or pasted text, document selection, estimate, read start and progress |
| Wizard targets | Roles | Six seniorities, industries and seven company types; saved role patterns are read-only here with a Search options link |
| Wizard places | Anywhere, places and a separate four-choice work-mode card | Saved place patterns are read-only here with a Search options link |
| Wizard work | Five employment contracts in one card and Freelance mission in its own card | All six contracts remain selectable |
| Wizard pay | Rather not say | Salary and daily rate, each with From, To and Currency; folding retains the draft |
| Wizard application mode | Two modes, three languages and three tones in separate cards | Existing Full auto availability and checks remain as before |
| Wizard summary | One Change per answer card; starter Add all and Add the ticked ones | Individual starter selections and Remove; Search options still exposes the starter list as before |
| Freelance sources | Four switches and Add a mission by hand | Per-source terms, saved through the existing Search form; opt-in information stays reachable |

The shared component keeps the user's expanded preference. A pay validation error forces the
section open and focuses the invalid field; it stays open while the person corrects the draft.
`simple-panels.test.tsx`, the wizard pay regression, existing source-card error tests and the
new O10 journey cover these behaviors; the unit tests pass (Round 2), the journey has not run yet.

## Other-page walkthrough and issue work remaining

`web/e2e/opportunities-o10.spec.ts` records an accessibility snapshot and a screenshot at 375px
for each route below. Inspect the actual captures, expand the page's controls and dialogs, and
then create the bounded page issues with exact labels and controls. Check duplicates again
before publishing issue bodies from files, then link every issue from #77. The initial GitHub
search on 2026-10-21 found #399 for D13 follow-ups and the #77/#350 umbrellas; it did not find
an existing simple-by-default issue per page. No other lane's implementation is authorized here.

| Page group | Owning label | Routes or views to walk |
|---|---|---|
| Jobs filters | lane:hardening | /jobs, expanded status/type filters, fit/date range and sort |
| Profile Identity | lane:hardening | /profile?tab=identity, contact fields, languages and other links |
| Profile Experience | lane:hardening | /profile?tab=experience, employers and education, keys and dates |
| Profile Facts | lane:hardening | /profile?tab=facts, blocks, editor and preview |
| Profile Skills | lane:hardening | /profile?tab=skills, years, skill categories, gates, penalty and matching preview |
| Profile Answers | lane:hardening | /profile?tab=answers, salary figures, repeated answers, equal-opportunity questions |
| Profile Search | lane:hardening | /profile?tab=search, fit/days/schedule, board and browser-source options, patterns; coordinate existing D13 #399 |
| Application settings | lane:apply | /settings/applications, mode, thresholds, hold, pacing, company limits and rehearsal states |
| Account and administration | lane:hardening | /account and /admin/users, account panels, invite/reset dialogs and administration controls |
| CV and posting document editing | lane:docs-edit | /profile/cv, a seeded base CV, the posting documents card and CV editor; also inspect a populated letter editor |
| Document templates | lane:docs-design | /documents/templates, picker and its edit/import dialogs |
| Ingestion fill | lane:hardening | /profile/fill?step=prompt, documents and review; expand run settings and review controls |
| Runs | lane:hardening | /runs and a fake run's detail, filters, measurement details and live stream |

The audit helper is a starting inventory. A human or resumed agent must inspect the captured
pages and the populated/dialog states before publishing the follow-up issues. None of these
other-page source files is changed by O10.

## Required checks before approval

- Done in Round 2: dependencies, behavioral RED and GREEN, the feed-only `sweep.begin`
  regression, the opportunities API tests, the touched Vitest folders, web typecheck and lint.
- On one owned fake stack, run the complete opportunities journey group, including O10.
- Review collapsed/open screenshots at desktop and 375px in light and dark. Confirm each
  existing option and saved setting from the product inventory remains reachable.
- Finish the other-page walkthrough, create the owning-lane issues and link them from #77.
- Re-run CI plan and journey registration guards after any registration or task-document change.

## Prerequisite integration

#538 and #553 are on main. After the merge of main on 2026-10-25 the branch changes no backend
code: main's `sweep.begin` and `_insert` already hold the `enabled_feeds(search)` predicate, and
the profile service comment conflict was resolved to main's wording. `test_o10_begin.py` stays
as the pure feed-only start regression. The O10 change is the product pages, `wording.ts`,
their tests and the journeys.

## Sources checked

The state update that retains the error-open disclosure follows React's documented guarded
update of the currently rendering component: [useState](https://react.dev/reference/react/useState).
The label regression traverses JSX with `createSourceFile` and `forEachChild` as described by
the [TypeScript compiler API](https://github.com/microsoft/TypeScript/wiki/Using-the-Compiler-API).
The audit records the accessible structure using
[Playwright aria snapshots](https://playwright.dev/docs/aria-snapshots).
