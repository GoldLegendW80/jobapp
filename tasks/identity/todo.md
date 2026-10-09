# Tasks: identity

Plan: [plan.md](plan.md). Spec: [SPEC-identity.md](../../SPEC-identity.md).

## I1: Identity schema, security primitives and first admin from the CLI

**Description:** Models and migration 0002 for users, sessions, invites, login_attempts (citext extension, enums); password hashing (argon2id), password rules with the bundled common-password list, token generation and hashing; CLI `python -m app.identity.cli create-admin <email>` that creates a setup invite and prints a one-time link; `./deploy.sh admin create <email>` wrapper.

**Acceptance criteria:**
- [x] Migration 0002 upgrades and downgrades cleanly; `alembic check` clean
- [x] Unit tests for hashing, verification, rules, token hashing
- [x] CLI prints a link path `/invite/<token>`; running it twice for the same email revokes the first link

**Verification:** pytest (226 passed); `./deploy.sh admin create` added with I3 and used on the Jetson on the Jetson prints a link

**Dependencies:** platform

**Files:** `api/app/identity/{models,security,service,cli}.py`, `api/app/identity/common_passwords.txt`, `api/alembic/versions/0002_identity.py`, `api/tests/identity/`, `deploy.sh`

**Scope:** M

## I2: Sign in, sign out, session cookie and default-deny API

**Description:** `/api/auth/login`, `/logout`, `/me`, invite check and accept (needed to finish the first admin), `current_user` and `require_admin` dependencies, CSRF header middleware, default-deny for `/api/*` with a public allowlist, sliding session refresh.

**Acceptance criteria:**
- [x] Cookie flags correct over http locally and https through the tunnel
- [x] Route enumeration test: every non-allowlisted route returns 401 without a session
- [x] Non-GET without `X-Requested-With: jobapp` returns 403

**Verification:** pytest (integration, real Postgres); curl sequence through Caddy

**Dependencies:** I1

**Files:** `api/app/identity/{router,deps,schemas}.py`, `api/app/main.py`, `api/tests/identity/`

**Scope:** M

## I3: Frontend shell: router, login page, protected routes, sign out

**Description:** React Router, auth provider from `/api/auth/me`, `/login`, `/invite/:token` (first admin setup uses it), protected app shell with header and user menu, global 401 handling, `next` redirect.

**Acceptance criteria:**
- [x] Unauthenticated visit to `/` goes to `/login?next=/`, then back after sign in
- [x] Invite page sets a password and lands signed in
- [x] Sign out returns to `/login`; back button does not show protected content

**Verification:** Vitest; Playwright e2e locally through Caddy; screenshots

**Dependencies:** I2

**Files:** `web/src/features/auth/*`, `web/src/App.tsx`, `web/src/lib/api.ts`, `web/e2e/identity.spec.ts`

**Scope:** M

## Checkpoint I-A
- [x] On the Jetson: `./deploy.sh admin create`, open the link through the public URL, set password, sign out, sign in

## I4: Invites: admin creates a link, friend accepts and is signed in

**Description:** Admin invite endpoints (create, list, revoke), invite expiry and single use, concurrent accept safety; admin invites section in the UI with a copy-link dialog.

**Acceptance criteria:**
- [x] Expired, used, revoked, malformed tokens all give the same 404 page
- [x] Two concurrent accepts: exactly one succeeds
- [x] Link shown once, built from the current origin

**Verification:** pytest; Playwright with two browser contexts

**Dependencies:** I3

**Files:** `api/app/identity/admin_router.py`, `web/src/features/admin/*`, tests

**Scope:** M

## I5: Admin users page: list, disable, role, last-admin protection

**Acceptance criteria:**
- [x] Disabling a user ends their sessions; their next request is 401 and the UI lands on `/login`
- [x] The last active admin cannot be disabled or demoted
- [x] Members get 404 on admin API routes and cannot see the admin entry

**Verification:** pytest; Playwright two contexts

**Dependencies:** I4

**Files:** `api/app/identity/admin_router.py`, `web/src/features/admin/*`, tests

**Scope:** M

## I6: Account password change and reset links

**Acceptance criteria:**
- [x] Changing the password ends the other sessions, keeps the current one
- [x] Admin creates a reset link (1 hour); `/reset/:token` sets a new password and ends all old sessions
- [x] Wrong current password, weak new password, same as email: clear errors

**Verification:** pytest; Playwright

**Dependencies:** I5

**Files:** `api/app/identity/router.py`, `web/src/features/account/*`, `web/src/features/auth/reset-page.tsx`, tests

**Scope:** M

## Checkpoint I-B
- [x] Two-user journey green; isolation fixture in place for later modules

## I7: Login throttling and auth hardening

**Acceptance criteria:**
- [x] 5 failures per email or 20 per IP in 15 minutes give 429 with Retry-After; success after the window
- [x] Unknown email and wrong password give the same message and comparable timing
- [x] No password, token or cookie value appears in logs (log capture test)

**Verification:** pytest; a log scan in the smoke

**Dependencies:** I6

**Files:** `api/app/identity/{service,router}.py`, tests

**Scope:** S

## I8: End-to-end identity journey on the Jetson

**Acceptance criteria:**
- [ ] `./deploy.sh` then the full Playwright identity journey through the public URL passes
- [ ] Screenshots of every page, light and dark, desktop and 375px, in `tasks/evidence/identity/`
- [ ] `./deploy.sh smoke` extended with an auth check (401 on a protected route)

**Verification:** Playwright run log and screenshots

**Dependencies:** I7

**Files:** `web/e2e/identity.spec.ts`, `tests/jetson_smoke.sh`

**Scope:** S

## Checkpoint I-C
- [ ] All 5 success criteria of SPEC-identity.md verified
