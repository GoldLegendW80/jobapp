# Implementation Plan: identity

Spec: [SPEC-identity.md](../../SPEC-identity.md). Tasks: [todo.md](todo.md). Platform plan stays in [../plan.md](../plan.md).

## Overview

Invite-only accounts with server-side sessions, an admin area, and the `current_user` dependency every later module filters by. Built in vertical slices: each task ships schema, API, UI and tests for one capability.

## Architecture decisions

- **Server-side sessions in Postgres**, opaque cookie token, SHA-256 stored. Revocation is a row delete; no JWT.
- **Default deny.** A router-level dependency makes every `/api/*` route require a session unless it is on an explicit public allowlist (health, login, invite check and accept). A test enumerates `app.routes` to prove it.
  As built in I2: `deps.default_deny` is an app-wide dependency (`FastAPI(dependencies=...)`), so it also covers routes added later; `deps.PUBLIC_ROUTES` lists (method, path) pairs; `deps.assert_default_deny` runs in `create_app` and again at startup and refuses an app where an API route skips the guard, anything else is served under `/api`, a public route is reused under another prefix, or an allowlist entry names no route. Logout is on the allowlist too: it only ends the session its cookie names and answers 204 without one, so it stays idempotent.
- **Cookie writes in one place.** Handlers and dependencies only record the new cookie state; a middleware writes `Set-Cookie` with the flags (Secure from the request scheme after `--proxy-headers`, or `APP_ENV=production`). The sliding refresh therefore reaches the browser whatever the handler returns.
- **Logs.** The request log records the route template (`/api/auth/invites/{token}`), unmatched link paths are masked, SQL parameters are hidden (`hide_parameters`), 422 bodies never echo input, and Caddy's access log masks link tokens in the URI and the Referer.
- **CSRF by custom header** `X-Requested-With: jobapp` on non-GET plus SameSite=Lax; enforced in middleware.
- **No email.** Links are shown once to the admin, who shares them.
- **Frontend auth state** from `GET /api/auth/me` through TanStack Query; a global 401 handler clears it and routes to `/login?next=`.
- **Rate limiting in Postgres** (`login_attempts`), no Redis dependency for auth, so it survives restarts and needs no new service.

## Task list

- [x] I1: Identity schema, security primitives and first admin from the CLI
- [x] I2: Sign in, sign out, session cookie and default-deny API
- [x] I3: Frontend shell: router, login page, protected routes, sign out

### Checkpoint I-A
- [x] Harald's admin created by CLI on the Jetson can sign in and out through the public URL

- [x] I4: Invites: admin creates a link, friend accepts and is signed in
- [x] I5: Admin users page: list, disable, role, last-admin protection
- [x] I6: Account password change and reset links

### Checkpoint I-B
- [x] Two-user journey works; isolation fixture proves cross-user reads fail

- [x] I7: Login throttling and auth hardening
- [x] I8: End-to-end identity journey on the Jetson, screenshots of every page

### Checkpoint I-C
- [x] All 5 success criteria of SPEC-identity.md verified

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Secure cookie breaks local http dev | Med | `Secure` set from `X-Forwarded-Proto` / APP_ENV; tests cover both |
| Tunnel URL changes so invite links break | Med | Links are relative to whatever origin the admin uses; the admin UI builds the link from `window.location.origin`; documented |
| argon2 hashing is slow on the Jetson | Low | Measure; library defaults target ~50ms; run in a threadpool so the event loop never blocks |
| Route added later without auth | High | Default-deny dependency plus the enumeration test that fails on any unlisted public route |

## Identity: verification

Checkpoint I-C, 2026-10-09. Jetson run: `./deploy.sh && tests/e2e_jetson.sh` through the public
quick tunnel URL, 32 passed and 2 skipped (see below), log in
[evidence/identity/run.log](../evidence/identity/run.log), 145 screenshots indexed in
[evidence/identity/index.md](../evidence/identity/index.md). Local run: the same config on a
scratch stack (`-p jobapp-i8`), 34 passed, log in
[evidence/identity/local-run.log](../evidence/identity/local-run.log). Api: `pytest tests/identity`
567 passed against real Postgres. `./deploy.sh smoke` passed with the new auth checks.

