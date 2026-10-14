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
cp "$ROOT/ops/agent-test.sh" "$ROOT/ops/agent_test_cap.py" "$REPO/ops/"
git -C "$REPO" init -q -b main
git -C "$REPO" -c user.name=t -c user.email=t@example.org add -A
git -C "$REPO" -c user.name=t -c user.email=t@example.org commit -q -m init
git -C "$REPO" worktree add -q -b feature/Some_Task "$WORK/wt" main
mkdir -p "$WORK/wt/api" "$WORK/wt/web"

# The fakes. CALLS holds one line per call; uv and pnpm also write their own niceness.
# What `nice -n 15` gives from here: 15 when this test runs at normal priority, as in the gate,
# and the system's ceiling when an agent runs it under nice itself.
NICE15="$(nice -n 15 sh -c 'ps -o nice= -p $$' | tr -d ' ')"
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
printf 'uv pythonpath=%s\n' "${PYTHONPATH:-}" >>"$CALLS"
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
  "--tx 4*popen" "--maxprocesses=4" "tests/x -q -n 3" "-n" "-n -1" \
  "-vn3" "-qn 4" "-xn4" "-vxn 3" "-qnauto" "-o addopts=-n3" "-oaddopts=-n3" "--override-ini=addopts=-n 3" \
  "--override-ini addopts=" "-p no:agent_test_cap" "-pno:agent_test_cap" "-vd" "-k"; do
  # shellcheck disable=SC2086
  run 64 $args
  calls_lack "docker run"
  calls_lack "uv "
done
for addopts in "-n 3" "-q -n4" "-vn 3" "--numprocesses=auto" "-o addopts=-n3" "--tx 3*popen"; do
  PYTEST_ADDOPTS="$addopts" run 64 tests
  out_has "PYTEST_ADDOPTS: "
  calls_lack "docker run"
done
ok "-n 3 and more, auto, logical, --tx, --maxprocesses and a bare -n are refused (exit 64), nothing started, whatever the spelling: clustered (-vn3, -qn 4), PYTEST_ADDOPTS, -o addopts"

for args in "-n 2" "-n2" "-n 1" "-n 0" "--numprocesses=2" "-n 02" "tests/jobs -q" "-vn2" "-qn 1" "-xvn2" \
  "-k n3 -m docker" "-kdn3" "-o log_level=INFO" "-p no:cacheprovider" "-W error -vn 2" "tests/x -- -n3"; do
  # shellcheck disable=SC2086
  run 0 $args
  calls_have "uv nice=$NICE15"
  calls_have "args=--native-tls run pytest -p agent_test_cap $args"
  calls_have "uv pythonpath=$WORK/wt/ops"
done
PYTEST_ADDOPTS="-q -n 2" run 0 tests
ok "-n 2 or fewer, and no -n, run pytest with the arguments as given and the worker cap plugin"

# The plugin reads what pytest resolved (here a stand-in for pytest and xdist's config.option).
python3 -I - "$ROOT/ops/agent_test_cap.py" <<'EOF' || fail "the worker cap plugin"
import importlib.util, sys, types
from types import SimpleNamespace as NS
fake = types.ModuleType("pytest")
fake.UsageError = type("UsageError", (Exception,), {})
fake.hookimpl = lambda **_: (lambda f: f)
fake.Config = object
sys.modules["pytest"] = fake
spec = importlib.util.spec_from_file_location("agent_test_cap", sys.argv[1])
cap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cap)
def refused(**option):
    try:
        cap.pytest_configure(NS(option=NS(**option)))
    except fake.UsageError:
        return True
    return False
for option in [dict(numprocesses=3, tx=["popen"] * 3), dict(numprocesses=None, tx=["3*popen"]),
               dict(numprocesses=None, tx=["popen"] * 3), dict(numprocesses="auto"), dict(numprocesses=8)]:
    assert refused(**option), option
for option in [dict(numprocesses=2, tx=["popen"] * 2), dict(numprocesses=0, tx=[]), dict(numprocesses=None, tx=None),
               dict(), dict(numprocesses=1, tx=["popen"]), dict(tx=["2*popen"])]:
    assert not refused(**option), option
EOF
ok "the plugin refuses more than two workers as pytest resolved them (numprocesses, tx), and lets two or fewer run"

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
calls_have "pnpm nice=$NICE15 pwd=$WORK/wt/web args=exec playwright test -c e2e/jobs-all.config.ts --workers=1"
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
