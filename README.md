# jobapp

A self-hosted job-search app for Harald and a few invited friends. It finds postings, scores them against a profile, writes a tailored CV and letter per job, and lets an agent fill application forms in a live browser. It runs as a Docker Compose stack on a Jetson Orin Nano at home and is reached through a Cloudflare quick tunnel.

## Documents

- [CAPABILITY-MAP.md](CAPABILITY-MAP.md): the modules, their dependencies and the build order.
- [SPEC-platform.md](SPEC-platform.md): the platform module (Compose stack, deploy, tunnel), built first.
- [tasks/plan.md](tasks/plan.md) and [tasks/todo.md](tasks/todo.md): the implementation plan and task list of the current module.

## Setup

```bash
cp .env.example .env           # then fill in real values; .env is never committed
pre-commit install             # ruff, prettier, eslint, gitleaks and file fixers on every commit
```

## Development on the Mac

```bash
docker compose -f compose.yml -f compose.dev.yml up --build    # api reloads; http://localhost via caddy
pnpm --dir web dev                                             # Vite with hot reload on :5173, /api proxied to :8000
docker compose exec api alembic upgrade head                   # apply migrations
docker compose exec api pytest                                 # backend tests
pnpm --dir web test                                            # frontend tests
pnpm --dir web lint && uv run --directory api ruff check .     # lint
tests/compose_smoke.sh                                         # whole-stack smoke test, then down
E2E_CADDY_URL=http://127.0.0.1 pnpm --dir web exec playwright test caddy.spec.ts   # browser through caddy
```

Caddy (`caddy/Caddyfile`) is the only service with a host port, `${HTTP_BIND:-127.0.0.1}:${HTTP_PORT:-80}`.
It serves the React build baked into its image by the `caddy` target of `web/Dockerfile`, falls back
to `index.html` for client routes, and proxies `/api/*` to `api:8000` with the prefix kept. Set
`HTTP_PORT=8080` in `.env` if port 80 is taken on the Mac.

`docker compose exec api pytest` needs a test database: the smoke script creates `jobapp_test` in the
compose `db` and runs `pytest -m "not docker"` with `TEST_DATABASE_URL` pointing at it.
Migrations live in `api/alembic/versions/`; models register on `app.models_base.Base` and are imported
in `api/app/models.py` so `alembic revision --autogenerate` sees them.

## Deploy to the Jetson

```bash
./deploy.sh                    # preflight, rsync to jetson:~/jobapp, build there, compose up --wait, migrate, health
./deploy.sh --env              # same, and replace the Jetson's .env with .env.jetson
./deploy.sh logs api           # follow the logs of one service (last 200 lines first)
./deploy.sh ps                 # docker compose ps on the Jetson
./deploy.sh stats              # memory and CPU per service
./deploy.sh ssh                # shell in the deploy directory
./deploy.sh url                # the current https://*.trycloudflare.com URL, alone on stdout
./deploy.sh smoke              # checks on the Jetson, then through the public URL; exit 1 if any fails
./deploy.sh smoke --full       # same, plus a database outage and recovery (stops and starts db once)
./deploy.sh backup [--pull]    # verified pg_dump -Fc to jetson:~/jobapp-backups/, --pull copies it to backups/
./deploy.sh backups            # list the dumps on the Jetson
./deploy.sh restore latest     # restore into the scratch database jobapp_restore_check, print row counts
./deploy.sh restore <file> --into-live   # replace the live database (type RESTORE jobapp to confirm)
./deploy.sh backup-schedule install|remove|status   # nightly backup in the Jetson user's crontab
./deploy.sh admin create <email>   # one-time admin setup link on the public URL (a reset link if that admin exists)
tests/deploy_test.sh           # local tests of deploy.sh: shellcheck, dispatcher, unreachable host, .rsyncignore, url, restore prompt, admin create
tests/jetson_smoke.sh          # smoke of the deployed stack over ssh (stops and starts db once)
tests/backup_test.sh           # backup and restore round trip and refusals on the Jetson (BACKUP_TEST_LIVE=1: live restore too)
tests/crash_check.sh           # kill each service's main process, check the daemon restarts it, then smoke
tests/reboot_check.sh          # run by a human: reboots the Jetson (sudo), checks the stack is back within 2 minutes
```

