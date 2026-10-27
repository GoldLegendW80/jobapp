Browser B17: sign in to a job website once, then Find jobs reads it signed in

Closes #714

## What and why

The owner asked (2026-10-26) for a browser where each person signs in to a job website once and keeps that login per user, so the search agent can read the website signed in. Each website of Find jobs now has a Sign in action. It opens the live view on the website in the person's own saved browser profile, and the person signs in or signs up themselves: the agent never types a password. Done records the login for that person's profile only. Searches then read the website in that profile, signed in, with JavaScript on, and every guard still applies. When the login has ended, the search stops and asks the person to sign in again.

Branch `browser/b17-signed-in-discovery`. The first two commits (7540c510, 8bbf6f94) come from a previous builder that was cut off. The five commits after them complete the work and fix what that builder left open (see "Found and fixed" below).

## Acceptance, line by line

1. **A Sign in action on each job website (in the setup and in the Search tab):** done.
   - In the Find jobs setup, each website row has its Sign in (`web/src/features/browser/site-signin.tsx`, `SiteSignIn`). The profile's Search tab lists the saved websites of Find jobs, each with its Sign in (`JobWebsitesSignIn`, in `search-tab.tsx`).
   - The session is B7's sign in session at the website's address: the person holds the input and there is no debugging port.
   - Done (`POST /api/browser/sites/done`) ends that session. It then asks browserd (`POST /profiles/{id}/site-session`, which runs prep `sitecookies` as the profile's uid) whether the cookie store holds an unexpired cookie of that host or of a parent domain. Only the host and expiry columns are read, never a name or a value. The login is recorded only when the answer is yes.
   - The row shows Signed in, or Sign in again with "Your login on this website has ended..." after a search met the sign in page. Otherwise it shows nothing besides the action. Forget all logins (B7) removes every website login along with the profile.
   - Evidence: `test_b17_signed_in.py::test_done_records_the_login_for_that_person_only`, `test_expired_then_forget_all_logins_removes_every_site`, `test_site_cookies*`; vitest `site-signin.test.tsx` (6 cases); journey `browser-b17.spec.ts` test 1.
2. **Signed in websites are read in the person's own profile, every other website stays anonymous, and person B never uses A's login:** done.
   - A website with a login is searched in the person's persisted context with JavaScript on. Only code sets `Scope.signed_in`: the scope keys always override the model's arguments.
   - Every other website stays anonymous with JavaScript off. Searches still run one lease at a time.
   - Person B never uses A's profile, session or website login.
   - Evidence: `test_only_the_person_who_signed_in_reads_the_site_signed_in`, `test_an_anonymous_scope_never_touches_the_persons_context`, and B11's `test_isolation.py`. The isolation table now has `GET /api/browser/sites` and `POST /api/browser/sites/done`, and B's answers show nothing of A's website login. `test_a_login_is_recorded_only_on_the_profile_whose_cookies_were_read` covers the record. Journey test 4.
3. **Every guard holds:** done.
   - Apply shaped paths and links, and apply or contact buttons (Postuler, Candidater, Easy Apply, Apply now, Contact), stay refused (`test_apply_and_contact_buttons_stay_refused_signed_in`).
   - Only GET and HEAD leave the page, never to an apply, submit, delete, payment or sign out address. Documents must stay within the frozen scope, so only the site's GET search box is used (`test_signed_request_lets_only_reads_leave`, `test_the_route_lets_only_reads_leave`, `test_a_signed_in_request_never_signs_the_person_out`).
   - New for B17: before the first navigation, the tab bypasses the profile's service workers, blocks ws:// and wss://, and gets a script that removes Worker, SharedWorker, WebSocket, WebTransport and service worker registration. If one of these steps fails, the search ends and the tab closes (`test_the_persons_context_bypasses_service_workers_and_blocks_sockets_first`, `test_a_hardening_step_that_fails_ends_the_discovery`).
   - Pacing, daily caps per platform, the 7 day checkpoint pause and the prompt injection refusals are unchanged: `GuardedTools` runs the same policy before each call.
   - A sign in page met while signed in ends the website with `platform_signin` and marks its login expired. The page can show up as a redirect, an address or a heading. The summary then says "This website needs you to sign in. Use Sign in beside it in Find jobs, then search again." (`test_a_sign_in_page_while_signed_in_ends_platform_signin_and_expires`, `test_a_sign_in_page_is_told_and_nothing_leaves_after_the_end`, journey test 3).
4. **Tests:** unit and api tests done (listed above).
   - The fake website with a login page is in `tests/browser_pages/server.py`: J15's invented website under the name `members.example-robotics.test`, members only. It has a sign in page, a log of what it saw, and `/b17/expire` to end every login.
   - The journey `web/e2e/browser-b17.spec.ts` is written: sign in through the live view, Find jobs reads the members only postings with the member's cookie, GET only, the Send application button refused, then after the logins end, Sign in again, then B. **It has not been run here** (see "Not run").

## Found and fixed on top of the first builder's commits
- `test_isolation.py` would have failed on its first test: the two new routes were not in its table.
- Done made a browser profile row for a person who had none (`profiles.ensure`). It now only reads.
- A race: if a Forget and a new profile happened between browserd's check and the write, the new empty profile got the login. The write is now bound to the checked profile, by its id and the person's.
- Service workers, WebSockets and workers bypass Playwright's route (measured in the Playwright 1.55 driver source: service worker traffic is routed only under `PW_EXPERIMENTAL_SERVICE_WORKER_NETWORK_EVENTS`). They are now bypassed or blocked as described above.
- A page request to `/logout` or `?action=signout` could sign the person out. It is now refused.
- A failed hardening step used to leave the tab open. It is now closed.
- `test_b17_signed_in.py` found `browserd` only through import order. It now sets the path itself.

## Tests run
- `ops/agent-test.sh --name b17 tests/browser -m "not docker" -n 2`: 3580 passed.
- `ops/agent-test.sh --name b17 tests/test_migrations.py tests/jobs/test_migration.py tests/browser/test_b3_api.py -n 2`: 39 passed. The migration head constant is now 0028.
- `ruff check app tests` and `ruff format --check app tests` in api: clean. The same for `browser/browserd`: clean.
- `pnpm --dir web run typecheck`: clean. `pnpm --dir web run lint`: clean.
- `vitest run src/features/browser src/features/jobs src/features/profile`: 45 files, 1260 tests passed.
- `python3 .github/scripts/e2e_groups.py`: passes.
- `python3 .github/scripts/ci_plan_test.py`: 4 failures. The same 4 fail on a clean origin/main 64fe8b08 ("a dynamic import in api/app/apply/profile_router.py"), so they come before this branch.
- `uvx pre-commit run --files <changed files>`: every hook passes, gitleaks included.
- The members website's handler was exercised in process (a scratch script): 302 to the sign in page without the cookie, the cookie set by `/j15/session`, members only pages served with it, POST refused with 405, and the cookie refused after `/b17/expire`.

## Not run, and why
- The docker marked tests of `tests/browser` and the journey `browser-b17.spec.ts` (and the rest of the `browser` group). The browser image cannot be built in this sandbox:
  - First, `docker.io/library/debian:bookworm-slim` answered `429 Too Many Requests`.
  - Once the pull went through, apt inside the build failed, with `--network host` and the proxy build args too: `E: Failed to fetch http://deb.debian.org/debian-security/dists/bookworm-security/InRelease 405 Method Not Allowed [IP: 127.0.0.1 33829]`. The sandbox proxy refuses plain http.
  - The fixture build of `test_browser_container.py` fails the same way (`Dockerfile:25 RUN apt-get update`).
  - So nothing in this PR has run against a real Chromium. The new CDP calls in particular (`Network.setBypassServiceWorker`, `Network.setBlockedURLs` with `urls`, `add_init_script` on the persisted context) are only proved against fakes. If Chromium 156 refuses one of them, a signed in search ends `unsupported_discovery` (fail closed). The journey would show that.
  - **Please run the `browser` group** (`ops/local-check.sh`) before merging.

## Files changed outside the owned ones
- `api/tests/test_migrations.py`: the migration `HEAD` constant goes from 0027 to 0028, one line.
- `tests/browser-pages.compose.yml`: the alias `members.example-robotics.test`, added on the pages service and in the browser's `BROWSER_PROXY_ALLOW_PRIVATE`.
- `tests/browser_pages/server.py` and the new `tests/browser_pages/b17/login.html`: the members only website. J15's log and answers on `jobs.example-robotics.test` are unchanged.
- `api/tests/browser/test_isolation.py`: the B11 table, extended.
- `web/src/features/profile/search-tab.tsx`: one component line.
- `web/src/features/jobs/search-setup.tsx`: one component line plus a class.
- `web/src/features/jobs/labels.ts`: the `platform_signin` sentence. This one line meets PR #715 and jobs/632, and is kept small for that merge.
- `api/app/browser/sources/interactive.py`: small and local, as the brief asked.
- `SPEC-browser.md`: one bullet. `tasks/browser/todo.md`: the B17 section.

Size: about 2270 changed lines, about 1550 of them tests, the journey and fixtures. That is over the 1500 line guide, because the acceptance needs the fake site and the journey.

## Self review
1. Heuristics: there are neighbour tables for the sign out words, the sign in addresses and the request rule. That is 34 rows for sign out and 15 for sign in addresses (`test_a_signed_in_request_never_signs_the_person_out` covers job slugs like log-analyst, sign-language-teacher, applied-scientist and supply-chain-lead), plus 17 rows for the route. Anything not recognised is refused: the route refuses whatever `signed_request` does not accept.
2. Shared state: Done reads the profile and then writes against that same profile id (composite of id and user). There is a two step race test. Concurrent Dones upsert the same row.
3. Clock: stored times only, and no rule reads them. `SiteLogins` takes an injected clock.
4. Sinks: no cookie name or value is read. The api logs neither the host nor the address. A's website host is a canary in B's answers (`test_isolation.py`).
5. Every "never", "every" and "only" claim has its test named above. The open points below are stated plainly.
6. Size caps: the Done address is capped by `ADDRESS_MAX`, the host at 253, browserd's body at 1024, and a request URL at 8000.
9. The tests can fail: without the hardening, without the route rows or without the profile binding, they go red (checked by reading what each asserts).
11. `git merge-tree` with origin/main 64fe8b08: no conflict. Migration 0028 is still the next number, and no open branch adds one.

## Declared open points
- A cookie of the host proves a visit, not a login. A Done after a visit without signing in records the website. The first search then meets the sign in page and marks it expired, so the row says Sign in again.
- Not caught: a request that has a side effect on a GET, at an address without the apply, submit, delete, payment or sign out words. Also not caught: a subresource redirect hop (Playwright's route sees only the first address). Main frame redirect hops are checked by `_hop`.
- The worker and socket script runs in the page, so a hostile page's freshly made frame may still reach those constructors. The route, the service worker bypass and the CDP socket block still hold there.

## Open questions for the owner
None.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_017CqfhHnPbtyZ6jyK1KZS4T

# B17 round 1 fixes

Reviewed head f69763c0 (verdict-b17.md). Fixed on branch `fix/b17-r1`, made from `claude/serene-gates-xxs89r` (ec27776a) and merged with its later head 4390586a (no conflict). Worktree `.claude/worktrees/fixb17`, local head e09657169082ec8c9d78bea62b601764daea1321, not pushed. 8 files, +389 / -22.

## B1: service workers out of the page's reach

- `browser/browserd/tools/actions.py`: fixup F2. `SIGNED_IN_JS` ends with `try { delete Navigator.prototype.serviceWorker; } catch (e) {}`. Its comment, the module docstring and the `_begin_signed_in` docstring now say "service workers bypassed and out of the page's reach". The docstring declares the residual: an event the browser itself delivers to such a worker (push, background sync, a cookie change) can still wake it.
- `api/tests/browser/test_b17_browserd.py`: `test_the_persons_context_bypasses_service_workers_and_blocks_sockets_first` asserts that `delete Navigator.prototype.serviceWorker;` is in `SIGNED_IN_JS`. It goes red without the line.
- `SPEC-browser.md` (B17 bullet) and `tasks/browser/todo.md` (B17 section): reworded the same way. The residual is listed in the plan's open points.
- Real Chromium proof. I ran the reviewer's `rvb17-probe/probe.py` here (Chrome for Testing 153.0.8010.12, Playwright 1.55, browserd's real `Tab.begin_discovery`, signed in scope):
  - **Before the fix**, on the merged tree: `in_nav: true`, and `POST /p/sw-message-post` reached the server (red, as the reviewer measured).
  - **After the fix:** `in_nav: false`. The message path throws TypeError, in the page and in a fresh frame. `register` throws TypeError. No POST reaches the server. The page's own fetch still reaches the network (`GET /p/sw-data`, answered by the server, not the worker).
  - **Variant (my copy, `fixb17-probe/probe.py`):** a `BroadcastChannel('b17')` message to a worker that listens on it. Nothing reached the server, because the bypassed worker is never running. `Object.getOwnPropertyDescriptor(Navigator.prototype, 'serviceWorker')` is gone.

## B2: the journey can now fail on the request rule

- `tests/browser_pages/server.py`: only the members host changed. Member pages (`/j15/search`, and the `.html` files and folder index under `/j15/`) are served with `B17_PROBE_JS` before `</body>`.
  - On load, the script tries each of these in its own try: a fetch POST `/j15/b17-post`, `sendBeacon('/j15/b17-beacon')`, a keepalive POST `/j15/b17-keepalive`, `new WebSocket(ws://host/j15/b17-ws)`, `new Worker('/j15/b17-worker.js')`, and an Image at `/j15/logout`. It keeps a reference to each object, so a socket is not collected before it connects.
  - It then asks `GET /j15/b17-ran?<what each attempt did>`.
  - It adds no element to the page, so the refs e5 to e8 of the recorded model stream stay the same.
  - The server's existing log records whatever reaches it. J15's own host, the login page and the session route are unchanged and stay script free.
- `web/e2e/browser-b17.spec.ts`:
  - **Test 1, positive control:** after Done, it polls the site log for `POST /j15/b17-post`. That request came from A's own sign in session, which has no guard, so the probe is shown to reach the site. Test 1 then moves the log mark (`before = siteLog().length`).
  - **Latent bug fixed:** test 2 used to slice from the `beforeAll` mark. It therefore also saw test 1's non-member `/j15/` and `/j15/login` entries, and its `read.every(member === true)` would have failed. The journey has never run.
  - **Test 2:**
    - at least one `/j15/b17-ran?` marker, so the script ran with JavaScript on;
    - every marker says `ws=err:SecurityError` and `worker=err:SecurityError`, so the init script holds on the gate's Chromium;
    - none of the six probe paths is in the log (`probed(seen)` is `[]`);
    - the existing GET only check stays.
  - The header describes the probe.
- **Real Chromium proof** (my harness `fixb17-probe/members_probe.py`). It runs server.py's real `Handler` and browserd's real signed in discovery, in Chromium 153 with `--host-resolver-rules` for the members host and `--no-proxy-server`:
  - Own session (no guard): all six paths reached the site (3 POSTs, `/j15/b17-ws`, `/j15/b17-worker.js`, `/j15/logout`).
  - Signed in search over `/j15/`, the page 2 search and the posting: no probe path, GET only, 3 markers (one per page), each `ws=err:SecurityError&worker=err:SecurityError`.
  - Route disabled (`_signed_route` always falls back): the 3 POSTs and `/j15/logout` reached the site, so the journey would go red.
  - Init script emptied: `/j15/b17-ws` and `/j15/b17-worker.js` reached the site and the markers said `ws=made&worker=made`, so the journey would go red.
- The journey was type checked only (`pnpm --dir web run typecheck`), not run. The orchestrator runs the browser group.

## B3: the apply and submit words are read in the query

- `browser/browserd/tools/discovery.py`. The new functions are `_applies`, `own_words` and `query_applies`. `destructive(url, scope=None)` now also refuses when `query_applies(parts.query, scope)` is true.
  - **Every query key and value is read**, both split and glued: an APPLY_PARTS part inside any word, a word starting with a submit stem, or a confirm verb with an application noun anywhere in the query (the noun is also found inside a glued word, such as `confirmapplication`). This is `detect.says_submit`'s rule without its 60 name and 500 character caps.
  - **Own search words skipped:** a value whose folded words equal the frozen scope's query or one of its filter values is skipped. So a search for "apply engineer" or "Sender" is read.
  - **Keys are always read.**
  - **Fails closed:** a query of more than 100 fields is refused.
  - **Not read in the query** (stated in the docstring): the save and update words, and `says_danger`, because `sort=updated`, `saved=true` and `category=payments` are reads.
- **Same family, documents.** `signed_request` used to return `read_url(...)` alone for a document, so a frame navigation to `/logoff`, `/fr/deconnexion`, `/fr/se-deconnecter` or `/de/abmelden` left. I measured True for each before the fix. A document must now pass `read_url` and also `not destructive(url, scope)`.
- **Tests** (`api/tests/browser/test_b17_browserd.py`):
  - **Refused:** 43 spellings × 11 places × 2 hosts. The spellings include easy apply in 15 forms, full width and zero width forms, postuler, candidater, candidature, bewerben, solliciteren, submit, envoyer, send, and the confirm and noun pairs, split and glued. The places include as a value, as a key, a blank key, after `;`, the 61st field and past 500 characters.
  - **Allowed:** 41 rows, among them the reviewer's four and the person's own words in other case or separators.
  - **Exact match:** an 8 row table of what counts as exactly the person's words.
  - **Fail closed:** the 101 field case.
  - **`ROUND1` regression table:** the reviewer's 8 rows plus the 6 document rows.
- **Independent fuzz** (`fixb17-fuzz/fuzz.py`, 6 seeds × 400 refused and 400 true rows):
  - The first run missed glued `confirmapplication` and `completeapplication`. The family is now fixed (the noun is read inside a word), and those spellings are in the table.
  - Now 0 of 2,400 refused rows are missed.
  - The only true rows refused are `q=sender` when the person's search is something else (declared below).
  - Cost: about 2 ms for 80 fields. Past 100 fields the query is refused before it is read.

## B4: fixup F1

- `api/tests/browser/test_b17_signed_in.py`: `SignInPage`, `test_a_sign_in_heading_while_signed_in_ends_platform_signin_and_expires` and `test_a_sign_in_heading_while_anonymous_is_not_an_expiry` were added exactly as written, after `test_signin_required_while_anonymous_is_not_an_expiry`. Both pass.

## New finding (measured, not fixed: outside the four blockers)

`Network.setBlockedURLs` does not stop a page's WebSocket in Chromium 153.
- With `{"urls": ["ws://*", "wss://*"]}`, `{"urls": ["*b17-ws*"]}` and `{"urlPatterns": [{"urlPattern": "ws://*", "block": true}]}`, the handshake reached the server (close 1006 after it arrived).
- The same `*b17-ws*` pattern did stop a fetch (TypeError, not reached). Harness: `fixb17-probe/ws_block.py`.
- So the "socket block" is not a backstop. `SIGNED_IN_JS` is what refuses a socket. It held in the page and in a fresh frame on 153, and the journey now asserts `ws=err:SecurityError` on the gate's Chromium.
- I corrected the claims: the plan ("asks Chromium to block", and the open point no longer says the socket block holds in a fresh frame), the `_begin_signed_in` docstring (a declared open point), and the test docstring.
- Possible follow up for the orchestrator: Playwright's `context.route_web_socket` as a real backstop.

## Tests and results

- `ops/agent-test.sh --name fixb17 tests/browser -m "not docker" -n 2`: 3760 passed. After merging the combined branch's later head: 3762 passed. The two B17 files on their own: 239 passed.
- browserd's own tests are in `api/tests/browser` (they import `browser/` by path), so they are covered above. The gate lints browserd with `ruff check . ../browser` from `api/`.
- `cd api && uv run --frozen ruff check . ../browser` and `ruff format --check . ../browser`: clean (904 files formatted).
- `pnpm --dir web run typecheck` and `pnpm --dir web run lint`: clean, before and after the merge. Prettier is clean on the journey.
- `python3 .github/scripts/e2e_groups.py`: passes.
- `python3 .github/scripts/ci_plan_test.py`: 1 failure and 3 errors. These are the known ones: `MappingError: a dynamic import in api/app/apply/profile_router.py`, in a file this diff does not touch.
- `uvx pre-commit run --files <the 8 changed files>`: every hook passed, gitleaks included.
- Not run: the journey and the Docker marked tests. No image was built.

## Reworded PR text (B1 and B3)

### B1 (replaces the hardening paragraph)

A signed in website is searched in the person's own persisted context with JavaScript on. Before the first navigation the tab:
- bypasses the profile's service workers (`Network.setBypassServiceWorker`), so each request of the page reaches the route;
- asks Chromium to block ws:// and wss:// (`Network.setBlockedURLs`);
- gets an init script (`SIGNED_IN_JS`) that removes Worker, SharedWorker, WebSocket and WebTransport, and deletes `navigator.serviceWorker` itself.

Service workers are therefore bypassed and out of the page's reach. A page can neither register one nor message one the profile already holds; such a worker's own fetch would leave without the route. Measured in Chromium 153 with the reviewer's probe: before the line, `getRegistration('/').active.postMessage` made the worker POST to the site; after it, `'serviceWorker' in navigator` is false in the page and in a fresh frame, the message path throws TypeError, and no POST leaves. The page's own fetch still reaches the network. Unit evidence: `test_the_persons_context_bypasses_service_workers_and_blocks_sockets_first` asserts the line.

Declared open points:
- An event the browser itself delivers to a worker already in the profile (push, background sync, a cookie change) can still wake it, and that worker's requests do not pass the route.
- In Chromium 153, `Network.setBlockedURLs` stopped a fetch but not a page's WebSocket, so the init script is what refuses a socket there. The journey asserts `ws=err:SecurityError` on the gate's Chromium.

The journey cannot prove the service worker path. The members host is plain http, which is not a secure context, so no service worker can register there. The path is proved by the unit assertion and the Chromium 153 probe above.

### B3 (replaces "never to an apply, submit, delete, payment or sign out address")

Playwright's route lets only GET and HEAD leave. It never lets a request go to:
- an address whose path applies, submits, deletes or pays;
- an address whose path or query applies, submits or signs the person out.

The rule is `discovery.destructive`. The apply and submit words are read in every query key and value, split and glued: `?action=apply`, `?do=postuler`, `?easyApply=true` and `?op=submitApplication` are refused. A value that is the person's own search words (the frozen scope's query or a filter value, compared as folded words) is skipped, so a search for "apply engineer" is read. A query of more than 100 fields is refused. A frame's navigation stays within the frozen scope and is held to the same address rule, so `/logoff` or `/fr/deconnexion` in a frame no longer leaves.

