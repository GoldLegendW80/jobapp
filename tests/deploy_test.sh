#!/usr/bin/env bash
# Local tests of deploy.sh that need no Jetson (platform tasks 4, 8 and 9). Rerunnable, touches
# nothing remote.
#
#   tests/deploy_test.sh
#
# Covers: shellcheck, the subcommand dispatcher, an unreachable host failing fast, the
# .rsyncignore rules (secrets and junk never shipped, a remote .env never deleted by --delete), and
# `url` against a fake ssh that plays the Jetson's docker (the Funnel URL from tailscale status
# alone on stdout; a renamed node warned; not logged in, approval pending, no ts.net name, still
# starting, stopped or missing tailscale all an exit 1 with the reason), the backup and restore
# options refused before any network call, *.dump and backups/ kept out of git and rsync, and the
# typed confirmation of restore --into-live against a fake Jetson, `admin create` (argument
# checks before any network call; the CLI run in the api container with the Funnel URL, quoted),
# `admin unlock` (argument checks first; psql in the db container, values bound, count printed),
# and `jobs import` and `profile import` (argument checks; conversion on the Mac of a synthetic job
# folder, the document streamed to the CLI in the api container; a bad source never sent).
# Issue #81: ops/migrate.sh against a fake docker (a one off container of the new image, the no-op
# at head, a failing migration, a timeout, a kill, a concurrent run, a first install, masked credentials),
# the order of cmd_deploy (build, migrate, compose up), and the deploy step of deploy.yml cut out of
# the workflow and run against the same fakes (backup, then migrate, then up; a failing migration
# stops with the stack untouched; a failed start rolls back without a downgrade).
# Needs Docker (for the pinned shellcheck image) and rsync.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SHELLCHECK_IMAGE="koalaman/shellcheck:v0.11.0"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

passed=0
step() { printf '\n== %s\n' "$*"; }
ok() {
  passed=$((passed + 1))
  printf '   ok: %s\n' "$*"
}
fail() {
  printf '   FAIL: %s\n' "$*" >&2
  exit 1
}

# run <expected exit> <cmd...>: run, keep stdout+stderr in $WORK/out, check the exit code
run() {
  local want="$1" got=0
  shift
  "$@" >"$WORK/out" 2>&1 || got=$?
  [[ "$got" == "$want" ]] || fail "$* exited $got, expected $want: $(head -c 400 "$WORK/out")"
}
out_has() { grep -q -- "$1" "$WORK/out" || fail "output lacks '$1': $(head -c 400 "$WORK/out")"; }

step "shellcheck"
SCRIPTS=(deploy.sh ops/db.sh ops/previous.sh ops/migrate.sh ops/runner/entrypoint.sh ops/runner/job-guard.sh tests/deploy_test.sh tests/jetson_smoke.sh tests/backup_test.sh
  tests/crash_check.sh tests/reboot_check.sh tests/tailscale_test.sh tests/migrate_test.sh)
docker run --rm -v "$ROOT:/mnt:ro" -w /mnt "$SHELLCHECK_IMAGE" "${SCRIPTS[@]}" || fail "shellcheck"
ok "${SCRIPTS[*]} clean"
for f in "${SCRIPTS[@]}"; do
  [[ -x "$f" ]] || fail "$f is not executable"
done
[[ -x deploy.sh ]] || fail "deploy.sh is not executable"
head -n 1 deploy.sh | grep -q '^#!/usr/bin/env bash$' || fail "deploy.sh shebang"
grep -q '^set -euo pipefail$' deploy.sh || fail "deploy.sh lacks set -euo pipefail"
ok "executable, env bash, set -euo pipefail"

step "dispatcher"
run 0 ./deploy.sh --help
out_has "logs <service>"
ok "--help prints usage"
run 2 ./deploy.sh frobnicate
out_has "./deploy.sh"
ok "unknown subcommand exits 2 with the usage"
out_has "backup \[--pull\]"
out_has "restore <file> --into-live"
ok "--help documents backup, restore and backup-schedule"

step "backup and restore options, refused before any network call"
# An unreachable host proves the refusal comes first: past the parsing it would say "cannot reach".
nohost() { env DEPLOY_HOST=nonexistent-host.invalid "$@"; }
run 1 nohost ./deploy.sh backup --bogus
out_has "unknown option for backup: --bogus"
run 1 nohost ./deploy.sh backup extra
out_has "unknown option for backup: extra"
run 1 nohost ./deploy.sh backups extra
out_has "usage: ./deploy.sh backups"
run 1 nohost ./deploy.sh restore
out_has "usage: ./deploy.sh restore <file>"
run 1 nohost ./deploy.sh restore a.dump b.dump
out_has "restore takes one file, got 'a.dump' and 'b.dump'"
run 1 nohost ./deploy.sh restore a.dump --bogus
out_has "unknown option for restore: --bogus"
run 1 nohost ./deploy.sh restore a.dump --into-live --keep
out_has "--keep is for the scratch restore"
run 1 nohost ./deploy.sh restore a.dump --yes
out_has "--yes only confirms --into-live"
run 1 nohost ./deploy.sh restore --drop-scratch a.dump
out_has "--drop-scratch takes no file"
ok "unknown options, extra or missing files, --keep with --into-live, --yes alone, --drop-scratch with a file"
run 1 nohost ./deploy.sh restore a.dump --into-live </dev/null
out_has "run it in a terminal to type the confirmation, or pass --yes"
ok "--into-live without a terminal and without --yes is refused before anything"
for keep in 0 -1 abc 1.5 07 10000 "7 days"; do
  run 1 nohost env BACKUP_KEEP="$keep" ./deploy.sh backup
  out_has "BACKUP_KEEP must be a whole number from 1 to 9999, got '$keep'"
  run 1 nohost env BACKUP_KEEP="$keep" ./deploy.sh backup-schedule install
  out_has "BACKUP_KEEP must be a whole number from 1 to 9999, got '$keep'"
done
run 1 nohost env BACKUP_DIR=jobapp/backups ./deploy.sh backup
out_has "must not be inside DEPLOY_DIR"
run 1 nohost env BACKUP_DIR='~/my backups' ./deploy.sh backups
out_has "BACKUP_DIR may only hold"
run 1 nohost env BACKUP_DIR=/ ./deploy.sh backup
out_has "BACKUP_DIR must name a directory"
ok "BACKUP_KEEP 0, -1, abc, 1.5, 07, 10000 and '7 days' refused by backup and by backup-schedule install; BACKUP_DIR inside the deploy directory, with a space, or / refused"
for args in "" "bogus" "install now"; do
  # shellcheck disable=SC2086 # word splitting wanted
  run 1 nohost ./deploy.sh backup-schedule $args
  out_has "usage: ./deploy.sh backup-schedule install|remove|status"
done
run 1 nohost env BACKUP_CRON='17 3 * *' ./deploy.sh backup-schedule status
out_has "BACKUP_CRON must be five cron fields"
run 1 nohost env BACKUP_CRON='17 3 * * * ; rm -rf ~' ./deploy.sh backup-schedule install
out_has "BACKUP_CRON must be five cron fields"
ok "backup-schedule needs one of install, remove, status; BACKUP_CRON must be five plain fields (no injection)"
for cmd in "backup" "backups" "restore x.dump" "backup-schedule status"; do
  # shellcheck disable=SC2086
  run 1 nohost ./deploy.sh $cmd
  out_has "cannot reach nonexistent-host.invalid"
done
ok "backup, backups, restore and backup-schedule fail fast on an unreachable host"
run 1 ./deploy.sh url extra
out_has "usage: ./deploy.sh url"
run 1 ./deploy.sh smoke --bogus
out_has "unknown option for smoke: --bogus"
ok "url takes no argument, smoke refuses an unknown option, both before any network call"
for args in "admin" "admin create" "admin list x@y.z" "admin create a@b.c extra" "admin delete a@b.c"; do
  # shellcheck disable=SC2086 # word splitting wanted
  run 1 nohost ./deploy.sh $args
  out_has "usage: ./deploy.sh admin create <email>"
done
for email in "no-at-sign" "-x@example.org" "--base-url=https://evil@x" "" "$(printf 'a%.0s' {1..320})@x.org"; do
  run 1 nohost ./deploy.sh admin create "$email"
  out_has "admin create needs one email address"
done
for email in "a b@example.org" $'a\tb@example.org' $'a@example.org\nrm' $'a\x01@example.org'; do
  run 1 nohost ./deploy.sh admin create "$email"
  out_has "contains whitespace or control characters"
done
run 1 nohost ./deploy.sh admin create zoe@example.org
out_has "cannot reach nonexistent-host.invalid"
ok "admin create: wrong or missing action, extra arguments, no @, leading dash, over 320 characters, whitespace or control characters refused before any network call; a good email reaches the ssh check"
run 0 ./deploy.sh --help
out_has "admin create <email>"
ok "--help documents admin create"
for args in "admin unlock" "admin unlock --ip 203.0.113.9" "admin unlock no-at-sign"; do
  # shellcheck disable=SC2086 # word splitting wanted
  run 1 nohost ./deploy.sh $args
  out_has "admin unlock needs one email address"
done
for args in "admin unlock a@b.c d@e.f" "admin unlock a@b.c --ip" "admin unlock --ip"; do
  # shellcheck disable=SC2086 # word splitting wanted
  run 1 nohost ./deploy.sh $args
  out_has "usage: ./deploy.sh admin unlock <email> \[--ip <ip>\]"
done
run 1 nohost ./deploy.sh admin unlock a@b.c --bogus
out_has "unknown option for admin unlock: --bogus"
run 1 nohost ./deploy.sh admin unlock -x@example.org
out_has "unknown option for admin unlock: -x@example.org"
for email in "a b@example.org" $'a@example.org\nrm' "$(printf 'a%.0s' {1..320})@x.org"; do
  run 1 nohost ./deploy.sh admin unlock "$email"
  out_has "admin unlock"
  grep -q "cannot reach" "$WORK/out" && fail "'$email' reached the ssh check"
done
for ip in "1.2.3.4; drop table users" "'1.2.3.4'" "$(printf '1%.0s' {1..46})" "localhost"; do
  run 1 nohost ./deploy.sh admin unlock a@b.c --ip "$ip"
  out_has "is not an IP address"
done
run 1 nohost ./deploy.sh admin unlock zoe@example.org --ip 2001:db8::1
out_has "cannot reach nonexistent-host.invalid"
run 1 nohost ./deploy.sh admin unlock --ip 203.0.113.9 zoe@example.org
out_has "cannot reach nonexistent-host.invalid"
ok "admin unlock: no email, two emails, --ip without a value, unknown option, leading dash, whitespace, over 320 characters and a non address --ip refused before any network call; a good email with an IPv4 or IPv6 --ip, in either order, reaches the ssh check"
run 0 ./deploy.sh --help
out_has "admin unlock <email> \[--ip <ip>\]"
ok "--help documents admin unlock"
for module in jobs profile; do
  for args in "$module" "$module export a@b.c" "$module import" "$module import a@b.c extra"; do
    # shellcheck disable=SC2086 # word splitting wanted
    run 1 nohost ./deploy.sh $args
    out_has "usage: ./deploy.sh $module import <email>"
  done
  for email in "no-at-sign" "-x@example.org" "a b@example.org"; do
    run 1 nohost ./deploy.sh "$module" import "$email"
    grep -q "$module import" "$WORK/out" || fail "$module import $email: $(cat "$WORK/out")"
  done
  run 0 ./deploy.sh --help
  out_has "$module import <email>"
done
ok "jobs import and profile import: wrong action, extra arguments and bad emails refused before any network call; --help documents both"
for args in "jobs mark-overrides" "jobs mark-overrides a@b.c --force" "jobs mark-overrides a@b.c --apply x" "jobs mark-overrides --apply a@b.c"; do
  # shellcheck disable=SC2086 # word splitting wanted
  run 1 nohost ./deploy.sh $args
  grep -q "mark-overrides" "$WORK/out" || fail "jobs mark-overrides $args: $(cat "$WORK/out")"
done
run 0 ./deploy.sh --help
out_has "jobs mark-overrides <email> \[--apply\]"
ok "jobs mark-overrides: missing email, unknown option, extra argument refused before any network call; --help documents it"
run 1 ./deploy.sh deploy --bogus-flag
out_has "unknown option for deploy: --bogus-flag"
ok "unknown deploy option refused before any network call"
run 1 ./deploy.sh logs
out_has "usage: ./deploy.sh logs <service>"
run 1 ./deploy.sh logs api caddy
ok "logs needs exactly one service"
run 1 env DEPLOY_DIR=/ ./deploy.sh ps
out_has "DEPLOY_DIR must name a directory"
ok "DEPLOY_DIR=/ refused"

step "runner: arguments and settings refused before any network call"
for args in "runner" "runner bogus" "runner install extra" "runner status remove"; do
  # shellcheck disable=SC2086 # word splitting wanted
  run 1 env DEPLOY_HOST=nonexistent-host.invalid ./deploy.sh $args
  out_has "usage: ./deploy.sh runner install|status|remove"
done
run 1 env DEPLOY_HOST=nonexistent-host.invalid JOBAPP_RUNNER_REPO=no-slash ./deploy.sh runner status
out_has "JOBAPP_RUNNER_REPO must be owner/name"
run 1 env DEPLOY_HOST=nonexistent-host.invalid JOBAPP_RUNNER_NAME='a b' ./deploy.sh runner status
out_has "JOBAPP_RUNNER_NAME may only hold"
run 1 env DEPLOY_HOST=nonexistent-host.invalid DEPLOY_DIR=apps/jobapp ./deploy.sh runner install
out_has "folder directly in the Jetson home"
ok "runner: no action, unknown action, extra arguments, bad repo, name and folder refused before ssh"
if command -v gh >/dev/null; then
  run 1 env DEPLOY_HOST=nonexistent-host.invalid ./deploy.sh runner status
  out_has "cannot reach nonexistent-host.invalid"
  ok "runner status: an unreachable host fails with the ssh message"
fi

step "runner image: the job guard is baked in and refuses every job but deploy.yml from main"
grep -q '^ENV ACTIONS_RUNNER_HOOK_JOB_STARTED=/usr/local/bin/runner-job-guard.sh$' ops/runner/Dockerfile ||
  fail "ops/runner/Dockerfile does not set ACTIONS_RUNNER_HOOK_JOB_STARTED"
grep -q '^COPY --chmod=755 job-guard.sh /usr/local/bin/runner-job-guard.sh$' ops/runner/Dockerfile ||
  fail "ops/runner/Dockerfile does not copy job-guard.sh"
grep -q 'Dockerfile entrypoint.sh job-guard.sh' deploy.sh || fail "runner_install does not send job-guard.sh"
guard() { # guard <exit> <repository> <workflow ref> <event>
  run "$1" env -i PATH="$PATH" GITHUB_REPOSITORY="$2" GITHUB_WORKFLOW_REF="$3" GITHUB_EVENT_NAME="$4" \
    ops/runner/job-guard.sh
}
main_ref="GoldLegendW80/jobapp/.github/workflows/deploy.yml@refs/heads/main"
guard 1 GoldLegendW80/jobapp "$main_ref" workflow_run
out_has "refused: event workflow_run"
guard 0 GoldLegendW80/jobapp "$main_ref" workflow_dispatch
guard 1 GoldLegendW80/jobapp "$main_ref" push
out_has "refused: event push"
guard 1 GoldLegendW80/jobapp "$main_ref" pull_request
guard 1 GoldLegendW80/jobapp "GoldLegendW80/jobapp/.github/workflows/deploy.yml@refs/heads/evil" workflow_dispatch
out_has "refused: workflow"
guard 1 GoldLegendW80/jobapp "GoldLegendW80/jobapp/.github/workflows/ci.yml@refs/heads/main" workflow_dispatch
guard 1 GoldLegendW80/jobapp "GoldLegendW80/jobapp/.github/workflows/deploy.yml@refs/pull/1/merge" pull_request
guard 1 someone/fork "someone/fork/.github/workflows/deploy.yml@refs/heads/main" workflow_dispatch
out_has "refused: repository"
guard 1 "" "" ""
ok "hook set in the image; allowed: deploy.yml@main on workflow_dispatch; refused: workflow_run (issue #116), push, pull_request, another branch, another workflow, a fork, no context"

step "runner install and remove against a fake Jetson and a fake gh: the token never shows"
# A canary token: install and remove must pass it to the container on stdin only, never on a
# command line (here: the remote command string) and never in deploy.sh's own output.
CANARY="CANARYTOKEN0123456789abcdefXYZ"
mkdir -p "$WORK/bin3"
cat >"$WORK/bin3/ssh" <<'FAKE'
#!/usr/bin/env bash
cmd="${*: -1}"
echo "$cmd" >>"$FAKE_CALLS"
case "$cmd" in
  true) ;;
  *"id -un"*) printf 'u\n1000\n1000\n/home/u\n995\n' ;;
  *"docker build"*) cat >/dev/null ;;
  *"--entrypoint test"*) exit 1 ;;
  *" configure"* | *" remove") cat >>"$FAKE_STDIN" ;;
  *"docker image inspect"*) exit 0 ;;
esac
exit 0
FAKE
cat >"$WORK/bin3/gh" <<'FAKE'
#!/usr/bin/env bash
case "$*" in
  *registration-token* | *remove-token*) echo "$FAKE_TOKEN" ;;
  *"actions/runners --paginate"*) [[ "$FAKE_LISTED" == 1 ]] && echo "7 online false self-hosted,Linux,ARM64,jetson" ;;
esac
exit 0
FAKE
chmod +x "$WORK/bin3/ssh" "$WORK/bin3/gh"
fake3() {
  env PATH="$WORK/bin3:$PATH" FAKE_CALLS="$WORK/calls3" FAKE_STDIN="$WORK/stdin3" \
    FAKE_TOKEN="$CANARY" DEPLOY_HOST=fakejetson "$@"
}
: >"$WORK/calls3"
: >"$WORK/stdin3"
run 0 fake3 FAKE_LISTED=1 ./deploy.sh runner install
out_has "online on GitHub"
grep -q "docker run --rm -i .* configure" "$WORK/calls3" || fail "no configure call: $(cat "$WORK/calls3")"
grep -q "docker run -d --name jobapp-runner --restart unless-stopped" "$WORK/calls3" || fail "no runner container"
grep -q -- "--group-add 995" "$WORK/calls3" || fail "docker group not added"
run 0 fake3 FAKE_LISTED=0 ./deploy.sh runner remove
grep -q "docker run --rm -i .* remove" "$WORK/calls3" || fail "no remove call: $(cat "$WORK/calls3")"
[[ "$(grep -c "$CANARY" "$WORK/stdin3")" == 2 ]] || fail "the token did not reach configure and remove on stdin"
grep -q "$CANARY" "$WORK/calls3" && fail "the token was on a remote command line"
grep -q "$CANARY" "$WORK/out" && fail "deploy.sh printed the token"
ok "configure and remove get the token on stdin only; it is on no command line and in no output"

step "unreachable host fails fast with a clear message"
for host in nonexistent-host.invalid 192.0.2.1; do
  start=$SECONDS
  run 1 env DEPLOY_HOST="$host" ./deploy.sh
  out_has "cannot reach $host over ssh within 5s"
  ((SECONDS - start <= 10)) || fail "$host took $((SECONDS - start))s"
  ok "$host: exit 1 in $((SECONDS - start))s"
done
run 1 env DEPLOY_HOST=nonexistent-host.invalid ./deploy.sh logs api
out_has "cannot reach nonexistent-host.invalid"
run 1 env DEPLOY_HOST=nonexistent-host.invalid ./deploy.sh ps
for cmd in url smoke; do
  run 1 env DEPLOY_HOST=nonexistent-host.invalid ./deploy.sh "$cmd"
  out_has "cannot reach nonexistent-host.invalid"
done
ok "logs, ps, url and smoke fail the same way"

step ".rsyncignore: what ships and what --delete keeps"
dst="$WORK/dst"
mkdir -p "$dst/api" "$WORK/src/web/node_modules/x" "$WORK/src/api/.venv" "$WORK/src/api/app/__pycache__" \
  "$WORK/src/.git" "$WORK/src/tasks/evidence/task4" "$WORK/src/web/dist" "$WORK/src/.claude"
printf 'POSTGRES_PASSWORD=remote-secret\n' >"$dst/.env"
printf 'stale\n' >"$dst/api/removed.py"
cp .rsyncignore "$WORK/src/"
printf 'services: {}\n' >"$WORK/src/compose.yml"
printf 'x\n' | tee "$WORK/src/.env" "$WORK/src/.env.jetson" "$WORK/src/web/node_modules/x/i.js" \
  "$WORK/src/api/.venv/p" "$WORK/src/api/app/__pycache__/m.pyc" "$WORK/src/.git/HEAD" \
  "$WORK/src/tasks/evidence/task4/a.png" "$WORK/src/web/dist/index.html" "$WORK/src/.claude/s" \
  "$WORK/src/.DS_Store" "$WORK/src/api/app/main.py" "$WORK/src/café ünïcode.txt" >/dev/null
mkdir -p "$WORK/src/backups"
printf 'PGDMP\n' | tee "$WORK/src/backups/jobapp-2026-10-08T120000.dump" "$WORK/src/stray.dump" >/dev/null
rsync -a --delete --exclude-from="$WORK/src/.rsyncignore" "$WORK/src/" "$dst/"
[[ "$(cat "$dst/.env")" == "POSTGRES_PASSWORD=remote-secret" ]] || fail "remote .env changed or deleted"
ok "remote .env kept byte for byte through --delete"
[[ ! -e "$dst/api/removed.py" ]] || fail "--delete left a stale file"
ok "stale remote file deleted"
[[ -f "$dst/api/app/main.py" && -f "$dst/compose.yml" && -f "$dst/café ünïcode.txt" ]] || fail "source files missing"
ok "code and unicode file names shipped"
for junk in .env.jetson web/node_modules api/.venv api/app/__pycache__ .git tasks/evidence web/dist .claude .DS_Store \
  backups stray.dump; do
  [[ ! -e "$dst/$junk" ]] || fail "$junk was shipped"
done
ok "never shipped: .env.jetson, node_modules, .venv, __pycache__, .git, evidence, dist, .claude, .DS_Store, backups/, *.dump"
for f in .env.jetson backups/jobapp-2026-10-08T120000.dump stray.dump api/x.dump; do
  git check-ignore -q "$f" || fail "$f is not git-ignored"
done
ok ".env.jetson, backups/ and any *.dump are git-ignored"

