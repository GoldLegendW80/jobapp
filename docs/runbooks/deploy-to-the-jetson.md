# Deploy to the Jetson

What `./deploy.sh` does on the Jetson and its commands.

```bash
./deploy.sh                    # preflight, rsync to jetson:~/jobapp, build there, migrate (before the new code starts), compose up --wait, health
./deploy.sh --env              # same, and replace the Jetson's .env with .env.jetson
./deploy.sh logs api           # follow the logs of one service (last 200 lines first)
./deploy.sh ps                 # docker compose ps on the Jetson
./deploy.sh stats              # memory and CPU per service
./deploy.sh ssh                # shell in the deploy directory
./deploy.sh url                # the stable public URL, https://jobapp.taild83d16.ts.net, alone on stdout
./deploy.sh smoke              # checks on the Jetson, then through the public URL; exit 1 if any fails
./deploy.sh smoke --full       # same, plus a database outage and recovery (stops and starts db once)
./deploy.sh backup [--pull]    # verified pg_dump -Fc to jetson:~/jobapp-backups/, then the retention (7 days); --pull copies it to backups/
./deploy.sh backups            # list the dumps on the Jetson
./deploy.sh restore latest     # restore into the scratch database jobapp_restore_check, print row counts
./deploy.sh restore <file> --into-live   # replace the live database (type RESTORE jobapp to confirm)
./deploy.sh backup-schedule install|remove|status   # nightly backup in the Jetson user's crontab; status shows the retention and what is on the Jetson
./deploy.sh admin create <email>   # one-time admin setup link on the public URL (a reset link if that admin exists)
tests/deploy_test.sh           # local tests of deploy.sh: shellcheck, dispatcher, unreachable host, .rsyncignore, url, restore prompt, admin create, the backup retention with a fake clock
tests/migrate_test.sh          # by hand on the Mac, not in CI (real Docker, builds the api image four times): migrate with the new image while the old api serves, failing, hanging and killed migrations, then compose up
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
http://127.0.0.1:18080. `DEPLOY_HOST` and `DEPLOY_DIR` override the target. A checked `main` is
deployed with `ops/deploy-checked.sh` (continuous-delivery.md); `./deploy.sh` stays for the first deploy, a
new `.env`, and deploying from the Mac's own files.