Evidence in `api/tests/browser/test_b17_browserd.py`:
- refused table: 43 spellings × 11 places × 2 hosts;
- allowed table: 41 rows, including `?q=applied+scientist`, `?q=supply+chain`, `?category=payments`, `?event=page_view`, `?sort=updated_at`, `?saved=true`, `?hasApplied=false`;
- the `ROUND1` regression table.

Independent fuzz: 0 of 2,400 refused rows missed; the true rows refused are only the declared `sender` case.

Declared open points:
- A delete or payment word, or a save or update word, in the query only (`?action=delete`, `?action=save`) is not refused, because `?category=payments` and `?sort=updated` are reads.
- A query value that is the person's own search words leaves whatever its key. For example, a search for "apply" lets `?action=apply` leave.
- A value starting with a submit stem that is not the person's own words is refused. Examples are `?city=Sendai`, or `?q=sender` when the search is something else.
- A path based search whose words include apply (`/jobs/q-apply-engineer`) was already refused by the path rule, and still is.

## Self review (checklist items applied)

1. Heuristic: accept and refuse tables generated from the rule, an independent fuzz (2,400 refused rows, 2,400 true rows, both error rates above), fail closed past 100 fields, residue declared in the module docstring and the plan.
5. Absolute words: each "never", "every" and "cannot" added in the diff is backed by a test or a measurement above. The socket block claim was corrected after measuring it.
6. Size and time: query parse capped at 100 fields (refused past it), URL at 8000 characters (unchanged); the rule is linear, about 2 ms for 80 fields.
9. Tests can fail: B1 went red in the probe before the fix; B3's rows were measured leaving before the fix; the journey's assertions go red with the route disabled or the init script emptied (harness above); the journey has a positive control.
10. Hostile fake: the members website's pages now try every way out.
11. Merge: the combined branch's later head is merged in with no conflict, and the tests were rerun after it.
12. Probes rerun with variants: BroadcastChannel and a fresh frame for B1; glued, full width, zero width, French, German, Dutch, as a key and past the caps for B3.
16. Regression table: `ROUND1` in `test_b17_browserd.py`.
- Dashes: none in the added lines.
- Files outside the owned ones: none. Everything is B17's (SPEC-browser bullet, plan section, browserd tools, B17 tests, members pages, journey).