step "url against a fake Jetson (fake ssh on PATH)"
# The fake answers the remote commands deploy.sh sends: the tailscale container id, its state,
# and its `tailscale status --json --peers=false`, from FAKE_* variables; it records every status
# command.
mkdir -p "$WORK/bin"
cat >"$WORK/bin/ssh" <<'FAKE'
#!/usr/bin/env bash
cmd="${*: -1}"
case "$cmd" in
  *"ps -a -q tailscale"*) [[ -z "${FAKE_TS_ID:-}" ]] || echo "$FAKE_TS_ID" ;;
  *"docker inspect"*) echo "${FAKE_TS_STATE:-running}" ;;
  *"tailscale status --json"*)
    echo "$cmd" >>"$FAKE_CALLS"
    cat "$FAKE_TS_JSON"
    ;;
  *"exec -T db sh -c"*"psql"*)
    echo "$cmd" >>"$FAKE_CALLS"
    cat >>"$FAKE_CALLS"
    echo "${FAKE_UNLOCKED:-3}"
    ;;
  *"exec -T api python -m app.jobs.cli import"* | *"exec -T api python -m app.profile.cli import"*)
    echo "$cmd" >>"$FAKE_CALLS"
    # What reached the Jetson on stdin: kept whole, to compare with the local conversion.
    cat >"$FAKE_STDIN"
    echo "Imported 2 rows: 2 created, 0 updated, 0 unchanged, 0 skipped."
    exit "${FAKE_CLI_EXIT:-0}"
    ;;
  *"exec -T api python -m app.jobs.cli mark-overrides"*)
    echo "$cmd" >>"$FAKE_CALLS"
    echo "Dry run: would mark 0 fits as set by hand (they differ from the computed fit)."
    exit "${FAKE_CLI_EXIT:-0}"
    ;;
  *"exec -T api python -m app.identity.cli"*)
    echo "$cmd" >>"$FAKE_CALLS"
    echo "Setup link for zoe@example.org, valid 7 days, single use:"
    echo "  https://jobapp.taild83d16.ts.net/invite/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    exit "${FAKE_CLI_EXIT:-0}"
    ;;
esac
exit 0
FAKE
chmod +x "$WORK/bin/ssh"
fake() { env PATH="$WORK/bin:$PATH" FAKE_CALLS="$WORK/calls" FAKE_STDIN="$WORK/stdin" DEPLOY_HOST=fakejetson "$@"; }
# run_split <expected exit> <cmd...>: like run, stdout and stderr kept apart
run_split() {
  local want="$1" got=0
  shift
  "$@" >"$WORK/stdout" 2>"$WORK/stderr" || got=$?
  [[ "$got" == "$want" ]] || fail "$* exited $got, expected $want: $(head -c 400 "$WORK/stderr")"
}
# ts_status <BackendState> <DNSName> <HostName> [AuthURL]: a `tailscale status --json --peers=false`
# as v1.102 prints it (indented, Self after the top-level fields, CertDomains and a User map after).
ts_status() {
  cat <<JSON
{
  "Version": "1.102.5-t1b2c3d4e-gabcdef012",
  "TUN": false,
  "BackendState": "$1",
  "HaveNodeKey": true,
  "AuthURL": "${4:-}",
  "TailscaleIPs": [
    "100.101.102.103",
    "fd7a:115c:a1e0::1"
  ],
  "Self": {
    "ID": "nABCDEF1CNTRL",
    "PublicKey": "nodekey:0000000000000000000000000000000000000000000000000000000000000000",
    "HostName": "$3",
    "DNSName": "$2",
    "OS": "linux",
    "UserID": 1234567890,
    "TailscaleIPs": [
      "100.101.102.103"
    ],
    "Online": true,
    "Capabilities": [
      "funnel",
      "https"
    ]
  },
  "Health": [],
  "MagicDNSSuffix": "taild83d16.ts.net",
  "CurrentTailnet": {
    "Name": "hthautin@example.org",
    "MagicDNSSuffix": "taild83d16.ts.net",
    "MagicDNSEnabled": true
  },
  "CertDomains": [
    "otherhost.taild83d16.ts.net"
  ],
  "User": {
    "1234567890": {
      "ID": 1234567890,
      "LoginName": "someone@example.org",
      "DisplayName": "Someone"
    }
  },
  "ClientVersion": null
}
JSON
}
ts_status Running jobapp.taild83d16.ts.net. jobapp >"$WORK/ts.json"
: >"$WORK/calls"
run_split 0 fake FAKE_TS_ID=abc123 FAKE_TS_JSON="$WORK/ts.json" ./deploy.sh url
[[ "$(cat "$WORK/stdout")" == "https://jobapp.taild83d16.ts.net" ]] ||
  fail "stdout is not exactly the URL: $(cat "$WORK/stdout")"
[[ "$(wc -l <"$WORK/stdout" | tr -d ' ')" == "1" ]] || fail "stdout has more than one line"
[[ ! -s "$WORK/stderr" ]] || fail "stderr not empty on success: $(cat "$WORK/stderr")"
url="$(fake FAKE_TS_ID=abc123 FAKE_TS_JSON="$WORK/ts.json" ./deploy.sh url 2>/dev/null)"
[[ "$url" == "https://jobapp.taild83d16.ts.net" ]] || fail "\$(./deploy.sh url) gave '$url'"
ok "stdout is https://<Self.DNSName> alone, trailing dot dropped, one line, so \$(./deploy.sh url) works"
grep -qF -- "docker exec abc123 tailscale status --json --peers=false" "$WORK/calls" ||
  fail "status not asked of the running container: $(cat "$WORK/calls")"
ok "the name comes from tailscale status --json --peers=false in the tailscale container (CertDomains and users ignored)"

# Another device already holds the name: the node became jobapp-1, the URL moved. Printed, with a
# warning on stderr, so OAuth redirect URIs are fixed before anyone wonders why sign in fails.
ts_status Running jobapp-1.taild83d16.ts.net. jobapp >"$WORK/ts1.json"
run_split 0 fake FAKE_TS_ID=abc123 FAKE_TS_JSON="$WORK/ts1.json" ./deploy.sh url
[[ "$(cat "$WORK/stdout")" == "https://jobapp-1.taild83d16.ts.net" ]] || fail "renamed node: $(cat "$WORK/stdout")"
grep -q "the node is jobapp-1, not jobapp: another device holds that name" "$WORK/stderr" ||
  fail "no warning for a renamed node: $(cat "$WORK/stderr")"
ok "a node renamed by a name clash (jobapp-1): URL printed, warning on stderr"

ts_status NeedsLogin "" jobapp "https://login.tailscale.com/a/0123456789abc" >"$WORK/login.json"
run_split 1 fake FAKE_TS_ID=abc123 FAKE_TS_JSON="$WORK/login.json" ./deploy.sh url
[[ ! -s "$WORK/stdout" ]] || fail "stdout not empty on failure: $(cat "$WORK/stdout")"
grep -q "tailscale on fakejetson is not logged in (NeedsLogin): put a fresh one-time TS_AUTHKEY in jobapp/.env" "$WORK/stderr" ||
  fail "NeedsLogin message: $(cat "$WORK/stderr")"
grep -qF "or log in once at https://login.tailscale.com/a/0123456789abc" "$WORK/stderr" || fail "no login link: $(cat "$WORK/stderr")"
ok "not logged in (no key, expired key): exit 1 at once, says to set TS_AUTHKEY, shows the login link"

ts_status NeedsMachineAuth "" jobapp >"$WORK/approve.json"
run_split 1 fake FAKE_TS_ID=abc123 FAKE_TS_JSON="$WORK/approve.json" ./deploy.sh url
grep -q "waits for the device to be approved" "$WORK/stderr" || fail "NeedsMachineAuth: $(cat "$WORK/stderr")"
ok "device approval pending: exit 1 with the reason"

ts_status Running "" jobapp >"$WORK/nodns.json"
run_split 1 fake FAKE_TS_ID=abc123 FAKE_TS_JSON="$WORK/nodns.json" ./deploy.sh url
grep -q "reports no ts.net name ('none'): is MagicDNS on" "$WORK/stderr" || fail "no DNSName: $(cat "$WORK/stderr")"
ts_status Running 'evil.example.org.' jobapp >"$WORK/evil.json"
run_split 1 fake FAKE_TS_ID=abc123 FAKE_TS_JSON="$WORK/evil.json" ./deploy.sh url
[[ ! -s "$WORK/stdout" ]] || fail "accepted a name outside ts.net: $(cat "$WORK/stdout")"
grep -q "reports no ts.net name ('evil.example.org')" "$WORK/stderr" || fail "foreign name: $(cat "$WORK/stderr")"
ok "connected without a ts.net name (MagicDNS off, or a foreign name): exit 1, nothing on stdout"

ts_status Starting "" jobapp >"$WORK/starting.json"
start=$SECONDS
run_split 1 fake FAKE_TS_ID=abc123 FAKE_TS_JSON="$WORK/starting.json" DEPLOY_URL_TIMEOUT=3 ./deploy.sh url
[[ ! -s "$WORK/stdout" ]] || fail "stdout not empty on failure: $(cat "$WORK/stdout")"
grep -q "waiting up to 3s for tailscale to connect" "$WORK/stderr" || fail "no waiting note on stderr"
grep -q "tailscale (running, Starting) did not connect within 3s" "$WORK/stderr" || fail "timeout message: $(cat "$WORK/stderr")"
((SECONDS - start >= 3 && SECONDS - start <= 10)) || fail "timeout took $((SECONDS - start))s"
: >"$WORK/empty.json"
run_split 1 fake FAKE_TS_ID=abc123 FAKE_TS_JSON="$WORK/empty.json" DEPLOY_URL_TIMEOUT=1 ./deploy.sh url
grep -q "tailscale (running, no status yet) did not connect within 1s" "$WORK/stderr" || fail "no status: $(cat "$WORK/stderr")"
ok "still starting, or no status from the CLI: waits, then exit 1 after the timeout, stdout empty"

run_split 1 fake FAKE_TS_ID=abc123 FAKE_TS_STATE=exited FAKE_TS_JSON="$WORK/ts.json" ./deploy.sh url
grep -q "tailscale is exited on fakejetson, not running (./deploy.sh logs tailscale" "$WORK/stderr" || fail "exited: $(cat "$WORK/stderr")"
[[ ! -s "$WORK/stdout" ]] || fail "stdout not empty"
run_split 1 fake FAKE_TS_ID= FAKE_TS_JSON="$WORK/ts.json" ./deploy.sh url
grep -q "tailscale is not running on fakejetson: no container" "$WORK/stderr" || fail "missing: $(cat "$WORK/stderr")"
[[ ! -s "$WORK/stdout" ]] || fail "stdout not empty"
ok "tailscale container stopped or missing: exit 1 with a clear message, stdout empty"

step "admin create against the fake Jetson"
: >"$WORK/calls"
run_split 0 fake FAKE_TS_ID=abc123 FAKE_TS_JSON="$WORK/ts.json" ./deploy.sh admin create Zoe@Example.org
grep -q "https://jobapp.taild83d16.ts.net/invite/A\{43\}$" "$WORK/stdout" ||
  fail "full link not on stdout: $(cat "$WORK/stdout")"
grep -qF -- "docker compose -f compose.yml --profile tunnel exec -T api python -m app.identity.cli create-admin Zoe@Example.org --base-url https://jobapp.taild83d16.ts.net" "$WORK/calls" ||
  fail "remote command: $(cat "$WORK/calls")"
ok "admin create runs the CLI in the api container with --base-url set to the Funnel URL, the full link on stdout"
: >"$WORK/calls"
run_split 0 fake FAKE_TS_ID=abc123 FAKE_TS_JSON="$WORK/ts.json" ./deploy.sh admin create "o'brien+\$(id)@example.org"
grep -qF -- "create-admin o\'brien+\\\$\(id\)@example.org --base-url" "$WORK/calls" ||
  fail "email not quoted for the remote shell: $(cat "$WORK/calls")"
ok "quotes, \$ and parentheses in the email reach the CLI quoted, never run by the remote shell"
: >"$WORK/calls"
run_split 1 fake FAKE_TS_ID= FAKE_TS_JSON="$WORK/ts.json" ./deploy.sh admin create zoe@example.org
grep -q "tailscale is not running on fakejetson" "$WORK/stderr" || fail "no tailscale: $(cat "$WORK/stderr")"
grep -q "exec -T api" "$WORK/calls" && fail "the CLI ran without a public URL"
ok "no public URL: exit 1 before the CLI runs"
run_split 2 fake FAKE_TS_ID=abc123 FAKE_TS_JSON="$WORK/ts.json" FAKE_CLI_EXIT=2 ./deploy.sh admin create zoe@example.org
ok "the CLI's exit code (2, invalid email) is passed through"

step "admin unlock against the fake Jetson"
: >"$WORK/calls"
run_split 0 fake ./deploy.sh admin unlock "O'Brien+\$(id)@Example.org" --ip 203.0.113.9
grep -qF "removed 3 failed sign in attempt(s) of O'Brien+\$(id)@Example.org and of 203.0.113.9 in the last 15 minutes" "$WORK/stdout" ||
  fail "count not printed: $(cat "$WORK/stdout")"
grep -qF -- "docker compose -f compose.yml --profile tunnel exec -T db sh -c psql\ -X\ -q\ -v\ ON_ERROR_STOP=1" "$WORK/calls" ||
  fail "remote command: $(cat "$WORK/calls")"
grep -qF -- "sh O\'Brien+\\\$\(id\)@Example.org 203.0.113.9" "$WORK/calls" ||
  fail "email not quoted for the remote shell: $(cat "$WORK/calls")"
grep -qF -- "email = :'email'" "$WORK/calls" || fail "the SQL does not bind the email: $(cat "$WORK/calls")"
grep -qF -- "not success" "$WORK/calls" || fail "the SQL is not limited to failures"
grep -qF -- "interval '15 minutes'" "$WORK/calls" || fail "the SQL is not limited to the window"
grep -q "Brien" <<<"$(grep -F "email = " "$WORK/calls")" && fail "the email was spliced into the SQL"
ok "admin unlock runs psql in the db container with the email and address as quoted arguments bound as psql variables, failures in the 15 minute window only, and prints the count"
: >"$WORK/calls"
run_split 0 fake ./deploy.sh admin unlock zoe@example.org
grep -qF -- "sh zoe@example.org ''" "$WORK/calls" || fail "no empty address argument: $(cat "$WORK/calls")"
grep -qF "removed 3 failed sign in attempt(s) of zoe@example.org in the last 15 minutes" "$WORK/stdout" ||
  fail "count without --ip: $(cat "$WORK/stdout")"
ok "without --ip the address is passed empty and only the email's rows go"
run_split 1 fake env FAKE_UNLOCKED="ERROR: nope" ./deploy.sh admin unlock zoe@example.org
grep -q "unexpected answer from psql" "$WORK/stderr" || fail "garbage answer: $(cat "$WORK/stderr")"
ok "an answer that is not a count fails with exit 1"
step "jobs import and profile import against the fake Jetson"
# A small synthetic job folder: data/jobs.csv with two rows, one with a posting and a cv.pdf.
src="$WORK/jobfolder"
mkdir -p "$src/data/jobs/acme-ai-engineer"
printf '%s\n' "id,company,title,location,type,url,found_on,status,applied_on,fit,competencies,gaps,description,jd,cv,letter,report,notes" \
  'acme-ai-engineer,Acme,AI Engineer,Paris,CDI,https://e.org/1,2026-10-07,applied,2026-10-08,88,"agents, python",,,,,,,"a note, with a comma"' \
  'beta-ml,Beta,ML,Lyon,,https://e.org/2,2026-10-07,found,,,,,,,,,,' >"$src/data/jobs.csv"
printf 'COMPANY: Acme\nTITLE: AI Engineer\n\nBuild agents.\n' >"$src/data/jobs/acme-ai-engineer/posting.txt"
printf '%%PDF-1.7\nsynthetic\n' >"$src/data/jobs/acme-ai-engineer/cv.pdf"
: >"$WORK/calls"
rm -f "$WORK/stdin"
run_split 0 fake JOBAPP_SOURCE_DIR="$src" ./deploy.sh jobs import Zoe@Example.org
grep -qF -- "docker compose -f compose.yml --profile tunnel exec -T api python -m app.jobs.cli import --email Zoe@Example.org --file -" "$WORK/calls" ||
  fail "remote command: $(cat "$WORK/calls")"
grep -q '"format": "jobapp-jobs"' "$WORK/stdin" || fail "the document did not reach the Jetson on stdin"
grep -q '"slug": "beta-ml"' "$WORK/stdin" || fail "rows missing from the document"
grep -q "Converted 2 rows (applied 1, found 1), 1 posting texts, 1 files" "$WORK/stdout" ||
  fail "conversion summary: $(cat "$WORK/stdout")"
grep -q "Imported 2 rows" "$WORK/stdout" || fail "the CLI's summary is not shown: $(cat "$WORK/stdout")"
ok "jobs import converts JOBAPP_SOURCE_DIR/data on the Mac, then streams the document to app.jobs.cli import in the api container"
ls /tmp/jobapp-import.* >/dev/null 2>&1 && fail "the temporary document was left in /tmp"
ok "the temporary document is removed afterwards"
run_split 2 fake JOBAPP_SOURCE_DIR="$src" FAKE_CLI_EXIT=2 ./deploy.sh jobs import zoe@example.org
ok "the import CLI's exit code is passed through"
: >"$WORK/calls"
run_split 0 fake ./deploy.sh jobs mark-overrides zoe@example.org
grep -qF -- "exec -T api python -m app.jobs.cli mark-overrides --email zoe@example.org" "$WORK/calls" ||
  fail "mark-overrides remote command: $(cat "$WORK/calls")"
grep -qF -- "--apply" "$WORK/calls" && fail "a dry run sent --apply: $(cat "$WORK/calls")"
grep -q "(dry run)" "$WORK/stdout" "$WORK/stderr" || fail "the dry run is not announced"
: >"$WORK/calls"
run_split 0 fake ./deploy.sh jobs mark-overrides zoe@example.org --apply
grep -qF -- "mark-overrides --email zoe@example.org --apply" "$WORK/calls" ||
  fail "mark-overrides --apply remote command: $(cat "$WORK/calls")"
run_split 2 fake FAKE_CLI_EXIT=2 ./deploy.sh jobs mark-overrides zoe@example.org
ok "jobs mark-overrides runs app.jobs.cli mark-overrides in the api container, dry run unless --apply, exit code passed through"
: >"$WORK/calls"
run_split 1 fake JOBAPP_SOURCE_DIR="$WORK/empty-folder" ./deploy.sh jobs import zoe@example.org
grep -q "is not a folder (set JOBAPP_SOURCE_DIR" "$WORK/stderr" || fail "missing source: $(cat "$WORK/stderr")"
mkdir -p "$WORK/nocsv/data"
run_split 1 fake JOBAPP_SOURCE_DIR="$WORK/nocsv" ./deploy.sh jobs import zoe@example.org
grep -q "conversion failed, nothing was sent" "$WORK/stderr" || fail "bad source: $(cat "$WORK/stderr")"
run_split 1 fake JOBAPP_SOURCE_DIR="$WORK/nocsv" ./deploy.sh profile import zoe@example.org
grep -q "profile import: conversion failed, nothing was sent" "$WORK/stderr" || fail "bad profile source: $(cat "$WORK/stderr")"
[[ ! -s "$WORK/calls" ]] || fail "a failed conversion still reached the Jetson: $(cat "$WORK/calls")"
ok "a missing or unconvertible source fails before any remote call (jobs and profile)"

step "restore --into-live confirmation, against a fake Jetson"
# This fake swallows the ops/db.sh streamed to `bash -s` and records the command; it answers the
# port and health probes that follow a live restore. `script` gives deploy.sh a terminal, and the
# answer is typed two seconds later, once the prompt is up.
mkdir -p "$WORK/bin2"
cat >"$WORK/bin2/ssh" <<'FAKE'
#!/usr/bin/env bash
cmd="${*: -1}"
case "$cmd" in
  *"bash -s --"*)
    cat >/dev/null
    echo "$cmd" >>"$FAKE_CALLS"
    [[ "$cmd" != *" verify latest" ]] || echo "file=/home/u/jobapp-backups/jobapp-2026-10-08T120000.dump"
    ;;
  *"HTTP_PORT="*) echo 80 ;;
  *"/api/health"*) echo '{"status":"ok"}' ;;
  *"/some/route"*) echo '<div id="root"></div>' ;;
esac
exit 0
FAKE
chmod +x "$WORK/bin2/ssh"
fake2() { env -u BACKUP_KEEP -u BACKUP_ACCEPT_SHRINK PATH="$WORK/bin2:$PATH" FAKE_CALLS="$WORK/calls2" DEPLOY_HOST=fakejetson "$@"; }
typed() { # typed <answer> <cmd...>: run cmd against the fake in a pseudo terminal, type the answer
  local answer="$1"
  shift
  (
    sleep 2
    printf '%s\n' "$answer"
    sleep 1
  ) | in_pty env PATH="$WORK/bin2:$PATH" FAKE_CALLS="$WORK/calls2" DEPLOY_HOST=fakejetson \
    "$@" >"$WORK/out" 2>&1 || true
}
in_pty() { # in_pty <cmd...>: run cmd in a pseudo terminal, BSD script (macOS) or util-linux (CI)
  if script --version 2>/dev/null | grep -q util-linux; then
    script -q -e -c "$(printf '%q ' "$@")" /dev/null
  else
    script -q /dev/null "$@"
  fi
}
: >"$WORK/calls2"
typed "restore jobapp" ./deploy.sh restore latest --into-live
out_has "Type RESTORE jobapp to go on"
out_has "confirmation not given, nothing changed"
grep -q -- "bash -s -- verify latest" "$WORK/calls2" || fail "no verify call: $(cat "$WORK/calls2")"
grep -q -- "restore-live" "$WORK/calls2" && fail "restore-live ran after a wrong answer"
ok "a wrong answer (lowercase) refuses after the verification, restore-live never runs"
: >"$WORK/calls2"
typed "" ./deploy.sh restore latest --into-live
out_has "confirmation not given, nothing changed"
grep -q -- "restore-live" "$WORK/calls2" && fail "restore-live ran after an empty answer"
ok "an empty answer refuses too"
: >"$WORK/calls2"
typed "RESTORE jobapp" ./deploy.sh restore latest --into-live
grep -q -- "bash -s -- restore-live /home/u/jobapp-backups/jobapp-2026-10-08T120000.dump" "$WORK/calls2" ||
  fail "restore-live not called with the verified path: $(cat "$WORK/calls2")"
out_has '/api/health: {"status":"ok"}'
ok "the exact phrase runs restore-live on the path verify resolved (latest pinned), then the health check"
: >"$WORK/calls2"
run 0 fake2 ./deploy.sh restore latest --into-live --yes </dev/null
grep -q "Type RESTORE" "$WORK/out" && fail "--yes still prompted"
grep -q -- "JOBAPP_DIR=jobapp BACKUP_DIR=jobapp-backups bash -s -- restore-live /home/u/" "$WORK/calls2" ||
  fail "settings not passed: $(cat "$WORK/calls2")"
grep -q "BACKUP_KEEP" "$WORK/calls2" && fail "a BACKUP_KEEP was sent although none is set here: $(cat "$WORK/calls2")"
ok "--yes skips the prompt without a terminal; BACKUP_DIR reaches ops/db.sh, and no BACKUP_KEEP when none is set on the Mac (T10: the Jetson's .env, then the default of ops/db.sh, decide)"
: >"$WORK/calls2"
# The fake prints no backup=<path> line, so the run must fail rather than claim a backup.
run 1 fake2 env BACKUP_KEEP=3 ./deploy.sh backup --pull
grep -q -- "BACKUP_DIR=jobapp-backups BACKUP_KEEP=3 bash -s -- backup" "$WORK/calls2" || fail "BACKUP_KEEP not passed: $(cat "$WORK/calls2")"
out_has "ops/db.sh reported no backup file"
ok "backup passes a BACKUP_KEEP set on the Mac (an explicit value wins), and fails when ops/db.sh reports no file"
grep -q "BACKUP_ACCEPT_SHRINK" "$WORK/calls2" && fail "BACKUP_ACCEPT_SHRINK was sent although it is not set: $(cat "$WORK/calls2")"
: >"$WORK/calls2"
run 1 fake2 env BACKUP_ACCEPT_SHRINK=1 ./deploy.sh backup
grep -q -- "BACKUP_DIR=jobapp-backups BACKUP_ACCEPT_SHRINK=1 bash -s -- backup" "$WORK/calls2" || fail "BACKUP_ACCEPT_SHRINK not passed: $(cat "$WORK/calls2")"
ok "T10: BACKUP_ACCEPT_SHRINK=1 in the shell of one ./deploy.sh backup reaches ops/db.sh, and nothing sends it otherwise"
# shellcheck disable=SC2088 # literal ~/, as BACKUP_DIR is written in .env: deploy.sh strips it
for spelled in '~/jobapp-backups/' '~/jobapp-backups/.' 'jobapp-backups//' '~/jobapp-backups/./'; do
  : >"$WORK/calls2"
  run 1 fake2 env BACKUP_DIR="$spelled" ./deploy.sh backup
  grep -q -- "JOBAPP_DIR=jobapp BACKUP_DIR=jobapp-backups bash -s -- backup" "$WORK/calls2" ||
    fail "BACKUP_DIR=$spelled was not sent as jobapp-backups: $(cat "$WORK/calls2")"
