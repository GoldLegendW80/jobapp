#!/usr/bin/env bash
# Local tests of deploy.sh that need no Jetson (platform tasks 4, 8 and 9). Rerunnable, touches
# nothing remote.
#
#   tests/deploy_test.sh
#
# Covers: shellcheck, the subcommand dispatcher, an unreachable host failing fast, the
# .rsyncignore rules (secrets and junk never shipped, a remote .env never deleted by --delete), and
# `url` against a fake ssh that plays the Jetson's docker (URL alone on stdout, logs read since the
# container's last start, waiting, stopped or missing cloudflared), the backup and restore
# options refused before any network call, *.dump and backups/ kept out of git and rsync, and the
# typed confirmation of restore --into-live against a fake Jetson, `admin create` (argument
# checks before any network call; the CLI run in the api container with the tunnel URL, quoted),
# `admin unlock` (argument checks first; psql in the db container, values bound, count printed),
# and `jobs import` and `profile import` (argument checks; conversion on the Mac of a synthetic job
# folder, the document streamed to the CLI in the api container; a bad source never sent).
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
SCRIPTS=(deploy.sh ops/db.sh ops/runner/entrypoint.sh tests/deploy_test.sh tests/jetson_smoke.sh tests/backup_test.sh
  tests/crash_check.sh tests/reboot_check.sh)
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
for keep in 0 -1 abc 1.5 ""; do
  [[ -z "$keep" ]] && continue
  run 1 nohost env BACKUP_KEEP="$keep" ./deploy.sh backup
  out_has "BACKUP_KEEP must be a whole number of at least 1, got '$keep'"
done
run 1 nohost env BACKUP_DIR=jobapp/backups ./deploy.sh backup
out_has "must not be inside DEPLOY_DIR"
run 1 nohost env BACKUP_DIR='~/my backups' ./deploy.sh backups
out_has "BACKUP_DIR may only hold"
run 1 nohost env BACKUP_DIR=/ ./deploy.sh backup
out_has "BACKUP_DIR must name a directory"
ok "BACKUP_KEEP 0, -1, abc, 1.5 refused; BACKUP_DIR inside the deploy directory, with a space, or / refused"
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
grep -q 'token' <(sed -n '/^runner_install()/,/^}/p' deploy.sh | grep -E 'echo|info|printf') &&
  fail "runner_install prints something about the token"