Images are built on the Jetson itself (arm64) with only `compose.yml`. `.rsyncignore` keeps
secrets, `node_modules`, `.venv`, `dist`, `.git` and evidence off the Jetson, and since `.env` is
excluded, `rsync --delete` never removes the Jetson's `.env`. The Jetson's production `.env` is
generated on the first deploy into the git-ignored `.env.jetson` (random `POSTGRES_PASSWORD` and
`SECRET_KEY`, `APP_ENV=production`, mode 600) and copied only when the Jetson has none or with
`--env`; `--env` refuses to change `POSTGRES_PASSWORD` once the `pgdata` volume exists. If
`.env.jetson` is lost, the next deploy copies the Jetson's `.env` back. Caddy listens on the
Jetson's loopback only; from the Mac, open it with `ssh -N -L 18080:127.0.0.1:80 jetson` and browse
http://127.0.0.1:18080. `DEPLOY_HOST` and `DEPLOY_DIR` override the target. Deploys are manual;
GitHub Actions only runs lint and tests.

## Public URL

`cloudflared` runs a Cloudflare quick tunnel (free, no account, no domain) from the Jetson to
Caddy. It only makes outbound connections, so no port is opened on the router. The URL is
random and changes every time the container starts (deploy that recreates it, `restart`, reboot):

```bash
curl "$(./deploy.sh url)/api/health"     # {"status":"ok"} from anywhere
```

