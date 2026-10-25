# Accounts

The first admin, inviting people, managing accounts, a user's own account and sign in throttling.

## Contents

- [Inviting people](#inviting-people)
- [Managing accounts](#managing-accounts)
- [Your account](#your-account)
- [Sign in throttling](#sign-in-throttling)

Nobody signs up alone. The first admin comes from the Jetson's command line:

```bash
./deploy.sh admin create you@example.org    # prints https://jobapp.taild83d16.ts.net/invite/<token>, valid 7 days, single use
```

Open the link, choose a name and a password (10 to 128 characters, not the email, not a common
password) and you land signed in. Run it again for an active admin to get a 1 hour password reset
link instead. The link holds a secret: it is printed once, never logged, and Caddy masks tokens in
its access log. Every page except `/login`, `/invite/<token>` and `/reset/<token>` needs a session;
any api answer 401 sends the browser to `/login?next=<page>`.

The identity journey runs in a real browser against the real stack, locally on a scratch project:

```bash
HTTP_PORT=8093 docker compose -p jobapp-i3 -f compose.yml -f compose.mac.yml up -d --build --wait
docker compose -p jobapp-i3 -f compose.yml exec -T api alembic upgrade head
E2E_CADDY_URL=http://127.0.0.1:8093 E2E_COMPOSE_PROJECT=jobapp-i3 pnpm --dir web exec playwright test identity.spec.ts
docker compose -p jobapp-i3 -f compose.yml down -v       # that scratch project only
```

and on the Jetson through the public URL (the spec runs `./deploy.sh admin create` itself), then
the test account is removed:

```bash
E2E_CADDY_URL=$(./deploy.sh url) E2E_ADMIN_VIA_DEPLOY=1 E2E_ADMIN_EMAIL=e2e-admin@example.org \
  E2E_SHOT_PREFIX=jetson- pnpm --dir web exec playwright test identity.spec.ts
ssh jetson "cd jobapp && docker compose -f compose.yml exec -T db sh -c 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"'" \
  <<< "delete from invites where email = 'e2e-admin@example.org'; delete from users where email = 'e2e-admin@example.org';"
```

Do not run `identity.spec.ts` in parallel with `caddy.spec.ts`: the latter stops the database.

The whole identity surface runs as one journey on the Jetson through the public URL, with
throwaway `e2e-*@example.org` accounts only:

```bash
./deploy.sh && OWNER=<the e-mail address of your account on the Jetson> tests/e2e_jetson.sh
```

It runs `web/e2e/identity-all.config.ts` (identity, invites, users, account, profile and throttle
specs in that order, one after the other; each unlocks its own failed attempts with `./deploy.sh
admin unlock`, so the run never reaches the 20 failures per address), snapshots Harald's account
before and after and fails if anything changed, deletes every e2e row (users with their sessions
and profile, invites, attempts), checks that no failed sign in is left in the window for his
email or this Mac's addresses, and writes every screenshot with an index to
`tasks/evidence/identity/`. It refuses to start while e2e rows of an earlier run are left
(`E2E_CLEAN_FIRST=1` removes them first).
Locally the same config runs on a scratch project (one shared admin, so the last admin rule runs
too):

```bash
HTTP_PORT=8098 docker compose -p jobapp-i8 -f compose.yml -f compose.mac.yml up -d --build --wait
docker compose -p jobapp-i8 -f compose.yml exec -T api alembic upgrade head
E2E_CADDY_URL=http://127.0.0.1:8098 E2E_COMPOSE_PROJECT=jobapp-i8 E2E_ADMIN_EMAIL=e2e-admin@example.org \
  E2E_PASSWORD_THROTTLE=1 pnpm --dir web exec playwright test -c e2e/identity-all.config.ts
docker compose -p jobapp-i8 -f compose.yml down -v
```

The specs' own runs below remain for one area at a time.

## Inviting people

Signed in as an admin, open **Admin** (`/admin/users`), then **Invite someone**: an email, the kind
of link (an invite creates an account, valid 7 days; a password reset is for an existing active
account, valid 1 hour) and, for an invite, the role. The dialog shows the full link once, built
from the address you are on, with a Copy button: send it yourself. It is never shown again; a new
link for the same email and kind revokes the previous unused one. The Invites table lists every
link with its status (active, used, revoked, expired) and lets you revoke an active one. An invite
for an email that already has an account is refused: send a reset link instead.

The API behind it, admin only (a member gets 404, nobody signed in 401):
`GET /api/admin/invites?limit=&offset=` (newest first, never a token), `POST /api/admin/invites`
`{email, kind, role}` (201 with `path` once), `DELETE /api/admin/invites/{id}` (204, or 404 for
anything not active).

The invites journey (`web/e2e/invites.spec.ts`) runs like the identity one, on its own scratch
project:

```bash
HTTP_PORT=8094 docker compose -p jobapp-i4 -f compose.yml -f compose.mac.yml up -d --build --wait
docker compose -p jobapp-i4 -f compose.yml exec -T api alembic upgrade head
E2E_CADDY_URL=http://127.0.0.1:8094 E2E_COMPOSE_PROJECT=jobapp-i4 E2E_FRIEND_EMAIL=friend@example.org \
  pnpm --dir web exec playwright test invites.spec.ts
docker compose -p jobapp-i4 -f compose.yml down -v
```

On the Jetson it uses two throwaway accounts, removed afterwards with everything they own
(sessions and profile rows cascade with the user):

```bash
E2E_CADDY_URL=$(./deploy.sh url) E2E_ADMIN_VIA_DEPLOY=1 E2E_ADMIN_EMAIL=e2e-admin@example.org \
  E2E_FRIEND_EMAIL=e2e-friend@example.org E2E_SHOT_PREFIX=jetson- \
  pnpm --dir web exec playwright test invites.spec.ts
ssh jetson "cd jobapp && docker compose -f compose.yml exec -T db sh -c 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"'" <<< "
  delete from invites where email in ('e2e-admin@example.org', 'e2e-friend@example.org');
  delete from login_attempts where email in ('e2e-admin@example.org', 'e2e-friend@example.org');
  delete from users where email in ('e2e-admin@example.org', 'e2e-friend@example.org');"
```

## Managing accounts

The **Accounts** table above the invites lists everyone who can sign in, by name: email, role,
status, last sign in and how many browsers are signed in. Your own row says **You**. Each row's
actions menu offers **Make admin** or **Make member**, and **Disable** or **Enable**, each behind a
confirmation:

- Disabling signs the account out of every browser at once (its sessions are deleted, its next
  request answers 401 and lands on `/login`) and refuses its sign in until it is enabled again.
  Enabling never brings an old session back: the person signs in again. Their data stays.
- A role change applies from the account's next request; a demoted admin loses the Admin entry
  on their next page load.
- The last active admin can be neither disabled nor made a member (the menu greys those out
  and the API answers 409), even when two admins act at the same moment: every change takes one
  database lock and rechecks under it. You can make yourself a member while another admin
  exists (the dialog asks first, then the app reloads who you are and leaves the admin area).
  Your own row has no Disable: another admin does that.

The API, admin only like the invites: `GET /api/admin/users?limit=&offset=` (sorted by name, each
with `sessions`, plus `active_admins`), `PATCH /api/admin/users/{id}` `{is_active?, role?}` (200
with the account, also when nothing changes; 404 for an unknown id, 409 for the last admin, 422
for an empty or invalid body).

The accounts journey (`web/e2e/users.spec.ts`) runs like the invites one, on its own scratch
project (`-p jobapp-i5`, `HTTP_PORT=8095`, `E2E_COMPOSE_PROJECT=jobapp-i5`), and on the Jetson with
the same two throwaway accounts and the same cleanup. It only ever acts on the rows of its two
emails, and checks the last admin rule in the browser only where its admin is the only active one.

## Your account

Everyone has an **Account** page (`/account`, from the user menu at the top right) with two cards:

- **Profile name**: the name shown in the header menu and to the admin. Same rules as on the
  invite page: trimmed, inner spaces collapsed, 1 to 100 characters, no control characters.
- **Change password**: the current password, then the new one twice (10 to 128 characters, not
  your email, not a common password, not the current one). Each field has a show toggle. On
  success every other browser signed in to the account is signed out at its next request; the
  browser you used stays signed in.

Forgot your password? Ask the admin for a reset link (Admin, Invite someone, kind "Password
reset"): it is valid 1 hour, opens `/reset/<token>`, and setting a new password there signs out
every browser of the account.

The API, for the signed in user only (401 without a session, 403 without the CSRF header):
`PATCH /api/auth/me` `{name}` (200 with the user, 422 `name: <rule>`) and `POST /api/auth/password`
`{current_password, new_password}` (204). A wrong current password is a 400
`current_password: The current password is incorrect.`, not a 401: the session is fine and stays
signed in. A broken rule is a 422 `new_password: <the invite page's message>`. Errors name their
field first so the page can put each message under its input. Under two simultaneous changes from
two browsers, the first one wins and ends the other's session, which gets a 401.

The account journey (`web/e2e/account.spec.ts`) runs like the others, on its own scratch project
(`-p jobapp-i6`, `HTTP_PORT=8096`, `E2E_COMPOSE_PROJECT=jobapp-i6`), and on the Jetson with the
throwaway admin `e2e-admin@example.org` only, removed afterwards with the same cleanup as above.

## Sign in throttling

Every sign in attempt is recorded in `login_attempts` (the normalised email, or the trimmed
lowercase text when it is not an address; the client address the api sees, which is
Funnel's `X-Forwarded-For` through the public URL and the TCP peer on loopback; the time; success or not),
unknown and disabled accounts included. Before any password is checked:

- 5 failures for one email in 15 minutes, or 20 from one address, answer **429**
  `{"detail": "Too many sign in attempts. Try again in N minutes."}` with `Retry-After` in whole
  seconds, counted from the oldest failure still in the window. The window slides: a failure
  stops counting exactly 15 minutes after it happened.
- "One address" is the whole address for IPv4 and the /64 for IPv6: one subscriber is given a
  whole /64, so its addresses share one count. `::ffff:a.b.c.d` counts as the IPv4 address. The
  row keeps the full address.
- A successful sign in resets the email's count (the person proved they hold the password); it
  does not reset the address's count, so one account someone owns cannot launder guesses on
  others.
- A 429 runs no argon2 and is not recorded, so retrying does not extend the wait. Its speed
  reveals nothing: the same refusal comes for any email once the limit is reached.
- The count and the record happen under one Postgres advisory lock per email and per address,
  before argon2 runs, so parallel guesses never overshoot: 20 at once on one email give exactly
  5 checks and 15 refusals.
- A wrong current password on the Account page counts as a failed sign in of that account (both
  guess the same password): after 5 the card answers 429 too, and so does the sign in of that
  email. Invite and reset links are not throttled: the token is the credential.
- Rows older than 30 days are deleted on write, at most once an hour per api process.

The sign in page and the password card show the server's message and keep their button disabled
with a countdown until `Retry-After` has passed. The journey `web/e2e/throttle.spec.ts` runs on a
scratch project (`-p jobapp-i7`, `HTTP_PORT=8097`, `E2E_COMPOSE_PROJECT=jobapp-i7`,
`E2E_PASSWORD_THROTTLE=1`) and deletes its own attempts afterwards; on the Jetson with
`e2e-admin@example.org` only. Every local browser reaches the api from one address (Caddy's
peer), so all specs of one run share the 20 failure budget per 15 minutes.

To unlock someone on the Jetson, delete their failed attempts still in the window (also those of
one address with `--ip`, whatever the email); it prints how many rows went:

```bash
./deploy.sh admin unlock someone@example.org                   # that email, case insensitive
./deploy.sh admin unlock someone@example.org --ip 203.0.113.9  # and every failure from that address
```

Successful attempts and failures older than 15 minutes are kept. The email and the address reach
psql as bound variables, and Postgres refuses anything that is not an address. `--ip` removes the
rows of that exact address: for IPv6, where the limit counts the whole /64, it does not unlock
the other addresses of the /64 yet (issue #152).