| Success criterion of SPEC-identity.md | Proved by |
|---|---|
| 1. Harald creates himself as admin from the CLI on the Jetson and signs in through the public tunnel URL | His account `hthautin@gmail.com` exists on the Jetson as an active admin, created 2026-10-08 17:39:02 UTC by accepting an invite printed by `./deploy.sh admin create` (both of his invite rows have no creator, which only the CLI produces; the first was revoked when the second was printed). The accept signed him in at that moment (`last_login_at` equals the invite's `used_at`), on the tunnel URL the CLI built the link from. Not repeated here: the journey never signs in as him. The same path is exercised end to end by `identity.spec.ts` ("the CLI link sets up the admin, who lands signed in on /") with a throwaway admin through `./deploy.sh admin create` on the public URL. |
| 2. A friend invited by link signs in from another browser and sees only their own space; `/admin/users` is refused | `invites.spec.ts` ("the friend opens a copied invite in another browser and lands signed in as a member": no Admin entry, `/admin/users` is the not found page, `GET /api/admin/invites` answers 404) and `users.spec.ts` (accept in a second context, demoted admin gets 404). Isolation: `tests/profile/test_api.py` `test_users_never_see_each_other` and `test_query_parameters_cannot_select_another_user`; `test_member_gets_the_same_404_as_an_unknown_route` in the admin users and invites api tests. Screenshots `invites-member-admin-404-*`, `users-admin-demoted-404-*`. |
| 3. Every API route except health and the public auth routes returns 401 without a session, enumerated | `tests/identity/test_guard_api.py`: `test_every_non_public_route_is_401_without_a_session` walks `app.routes`, `test_routes_added_later_are_denied_too`, `test_the_real_app_passes_the_check` (startup refuses an unguarded route). On the Jetson: `./deploy.sh smoke` checks 401 on `/api/auth/me`, `/api/admin/users`, `/api/admin/invites`, `/api/profile`, `/api/profile/revisions` on loopback (no cookie and a forged one) and on the first four plus `PATCH /api/auth/me` through the tunnel. |
| 4. Brute force on login is throttled with 429; disabled users are signed out on their next request | `throttle.spec.ts` on the Jetson: five wrong passwords, the sixth (right password) gets 429 with the countdown (`throttle-login-throttled-*`); locally also the password card. `tests/identity/test_throttle_api.py` (per email, per address, sliding window, parallel guesses). `users.spec.ts` "disabling the friend sends their next navigation to the sign in page" (and a stale cookie stays out after enable); `test_disabling_ends_the_sessions_at_once`, `test_session_of_a_disabled_user_is_refused_and_deleted`. |
| 5. All identity tests and the Playwright journey pass locally and against the Jetson | pytest 567 passed; `web/e2e/identity-all.config.ts` 34 of 34 locally and 32 passed on the Jetson. The 2 skipped there are by design: the last admin rule in the browser needs the spec's admin to be the only active one (Harald is a second admin there; the rule passes locally and in `test_admin_users_api.py`), and the password card throttle needs a second throwaway account (scratch stack only). |

Live data untouched: `tests/e2e_jetson.sh` snapshots Harald's account (role, is_active, created,
updated and last sign in times, md5 of the password hash, name, sessions and their md5, profile
revision, updated time and md5 of its sections, revisions count, invites) before and after the run:
identical, 15 fields. Afterwards zero e2e users, sessions, profiles, invites and login attempts, and
zero failed sign ins in the 15 minute window for his email, for the Mac's IPv4 and IPv6 addresses,
and overall. Each spec unlocked its own failures in between with `./deploy.sh admin unlock`.