`./deploy.sh url` reads the logs of the container's current start only, so it never prints the
dead URL of a previous start, and waits up to 30s for a fresh one. `./deploy.sh` prints the URL at
the end. A new hostname can take up to a minute to resolve (the zone's negative DNS cache is 60s);
the smoke backs off and retries rather than hammering it, because quick tunnels are rate limited
and have no uptime guarantee.

cloudflared reaches Caddy on its own listener, `caddy:8080`, never published. That listener trusts
the Compose network as a proxy and takes the visitor's address from `Cf-Connecting-Ip`, which the
Cloudflare edge sets (and refuses with 403 when a visitor sends it). The published `:80` trusts
nobody. Either way the api receives exactly one `X-Forwarded-For` and logs it as `client`.

The service sits in the Compose profile `tunnel`, which `deploy.sh` enables on the Jetson. Neither
the dev stack nor a plain `docker compose -f compose.yml up` on the Mac opens a public tunnel.

## Accounts

Nobody signs up alone. The first admin comes from the Jetson's command line:

```bash
./deploy.sh admin create you@example.org    # prints https://<tunnel>/invite/<token>, valid 7 days, single use
```

Open the link, choose a name and a password (10 to 128 characters, not the email, not a common
password) and you land signed in. Run it again for an active admin to get a 1 hour password reset
link instead. The link holds a secret: it is printed once, never logged, and Caddy masks tokens in
its access log. Every page except `/login`, `/invite/<token>` and `/reset/<token>` needs a session;
any api answer 401 sends the browser to `/login?next=<page>`.

The identity journey runs in a real browser against the real stack, locally on a scratch project:

```bash
HTTP_PORT=8093 docker compose -p jobapp-i3 -f compose.yml up -d --build --wait
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

### Inviting people

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
HTTP_PORT=8094 docker compose -p jobapp-i4 -f compose.yml up -d --build --wait
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

### Managing accounts

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

## Profile export and import

`GET /api/profile/export` downloads the whole profile as `profile-YYYY-MM-DD.json`;
`POST /api/profile/import` takes that file back (2 MB at most) and replaces all six sections in
one transaction, one revision each. Harald's engine files convert into the same format on the
Mac, then load into one account inside the api container:

```bash
uv run --native-tls --directory api python -m app.profile.cli convert --source-dir "$PWD/.." --out /tmp/profile.json
docker compose exec -T api python -m app.profile.cli import --email you@example.org --file - < /tmp/profile.json
```

The import prints the new revision and the completeness, never the profile itself. Exit codes:
0 imported, 2 invalid arguments or file, 1 no such account or a database error.

## Backups and restores

`./deploy.sh backup` runs `pg_dump -Fc` inside the `db` container and streams it to
`jetson:~/jobapp-backups/jobapp-YYYY-MM-DDTHHMMSS.dump` (UTC time). The dump is written to a hidden
`.partial` file, checked with `pg_restore --list` and a full read into `/dev/null` (which is what
catches a truncated or damaged file), then renamed; the directory is mode 700 and every dump mode
600. The newest `BACKUP_KEEP` dumps (default 14) are kept and older ones deleted. A backup is
refused when the free disk would drop under the database size plus `BACKUP_MIN_FREE_MB` (100);
a disk that fills up during `pg_dump` makes it fail, and the partial file is removed. Every backup
and restore holds `flock` on `~/jobapp-backups/.lock`, so a second one at the same time is refused
with a clear message. The backup directory lives outside `~/jobapp`, so `rsync --delete` never
touches it; `backups/` and `*.dump` are git-ignored and never rsynced.

`./deploy.sh backup --pull` also copies the dump to `backups/` on the Mac (dir 700, file 600,
sha256 compared on both sides). That copy is the only one off the Jetson's SD card.

`./deploy.sh restore <file>` takes a name in `~/jobapp-backups`, a path on the Jetson, `latest`,
or a file on the Mac (uploaded to `~/jobapp-backups/incoming/` first). By default it restores into
the scratch database `jobapp_restore_check`, prints the row count of every table and drops it
(`--keep` leaves it, `./deploy.sh restore --drop-scratch` drops it later). A missing, empty,
foreign, truncated or damaged file is refused before any database is touched.

`./deploy.sh restore <file> --into-live` replaces the live database. It verifies the file, asks you
to type `RESTORE jobapp` (`--yes` skips the question, for scripts; without a terminal and without
`--yes` it refuses), takes a pre-restore backup (`jobapp-...-pre-restore.dump`), stops `api`,
drops and recreates the database, restores (rolling back to the pre-restore dump if `pg_restore`
fails), runs `alembic upgrade head`, starts `api` and checks health. About 15 seconds of 502 on
`/api` for a small database.

Nightly backups: `./deploy.sh backup-schedule install` adds one line to the crontab of the Jetson
user (no sudo; idempotent; other lines kept), `remove` takes it out, `status` shows it and the end
of `~/jobapp-backups/backup.log`. It runs the deployed `~/jobapp/ops/db.sh`, so deploy first. The
line, at `BACKUP_CRON` (default `17 3 * * *`, the Jetson's local time):

```
17 3 * * * JOBAPP_DIR=jobapp BACKUP_DIR=jobapp-backups BACKUP_KEEP=14 "$HOME/jobapp/ops/db.sh" backup >>"$HOME/jobapp-backups/backup.log" 2>&1 # jobapp-backup (./deploy.sh backup-schedule)
```

## Reboot and crash recovery

Every service has `restart: unless-stopped` and `docker.service` is enabled at boot, so the daemon
starts the stack again after a reboot; `cloudflared` comes back with a new URL.
`tests/crash_check.sh` proves the restart half without sudo: it kills each container's main process
with SIGKILL from the host PID namespace (what the OOM killer or a segfault looks like) and waits
for the daemon to restart it healthy. `docker kill` and `docker compose kill` count as a manual
stop, which `unless-stopped` deliberately leaves down. The reboot itself needs sudo, so it is run by
hand:

```bash
tests/reboot_check.sh 2>&1 | tee tasks/evidence/task9/reboot.log   # asks for the sudo password once
```

It reboots the Jetson, waits for ssh with a new boot id, waits for the five services to be healthy,
runs `./deploy.sh smoke` and prints the new URL and the seconds since boot; it passes when the
stack is healthy within 120 seconds of boot.
