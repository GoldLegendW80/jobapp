#!/usr/bin/env bash
# Local tests of deploy.sh that need no Jetson (platform task 4). Rerunnable, touches nothing remote.
#
#   tests/deploy_test.sh
#
# Covers: shellcheck, the subcommand dispatcher, an unreachable host failing fast, and the
# .rsyncignore rules (secrets and junk never shipped, a remote .env never deleted by --delete).
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
for pair in "url:task 8" "smoke:task 8" "backup:task 9" "restore:task 9"; do
  run 3 ./deploy.sh "${pair%%:*}"
  out_has "not yet implemented (${pair#*:}"
done
ok "url, smoke (task 8), backup, restore (task 9): not yet implemented, exit 3"
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
ok "logs and ps fail the same way"

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

printf '\nAll %d checks passed.\n' "$passed"
