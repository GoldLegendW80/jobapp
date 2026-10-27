J17: a ticked job website is its permission in Find jobs, and Websites only

## What

Owner decision of 2026-10-27 (refs #626, #651, #714): ticking a job website in Find jobs and saving is the person's permission to search it. It replaces the #651 rule "selection is not acceptance" and its separate "Review website permissions" cards.

- Find jobs setup: one row per job website (LinkedIn, Welcome to the Jungle, Indeed, Free-Work) and per website of the person's own, each with its checkbox, B17's Sign in, and the server's current risk sentence beside the box (linked to it as its description), with a line saying what saving does ("Saving with this box ticked gives this permission.", "Permission given.", "Saving removes this permission.", or the pause date after a checkpoint). A website of her own at a job website's address (`fr.indeed.com`) shows that platform's sentence and counts as that platform.
- LinkedIn's row (and any row of her own at LinkedIn's address) also says, inside the box's description: "Ticking LinkedIn also allows Easy Apply with your LinkedIn account. Each application still needs your approval before it is sent." LinkedIn keeps one switch for its search, its fixed reader and Easy Apply. The server's sentence and its text version are unchanged; the line is fixed text of the setup, shown while the sentence loads too.
- Save and Start: the reviewed profile save first (unchanged, 409 on another tab), then one consent write per platform whose box asks for a change, through the platform's own route (`PUT /api/browser/linkedin`, `PUT /api/opportunities/platforms/{kind}`) with `base_revision` = the revision just saved and `accept_version` = the version of the sentence shown. Every take back is written before any grant, and the writes stop at the first failure, so no permission is given while one the person took back is still on. Before deciding, the setup reads each concerned platform's state again: a sentence that changed since it was shown, or could not be loaded, refuses the save and shows the new text. After each write it reads the profile and checks that only that permission changed; anything else is a conflict ("Load current settings"), never a silent overwrite or retry. Then Start with the final revision.
- Unticking: a job website's box is ticked when any website at its address is, her own included, and unticking it unticks them all (the platform's row goes, her own rows stay unticked), so the box always responds. Indeed's and Welcome to the Jungle's permission is taken back when their box is unticked (they serve this search only); LinkedIn's and Free-Work's when it was given for the websites (`interactive_only`) or the box was ticked when the setup opened. A LinkedIn or Free-Work permission given in Account for the existing reader (and LinkedIn's Easy Apply) stays while its box was never ticked here, and the row says so.
- A new LinkedIn or Free-Work permission row is `interactive_only` (no fixed reader starts with it) and gets the first title to include as its required words; without a title the save is refused with a sentence saying so.
- Websites only: a checkbox in the Websites section. `POST /api/jobs/sweeps {websites_only: true}` freezes the enabled websites alone (no board, feed or fixed reader in the scope or the plan), 422 "Tick at least one website to search websites only." without one; the public scope carries `websites_only`; the summary says "Websites only, no board or feed searched" and the history line "By hand, websites only". The setup reopens with the choice of the last search started by hand; the automatic schedule always searches every source.
- The apply section's Easy Apply hint for LinkedIn now names both places: "Turn on LinkedIn in Account and accept its current sentence to use Easy Apply, or tick LinkedIn in Find jobs and save. You can also apply yourself."
- The decision is in docs/decisions.md (interactive search), with the owner's confirmations of 2026-10-27 below; SPEC-browser's "Choosing a platform does not satisfy its consent" sentence, SPEC-jobs, the #626 plan's J13 consent paragraph and acceptance, and tasks/jobs/todo.md (J17 section) are updated. The `permission_unavailable` summary sentence now says "Tick it in Find jobs and save to give it."

## Why

The owner found the separate permission cards a second step for the same intent; he decided the tick is the permission, with the risk written beside it, and wanted a way to search the websites alone.

## Acceptance

1. One checkbox per job website and own website, risk sentence beside it, consent recorded on save through the dedicated route at the current text version with the reviewed revision, #651's conflict rules kept, no card or prompt: `search-setup.test.tsx` ("shows each job website's current risk sentence beside its box and no separate permission step", "says beside LinkedIn's box, and only there, that ticking it also allows Easy Apply" (the preset row and her own `fr.linkedin.com` row have the line; Welcome to the Jungle, Indeed, Free-Work, her own Indeed address and an unrelated website do not), "records the %s permission of a ticked website after the reviewed save" x4, "takes the %s permission back..." x4, "takes a permission back before it gives one", "leaves Indeed taken back and LinkedIn off when LinkedIn's grant after it fails", "gives LinkedIn no permission when taking Indeed's back fails first", "writes nothing for a ticked website whose permission is current", "leaves a %s permission given for its reader alone..." x2, "takes back a websites only LinkedIn permission...", "gives a new %s reader row the first title..." x2, "refuses to save a LinkedIn tick without words...", "asks to read a risk sentence that changed after it was shown...", "refuses a ticked website whose risk sentence cannot be loaded...", "makes a stale consent revision a conflict, never a silent retry", "keeps the draft and starts nothing when %s changes in another tab while a permission is saved" x5, "blocks Start after an unconfirmed %s consent answer" x3, "keeps the draft editable after a consent route refuses its body", "writes no permission when the reviewed profile save is stale", "gives the platform's permission for a website of her own at its address, once for two rows" (now ticks Indeed before adding `fr.indeed.com`, and checks that both rows are saved with exactly one Indeed write), "unticks her own Indeed address with Indeed's box and takes the permission back", "unticks the %s row and every address of hers there with its box" x4 (`fr.linkedin.com` and `lnkd.in`, `app.welcometothejungle.com` and `wttj.co`, `fr.indeed.com` and `indeed.fr`, `fr.free-work.com` and `freework.fr`), "adds www.indeed.com when Indeed's box is ticked again after unticking her own Indeed address"), `site-permissions.test.ts` (67 cases: the address table, the plan with "takes every permission back before it gives any, each in the fixed order", and the take back rules), `apply-section.test.tsx` (the Easy Apply hint names Find jobs). Done.
2. Websites only runs the ticked websites and no board or feed, the summary says so: `api/tests/jobs/test_j17_websites_only.py` (scope, strict boolean, no website refused, a run that reads no board, feed or fixed reader, the schedule searches every source), `find-jobs.test.tsx` "Websites only (jobs J17)", `search-setup.test.tsx` Websites only tests. Done.
3. Server checks unchanged: no server guard was edited. `api/tests/opportunities/test_j17_ticked_consent.py`, per platform: a website saved ticked by the generic save alone ends `permission_unavailable`; the consent body the setup sends allows it; taking it back during the search gives `permission_revoked`; a stale revision or sentence version is a 409 that records nothing; the 7 day pause after a checkpoint outlives a new tick. The B14 policy tests, caps and pacing tests pass unchanged. Done.
4. Tests: jobs, browser, profile and opportunities folders, web jobs, profile and apply features, the J15 journey updated (ticks the four websites and her own, checks each box's risk sentence, that LinkedIn's box says it also allows Easy Apply, and that no card or "I accept" box exists, saves, checks each platform's permission is allowed, and that no website ends `permission_*`). The journey is type checked, not run (see below). Done, journey run left to the orchestrator.

## Decided by the owner (2026-10-27)

- Indeed's and Welcome to the Jungle's risk sentences keep their current text ("Enable it only if you accept this risk." included), with no new text version, so nobody is asked to accept again.
- LinkedIn keeps one switch for its search, its fixed reader and Easy Apply: ticking LinkedIn in Find jobs also allows Easy Apply, and the line beside its box says so; each application still needs the person's approval. Unticking a LinkedIn box that was ticked turns it off for all three.

## Tests run

Round 1 fixes (on the combined branch):
- `pnpm --dir web run typecheck` (covers web/e2e) and `pnpm --dir web run lint`: clean.
- `pnpm --dir web exec vitest run src/features/jobs src/features/apply --maxWorkers=2 --testTimeout=20000`: 33 files, 996 passed.
- Mutation checks: without the untick of her own rows, the untick tests fail; with grants before take backs, the three order tests fail; without LinkedIn's Easy Apply line, its two tests fail.
- `python3 .github/scripts/e2e_groups.py`: OK.
- `python3 .github/scripts/ci_plan_test.py`: 1 failure and 3 errors, the known ones of main.
- `uvx pre-commit run --files <changed files>`: all hooks passed, gitleaks included.
- No api file changed in round 1, so the api folders were not rerun.

First build:
- `ops/agent-test.sh --name j17 tests/jobs -m "not docker" -n 2`: 1292 passed, 15 skipped.
- `ops/agent-test.sh --name j17 tests/opportunities -m "not docker" -n 2`: 1705 passed (a first run fell in the Docker outage of 12:20 to 12:26 UTC; rerun clean).
- `ops/agent-test.sh --name j17 tests/browser -m "not docker" -n 2`: 3635 passed.
- `ops/agent-test.sh --name j17 tests/profile -m "not docker" -n 2`: 1610 passed, 17 skipped.
- `uv run ruff check app tests` and `uv run ruff format --check app tests`: clean.
- `pnpm --dir web exec vitest run src/features/jobs src/features/profile --maxWorkers=2 --testTimeout=20000`: 40 files, 1246 passed.
- A mutation check: with the grant write disabled, the four "records the %s permission" tests fail.

Not run: the Playwright journeys (J15, and B8, B10, B17, O8 which open the setup) and no image build, by the sandbox's rule. Built on `combine/b17` with `combine/j16` merged (J16's shared lists component kept; J16's own setup test now expects revision 8 because its Free-Work tick records the permission).

## Files outside the owned ones

- `web/src/features/apply/apply-section.tsx` and its test: the Easy Apply hint also names Find jobs' LinkedIn box (one sentence).
- `web/src/features/jobs/sweeps-api.ts`, `use-sweep-runner.ts`: the start takes `websitesOnly`; the sweep schema reads `search_scope.websites_only`.
- `web/src/features/jobs/find-jobs.tsx`: the summary and history lines say Websites only.
- `web/src/features/jobs/labels.ts`: the `permission_unavailable` sentence no longer names the removed cards.
- `api/app/jobs/schemas.py`, `sweep.py`, `sweep_router.py`: the websites only start (the sweep's mode, owned by this task).
- `docs/decisions.md`, `SPEC-browser.md`, `SPEC-jobs.md`, `tasks/jobs/interactive-search-626.md`, `tasks/jobs/todo.md`: the decision and the stale sentences.

## Self review

- 1 Heuristic: `platformOf` mirrors `platform_policy.platform_of` (any host label but the last); 31 address cases, both directions; the server's check stays the authority, so a client misreading fails closed there. The untick follows `platformOf`, tested with two address shapes per platform.
- 2 Shared state: every consent write is bound to the revision of the save just made; after each write the profile is read and compared; two tab tests for the save, the write and the read in between; no retry. Take backs are written before grants, tested with a failure on each side.
- 3 Invariant: "a permission is written only by a Save whose box is ticked, at the version shown": plan unit tests (never shown, older, newer version refused) and component tests. "No permission is given while one the person took back is still on": the plan order test and the two failure tests.
- 4 Sinks: no new log line, no secret; the profile save body carries no permission (asserted).
- 5 Claims: "never satisfies", "never a silent overwrite", "the schedule always searches every source", "the summary says so", "the sentence beside its box says so" (the LinkedIn line test), "unticking it and saving takes the permission back" (the untick tests) each have the test named above.
- 9 A test can fail: the mutation checks above.
- 11 Merge: built on the combined branch; `git merge-tree` with its current head shows no conflict.
- 12 Reviewer probes with variants: the Indeed case of the review, plus every platform with two address shapes of hers and the platform's own row.
- 14 Every acceptance line has its evidence above.

## Declared open points

- A website of her own is classified by the client like the server for ASCII hosts; an internationalised host is compared in its punycode form. Any difference ends on the server's check (`permission_unavailable`), never in a search without permission.
- A save is two steps: if the profile save succeeds and a consent write then fails, the ticked selection is saved without its permission (the search says it needs it); the setup shows the error and the person saves again.
- After "Load current settings" following such a partial save, the memory of "ticked when the setup opened" restarts, so a LinkedIn or Free-Work permission given for the existing reader would stay; a websites only one, and Indeed's and Welcome to the Jungle's, are still taken back.
- J15: LinkedIn and Free-Work now pass the check and open the pages service like Indeed and Welcome to the Jungle; their `result_pages >= 1` is unverified until the journey runs, and LinkedIn's 10 to 30 s pacing lengthens that sweep.
- Review follow ups not done in round 1, as asked: keeping the first "ticked when opened" set across reloads; whether a newer sentence version needs a fresh tick (an owner question); tests for a failed profile read after a consent write and for the `isCurrent` branch; `readerWords` with regular expression titles; naming A18's profile completion in the LinkedIn line; the load error shown beside older cached data; the risk sentence shown twice with an address of her own; the Websites only box that can flip after the sweep list loads.

Refs #626, #651, #714

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_017CqfhHnPbtyZ6jyK1KZS4T