done
# shellcheck disable=SC2088 # literal ~/, as above
for spelled in '~/' '~/.' / // /.; do
  run 1 nohost env BACKUP_DIR="$spelled" ./deploy.sh backup
  out_has "BACKUP_DIR must name a directory"
done
ok "T10: a slash or a /. after BACKUP_DIR is trimmed before the name is sent (ops/db.sh would not see a symbolic link behind it); the home folder and / are refused however they are spelled"

step "ops/db.sh restore-live stops the api and the worker, and starts both on every path"
# ops/db.sh itself, run here with a fake docker (and flock, stat) found first: what it asks of
# Compose, in order, and what was stopped while the live database was dropped and restored.
DBT="$WORK/dbsh"
mkdir -p "$DBT/bin" "$DBT/app" "$DBT/backups"
printf 'name: fake\n' >"$DBT/app/compose.yml"
printf 'POSTGRES_USER=jobapp\nPOSTGRES_DB=jobapp\n' >"$DBT/app/.env"
printf 'PGDMP fake dump\n' >"$DBT/backups/jobapp-2026-10-08T120000.dump"
cat >"$DBT/bin/flock" <<'SH'
#!/usr/bin/env bash
# Free, unless the test says the lock on this descriptor is held (FAKE_BUSY_FD, T10).
[[ "${FAKE_BUSY_FD:-}" != "${!#}" ]] || exit 1
exit 0
SH
cat >"$DBT/bin/stat" <<'SH'
#!/usr/bin/env bash
wc -c <"${!#}" | tr -d ' '
SH
cat >"$DBT/bin/sync" <<'SH'
#!/usr/bin/env bash
# Records each flush in $FAKE_DIR/order, a folder with what it holds at that moment (T10, the
# review's B1). FAKE_OLD_SYNC: a sync that knows no path. FAKE_FAIL_SYNC=file|dir: that one fails.
if [[ "${1:-}" == --help ]]; then
  [[ -n "${FAKE_OLD_SYNC:-}" ]] || echo "Usage: sync [OPTION] [FILE]..."
  exit 0
fi
if [[ $# -eq 0 ]]; then
  echo "sync" >>"$FAKE_DIR/order"
  [[ "${FAKE_FAIL_SYNC:-}" != plain ]] || exit 1
elif [[ -d "$1" ]]; then
  echo "sync dir $1 holding: $(ls -A "$1" | tr '\n' ' ')" >>"$FAKE_DIR/order"
  [[ "${FAKE_FAIL_SYNC:-}" != dir ]] || exit 1
else
  echo "sync file $1" >>"$FAKE_DIR/order"
  [[ "${FAKE_FAIL_SYNC:-}" != file ]] || exit 1
fi
SH
cat >"$DBT/bin/docker" <<'SH'
#!/usr/bin/env bash
# Plays the Jetson's docker for ops/db.sh; logs every call and the stopped services. The dump
# and its two verifications also go to $FAKE_DIR/order, and each can be made to fail (T10).
log="$FAKE_DIR/calls"
args="$*"
case "$args" in
  *"ps -q db"*) echo dbid ;;
  inspect*) echo "${FAKE_DB_STATE:-running healthy}" ;;
  *" stop "*)
    echo "stop ${args##* stop }" >>"$log"
    echo "${args##* stop }" >"$FAKE_DIR/stopped"
    ;;
  *" start "*)
    echo "start ${args##* start }" >>"$log"
    : >"$FAKE_DIR/stopped"
    ;;
  *" up "*)
    echo "up ${args##* up }" >>"$log"
    : >"$FAKE_DIR/stopped"
    ;;
  *"alembic upgrade head"*)
    echo "alembic" >>"$log"
    [[ -z "${FAKE_FAIL_ALEMBIC:-}" ]] || exit 1
    ;;
  *"pg_restore --list"*)
    cat >/dev/null
    echo "verify list" >>"$FAKE_DIR/order"
    if [[ -n "${FAKE_FAIL_LIST:-}" ]]; then
      echo "pg_restore: error: input file does not appear to be a valid archive" >&2
      exit 1
    fi
    printf ';\n; dbname: jobapp\n1; table a\n2; data a\n'
    ;;
  *"pg_restore -f /dev/null"*)
    cat >/dev/null
    echo "verify read" >>"$FAKE_DIR/order"
    if [[ -n "${FAKE_FAIL_READ:-}" ]]; then
      echo "pg_restore: error: could not read from input file: end of file" >&2
      exit 1
    fi
    ;;
  *"pg_dump"*)
    echo "pg_dump" >>"$FAKE_DIR/order"
    # 16 bytes, like the dumps the tests seed; FAKE_DUMP_BYTES makes it bigger (the size check).
    [[ -n "${FAKE_EMPTY_DUMP:-}" ]] || printf 'PGDMP fake dump\n'
    [[ -z "${FAKE_DUMP_BYTES:-}" ]] || head -c "$((FAKE_DUMP_BYTES - 16))" /dev/zero | tr '\0' x
    if [[ -n "${FAKE_FAIL_DUMP:-}" ]]; then
      echo "pg_dump: error: connection to server failed" >&2
      exit 1
    fi
    ;;
  *"pg_database_size"*) echo 1000 ;;
  *"from pg_database where datname = 'jobapp_restore_check'"*)
    # Is the scratch database of restore --keep there (T10)? FAKE_SCRATCH=1: yes; down: no answer.
    [[ "${FAKE_SCRATCH:-0}" != down ]] || exit 1
    echo "${FAKE_SCRATCH:-0}"
    ;;
  *"drop database"*) echo "drop while stopped: $(cat "$FAKE_DIR/stopped" 2>/dev/null)" >>"$log" ;;
  *"pg_restore -U"*)
    cat >/dev/null
    echo "restore while stopped: $(cat "$FAKE_DIR/stopped" 2>/dev/null)" >>"$log"
    if [[ -n "${FAKE_FAIL_RESTORE:-}" && ! -e "$FAKE_DIR/failed" ]]; then
      touch "$FAKE_DIR/failed"
      exit 1
    fi
    ;;
  *) cat >/dev/null 2>&1 || true ;;
esac
SH
chmod +x "$DBT/bin/"*
dbsh() { # dbsh <expected exit> [env assignments]: restore-live of the fake dump
  local want="$1"
  shift
  rm -f "$DBT/calls" "$DBT/stopped" "$DBT/failed"
  run "$want" env "$@" FAKE_DIR="$DBT" DB_SH_TEST_BIN="$DBT/bin" JOBAPP_DIR="$DBT/app" \
    BACKUP_DIR="$DBT/backups" BACKUP_MIN_FREE_MB=0 bash ops/db.sh restore-live jobapp-2026-10-08T120000.dump
}
calls_are() { # calls_are <expected, one per line>
  diff <(printf '%s\n' "$@") "$DBT/calls" >/dev/null || fail "calls: $(cat "$DBT/calls")"
}
dbsh 0
calls_are "stop api worker" "drop while stopped: api worker" "restore while stopped: api worker" \
  "alembic" "up -d --no-deps --wait --wait-timeout 120 api worker"
ok "restore-live: api and worker stopped through drop and restore, started healthy after the migrations"
dbsh 1 FAKE_FAIL_RESTORE=1
out_has "the live database was rolled back"
calls_are "stop api worker" "drop while stopped: api worker" "restore while stopped: api worker" \
  "drop while stopped: api worker" "restore while stopped: api worker" \
  "up -d --no-deps --wait --wait-timeout 120 api worker"
ok "a failed restore rolls back with both still stopped, then starts api and worker"
dbsh 1 FAKE_FAIL_ALEMBIC=1
out_has "starting api and worker again after a failure"
calls_are "stop api worker" "drop while stopped: api worker" "restore while stopped: api worker" \
  "alembic" "start api worker"
ok "any other failure after the stop starts api and worker again on exit"

step "T10, retention: ops/db.sh backup with a clock, an rm and locks the test controls"
# The fakes of the step above, plus a date that prints $FAKE_NOW (a run happens "at" a chosen UTC
# time, so nothing here depends on the real clock) and an rm that records what it is asked to
# delete before deleting it. Each scenario starts from an empty fake Jetson in $ROT: app/
# (compose.yml, .env) and backups/. A dump is named by its age in days before the run of
# 2026-10-11 01:17 UTC (03:17 in Paris, the nightly hour), and every one is written now: its
# mtime says today whatever its name says.
cat >"$DBT/bin/date" <<'SH'
#!/usr/bin/env bash
if [[ -n "${FAKE_NOW:-}" ]]; then
  echo "$FAKE_NOW"
else
  exec /bin/date "$@"
fi
SH
cat >"$DBT/bin/rm" <<'SH'
#!/usr/bin/env bash
echo "rm $*" >>"$FAKE_DIR/order"
exec /bin/rm "$@"
SH
chmod +x "$DBT/bin/"*
ROT="$WORK/rot"
NOW=2026-10-11T011700
ago() { # ago <days> [HHMMSS]: the time in the name of a dump made that many days before $NOW
  local day=$((11 - $1)) month=10
  if ((day < 1)); then
    day=$((day + 30))
    month=9
  fi
  printf '2026-%02d-%02dT%s' "$month" "$day" "${2:-011700}"
}
d() { # d <days> [tag] [HHMMSS]: the name of that dump
  printf 'jobapp-%s%s.dump' "$(ago "$1" "${3:-011700}")" "${2:+-$2}"
}
[[ "$(ago 0)" == "$NOW" && "$(ago 10)" == 2026-10-01T011700 && "$(ago 11 235959)" == 2026-09-30T235959 &&
  "$(ago 40)" == 2026-09-01T011700 && "$(d 8 predeploy-abc 140000)" == jobapp-2026-10-03T140000-predeploy-abc.dump ]] ||
  fail "the test's own dates are wrong: $(ago 0) $(ago 10) $(ago 11 235959) $(ago 40) $(d 8 predeploy-abc 140000)"
