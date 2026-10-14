#!/usr/bin/env bash
# Tests of ops/agent-test.sh (issue #280), the way agents run tests beside the merge gate: the
# limits on -n and on Playwright workers, the refusal of Playwright while the gate holds its lock,
# the throwaway services (--cpus 1, the gate's images, removed on every exit), nice on what runs.
# docker, uv and pnpm are fakes on PATH that write what they were asked into a log: no container
# starts, no test runs, nothing leaves this machine.
#
#   tests/agent_test_test.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
WORK="$(cd "$WORK" && pwd -P)"
trap 'rm -rf "$WORK"' EXIT

passed=0
ok() {
  passed=$((passed + 1))
  printf 'ok   %s\n' "$*"
}
fail() {
  printf 'FAIL %s\n' "$*" >&2
  exit 1
}

# A throwaway repository that holds the script, with a worktree: the lock is the main checkout's.
REPO="$WORK/repo"
mkdir -p "$REPO/ops" "$REPO/api" "$REPO/web"
cp "$ROOT/ops/agent-test.sh" "$REPO/ops/"
git -C "$REPO" init -q -b main
git -C "$REPO" -c user.name=t -c user.email=t@example.org add -A
git -C "$REPO" -c user.name=t -c user.email=t@example.org commit -q -m init
git -C "$REPO" worktree add -q -b feature/Some_Task "$WORK/wt" main
mkdir -p "$WORK/wt/api" "$WORK/wt/web"

# The fakes. CALLS holds one line per call; uv and pnpm also write their own niceness.
BIN="$WORK/bin"
mkdir -p "$BIN"
CALLS="$WORK/calls"
cat >"$BIN/docker" <<'EOF'
#!/usr/bin/env bash
printf 'docker %s\n' "$*" >>"$CALLS"
case "$1" in
run) [[ -z "${FAKE_RUN_FAILS:-}" || "$*" != *"$FAKE_RUN_FAILS"* ]] || exit 1; echo 0123456789ab ;;
exec) [[ -z "${FAKE_NEVER_READY:-}" ]] || exit 1 ;;
port) [[ "$*" == *5432* ]] && echo 127.0.0.1:55432 || echo 127.0.0.1:56379 ;;
esac
exit 0
EOF
cat >"$BIN/uv" <<'EOF'
#!/usr/bin/env bash
printf 'uv nice=%s pwd=%s db=%s redis=%s args=%s\n' "$(ps -o nice= -p $$ | tr -d ' ')" "$PWD" "$TEST_DATABASE_URL" "$REDIS_URL" "$*" >>"$CALLS"
if [[ -n "${FAKE_PYTEST_WAITS:-}" ]]; then
  echo $$ >"$FAKE_PYTEST_WAITS"
  exec sleep 30 # ended by the SIGINT the script forwards