ok "runner_install never prints the token"

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
# The fake answers the remote commands deploy.sh sends: the cloudflared container id, its state
# and start time, and its logs, from FAKE_* variables; it records every docker logs command.
mkdir -p "$WORK/bin"
cat >"$WORK/bin/ssh" <<'FAKE'
#!/usr/bin/env bash
cmd="${*: -1}"
case "$cmd" in
  *"ps -a -q cloudflared"*) [[ -z "${FAKE_CF_ID:-}" ]] || echo "$FAKE_CF_ID" ;;
  *"docker inspect"*) echo "${FAKE_CF_STATE:-running} ${FAKE_CF_STARTED:-2026-10-08T12:34:54.123456789Z}" ;;
  *"docker logs"*)
    echo "$cmd" >>"$FAKE_CALLS"
    cat "$FAKE_CF_LOG"
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
    echo "  https://gathering-saves-below-twelve.trycloudflare.com/invite/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
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
cat >"$WORK/cf.log" <<'LOG'
2026-10-08T12:34:54Z INF Requesting new quick Tunnel on trycloudflare.com...
2026-10-08T12:34:54Z INF Requesting https://api.trycloudflare.com/tunnel
2026-10-08T12:34:59Z INF +--------------------------------------------------------------------------------------------+
2026-10-08T12:34:59Z INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |
2026-10-08T12:34:59Z INF |  https://gathering-saves-below-twelve.trycloudflare.com                                    |
2026-10-08T12:34:59Z INF +--------------------------------------------------------------------------------------------+
2026-10-08T12:34:59Z INF Settings: map[ha-connections:1 metrics:127.0.0.1:20241 no-autoupdate:true url:http://caddy:8080]
2026-10-08T12:35:00Z INF Registered tunnel connection connIndex=0 location=cdg12 protocol=quic ✓
LOG
: >"$WORK/calls"
run_split 0 fake FAKE_CF_ID=abc123 FAKE_CF_LOG="$WORK/cf.log" ./deploy.sh url
[[ "$(cat "$WORK/stdout")" == "https://gathering-saves-below-twelve.trycloudflare.com" ]] ||
  fail "stdout is not exactly the URL: $(cat "$WORK/stdout")"
[[ "$(wc -l <"$WORK/stdout" | tr -d ' ')" == "1" ]] || fail "stdout has more than one line"
url="$(fake FAKE_CF_ID=abc123 FAKE_CF_LOG="$WORK/cf.log" ./deploy.sh url 2>/dev/null)"
[[ "$url" == "https://gathering-saves-below-twelve.trycloudflare.com" ]] || fail "\$(./deploy.sh url) gave '$url'"
ok "stdout is the URL alone, one line, so \$(./deploy.sh url) works; api.trycloudflare.com ignored"
grep -q -- "docker logs --since 2026-10-08T12:34:54.123456789Z abc123" "$WORK/calls" ||
  fail "logs not read since the container start: $(cat "$WORK/calls")"
ok "logs read with --since the container's StartedAt, so a stale URL of a previous start is never seen"

# Two URLs in the window (a restart between StartedAt and the read): the newest wins.
printf '%s\n' "INF |  https://old-name-one.trycloudflare.com  |" "INF |  https://new-name-two.trycloudflare.com  |" >"$WORK/two.log"
run_split 0 fake FAKE_CF_ID=abc123 FAKE_CF_LOG="$WORK/two.log" ./deploy.sh url
[[ "$(cat "$WORK/stdout")" == "https://new-name-two.trycloudflare.com" ]] || fail "not the newest: $(cat "$WORK/stdout")"
ok "the last URL logged wins"

: >"$WORK/empty.log"
start=$SECONDS
run_split 1 fake FAKE_CF_ID=abc123 FAKE_CF_LOG="$WORK/empty.log" DEPLOY_URL_TIMEOUT=3 ./deploy.sh url
[[ ! -s "$WORK/stdout" ]] || fail "stdout not empty on failure: $(cat "$WORK/stdout")"
grep -q "waiting up to 3s" "$WORK/stderr" || fail "no waiting note on stderr"
grep -q "logged no trycloudflare URL within 3s" "$WORK/stderr" || fail "timeout message: $(cat "$WORK/stderr")"
((SECONDS - start >= 3 && SECONDS - start <= 10)) || fail "timeout took $((SECONDS - start))s"
ok "no URL yet: waits, then exit 1 after the timeout, message on stderr, stdout empty"

printf 'INF |  https://evil.trycloudflare.com.attacker.example  |\nINF https://trycloudflare.com/\n' >"$WORK/bad.log"
run_split 1 fake FAKE_CF_ID=abc123 FAKE_CF_LOG="$WORK/bad.log" DEPLOY_URL_TIMEOUT=1 ./deploy.sh url
[[ ! -s "$WORK/stdout" ]] || fail "accepted a lookalike URL: $(cat "$WORK/stdout")"
ok "lookalike hosts and the bare domain are not taken for the tunnel URL"

run_split 1 fake FAKE_CF_ID=abc123 FAKE_CF_STATE=exited FAKE_CF_LOG="$WORK/cf.log" ./deploy.sh url
grep -q "cloudflared is exited on fakejetson, not running" "$WORK/stderr" || fail "exited: $(cat "$WORK/stderr")"
[[ ! -s "$WORK/stdout" ]] || fail "stdout not empty"
run_split 1 fake FAKE_CF_ID= FAKE_CF_LOG="$WORK/cf.log" ./deploy.sh url
grep -q "cloudflared is not running on fakejetson: no container" "$WORK/stderr" || fail "missing: $(cat "$WORK/stderr")"
ok "cloudflared stopped or missing: exit 1 with a clear message, stdout empty"

step "admin create against the fake Jetson"
: >"$WORK/calls"
run_split 0 fake FAKE_CF_ID=abc123 FAKE_CF_LOG="$WORK/cf.log" ./deploy.sh admin create Zoe@Example.org
grep -q "https://gathering-saves-below-twelve.trycloudflare.com/invite/A\{43\}$" "$WORK/stdout" ||
  fail "full link not on stdout: $(cat "$WORK/stdout")"
grep -qF -- "docker compose -f compose.yml --profile tunnel exec -T api python -m app.identity.cli create-admin Zoe@Example.org --base-url https://gathering-saves-below-twelve.trycloudflare.com" "$WORK/calls" ||
  fail "remote command: $(cat "$WORK/calls")"
ok "admin create runs the CLI in the api container with --base-url set to the tunnel URL, the full link on stdout"
: >"$WORK/calls"
run_split 0 fake FAKE_CF_ID=abc123 FAKE_CF_LOG="$WORK/cf.log" ./deploy.sh admin create "o'brien+\$(id)@example.org"
grep -qF -- "create-admin o\'brien+\\\$\(id\)@example.org --base-url" "$WORK/calls" ||
  fail "email not quoted for the remote shell: $(cat "$WORK/calls")"
ok "quotes, \$ and parentheses in the email reach the CLI quoted, never run by the remote shell"
: >"$WORK/calls"
run_split 1 fake FAKE_CF_ID= FAKE_CF_LOG="$WORK/cf.log" ./deploy.sh admin create zoe@example.org
grep -q "cloudflared is not running on fakejetson" "$WORK/stderr" || fail "no tunnel: $(cat "$WORK/stderr")"
grep -q "exec -T api" "$WORK/calls" && fail "the CLI ran without a public URL"
ok "no tunnel URL: exit 1 before the CLI runs"
run_split 2 fake FAKE_CF_ID=abc123 FAKE_CF_LOG="$WORK/cf.log" FAKE_CLI_EXIT=2 ./deploy.sh admin create zoe@example.org
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
fake2() { env PATH="$WORK/bin2:$PATH" FAKE_CALLS="$WORK/calls2" DEPLOY_HOST=fakejetson "$@"; }
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
grep -q -- "BACKUP_DIR=jobapp-backups BACKUP_KEEP=14 bash -s -- restore-live /home/u/" "$WORK/calls2" ||
  fail "settings not passed: $(cat "$WORK/calls2")"
ok "--yes skips the prompt without a terminal; BACKUP_DIR and BACKUP_KEEP reach ops/db.sh"
: >"$WORK/calls2"
# The fake prints no backup=<path> line, so the run must fail rather than claim a backup.
run 1 fake2 env BACKUP_KEEP=3 ./deploy.sh backup --pull
grep -q -- "BACKUP_KEEP=3 bash -s -- backup" "$WORK/calls2" || fail "BACKUP_KEEP not passed: $(cat "$WORK/calls2")"
out_has "ops/db.sh reported no backup file"
ok "backup passes BACKUP_KEEP, and fails when ops/db.sh reports no file"

printf '\nAll %d checks passed.\n' "$passed"
