# O10 implementation and evidence

Status: complete. Source, unit and API checks ran on 2026-10-25 after merging main (Round 2,
below); the review of 2026-10-26 merged main again and ran the journeys, reviewed the captures and
filed the other pages' issues (Round 3, below).

## Round 3 (review, 2026-10-26, after merging main at 3db1dd19)

- The `opportunities` journey group on an owned fake stack: 42 passed, among them both O10
  tests and the reworked o5, o5b and o8 specs.
- Captures reviewed (Discover, wizard targets, contracts and pay, the sources card; collapsed and
  open, desktop and 375 px, light and dark): every option of the inventory below is reachable,
  no horizontal scroll, the folded pay error opens Advanced and focuses From.
- The 375 px walk of the other pages (no horizontal scroll on any) gave one issue per page group,
  linked from #77: #701 jobs list, #702 profile tabs, #703 Search tab, #704 Application settings,
  #705 Account and administration, #706 CV and posting documents, #707 Document templates,
  #708 Fill my profile, #709 Agent runs. Pages the walk caught still loading are named in their
  issue, to walk again populated.

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
new O10 journey cover these behaviors; the unit tests pass (Round 2), the journey passed (Round 3).

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

## Checks

- Round 2: dependencies, behavioral RED and GREEN, the feed-only `sweep.begin` regression, the
  opportunities API tests, the touched Vitest folders, web typecheck and lint.
- Round 3: the opportunities journey group, the capture review and the other pages' issues.

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