fi
exit "${FAKE_PYTEST_RC:-0}"
EOF
cat >"$BIN/pnpm" <<'EOF'
#!/usr/bin/env bash
printf 'pnpm nice=%s pwd=%s args=%s\n' "$(ps -o nice= -p $$ | tr -d ' ')" "$PWD" "$*" >>"$CALLS"
EOF
chmod +x "$BIN"/*
export PATH="$BIN:$PATH" CALLS WORK
unset AGENT_TEST_NAME FAKE_RUN_FAILS FAKE_NEVER_READY FAKE_PYTEST_RC FAKE_PYTEST_WAITS

SCRIPT="$WORK/wt/ops/agent-test.sh"
# run <expected exit> <arguments ...>: the script from the worktree, output in $WORK/out
run() {
  local want="$1" got=0
  shift
  : >"$CALLS"
  "$SCRIPT" "$@" >"$WORK/out" 2>&1 || got=$?
  [[ "$got" == "$want" ]] || fail "agent-test.sh $* exited $got, expected $want: $(head -c 600 "$WORK/out")"
}
calls_have() { grep -qF -- "$1" "$CALLS" || fail "no call has '$1': $(cat "$CALLS")"; }
calls_lack() { if grep -qF -- "$1" "$CALLS"; then fail "a call has '$1': $(cat "$CALLS")"; fi; }
out_has() { grep -qF -- "$1" "$WORK/out" || fail "output lacks '$1': $(cat "$WORK/out")"; }
removed_both() { # the two containers of the last run were removed, by name
  local line
  line="$(grep '^docker rm ' "$CALLS" || true)"
  [[ "$line" == *"-pg"* && "$line" == *"-redis"* ]] || fail "not both containers removed: $(cat "$CALLS")"
}

# --- -n above 2 is refused before any container starts --------------------------------------------
for args in "-n 3" "-n 4" "-n4" "-n 12" "--numprocesses 3" "--numprocesses=8" "-n auto" "-n logical" "-nauto" \
  "--tx 4*popen" "--maxprocesses=4" "tests/x -q -n 3" "-n" "-n -1"; do
  # shellcheck disable=SC2086
  run 64 $args
  calls_lack "docker run"
  calls_lack "uv "
done
ok "-n 3 and more, auto, logical, --tx, --maxprocesses and a bare -n are refused (exit 64), nothing started"

for args in "-n 2" "-n2" "-n 1" "-n 0" "--numprocesses=2" "-n 02" "tests/jobs -q"; do
  # shellcheck disable=SC2086
  run 0 $args
  calls_have "uv nice=15"
  calls_have "args=--native-tls run pytest $args"
done
ok "-n 2 or fewer, and no -n, run pytest with the arguments as given"

# --- the services: the gate's images, --cpus 1, the caller's name, removed on exit -------------------
run 0 --name "Brave_Task.7" tests/jobs -n 2
calls_have "--name agent-brave-task-7-"
[[ "$(grep -c -- '--cpus 1' "$CALLS")" == 2 ]] || fail "not both containers capped at one CPU: $(cat "$CALLS")"
calls_have "--label jobapp.agent-test=brave-task-7"
images="$(grep -oE '(postgres|redis):[0-9.]+-alpine' "$ROOT/ops/local-check.sh" | sort -u)"
[[ "$(wc -l <<<"$images" | tr -d ' ')" == 2 ]] || fail "the gate's api-services images were not found: $images"
for image in $images; do
  calls_have " $image"
done
calls_have "-c fsync=off"
calls_have "db=postgresql+psycopg://jobapp:ci-only-not-a-secret@127.0.0.1:55432/jobapp_test"
calls_have "redis=redis://127.0.0.1:56379/0"
calls_have "pwd=$WORK/wt/api "
removed_both
ok "Postgres and Redis: the gate's images and settings, --cpus 1, named after the caller, then removed"

AGENT_TEST_NAME=from-env run 0 -q
calls_have "--name agent-from-env-"
run 0 -q
calls_have "--name agent-feature-some-task-"
ok "the caller's name: --name, else AGENT_TEST_NAME, else the branch"

FAKE_PYTEST_RC=1 run 1 tests
removed_both
FAKE_PYTEST_RC=5 run 5 tests
removed_both
ok "pytest's exit status is kept and the containers removed when it fails"

FAKE_RUN_FAILS=redis run 4 tests
calls_lack "uv "
removed_both
FAKE_NEVER_READY=1 AGENT_TEST_READY_S=2 run 4 tests
out_has "did not answer in 2 s"
calls_lack "uv "
removed_both
ok "a service that does not start or never answers: exit 4, pytest not run, both removed"

# Interrupted while pytest runs: the trap removes the containers and the status says so.
: >"$CALLS"
rm -f "$WORK/started"
FAKE_PYTEST_WAITS="$WORK/started" "$SCRIPT" tests >"$WORK/out" 2>&1 &
pid=$!
for _ in $(seq 1 100); do
  [[ -f "$WORK/started" ]] && break
  sleep 0.1
done
[[ -f "$WORK/started" ]] || fail "the fake pytest never started"
kill -TERM "$pid"
got=0
wait "$pid" || got=$?
[[ "$got" == 130 ]] || fail "interrupted: exit $got, expected 130"
removed_both
if kill -0 "$(cat "$WORK/started")" 2>/dev/null; then fail "the fake pytest outlived the script"; fi
ok "interrupted (SIGTERM) during pytest: exit 130 and both containers removed"

# --- Playwright --------------------------------------------------------------------------------------
run 0 --playwright -c e2e/jobs-all.config.ts
calls_have "pnpm nice=15 pwd=$WORK/wt/web args=exec playwright test -c e2e/jobs-all.config.ts --workers=1"
calls_lack "docker"
run 0 --playwright jobs.spec.ts --workers 1
run 0 --playwright jobs.spec.ts -j1
for args in "--workers 2" "--workers=4" "-j 2" "-j3" "--workers=50%" "--workers"; do
  # shellcheck disable=SC2086
  run 64 --playwright jobs.spec.ts $args
  calls_lack "pnpm"
done
ok "Playwright: nice -n 15, one worker, more than one refused"

mkdir -p "$REPO/.local-check/lock"
printf 'process 4242, check lc1014000000-4242, started 2026-10-14 10:00:00, base origin/main' >"$REPO/.local-check/lock/holder"
run 3 --playwright -c e2e/jobs-all.config.ts
calls_lack "pnpm"
out_has "process 4242, check lc1014000000-4242"
run 0 tests/jobs -n 2
ok "while the gate holds its lock (seen from a worktree): Playwright refused naming the holder, pytest runs niced"
rm -rf "$REPO/.local-check/lock"

printf '\n%s passed\n' "$passed"
