# Spec: browser

Module of [CAPABILITY-MAP.md](CAPABILITY-MAP.md). Depends on `platform`; used by `apply` and by the jobs sweep for boards without an API. Umbrella issue: #67. Plan: [tasks/browser/todo.md](tasks/browser/todo.md). The owner's decisions of 2026-10-14 are taken (below); nothing is built.

## Objective

Each person of the app has one real browser on the Jetson, with their own logins, which an agent can drive and the person can watch live from any device and take over at any moment: to solve a captcha, to sign in to LinkedIn, Google or an applicant tracking system, or to finish a form by hand. The same browser service also reads the job sources that have no API (LinkedIn, Google Careers, company career sites, Business France VIE). It is the Jetson's version of what the command line does on the owner's Mac today: a real Chrome with a persistent profile, driven over CDP, headed, human paced.

User stories:
- As a person, I open "Your browser" in Account and see my browser live; I sign in to the sites I use once, and I stay signed in next time.
- As a person, while the agent fills an application I watch it in the job page, and I click "Take over" to type myself; "Give back" lets the agent continue.
- As a person, I am told when my browser needs me ("A captcha needs you on Example Robotics"), and the link opens the live view on that tab.
- As a person, I click "Forget all logins" and my browser starts empty next time.
- As a person, when the agent reaches a site that needs an account, I am told, I sign in or sign up myself in the live view, I click "I'm signed in", and the agent goes on where it was; the next time, the site already knows me.
- As a person, I ask the agent to complete my profile on a site I use (Workday, LinkedIn, a company portal) from the profile of the app (SPEC-apply, "Complete my profile on a site").
- As the owner, I know that one person can never see, drive or read another person's browser.

## What the command line teaches (read in `engine/stage.py`, `engine/vie.py` and `AGENTS.md` of the parent folder)

