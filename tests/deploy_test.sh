#!/usr/bin/env bash
# Local tests of deploy.sh that need no Jetson (platform tasks 4 and 8). Rerunnable, touches
# nothing remote.
#
#   tests/deploy_test.sh
#
# Covers: shellcheck, the subcommand dispatcher, an unreachable host failing fast, the
# .rsyncignore rules (secrets and junk never shipped, a remote .env never deleted by --delete), and
# `url` against a fake ssh that plays the Jetson's docker (URL alone on stdout, logs read since the
# container's last start, waiting, stopped or missing cloudflared).
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
docker run --rm -v "$ROOT:/mnt:ro" -w /mnt "$SHELLCHECK_IMAGE" deploy.sh tests/deploy_test.sh tests/jetson_smoke.sh ||
  fail "shellcheck"
ok "deploy.sh, tests/deploy_test.sh, tests/jetson_smoke.sh clean"
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
for cmd in backup restore; do
  run 3 ./deploy.sh "$cmd"
  out_has "not yet implemented (task 9"
done
ok "backup, restore (task 9): not yet implemented, exit 3"
run 1 ./deploy.sh url extra
out_has "usage: ./deploy.sh url"
run 1 ./deploy.sh smoke --bogus
out_has "unknown option for smoke: --bogus"
ok "url takes no argument, smoke refuses an unknown option, both before any network call"
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
rsync -a --delete --exclude-from="$WORK/src/.rsyncignore" "$WORK/src/" "$dst/"
[[ "$(cat "$dst/.env")" == "POSTGRES_PASSWORD=remote-secret" ]] || fail "remote .env changed or deleted"
ok "remote .env kept byte for byte through --delete"
[[ ! -e "$dst/api/removed.py" ]] || fail "--delete left a stale file"
ok "stale remote file deleted"
[[ -f "$dst/api/app/main.py" && -f "$dst/compose.yml" && -f "$dst/café ünïcode.txt" ]] || fail "source files missing"
ok "code and unicode file names shipped"
for junk in .env.jetson web/node_modules api/.venv api/app/__pycache__ .git tasks/evidence web/dist .claude .DS_Store; do
  [[ ! -e "$dst/$junk" ]] || fail "$junk was shipped"
done
ok "never shipped: .env.jetson, node_modules, .venv, __pycache__, .git, evidence, dist, .claude, .DS_Store"
git check-ignore -q .env.jetson || fail ".env.jetson is not git-ignored"
ok ".env.jetson is git-ignored"

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
esac
exit 0
FAKE
chmod +x "$WORK/bin/ssh"
fake() { env PATH="$WORK/bin:$PATH" FAKE_CALLS="$WORK/calls" DEPLOY_HOST=fakejetson "$@"; }
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

printf '\nAll %d checks passed.\n' "$passed"