rot_new() { # an empty fake Jetson
  rm -rf "$ROT"
  mkdir -p "$ROT/app" "$ROT/backups" "$ROT/tmp" "$ROT/outside"
  printf 'name: fake\n' >"$ROT/app/compose.yml"
  printf 'POSTGRES_USER=jobapp\nPOSTGRES_DB=jobapp\n' >"$ROT/app/.env"
  : >"$ROT/backups/.lock"
}
seed() { # seed <path under backups/>...: fake dumps, all written now
  local f
  for f in "$@"; do
    mkdir -p "$(dirname "$ROT/backups/$f")"
    printf 'PGDMP fake dump\n' >"$ROT/backups/$f"
  done
}
# rot <expected exit> <UTC time of the run> [NAME=value ...] [-- <ops/db.sh arguments, default: backup>]
rot() {
  local want="$1" now="$2" envs=()
  shift 2
  while (($#)) && [[ "$1" != -- ]]; do
    envs+=("$1")
    shift
  done
  if (($#)); then shift; fi
  (($#)) || set -- backup
  : >"$ROT/order"
  run "$want" env -u BACKUP_KEEP -u BACKUP_ACCEPT_SHRINK FAKE_NOW="$now" FAKE_DIR="$ROT" DB_SH_TEST_BIN="$DBT/bin" TMPDIR="$ROT/tmp" \
    JOBAPP_DIR="$ROT/app" BACKUP_DIR="$ROT/backups" BACKUP_MIN_FREE_MB=0 ${envs[@]+"${envs[@]}"} bash ops/db.sh "$@"
}
holds() { # holds <folder under backups/, or .>: every entry there but the lock and the folders
  (cd "$ROT/backups/$1" && find . -mindepth 1 -maxdepth 1 ! -name .lock ! -type d | sed 's|^\./||' | LC_ALL=C sort)
}
only() { # only <folder under backups/, or .> [name ...]: exactly these are left there
  local sub="$1" want
  shift
  want="$(printf '%s\n' "$@" | LC_ALL=C sort)"
  [[ "$(holds "$sub")" == "$want" ]] ||
    fail "backups/$sub holds [$(holds "$sub" | tr '\n' ' ')], expected [$(tr '\n' ' ' <<<"$want")]: $(cat "$WORK/out")"
}
tree_of() { (cd "$ROT" && find backups outside -mindepth 1 | LC_ALL=C sort); } # every path, for "nothing changed"
no_delete() { # no_delete <tree before>: the last run removed and added nothing, and said so
  [[ "$(tree_of)" == "$1" ]] || fail "the folders changed: $(diff <(printf '%s\n' "$1") <(tree_of) | tr '\n' ' ')"
  grep -q "deleted old backup" "$WORK/out" && fail "the run says it deleted a backup: $(cat "$WORK/out")"
  grep -q "^rm .*backups/.*jobapp-[^/]*\.dump$" "$ROT/order" && fail "a dump reached rm: $(cat "$ROT/order")"
  return 0
}
ok "fakes ready: a run is at a time the test chooses, and every rm is recorded"

step "T10, retention: 7 by default, by count and by the date in the name; an explicit value wins for both"
rot_new
seed "$(d 2 '' 080000)" "$(d 2 '' 090000)" "$(d 2 '' 100000)" "$(d 1 '' 080000)" "$(d 1 '' 090000)" "$(d 1 '' 100000)" \
  "$(d 0 '' 000500)" "$(d 0 '' 001000)"
rot 0 "$NOW"
only . "$(d 2 '' 100000)" "$(d 1 '' 080000)" "$(d 1 '' 090000)" "$(d 1 '' 100000)" "$(d 0 '' 000500)" "$(d 0 '' 001000)" "$(d 0)"
out_has "deleted old backup $(d 2 '' 080000) (beyond the newest 7)"
out_has "retention: 7 backup(s) kept in $ROT/backups (BACKUP_KEEP=7, the default of ops/db.sh)"
out_has "^backup=$ROT/backups/$(d 0)$"
grep -q "WARNING" "$WORK/out" && fail "the default retention warns: $(cat "$WORK/out")"
ok "no BACKUP_KEEP anywhere: 7. Nine dumps of three days leave the newest 7, the new one among them"

rot_new
for n in 1 2 3 4 5 6 7; do seed "$(d "$n" '' "01170$n")"; done
rot 0 "$NOW"
only . "$(d 1 '' 011701)" "$(d 2 '' 011702)" "$(d 3 '' 011703)" "$(d 4 '' 011704)" "$(d 5 '' 011705)" "$(d 6 '' 011706)" "$(d 0)"
ok "a week of nightly dumps, each a few seconds later than the last: the one of 7 nights ago goes, 7 stay"

rot_new
seed "$(d 7 '' 000000)" "$(d 7 '' 235959)" "$(d 6 '' 000000)" "$(d 6 '' 235959)"
rot 0 "$NOW"
only . "$(d 6 '' 000000)" "$(d 6 '' 235959)" "$(d 0)"
out_has "deleted old backup $(d 7 '' 235959) (7 days old, the limit is 7)"
ok "age is counted in UTC calendar days between two names: the dump of 7 days ago at 23:59:59 goes (6 days and 1 hour on a clock), the one of 6 days ago at 00:00:00 stays; the count rule had nothing to delete"

rot_new
seed "$(d 9)" "$(d 1)"
touch -t 202609010000 "$ROT/backups/$(d 1)" # an old mtime on a recent name
touch "$ROT/backups/$(d 9)"                 # today's mtime on an old name: copied yesterday, or touched
rot 0 "$NOW"
only . "$(d 1)" "$(d 0)"
ok "the name wins over the mtime: a dump whose name says 9 days ago goes although it was written today, one named yesterday stays although its mtime says September"

# Month ends, a leap year, a year that is not one (2100), and the new year: 7 days before goes, 6 stays.
for c in "2028-03-01 2028-02-23 2028-02-24" "2027-03-01 2027-02-22 2027-02-23" "2100-03-01 2100-02-22 2100-02-23" \
  "2027-01-03 2026-12-27 2026-12-28" "2026-08-05 2026-07-29 2026-07-30"; do
  read -r day gone stays <<<"$c"
  rot_new
  seed "jobapp-${gone}T120000.dump" "jobapp-${stays}T120000.dump"
  rot 0 "${day}T011700"
  only . "jobapp-${stays}T120000.dump" "jobapp-${day}T011700.dump"
done
ok "the day arithmetic across the end of February (2028 a leap year, 2027 and 2100 not), the new year and a 31 day month"

rot_new
for n in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16; do seed "$(d "$n")"; done
rot 0 "$NOW" BACKUP_KEEP=14
only . "$(d 0)" "$(d 1)" "$(d 2)" "$(d 3)" "$(d 4)" "$(d 5)" "$(d 6)" "$(d 7)" "$(d 8)" "$(d 9)" "$(d 10)" "$(d 11)" "$(d 12)" "$(d 13)"
out_has "BACKUP_KEEP=14, set in the environment of this run"
out_has "WARNING: BACKUP_KEEP=14 keeps dumps up to 14 days, more than the 7 days within which a deleted document is promised to leave the server's backups"
rot_new
seed "$(d 20)" "$(d 14)" "$(d 13)" "$(d 8)"
rot 0 "$NOW" BACKUP_KEEP=14
only . "$(d 13)" "$(d 8)" "$(d 0)"
rot_new
seed "$(d 20)" "$(d 14)" "$(d 13)" "$(d 8)" "$(d 6)"
rot 0 "$NOW"
only . "$(d 6)" "$(d 0)"
ok "an explicit BACKUP_KEEP=14 wins for both rules: 14 dumps and 14 days (13 stays, 14 goes), with the warning that users are promised 7; the same folder at the default keeps nothing past 6 days"

rot_new
printf 'BACKUP_KEEP=3\n' >>"$ROT/app/.env"
seed "$(d 4)" "$(d 3)" "$(d 2)" "$(d 1)"
rot 0 "$NOW"
only . "$(d 2)" "$(d 1)" "$(d 0)"
out_has "BACKUP_KEEP=3, set in $ROT/app/.env"
rot_new
printf 'BACKUP_KEEP=3\n' >>"$ROT/app/.env"
seed "$(d 4)" "$(d 3)" "$(d 2)" "$(d 1)"
rot 0 "$NOW" BACKUP_KEEP=5
only . "$(d 4)" "$(d 3)" "$(d 2)" "$(d 1)" "$(d 0)"
out_has "BACKUP_KEEP=5, set in the environment of this run"
for line in 'BACKUP_KEEP="3"' "BACKUP_KEEP='3'" $'BACKUP_KEEP=3\r' 'BACKUP_KEEP= 3 ' '  BACKUP_KEEP=3' \
  'export BACKUP_KEEP=3' $'export\tBACKUP_KEEP=3'; do
  rot_new
  printf '%s\n' "$line" >>"$ROT/app/.env"
  seed "$(d 4)" "$(d 3)" "$(d 2)" "$(d 1)"
  rot 0 "$NOW"
  only . "$(d 2)" "$(d 1)" "$(d 0)"
  out_has "BACKUP_KEEP=3, set in $ROT/app/.env"
done
rot_new
printf 'BACKUP_KEEP=\n' >>"$ROT/app/.env"
seed "$(d 8)" "$(d 6)"
rot 0 "$NOW"
only . "$(d 6)" "$(d 0)"
out_has "BACKUP_KEEP=7, the default of ops/db.sh"
ok "the BACKUP_KEEP of the deployed .env is read at each run (quoted, spaced, with a carriage return, or written with export as compose accepts it: never ignored without a word), the environment of the run wins over it, an empty line means the default"

for keep in 0 -1 abc 1.5 07 10000 "7 days"; do
  rot_new
  seed "$(d 30)" "$(d 20)" "deploys/$(d 30 predeploy-aaaaaaaaaaaa)" "incoming/$(d 30)"
  before="$(tree_of)"
  rot 1 "$NOW" "BACKUP_KEEP=$keep"
  out_has "BACKUP_KEEP must be a whole number from 1 to 9999, got '$keep' (set in the environment of this run)"
  no_delete "$before"
  [[ ! -s "$ROT/order" ]] || fail "BACKUP_KEEP=$keep still reached pg_dump: $(cat "$ROT/order")"
  rot_new
  seed "$(d 30)" "$(d 20)"
  before="$(tree_of)"
  printf 'BACKUP_KEEP=%s\n' "$keep" >>"$ROT/app/.env"
  rot 1 "$NOW"
  out_has "BACKUP_KEEP must be a whole number from 1 to 9999, got '$keep' (set in $ROT/app/.env)"
  no_delete "$before"
done
ok "BACKUP_KEEP 0, -1, abc, 1.5, 07, 10000 and '7 days' are refused, from the environment and from .env: no dump, nothing deleted, exit 1"
# The review's N5: the same typo must not stand between the owner and a restore. The commands
# that delete nothing say it and go on; the ones that use the number are refused.
rot_new
seed "$(d 30)" "$(d 20)" "deploys/$(d 30 predeploy-aaaaaaaaaaaa)"
printf 'BACKUP_KEEP=abc\n' >>"$ROT/app/.env"
KEEP_WARNING="db.sh: WARNING: BACKUP_KEEP must be a whole number from 1 to 9999, got 'abc' (set in $ROT/app/.env)"
before="$(tree_of)"
for c in "verify $(d 20)" "list" "restore $ROT/backups/$(d 20)"; do
  # shellcheck disable=SC2086 # the command and its argument
  rot 0 "$NOW" -- $c
  out_has "$KEEP_WARNING. ${c%% *} deletes nothing and goes on"
  no_delete "$before"
done
out_has "restored into the scratch database"
rot 0 "$NOW" -- list
out_has "retention: none while this stands: BACKUP_KEEP must be a whole number"
rot 0 "$NOW" -- restore-live "$(d 20)"
out_has "$KEEP_WARNING. restore-live deletes nothing and goes on"
out_has "restored $ROT/backups/$(d 20) into jobapp"
only . "$(d 30)" "$(d 20)" "$(d 0 pre-restore)"
before="$(tree_of)"
for c in backup prune retention; do
  rot 1 "$NOW" -- "$c"
  out_has "db.sh: BACKUP_KEEP must be a whole number from 1 to 9999, got 'abc' (set in $ROT/app/.env)"
  grep -q "WARNING" "$WORK/out" && fail "$c only warned about an invalid BACKUP_KEEP: $(cat "$WORK/out")"
  no_delete "$before"
done
ok "an invalid BACKUP_KEEP in .env: verify, list, restore and restore-live say it and do their work (the pre-restore dump is written, nothing is deleted); backup, prune and retention are refused before anything"

step "T10, retention by age in every folder the server keeps dumps in"
rot_new
seed "$(d 9 pre-restore)" "$(d 3 pre-restore)" "$(d 1)" \
  "deploys/$(d 8 predeploy-aaaaaaaaaaaa 140000)" "deploys/$(d 7 predeploy-bbbbbbbbbbbb 235959)" \
  "deploys/$(d 6 predeploy-cccccccccccc 000001)" "deploys/$(d 0 predeploy-dddddddddddd 001500)" \
  "incoming/$(d 9)" "incoming/$(d 2)"
rot 0 "$NOW"
only . "$(d 3 pre-restore)" "$(d 1)" "$(d 0)"
only deploys "$(d 6 predeploy-cccccccccccc 000001)" "$(d 0 predeploy-dddddddddddd 001500)"
only incoming "$(d 2)"
out_has "deleted old backup $(d 8 predeploy-aaaaaaaaaaaa 140000) (8 days old, the limit is 7)"
out_has "deleted old backup $(d 9 pre-restore) (9 days old, the limit is 7)"
out_has "retention: 2 backup(s) kept in $ROT/backups/deploys (BACKUP_KEEP=7, the default of ops/db.sh)"
out_has "retention: 1 backup(s) kept in $ROT/backups/incoming"
# Every file that reached rm in the backup folders is a dump, by its whole name, directly in one
# of the three, with -f and -- before the one path; the others are the run's own mktemp files,
# outside those folders.
tmp_re='^rm -f /[^[:space:]]*/tmp\.[A-Za-z0-9]+$'
while IFS= read -r line; do
  case "$line" in
    "rm -f -- $ROT/backups/"*)
      rest="${line#"rm -f -- $ROT/backups/"}"
      [[ "$rest" =~ ^((deploys|incoming)/)?jobapp-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{6}(-[a-z0-9-]+)?\.dump$ ]] ||
        fail "rm was given something that is not a dump: $line"
      ;;
    *"$ROT/backups"* | *"$ROT/outside"*) fail "an rm in the backup folders that is not the rotation's: $line" ;;
    *) [[ "$line" =~ $tmp_re ]] || fail "an rm the run should not make: $line" ;;
  esac
done < <(grep '^rm ' "$ROT/order")
[[ "$(grep -c "^rm -f -- $ROT/backups/" "$ROT/order")" == 4 ]] || fail "not exactly the 4 old dumps were removed: $(cat "$ROT/order")"
ok "the nightly run: a predeploy dump 8 days old goes and one 6 days old stays, a pre-restore dump 9 days old goes, an uploaded dump 9 days old goes; rm only ever got whole dump names in the three folders"

rot_new
for h in 01 02 03 04 05 06 07 08 09; do seed "deploys/$(d 1 "predeploy-aaaaaaaaaa$h" "${h}0000")"; done
rot 0 "$NOW"
[[ "$(holds deploys | wc -l | tr -d ' ')" == 9 ]] || fail "the nightly run applied the count rule to deploys/: $(holds deploys | tr '\n' ' ')"
ok "the count rule is for the folder of the run: nine predeploy dumps of yesterday all stay through the nightly run"

rot_new
seed "$(d 20)" "incoming/$(d 20)" "deploys/$(d 8 predeploy-000000000000)"
for h in 01 02 03 04 05 06 07 08; do seed "deploys/$(d 1 "predeploy-aaaaaaaaaa$h" "${h}0000")"; done
rot 0 "$NOW" BACKUP_DIR="$ROT/backups/deploys" -- backup --tag predeploy-0123456789ab
only deploys "$(d 1 predeploy-aaaaaaaaaa03 030000)" "$(d 1 predeploy-aaaaaaaaaa04 040000)" "$(d 1 predeploy-aaaaaaaaaa05 050000)" \
  "$(d 1 predeploy-aaaaaaaaaa06 060000)" "$(d 1 predeploy-aaaaaaaaaa07 070000)" "$(d 1 predeploy-aaaaaaaaaa08 080000)" \
  "$(d 0 predeploy-0123456789ab)"
only . "$(d 20)"
only incoming "$(d 20)"
out_has "^backup=$ROT/backups/deploys/$(d 0 predeploy-0123456789ab)$"
ok "the deploy's own backup (BACKUP_DIR is the predeploy folder): both rules there, newest 7 and nothing 7 days old, and no other folder touched"

step "T10, safety: the newest verified dump of the whole tree stays, and no folder has an exception"
# The review of 2026-10-11: keeping the newest dump of each folder whatever its age made the 7
# days false, since a document deleted after the last deploy stayed in the last predeploy dump
# until the next deploy. What stays is one dump, the one this run wrote and verified.
rot_new
seed "$(d 30)" "$(d 20 pre-restore)" "$(d 1)" "deploys/$(d 30 predeploy-aaaaaaaaaaaa)" "deploys/$(d 20 predeploy-bbbbbbbbbbbb)" \
  "incoming/$(d 30)" "incoming/$(d 20)"
rot 0 "$NOW"
only . "$(d 1)" "$(d 0)"
only deploys
only incoming
out_has "deleted old backup $(d 20 predeploy-bbbbbbbbbbbb) (20 days old, the limit is 7)"
out_has "retention: 0 backup(s) kept in $ROT/backups/deploys"
grep -q "although" "$WORK/out" && fail "a dump past the limit was spared although yesterday's is there: $(cat "$WORK/out")"
ok "deploys/ and incoming/ hold only dumps past the limit: all go, the last predeploy dump and the last upload included; no folder keeps its newest for its own sake"

rot_new
for n in 1 2 3 4 5 6 7; do seed "$(d "$n")"; done
seed "deploys/$(d 7 predeploy-aaaaaaaaaaaa 140000)"
rot 0 "$NOW"
only . "$(d 0)" "$(d 1)" "$(d 2)" "$(d 3)" "$(d 4)" "$(d 5)" "$(d 6)"
only deploys
ok "a week without a deploy: the only predeploy dump, taken 7 days ago in the afternoon, goes with the nightly dump of that day, so nothing a user deleted since is left in it"

# Decision of 2026-10-11 (the review's N4, a): a run never leaves the server with its own dump
# alone. When every other dump of the tree is past the limit, the newest of them stays for one
# more run, and the next run, which has this run's dump beside its own, deletes it.
rot_new
seed "$(d 30)" "$(d 20 pre-restore)" "deploys/$(d 30 predeploy-aaaaaaaaaaaa)" "deploys/$(d 20 predeploy-bbbbbbbbbbbb)" \
  "incoming/$(d 30)" "incoming/$(d 19)"
rot 0 "$NOW"
only . "$(d 0)"
only deploys
only incoming "$(d 19)"
out_has "kept $(d 19) in $ROT/backups/incoming although it is 19 days old, the limit is 7: every other dump goes in this run, and a run never leaves the server with its own dump alone. The next run that leaves another dump beside its own deletes it"
[[ "$(grep -c "^rm -f -- $ROT/backups/" "$ROT/order")" == 5 ]] || fail "not the five others were deleted: $(cat "$ROT/order")"
rot 0 "$(ago -1)"
only . "$(d 0)" "$(d -1)"
only incoming
grep -q "although" "$WORK/out" && fail "the spared dump was spared a second time: $(cat "$WORK/out")"
ok "every other dump of the three folders is past the limit: five go and the newest of them, wherever it is, stays for this run, with the reason; the next run, a day later, deletes it. One run more, in that case only"
rot_new
seed "$(d 20)" "deploys/$(d 20 predeploy-aaaaaaaaaaaa)" "incoming/$(d 20)"
rot 0 "$NOW"
only . "$(d 20)" "$(d 0)"
only deploys
only incoming
rot_new
seed "$(d 20 pre-restore)" "incoming/$(d 20)"
rot 0 "$NOW"
only . "$(d 20 pre-restore)" "$(d 0)"
only incoming
ok "at the same time in several names, the one in the folder of the run is the one spared: the time alone is compared, so a tag in the name does not decide"

# The review's N14: the dump that is spared, and the dump that counts as left, must be something
# to restore. An empty file with a dump's name is neither.
rot_new
seed "$(d 30)" "$(d 20)"
: >"$ROT/backups/$(d 9)"
rot 0 "$NOW"
only . "$(d 20)" "$(d 0)"
out_has "kept $(d 20) in $ROT/backups although it is 20 days old"
rot_new
seed "$(d 30)" "$(d 20)"
: >"$ROT/backups/$(d 1)"
rot 0 "$NOW"
only . "$(d 20)" "$(d 1)" "$(d 0)"
out_has "kept $(d 20) in $ROT/backups although it is 20 days old"
rot_new
: >"$ROT/backups/$(d 30)"
: >"$ROT/backups/$(d 20)"
rot 0 "$NOW"
only . "$(d 0)"
grep -q "although" "$WORK/out" && fail "an empty file was spared: $(cat "$WORK/out")"
ok "an empty file named like a dump is never the one spared (the newest real dump is, though an empty one is newer), an empty file of yesterday does not count as a dump left, and when every other dump is empty none is kept"

rot_new
seed "$(d 30)" "deploys/$(d 30 predeploy-aaaaaaaaaaaa)" "deploys/$(d 9 predeploy-bbbbbbbbbbbb)"
rot 0 "$NOW" BACKUP_DIR="$ROT/backups/deploys" -- backup --tag predeploy-0123456789ab
only deploys "$(d 9 predeploy-bbbbbbbbbbbb)" "$(d 0 predeploy-0123456789ab)"
only . "$(d 30)"
rot 0 "$(ago 0 020000)" BACKUP_DIR="$ROT/backups/deploys" -- backup --tag predeploy-123456789abc
only deploys "$(d 0 predeploy-0123456789ab)" "$(d 0 predeploy-123456789abc 020000)"
ok "a deploy after a long pause: its backup deletes the older predeploy dumps but the newest of them, which the next deploy (or the next nightly run) deletes; the folder of the nightly run is not its to rotate"
rot_new
seed "$(d 2)" "$(d 1)"
rot 0 "$NOW" BACKUP_KEEP=1
only . "$(d 1)" "$(d 0)"
out_has "kept $(d 1) in $ROT/backups although it is 1 days old, the limit is 1"
ok "BACKUP_KEEP=1 therefore keeps two: the run's dump and the one before it"

rot_new
for n in 1 2 3 4 5 6 7 8; do seed "$(d "$n")"; done
rot 0 "$(ago 20)" # the clock says 20 days before the newest names: this run's dump is the oldest by name
only . "jobapp-$(ago 20).dump" "$(d 1)" "$(d 2)" "$(d 3)" "$(d 4)" "$(d 5)" "$(d 6)" "$(d 7)"
out_has "kept $(d 1), whose name is 19 day(s) ahead of this run's (a clock was wrong): by its name it goes in 26 days, sooner by hand"
ok "a clock set back: the dump this run wrote is the oldest name of nine and still stays (the count rule takes the next one); the dumps named ahead of it are kept and said"

rot_new
for n in 1 2 3; do seed "$(d "$n")"; done
seed "deploys/$(d 1 predeploy-aaaaaaaaaaaa)" "incoming/$(d 1)"
rot 0 2027-10-11T011700 # the clock a year ahead: everything else looks a year old
only . "$(d 1)" jobapp-2027-10-11T011700.dump
only deploys
only incoming
out_has "kept $(d 1) in $ROT/backups although it is 366 days old, the limit is 7: every other dump goes in this run"
rot 0 "$NOW" # the clock right again
only . "$(d 1)" jobapp-2027-10-11T011700.dump "$(d 0)"
out_has "kept jobapp-2027-10-11T011700.dump, whose name is 365 day(s) ahead of this run's (a clock was wrong): by its name it goes in 372 days, sooner by hand"
ok "a clock a year ahead: every other dump looks a year old and goes, but the newest real one and the run's own stay, so the clock alone never leaves one dump; once the clock is right the dump of that run is ahead by its name, kept and said at every run"

rot_new
seed "$(d 30)" "$(d 20)" "$(d 9)" "deploys/$(d 30 predeploy-aaaaaaaaaaaa)" "deploys/$(d 9 predeploy-bbbbbbbbbbbb)" "incoming/$(d 30)"
for n in 1 2 3 4 5 6 7 8; do seed "$(d 1 '' "0${n}0000")"; done
before="$(tree_of)"
for c in "FAKE_FAIL_DUMP=1|pg_dump failed, nothing kept" "FAKE_EMPTY_DUMP=1|empty file, not a backup" \
  "FAKE_FAIL_LIST=1|not a valid pg_dump custom-format archive" "FAKE_FAIL_READ=1|corrupt or truncated backup, refused" \
  "FAKE_DB_STATE=running unhealthy|the db service is 'running unhealthy'" \
  "BACKUP_MIN_FREE_MB=100000000000|not enough disk for a backup" \
  "FAKE_BUSY_FD=9|another backup or restore is running on this host"; do
  rot 1 "$NOW" "${c%%|*}"
  out_has "${c#*|}"
  no_delete "$before"
  grep -q "^backup=" "$WORK/out" && fail "${c%%|*}: a backup was reported: $(cat "$WORK/out")"
done
ok "a dump that cannot be written (pg_dump fails, writes nothing, no disk, db unhealthy, lock held) or verified (the list, the full read): exit 1, no partial file, and not one old dump deleted, by count or by age"

# The review's B1: a verified dump is not yet a dump on the disk. Its data is flushed while it
# still has its partial name, the folder once it has its real one, and only then is an old dump
# unlinked: a power cut never finds the old dumps gone and the new one empty.
rot_new
seed "$(d 9)" "$(d 8)" "$(d 1)"
rot 0 "$NOW"
only . "$(d 1)" "$(d 0)"
# first_at and last_at: the line of $ROT/order a pattern is first or last found at, nothing when it
# is not there. Never a failure of their own (the review's N20): a missing flush must reach the
# message below, not end this script without a FAIL line.
first_at() { grep -n -- "$1" "$ROT/order" | head -n 1 | cut -d: -f1 || true; }
last_at() { grep -n -- "$1" "$ROT/order" | tail -n 1 | cut -d: -f1 || true; }
verified="$(last_at '^verify read$')"
flushed_file="$(first_at "^sync file $ROT/backups/\.$(d 0)\.partial$")"
flushed_dir="$(first_at "^sync dir $ROT/backups holding: ")"
first_rm="$(first_at "^rm -f -- $ROT/backups/")"
[[ -n "$verified" && -n "$flushed_file" && -n "$flushed_dir" && -n "$first_rm" ]] ||
  fail "a verification, a flush or a deletion is missing: $(cat "$ROT/order")"
((verified < flushed_file && flushed_file < flushed_dir && flushed_dir < first_rm)) ||
  fail "the order is not verify, flush the file, flush the folder, delete: $(cat "$ROT/order")"
held="$(sed -n "${flushed_dir}p" "$ROT/order")"
[[ "$held" == *" $(d 0) "* && "$held" != *partial* && "$held" == *" $(d 9) "* ]] ||
  fail "the folder was not flushed with the new dump under its name and the old ones still there: $held"
ok "the new dump is flushed to the disk under its partial name, then the folder with the dump renamed and every old dump still in it, and only then is the first old dump deleted"
rot_new
seed "$(d 30)" "$(d 20)" "deploys/$(d 30 predeploy-aaaaaaaaaaaa)"
before="$(tree_of)"
rot 1 "$NOW" FAKE_FAIL_SYNC=file
out_has "could not flush $ROT/backups/.$(d 0).partial to the disk, nothing deleted"
no_delete "$before"
rot 1 "$NOW" FAKE_FAIL_SYNC=dir
out_has "could not flush $ROT/backups to the disk, nothing deleted"
[[ "$(tree_of)" == "$(printf '%s\n%s\n' "$before" "backups/$(d 0)" | LC_ALL=C sort)" ]] || fail "more than the new dump changed: $(tree_of | tr '\n' ' ')"
grep -q -e "deleted old backup" -e "^backup=" "$WORK/out" && fail "a run whose flush failed deleted or reported a backup: $(cat "$WORK/out")"
ok "a flush that fails ends the run with exit 1 and deletes nothing: before the name, the partial file is removed; after it, the dump stays and so does every old one"
rot_new
seed "$(d 9)" "$(d 1)"
rot 0 "$NOW" FAKE_OLD_SYNC=1
only . "$(d 1)" "$(d 0)"
[[ "$(grep -c '^sync$' "$ROT/order" || true)" == 2 ]] || fail "an old sync was not run twice without a path: $(cat "$ROT/order")"
last_sync="$(last_at '^sync$')"
first_rm="$(first_at "^rm -f -- $ROT/backups/")"
[[ -n "$last_sync" && -n "$first_rm" ]] || fail "an old sync or the deletion is missing: $(cat "$ROT/order")"
((last_sync < first_rm)) || fail "an old sync ran after the first deletion: $(cat "$ROT/order")"
rot_new
seed "$(d 9)" "$(d 1)"
before="$(tree_of)"
rot 1 "$NOW" FAKE_OLD_SYNC=1 FAKE_FAIL_SYNC=plain
out_has "db.sh: could not flush the disks, nothing deleted"
no_delete "$before"
ok "a sync that takes no path (coreutils before 8.24) flushes every disk instead, twice, still before the first deletion; when that sync fails the run ends with exit 1, its partial file removed and nothing deleted"

rot_new
for n in 10 11 12 13 14 15 16; do seed "$(d "$n")"; done
seed "deploys/$(d 12 predeploy-aaaaaaaaaaaa)" "deploys/$(d 11 predeploy-bbbbbbbbbbbb)"
before="$(tree_of)"
rot 1 "$NOW" FAKE_FAIL_DUMP=1
no_delete "$before"
rot 0 "$NOW"
only . "$(d 10)" "$(d 0)"
only deploys
verified="$(last_at '^verify read$')"
first_rm="$(first_at "^rm -f -- $ROT/backups/")"
[[ -n "$verified" && -n "$first_rm" ]] || fail "no verification or no deletion in: $(cat "$ROT/order")"
((verified < first_rm)) || fail "a dump was deleted before the new one was verified: $(cat "$ROT/order")"
[[ "$(sed -n '1p' "$ROT/order")" == pg_dump ]] || fail "the run did not start with the dump: $(cat "$ROT/order")"
[[ "$(grep -c "^rm -f -- $ROT/backups/" "$ROT/order")" == 8 ]] || fail "not eight of the nine old dumps: $(cat "$ROT/order")"
out_has "kept $(d 10) in $ROT/backups although it is 10 days old, the limit is 7: every other dump goes in this run"
rot 0 "$(ago -1)"
only . "$(d 0)" "$(d -1)"
ok "a Jetson switched off for ten days: a first night whose dump fails deletes none of the nine old dumps; the next writes a dump, verifies it, and only then deletes eight, keeping the newest of them for one more run; the next run, the night after, deletes it"

rot_new
seed "$(d 30)" "$(d 20)" "$(d 10)" "deploys/$(d 30 predeploy-aaaaaaaaaaaa)" "deploys/$(d 20 predeploy-bbbbbbbbbbbb)" "incoming/$(d 30)"
before="$(tree_of)"
rot 0 "$NOW" -- prune
no_delete "$before"
out_has "prune applies the count rule alone"
rot 0 "$NOW" BACKUP_KEEP=2 -- prune
only . "$(d 20)" "$(d 10)"
only deploys "$(d 30 predeploy-aaaaaaaaaaaa)" "$(d 20 predeploy-bbbbbbbbbbbb)"
only incoming "$(d 30)"
[[ "$(grep -c -e pg_dump -e verify "$ROT/order" || true)" == 0 ]] || fail "prune made a dump: $(cat "$ROT/order")"
ok "prune alone never deletes by age (three dumps of 10 to 30 days stay), only by count, and only in its own folder"

rot_new
seed "$(d 30)" "$(d 20)" "deploys/$(d 30 predeploy-aaaaaaaaaaaa)" "incoming/$(d 30)"
rot 0 "$NOW" -- restore-live "$(d 20)"
only . "$(d 30)" "$(d 20)" "$(d 0 pre-restore)"
only deploys "$(d 30 predeploy-aaaaaaaaaaaa)"
only incoming "$(d 30)"
before="$(tree_of)"
rot 0 "$NOW" -- restore "$ROT/backups/incoming/$(d 30)"
no_delete "$before"
rot 0 "$NOW" -- verify "$(d 30)"
no_delete "$before"
rot 0 "$NOW" -- list
no_delete "$before"
out_has "retention: BACKUP_KEEP=7 (the default of ops/db.sh)"
ok "a restore never deletes: restore-live of a 20 day old dump writes its pre-restore dump and keeps every old one, the dump being restored included; restore, verify and list change nothing"

rot_new
seed "$(d 9)" "deploys/$(d 9 predeploy-aaaaaaaaaaaa)" "deploys/$(d 8 predeploy-bbbbbbbbbbbb)" "incoming/$(d 9)"
rot 0 "$NOW" FAKE_BUSY_FD=8
only . "$(d 0)"
only deploys "$(d 9 predeploy-aaaaaaaaaaaa)" "$(d 8 predeploy-bbbbbbbbbbbb)"
only incoming
out_has "skipped $ROT/backups/deploys: a backup or a restore holds its lock"
ok "deploys/ is left alone while a predeploy backup or a restore holds its lock: exit 0, the folder of the run still rotated"
rot_new
seed "$(d 9)" "deploys/$(d 9 predeploy-aaaaaaaaaaaa)" "incoming/$(d 9)"
mkdir "$ROT/backups/deploys/.lock" # a lock file that cannot be opened: here it is a folder
rot 1 "$NOW"
only . "$(d 0)"
only deploys "$(d 9 predeploy-aaaaaaaaaaaa)"
only incoming
out_has "skipped $ROT/backups/deploys: cannot open the lock file $ROT/backups/deploys/.lock"
grep -q "holds its lock" "$WORK/out" && fail "a lock file that cannot be opened is reported as held: $(cat "$WORK/out")"
out_has "^backup=$ROT/backups/$(d 0)$"
out_has "db.sh: the dump is written and kept, but the rotation is not complete: cannot open the lock file $ROT/backups/deploys/.lock"
ok "a lock file of deploys/ that cannot be opened is said as what it is, not as held: nothing is deleted there, the rest of the run is done, the dump is reported, and the run ends with exit 1 so that it is seen"
rot_new
seed "$(d 30)" "$(d 20)"
rm -f "$ROT/backups/.lock"
mkdir "$ROT/backups/.lock"
before="$(tree_of)"
rot 1 "$NOW"
out_has "db.sh: cannot open the lock file $ROT/backups/.lock"
no_delete "$before"
[[ ! -s "$ROT/order" ]] || fail "a dump was made without the lock: $(cat "$ROT/order")"
ok "the run's own lock file that cannot be opened: no dump, nothing deleted, exit 1 with the reason instead of bash's raw error"
if command -v flock >/dev/null; then
  # The same with the real flock: this script holds the lock of deploys/ (descriptor 7) during
  # the run, as a predeploy backup or a restore from that folder would. No fake sync either in
  # this folder of fakes: the sync of this machine's coreutils is given the partial file, then
  # the folder, and the run only ends well if it takes both.
  rot_new
  mkdir -p "$ROT/realbin"
  cp "$DBT/bin/docker" "$DBT/bin/stat" "$DBT/bin/date" "$DBT/bin/rm" "$ROT/realbin/"
  seed "$(d 9)" "deploys/$(d 9 predeploy-aaaaaaaaaaaa)" "deploys/$(d 8 predeploy-bbbbbbbbbbbb)"
  exec 7>>"$ROT/backups/deploys/.lock"
  flock -n 7 || fail "the test could not take the lock of deploys/"
  rot 0 "$NOW" DB_SH_TEST_BIN="$ROT/realbin"
  exec 7>&-
  only . "$(d 0)"
  only deploys "$(d 9 predeploy-aaaaaaaaaaaa)" "$(d 8 predeploy-bbbbbbbbbbbb)"
  out_has "skipped $ROT/backups/deploys: a backup or a restore holds its lock"
  rot 0 "$(ago 0 011800)" DB_SH_TEST_BIN="$ROT/realbin"
  only deploys
  ok "with the real flock and the real sync: held by another process, deploys/ is skipped; released, the next run deletes what is too old there; both runs flushed their dump and its folder through coreutils' own sync"
else
  ok "no flock on this machine: the held lock of deploys/ is checked with the real flock in CI"
fi

step "T10, safety: a dump less than half the size of the last one accepted deletes nothing"
# Decision of 2026-10-11 (the review's N4, b): a database that lost its data still dumps, and the
# dump still verifies. The fake pg_dump writes 16 bytes, like the dumps seeded so far; `big` seeds
# a larger one and FAKE_DUMP_BYTES makes the run's own larger.
big() { # big <path under backups/> <bytes>: a fake dump of that size, written now
  mkdir -p "$(dirname "$ROT/backups/$1")"
  {
    printf 'PGDMP fake dump\n'
    head -c "$(($2 - 16))" /dev/zero | tr '\0' x
  } >"$ROT/backups/$1"
}
rot_new
seed "$(d 30)" "$(d 20)" "deploys/$(d 30 predeploy-aaaaaaaaaaaa)" "incoming/$(d 30)"
big "$(d 1)" 100
before="$(tree_of)"
rot 0 "$NOW"
out_has "   WARNING: held for 1 run(s), since $(d 0). This dump is 16 bytes, less than half of $(d 1) (100 bytes), the last one accepted in $ROT/backups"
out_has "this run keeps its dump and deletes NOTHING, in any folder"
out_has "^backup=$ROT/backups/$(d 0)$"
[[ "$(tree_of)" == "$(printf '%s\n%s\n%s\n' "$before" "backups/$(d 0)" "backups/.size-hold" | LC_ALL=C sort)" ]] ||
  fail "more than the new dump and the mark changed: $(tree_of | tr '\n' ' ')"
[[ "$(cat "$ROT/backups/.size-hold")" == "$(printf '%s\n%s\n1' "$(d 1)" "$(d 0)")" ]] ||
  fail "the mark does not hold the last accepted dump, the first dump held and the count: $(cat "$ROT/backups/.size-hold")"
grep -q -e "deleted old backup" -e "although" "$WORK/out" && fail "a held run deleted or spared: $(cat "$WORK/out")"
grep "^rm " "$ROT/order" | grep -v "/\.size-hold\.new$" | grep -q "backups/" && fail "a held run removed something in the backup folders: $(cat "$ROT/order")"
ok "yesterday's dump is 100 bytes and today's 16: exit 0, the dump kept and reported, a warning with both sizes, and not one of the four dumps past the limit deleted, in any folder"
rot 0 "$(ago -1)"
out_has "   WARNING: held for 2 run(s), since $(d 0). This dump is 16 bytes, less than half of $(d 1) (100 bytes), the last one accepted in $ROT/backups"
grep -q "deleted old backup" "$WORK/out" && fail "the second small dump was measured against the first: $(cat "$WORK/out")"
[[ -f "$ROT/backups/$(d 30)" && -f "$ROT/backups/deploys/$(d 30 predeploy-aaaaaaaaaaaa)" && -f "$ROT/backups/incoming/$(d 30)" ]] || fail "a held run deleted an old dump"
rot 3 "$(ago -1 120000)" -- retention
out_has "   WARNING: held for 2 run(s), since $(d 0): the rotation of $ROT/backups. A dump came out less than half the size of $(d 1) (100 bytes), the last one accepted there, and its newest is $(d -1) (16 bytes)"
out_has "^held=$ROT/backups$"
out_has "^keep=7$"
grep -q "left alone" "$WORK/out" && fail "the mark is reported as a stray file: $(cat "$WORK/out")"
ok "the night after, as small: still measured against the 100 bytes of the last accepted dump, not against yesterday's 16, so still nothing deleted; retention shows the hold with both sizes, the count and since when, and ends with status 3 so that it is seen"

# The review's N11: prune is the command someone types when a folder grows, and what it would
# take by count is exactly the larger, older dumps the hold keeps.
before="$(tree_of)"
rot 1 "$(ago -1 130000)" BACKUP_KEEP=1 -- prune
out_has "db.sh: prune deletes nothing while the rotation of $ROT/backups is held"
out_has "   WARNING: held for 2 run(s), since $(d 0): the rotation of $ROT/backups"
no_delete "$before"
ok "prune during a hold: refused with exit 1 and the hold shown, nothing deleted, the mark untouched"

rot 0 "$(ago -2)" FAKE_DUMP_BYTES=50
out_has "size check: this dump is 50 bytes, measured against $(d 1) (100 bytes): passed"
out_has "the size check passes again after 2 run(s) held: the hold on the rotation of $ROT/backups is lifted"
[[ ! -e "$ROT/backups/.size-hold" ]] || fail "the mark is still there"
only . "$(d 1)" "$(d 0)" "$(d -1)" "$(d -2)"
only deploys
only incoming
rot 0 "$(ago -2 120000)" -- retention
grep -q -e WARNING -e ERROR -e "^held=" "$WORK/out" && fail "retention still shows a hold: $(cat "$WORK/out")"
ok "a dump of half that size (50 of 100, not less than half) lifts the hold by itself, that run deletes what was waiting, and retention ends with 0 again"

# The review's N12: a hold has no bound of its own, so from the seventh run held it is an ERROR
# that says since when, and still nothing is deleted.
rot_new
seed "$(d 30)"
big "$(d 8)" 100
for n in 7 6 5 4 3 2; do
  rot 0 "$(ago "$n")"
  out_has "   WARNING: held for $((8 - n)) run(s), since $(d 7)"
done
rot 0 "$(ago 1)"
out_has "   ERROR: held for 7 runs, since $(d 7). Nothing is deleted there, the folder grows by a dump at every run without any bound but the disk, and the 7 days wait with it. This dump is 16 bytes, less than half of $(d 8) (100 bytes)"
grep -q "   WARNING: held" "$WORK/out" && fail "the seventh run held is still a warning: $(cat "$WORK/out")"
rot 0 "$NOW"
out_has "   ERROR: held for 8 runs, since $(d 7)"
only . "$(d 30)" "$(d 8)" "$(d 7)" "$(d 6)" "$(d 5)" "$(d 4)" "$(d 3)" "$(d 2)" "$(d 1)" "$(d 0)" .size-hold
rot 3 "$(ago 0 120000)" -- retention
out_has "   ERROR: held for 8 runs, since $(d 7). Nothing is deleted there, the folder grows by a dump at every run without any bound but the disk, and the 7 days wait with it: the rotation of $ROT/backups"
ok "seven nights held: six warnings, then an ERROR from the seventh run that says since which dump and that the folder grows without bound; ten dumps, three of them past the limit, none deleted; retention says the same"

rot_new
seed "$(d 30)"
big "$(d 1)" 100
rot 0 "$NOW"
[[ -f "$ROT/backups/.size-hold" && -f "$ROT/backups/$(d 30)" ]] || fail "the run was not held: $(cat "$WORK/out")"
rot 0 "$(ago -1)" BACKUP_ACCEPT_SHRINK=1
out_has "BACKUP_ACCEPT_SHRINK is set: the size of this dump (16 bytes) is accepted and the hold on the rotation of $ROT/backups is lifted"
only . "$(d 1)" "$(d 0)" "$(d -1)"
rot 0 "$(ago -2)"
out_has "size check: this dump is 16 bytes, measured against $(d -1) (16 bytes): passed"
grep -q -e WARNING -e ERROR "$WORK/out" && fail "the run after an accepted shrink is held again: $(cat "$WORK/out")"
[[ ! -e "$ROT/backups/.size-hold" ]] || fail "the mark came back"
ok "a database meant to shrink: one run with BACKUP_ACCEPT_SHRINK=1 lifts the hold and deletes what was waiting, and the run after it, without the variable, is measured against that accepted dump"
# The review's N17: the same with a dump named ahead of the clock in the folder. It is not what a
# run is measured against, so the override still lasts exactly one run.
rot_new
big "$(d 2)" 100
big jobapp-2027-10-11T011700.dump 100
rot 0 "$NOW"
out_has "less than half of $(d 2) (100 bytes)"
rot 0 "$(ago -1)" BACKUP_ACCEPT_SHRINK=1
rot 0 "$(ago -2)"
out_has "size check: this dump is 16 bytes, measured against $(d -1) (16 bytes): passed"
grep -q -e WARNING -e ERROR "$WORK/out" && fail "with a dump named ahead in the folder the run after the override is held again: $(cat "$WORK/out")"
ok "with a 100 byte dump named a year ahead in the folder: a run is measured against the newest dump that is not after it, the override lasts one run, and the next run passes against the accepted dump"

rot_new
seed "$(d 1)"
before="$(tree_of)"
rot 1 "$NOW" BACKUP_ACCEPT_SHRINK=yes
out_has "BACKUP_ACCEPT_SHRINK must be 1 or not set, got 'yes'"
no_delete "$before"
[[ ! -s "$ROT/order" ]] || fail "a dump was made with an invalid BACKUP_ACCEPT_SHRINK: $(cat "$ROT/order")"
rot_new
big "$(d 1)" 32
seed "$(d 9)"
rot 0 "$NOW"
only . "$(d 1)" "$(d 0)"
grep -q -e WARNING -e ERROR "$WORK/out" && fail "exactly half is held: $(cat "$WORK/out")"
rot_new
big "$(d 1)" 100
rot 0 "$NOW" FAKE_DUMP_BYTES=40
out_has "   WARNING: held for 1 run(s), since $(d 0). This dump is 40 bytes, less than half of $(d 1) (100 bytes)"
rot_new
rot 0 "$NOW"
out_has "size check: this dump is 16 bytes, with no other dump of this folder to be measured against: passed"
grep -q -e WARNING -e ERROR "$WORK/out" && fail "a first dump, with nothing to be measured against, is held: $(cat "$WORK/out")"
rot_new
big "$(d 1)" 100
printf 'BACKUP_ACCEPT_SHRINK=1\n' >>"$ROT/app/.env"
rot 0 "$NOW"
out_has "   WARNING: held for 1 run(s), since $(d 0)"
ok "exactly half passes, 40 of 100 is held (less than half, not less than a quarter), a first dump passes, any other value of BACKUP_ACCEPT_SHRINK is refused before the dump, and the variable in .env is not read: it is for one run, from that run's environment"

# The review's N16: restore-live writes its pre-restore dump without this check, so that dump is
# never what a later run is measured against.
rot_new
big "$(d 2)" 100
seed "$(d 1 pre-restore)" "$(d 9)"
rot 0 "$NOW"
out_has "   WARNING: held for 1 run(s), since $(d 0). This dump is 16 bytes, less than half of $(d 2) (100 bytes)"
[[ -f "$ROT/backups/$(d 9)" ]] || fail "a run measured against a pre-restore dump deleted: $(cat "$WORK/out")"
rot_new
seed "$(d 1 pre-restore)"
rot 0 "$NOW"
out_has "size check: this dump is 16 bytes, with no other dump of this folder to be measured against: passed"
ok "a small pre-restore dump of yesterday is not the reference: the run is measured against the 100 byte dump before it and held; with nothing but a pre-restore dump beside it, a run has nothing to be measured against"
# Nor is an empty file with a dump's name (N14 and N16 together): any dump is more than half of
# nothing, so measured against it the run would pass and delete the good dumps before it.
rot_new
big "$(d 2)" 100
seed "$(d 9)"
: >"$ROT/backups/$(d 1)"
rot 0 "$NOW"
out_has "   WARNING: held for 1 run(s), since $(d 0). This dump is 16 bytes, less than half of $(d 2) (100 bytes)"
[[ -f "$ROT/backups/$(d 9)" ]] || fail "a run measured against an empty file deleted: $(cat "$WORK/out")"
rot_new
: >"$ROT/backups/$(d 1)"
rot 0 "$NOW"
out_has "size check: this dump is 16 bytes, with no other dump of this folder to be measured against: passed"
ok "an empty file named like yesterday's dump is not the reference either: the run is measured against the 100 byte dump before it and held; with nothing but that empty file beside it, a run has nothing to be measured against"

# The review's N15: the hold lives in that one file. It is read only when it is a regular file
# that can be read, written whole or not at all, and a hold never ends because of the mark.
rot_new
seed "$(d 9)" "$(d 1)"
printf 'not a dump name\n' >"$ROT/backups/.size-hold"
rot 0 "$NOW"
only . "$(d 1)" "$(d 0)"
out_has "size check: the mark $ROT/backups/.size-hold names no dump of this folder, so no hold stands on its word"
[[ ! -e "$ROT/backups/.size-hold" ]] || fail "a mark that names no dump of the folder was kept"
for kind in link fifo folder; do
  rot_new
  seed "$(d 9)" "$(d 1)"
  case "$kind" in
    link)
      # a link to a file that holds a dump's name: if it were read, the run would be held on it
      printf '%s\n%s\n3\n' "$(d 1)" "$(d 1)" >"$ROT/outside/mark"
      ln -s "$ROT/outside/mark" "$ROT/backups/.size-hold"
      ;;
    fifo) mkfifo "$ROT/backups/.size-hold" ;;
    folder) mkdir "$ROT/backups/.size-hold" ;;
  esac
  before="$(tree_of)"
  rot 1 "$NOW"
  out_has "   WARNING: $ROT/backups/.size-hold is not a regular file that can be read"
  out_has "whether the rotation of $ROT/backups is held cannot be known, so nothing was deleted"
  out_has "^backup=$ROT/backups/$(d 0)$"
  out_has "db.sh: the dump is written and kept, but the rotation is not complete"
  [[ "$(tree_of)" == "$(printf '%s\n%s\n' "$before" "backups/$(d 0)" | LC_ALL=C sort)" ]] || fail "$kind: more than the new dump changed: $(tree_of | tr '\n' ' ')"
  grep -q -e "deleted old backup" -e "held for" "$WORK/out" && fail "$kind: the mark was read, or the run deleted: $(cat "$WORK/out")"
  rot 3 "$NOW" -- retention
  out_has "   WARNING: $ROT/backups/.size-hold is not a regular file that can be read"
  rot 1 "$NOW" BACKUP_KEEP=1 -- prune
  out_has "db.sh: prune deletes nothing while the rotation of $ROT/backups is held, or while its mark cannot be read"
  [[ -f "$ROT/backups/$(d 9)" && -f "$ROT/backups/$(d 1)" ]] || fail "$kind: prune deleted with a mark it cannot read"
  [[ "$kind" != link || "$(cat "$ROT/outside/mark")" == "$(printf '%s\n%s\n3' "$(d 1)" "$(d 1)")" ]] ||
    fail "the file a linked mark points to was written: $(cat "$ROT/outside/mark")"