# B17 round 2 fixes

Re-checked head e09657169082ec8c9d78bea62b601764daea1321 (verdict-b17-r2.md). Fixed on branch `fix/b17-r2`, made from `claude/serene-gates-xxs89r` at cf91a8a0 (e0965716 plus the J17 fixes). Worktree `.claude/worktrees/fixb17b`, local head 417b22db3f735412b913eb5a53cc654faf11bbdf, not pushed. 9 files, +167 / -44. No conflict with origin/main (`git merge-tree`).

## R2-B1: WebSocketStream

- `browser/browserd/tools/actions.py`: `SIGNED_IN_JS` removes `["Worker", "SharedWorker", "WebSocket", "WebSocketStream", "WebTransport"]`. The `_begin_signed_in` docstring lists it.
- `api/tests/browser/test_b17_browserd.py`: the names loop asserts each of the five names, quoted, in `SIGNED_IN_JS`, then `"register"`.
- `tests/browser_pages/server.py` `B17_PROBE_JS`: `attempt("wss", () => new WebSocketStream("ws://" + location.host + "/j15/b17-wss"))`. The module docstring lists it.
- `web/e2e/browser-b17.spec.ts`: `/j15/b17-wss` is in `PROBES`, and each marker must say `wss=err:SecurityError`, beside `ws` and `worker`. No new path holds "send".

