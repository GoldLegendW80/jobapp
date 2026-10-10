# Spec: identity

Module of [CAPABILITY-MAP.md](CAPABILITY-MAP.md). Depends on `platform`. Every later module relies on its `current_user` dependency and on the rule that every user-owned row carries a `user_id`.

## Objective

Let Harald and a few invited friends sign in to the app, and keep each person's data private to them. Nobody can sign up alone: Harald (admin) creates an invite, sends the link himself (WhatsApp, SMS, anything), the friend opens it and chooses a password. No email is sent by the app (everything stays free, no SMTP).

User stories:
- As the admin, I create an invite for an email address and get a one-time link valid 7 days.
- As an invited friend, I open the link, set my name and password, and land signed in.
- As a user, I sign in with email and password, stay signed in for 30 days on that browser, and sign out.
- As a user, I change my password.
- As the admin, I list users, disable or re-enable one (their sessions end at once), revoke an unused invite, and generate a password reset link for someone who forgot theirs (same one-time link mechanism).
- As anyone not signed in, I can only see the sign-in page, the invite page and the reset page; every API route except health and auth returns 401.

The very first admin is created from the command line on the Jetson: `./deploy.sh admin create <email>` prints a one-time setup link.

## Tech stack

Builds on platform. Additions:
- Passwords: `argon2-cffi` (argon2id, library defaults).
- Sessions: server-side, table `sessions` in Postgres. The cookie holds an opaque random token (32 bytes, URL-safe); the database stores only its SHA-256.
- Frontend: React Router 7 (library mode) for routes, react-hook-form with zod for forms, shadcn Form, Input, Label, Alert, Table, Dialog, DropdownMenu, Sonner toasts.

## Data model

```
users(id uuid pk, email citext unique, name text, password_hash text, role enum(admin, member),
      is_active bool default true, created_at, updated_at, last_login_at)
sessions(id uuid pk, user_id fk users on delete cascade, token_hash bytea unique,
         created_at, last_seen_at, expires_at, user_agent text, ip inet)
invites(id uuid pk, email citext, kind enum(invite, reset), token_hash bytea unique,
        created_by fk users, created_at, expires_at, used_at null, revoked_at null)
login_attempts(id bigserial, email citext, ip inet, at timestamptz, success bool)
```

## API

All under `/api/auth` and `/api/admin`; JSON; errors are `{"detail": "..."}` with no dashes as separators in messages.

| Method and path | Who | Does |
|---|---|---|
| POST /api/auth/login | anyone | email, password → sets cookie, returns the user |
| POST /api/auth/logout | signed in | deletes the session, clears the cookie |
| GET /api/auth/me | signed in | current user |
| POST /api/auth/password | signed in | current and new password; ends the user's other sessions |
| GET /api/auth/invites/{token} | anyone | checks a link: returns email and kind, or 404 if invalid, used, revoked or expired |
| POST /api/auth/invites/{token}/accept | anyone | name (invite only) and password → creates or updates the user, signs in |
| GET/POST /api/admin/users | admin | list users; (no POST: users come from invites) |
| PATCH /api/admin/users/{id} | admin | `is_active`, `role`; disabling ends their sessions; the last active admin cannot be disabled or demoted |
| GET/POST /api/admin/invites | admin | list; create `{email, kind}` → returns the link once |
| DELETE /api/admin/invites/{id} | admin | revoke an unused invite |

Rules:
- Cookie `jobapp_session`: HttpOnly, Secure when served over https (Tailscale Funnel), SameSite=Lax, Path=/, Max-Age 30 days, sliding (refreshed at most once a day).
- CSRF: every non-GET request must carry header `X-Requested-With: jobapp` (set by the frontend client); together with SameSite=Lax and JSON-only bodies this blocks cross-site forms.
- Rate limit: 5 failed logins per email per 15 minutes and 20 per IP per 15 minutes → 429 with `Retry-After`. Same message for unknown email and wrong password, and comparable timing (hash a dummy password for unknown emails).
- Passwords: 10 to 128 characters, not equal to the email, checked against a small bundled list of the most common passwords.
- Invite and reset links: one use, 7 days for invites and 1 hour for resets, a new link for the same email revokes the previous unused one.
- Every later module gets its user from `Depends(current_user)` and filters by `user_id`; `Depends(require_admin)` for admin routes. A test helper asserts that a user never reads another user's rows.

## Frontend

Routes: `/login`, `/invite/:token`, `/reset/:token`, `/` (protected shell with a header: app name, user menu with Account and Sign out, Admin entry for admins), `/account` (change password), `/admin/users` (users table, invites table, "Invite someone" dialog that shows and copies the link). Unauthenticated access to a protected route redirects to `/login?next=<path>`, and back after sign in. A 401 from any API call sends the user to `/login`. Mobile friendly at 375px.

## Commands

As platform, plus:
```bash
./deploy.sh admin create <email>      # on the Jetson: prints a one-time setup link for the first admin
docker compose exec api python -m app.identity.cli create-admin <email>   # what the above runs
```

## Project structure

```
api/app/identity/  models.py schemas.py router.py admin_router.py service.py security.py deps.py cli.py common_passwords.txt
api/alembic/versions/0002_identity.py
api/tests/identity/
web/src/features/auth/      login, invite, reset pages, auth provider, protected route
web/src/features/account/
web/src/features/admin/
web/e2e/identity.spec.ts
```

## Testing strategy

- Unit: password rules, token hashing, cookie flags, rate limiter windows.
- Integration (real Postgres): every endpoint, every rule above, including the edge cases: expired, used, revoked and malformed tokens; case-insensitive email; unicode names; concurrent accept of the same invite (only one wins); disabling a user kills an open session on its next request; last admin protection; 429 and recovery after the window; session expiry and sliding refresh; logout of one session leaves the other browsers signed in; password change ends the other sessions; CSRF header missing → 403; SQL-looking and very long inputs.
- Isolation: a fixture with two users proves cross-user access returns 404, not 403, on admin and user routes.
- End to end (Playwright, through Caddy, locally and on the Jetson through the public URL): first admin from the CLI link, invite a friend, friend accepts in a second browser context, both sign in, friend cannot open `/admin/users`, admin disables friend and the friend's next click lands on `/login`, password reset link, sign out. Screenshots of every page in light and dark, desktop and 375px.

## Boundaries

- **Always:** hash every token and password; constant-time comparisons; generic auth error messages; `user_id` filter in every user-owned query.
- **Ask first:** adding email sending, OAuth or social login, changing session lifetime or password rules.
- **Never:** log a password, a token or a full cookie; return whether an email exists; store a plain token.

## Success criteria

1. Harald creates himself as admin from the CLI on the Jetson and signs in through the public URL.
2. A friend invited by link signs in from another browser and sees only their own space; `/admin/users` is refused to them.
3. Every API route except health and the public auth routes returns 401 without a session (an automated test enumerates the routes).
4. Brute force on login is throttled with 429; disabled users are signed out on their next request.
5. All identity tests and the Playwright journey pass locally and against the Jetson.

## Open questions

None blocking; defaults above are proposed for approval.