done
ok "a mark that is a symbolic link (to a file naming a real dump), a FIFO or a folder is never opened: the run does not hang, deletes nothing, keeps and reports its dump and ends with exit 1; retention ends with 3 and prune is refused; a plain mark that names no dump holds nothing and is removed"
rot_new
seed "$(d 9)"
big "$(d 1)" 100
printf 'the owner of this file wants it kept\n' >"$ROT/outside/precious"
ln -s "$ROT/outside/precious" "$ROT/backups/.size-hold.new" # where the mark is written before it is renamed
rot 0 "$NOW"
out_has "   WARNING: held for 1 run(s), since $(d 0)"
[[ -f "$ROT/backups/.size-hold" && ! -L "$ROT/backups/.size-hold" && "$(sed -n '1p' "$ROT/backups/.size-hold")" == "$(d 1)" ]] ||
  fail "the mark was not written as a file of its own"
[[ "$(cat "$ROT/outside/precious")" == "the owner of this file wants it kept" ]] || fail "the mark was written through a link: $(cat "$ROT/outside/precious")"
rot_new
seed "$(d 9)"
big "$(d 1)" 100
mkdir -p "$ROT/backups/.size-hold.new/in-the-way"
rot 1 "$NOW"
out_has "db.sh: the dump is written and kept, but the rotation is not complete: could not write the mark $ROT/backups/.size-hold"
out_has "   WARNING: held for 1 run(s), since $(d 0)"
[[ -f "$ROT/backups/$(d 9)" ]] || fail "a held run whose mark could not be written deleted"
rot_new
seed "$(d 9)"
big "$(d 2)" 100
rot 0 "$(ago 1)"
rm -f "$ROT/backups/.size-hold"
rot 0 "$NOW"
out_has "size check: this dump is 16 bytes, measured against $(d 1) (16 bytes): passed"
ok "the mark is written beside itself and renamed: a link waiting at that name is replaced, never written through; a mark that cannot be written ends the run with exit 1 and nothing deleted; a mark removed by hand ends the hold, and the run then says in so many words which dump it was measured against"

rot_new
seed "$(d 30)" "deploys/$(d 9 predeploy-bbbbbbbbbbbb)"
big "deploys/$(d 1 predeploy-aaaaaaaaaaaa)" 100
rot 0 "$NOW" BACKUP_DIR="$ROT/backups/deploys" -- backup --tag predeploy-0123456789ab
out_has "   WARNING: held for 1 run(s), since $(d 0 predeploy-0123456789ab). This dump is 16 bytes, less than half of $(d 1 predeploy-aaaaaaaaaaaa) (100 bytes), the last one accepted in $ROT/backups/deploys"
out_has "^backup=$ROT/backups/deploys/$(d 0 predeploy-0123456789ab)$"
only deploys "$(d 9 predeploy-bbbbbbbbbbbb)" "$(d 1 predeploy-aaaaaaaaaaaa)" "$(d 0 predeploy-0123456789ab)" .size-hold
rot 0 "$(ago 0 030000)"
only . "$(d 0 '' 030000)"
only deploys "$(d 1 predeploy-aaaaaaaaaaaa)" "$(d 0 predeploy-0123456789ab)" .size-hold
rot 3 "$(ago 0 040000)" -- retention
out_has "   WARNING: held for 1 run(s), since $(d 0 predeploy-0123456789ab): the rotation of $ROT/backups/deploys"
out_has "^held=$ROT/backups/deploys$"
ok "the same in the deploy's own folder: the predeploy backup ends well, so the deploy goes on, and deletes nothing there; the nightly run, whose own dump passes its check, still deletes by age in deploys/, and retention shows the hold of that folder"

step "T10, safety: only whole dump names, directly in the folder, never through a link"
rot_new
odd=(notes.txt jobapp-latest.dump jobapp-2026-13-45T250000.dump jobapp-2026-02-30T000000.dump jobapp-20260901T000000.dump
  jobapp-2026-09-01T000000.dump.bak "x jobapp-2026-09-01T000000.dump" jobapp-2026-09-01T000000-UPPER.dump
  jobapp-2026-09-01T000000-.dump jobapp-2026-09-01T000000.dump.partial .jobapp-2026-09-01T000000.dump.partial
  jobapp-1969-12-31T000000.dump)
for f in "${odd[@]}"; do printf 'not a dump of this script\n' >"$ROT/backups/$f"; done
nl=$'jobapp-2026-09-04T000000.dump\njobapp-2026-09-05T000000.dump'
printf 'a name with a line break\n' >"$ROT/backups/$nl"
printf 'log\n' >"$ROT/backups/backup.log"
mkdir "$ROT/backups/jobapp-2026-09-02T000000.dump" "$ROT/backups/previous-tree"
# What every deploy leaves next to the dumps (deploy.yml, ops/previous.sh save-tree): passed over.
printf '.env*\n' >"$ROT/backups/previous-tree.rsyncignore"
printf 'name: previous release\n' >"$ROT/backups/previous-tree/compose.yml"
: >"$ROT/backups/.deploy.lock"
printf 'inside a folder\n' >"$ROT/backups/jobapp-2026-09-02T000000.dump/inside"
printf 'a file outside the backup folder\n' >"$ROT/outside/precious.dump"
ln -s "$ROT/outside/precious.dump" "$ROT/backups/jobapp-2026-09-03T000000.dump"
mkdir "$ROT/outside/elsewhere"
printf 'PGDMP fake dump\n' >"$ROT/outside/elsewhere/$(d 30 predeploy-aaaaaaaaaaaa)"
ln -s "$ROT/outside/elsewhere" "$ROT/backups/deploys"
seed "$(d 9)" "$(d 1)" "incoming/$(d 9)" "incoming/cv.pdf" "incoming/local-truncated.dump"
rot 0 "$NOW"
for f in "${odd[@]}" "$nl" backup.log jobapp-2026-09-02T000000.dump/inside; do
  [[ -f "$ROT/backups/$f" ]] || fail "'$f' was removed"
done
[[ -L "$ROT/backups/jobapp-2026-09-03T000000.dump" && "$(cat "$ROT/outside/precious.dump")" == "a file outside the backup folder" ]] ||
  fail "the link named like an old dump, or the file it points to, was touched"
[[ -L "$ROT/backups/deploys" && -f "$ROT/outside/elsewhere/$(d 30 predeploy-aaaaaaaaaaaa)" ]] ||
  fail "a deploys/ that is a link was followed: $(cat "$WORK/out")"
[[ ! -e "$ROT/backups/$(d 9)" && ! -e "$ROT/backups/incoming/$(d 9)" ]] || fail "the real old dumps next to them were not deleted"
only incoming cv.pdf local-truncated.dump
for f in "${odd[@]}" "jobapp-2026-09-02T000000.dump (a directory)" "jobapp-2026-09-03T000000.dump (a symbolic link)"; do
  grep -qF "left alone in $ROT/backups, not a dump of this script: $f" "$WORK/out" || fail "'$f' is not reported: $(cat "$WORK/out")"
done
grep -qF "left alone in $ROT/backups/incoming, not a dump of this script: local-truncated.dump (outside the retention, delete it by hand)" "$WORK/out" ||
  fail "the odd upload is not reported: $(cat "$WORK/out")"
out_has "left alone: $ROT/backups/deploys is a symbolic link or a file, not a folder of dumps"
grep -q "not a dump of this script: \(backup\.log\|\.lock\|\.deploy\.lock\|previous-tree\|incoming\)" "$WORK/out" &&
  fail "the script reports files the app keeps there itself (the review's N1: previous-tree.rsyncignore): $(cat "$WORK/out")"
[[ -f "$ROT/backups/previous-tree.rsyncignore" && -f "$ROT/backups/previous-tree/compose.yml" ]] || fail "the saved tree of the deploy was touched"
[[ "$(grep -c "^rm -f -- " "$ROT/order")" == 2 ]] || fail "more than the two old dumps reached rm: $(cat "$ROT/order")"
ok "left alone and reported, run after run: no timestamp, a date that does not exist, another suffix or prefix, an upper case tag, a partial file, a name with a line break, a folder and a symbolic link named like old dumps (the file it points to untouched), a deploys/ that is a link"
rot 0 "$NOW" -- retention
out_has "left alone: $ROT/backups/deploys is a symbolic link or a file, not a folder of dumps"
grep -qF "left alone in $ROT/backups, not a dump of this script: jobapp-latest.dump" "$WORK/out" || fail "retention does not list what is left alone: $(cat "$WORK/out")"
ok "retention lists the same files and the same link, so status shows what sits outside the rotation"