| On the Mac today | On the Jetson |
|---|---|
| Google Chrome, `channel="chrome"`, started by hand with `--remote-debugging-port=9333` and a persistent `--user-data-dir`, `--disable-blink-features=AutomationControlled`; Playwright attaches with `connect_over_cdp` | Google Chrome has no Linux arm64 build. Chromium arm64 started by the service itself (never by Playwright's `launch`, which adds automation switches), with the same switches, one user data directory per person; Playwright attaches over CDP inside the browser container |
| Headless Chromium fails Ashby's invisible reCAPTCHA; what passes is a real browser, headed, a persistent profile, human paced typing, scrolling, hover and a few seconds of dwell before Submit | Headed Chromium on a virtual display (Xvfb), a persistent profile per person, the same pacing written once in the tool layer so no agent can type faster |
| Every browser action through Playwright (hard rule 8); plain HTTP from that network is scored as a bot | Every browser action through the tool layer of this module; the app never fetches an application page with plain HTTP |
| The person watches the Chrome window on the Mac | The live view: noVNC in the app's page |

## Owner decisions (taken on 2026-10-14)

1. **Accounts on job sites: the person signs in, the browser profile remembers.** The owner's words: "The agent open playwright, user sign in or log in, we save all the playwright profile per user so next opening will be connected. User can ask to complete his profile on the website with all informations." Hence: the app never chooses a password and never creates an account by itself. When a site needs an account, the agent opens the page in the person's browser session on the Jetson, and the person signs in or signs up himself in the live view (the sign in hand off, below). The browser profile of each person (cookies, local storage) is persisted on the Jetson, so the next run is already connected. A new action, "Complete my profile on this site", lets the person ask the agent to fill the candidate profile of a site from the app's profile (SPEC-apply), with the guardrails of a form fill.
2. **LinkedIn: full use.** The owner's words: "Full use". The app searches LinkedIn for jobs and submits Easy Apply from the person's own signed in session, in Review first and in Full auto too. Kept: human pacing, a low request rate (one sweep a day at most, 5 result pages, 10 to 30 seconds per page; one Easy Apply every 10 minutes and 15 a day at most) and the per company and per day guards of the apply module. LinkedIn stays a per person switch, off by default, since the app has several users. Turning it on shows one plain sentence, which the person accepts once: "LinkedIn's terms forbid automated use of its site, and your account may be limited or closed; the app keeps to the pace of a person, but the risk is yours." The acceptance is recorded with its date and the version of the sentence, and without it no LinkedIn work starts (search, Easy Apply, profile).

## Which browser runs on the Jetson

- **Chromium for Linux arm64, headed, on Xvfb.** Debian's `chromium` package (bookworm security updates, a real stable channel build, arm64 native), pinned by version in the image and rebuilt when Debian publishes a security update. Playwright's own Chromium build for arm64 is the fallback if B1 finds Debian's unusable over CDP. Either way the browser is started by the service, not by Playwright.
- **Switches** (as `stage.py`, plus what a container needs): `--user-data-dir=/profiles/<profile id>`, `--remote-debugging-address=127.0.0.1 --remote-debugging-port=<per session>`, `--no-first-run --no-default-browser-check`, `--disable-blink-features=AutomationControlled`, `--window-size=1440,900`, `--lang` from the languages of the `identity` section and `TZ` from `search.sweep_schedule.timezone` (it exists, default `Europe/Paris`; the identity section has a city and a country but no time zone, so a person with no sweep schedule gets the zone derived from the country), `--password-store=basic` and the preference `credentials_enable_service=false` (the browser never saves a password), `--disable-features=Translate`, downloads into a per session directory nobody reads. Never `--no-sandbox` (below), never `--headless`.
- **Display:** one Xvfb screen of 1440 by 900 at 24 bits, the Chromium window maximised on it, one window per session. Fonts: Liberation, Noto (Latin, CJK subset), Noto Color Emoji, so pages render as on a desktop.

## Memory on the Jetson

- **Assumed figure: one live browser session costs about 950 MB at peak**: Chromium with one heavy application page 650 to 750 MB (browser, GPU, network and one or two renderer processes), the Playwright driver and `browserd` about 150 MB, Xvfb about 40 MB, x11vnc and websockify about 60 MB. **Container limit: 1,152 MB** (`mem_limit: 1152m`, `shm_size: 128m` counted inside it). With the 3,968 MB of `compose.yml` today, the stack totals exactly 5,120 MB, the 5G budget of SPEC-platform, instead of the 5,504 MB the reservation of 1,536 MB would cost.
- **Hence one Chromium at a time on the whole Jetson.** Sessions are leased one at a time; a second request waits in a queue (below). Two at once would need about 1.9 GB, which the Jetson does not have. Tab count per session is capped at 3; a page that pushes the container past 90 % of its limit is closed by `browserd` (the tab, not the session), with the reason recorded.
- **B1 measures before anything else is built**: peak resident memory of the container for a blank page, an Ashby form, a Greenhouse form, a Workday form and LinkedIn's job search, each held 2 minutes with the live view connected. If the Workday peak passes 1,100 MB, B1 proposes the change (a lower parser limit, measured by ingestion, or Workday out of reach) and the owner sees it in the PR before B2 starts.

## The service

```
browser container (image jobapp-browser, arm64, Debian slim)
  tini -> browserd (Python, asyncio)            control API on :9300 (network browsing), shared secret
            Xvfb :1                            virtual screen
            x11vnc -> :5900 (localhost)        live view source, view only unless the person takes over
            websockify :6080 (network browsing), noVNC static files, token per lease
            chromium (one at a time)           CDP on 127.0.0.1 only, per lease
            Playwright (Python, connect_over_cdp to localhost)   the tool layer
```

- **`browserd`** is the only process anything outside the container talks to. Its control API (`BROWSER_CONTROL_SECRET` in `.env`, known to the api and the worker only): `POST /leases` (person id, profile id, purpose, automation on or off) answers a lease id and a lease token once Chromium is up; `DELETE /leases/{id}`; `GET /leases/current`; the **tool API** of the current lease (`POST /leases/{id}/tools/{name}`, lease token required), and `PUT /leases/{id}/files/{name}` to place a document for upload. CDP never leaves the container: the agents never get a CDP address.
- **Leases** are kept by the api in Redis (one key, compare and set), mirrored by `browserd`, which refuses a second lease. Purposes and their priority: `takeover` (the person in the live view) > `apply_fill` and `apply_submit` waited on by a person > `full_auto` > `sweep`. No preemption: a running lease ends first. Limits: 15 minutes of active time for an apply lease, 30 minutes for a person's session, 5 minutes without any input or tool call ends a lease. While a lease is `waiting_person` (the sign in hand off) neither the maximum nor the idle timer runs: the wait has its own deadline of 30 minutes, and the lease then resumes with the active time it had left, so a hand off in the middle of an application never ends the lease at 15 minutes. The maximum is a setting per purpose, because a Workday wizard at human pacing may need more than 15 minutes of active time: A15 measures it and proposes the figure; the queue page says "Your browser starts in about 6 minutes".
- **Chromium lifecycle:** started for a lease on the person's profile directory, closed at the end of the lease (profile flushed to disk). A crash or a hang (no CDP answer in 10 s) ends the lease with `browser_crashed`; the next lease starts a fresh process.

## How agents drive it

- **Decision taken: a narrow tool layer, not the raw Playwright MCP server.** The capability map names "Playwright MCP endpoint". The official `@playwright/mcp` server exposes `browser_evaluate` and `browser_run_code` (arbitrary JavaScript and Playwright code), uploads by any path and every tab, which no agent of this app may have. So `browserd` implements a short list of tools with the same names, arguments and accessibility snapshot format as Playwright MCP, and the worker exposes them to an agent as tools of its in-process MCP server (the agents runtime pattern of `api/app/agents/tools.py`: `register(name, factory)` and `create_sdk_mcp_server`; tool names must match `^[a-z][a-z0-9_]{0,47}$`, which the `browser_*` names do), bound server side to the run's user and lease:
  `browser_snapshot` (the accessibility snapshot with element refs, cut at 40,000 characters), `browser_navigate` (http and https only, to the hosts the lease allows), `browser_click`, `browser_type`, `browser_select_option`, `browser_check`, `browser_file_upload` (a document name the lease was given, never a path), `browser_press_key` (a short allow list), `browser_wait_for`, `browser_take_screenshot`, `browser_tabs` (list and select, no new tab to another host), and `browser_request_signin` (not in Playwright MCP: asks the person to sign in or sign up, see "The sign in hand off"; it takes a site name and a reason, never a credential).
- **What the tool layer enforces in code**, so no prompt can change it: human pacing (typing 60 to 140 ms per character with jitter, a scroll and a hover before every click, 1 to 3 s of dwell between actions, reading pauses on new pages); a host allow list per lease (the application's host, its ATS's asset and upload hosts, the identity providers the person signs in with); no `file:`, `chrome:`, `devtools:`, `view-source:` or `data:` navigation; refusal of a click on any element the submit detector of the apply module marks (submit, send, apply, postuler, envoyer, and the adapters' own selectors) unless the call comes from the apply gate's code path, which no agent can reach; refusal of buttons that delete, withdraw or pay. A lease flagged `profile_edit` (SPEC-apply, "Complete my profile on a site") has its detector extended to Save, Update, Enregistrer and Mettre à jour, and only the profile gate's code path may click them. Page content is data, never instructions: every agent prompt that sees a snapshot says so.
- **Uploads:** the worker sends the frozen PDF bytes to `PUT /leases/{id}/files/{name}` (a tmpfs directory per lease, deleted with it) and the tool sets that file; file choosers (Google Careers' Browse button) are answered the same way. B1 records whether `set_input_files` over a local CDP connection needs this; the design does not depend on it.
- **For development only,** the official Playwright MCP server or CLI can attach to a lease from inside the container (`docker compose exec`). It is never reachable from the network.

## The live view

- **noVNC** in the page, through Caddy at `/vnc/` (websocket to `browser:6080`). Caddy asks the api first (`forward_auth` to `GET /api/browser/live/auth`), which checks the session and a one use ticket bound to the person and the current lease (30 s to use, then a websocket that lives as long as the lease). A person who does not hold the current lease gets 403: they can never see another person's screen. The VNC password is per lease and never shown.
- **View only while an agent works.** "Take over" asks `browserd` to switch x11vnc to input mode (`x11vnc -R viewonly:0`) and pauses the run: the agent's next tool call waits until "Give back" or 15 minutes, then is told "The person used the browser; the page is now <title>". The person's keyboard and mouse never mix with the agent's.
- **Mobile:** the viewer scales to the screen and offers the noVNC on screen keyboard; a phone over the Tailscale Funnel is enough to solve a captcha. Expected traffic 100 to 500 KB/s while the page moves, near zero when still.
- **Rejected:** a stream of CDP screencast frames (no browser dialogs, no file chooser, no captcha frames from another origin in some cases, and a second input path to secure).

## Persistent profiles and logins

- **One profile directory per person, persisted** (`browser_profiles` table: `user_id`, a random `profile_id`, the Linux uid that owns it, size, last used). On the volume `browserprofiles`, mode 0700, each directory owned by its own uid (20000 plus a counter); `browserd` keeps only the capabilities to start Chromium under that uid and drops them in the child. **Every lease of the person starts on that same directory**, whatever its purpose (apply, sweep, hand off, complete my profile, "Look at my browser"), and ends by closing Chromium cleanly so the profile is flushed. Logins persist as cookies and local storage in that directory, like a desktop browser, so the next opening is connected. Cookies without an expiry date (session cookies) are kept across restarts with the setting "continue where you left off" (`session.restore_on_startup`); B7 proves it with a fixture that sets a session cookie.
- **The sign in hand off** (the way a person signs in, whoever needs it). An agent that reaches a login or sign up wall, or an adapter that sees one, calls `browser_request_signin(site, reason)`. Then: the lease keeps its place and waits for the person (`waiting_person: signin`, the idle timer suspended, 30 minutes at most); `browserd` notes the tab's address, closes Chromium and starts it again on the same profile, at that address, **without any debugging port** and with the live view in input mode, so Google, LinkedIn or an ATS sees an ordinary browser while the person types; the person is told ("Sign in to Example Portal, then click I'm signed in") with a link that opens the live view on that tab; he signs in or signs up himself, with a password, "Sign in with Google" or an e-mail code, and clicks **I'm signed in**; `browserd` closes Chromium, starts it again with CDP on the same profile at the same address, and the waiting tool call answers "The person signed in; the page is now <title>". The adapter then checks that the wall is gone, else it asks again. "Cancel" or 30 minutes without a click ends the hand off: the lease is released and the work waits as `needs_person` in the apply module's queue. The app never sees what is typed: keystrokes go from the viewer to the browser, nothing is logged.
- **"Sign in to a site"** in Account is the same mechanism started by the person, without an agent: a session with no debugging port, at an address he types or picks.
- **What is never stored:** passwords (the browser's password manager is off; the app has no password vault), payment cards (autofill of cards off). The app never chooses a password and never creates an account by itself.
- **Cookies on Linux are protected by a fixed key** when no keyring exists, so the volume is as sensitive as the logins. **Encryption at rest, stated plainly:** the app adds none. The Jetson's disk is not encrypted (SPEC-platform plans no disk encryption), so the profile files are readable in the clear by `root` on the Jetson, that is, by the administrator who holds the shell (today the owner), and by anyone who has the physical disk. Inside the stack only `browserd` and the Chromium of that person's own uid (mode 0700) can read a profile; no other service mounts the volume, and no route of the api returns a profile file. It is excluded from the nightly backup (losing it costs the person their logins, nothing else) and from any dump, and it is deleted with the account (the cascade queues the directory's removal, B3 and B7). If the owner wants the disk encrypted, that is a platform decision, not this module's. "Forget all logins" deletes the directory; the next lease starts empty and the sites ask again. A profile over 1 GB is trimmed of its caches at lease end.

## Isolation and network

- **Between people:** one lease at a time, one profile directory per uid, a lease token per lease, the live view ticket per person and lease, uploads per lease in tmpfs. The worker passes the run's user id to `browserd`, never a profile path. Tests in every task prove that person B gets 404 or 403 on A's lease, view, files and profile.
- **Chromium's own sandbox stays on.** The container gets the seccomp profile that permits user namespaces for Chromium (no `SYS_ADMIN`, no `--privileged`), `no-new-privileges`, a read only root, `cap_drop: ALL` plus `SETUID` and `SETGID` for `browserd`. B1 checks that the Jetson kernel allows unprivileged user namespaces; if not, the setuid sandbox helper is the fallback, never `--no-sandbox`.
- **Networks:** `browser` is on a new network `egress` (the only one with a route out, used by no other service) and on `browsing` (internal: `browserd`'s control port and websockify, reached by the api, the worker and Caddy). It is not on `default`, so it cannot resolve db, redis or the api. A host firewall rule (`DOCKER-USER`, installed by `deploy.sh`) drops traffic from the `egress` subnet to private, link local and carrier grade NAT ranges (the home network, the router, the Jetson itself, the tailnet's 100.64.0.0/10), so a hostile page can reach the internet and nothing else.

## Bot detection: what remains and what reduces it

- **The address is good:** the Jetson goes out through the owner's home connection, a residential address, not a datacenter. What remains: every person of the app shares that one address, so the app paces per host across people (apply: one submission per host every 3 minutes, sweeps one page every 5 to 15 s).
- **The fingerprint is not a desktop's:** `X11; Linux x86_64` in the user agent with the client hint architecture `arm` (what Debian's arm64 Chromium reports, B1; never faked, a fingerprint that lies is not allowed), the brand "Chromium" in client hints instead of "Google Chrome", WebGL through a software renderer (no GPU in Xvfb). These are honest values; faking them is refused (a mismatched fingerprint is easier to spot than a rare one). Reduced by: a headed browser with a real window, a persistent profile that ages like a person's, the person's language and time zone, cookies of sites the person signs in to (a signed in Google account raises reCAPTCHA scores), no automation switches, no `navigator.webdriver`, the human pacing of the tool layer.
- **What the design does when detection wins:** a captcha or a block page ends automatic work on that page and asks the person, in the live view; Full auto sends the application to the review queue. The app never uses a captcha solving service.

## The second use: finding jobs where there is no API

The jobs sweep (SPEC-jobs) covers Ashby, Greenhouse and Lever by API. Sources that need a browser become **browser sources**, declared in the person's `search` section beside the boards, swept in `sweep` leases at night or when the browser is idle, results upserted through the jobs module's own `add` path (status `found`, scored, never apply rules applied) with the posting text saved:
- **Google Careers** and **Business France VIE** (mon-vie-via): public result pages read by deterministic extractors, no model.
- **Company career sites** without a known ATS: the finder agent reads listing pages with the read only tools (`browser_navigate`, `browser_snapshot`), a budget of 20 pages per site per sweep, and returns postings as structured rows that code validates.
- **LinkedIn:** searched from the person's own signed in session after the opt in of owner decision 2, under the pacing of B10.
Every sweep lease is low priority and ends at once when a person asks for their browser.

## API

| Method and path | Does |
|---|---|
| GET /api/browser | the person's browser: profile exists, size, last used, current lease (theirs only), queue position |
| POST /api/browser/sessions `{purpose: "signin" \| "look"}` ; DELETE /api/browser/sessions/current | start or end a session of the person in the live view |
| POST /api/browser/live/ticket ; GET /api/browser/live/auth (Caddy only) | one use ticket for the viewer, and its check |
| POST /api/browser/takeover ; POST /api/browser/giveback | switch input between the agent and the person |
| DELETE /api/browser/profile | forget all logins |
| POST /api/browser/handoff/done ; POST /api/browser/handoff/cancel | "I'm signed in" (restarts Chromium with CDP at the same address) and "Cancel" of a sign in hand off; the state itself (`waiting_person: signin`, the site, the minutes left) is part of `GET /api/browser` |
| GET and PUT /api/browser/linkedin | the LinkedIn switch of the person: off by default; turning it on records the acceptance of the sentence (date and version) |

## Frontend

Account: "Your browser" card (status, Sign in to a site, a slot for "Complete my profile on a site" that the apply module fills, the LinkedIn switch with its sentence, Forget all logins with a confirmation that names what is lost). A hand off banner wherever a run waits for a sign in (the job page, the queue, the app shell): the site, "Open the live view", "I'm signed in", "Cancel". A live view component (`<LiveBrowser lease=... />`) used by Account and by the job page of the apply module: the screen, Take over and Give back, full screen, a status line ("The assistant is filling the form", "Waiting for you: a captcha"). The queue position while waiting. Mobile friendly.

## Testing strategy

- Unit: lease state machine and priorities, ticket signing and expiry, host allow list, the submit and danger detectors, pacing bounds, profile uid allocation.
- Integration: the real browser container in a test stack (`tests/browser.compose.yml`) on the Mac (arm64 Docker, the same image as the Jetson) against the fake ATS pages of the apply module; isolation between two people; network tests from inside the container (db, redis, the router's address and the host refused; a public test page reached through a stub).
- Journeys (Playwright on the Mac driving the app's page, which shows the live view of the container's Chromium).
- On the Jetson: memory and start time measured (B1, Checkpoint B).

## Boundaries

- **Always:** one session at a time; the person can watch and take over; the sandbox on; pacing in code.
- **Ask first:** a second concurrent browser; any captcha service; LinkedIn beyond the owner's decision; giving an agent a tool outside the list above.
- **Never:** CDP outside the container; `--no-sandbox` or `--headless`; storing a password, choosing one or creating an account for a person; one person's profile, screen or files reachable by another; a fingerprint that lies.

## Success criteria

1. On the Jetson, the owner opens "Your browser" from his phone, signs in to a site, and is still signed in the next day.
2. An agent fills a fake form in the browser while he watches, he takes over, gives back, and the agent continues.
3. The stack stays within 5G with the browser running a heavy page (measured).
4. No person can reach another's browser, and the browser cannot reach the home network or the stack's databases (tests).
5. A run that meets a login wall waits; the owner signs in himself in the live view, clicks "I'm signed in", the run goes on, and the next day's run is already connected to that site.
