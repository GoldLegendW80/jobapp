# Interactive job search, issue #626: implementation plan

Approved contract: [SPEC-jobs](../../SPEC-jobs.md#interactive-search-626) and [SPEC-browser](../../SPEC-browser.md#interactive-discovery-626). The owner authorized fresh subagents, implementation and new draft PR work on 2026-10-24. [Draft #627](https://github.com/GoldLegendW80/jobapp/pull/627) starts with the reviewed specification. No implementation, acceptance run, merge or deployment is claimed by this plan.

## Task tracking

GitHub Issues are the task list target. The existing unrelated incomplete tasks/plan.md and all existing tasks remain intact. This plan uses the repository convention tasks/<module>/. J8 is historical completed work (#122); new jobs tasks start at J9. Each slice has its own branch and draft PR, retaining Refs #626 until full acceptance.

- [B13, #628](https://github.com/GoldLegendW80/jobapp/issues/628): execute guarded interactive job search through the LLM runtime
- [J9, #629](https://github.com/GoldLegendW80/jobapp/issues/629): save chosen websites and freeze search criteria at Start
- [J16, #641](https://github.com/GoldLegendW80/jobapp/issues/641): preserve source modes and pending outcomes in the existing search summary
- [J10, #630](https://github.com/GoldLegendW80/jobapp/issues/630): run selected websites through the interactive finder
- [B14, #631](https://github.com/GoldLegendW80/jobapp/issues/631): enforce platform consent and durable search checkpoints
- [J11, #632](https://github.com/GoldLegendW80/jobapp/issues/632): save verified search findings incrementally
- [J12, #633](https://github.com/GoldLegendW80/jobapp/issues/633): cancel searches and restore persisted progress
- [B15, #634](https://github.com/GoldLegendW80/jobapp/issues/634): watched sign-in and takeover during manual search
- [B16, #635](https://github.com/GoldLegendW80/jobapp/issues/635): follow observed posting destinations within source scope
- [J13, #636](https://github.com/GoldLegendW80/jobapp/issues/636): choose websites and criteria from Find jobs
- [J14, #637](https://github.com/GoldLegendW80/jobapp/issues/637): show live browsing progress and partial search results
- [J15, #638](https://github.com/GoldLegendW80/jobapp/issues/638): verify the complete interactive search journey on fake sites

This remains one capability in the existing jobs, profile, agents and browser modules. Reuse RuntimeFinder, runner.execute, SdkBackend and the existing bound tool layer.

Implementation checkpoints are published as [draft #639](https://github.com/GoldLegendW80/jobapp/pull/639) for J9 and [draft #640](https://github.com/GoldLegendW80/jobapp/pull/640) for B13. Review findings and verification evidence are recorded in those drafts. They do not establish full acceptance or change the deployed application.

## Source findings that determine the order

* `finder.py` and `finder_tools.py` currently offer navigate, snapshot, wait and report only. `FinderDefinition.prompt` returns a constant first message; `sources/site.py` has no criteria. The first slice must execute new search actions through the runtime and Playwright, not just define interfaces or parse canned jobs.
* browserd `detect.py` and `tools/actions.py` refuse native submit controls, Enter within forms and typed submit. `tools/routes.py` only issues gate capabilities for `apply_submit` and `profile_edit`. Search permission must remain separate and must never add `sweep` to `GATE_PURPOSES`.
* `sweep._insert` checks today's search, but `claim` reloads it later and replaces `Sweep.stats`. `save_progress` and finalization also rebuild stats. Frozen run data and cancellation therefore need deliberate preservation, including compare-and-update ownership of control fields.
* `Runner._one` waits for a whole source before calling its `store` callback. Incremental results need a verified batch callback invoked during reporting, committed before progress is announced.
* `site.py` assigns employer from the careers host; `_store_found` substitutes today's date for a missing publication date. Both are unsuitable for a job board discovery path. `Job.company` is non-null but may store an explicit unknown label. `Job.published_on`, pay and work mode are already nullable.
* `signin_tool`, API `handoff.py` and browserd `handoff.py` explicitly refuse sweep sign-in even with a viewer. A separate bounded manual-discovery handoff slice is required. The current sweep preemption on a person's own browser session also needs a clear outcome.
* Existing JSONB columns cover saved settings, frozen scope, bounded progress and job event provenance. Do not add a search schema migration merely for this work. Existing LinkedIn checkpoint rows remain the authority for LinkedIn; general platform checkpoint work belongs to the existing per-platform consent foundation.

## Implementation contract

Keep the repository's snake_case HTTP fields, strict Pydantic validation, current `{detail: ...}` errors, session ownership, CSRF checks and no-store behavior. Extend interfaces additively.

### Saved profile

Use the existing `PUT /api/profile/search` `{base_revision, data}` and its existing revision conflict behavior. Add to `SearchSection`:

* `interactive_sites`: at most 10 entries, default empty. Each is `{platform, url, enabled, mode: "llm_browser"}`. `platform` is `linkedin`, `welcome_to_the_jungle`, `indeed`, `freework` or `custom`. Canonicalize URLs with the existing finder/address policy, enforce at most 2,000 characters, reject credentials, IP/private destinations and duplicate canonical URLs, and derive a stable safe source identifier in code. Reject a claimed known platform with a mismatching host. Independently classify protected platform hosts even when the submitted platform is `custom`.
* `interactive_instructions`: optional normalized multiline text, maximum 2,000 characters.

Reuse `title_include`, `title_exclude`, `location`, `remote_stance`, `contract_types`, `seniority`, `days` and `compensation` as the criteria. Industries and company types retain their scoring role. Existing boards, feeds, fixed browser readers and legacy finder remain explicit alternatives, not fallback implementations for these entries. Setup labels distinguish `llm_browser`, API/feed and existing fixed readers. The interactive website cap does not erase the existing board/feed caps.

Consent is not a writable property of an interactive site. A source being selected or enabled never records acceptance, clears a checkpoint or enables an apply path. Existing profile save/import protections must preserve browser-owned consent/checkpoint fields. Extend their protections when integrating the generic foundation.

### Start, scope and status

Keep `POST /api/jobs/sweeps` and existing no-body callers. On the start transaction, deep-copy the selected enabled sources, explicit modes and relevant criteria into a versioned internal `Sweep.stats.search_scope` before enqueuing. Capture the profile revision, created time and manual/schedule trigger. `search_scope.version = 1` is a JSON payload format, not an Alembic revision. `claim`, progress, reaping and finalization preserve it. A scheduled run freezes when its sweep row is created. Runs created before the change retain a documented legacy path.

Save and Start in Jobs first saves the reviewed search revision, then starts with the returned revision as an optional `profile_revision` request field. The start transaction compares that field and returns 409 if another edit won; it does not silently run different criteria. Existing no-body callers keep working. Double starts keep the current single-running-sweep 409 and run-following behavior.

Consent and checkpoint permissions are checked against current server state before lease acquisition and again before later actions. Frozen criteria freeze intent, not stale permission. Revocation stops later actions. Runtime input contains only an opaque internal request key and budgets; prompts obtain frozen criteria from the server-bound session, not raw run input/event JSON.

Extend `SweepOut` and `SourceOut` with optional/defaulted fields that old clients can ignore: source id, mode, phase, action, outcome/reason, pages, verified/saved/duplicate/rule counts, criterion statuses and queue/wait state. Source phases: `pending`, `queued`, `running`, `waiting_person`, `done`; outcomes distinguish `complete`, `partial`, `no_matches`, `blocked`, `failed`, `cancelled`, `budget_stopped`, `not_attempted`. Existing error codes remain available; do not infer complete coverage from an empty error. Initialize one status entry for every selected source before work. Criterion statuses are `applied`, `unavailable`, `unverified`, each supported by bounded observed evidence, never model reasoning.

Expose a sanitized frozen criteria summary in the caller's sweep detail, omitting custom instructions from SSE, audit events and action labels. A full private scope stays internal. Existing GET list/detail/events remain the authoritative reopened-run path. Incremental results are returned from committed `JobEventKind.sweep` links, scoped to user and sweep. No raw browser snapshots, typed input, cookies, credentials or private prompt text enter run progress.

Add idempotent `POST /api/jobs/sweeps/{id}/cancel`: caller-owned only, unknown/foreign 404, CSRF, no-store. Persist `cancel_requested_at` as a control field atomically under the sweep row lock and return current progress. Already-ended runs return their existing final view. Do not add a Postgres status enum merely for cancellation: a terminal sweep may retain existing database status plus `outcome="cancelled"` and a clear reason. The executor checks the persisted flag between tool calls, stops after any in-flight call, commits already verified findings, interrupts/closes the actual agent backend, then releases its lease in `finally`. Progress writes cannot erase a concurrent cancel request.

### Browser permission and findings

The browser tool contract remains the existing lease/token endpoint and existing action names. A trusted SDK tool factory, not a model argument, may mark an action as discovery. browserd accepts that marker only on a sweep lease, applies a separate conservative discovery policy, and never issues or accepts an apply/profile capability for it. Start with native GET search forms, explicit observed search/filter fields, same-scope pagination and verified detail navigation. Query typing and search submission must be actual Playwright actions. Unknown controls, ambiguous write behavior and unsupported JS search flows return a visible unsupported reason. Do not use a positive Search label as sufficient authorization.

The discovery policy combines current element/control/form facts with a narrow read request policy. Refuse password/account/contact/application/profile/payment controls and endpoints, misleading Search controls, hidden submit targets, unsafe Enter/Space, account writes and off-scope redirects. Initial safe support can refuse POST search endpoints; widening any endpoint rule requires a concrete fake page and refusal neighbors. Retain the stronger danger detector and existing gate tests unchanged. The model gets no evaluate/JavaScript, upload, arbitrary key, filesystem, CDP or grant-permission tool.

Only accept a finding after code has read its opened detail page in this run. Title and any claimed employer must be supported in that page; an absent employer is explicitly unknown. Bind the observed canonical URL and detail text to the source/session and reject model text substituted for code-read text. Capture publication date only when observed; `found_on` stays the observation date. Unknown pay/seniority/date/work mode stay unknown. A rationale cites bounded facts and criterion uncertainty.

Persist provenance/rationale in the existing user-owned sweep job event data, with a validated namespace such as `discovery: {source_id, mode, observed_url, detail_read_at, employer_known, criterion_evidence}`. Do not misuse the unrelated `legacy` object. The API can derive an additive discovery view from that event without changing core Job columns. Keep existing deduplication and global never-apply checks. Avoid treating every unknown employer as the same known employer during identity deduplication; URL remains authoritative for these rows.

For posting destinations outside the selected host, code must first observe the destination on that source's page and classify it as a posting destination. A narrow source/destination policy controls any new lease host; do not add arbitrary observed links to a lease. Initial unsupported external destinations get a visible refusal. Support approved observed ATS destinations in its own slice, retaining exact source attribution, redirect checks and no general web expansion.

## Ordered issues and bounded ownership

Target about 800 total changed lines per slice and about five application files. Estimate tests and fixtures explicitly; split oversized work before writing. Preserve the repository review size bar and every required behavior check.

### B13 (#628) / first issue: execute a native interactive search through the real runtime

**Deliverable:** an internally invoked interactive finder run accepts an explicit immutable test scope, uses `runner.execute` and the SDK tool registration, types a query, submits a harmless native GET search, selects a filter, clicks next page, opens a detail page and returns only code-read verified findings. This is executable search behavior, not a contract-only scaffold. Existing legacy finder behavior stays available. Public setup wiring follows J9/J10.

**Owns:** `api/app/browser/finder.py`, `api/app/browser/finder_tools.py`, `browser/browserd/detect.py`, `browser/browserd/tools/actions.py`; if needed one small `browser/browserd/tools/discovery.py`. New logic stays in the current browser module. Factories add the code-owned discovery marker when invoking bound `LeaseTools.call`; never add it to model schemas. Keep old finder tools restricted unless the session was explicitly created for interactive discovery.

**Acceptance:** (1) recorded deterministic model tool calls pass through real agent runtime and actual Playwright against an interactive fake page; the job is unreachable until query, filter and pagination actions execute, (2) parser-only execution or removing the new runtime tools fails this proof, (3) apply/message/profile controls, misleading search actions, foreign/ended leases, hostile text and off-scope redirects produce refusals and no write.

**Tests:** extend B9 runtime and B5 policy/action coverage; add one real browser integration test using the same fake fixture. Include 30 to 40 accepted/refused neighbors around every new text heuristic, with structure and allowlists preferred to lexical guesses. Assert no gate capability is issued and existing apply/profile gate tests still pass. Do not claim full #626, sign-in or production source wiring in this PR.

### J9 (#629) / independent second issue: save chosen sites and freeze a run at Start

**Depends on:** fixed contract above, not B13 implementation. **Owns:** `api/app/profile/schemas.py`, `api/app/profile/service.py` only if preservation is needed, `api/app/jobs/sweep.py`, `api/app/jobs/schemas.py`, `api/app/jobs/sweep_router.py`.

**Acceptance:** (1) strict per-person site/instruction validation and profile revision checks, including a custom URL disguised as a consent-controlled platform, (2) freeze on manual/scheduled sweep creation and prove edits before worker claim and during the run affect only later sweeps, (3) list/detail/events report every selected source with explicit mode and safe scope summary; all stats rewrite paths retain the scope and existing no-body starts/onboarding work.

**Tests:** profile save/import preservation and two-person tests; sweep API, worker, schedule and crash/reap tests. No schema migration or UI in this issue.

**Compatibility dependency:** J16 must precede merging the backend status expansion. Its optional client fields work with the old API, so this adds no dependency cycle. Record its actual PR as a dependency of draft #639 when published.

### J16 (#641) / review follow up: retain truthful source modes in the current summary

**Depends on:** the fixed HTTP contract above; no backend implementation dependency. **Owns:** `web/src/features/jobs/sweeps-api.ts`, `sweep-sources.tsx` and their owning schema/render tests.

**Acceptance:** preserve optional nullable source identity, mode, phase, outcome and reason while accepting old payloads. API board entries must not appear under browser activity, since their aggregate/failure summary already exists. An interactive source that was not attempted must say it was not searched rather than imply an observed zero. Keep feed and legacy rendering compatible. This is a small compatibility change; the setup and live-run interfaces remain J13/J14.

**Tests:** mixed-mode and pending-outcome regressions, legacy payload parsing, whole Jobs feature tests, typecheck, lint and normal hooks. Transfer these client paths to J14 after this slice; do not edit them concurrently.

### J10 (#630) / third issue: connect selected interactive sites to the bounded runtime

**Depends on:** B13, J9. **Owns:** `api/app/browser/sources/__init__.py`, one `api/app/browser/sources/interactive.py`, `api/app/browser/finder.py`, `api/app/browser/finder_tools.py`, `api/app/jobs/sweep.py` only for the existing hook contract.

**Acceptance:** (1) selected `llm_browser` entries pass frozen criteria into actual queries/filters and use `RuntimeFinder` with normal production `SdkBackend`, (2) missing model/browser, unavailable permission or unsupported interaction is source-specific and cannot fall back to API/fixed readers; each selected site is attempted or explained, (3) one-browser queue, sweep priority/preemption, per-host Redis pacing and 900/840 second, 20 page, 40 turn, 600k token, 100 verified posting ceilings remain enforced with tighter platform/runtime limits winning.

**Tests:** runtime options/credential modes, missing credential/browser, hostile instructions, page/action/token/time limits, queue contention and lease release. FakeBackend is a test seam, not a replacement production implementation. Retain private_io/live_text protections.

### B14 (#631) / fourth issue: connect per-platform opt-in and durable checkpoint controls

**Depends on:** J9 and the existing #447 / draft #619 foundation, if its reviewed contract is adopted. Do not edit #619 or any approved PR. Root either declares a real dependency or creates a separate missing foundation issue before this task. **Owns:** the existing browser platform guard/router files supplied by that foundation, `sources/interactive.py`, and profile consent preservation as needed, at most five application files.

**Acceptance:** (1) protected platform host classification cannot be bypassed by custom URLs, choosing a site is not consent, and consent is per person/platform with version/date, (2) guard before lease and subsequent actions honors current revocation and stale text, (3) checkpoint is committed immediately, remains for seven days across restart and profile import/save, cannot be cleared by setup or toggling, and lets other selected sources continue with truthful reasons.

**Tests:** fake each controlled platform, two-person consent isolation, stale version, import bypass, revoke mid-run, checkpoint before simulated process loss, seven-day boundary with injected clock. Keep existing LinkedIn authority and current tighter daily/page caps. No real platform visit.

**Migration note:** saved search and run scope need none, so #604/#625 Alembic numbering collisions are avoidable here. General platform checkpoint persistence may genuinely require the existing foundation's migration. Do not create a competing migration or repurpose `linkedin_checkpoints` for other sites. Validate the foundation chain only when composing dependent work, outside this read-only proposal.

### J11 (#632) / fifth issue: save observed findings incrementally and truthfully

**Depends on:** J10. **Owns:** `api/app/browser/finder.py`, `api/app/browser/finder_tools.py`, `api/app/browser/sources/interactive.py`, `api/app/jobs/sweep.py`, `api/app/jobs/schemas.py` if provenance response needs it.

**Acceptance:** (1) each verified reported batch commits through existing jobs storage before progress announces it; a later error preserves earlier jobs, (2) opened-detail provenance, employer evidence, unknown fields and observed rationale are validated, without the host-as-employer or missing-date-as-today substitutions, (3) dedup/rule counts and user isolation hold, global never-apply including cybersecurity produces skipped records, and no Like/apply/contact/account write is created.

**Tests:** missing employer/date/pay/work-mode, invented title/company, unvisited URL, model-supplied text mismatch, same title with different unknown employers, duplicate URL, source attribution, never-apply, hostile page and incremental partial failure. Known observations stay bounded in memory; store batches rather than retain all snapshots. Seniority remains a score preference and pay rules remain existing behavior.

### J12 (#633) / sixth issue: cancellable persisted progress and useful summaries

**Depends on:** J11. **Owns:** `api/app/jobs/sweep.py`, `api/app/jobs/sweep_router.py`, `api/app/jobs/schemas.py`, `api/app/browser/finder.py`, `api/app/browser/sources/interactive.py`.

**Acceptance:** (1) idempotent owner-only cancel stops later actions after the in-flight call, preserves committed findings and releases the lease, (2) concurrent progress cannot erase cancel or frozen scope; reconnect/list/detail restore the running state and incremental results, (3) source outcomes distinguish no matches, partial, blocked/failed, cancellation, budgets, unattempted, duplicates and exclusions, plus saved findings below the display threshold separately from no findings.

**Tests:** cancellation during a held tool call, cancel before claim, cancel/final race, progress/cancel race, backend interruption, terminal idempotence, foreign ids and one-source failure followed by another. Freeze min_fit for the run explanation while clearly labeling the display threshold used for visible result counts.

### B15 (#634) / seventh issue: watched manual search sign-in and takeover

**Depends on:** J10, J12. **Owns:** `browser/browserd/handoff.py`, `api/app/browser/handoff.py`, `api/app/browser/finder.py`, `api/app/browser/finder_tools.py`, one existing browser lease/client transport file if the trusted manual-discovery discriminator needs it. Split transport changes separately if more files are necessary.

**Acceptance:** (1) only a person-started interactive discovery with its own current viewer may use the existing sign-in swap; schedule/full_auto still refuse and the model never gets credential fields, (2) same person's persistent profile, input ownership, existing lease/handoff deadlines and total outer run budget hold through done/cancel/timeout, (3) manual takeover pauses automatic tools and giveback resumes only within the immutable scope; another person's view/ticket/lease is refused.

**Tests:** adapt B12 tests and real browser journey for manual interactive sweep; explicitly assert unattended sweep remains denied. If opening the person's own browser preempts the sweep under B8, report partial/preempted, do not pretend that lease resumed. The within-run handoff must use the held lease rather than opening a competing sign-in session. No second browser.

### B16 (#635) / eighth issue: approved observed posting destinations

**Depends on:** J10, J11. **Owns:** browser interactive source/session and the minimal existing lease/client/browserd host transport needed, split at five files if necessary.

**Acceptance:** (1) an approved destination may be reached only after its posting link was observed on the selected source, with source provenance retained, (2) unrelated links, host swaps, private addresses, credentials, unsafe redirects and a destination's further expansion are refused, (3) unsupported destinations get a source-specific reason and already saved results remain.

**Tests:** fake selected board to approved fake ATS, source to unrelated footer, source-controlled misleading link, redirect-chain escape and foreign lease. No wildcard general browsing. This can remain conservative rather than promising universal custom-site support.

### J13 (#636) / ninth issue: Jobs setup and saved selection

**Depends on:** J9; integration finish depends on J10 and B14. **Owns:** `web/src/features/jobs/find-jobs.tsx`, new `search-setup.tsx`, `use-sweep-runner.ts`, `web/src/features/profile/api.ts`, `editor-forms.ts` as required for preservation. Split the field-schema preservation from visual work if five files is exceeded.

**Acceptance:** (1) Find jobs opens accessible setup with four confirmed target choices plus custom URLs, saved criteria and custom instructions, strict server-matching limits and explicit modes, (2) Save and Start handles revision conflicts and per-platform consent without equating selection to opt-in, (3) profile Search edits preserve interactive fields, and existing API/feed source choices and schedule/onboarding behavior remain available.

**Tests:** component/form tests for first setup, reopened values, unsaved close, validation, conflict and permission refusal, keyboard focus, 375px, light/dark. Keep advanced numeric/debug controls out of the simple initial flow.

### J14 (#637) / tenth issue: Jobs live run, watch and partial result UI

**Depends on:** J12, B15, J13. **Owns:** jobs `find-jobs.tsx`, `sweeps-api.ts`, `use-sweep-runner.ts`, `sweep-sources.tsx`, optionally one small new run-panel component.

**Acceptance:** (1) show selected sites, frozen criteria summary, current observable action, queue/wait state, watch/handoff/takeover using existing browser components, incremental saved jobs and Cancel, (2) leaving/reopening Jobs restores the current run without a duplicate start, (3) final/empty/partial states show precise source outcomes, unsupported criteria, unknown evidence and threshold-hidden saved results with links to results and Search options.

**Tests:** component reconnect/SSE-poll fallback/cancel/partial tests and existing jobs feature tests, accessible live regions and mobile overflow, no model reasoning or private snapshots rendered.

### J15 (#638) / final automated issue: full fake-site proof and owner acceptance handback

**Depends on:** all prior slices. **Owns:** tests and fixtures, existing evidence directory, and narrow journey registration only. No broad app refactor.

**Acceptance:** (1) fake pages representing LinkedIn/WTTJ/Indeed/Free-Work and a custom URL expose the relevant posting only after actual query typing, harmless submission, filters, pagination and detail read via real runtime tool invocation; a parser-only path fails, (2) one coherent browser journey covers setup, progress, watch/handoff, incremental results, cancel and reopened/partial/empty states, desktop and 375px, keyboard/light/dark, (3) tool traces, observed query/filter state, committed provenance and refusal probes substantiate each spec acceptance line, with real-model effectiveness explicitly still pending owner acceptance.

After a separately authorized deployment, the owner alone runs the configured actual model against their confirmed selected sites and criteria. Record runtime/tool invocation and observed saved jobs or honest source-specific zero/blocked reasons, without personal data in the public repo. Development agents do not visit real sites, accounts or credentials. Root must not close #626 or describe the feature as fully accepted before that human evidence. The current authorization is draft PR publication, not merge/deploy.

## Two-builder schedule and shared ownership

Start Builder A on B13, Builder B on J9 against this contract and the linked task issues. They own disjoint application files except potential `finder.py`, which is A-only during this group. J9 owns all jobs sweep/schema/router files; A does not touch them. This gives two real, bounded deliverables without parallel edits of security policy.

After those land in the dependency branch: A owns J10 then J11/J12 sequentially; B can own B14 consent integration, then the J13 setup surface against the fixed API. `api/app/profile/schemas.py` and `service.py` transfer from J9 to B14 explicitly, never concurrently. Runtime/session files transfer from B13 to J10/J11/J12/B15/B16 serially. `browserd/detect.py` and `tools/actions.py` have one policy owner. Jobs `find-jobs.tsx`/runner/api transfer J13 to J14 serially. Root owns all task-plan edits, issue links, approval-status docs and shared registry lines. Do not make multiple agents edit `.github/workflows`, `worker.py`, config, Compose or journey registries in parallel.

Checkpoints: after B13+J9, demonstrate real bounded fake search plus immutable persisted scope; after J10+B14+J11/J12, demonstrate a guarded selected-source run with incremental committed jobs and cancellation; after B15+B16+J13/J14, execute the complete fake browser journey. Each checkpoint is evidence, not an extra request for permission to continue already authorized work.

## Verification and normal publication constraints

For touched backend modules, run each whole relevant module folder once through the existing helper, with one pytest worker, for example `rtk proxy ops/agent-test.sh --name jobs-626-b13 tests/browser tests/agents -m "not docker" -n 1` or `... tests/jobs tests/browser -m "not docker" -n 1`. Profile work includes `tests/profile`. Keep the code's injected clocks and existing fake model seam. The actual-browser proof cannot be marked run by these non-Docker unit tests alone.

Run actual browser tests and all journeys of the browser group on an isolated own-name Compose stack/image tags through `rtk proxy ops/agent-test.sh --name jobs-626-j14 --playwright --config web/e2e/browser-all.config.ts --workers=1`, using the repository helper's actual accepted arguments and gate setup as the implementation guide. Run any other touched module's journey group. No gate execution by builders, no shared latest retagging, no real sites/accounts, and remove only their own containers/volumes/tags after the command. E2E accounts only `e2e-*@example.org`.

Backend: `rtk proxy uv run ruff check app tests` and `rtk proxy uv run ruff format --check app tests` in api. Web: typecheck, full tests for touched features and lint using existing pnpm commands. Always run `rtk proxy python3 .github/scripts/ci_plan_test.py` and `rtk proxy python3 .github/scripts/e2e_groups.py`. Search existing assertions for changed behavior and update legitimate expectations, never skip/delete tests or weaken constraints.

Normal pre-commit, formatting, secret scan and git hooks must run. No `--no-verify`, hook-path/config changes, `SKIP`, secret reads or environment dumps. Existing installed dependencies/caches may be reused read-only. A private copy of a hook environment must be complete and include any embedded absolute paths or explicit virtualenv interpreter invocations, then run ordinary `pre-commit run --files ...` and the normal commit hook against that environment; don't assume moving a venv rewrites its shebangs. Any copy consumes root's disk reserve, so estimate before copying and do not install or rewrite shared caches merely to make a planning or docs commit pass.

Disk measured about 16.25 GiB free during source inspection. Maintain 12 GiB minimum throughout, plus explicit headroom for each builder's stack/artifacts; the gate needs about 3 GiB and starts with at least 16 GiB when eventually authorized. No heavy stack while `.local-check/lock` exists. Root reserves disk before concurrent work, at most two heavy builders, pytest `-n 1`, browser `--workers=1`. Do not write `.local-check` or clean others' worktrees/caches. No background processes or wait loops.

Each builder creates its own worktree, one issue/branch/draft PR, normal push, repository PR template, first-line `Depends on #...` for dependencies, truthful commands/results/remaining work, relevant self-review and declared heuristic limits. Merge origin/main and rerun checks before pushing as repository rules require, without touching approved PR branches. Use a body file for multiline gh publication. This session authorizes the new draft PR; it does not authorize merge, deploy or edits to approved PRs. A fresh first reviewer is separate from the builder.

The minimal documentation status update for the latest user instruction is already root-owned: owner approved this concrete contract and continuing implementation/draft PR, targets unchanged, automated acceptance pending, real-model owner acceptance pending, no implementation shipped. Keep all four spec/decision cross-links consistent with the new anchors. Do not turn pending acceptance bullets into completed claims.