# The review's B2: the folder of the run can itself be the link, and that is what the deploy's own
# command line makes of a linked deploys/. The dump is written through it; nothing is deleted
# through it, by age or by count, and the mode of the folder it points to is not touched.
real_mode() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"; }
names_in() { (cd "$1" && find . -mindepth 1 -maxdepth 1 | sed 's|^\./||' | LC_ALL=C sort | tr '\n' ' '); }
# The review's B3: the same with a slash or a /. after the name, in any number. `[[ -L folder/ ]]`
# is false for a symbolic link, so the name is trimmed once, before anything looks at it.
for tail in "" / /. // /./ /.//.; do
  rot_new
  mkdir "$ROT/outside/elsewhere"
  chmod 755 "$ROT/outside/elsewhere"
  printf 'PGDMP fake dump\n' >"$ROT/outside/elsewhere/$(d 30 predeploy-aaaaaaaaaaaa)"
  printf 'PGDMP fake dump\n' >"$ROT/outside/elsewhere/$(d 20 predeploy-bbbbbbbbbbbb)"
  for h in 01 02 03 04 05 06 07 08; do
    printf 'PGDMP fake dump\n' >"$ROT/outside/elsewhere/$(d 1 "predeploy-aaaaaaaaaa$h" "${h}0000")"
  done
  printf 'not a dump\n' >"$ROT/outside/elsewhere/keep.txt"
  ln -s "$ROT/outside/elsewhere" "$ROT/backups/deploys"
  elsewhere="$(cd "$ROT/outside/elsewhere" && find . -mindepth 1 ! -name .lock | LC_ALL=C sort)"
  rot 0 "$NOW" BACKUP_DIR="$ROT/backups/deploys$tail" -- backup --tag predeploy-0123456789ab
  [[ "$(cd "$ROT/outside/elsewhere" && find . -mindepth 1 ! -name .lock | LC_ALL=C sort)" == "$(printf '%s\n%s\n' "$elsewhere" "./$(d 0 predeploy-0123456789ab)" | LC_ALL=C sort)" ]] ||
    fail "BACKUP_DIR=deploys$tail: the deploy's backup changed more than its own dump in the folder the link points to: $(names_in "$ROT/outside/elsewhere")"
  [[ "$(real_mode "$ROT/outside/elsewhere")" == 755 ]] || fail "BACKUP_DIR=deploys$tail: the mode of the folder behind the link was changed: $(real_mode "$ROT/outside/elsewhere")"
  out_has "left alone: $ROT/backups/deploys is a symbolic link. Nothing is deleted through a link"
  out_has "^backup=$ROT/backups/deploys/$(d 0 predeploy-0123456789ab)$"
  grep -q -e "^rm -f -- " "$ROT/order" && fail "BACKUP_DIR=deploys$tail: a dump was deleted through a link: $(cat "$ROT/order")"
  grep -q "deleted old backup" "$WORK/out" && fail "BACKUP_DIR=deploys$tail: the run says it deleted through a link: $(cat "$WORK/out")"
  rot 0 "$NOW" BACKUP_DIR="$ROT/backups/deploys$tail" BACKUP_KEEP=1 -- prune
  out_has "left alone: $ROT/backups/deploys is a symbolic link"
  [[ "$(find "$ROT/outside/elsewhere" -name 'jobapp-*.dump' | wc -l | tr -d ' ')" == 11 ]] || fail "BACKUP_DIR=deploys$tail: prune deleted through a link"
  rot 0 "$NOW" BACKUP_DIR="$ROT/backups/deploys$tail" -- retention
  out_has "left alone: $ROT/backups/deploys is a symbolic link"
  out_has "^keep=7$"
  [[ "$(real_mode "$ROT/outside/elsewhere")" == 755 ]] || fail "BACKUP_DIR=deploys$tail: prune or retention changed the mode of the folder behind the link"
done
ok "the deploy's own command line with a deploys/ that is a link, named as it is or with /, /., //, /./ or /.//. after it: the dump is written through it, and of the ten dumps in the folder it points to, two past the age limit and two more beyond the newest 7, none goes, by backup or by prune; that folder's mode is untouched; backup, prune and retention say why, with the name trimmed"
for tail in "" / /. //; do
  rot_new
  seed "$(d 30)" "$(d 20)" "incoming/$(d 30)"
  mv "$ROT/backups" "$ROT/outside/real"
  ln -s "$ROT/outside/real" "$ROT/backups"
  rot 0 "$NOW" BACKUP_DIR="$ROT/backups$tail"
  [[ -f "$ROT/outside/real/$(d 30)" && -f "$ROT/outside/real/$(d 20)" && -f "$ROT/outside/real/incoming/$(d 30)" && -f "$ROT/outside/real/$(d 0)" ]] ||
    fail "BACKUP_DIR=backups$tail: a backup folder that is a link was rotated: $(names_in "$ROT/outside/real")"
  out_has "left alone: $ROT/backups is a symbolic link"
  grep -q -e "^rm -f -- " "$ROT/order" && fail "BACKUP_DIR=backups$tail: a dump was deleted through a link: $(cat "$ROT/order")"
done
ok "the folder of the nightly run when it is a link, with or without a slash or a /. after its name: the same, nothing deleted in it or in its incoming/, and said"
rot_new
seed "$(d 9)" "$(d 1)" "incoming/$(d 9)"
rot 0 "$NOW" BACKUP_DIR="$ROT/backups/./"
only . "$(d 1)" "$(d 0)"
only incoming
out_has "^backup=$ROT/backups/$(d 0)$"
for bad in / // /. "$HOME" "$HOME/" "$HOME/."; do
  rot 1 "$NOW" "BACKUP_DIR=$bad" -- list
  out_has "db.sh: BACKUP_DIR must name a folder of its own, not / and not the home folder"
done
ok "a real folder named with a slash is rotated as usual, under its trimmed name; / and the home folder, however they are spelled, are refused by every command before it does anything"
grep -nE '(^|[^[:alnum:]_])rm[[:space:]]+-[[:alnum:]]*[rR]' ops/db.sh && fail "ops/db.sh has a recursive rm"
grep -nE -- '(^|[^-])-(delete|exec)([^[:alnum:]_-]|$)|xargs' ops/db.sh && fail "ops/db.sh deletes through find or xargs"
# shellcheck disable=SC2016 # the literal text of ops/db.sh
[[ "$(grep -c 'rm -f -- ' ops/db.sh)" == 1 && "$(grep -c 'rm -f -- "$dir/$name"' ops/db.sh)" == 1 ]] ||
  fail "a dump is deleted somewhere else than in remove_dump"
ok "ops/db.sh: no recursive rm, no find -delete, no xargs; one rm deletes dumps, by one full path"

step "T10, ops/db.sh retention: what must hold for the 7 days, shown so that it can be checked"
rot_new
seed "$(d 6)" "$(d 0 '' 001000)" "deploys/$(d 3 predeploy-aaaaaaaaaaaa)"
# The folder as the Jetson has it: what the deploys and the nightly run keep there themselves.
mkdir "$ROT/backups/previous-tree" "$ROT/backups/incoming"
printf '.env*\n' >"$ROT/backups/previous-tree.rsyncignore"
printf 'log\n' >"$ROT/backups/backup.log"
: >"$ROT/backups/.deploy.lock"
: >"$ROT/backups/deploys/.lock"
before="$(tree_of)"
rot 0 "$NOW" -- retention
out_has "retention: BACKUP_KEEP=7 (the default of ops/db.sh)"
out_has "$ROT/backups: 2 dump(s), the newest 0 day(s) old, the oldest 6 day(s) old ($(d 6))$"
out_has "$ROT/backups/deploys: 1 dump(s), the newest 3 day(s) old, the oldest 3 day(s) old ($(d 3 predeploy-aaaaaaaaaaaa))$"
out_has "$ROT/backups/incoming: no dump"
out_has "^keep=7$"
# The check the pull request gives for the Jetson, word for word: it must be able to pass.
grep -q -e WARNING -e PAST -e "left alone" -e "scratch database" -e "not checked" "$WORK/out" &&
  fail "a healthy folder shaped like the Jetson's is reported as a problem: $(cat "$WORK/out")"
no_delete "$before"
[[ ! -s "$ROT/order" && ! -e "$ROT/calls" ]] || fail "retention made a dump, stopped a service or deleted: $(cat "$ROT/order" "$ROT/calls" 2>/dev/null)"
ok "a healthy server shaped like the Jetson (both lock files, backup.log, previous-tree and its .rsyncignore): 7 from the default, the newest and the oldest dump of each folder, and not one line with WARNING, PAST, left alone or scratch database; no lock taken, nothing changed"
rot 0 "$NOW" FAKE_BUSY_FD=9 -- retention
out_has "^keep=7$"
printf 'a stray copy\n' >"$ROT/backups/incoming/notes.dump"
rot 0 "$NOW" -- retention
out_has "left alone in $ROT/backups/incoming, not a dump of this script: notes.dump"
rm -f "$ROT/backups/incoming/notes.dump"
ok "retention needs no lock (it answers while a backup holds it), and a stray file in incoming/ is the one line that then appears"

# The same folders three days later, with no backup since (the nightly run failed or the Jetson
# was off), so a dump is now past the limit, and with a scratch database left by restore --keep.
rot 0 "$(ago -3)" FAKE_SCRATCH=1 -- retention
out_has "$ROT/backups: 2 dump(s), the newest 3 day(s) old, the oldest 9 day(s) old ($(d 6)), PAST the limit of 7 days: the next verified backup deletes it"
out_has "WARNING: no verified backup in $ROT/backups for 3 days: nothing is deleted until one succeeds"
out_has "left alone: the scratch database jobapp_restore_check exists, a whole copy of a dump kept by restore --keep and outside the retention"
no_delete "$before"
rot 0 "$NOW" FAKE_SCRATCH=down -- retention
out_has "not checked: whether the scratch database jobapp_restore_check of restore --keep exists"
rot 0 "$NOW" BACKUP_KEEP=14 -- retention
out_has "retention: BACKUP_KEEP=14 (set in the environment of this run)"
out_has "WARNING: BACKUP_KEEP=14 keeps dumps up to 14 days"
out_has "^keep=14$"
ok "what breaks the 7 days is said: no verified backup for 3 days, a dump past the limit, a scratch database left by restore --keep (or a database that could not be asked), a BACKUP_KEEP above 7"
# The review's N7: a dump named ahead of today (a clock that was wrong) has no age, and must not
# hide a week without a backup behind its date.
rot_new
seed "$(d 9)" "$(d 3)" jobapp-2027-10-11T011700.dump
rot 0 "$NOW" -- retention
out_has "$ROT/backups: 2 dump(s), the newest 3 day(s) old, the oldest 9 day(s) old ($(d 9)), PAST the limit of 7 days: the next verified backup deletes it; 1 more named ahead of today (a clock was wrong), the last jobapp-2027-10-11T011700.dump"
out_has "WARNING: no verified backup in $ROT/backups for 3 days"
grep -q -- " -[0-9][0-9]* day" "$WORK/out" && fail "a negative age is printed: $(cat "$WORK/out")"
rot_new
seed jobapp-2027-10-11T011700.dump jobapp-2027-10-12T011700.dump
rot 0 "$NOW" -- retention
out_has "$ROT/backups: 2 dump(s), every one named ahead of today (a clock was wrong), the last jobapp-2027-10-12T011700.dump"
out_has "WARNING: no verified backup in $ROT/backups dated up to today"
grep -q -- " -[0-9][0-9]* day" "$WORK/out" && fail "a negative age is printed: $(cat "$WORK/out")"
ok "a dump named ahead of today is counted apart and named, never given a negative age, and the warning about days without a verified backup still fires"
rot_new
seed "$(d 9)" "$(d 1)"
rot 0 "$NOW" FAKE_SCRATCH=1
only . "$(d 1)" "$(d 0)"
out_has "left alone: the scratch database jobapp_restore_check exists"
rot 0 "$(ago 0 011800)" FAKE_SCRATCH=down
out_has "not checked: whether the scratch database jobapp_restore_check of restore --keep exists"
out_has "^backup=$ROT/backups/$(d 0 '' 011800)$"
ok "every backup says so too, in backup.log night after night: the scratch database while it exists; a database that does not answer that question does not fail the backup"

step "T10, the nightly schedule against a fake Jetson: the line, the retention of its runs, no reinstall for a change"
# This fake ssh runs the remote command for real, with bash, in a folder that plays the Jetson's
# home: a deployed tree with the real ops/db.sh, and a crontab kept in a file. So install, status
# and remove are the commands the Jetson would run, and the installed line is then run the way
# cron runs it.
JH="$WORK/jetson"
mkdir -p "$JH/home/jobapp/ops" "$JH/home/jobapp-backups" "$JH/bin" "$JH/tmp"
cp ops/db.sh "$JH/home/jobapp/ops/db.sh"
printf 'name: fake\n' >"$JH/home/jobapp/compose.yml"
printf 'POSTGRES_USER=jobapp\nPOSTGRES_DB=jobapp\n' >"$JH/home/jobapp/.env"
cat >"$JH/bin/ssh" <<'FAKE'
#!/usr/bin/env bash
# Plays the Jetson: the remote command runs here, in the folder that is its home.
for a in "$@"; do
  [[ "$a" != -O ]] || exit 0
done
cd "$FAKE_HOME" || exit 255
HOME="$FAKE_HOME" exec bash -c "${*: -1}"
FAKE
cat >"$JH/bin/crontab" <<'FAKE'
#!/usr/bin/env bash
# The Jetson user's crontab, kept in a file.
case "${1:-}" in
  -l)
    [[ -f "$FAKE_HOME/.crontab" ]] || {
      echo "no crontab for jetson" >&2
      exit 1
    }
    cat "$FAKE_HOME/.crontab"
    ;;
  -)
    # Like the real one: the whole input is read before the table is replaced, so the pipeline
    # `crontab -l | ... | crontab -` reads the old table to its end.
    cat >"$FAKE_HOME/.crontab.new" && mv "$FAKE_HOME/.crontab.new" "$FAKE_HOME/.crontab"
    ;;
  *) exit 2 ;;
esac
FAKE
chmod +x "$JH/bin/"*
jet() { # jet <cmd...>: deploy.sh against the fake Jetson, with no backup setting from this shell
  env -u BACKUP_KEEP -u BACKUP_ACCEPT_SHRINK -u BACKUP_DIR -u BACKUP_CRON -u DEPLOY_DIR PATH="$JH/bin:$PATH" FAKE_HOME="$JH/home" \
    DEPLOY_HOST=fakejetson DB_SH_TEST_BIN="$DBT/bin" FAKE_DIR="$JH" FAKE_NOW="$NOW" TMPDIR="$JH/tmp" "$@"
}
MARK="# jobapp-backup (./deploy.sh backup-schedule)"
cron_run() { # cron_run <UTC time>: the command of the installed line, under cron's bare environment
  local cmd
  cmd="$(grep -F "$MARK" "$JH/home/.crontab" | cut -d' ' -f6-)"
  : >"$JH/order"
  env -i HOME="$JH/home" LOGNAME=jetson PATH=/usr/bin:/bin DB_SH_TEST_BIN="$DBT/bin" FAKE_DIR="$JH" FAKE_NOW="$1" \
    TMPDIR="$JH/tmp" BACKUP_MIN_FREE_MB=0 sh -c "$cmd" || fail "the cron command failed: $(tail -n 5 "$JH/home/jobapp-backups/backup.log")"
}
jet_holds() { (cd "$JH/home/jobapp-backups" && find . -maxdepth 1 -name 'jobapp-*.dump' | sed 's|^\./||' | LC_ALL=C sort | tr '\n' ' '); }
# What the Jetson has today: the line of before T10, with its 14, next to a line that is not ours.
# shellcheck disable=SC2016 # $HOME is for cron's shell
OLD_LINE='17 3 * * * JOBAPP_DIR=jobapp BACKUP_DIR=jobapp-backups BACKUP_KEEP=14 "$HOME/jobapp/ops/db.sh" backup >>"$HOME/jobapp-backups/backup.log" 2>&1 '"$MARK"
printf '%s\n%s\n' "# someone else's line, which must survive" "$OLD_LINE" >"$JH/home/.crontab"
for n in 10 8 1; do printf 'PGDMP fake dump\n' >"$JH/home/jobapp-backups/$(d "$n")"; done
run 0 jet ./deploy.sh backup-schedule status
out_has "installed: 17 3 \* \* \* JOBAPP_DIR=jobapp BACKUP_DIR=jobapp-backups BACKUP_KEEP=14 "
out_has "the line carries BACKUP_KEEP=14, which wins"
out_has "retention: BACKUP_KEEP=14 (set in the environment of this run)"
out_has "WARNING: BACKUP_KEEP=14 keeps dumps up to 14 days"
grep -q "^keep=" "$WORK/out" && fail "status shows the machine line of ops/db.sh: $(cat "$WORK/out")"
cron_run "$NOW"
[[ "$(jet_holds)" == "$(d 10) $(d 8) $(d 1) $(d 0) " ]] || fail "the old line did not keep 14 days: $(jet_holds)"
ok "the line installed before T10 carries BACKUP_KEEP=14: status says so, with the warning, and its run still keeps a dump of 10 days"

run 0 jet ./deploy.sh backup-schedule install
# The second time with a slash after the folder's name: the line is the same, the name trimmed.
# shellcheck disable=SC2088 # a literal ~/, as BACKUP_DIR is written in .env
run 0 jet env BACKUP_DIR='~/jobapp-backups/' ./deploy.sh backup-schedule install
[[ "$(grep -cF "$MARK" "$JH/home/.crontab")" == 1 ]] || fail "not exactly one jobapp line: $(cat "$JH/home/.crontab")"
grep -qx "# someone else's line, which must survive" "$JH/home/.crontab" || fail "a foreign line was lost: $(cat "$JH/home/.crontab")"
# shellcheck disable=SC2016 # $HOME is for cron's shell
NEW_LINE='17 3 * * * JOBAPP_DIR=jobapp BACKUP_DIR=jobapp-backups "$HOME/jobapp/ops/db.sh" backup >>"$HOME/jobapp-backups/backup.log" 2>&1 '"$MARK"
grep -qxF "$NEW_LINE" "$JH/home/.crontab" || fail "the installed line is not the expected one: $(cat "$JH/home/.crontab")"
out_has "the line carries no BACKUP_KEEP: each run reads it from jobapp/.env on fakejetson, then from the default of the ops/db.sh deployed there"
out_has "retention: BACKUP_KEEP=7 (the default of ops/db.sh)"
grep -qF "$NEW_LINE" README.md || fail "the README does not show the line that install writes: $NEW_LINE"
ok "install, twice: one line, without a BACKUP_KEEP, the 14 gone, the foreign line kept; install shows the retention its runs apply (7), and the README shows this very line"
cron_run 2026-10-12T011700
[[ "$(jet_holds)" == "$(d 1) $(d 0) jobapp-2026-10-12T011700.dump " ]] || fail "the new line did not apply 7 days: $(jet_holds)"
grep -q "BACKUP_KEEP=7, the default of ops/db.sh" "$JH/home/jobapp-backups/backup.log" || fail "backup.log lacks the retention: $(cat "$JH/home/jobapp-backups/backup.log")"
grep -q "^backup=$JH/home/jobapp-backups/jobapp-2026-10-12T011700.dump$" "$JH/home/jobapp-backups/backup.log" || fail "backup.log lacks the result"
ok "the installed line, run like cron (sh, HOME and a bare PATH): a verified dump, the dumps of 9 and 11 days ago deleted, the retention in backup.log"

crontab_before="$(cat "$JH/home/.crontab")"
printf 'BACKUP_KEEP=2\n' >>"$JH/home/jobapp/.env"
run 0 jet env FAKE_NOW=2026-10-12T120000 ./deploy.sh backup-schedule status
out_has "retention: BACKUP_KEEP=2 (set in $JH/home/jobapp/.env)"
out_has "$JH/home/jobapp-backups: 3 dump(s), the newest 0 day(s) old, the oldest 2 day(s) old ($(d 1)), PAST the limit of 2 days"
out_has "last lines of jobapp-backups/backup.log"
cron_run 2026-10-13T011700
[[ "$(jet_holds)" == "jobapp-2026-10-12T011700.dump jobapp-2026-10-13T011700.dump " ]] || fail "the .env value was not applied: $(jet_holds)"
[[ "$(cat "$JH/home/.crontab")" == "$crontab_before" ]] || fail "the crontab changed"
ok "a retention changed in the Jetson's .env is what the next nightly run applies and what status shows: no reinstall, the crontab untouched"

run 0 jet env BACKUP_KEEP=14 ./deploy.sh backup-schedule install
[[ "$(grep -cF "$MARK" "$JH/home/.crontab")" == 1 ]] || fail "not exactly one jobapp line: $(cat "$JH/home/.crontab")"
grep -qF " BACKUP_DIR=jobapp-backups BACKUP_KEEP=14 \"\$HOME/jobapp/ops/db.sh\" backup " "$JH/home/.crontab" || fail "an explicit BACKUP_KEEP is not in the line: $(cat "$JH/home/.crontab")"
out_has "the line carries BACKUP_KEEP=14, which wins over jobapp/.env on fakejetson"
out_has "retention: BACKUP_KEEP=14 (set in the environment of this run)"
ok "a BACKUP_KEEP set on the Mac at install is written into the line and wins over the Jetson's .env, and status says which one applies"

run 0 jet ./deploy.sh backup-schedule remove
run 0 jet ./deploy.sh backup-schedule remove
grep -qF "$MARK" "$JH/home/.crontab" && fail "the line is still there after remove"
grep -qx "# someone else's line, which must survive" "$JH/home/.crontab" || fail "remove lost a foreign line"
run 0 jet ./deploy.sh backup-schedule status
out_has "not installed"
out_has "retention: BACKUP_KEEP=2 (set in $JH/home/jobapp/.env)"
ok "remove, twice: only the jobapp line goes; status says not installed and what a backup run by hand would keep"

# The review's N19: the log is the past. A WARNING of the last run (made at 14, the night before a
# reinstall) must not be read as the state now, so its lines are marked and told apart.
printf '   WARNING: BACKUP_KEEP=14 keeps dumps up to 14 days, more than the 7 days\nbackup=/x\n' >>"$JH/home/jobapp-backups/backup.log"
run 0 jet env FAKE_NOW=2026-10-13T140000 ./deploy.sh backup-schedule status
out_has "last lines of jobapp-backups/backup.log (the last run, as it was then; the state now is above):"
out_has "^     | *WARNING: BACKUP_KEEP=14 keeps dumps up to 14 days"
grep "WARNING" "$WORK/out" | grep -qv '^     | ' && fail "a WARNING of the log is not told apart from the state now: $(cat "$WORK/out")"
ok "status marks the last lines of backup.log as those of the last run: a WARNING there is prefixed, and no unprefixed line warns when the state now is sound"

