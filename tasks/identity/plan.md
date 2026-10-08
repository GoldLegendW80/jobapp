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
- [ ] I3: Frontend shell: router, login page, protected routes, sign out

### Checkpoint I-A
- [ ] Harald's admin created by CLI on the Jetson can sign in and out through the public URL

- [ ] I4: Invites: admin creates a link, friend accepts and is signed in
- [ ] I5: Admin users page: list, disable, role, last-admin protection
- [ ] I6: Account password change and reset links

### Checkpoint I-B
- [ ] Two-user journey works; isolation fixture proves cross-user reads fail

- [ ] I7: Login throttling and auth hardening
- [ ] I8: End-to-end identity journey on the Jetson, screenshots of every page

### Checkpoint I-C
- [ ] All 5 success criteria of SPEC-identity.md verified

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Secure cookie breaks local http dev | Med | `Secure` set from `X-Forwarded-Proto` / APP_ENV; tests cover both |
| Tunnel URL changes so invite links break | Med | Links are relative to whatever origin the admin uses; the admin UI builds the link from `window.location.origin`; documented |
| argon2 hashing is slow on the Jetson | Low | Measure; library defaults target ~50ms; run in a threadpool so the event loop never blocks |
| Route added later without auth | High | Default-deny dependency plus the enumeration test that fails on any unlisted public route |