## R2-B2: inline sandboxed srcdoc frames

### Where the switch applies

Switches are per Chromium launch, per lease: `Chromium.spawn` calls `switches(request, ...)` with the lease's own `Request`. A discovery runs only in a `sweep` lease: `actions.run` refuses the discovery marker for any other purpose. The scope arrives later, with the first tool call, so the launch cannot tell whether the search is signed in. The flag therefore goes on every sweep lease and on no other lease: apply, sign in, takeover and profile edit leases keep Chromium's default.

### Changes

- `browser/browserd/chromium.py` `switches()`: `sandboxed = ",IsolateSandboxedIframes" if request.purpose == "sweep" else ""` is appended to the one `--disable-features=Translate,WebRtcHideLocalIpsWithMdns` switch. The comment and the docstring say why, and why sweep only.
- `api/tests/browser/test_browserd_unit.py` `test_switches_keep_the_sandbox_the_proxy_and_the_real_user_agent`: for every purpose in `PURPOSES` there is exactly one `--disable-features` switch. It is `Translate,WebRtcHideLocalIpsWithMdns` plus `IsolateSandboxedIframes` for `sweep` only.
- `tests/browser_pages/server.py` `B17_PROBE_JS`: after the page's marker, it adds a `<div hidden>` built with the DOM, holding an `<iframe sandbox="allow-scripts">` whose srcdoc script tries three things, each in its own try:
  - a no-cors POST fetch to `/j15/b17-sbx-post`;
  - `new WebSocket` to `/j15/b17-sbx-ws`;
  - `new WebSocketStream` to `/j15/b17-sbx-wss`.

  It runs on the members host only; J15's own pages are unchanged.