# The review's N12: a rotation that is held must be seen. status ends non zero while one stands,
# with both sizes and the one command that lifts it, for each folder.
{
  printf 'PGDMP fake dump\n'
  head -c 84 /dev/zero | tr '\0' x
} >"$JH/home/jobapp-backups/jobapp-2026-10-13T120000.dump"
printf 'PGDMP fake dump\n' >"$JH/home/jobapp-backups/jobapp-2026-10-13T130000.dump"
printf '%s\n%s\n2\n' jobapp-2026-10-13T120000.dump jobapp-2026-10-13T130000.dump >"$JH/home/jobapp-backups/.size-hold"
mkdir -p "$JH/home/jobapp-backups/deploys"
cp "$JH/home/jobapp-backups/jobapp-2026-10-13T120000.dump" "$JH/home/jobapp-backups/deploys/jobapp-2026-10-13T120000-predeploy-aaaaaaaaaaaa.dump"
printf 'PGDMP fake dump\n' >"$JH/home/jobapp-backups/deploys/jobapp-2026-10-13T130000-predeploy-bbbbbbbbbbbb.dump"
printf '%s\n%s\n7\n' jobapp-2026-10-13T120000-predeploy-aaaaaaaaaaaa.dump jobapp-2026-10-13T130000-predeploy-bbbbbbbbbbbb.dump >"$JH/home/jobapp-backups/deploys/.size-hold"
run 1 jet env FAKE_NOW=2026-10-13T140000 ./deploy.sh backup-schedule status
out_has "   WARNING: held for 2 run(s), since jobapp-2026-10-13T130000.dump: the rotation of $JH/home/jobapp-backups. A dump came out less than half the size of jobapp-2026-10-13T120000.dump (100 bytes), the last one accepted there, and its newest is jobapp-2026-10-13T130000.dump (16 bytes)"
out_has "   ERROR: held for 7 runs, since jobapp-2026-10-13T130000-predeploy-bbbbbbbbbbbb.dump"
out_has "to lift the hold of $JH/home/jobapp-backups: BACKUP_ACCEPT_SHRINK=1 ./deploy.sh backup"
out_has "to lift the hold of $JH/home/jobapp-backups/deploys: BACKUP_DIR=jobapp-backups/deploys BACKUP_ACCEPT_SHRINK=1 ./deploy.sh backup"
out_has "deploy.sh: a rotation is held on fakejetson, or its mark cannot be read (above): its backups delete nothing until it is lifted"
grep -q -e "^held=" -e "^keep=" "$WORK/out" && fail "status shows the machine lines of ops/db.sh: $(cat "$WORK/out")"
rm -f "$JH/home/jobapp-backups/.size-hold" "$JH/home/jobapp-backups/deploys/.size-hold"
run 0 jet env FAKE_NOW=2026-10-13T140000 ./deploy.sh backup-schedule status
ok "status with a rotation held in the folder and in deploys/: exit 1, each hold with both sizes, its count and since when (an ERROR from seven runs), and for each folder the one command that lifts it; exit 0 again once the holds are gone"

# A Jetson whose deployed ops/db.sh is the one of before T10: it knows no `retention`.
printf '#!/usr/bin/env bash\necho "db.sh: usage: db.sh backup [--tag NAME] | list | prune" >&2\nexit 1\n' >"$JH/home/jobapp/ops/db.sh"
run 0 jet ./deploy.sh backup-schedule status
out_has "retention: not reported. The ops/db.sh deployed on fakejetson is older than this command"
out_has "deploy, then run ./deploy.sh backup-schedule status again"
ok "status against a Jetson not deployed since T10 says the retention is not reported and to deploy first, instead of guessing"

step "T10, the defaults written down: one number, 7, and no other left"
grep -q '^DEFAULT_KEEP=7$' ops/db.sh || fail "ops/db.sh does not default to 7"
grep -q '^PROMISED_DAYS=7$' ops/db.sh || fail "ops/db.sh does not hold the 7 days users are promised"
grep -q '^BACKUP_KEEP=$' .env.example || fail ".env.example does not document BACKUP_KEEP (empty: 7)"
# shellcheck disable=SC2016 # a pattern for the literal text of the scripts
grep -nE 'BACKUP_KEEP:-[0-9]|setting BACKUP_KEEP [0-9]|BACKUP_KEEP=[0-9]+ (bash|"\$HOME)|default 14' deploy.sh ops/db.sh .github/workflows/deploy.yml .env.example &&
  fail "a retention other than the default of ops/db.sh is written in a script, the workflow or .env.example"
grep -q "newest 10 kept" README.md && fail "the README still says the newest 10 predeploy dumps are kept"
ok "ops/db.sh holds the only default (7); deploy.sh, the deploy workflow and .env.example name no number of their own"

step "deploy lock: the Jetson releases it after DEPLOY_LOCK_MAX_S even if the Mac never closes it"
# lock_script is the remote shell remote_lock sends; it is taken from deploy.sh itself.
eval "$(sed -n '/^lock_script() {/,/^}/p' deploy.sh)"
script_text="$(lock_script jobapp-backups 3600)"
[[ "$script_text" == *"timeout 3600 cat >/dev/null"* ]] || fail "no remote timeout in the lock script: $script_text"
# shellcheck disable=SC2016 # the literal text of deploy.sh, not an expansion
grep -qF 'remote "$(lock_script "$bdir" "$max")"' deploy.sh || fail "remote_lock does not send lock_script"
if command -v flock >/dev/null && command -v timeout >/dev/null; then
  lockdir="$WORK/lockhome"
  mkdir -p "$lockdir"
  # The holder's stdin never closes (a sleeping Mac): only the 2 second cap can end it.
  (cd "$lockdir" && sleep 30 | bash -c "$(lock_script backups 2)") >"$WORK/holder" 2>&1 &
  holder=$!
  for _ in $(seq 1 50); do
    grep -q '^locked' "$WORK/holder" && break
    sleep 0.1
  done
  grep -q '^locked' "$WORK/holder" || fail "the holder never took the lock: $(cat "$WORK/holder")"
  flock -n "$lockdir/backups/.deploy.lock" true && fail "the lock was free while held"
  start=$SECONDS
  flock -w 10 "$lockdir/backups/.deploy.lock" true || fail "the lock was still held after 10s"
  ((SECONDS - start <= 5)) || fail "the lock took $((SECONDS - start))s to go"
  kill "$holder" 2>/dev/null || true
  wait "$holder" 2>/dev/null || true
  ok "the remote lock command holds flock, then lets go after its cap with stdin still open"
else
  ok "the remote lock command runs timeout <DEPLOY_LOCK_MAX_S> cat (no flock here: the release itself is checked in CI)"
fi

step "generate_env: a new .env.jetson gets a CREDENTIALS_KEY, an existing one is never rewritten"
# The functions as deploy.sh defines them, run against a scratch LOCAL_ENV.
gen_env() {
  (
    eval "$(sed -n -e '/^info() {/p' -e '/^warn() {/p' -e '/^die() {/,/^}/p' -e '/^envval() {/,/^}/p' \
      -e '/^generate_env() {/,/^}/p' -e '/^warn_missing_credentials_key() {/,/^}/p' deploy.sh)"
    # shellcheck disable=SC2034 # read by the evaluated functions
    HOST=jetson RDIR=jobapp LOCAL_ENV="$1"
    "$2"
  )
}
genv="$WORK/gen/.env.jetson"
mkdir -p "$WORK/gen"
run 0 gen_env "$genv" generate_env
key="$(sed -n 's/^CREDENTIALS_KEY=//p' "$genv")"
[[ "$(printf '%s' "$key" | openssl base64 -d -A | wc -c | tr -d ' ')" == 32 ]] ||
  fail "CREDENTIALS_KEY is not 32 bytes of base64: $(grep -c CREDENTIALS_KEY "$genv") line(s)"
grep -q '^APP_ENV=production$' "$genv" || fail "generated .env lacks APP_ENV=production"
[[ "$(stat -c %a "$genv" 2>/dev/null || stat -f %Lp "$genv")" == 600 ]] || fail "generated .env is not mode 600"
before="$(cat "$genv")"
run 1 gen_env "$genv" generate_env
out_has "never overwrites"
[[ "$(cat "$genv")" == "$before" ]] || fail "generate_env rewrote an existing .env.jetson"
run 0 gen_env "$genv" warn_missing_credentials_key
[[ ! -s "$WORK/out" ]] || fail "warned although CREDENTIALS_KEY is set: $(cat "$WORK/out")"
printf 'APP_ENV=production\nSECRET_KEY=x\n' >"$WORK/gen/old.env"
run 0 gen_env "$WORK/gen/old.env" warn_missing_credentials_key
out_has "has no CREDENTIALS_KEY"
[[ "$(cat "$WORK/gen/old.env")" == $'APP_ENV=production\nSECRET_KEY=x' ]] || fail "the warning changed the old .env"
other="$WORK/gen/second.env"
run 0 gen_env "$other" generate_env
[[ "$(sed -n 's/^CREDENTIALS_KEY=//p' "$other")" != "$key" ]] || fail "two generated keys are equal"
ok "new .env.jetson: random 32 byte CREDENTIALS_KEY, mode 600; an existing file is never rewritten, only warned about when it lacks the key"

step "ops/previous.sh: :previous from the running containers, the deployed tree saved and restored"
mkdir -p "$WORK/bin4"
cat >"$WORK/bin4/docker" <<'FAKE'
#!/usr/bin/env bash
echo "docker $*" >>"$FAKE_CALLS"
case "$*" in
  "compose -f compose.yml --profile tunnel ps -q --status running api") echo "${FAKE_API_ID:-}" ;;
  "compose -f compose.yml --profile tunnel ps -q --status running caddy") echo "${FAKE_CADDY_ID:-}" ;;
  "compose -f compose.yml --profile tunnel ps -q --status running worker") echo "${FAKE_WORKER_ID:-}" ;;
  "inspect -f {{.Image}} c0ffee") echo "sha256:1111" ;;
  "inspect -f {{.Image}} beef") echo "sha256:2222" ;;
  "inspect -f {{.Image}} feed") echo "sha256:3333" ;;
esac
exit 0
FAKE
chmod +x "$WORK/bin4/docker"
prev() { env PATH="$WORK/bin4:$PATH" FAKE_CALLS="$WORK/calls4" "$@"; }
: >"$WORK/calls4"
run 0 prev FAKE_API_ID=c0ffee FAKE_WORKER_ID=feed FAKE_CADDY_ID=beef ops/previous.sh tag-running
grep -qx "docker tag sha256:1111 jobapp-api:previous" "$WORK/calls4" || fail "api not tagged from its container: $(cat "$WORK/calls4")"
grep -qx "docker tag sha256:3333 jobapp-worker:previous" "$WORK/calls4" || fail "worker not tagged from its container: $(cat "$WORK/calls4")"
grep -qx "docker tag sha256:2222 jobapp-caddy:previous" "$WORK/calls4" || fail "caddy not tagged from its container"
grep -q "jobapp-.*:latest" "$WORK/calls4" && fail ":latest was read: $(cat "$WORK/calls4")"
: >"$WORK/calls4"
run 0 prev FAKE_API_ID= FAKE_WORKER_ID= FAKE_CADDY_ID=beef ops/previous.sh tag-running
out_has "api: no running container, jobapp-api:previous left as it was"
out_has "worker: no running container, jobapp-worker:previous left as it was"
grep -q "jobapp-api:previous" "$WORK/calls4" && fail "a stopped api was still tagged: $(cat "$WORK/calls4")"
grep -q "jobapp-worker:previous" "$WORK/calls4" && fail "a stopped worker was still tagged: $(cat "$WORK/calls4")"
grep -qx "docker tag sha256:2222 jobapp-caddy:previous" "$WORK/calls4" || fail "caddy not tagged"
# First deploy of the worker image: the running worker still runs the api's image; its id is kept.
: >"$WORK/calls4"
run 0 prev FAKE_API_ID=c0ffee FAKE_WORKER_ID=c0ffee FAKE_CADDY_ID=beef ops/previous.sh tag-running
grep -qx "docker tag sha256:1111 jobapp-worker:previous" "$WORK/calls4" || fail "the worker's old api image was not kept"
ok "tag-running tags the image id of each running container, worker included (never :latest); a stopped service keeps its :previous"

# Every image the stack builds is built before the restart and rolled back on failure.
grep -q "^  for svc in api worker caddy; do$" deploy.sh || fail "deploy.sh does not build api, worker and caddy"
grep -q "^          for svc in api worker caddy; do$" .github/workflows/deploy.yml || fail "deploy.yml does not build the worker"
grep -q "^            for img in jobapp-api jobapp-worker jobapp-caddy; do$" .github/workflows/deploy.yml ||
  fail "deploy.yml does not restore jobapp-worker:previous"
grep -q "^IMAGES=(api:jobapp-api worker:jobapp-worker caddy:jobapp-caddy)$" ops/previous.sh || fail "previous.sh misses the worker"
ok "deploy.sh and deploy.yml build api, worker and caddy; the rollback restores jobapp-worker too"

app="$WORK/app"
saved="$WORK/backups/previous-tree"
mkdir -p "$app/caddy"
cp .rsyncignore "$app/"
printf 'old compose\n' >"$app/compose.yml"
printf 'old caddy\n' >"$app/caddy/Caddyfile"
printf 'gone later\n' >"$app/removed-by-new.txt"
printf 'SECRET=1\n' >"$app/.env"
printf 'abc123\n' >"$app/.deployed-commit"
run 0 ops/previous.sh save-tree "$app" "$saved"
[[ ! -e "$saved/.env" && ! -e "$saved/.deployed-commit" ]] || fail "secrets or the commit marker were copied"
# The new deploy: files changed, one removed, one added; the secrets left alone.
printf 'new compose\n' >"$app/compose.yml"
printf 'new caddy\n' >"$app/caddy/Caddyfile"
rm "$app/removed-by-new.txt"
printf 'new\n' >"$app/added-by-new.txt"
run 0 ops/previous.sh restore-tree "$saved" "$app"
[[ "$(cat "$app/compose.yml")" == "old compose" && "$(cat "$app/caddy/Caddyfile")" == "old caddy" ]] ||
  fail "compose.yml or the Caddyfile were not restored"
[[ -f "$app/removed-by-new.txt" && ! -e "$app/added-by-new.txt" ]] || fail "the tree is not the previous one"
[[ "$(cat "$app/.env")" == "SECRET=1" && "$(cat "$app/.deployed-commit")" == "abc123" ]] ||
  fail ".env or .deployed-commit changed"
run 0 ops/previous.sh save-tree "$WORK/never-deployed" "$saved"
out_has "nothing deployed"
[[ ! -e "$saved" ]] || fail "a stale previous tree survived a first deploy"
run 0 ops/previous.sh restore-tree "$saved" "$app"
out_has "no previous tree"
[[ "$(cat "$app/compose.yml")" == "old compose" ]] || fail "restore without a copy changed files"
run 1 ops/previous.sh bogus
run 1 ops/previous.sh save-tree only-one
ok "save-tree and restore-tree bring back compose.yml, the Caddyfile and the file list; .env and .deployed-commit untouched; first deploy and bad arguments"

step "issue #81: a fake docker and a fake timeout for ops/migrate.sh and the deploy step of deploy.yml"
# One stateful fake for both: every call goes to $FAKE_DIR/calls, in order, and FAKE_* variables
# choose what the Jetson answers (pending migrations, a failing one, a db that is down, an `up`
# that does not become healthy). The fake timeout records its limit and runs the command, or plays
# a limit that was reached (124) or a container that was killed (137).
MIG="$WORK/migrate"
mkdir -p "$MIG/bin" "$MIG/app"
printf 'name: fake\n' >"$MIG/app/compose.yml"
printf 'POSTGRES_USER=jobapp\n' >"$MIG/app/.env"
cat >"$MIG/bin/docker" <<'FAKE'
#!/usr/bin/env bash
args="$*"
echo "docker $args" >>"$FAKE_DIR/calls"
case "$args" in
  "inspect -f {{.State.Status}} "*)
    [[ -n "${FAKE_MIGRATE_STATE:-}" ]] || exit 1
    echo "$FAKE_MIGRATE_STATE"
    ;;
  "inspect -f {{.Image}} "*) echo "sha256:ran-${args##* }" ;;
  *" ps -q --status running db")
    [[ -n "${FAKE_DB_DOWN:-}" && ! -e "$FAKE_DIR/db-up" ]] || echo db-id
    ;;
  *" ps -q --status running "*) echo "${args##* }-id" ;;
  *" up -d --wait"*" db")
    [[ -z "${FAKE_FAIL_DB:-}" ]] || exit 1
    touch "$FAKE_DIR/db-up"
    ;;
  *" run --rm "*"alembic upgrade head"*)
    echo "INFO  [alembic.runtime.migration] Context impl PostgresqlImpl." >&2
    for ((n = 1; n <= ${FAKE_PENDING:-0}; n++)); do
      echo "INFO  [alembic.runtime.migration] Running upgrade 00$((9 + n)) -> 00$((10 + n)), step $n" >&2
    done
    if [[ -n "${FAKE_LEAK:-}" ]]; then
      echo "OperationalError: postgresql+psycopg://jobapp:CANARYPASSWORD@db:5432/jobapp refused" >&2
    fi
    if [[ -n "${FAKE_FAIL_MIGRATE:-}" ]]; then
      echo "psycopg.errors.DuplicateColumn: column \"credential_kind\" already exists" >&2
      exit 1
    fi
    echo "00$((10 + ${FAKE_PENDING:-0})) (head)"
    ;;
  *" up -d --remove-orphans "*)
    echo up >>"$FAKE_DIR/ups"
    if [[ -n "${FAKE_FAIL_UP:-}" && "$(wc -l <"$FAKE_DIR/ups" | tr -d ' ')" == 1 ]]; then exit 1; fi
    ;;
  "volume inspect jobapp_pgdata") [[ -z "${FAKE_NO_VOLUME:-}" ]] || exit 1 ;;
  *" exec -T db sh -c psql "*"pg_terminate_backend"*) echo 1 ;;
esac
exit 0
FAKE
cat >"$MIG/bin/timeout" <<'FAKE'
#!/usr/bin/env bash
echo "timeout $1 $2 $3" >>"$FAKE_DIR/calls"
shift 3
[[ -z "${FAKE_TIMEOUT:-}" ]] || exit 124
if [[ -n "${FAKE_KILLED:-}" ]]; then
  sleep "${FAKE_KILL_AFTER:-0}"
  exit 137
fi
exec "$@"
FAKE
cat >"$MIG/bin/flock" <<'FAKE'
#!/usr/bin/env bash
exit 0
FAKE
chmod +x "$MIG/bin/"*
MIG_RUN="docker compose -f compose.yml --profile tunnel run --rm --no-deps -T --name jobapp-migrate api sh -c alembic upgrade head && alembic current"
in_dir() { # in_dir <dir> <cmd...>: run cmd there
  (cd "$1" && shift && "$@")
}
# mig <expected exit> [env assignments]: ops/migrate.sh in the fake deploy directory
mig() {
  local want="$1"
  shift
  rm -f "$MIG/calls" "$MIG/db-up" "$MIG/ups"
  : >"$MIG/calls"
  run "$want" in_dir "$MIG/app" env -u COMPOSE -u MIGRATE_TIMEOUT_S -u MIGRATE_CONTAINER "$@" \
    FAKE_DIR="$MIG" PATH="$MIG/bin:$PATH" bash "$ROOT/ops/migrate.sh"
}
# mig_calls_are <expected, one per line>: every docker and timeout call of the last run, in order
mig_calls_are() {
  diff <(printf '%s\n' "$@") "$MIG/calls" >/dev/null || fail "calls: $(cat "$MIG/calls")"
}
# untouched <calls file>: nothing stopped, restarted, recreated or downgraded
untouched() {
  grep -Eq ' (stop|restart|down|kill|rm -s|up -d --remove-orphans)( |$)' "$1" && fail "the running stack was touched: $(cat "$1")"
  grep -q "downgrade" "$1" && fail "a downgrade was run: $(cat "$1")"
  return 0
}
ok "fakes ready"

step "ops/migrate.sh: one off container of the new image, no-op at head, stops safely on every failure"
mig 0
out_has "already at head, nothing to apply"
out_has "0010 (head)"
mig_calls_are "docker inspect -f {{.State.Status}} jobapp-migrate" \
  "docker compose -f compose.yml --profile tunnel ps -q --status running db" \
  "timeout -k 20 300" "$MIG_RUN" "docker rm -f jobapp-migrate"
untouched "$MIG/calls"
grep -Eq -- '--service-ports|--publish| -p ' "$MIG/calls" && fail "the one off container publishes a port: $(cat "$MIG/calls")"
ok "at head: one container of the api service (--rm, --no-deps, no port, named), nothing to apply, the running db left alone, exit 0"
mig 0 FAKE_PENDING=2
out_has "Running upgrade 0010 -> 0011"
out_has "migrations applied: 2"
out_has "0012 (head)"
untouched "$MIG/calls"
ok "pending migrations: alembic's lines in the log, the count and the revision reached; nothing restarted"
mig 1 FAKE_PENDING=1 FAKE_FAIL_MIGRATE=1
out_has "DuplicateColumn"
out_has "alembic upgrade head failed (exit 1, see above)"
out_has "the schema is what it was, and nothing was restarted"
grep -q "migrations applied" "$WORK/out" && fail "a failed migration was reported as applied"
untouched "$MIG/calls"
grep -qx "docker rm -f jobapp-migrate" "$MIG/calls" || fail "the one off container was not removed after a failure"
ok "a failing migration: exit 1 with alembic's error and what to do, the stack untouched, the container removed"
mig 1 FAKE_TIMEOUT=1 MIGRATE_TIMEOUT_S=7
out_has "did not finish within 7s and were stopped"
out_has "nothing was restarted"
grep -qx "timeout -k 20 7" "$MIG/calls" || fail "MIGRATE_TIMEOUT_S did not reach timeout: $(cat "$MIG/calls")"
grep -qx "docker rm -f jobapp-migrate" "$MIG/calls" || fail "the container was not removed after a timeout"
out_has "migration sessions ended in Postgres: 1"
lock_id="$(sed -n 's/^MIGRATION_LOCK_ID = //p' api/alembic/env.py | tr -d '_')"
[[ -n "$lock_id" ]] || fail "no MIGRATION_LOCK_ID in api/alembic/env.py"
grep -qx "MIGRATION_LOCK_ID=$lock_id" ops/migrate.sh || fail "ops/migrate.sh does not hold the advisory lock id of alembic/env.py ($lock_id)"
# The sessions it ends: on the migration's advisory lock (single key form, objsubid 1), in the
# current database only (pg_locks is cluster wide), never its own. The whole clause, as sent.
SESSIONS_WHERE="from pg_locks where locktype = 'advisory' and objsubid = 1 and ((classid::bigint << 32) | objid::bigint) = $lock_id and database = (select oid from pg_database where datname = current_database()) and pid <> pg_backend_pid()"
grep -q "exec -T db sh -c psql .* sh select count(\*) from (select pg_terminate_backend(pid) from pg_locks " "$MIG/calls" ||
  fail "the sessions on the migration's advisory lock were not ended in the db container: $(cat "$MIG/calls")"
