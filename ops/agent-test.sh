#!/usr/bin/env bash
# The one way an agent runs tests on this Mac, beside the merge gate (issue #280). The gate
# (ops/local-check.sh) keeps normal priority and must win; what an agent runs here yields to it.
#
#   ops/agent-test.sh [--name <caller>] <pytest arguments ...>
#   ops/agent-test.sh [--name <caller>] --playwright <Playwright arguments ...>
#
# pytest. A throwaway Postgres 16 and Redis, the images and settings of the gate's api-services
# step (no fsync), each capped with `docker run --cpus 1`, named agent-<caller>-<process id>-pg
# and -redis, published on free loopback ports and removed on exit, on failure and on Ctrl+C (a
# trap). Then, in api/, `nice -n 15 uv run pytest <arguments>` with TEST_DATABASE_URL and
# REDIS_URL set to them. At most `-n 2`: `-n 3` and more, `-n auto` and `-n logical` are refused.
# No -n means one process.
#
# Playwright. `nice -n 15 pnpm exec playwright test <arguments> --workers=1` in web/, against the
# stack the caller started (E2E_CADDY_URL, as the gate's s_journey sets it). Refused while the
# gate holds its lock (.local-check/lock of the main checkout), naming the holder: a journey
# group beside the gate's four stacks is what made it fail.
#
# The caller's name is --name, else AGENT_TEST_NAME, else the branch of this checkout; only
# [a-z0-9-] is kept. Exit status: pytest's or Playwright's own, 3 refused because the gate runs,
# 4 Docker or a tool missing, or the services did not start, 64 wrong arguments, 130 interrupted.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROG="ops/agent-test.sh"
# The images of the gate's s_api_services (tests/agent_test_test.sh checks they stay the same).
PG_IMAGE="postgres:16.15-alpine"
REDIS_IMAGE="redis:7.4.11-alpine"
MAX_PYTEST_JOBS=2
NICENESS=15

usage_error() {
  printf '%s: %s\n' "$PROG" "$*" >&2
  printf 'usage: %s [--name <caller>] <pytest arguments ...> | --playwright <Playwright arguments ...>\n' "$PROG" >&2
  exit 64
}