- `web/e2e/browser-b17.spec.ts`:
  - The three paths are in `PROBES`, so `probed(seen)` must stay empty in the search.
  - Test 1's positive control now waits for both `POST /j15/b17-post` and `POST /j15/b17-sbx-post` from A's own sign in session. This proves the hidden frame runs its script on the gate's Chromium, so the search's check can fail.
  - The header and the comments say so.
- Text: the `actions.py` module docstring and the comment above `SIGNED_IN_JS`, the `discovery.py` module docstring, the SPEC-browser B17 bullet and switches bullet, and the plan section (as built, journey and open points).

### Refs e5 to e8

My harness takes `pages.snapshot_for_ai`, browserd's own snapshot call, on Playwright 1.55. For each of `/j15/`, `/j15/search?q=engineer&location=Paris&page=2` and `/j15/jobs/robot-engineer.html`, the member page's snapshot (probe and hidden frame) is identical to J15's own page (no probe):
- in the person's own session: True for all three;
- in the signed in search with the sweep switches: True for all three.

Refs per page: 6, 6 and 8. So e5 to e8 do not move.

## Follow up 1: percent encoded path

- `browser/browserd/tools/discovery.py` `destructive`: the path is `unquote(parts.path)`, decoded once before its words are read. A `%` still in it after that decode is refused. `ends_session` reads the decoded path too: `/fr/d%C3%A9connexion` used to leave, because its words were `d`, `c3` and `a9connexion`.
- `test_a_percent_encoded_path_is_read_as_the_server_reads_it` has 44 rows, each on 2 hosts, and checks both `destructive` and `signed_request(..., document=False)`. A document with a `%` in its path stays refused.
  - **30 refused:** the reviewer's `/jobs/42/ap%70ly` and `/%61pply`, then:
    - upper case and trailing slash;
    - fully encoded, lower and upper case hex;
    - an encoded `/` and `?`;
    - easy_apply and easy apply;
    - postuler, candidater, candidature, bewerben, solliciteren;
    - submit, envoyer, save, delete, checkout;
    - logout, sign_out, déconnexion, abmelden;
    - a zero width space, a soft hyphen, a full width a;
    - encoded twice, a bad escape, and the declared `100%25.png`.
  - **14 allowed:** `data%20engineer`, `robot%2Bengineer`, ingénieur données, münchen, Japanese and Russian words, `applied%2Dscientist`, `supply%20chain%20lead`, `log%20analyst`, `sign%20language%20teacher`, `out%20of%20office`, `logo%402x.png`, `Inter%20Var.woff2`, `/jobs/42`.