sent="$(grep -o "from pg_locks where .* pid <> pg_backend_pid()" "$MIG/calls")"
[[ "$sent" == "$SESSIONS_WHERE" ]] || fail "the sessions ended are not those of the current database on the migration's lock: $sent"
untouched "$MIG/calls"
ok "a migration over MIGRATE_TIMEOUT_S: stopped, its container removed, the sessions of the current database on the migration's advisory lock (the id of alembic/env.py) ended in Postgres, exit 1, the stack untouched"
# Exit 137 is a SIGKILL. Before the limit it did not come from timeout: said as a kill, possibly
# out of memory. At the limit (timeout had to send KILL after its grace) it is a timeout.
mig 1 FAKE_KILLED=1
out_has "the migration container was killed after [0-9]*s, before the 300s limit (exit 137): possibly out of memory"
out_has "This is not a timeout"
out_has "nothing was restarted"
grep -q "did not finish within" "$WORK/out" && fail "a container killed before the limit was reported as a timeout"
out_has "migration sessions ended in Postgres: 1"
grep -qx "docker rm -f jobapp-migrate" "$MIG/calls" || fail "the container was not removed after a kill"
untouched "$MIG/calls"
mig 1 FAKE_KILLED=1 FAKE_KILL_AFTER=1 MIGRATE_TIMEOUT_S=1
out_has "did not finish within 1s and were stopped"
grep -q "was killed after" "$WORK/out" && fail "a kill by timeout at the limit was reported as out of memory"
out_has "migration sessions ended in Postgres: 1"
ok "exit 137 before the limit: reported as killed, possibly out of memory, not as a timeout, its session ended and the stack untouched; exit 137 at the limit: a timeout"
# The README's recovery block gives a human the same statement, and no longer says that removing
# the container ends the transaction.
readme_where="$(tr '\n' ' ' <README.md | tr -s ' ' | grep -o "from pg_locks where [^;]* pid <> pg_backend_pid()" || true)"
[[ "$readme_where" == "$SESSIONS_WHERE" ]] || fail "the README's recovery statement is not the one ops/migrate.sh runs: $readme_where"
grep -q "remove it (ends its transaction)" README.md && fail "the README still says docker rm ends the transaction"
grep -q "this alone does not end its transaction" README.md || fail "the README does not say that docker rm alone leaves the session"
ok "README recovery: docker rm alone does not end the transaction, and the statement to end the session is the one ops/migrate.sh runs"
for state in running restarting created; do
  mig 1 FAKE_MIGRATE_STATE="$state"
  out_has "another migration is in progress (container jobapp-migrate is $state): nothing was run"
  mig_calls_are "docker inspect -f {{.State.Status}} jobapp-migrate"
done
ok "a migration container that is running, restarting or created: this run stops, and never removes or kills it"
mig 0 FAKE_MIGRATE_STATE=exited
out_has "removed the exited container jobapp-migrate left by an earlier run"
grep -qx "$MIG_RUN" "$MIG/calls" || fail "the migration did not run after the leftover was removed"
ok "a leftover container that exited is removed, then the migration runs"
mig 0 FAKE_DB_DOWN=1 FAKE_PENDING=10
out_has "db is not running: starting it"
out_has "migrations applied: 10"
mig_calls_are "docker inspect -f {{.State.Status}} jobapp-migrate" \
  "docker compose -f compose.yml --profile tunnel ps -q --status running db" \
  "docker compose -f compose.yml --profile tunnel up -d --wait --wait-timeout 120 db" \
  "timeout -k 20 300" "$MIG_RUN" "docker rm -f jobapp-migrate"
ok "first install or a stopped db: db alone is started first, then every migration applies"
mig 1 FAKE_DB_DOWN=1 FAKE_FAIL_DB=1
out_has "db did not become healthy within 120s: no migration was run"
grep -q " run --rm " "$MIG/calls" && fail "a migration ran without a database"
ok "a db that does not start: exit 1 before any migration"
mig 1 FAKE_LEAK=1 FAKE_FAIL_MIGRATE=1
out_has "postgresql+psycopg://\*\*\*:\*\*\*@db:5432/jobapp"
grep -q "CANARYPASSWORD" "$WORK/out" && fail "a password in a URL reached the log"
ok "credentials in anything shaped like a URL are masked on the way to the log"
for bad in abc 0 -5 1.5; do
  mig 1 MIGRATE_TIMEOUT_S="$bad"
  out_has "MIGRATE_TIMEOUT_S must be a whole number of seconds, got '$bad'"
  [[ ! -s "$MIG/calls" ]] || fail "docker was called with a bad timeout: $(cat "$MIG/calls")"
done
mig 1 MIGRATE_CONTAINER='x; rm -rf /'
out_has "MIGRATE_CONTAINER must be a container name"
run 1 in_dir "$MIG/app" env FAKE_DIR="$MIG" PATH="$MIG/bin:$PATH" bash "$ROOT/ops/migrate.sh" downgrade
out_has "usage: ops/migrate.sh"
: >"$MIG/calls"
run 1 in_dir "$WORK" env FAKE_DIR="$MIG" PATH="$MIG/bin:$PATH" bash "$ROOT/ops/migrate.sh"
out_has "no deployed tree here"
[[ ! -s "$MIG/calls" ]] || fail "docker was called outside a deploy directory"
ok "a bad MIGRATE_TIMEOUT_S or container name, any argument (there is no downgrade), or no compose.yml and .env: refused before docker"
mig 0 COMPOSE="docker compose -p scratch -f compose.yml -f compose.mac.yml" MIGRATE_CONTAINER=scratch-migrate
grep -qx "docker compose -p scratch -f compose.yml -f compose.mac.yml run --rm --no-deps -T --name scratch-migrate api sh -c alembic upgrade head && alembic current" "$MIG/calls" ||
  fail "COMPOSE and MIGRATE_CONTAINER not honoured: $(cat "$MIG/calls")"
ok "COMPOSE and MIGRATE_CONTAINER choose the project and the name (scratch stacks on the Mac)"

step "deploy.sh: the migrations run after the build and before the new code starts"
# cmd_deploy, build_images, migrate and start_stack as deploy.sh defines them; every other step
# is a stub that records its name, and `remote` records the command sent to the Jetson.
deploy_order() { # deploy_order <substring of the remote command that fails, or ''>
  local failing="$1"
  : >"$WORK/order"
  (
    eval "$(sed -n -e '/^step() {/p' -e '/^info() {/p' -e '/^die() {/,/^}/p' -e '/^envval() {/,/^}/p' \
      -e '/^setting() {/,/^}/p' -e '/^record() {/,/^}/p' -e '/^rcompose() {/,/^}/p' \
      -e '/^build_images() {/,/^}/p' -e '/^migrate() {/,/^}/p' -e '/^start_stack() {/,/^}/p' \
      -e '/^cmd_deploy() {/,/^}/p' deploy.sh)"
    # shellcheck disable=SC2034 # read by the evaluated functions
    ROOT="$WORK/norepo" HOST=fakejetson RDIR=jobapp RDIR_Q=jobapp TIMES=() \
      RCOMPOSE="docker compose -f compose.yml --profile tunnel"
    # shellcheck disable=SC2329 # called by the evaluated functions
    remote() {
      echo "remote: $*" >>"$WORK/order"
      [[ -z "$failing" || "$*" != *"$failing"* ]]
    }
    for stub in take_lock ssh_master preflight remote_lock sync_tree push_env check_port health warn_leftover_authkey; do
      eval "$stub() { echo $stub >>\"\$WORK/order\"; }"
    done
    # shellcheck disable=SC2329 # called by the evaluated cmd_deploy
    public_url() { echo https://jobapp.example.ts.net; }
    # shellcheck disable=SC2329 # called by the evaluated cmd_deploy
    wait_public() { return 0; }
    cmd_deploy
  )
}
line_of() { grep -n -- "$1" "$WORK/order" | head -n 1 | cut -d: -f1; }
run 0 deploy_order ""
grep -qx "remote: cd jobapp && MIGRATE_TIMEOUT_S=300 bash ops/migrate.sh" "$WORK/order" ||
  fail "migrate does not run ops/migrate.sh in the deploy directory: $(cat "$WORK/order")"
built="$(line_of "build caddy")" migrated="$(line_of "ops/migrate.sh")" upped="$(line_of "up -d --remove-orphans")" healthy="$(line_of "^health$")"
[[ -n "$built" && -n "$migrated" && -n "$upped" && -n "$healthy" ]] || fail "a step is missing: $(cat "$WORK/order")"
((built < migrated && migrated < upped && upped < healthy)) ||
  fail "order is build $built, migrate $migrated, up $upped, health $healthy: $(cat "$WORK/order")"
grep -q "exec -T api alembic" "$WORK/order" && fail "a migration still runs in the started api container"
out_has "migrations: "
ok "cmd_deploy: build api, worker, caddy, then ops/migrate.sh, then compose up --wait, then health"
run 1 deploy_order "ops/migrate.sh"
out_has "the migrations did not apply"
out_has "the containers on fakejetson were not touched and still run the previous release"
out_has "Fix the migration and run ./deploy.sh again"
untouched "$WORK/order"
grep -qx "health" "$WORK/order" && fail "the deploy went on after a failed migration"
ok "a failing migration stops ./deploy.sh before compose up: exit 1, nothing restarted, the message says what to do"
run 1 deploy_order "up -d --remove-orphans"
out_has "the stack did not become healthy"
out_has "The migrations of this release are applied and stay applied (never downgraded automatically)"
grep -q "downgrade" "$WORK/order" && fail "./deploy.sh ran a downgrade"
ok "a stack that does not start after the migrations: exit 1, the schema is kept, no downgrade"
MIGRATE_TIMEOUT_S="1; rm -rf /" run 1 deploy_order ""
out_has "MIGRATE_TIMEOUT_S must be a whole number of seconds"
grep -q "ops/migrate.sh" "$WORK/order" && fail "a bad MIGRATE_TIMEOUT_S reached the Jetson"
ok "MIGRATE_TIMEOUT_S is checked before it is sent to the Jetson"
grep -Eq '^[[:space:]]*[^#[:space:]].*exec -T api alembic upgrade' deploy.sh .github/workflows/deploy.yml &&
  fail "deploy.sh or deploy.yml still migrates inside the started api container"
grep -Eq '^[[:space:]]*[^#[:space:]].*alembic downgrade' deploy.sh ops/migrate.sh ops/previous.sh ops/db.sh .github/workflows/deploy.yml &&
  fail "a deploy script runs alembic downgrade"
ok "no migration in the started api container, and no downgrade command, in deploy.sh, ops/ or deploy.yml"

step "deploy.yml: the deploy step itself, run against the fakes"
# The `run:` script of the step "Deploy under the Jetson deploy lock", cut out of the workflow and
# run as the runner would: a checkout ($GITHUB_WORKSPACE) with the real ops/migrate.sh and
# ops/previous.sh, a stub ops/db.sh and smoke that record their call, and a deployed tree ($APP).
WF="$WORK/workflow"
mkdir -p "$WF"
awk '
  /^      - name: Deploy under the Jetson deploy lock$/ { on = 1; next }
  on && /^      (- name:|# ---)/ { exit }
  on && /^        run: \|$/ { body = 1; next }
  on && body { sub(/^          /, ""); print }
' .github/workflows/deploy.yml >"$WF/step.sh"
head -n 1 "$WF/step.sh" | grep -qx "set -euo pipefail" || fail "the deploy step was not found in deploy.yml"
# shellcheck disable=SC2016 # the literal text of the workflow, not an expansion
grep -q 'echo "deployed \$COMMIT"' "$WF/step.sh" || fail "the deploy step was cut short"
# shellcheck disable=SC2016 # the literal text of the workflow, not an expansion
grep -q '\${{' "$WF/step.sh" && fail "the deploy step holds a workflow expression the test cannot run"
WF_COMMIT="0123456789abcdef0123456789abcdef01234567"
# wf <expected exit> [env assignments]: a fresh checkout and deployed tree, then the step
wf() {
  local want="$1"
  shift
  rm -rf "$WF/ws" "$WF/app" "$WF/backups" "$MIG/calls" "$MIG/db-up" "$MIG/ups"
  mkdir -p "$WF/ws/ops" "$WF/ws/tests" "$WF/app"
  : >"$MIG/calls"
  cp .rsyncignore "$WF/ws/"
  cp ops/migrate.sh ops/previous.sh "$WF/ws/ops/"
  printf 'name: new release\n' >"$WF/ws/compose.yml"
  cat >"$WF/ws/ops/db.sh" <<'STUB'
#!/usr/bin/env bash
echo "db.sh $* in $BACKUP_DIR keep=${BACKUP_KEEP:-unset}" >>"$FAKE_DIR/calls"
echo "   backup: $BACKUP_DIR/jobapp-2026-10-10T120000-predeploy.dump"
# A line the backup prints on its way, a held rotation for instance (T10, review N12).
[[ -z "${FAKE_PREDEPLOY_SAYS:-}" ]] || echo "$FAKE_PREDEPLOY_SAYS"
if [[ -n "${FAKE_FAIL_PREDEPLOY:-}" ]]; then
  # The dump is written, then the rotation stops on a dump it cannot delete (T10, review N10).
  echo "   deleted old backup jobapp-2026-10-01T120000-predeploy.dump (9 days old, the limit is 7)"
  echo "db.sh: could not delete $BACKUP_DIR/jobapp-2026-10-02T120000-predeploy.dump" >&2
  exit 1
fi
echo "backup=$BACKUP_DIR/jobapp-2026-10-10T120000-predeploy.dump"
STUB
  cat >"$WF/ws/tests/jetson_smoke.sh" <<'STUB'
#!/usr/bin/env bash
echo "smoke" >>"$FAKE_DIR/calls"
[[ -z "${FAKE_FAIL_SMOKE:-}" ]] || exit 1
STUB
  chmod +x "$WF/ws/tests/jetson_smoke.sh"
  printf 'POSTGRES_USER=jobapp\n' >"$WF/app/.env"
  if [[ " $* " != *" FIRST_INSTALL=1 "* ]]; then
    cp .rsyncignore "$WF/app/"
    printf 'name: previous release\n' >"$WF/app/compose.yml"
    printf 'previous-commit\n' >"$WF/app/.deployed-commit"
  fi
  run "$want" in_dir "$WF/ws" env -u MIGRATE_TIMEOUT_S -u MIGRATE_CONTAINER -u BACKUP_KEEP "$@" FAKE_DIR="$MIG" \
    PATH="$MIG/bin:$PATH" APP="$WF/app" BACKUPS="$WF/backups" COMMIT="$WF_COMMIT" \
    GITHUB_WORKSPACE="$WF/ws" COMPOSE="docker compose -f compose.yml --profile tunnel" bash -e "$WF/step.sh"
}
wf_line() { grep -n -- "$1" "$MIG/calls" | head -n 1 | cut -d: -f1; }
wf_count() { grep -c -- "$1" "$MIG/calls" || true; }
WF_UP="docker compose -f compose.yml --profile tunnel up -d --remove-orphans --wait --wait-timeout 180"

wf 0 FAKE_PENDING=1
tagged="$(wf_line "tag sha256:ran-api-id jobapp-api:previous")" built="$(wf_line " build caddy")"
dumped="$(wf_line "^db.sh backup --tag predeploy-0123456789ab in $WF/backups/deploys")"
migrated="$(wf_line " run --rm --no-deps -T --name jobapp-migrate api ")" upped="$(wf_line "^$WF_UP$")" smoked="$(wf_line "^smoke$")"
[[ -n "$tagged" && -n "$built" && -n "$dumped" && -n "$migrated" && -n "$upped" && -n "$smoked" ]] ||
  fail "a step is missing: $(cat "$MIG/calls")"
((tagged < built && built < dumped && dumped < migrated && migrated < upped && upped < smoked)) ||
  fail "order is tag $tagged, build $built, backup $dumped, migrate $migrated, up $upped, smoke $smoked: $(cat "$MIG/calls")"
out_has "Running upgrade 0010 -> 0011"
out_has "migrations applied: 1"
[[ "$(wf_count " up -d --remove-orphans ")" == 1 ]] || fail "compose up ran more than once on a good deploy"
[[ "$(wf_count "exec -T api alembic upgrade")" == 0 ]] || fail "a migration ran in the started api container"
[[ "$(cat "$WF/app/.deployed-commit")" == "$WF_COMMIT" ]] || fail ".deployed-commit not written"
[[ "$(cat "$WF/app/compose.yml")" == "name: new release" ]] || fail "the new tree is not in place"
ok "a pending migration: :previous tagged, images built, the predeploy backup, the migration in the one off container, then compose up, then the smoke"
grep -qx "db.sh backup --tag predeploy-0123456789ab in $WF/backups/deploys keep=unset" "$MIG/calls" ||
  fail "the predeploy backup was given a BACKUP_KEEP of its own: $(grep '^db.sh' "$MIG/calls")"
ok "T10: the predeploy backup names no retention of its own, so ops/db.sh applies the one of every backup (the Jetson's .env, then 7) in the predeploy folder"
out_has "   backup: $WF/backups/deploys/jobapp-2026-10-10T120000-predeploy.dump"
grep -q "predeploy backup: " "$WORK/out" && fail "a backup that warns of nothing left an annotation: $(cat "$WORK/out")"

# The review's N12: a rotation held by the size check ends its backup well, so the deploy is
# green. The line must not stay inside a folded group: it becomes an annotation of the run.
wf 0 "FAKE_PREDEPLOY_SAYS=   WARNING: held for 2 run(s), since jobapp-b.dump. This dump is 16 bytes, less than half of jobapp-a.dump (100 bytes)"
out_has "^::warning::predeploy backup: WARNING: held for 2 run(s), since jobapp-b.dump. This dump is 16 bytes, less than half of jobapp-a.dump (100 bytes)$"
[[ "$(cat "$WF/app/.deployed-commit")" == "$WF_COMMIT" ]] || fail "a warning of the predeploy backup stopped the deploy"
wf 0 "FAKE_PREDEPLOY_SAYS=   ERROR: held for 7 runs, since jobapp-b.dump. Nothing is deleted there"
out_has "^::error::predeploy backup: ERROR: held for 7 runs, since jobapp-b.dump. Nothing is deleted there$"
[[ "$(cat "$WF/app/.deployed-commit")" == "$WF_COMMIT" ]] || fail "an error line of the predeploy backup stopped the deploy"
ok "T10: a WARNING of the predeploy backup (a held rotation, a BACKUP_KEEP above 7) is a ::warning:: annotation of the run and an ERROR line an ::error:: one; the deploy goes on, since the backup itself ended well"

wf 1 FAKE_PENDING=1 FAKE_FAIL_PREDEPLOY=1
out_has "   backup: $WF/backups/deploys/jobapp-2026-10-10T120000-predeploy.dump"
out_has "   deleted old backup jobapp-2026-10-01T120000-predeploy.dump (9 days old, the limit is 7)"
out_has "db.sh: could not delete $WF/backups/deploys/jobapp-2026-10-02T120000-predeploy.dump"
out_has "::error::a deploy command failed: restoring the previous files and tags"
untouched "$MIG/calls"
[[ "$(wf_count " run --rm ")" == 0 ]] || fail "a migration ran after a predeploy backup that failed"
[[ "$(cat "$WF/app/compose.yml")" == "name: previous release" ]] || fail "the previous tree was not put back"
[[ "$(cat "$WF/app/.deployed-commit")" == "previous-commit" ]] || fail ".deployed-commit changed"
ok "T10: a predeploy backup that fails after its dump is written: what it had printed is in the job log with its error, the deploy stops before the migrations, the previous files are put back"

wf 0
out_has "already at head, nothing to apply"
[[ "$(wf_count " run --rm ")" == 1 ]] || fail "the no-op did not cost exactly one container"
[[ "$(cat "$WF/app/.deployed-commit")" == "$WF_COMMIT" ]] || fail ".deployed-commit not written"
ok "a database already at head: a no-op, the deploy goes on"

wf 1 FAKE_PENDING=1 FAKE_FAIL_MIGRATE=1
out_has "alembic upgrade head failed (exit 1, see above)"
out_has "::error::the migrations did not apply: the deploy stopped before starting the new code"
untouched "$MIG/calls"
[[ "$(wf_count "^smoke$")" == 0 ]] || fail "the smoke ran after a failed migration"
(($(wf_line "^db.sh backup") < $(wf_line " run --rm "))) || fail "no backup before the failed migration"
for img in jobapp-api jobapp-worker jobapp-caddy; do
  grep -qx "docker tag $img:previous $img:latest" "$MIG/calls" || fail "$img:latest was not put back: $(cat "$MIG/calls")"
done
[[ "$(cat "$WF/app/compose.yml")" == "name: previous release" ]] || fail "the previous tree was not put back"
[[ "$(cat "$WF/app/.deployed-commit")" == "previous-commit" ]] || fail ".deployed-commit changed"
ok "a failing migration: exit 1 before compose up, nothing stopped or restarted, the previous files and :latest tags back, .deployed-commit kept"

wf 1 FAKE_TIMEOUT=1
out_has "did not finish within 300s and were stopped"
untouched "$MIG/calls"
[[ "$(cat "$WF/app/compose.yml")" == "name: previous release" ]] || fail "the previous tree was not put back after a timeout"
ok "a migration that times out: the same stop, the stack untouched"

wf 1 FAKE_PENDING=1 FAKE_FAIL_UP=1
out_has "migrations applied: 1"
out_has "::error::compose up did not become healthy: rolling back to the previous files and images"
out_has "the schema stays migrated, it is never downgraded automatically"
out_has "database dump taken before this deploy: $WF/backups/deploys/jobapp-2026-10-10T120000-predeploy.dump"
[[ "$(wf_count " up -d --remove-orphans ")" == 2 ]] || fail "the rollback did not start the previous stack: $(cat "$MIG/calls")"
[[ "$(wf_count " run --rm ")" == 1 ]] || fail "the rollback ran alembic again: $(cat "$MIG/calls")"
grep -q "downgrade" "$MIG/calls" && fail "the rollback downgraded the schema: $(cat "$MIG/calls")"
restored="$(wf_line "tag jobapp-api:previous jobapp-api:latest")"
second_up="$(grep -n -- " up -d --remove-orphans " "$MIG/calls" | tail -n 1 | cut -d: -f1)"
((restored < second_up)) || fail "the previous images were not back before the second up"
[[ "$(cat "$WF/app/compose.yml")" == "name: previous release" ]] || fail "the previous tree was not put back"
[[ "$(cat "$WF/app/.deployed-commit")" == "previous-commit" ]] || fail ".deployed-commit changed"
[[ "$(wf_count "^smoke$")" == 0 ]] || fail "the smoke ran after a rollback"
ok "the migration applies, then compose up fails: the previous files and images are started again on the migrated schema, alembic is not run again, nothing downgrades, exit 1"

wf 1 FAKE_FAIL_SMOKE=1
[[ "$(wf_count " up -d --remove-orphans ")" == 1 ]] || fail "a failed smoke rolled back"
[[ "$(wf_count ":previous jobapp-api:latest")" == 0 ]] || fail "a failed smoke put the previous images back"
[[ "$(cat "$WF/app/compose.yml")" == "name: new release" ]] || fail "a failed smoke put the previous tree back"
[[ "$(cat "$WF/app/.deployed-commit")" == "previous-commit" ]] || fail ".deployed-commit written although the smoke failed"
ok "a smoke that fails once the new stack is up: red, no rollback, .deployed-commit kept (issue #16, unchanged)"

wf 0 FIRST_INSTALL=1 FAKE_NO_VOLUME=1 FAKE_DB_DOWN=1 FAKE_PENDING=10
out_has "no jobapp_pgdata volume: first start, nothing to back up"
out_has "db is not running: starting it"
out_has "migrations applied: 10"
started_db="$(wf_line " up -d --wait --wait-timeout 120 db")" migrated="$(wf_line " run --rm ")" upped="$(wf_line "^$WF_UP$")"
[[ -n "$started_db" && -n "$migrated" && -n "$upped" ]] || fail "a step is missing: $(cat "$MIG/calls")"
((started_db < migrated && migrated < upped)) || fail "first install order: db $started_db, migrate $migrated, up $upped"
[[ "$(wf_count "^db.sh backup")" == 0 ]] || fail "a backup ran without a database"
ok "first install on an empty database: db started alone, every migration applied, then the stack"

printf '\nAll %d checks passed.\n' "$passed"