NAME="${AGENT_TEST_NAME:-}"
MODE=pytest
while (($#)); do
  case "$1" in
  --name)
    [[ $# -ge 2 && -n "$2" ]] || usage_error "--name needs a name"
    NAME="$2"
    shift 2
    ;;
  --playwright)
    MODE=playwright
    shift
    break
    ;;
  *) break ;;
  esac
done
ARGS=("$@")

if [[ -z "$NAME" ]]; then NAME="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo agent)"; fi
NAME="$(printf '%s' "$NAME" | LC_ALL=C tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9-' '-' | sed 's/--*/-/g; s/^-//; s/-$//' | cut -c1-40)"
[[ -n "$NAME" ]] || NAME=agent
PREFIX="agent-$NAME-$$"

# --- the arguments ---------------------------------------------------------------------------------
check_jobs() { # check_jobs <value of -n>: refuses what is above MAX_PYTEST_JOBS or not a number
  [[ "$1" =~ ^[0-9]+$ ]] || usage_error "-n $1: give a number, at most $MAX_PYTEST_JOBS (the gate may run beside you)"
  ((10#$1 <= MAX_PYTEST_JOBS)) || usage_error "-n $1: at most -n $MAX_PYTEST_JOBS beside the gate"
}

check_pytest_args() {
  local i=0 arg
  while ((i < ${#ARGS[@]})); do
    arg="${ARGS[$i]}"
    case "$arg" in
    -n | --numprocesses)
      ((i + 1 < ${#ARGS[@]})) || usage_error "$arg needs a number"
      check_jobs "${ARGS[$((i + 1))]}"
      i=$((i + 1))
      ;;
    --numprocesses=*) check_jobs "${arg#*=}" ;;
    -n*) check_jobs "${arg#-n}" ;;
    # Other ways to start more processes than -n says.
    --tx* | --maxprocesses* | -d) usage_error "$arg: the number of processes is set with -n only (at most $MAX_PYTEST_JOBS)" ;;
    esac
    i=$((i + 1))
  done
}

check_playwright_args() {
  local i=0 arg
  while ((i < ${#ARGS[@]})); do
    arg="${ARGS[$i]}"
    case "$arg" in
    -j | --workers)
      ((i + 1 < ${#ARGS[@]})) || usage_error "$arg needs a number"
      [[ "${ARGS[$((i + 1))]}" == 1 ]] || usage_error "$arg ${ARGS[$((i + 1))]}: Playwright runs with one worker here"
      i=$((i + 1))
      ;;
    --workers=*) [[ "${arg#*=}" == 1 ]] || usage_error "$arg: Playwright runs with one worker here" ;;
    -j*) [[ "${arg#-j}" == 1 ]] || usage_error "$arg: Playwright runs with one worker here" ;;
    esac
    i=$((i + 1))
  done
}

# The gate's lock lives in .local-check/ of the main checkout, whichever worktree calls.
gate_lock() {
  local common
  common="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || {
    printf '%s\n' "$ROOT/.local-check/lock"
    return 0
  }
  printf '%s\n' "$(dirname "$common")/.local-check/lock"
}

# --- Playwright --------------------------------------------------------------------------------------
if [[ "$MODE" == playwright ]]; then
  check_playwright_args
  lock="$(gate_lock)"
  if [[ -d "$lock" ]]; then
    printf '%s: the merge gate is running (%s). No Playwright beside it: run again when %s is gone.\n' \
      "$PROG" "$(cat "$lock/holder" 2>/dev/null || echo "holder unknown")" "$lock" >&2
    exit 3
  fi
  command -v pnpm >/dev/null || {
    printf '%s: pnpm is not installed\n' "$PROG" >&2
    exit 4
  }
  cd "$ROOT/web"
  exec nice -n "$NICENESS" pnpm exec playwright test ${ARGS[@]+"${ARGS[@]}"} --workers=1
fi

# --- pytest ------------------------------------------------------------------------------------------
check_pytest_args
for tool in docker uv python3; do
  command -v "$tool" >/dev/null || {
    printf '%s: %s is not installed\n' "$PROG" "$tool" >&2
    exit 4
  }
done

STARTED=()
CHILD=""
# shellcheck disable=SC2329 # called by the trap
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  set +e
  if ((${#STARTED[@]})); then docker rm -f -v "${STARTED[@]}" >/dev/null 2>&1; fi
  exit "$rc"
}
# On Ctrl+C or a TERM: SIGINT to pytest, so its fixtures remove what they started, and the wait
# for it; then the trap above removes the services.
# shellcheck disable=SC2329 # called by the trap
interrupted() {
  printf '\n%s: interrupted, removing %s-*\n' "$PROG" "$PREFIX" >&2
  if [[ -n "$CHILD" ]]; then
    kill -INT "$CHILD" 2>/dev/null || true
    wait "$CHILD" 2>/dev/null || true
  fi
  exit 130
}
trap cleanup EXIT
trap interrupted INT TERM

start() { # start <name> <docker run arguments ...>: one service container, remembered for the trap
  local name="$1"
  shift
  STARTED+=("$name")
  docker run -d --rm --name "$name" --label "jobapp.agent-test=$NAME" --cpus 1 "$@" >/dev/null || {
    printf '%s: %s did not start\n' "$PROG" "$name" >&2
    exit 4
  }
}

start "$PREFIX-pg" -e POSTGRES_USER=jobapp -e POSTGRES_PASSWORD=ci-only-not-a-secret -e POSTGRES_DB=jobapp_test \
  -p 127.0.0.1::5432 "$PG_IMAGE" -c fsync=off -c synchronous_commit=off -c full_page_writes=off
start "$PREFIX-redis" -p 127.0.0.1::6379 "$REDIS_IMAGE"
READY_S="${AGENT_TEST_READY_S:-60}"
[[ "$READY_S" =~ ^[1-9][0-9]*$ ]] || usage_error "AGENT_TEST_READY_S must be a number of seconds"
ready=0
for _ in $(seq 1 "$READY_S"); do
  if docker exec "$PREFIX-pg" psql -U jobapp -d jobapp_test -h 127.0.0.1 -c 'SELECT 1' >/dev/null 2>&1 &&
    docker exec "$PREFIX-redis" redis-cli ping >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
((ready)) || {
  printf '%s: Postgres or Redis did not answer in %s s\n' "$PROG" "$READY_S" >&2
  exit 4
}
pg_port="$(docker port "$PREFIX-pg" 5432/tcp | sed -n '1s/.*://p')"
redis_port="$(docker port "$PREFIX-redis" 6379/tcp | sed -n '1s/.*://p')"
export TEST_DATABASE_URL="postgresql+psycopg://jobapp:ci-only-not-a-secret@127.0.0.1:$pg_port/jobapp_test"
export REDIS_URL="redis://127.0.0.1:$redis_port/0"
printf '%s: %s-pg on 127.0.0.1:%s, %s-redis on 127.0.0.1:%s, pytest under nice -n %s\n' \
  "$PROG" "$PREFIX" "$pg_port" "$PREFIX" "$redis_port" "$NICENESS"

cd "$ROOT/api"
# In the background, so a signal reaches the trap at once and not when pytest ends; a background
# job starts with SIGINT ignored, so the default is given back first (as the gate's interruptible).
python3 -c 'import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.execvp(sys.argv[1], sys.argv[1:])' \
  nice -n "$NICENESS" uv --native-tls run pytest ${ARGS[@]+"${ARGS[@]}"} &
CHILD=$!
rc=0
wait "$CHILD" || rc=$?
CHILD=""
exit "$rc"