- With browserd reverted, 25 of the 30 refused rows go red. The other 5 were already refused through a raw word.

## Follow up 5: test name

`test_the_persons_context_bypasses_service_workers_and_blocks_sockets_first` is now `test_the_persons_context_bypasses_service_workers_and_removes_sockets_first`. A comment in the test says that `Network.setBlockedURLs` stops no WebSocket in Chromium 153 (measured), so the init script is what refuses a socket. The call is kept and asserted unchanged.

## Probe results (Chrome for Testing 153.0.8010.12, Playwright 1.55.0, browserd's real `Tab.begin_discovery`, signed in scope)

The copies are in `(the session scratchpad)`. `probe4.py` and `probe6.py` are the reviewer's, changed in one way: their Chromium gets the `--disable-features` switch from this worktree's own `chromium.switches()` for a lease purpose (`FEATURES_OF`), instead of `EXTRA_CHROME_ARGS`. `members_probe.py` is round 1's harness: server.py's real Handler, the real signed in discovery, new probe paths, browserd's switches per phase, and snapshots. Every run was one foreground process, closed afterwards.

1. **`probe4.py`, sweep switches** (`Translate,WebRtcHideLocalIpsWithMdns,IsolateSandboxedIframes`). All 7 frame shapes show `ws=err:SecurityError, wss=err:SecurityError, post=err:TypeError, sw=false`: sbx srcdoc, sbx srcdoc by innerHTML, allow-same-origin, plain srcdoc, sbx data:, sbx blob:, and sbx `src`. The `src` frame tries ws and post only. The phase 2 server log holds only `GET /search` and `GET /p/frame.html`.
2. **`probe4.py`, apply_fill switches** (no flag, the control). The two inline sandboxed srcdoc frames show `ws=made, wss=made, post=sent, sw=true`. The log holds `POST /p/post-sbx`, `GET /p/ws-sbx`, `GET /p/wss-sbx`, `POST /p/beacon-sbx`, and the same four for `-html`. The other five shapes stay refused.
3. **`probe6.py`, sweep switches.** In the page itself, `main_wss` and `main_ws` are `err:SecurityError`, and the 7 frames are as in 1. The log holds only `GET /search` and `GET /p/frame.html`.
4. **`members_probe.py` as built.**
   - A's own session (sign in switches) reached all 10 probe paths, among them `/j15/b17-wss` and `/j15/b17-sbx-post`, `-ws` and `-wss`.
   - The signed in search over `/j15/`, page 2 and the posting:
     - no probe path, GET only;
     - 3 markers, each with `ws=err:SecurityError&wss=err:SecurityError&worker=err:SecurityError`;
     - snapshots identical to J15's.
5. **`members_probe.py no_flag`** (the search launched with the sign in switches). `/j15/b17-sbx-post`, `/j15/b17-sbx-ws` and `/j15/b17-sbx-wss` reached the site, and the methods were GET and POST. The journey would go red.
6. **`members_probe.py old_script`** (round 1's names list). `/j15/b17-wss` and `/j15/b17-sbx-wss` reached the site, and the marker said `wss=made`. The journey would go red.

Not measured: Debian's Chromium 156. The journey's new probes measure it at the gate. If the flag has no effect there, test 2 goes red on `/j15/b17-sbx-*`, and the owner decides on the declared alternative of the verdict.

## Tests and results

- `ops/agent-test.sh --name fixb17b tests/browser -m "not docker" -n 2`: 3806 passed.
- With `browser/` reverted, the changed tests go red: the switches test, the names test and 25 percent rows.
- `cd api && uv run --frozen ruff check . ../browser` and `ruff format --check . ../browser`: clean, 906 files. `tests/browser_pages/server.py` is outside that scope: it had 7 ruff findings before the change and has the same 7 after.
- `pnpm --dir web run typecheck` and `pnpm --dir web run lint`: clean. Prettier is clean on the journey.
- `python3 .github/scripts/e2e_groups.py`: passes.
- `python3 .github/scripts/ci_plan_test.py`: 1 failure and 3 errors, the known `MappingError` in `api/app/apply/profile_router.py`.
- `uvx pre-commit run --files <9 changed files>`: every hook passed, gitleaks included. The commit hook passed too.
- Dash scan of the added lines: none.
- Not run: the journey, the Docker marked tests and the image build. The orchestrator runs the browser group.

## PR text for the switch (owner posture change)

**Chromium switch for search leases (B17).** A sweep lease now starts Chromium with `--disable-features=Translate,WebRtcHideLocalIpsWithMdns,IsolateSandboxedIframes`. Every other lease (apply, Apply myself, Full auto, profile edit, sign in, takeover) keeps `Translate,WebRtcHideLocalIpsWithMdns` and Chromium's default isolation of sandboxed frames.

*Why.* By default Chromium runs an inline sandboxed frame (`<iframe sandbox="allow-scripts" srcdoc="...">`) in a process of its own. Playwright attaches to that process after the frame's inline script has started, so that script ran before the signed in search's init script and route. Measured in Chromium 153: from such a frame a no-cors POST, a WebSocket, a WebSocketStream and a sendBeacon reached the website with the person's session, and `navigator.serviceWorker` was there. With the switch, the frame stays in its page's process and gets the init script and the route like every other frame. In the seven frame shapes of the review's probe, nothing leaves but the `src` frame's own GET.

*Why every sweep lease and not only signed in searches.* Switches are fixed when the lease's Chromium starts, from the lease's purpose. Whether a search is signed in is known only when its first tool call arrives. A search runs only in a sweep lease, so the switch goes on every sweep lease. The anonymous search runs with JavaScript off, but without the switch an inline sandboxed frame still ran its script in Chromium 153 (the round 2 re-check measured its POSTs and sockets reaching the site); with the switch the frame stays in the page's process and JavaScript is off in it too, so the switch closes that hole for anonymous searches as well. A sweep lease is not only a native search: the LinkedIn, Google careers, VIE and career site readers run in sweep leases with JavaScript on, and there the switch changes only that a same site sandboxed frame runs in its page's process again (cross site frames keep site isolation).

*Cost.* A sandboxed frame of a search page shares its page's process: Chromium's process isolation between a page and its own sandboxed frames is off in search leases. Searches only read pages, and the route and init script then reach the frame.

*Measured on the gate's Chromium by the journey.* The members website's pages add a hidden inline sandboxed frame that tries a POST, a WebSocket and a WebSocketStream at `/j15/b17-sbx-*`. In A's own sign in session its POST must reach the site, the positive control. In the search none of the three may reach it. If a future Chromium ignores the switch, `browser-b17.spec.ts` goes red.

Unit evidence: `test_switches_keep_the_sandbox_the_proxy_and_the_real_user_agent` asserts the one `--disable-features` switch per purpose.

## Self review (checklist items applied)

1. **Heuristic:** the percent rule has a 44 row table (30 refused, 14 true inputs) over two hosts. It fails closed: a `%` left after one decode is refused. Declared cost: an encoded percent sign in a subresource path (`100%25.png`) is refused.
5. **Absolute words:**
   - "every sweep lease" and "every other lease keeps Chromium's default" are proved by the per purpose switch test.
   - "every frame of the page, an inline sandboxed one included" is proved on 153 by `probe4.py` and `probe6.py` (7 shapes). On 156 it rests on the journey, as said in the plan.
   - "nothing it adds shows in the page's snapshot" is proved by the snapshot comparison.
9. **Tests can fail:**
   - the unit tests go red with `browser/` reverted;
   - the journey's new probes go red without the flag (`no_flag`) or without WebSocketStream in the list (`old_script`);
   - the positive control proves the hidden frame runs.
10. **Hostile fake:** the members pages now also try a WebSocketStream and a sandboxed frame.
11. **Merge:** no conflict with origin/main.
12. **Probes rerun with variants:** probe4 and probe6 with the real switches of two purposes; percent rows in other languages, case, Unicode format characters, full width, and double encoding.
16. **Regression table:** the reviewer's two percent rows are marked "never deleted" at the head of the table.
- **Dashes:** none.
- **Files outside the owned ones:** `browser/browserd/chromium.py` (one switch, B17's), and the SPEC-browser switches bullet (one parenthesis). The rest is B17's.
