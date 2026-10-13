#!/usr/bin/env bash
# The batch check: every check the CI jobs api, checks and journeys ran, natively on this Mac, on
# the tree that a batch of approved pull requests produces (issue #116, owner's decision of
# 2026-10-13: no CI run per pull request any more, CONTRIBUTING.md, Merging).
#
#   ops/local-check.sh                                  origin/main as it is
#   ops/local-check.sh --merge 121 --merge 118          origin/main, then pull request 121, then 118
#   ops/local-check.sh --merge origin/platform/x        a branch or a commit instead of a number
#   ops/local-check.sh --base <ref> --only api          another base; one CI job only (no record)
#   ops/local-check.sh --jobs 6                         pytest and vitest processes (default: cores - 2)
#
# What it does. After a fetch, a throwaway detached worktree of --base under a temporary folder;
# each --merge ref merged there in the order given (git merge --no-ff --no-edit); then, in that
# tree, the FULL set, whatever the batch touches (the path plan of .github/scripts/ci_plan.py
# narrowed a pull request's run; a batch check does not narrow):
#
#   api       uv sync, ruff, the whole pytest suite on -n <jobs> with the coverage floor of 95,
#             the docker marked tests (the parser in its container, their own Postgres), the
#             scoring timing test alone. The migration rules are part of the suite
#             (api/tests/test_migration_rules.py, test_migrations.py).
#   checks    pre-commit on all files, the secret scan of the commits between base and the merged
#             head (.github/scripts/secret_scan.sh through its pre-commit hook, as CI called it),
#             the shell lint of the files ci.yml lists, the e2e groups and CI plan tests, every
#             tests/*_test.sh that ci.yml runs; the journey groups apps and ingestion.
#   journeys  the web lint, type check, vitest and build; the journey groups identity and sweep.
#
# The lists that ci.yml holds (the files of its Shellcheck step, the tests/*_test.sh steps) are read from
# ci.yml, so a step added there runs here. The four journey groups are written below, and
# tests/local_check_test.sh fails when their spec files differ from those of ci.yml.
#
# Arrangement. Four lanes start together: api (its own Postgres and Redis containers on free
# loopback ports; ruff, the suite, the coverage floor), web (install, then lint, type check
# and build side by side), images (api, worker and caddy built by compose under this
# check's own image names), checks (the tests/*_test.sh side by side). The journey groups start
# when the images and the web install are done, each on a scratch stack of its own (own project,
# own port, own database), LOCAL_CHECK_STACKS of them side by side (default: one per 4 GiB of
# Docker memory, at most 4), with one Playwright worker each, as in CI. When every lane has
# ended, the tests that measure time run alone, one after the other: Vitest, the docker marked
# tests, the scoring test.
#
# Safety of this machine:
# - one check at a time: a lock in .local-check/ of the main checkout; a second call says who
#   holds it and exits 3;
# - under 12 GiB free on /System/Volumes/Data it refuses to start (LOCAL_CHECK_MIN_FREE_GB);
# - every container, volume, network and image it makes is named jobapp-<check id>-..., and a
#   trap removes them, the temporary worktree and the lock on success, on failure and on Ctrl+C;
# - it never touches a container it did not start (never jobapp-ci-*, never another project) and
#   never builds or retags the shared jobapp-*:latest images.
#
# Output: one line per step (name, PASS or FAIL, seconds), the last 40 lines of the log of each
# failed step, the folder that keeps the full logs, then as the last line
#   LOCAL CHECK: PASS <tree sha> <seconds>s      or      LOCAL CHECK: FAIL <tree sha> <failed steps>
# On a PASS of the full set, a record .local-check/<tree sha>.json in the main checkout: the
# tree, the base, the merged refs with their commits, the steps and their seconds, the versions of
# the tools and the date. The tree sha is the key (git rev-parse HEAD^{tree} of what was checked):
# once the pull requests are merged on GitHub in the same order, git rev-parse origin/main^{tree}
# equals it exactly when what was checked is what is on main. ops/deploy-checked.sh deploys only a
# commit whose tree has such a record.
#
# Exit status: 0 passed, 1 a check failed, 2 a merge conflict (a result, not a crash), 3 the lock
# or the disk, 4 the check could not be prepared (no fetch, a tool missing), 64 wrong arguments,
# 130 interrupted.
#
# Not run here: tests/compose_smoke.sh. CI never ran it (it publishes 127.0.0.1:8000 and :5432,
# the ports of a developer's own stack); it stays a test run by hand.
#
# For tests/local_check_test.sh only: LOCAL_CHECK_STUB names a file that defines stub_plan, run
# in place of the real steps; the record then says "stub": true and deploy-checked.sh refuses it.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROG="ops/local-check.sh"

BASE="origin/main"
MERGES=()
ONLY=()
JOBS=""
MIN_FREE_GB="${LOCAL_CHECK_MIN_FREE_GB:-12}"
STEP_TIMEOUT="${LOCAL_CHECK_STEP_TIMEOUT:-1200}"
KEEP_LOGS=10
INT_GRACE="${LOCAL_CHECK_INT_GRACE:-8}"

usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; }
usage_error() {
  printf '%s: %s\n' "$PROG" "$*" >&2
  printf 'usage: %s [--base <ref>] [--merge <ref or pull request number> ...] [--only api|checks|journeys ...] [--jobs N]\n' "$PROG" >&2
  exit 64
}
say() { printf '%s\n' "$*"; }
fail_prepare() {
  printf '%s: %s\n' "$PROG" "$*" >&2
  exit 4
}

while (($#)); do
  case "$1" in
  --base)
    [[ $# -ge 2 && -n "$2" ]] || usage_error "--base needs a ref"
    BASE="$2"
    shift 2
    ;;
  --merge)
    [[ $# -ge 2 && -n "$2" ]] || usage_error "--merge needs a ref or a pull request number"
    MERGES+=("$2")
    shift 2
    ;;
  --only)
    [[ $# -ge 2 ]] || usage_error "--only needs api, checks or journeys"
    case "$2" in
    api | checks | journeys) ONLY+=("$2") ;;
    *) usage_error "--only takes api, checks or journeys, not '$2'" ;;
    esac
    shift 2
    ;;
  --jobs)
    [[ $# -ge 2 && "$2" =~ ^[1-9][0-9]*$ ]] || usage_error "--jobs needs a number, 1 or more"
    JOBS="$2"
    shift 2
    ;;
  -h | --help)
    usage
    exit 0
    ;;
  *) usage_error "unknown argument: $1" ;;
  esac
done
[[ "$MIN_FREE_GB" =~ ^[0-9]+$ ]] || usage_error "LOCAL_CHECK_MIN_FREE_GB must be a number"
[[ "$INT_GRACE" =~ ^[0-9]+$ ]] || usage_error "LOCAL_CHECK_INT_GRACE must be a number of seconds"
[[ "$STEP_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || usage_error "LOCAL_CHECK_STEP_TIMEOUT must be a number of seconds"

wanted() { # wanted <api|checks|journeys>: true when that CI job is part of this run
  local one
  ((${#ONLY[@]})) || return 0
  for one in "${ONLY[@]}"; do [[ "$one" == "$1" ]] && return 0; done
  return 1
}

command -v git >/dev/null || fail_prepare "git is not installed"
COMMON="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" ||
  fail_prepare "$ROOT is not in a git repository"
MAIN="$(dirname "$COMMON")"
RECORDS="$MAIN/.local-check"
LOCK="$RECORDS/lock"
CHECK_ID="lc$(date +%m%d%H%M%S)-$$"
PREFIX="jobapp-$CHECK_ID"
STARTED=$SECONDS
WORK=""
TREE=""
LOGS=""
HAVE_LOCK=0

# --- cleanup -------------------------------------------------------------------------------------
descendants() { # every process below the given one, children before their parent
  local child
  for child in $(pgrep -P "$1" 2>/dev/null || true); do
    descendants "$child"
    printf '%s\n' "$child"
  done
}

kill_tree() {
  local pid
  for pid in $(descendants "$1") "$1"; do kill -TERM "$pid" 2>/dev/null || true; done
}

docker_up() { command -v docker >/dev/null && docker info >/dev/null 2>&1; }

# Everything Docker holds under this check's own prefix, plus what a killed test left under a
# name that ends with its own process id (api/tests/ingestion/parser/test_container.py names its
# project and its image after os.getpid()). Nothing else: the names of others never match.
remove_docker_things() { # remove_docker_things [process id of a killed step ...]
  local pattern="^${PREFIX}[-_]" pid ids
  for pid in "$@"; do pattern="$pattern|^jobapp-[a-z0-9-]*-test-${pid}([-_]|\$)"; done
  ids="$(docker ps -a --format '{{.ID}} {{.Names}} {{.Label "com.docker.compose.project"}}' 2>/dev/null |
    awk -v p="$pattern" '$2 ~ p || ($3 != "" && ($3 "-") ~ p) { print $1 }')"
  # shellcheck disable=SC2086
  [[ -z "$ids" ]] || docker rm -f -v $ids >/dev/null 2>&1 || true
  ids="$(docker volume ls -q 2>/dev/null | awk -v p="$pattern" '$1 ~ p')"
  # shellcheck disable=SC2086
  [[ -z "$ids" ]] || docker volume rm -f $ids >/dev/null 2>&1 || true
  ids="$(docker network ls --format '{{.Name}}' 2>/dev/null | awk -v p="$pattern" '$1 ~ p')"
  # shellcheck disable=SC2086
  [[ -z "$ids" ]] || docker network rm $ids >/dev/null 2>&1 || true
  pattern="^${PREFIX}-"
  for pid in "$@"; do pattern="$pattern|^jobapp-worker:parser-test-${pid}\$"; done
  ids="$(docker image ls --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | awk -v p="$pattern" '$1 ~ p')"
  # shellcheck disable=SC2086
  [[ -z "$ids" ]] || docker image rm -f $ids >/dev/null 2>&1 || true
}

wait_gone() { # wait_gone <seconds> <process ids>: until none of them lives, at most that long
  local left="$1" pid alive
  while ((left > 0)); do
    alive=0
    for pid in $2; do
      if kill -0 "$pid" 2>/dev/null; then alive=1; fi
    done
    ((alive)) || return 0
    sleep 1
    left=$((left - 1))
  done
}

cleanup() {
  local rc=$? pids
  trap - EXIT INT TERM
  set +e
  pids="$(descendants $$)"
  # First SIGINT, for the programs that end in order on it (pytest and its fixtures), and
  # INT_GRACE seconds for them; then SIGTERM for what is left, ten seconds, and SIGKILL. Nothing
  # of the check runs any more when its containers are removed below.
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill -INT $pids 2>/dev/null
    wait_gone "$INT_GRACE" "$pids"
    # shellcheck disable=SC2086
    kill -TERM $pids 2>/dev/null
    wait_gone 10 "$pids"
    # shellcheck disable=SC2086
    kill -KILL $pids 2>/dev/null
  fi
  wait 2>/dev/null
  if [[ -n "$WORK" ]]; then
    # shellcheck disable=SC2086
    if docker_up; then remove_docker_things $pids; fi
    if [[ -n "$TREE" ]]; then
      git -C "$ROOT" worktree remove --force "$TREE" >/dev/null 2>&1
    fi
    rm -rf "$WORK"
    git -C "$ROOT" worktree prune >/dev/null 2>&1
  fi
  ((HAVE_LOCK)) && rm -rf "$LOCK"
  exit "$rc"
}
trap cleanup EXIT
trap 'printf "\n%s: interrupted, cleaning up\n" "$PROG" >&2; exit 130' INT TERM

# --- the lock and the disk -------------------------------------------------------------------------
mkdir -p "$RECORDS"
if ! mkdir "$LOCK" 2>/dev/null; then
  holder="$(cat "$LOCK/pid" 2>/dev/null || true)"
  if [[ -z "$holder" ]]; then
    sleep 1 # the holder may be between its mkdir and its first write
    holder="$(cat "$LOCK/pid" 2>/dev/null || true)"
  fi
  if [[ -n "$holder" ]] && kill -0 "$holder" 2>/dev/null; then
    printf '%s: another check is running (%s). One at a time.\n' "$PROG" "$(cat "$LOCK/holder" 2>/dev/null || echo "process $holder")" >&2
    exit 3
  fi
  say "a lock without a living holder was left in $LOCK: taken over"
  rm -rf "$LOCK"
  mkdir "$LOCK" || {
    printf '%s: cannot take the lock %s\n' "$PROG" "$LOCK" >&2
    exit 3
  }
fi
HAVE_LOCK=1
printf '%s\n' "$$" >"$LOCK/pid"
printf 'process %s, check %s, started %s, base %s' "$$" "$CHECK_ID" "$(date '+%Y-%m-%d %H:%M:%S')" "$BASE" >"$LOCK/holder"

VOLUME=/System/Volumes/Data
[[ -d "$VOLUME" ]] || VOLUME="${TMPDIR:-/tmp}"
free_gb="$(df -k "$VOLUME" | awk 'NR == 2 { printf "%d", $4 / 1048576 }')"
if ((free_gb < MIN_FREE_GB)); then
  printf '%s: %s GiB free on %s, under the %s GiB this check needs to start. Free some disk first (docker system df, docker builder prune).\n' \
    "$PROG" "$free_gb" "$VOLUME" "$MIN_FREE_GB" >&2
  exit 3
fi

# --- the tree to check -----------------------------------------------------------------------------
git -C "$ROOT" fetch -q origin 2>/dev/null || fail_prepare "git fetch origin failed: the check reads origin's refs, so it needs the network"
base_sha="$(git -C "$ROOT" rev-parse --verify -q "$BASE^{commit}")" || fail_prepare "--base $BASE names no commit"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/jobapp-local-check.XXXXXX")"
WORK="$(cd "$WORK" && pwd -P)"
TREE="$WORK/tree"
LOGS="$RECORDS/logs/$CHECK_ID"
mkdir -p "$LOGS"
: >"$WORK/results"
: >"$WORK/merges"
git -C "$ROOT" worktree add -q --detach "$TREE" "$base_sha" >"$LOGS/worktree.log" 2>&1 ||
  fail_prepare "cannot make the worktree of $BASE ($LOGS/worktree.log)"

for ref in ${MERGES[@]+"${MERGES[@]}"}; do
  if [[ "$ref" =~ ^[0-9]+$ ]]; then
    git -C "$ROOT" fetch -q origin "pull/$ref/head" 2>/dev/null || fail_prepare "cannot fetch pull request $ref from origin"
    sha="$(git -C "$ROOT" rev-parse FETCH_HEAD)"
    ref="pull request $ref"
  else
    sha="$(git -C "$ROOT" rev-parse --verify -q "$ref^{commit}")" || fail_prepare "--merge $ref names no commit"
  fi
  if ! git -C "$TREE" -c user.name=local-check -c user.email=local-check@localhost \
    merge --no-ff --no-edit -q "$sha" >"$LOGS/merge.log" 2>&1; then
    say "MERGE CONFLICT: $ref ($sha) does not merge into $BASE after the refs before it. Files:"
    git -C "$TREE" diff --name-only --diff-filter=U | sed 's/^/  /'
    say "Rebase or merge that branch by hand, or give another order. Nothing was checked."
    say "LOCAL CHECK: FAIL $(git -C "$TREE" rev-parse 'HEAD^{tree}') merge-conflict"
    exit 2
  fi
  printf '%s\t%s\n' "$ref" "$sha" >>"$WORK/merges"
done
head_sha="$(git -C "$TREE" rev-parse HEAD)"
tree_sha="$(git -C "$TREE" rev-parse 'HEAD^{tree}')"

# --- running steps ---------------------------------------------------------------------------------
# run_step <name> <command ...>: the command in a bash process of its own, started in the tree with
# errexit, nounset and pipefail, its output in the step's log; one line when it ends. A step that
# runs longer than STEP_TIMEOUT is stopped and fails.
#
# Why a process and not a subshell. Bash ignores errexit in everything it executes below a command
# whose status is tested (`run_step x s_x || true`, `if run_step x s_x`), through the function
# call and into its subshells, whatever `set -e` says there: a step of two commands then passed
# when the first failed and the last succeeded (review of pull request #121, B1; the same on bash
# 3.2 and 5). A new bash inherits nothing of that state, so the status of a step is the same
# however run_step is called. The step is given every function of this script (declare -f) and the
# variables named in STEP_ENV; a name it lacks stops it (nounset) and the step fails.
STEP_ENV=(ROOT MAIN RECORDS TREE WORK LOGS PREFIX CHECK_ID PROG JOBS WEB_JOBS STACKS base_sha head_sha)
run_step() {
  local name="$1" start=$SECONDS rc=0 pid watcher
  shift
  (
    cd "$TREE"
    export "${STEP_ENV[@]}"
    exec "$BASH" -c "$(declare -f)"$'\n''set -euo pipefail; "$@"' "$PROG" "$@"
  ) >"$LOGS/$name.log" 2>&1 </dev/null &
  pid=$!
  (
    sleep "$STEP_TIMEOUT"
    printf '\n%s: step %s ran longer than %s s, stopped\n' "$PROG" "$name" "$STEP_TIMEOUT" >>"$LOGS/$name.log"
    kill_tree "$pid"
  ) >/dev/null 2>&1 &
  watcher=$!
  wait "$pid" || rc=$?
  kill_tree "$watcher"
  wait "$watcher" 2>/dev/null || true
  printf '%s\t%s\t%s\n' "$name" "$rc" "$((SECONDS - start))" >>"$WORK/results"
  if ((rc == 0)); then
    printf '%-26s PASS %5ss\n' "$name" "$((SECONDS - start))"
  else
    printf '%-26s FAIL %5ss\n' "$name" "$((SECONDS - start))"
  fi
  return "$rc"
}

not_run() { # not_run <name> <why>: a step that could not start counts as failed
  printf '%s\n' "$2" >"$LOGS/$1.log"
  printf '%s\t%s\t%s\n' "$1" 1 0 >>"$WORK/results"
  printf '%-26s FAIL %5ss  (%s)\n' "$1" 0 "$2"
}

# A step started here is a background job, and bash starts those with SIGINT ignored, which a
# program inherits. pytest is given the default back: on Ctrl+C the cleanup sends it SIGINT and
# its fixtures remove the containers they started (their names carry nothing of this check).
interruptible() {
  python3 -c 'import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.execvp(sys.argv[1], sys.argv[1:])' "$@"
}

free_port() { python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }

# --- the steps: api --------------------------------------------------------------------------------
s_api_install() {
  cd api
  uv sync --frozen
}

# The service containers of the CI api job: the same images, the same throwaway settings (no
# fsync), on loopback ports of their own.
s_api_services() {
  local port tries
  docker run -d --rm --name "$PREFIX-pg" --label "jobapp.local-check=$CHECK_ID" \
    -e POSTGRES_USER=jobapp -e POSTGRES_PASSWORD=ci-only-not-a-secret -e POSTGRES_DB=jobapp_test \
    -p 127.0.0.1::5432 postgres:16.15-alpine \
    -c fsync=off -c synchronous_commit=off -c full_page_writes=off
  docker run -d --rm --name "$PREFIX-redis" --label "jobapp.local-check=$CHECK_ID" \
    -p 127.0.0.1::6379 redis:7.4.11-alpine
  for tries in $(seq 1 60); do
    if docker exec "$PREFIX-pg" psql -U jobapp -d jobapp_test -h 127.0.0.1 -c 'SELECT 1' >/dev/null 2>&1 &&
      docker exec "$PREFIX-redis" redis-cli ping >/dev/null 2>&1; then
      break
    fi
    [[ "$tries" != 60 ]] || {
      echo "Postgres or Redis did not answer in 60 s"
      return 1
    }
    sleep 1
  done
  port="$(docker port "$PREFIX-pg" 5432/tcp | sed -n '1s/.*://p')"
  echo "export TEST_DATABASE_URL=postgresql+psycopg://jobapp:ci-only-not-a-secret@127.0.0.1:$port/jobapp_test" >"$WORK/api.env"
  port="$(docker port "$PREFIX-redis" 6379/tcp | sed -n '1s/.*://p')"
  echo "export REDIS_URL=redis://127.0.0.1:$port/0" >>"$WORK/api.env"
}

s_ruff() {
  cd api
  uv run --frozen ruff check .
  uv run --frozen ruff format --check .
}

s_pytest() {
  cd api
  # shellcheck disable=SC1091
  . "$WORK/api.env"
  interruptible uv run --frozen pytest tests -m "not docker" -p no:cacheprovider \
    -n "$JOBS" --dist worksteal --cov --cov-report= --cov-fail-under=0 --durations=25
}

s_coverage() {
  cd api
  uv run --frozen coverage report --fail-under=95 | tail -n 15
  return "${PIPESTATUS[0]}"
}

s_pytest_docker() {
  cd api
  # shellcheck disable=SC1091
  . "$WORK/api.env"
  interruptible uv run --frozen pytest -m docker -p no:cacheprovider --durations=10
}

s_scoring() {
  cd api
  # shellcheck disable=SC1091
  . "$WORK/api.env"
  SCORE_BUDGET_S=0.3 uv run --frozen pytest -p no:cacheprovider \
    "tests/jobs/test_scoring.py::test_long_text_is_bounded_and_fast"
}

lane_api() {
  if ! run_step api-install s_api_install || ! run_step api-services s_api_services; then
    not_run pytest "the api dependencies or its Postgres and Redis did not start"
    return 0
  fi
  run_step ruff s_ruff || true
  if run_step pytest s_pytest; then run_step coverage s_coverage || true; fi
}

# The tests that measure time run alone, one after the other, once every lane has ended: Vitest
# (a countdown of the account page and a sort of the search tab read the real clock), the docker
# marked tests (the parser's CPU and wall clock limits, a paused database) and the scoring bound.
# Measured on 2026-10-13, next to the suite and four journey stacks (load 15 on 12 cores): Vitest
# failed in 2 runs of 4, two CPU limit tests and two `docker run` of the docker marked tests'
# own Postgres failed in 1 run; alone they passed every time.
quiet_phase() {
  if wanted journeys && [[ "$(cat "$WORK/web.done" 2>/dev/null)" == 0 ]]; then
    run_step vitest s_vitest || true
  fi
  if wanted api && [[ -f "$WORK/api.env" ]]; then
    run_step pytest-docker s_pytest_docker || true
    run_step scoring s_scoring || true
  fi
}

# --- the steps: web --------------------------------------------------------------------------------
s_web_install() {
  cd web
  pnpm install --frozen-lockfile
}
s_lint() {
  cd web
  pnpm lint
}
s_typecheck() {
  cd web
  pnpm typecheck
}
s_build() {
  cd web
  pnpm build
}
s_vitest() {
  cd web
  pnpm exec vitest run --maxWorkers="$WEB_JOBS" --reporter=default
}

lane_web() {
  local rc=0
  run_step web-install s_web_install || rc=$?
  echo "$rc" >"$WORK/web.done"
  ((rc == 0)) || return 0
  wanted journeys || return 0
  run_step lint s_lint &
  run_step typecheck s_typecheck &
  run_step build s_build &
  wait
}

# --- the steps: images and journeys ----------------------------------------------------------------
# The compose files of a scratch stack: compose.yml, the fake ATS, compose.mac.yml on Apple
# silicon (OPENSSL_armcap, see that file), and this check's own image names, so the shared
# jobapp-*:latest tags are neither built over nor run.
stack_files() {
  printf '%s\n' -f compose.yml -f tests/ats-stub.compose.yml
  if [[ "$(uname -s)-$(uname -m)" == Darwin-arm64 ]]; then printf '%s\n' -f compose.mac.yml; fi
  printf '%s\n' -f "$WORK/images.yml"
}

s_images() {
  local files=() one
  umask 077
  .github/scripts/ci_env.sh 8080 >.env
  if grep -q change-me .env; then
    echo "a placeholder secret of .env.example was left in .env"
    return 1
  fi
  echo "AGENTS_FAKE_BACKEND=true" >>.env
  cat >"$WORK/images.yml" <<EOF
services:
  caddy:
    image: $PREFIX-caddy:latest
  api:
    image: $PREFIX-api:latest
  worker:
    image: $PREFIX-worker:latest
  parser:
    image: $PREFIX-worker:latest
EOF
  # CI built with `docker buildx bake -f compose.yml`. The bake of Docker Desktop on this Mac
  # refuses that file ("service worker refers to undefined network default", measured on
  # 2026-10-13), so the same three images are built by compose, which starts them below, under
  # this check's names: the shared jobapp-*:latest tags are not written.
  while IFS= read -r one; do files+=("$one"); done < <(stack_files)
  docker compose --progress plain -p "$PREFIX-build" "${files[@]}" build api worker caddy
}

lane_images() {
  local rc=0
  run_step images s_images || rc=$?
  echo "$rc" >"$WORK/images.done"
}

s_chromium() {
  cd web
  pnpm exec playwright install chromium
}

# One journey group on a scratch stack of its own, as the CI steps "Start the stack and migrate",
# the group's Playwright command, "Stack logs" and "Tear down the scratch stack".
journey_specs() { # the Playwright arguments of a group, as ci.yml has them
  case "$1" in
  apps) echo "jobs.spec.ts jobs-map.spec.ts agents-runs.spec.ts agents-triage.spec.ts agents-chat.spec.ts agents-streaming.spec.ts --workers=1" ;;
  ingestion) echo "-c e2e/ingestion-all.config.ts" ;;
  identity) echo "-c e2e/identity-all.config.ts" ;;
  sweep) echo "jobs-sweep.spec.ts --workers=1" ;;
  esac
}

journey_play() { # journey_play <group> <Playwright arguments>
  # Called below `&&` in s_journey, where errexit is off: the two commands are chained.
  # shellcheck disable=SC2086
  cd web && pnpm exec playwright test $2 --reporter=list --output="test-results/$1"
}

s_journey() {
  local group="$1" project="$PREFIX-$1" rc=0 files=() port specs one
  while IFS= read -r one; do files+=("$one"); done < <(stack_files)
  port="$(free_port)"
  export HTTP_PORT="$port" E2E_CADDY_URL="http://127.0.0.1:$port" E2E_COMPOSE_PROJECT="$project" E2E_EVIDENCE=ci
  case "$group" in
  apps)
    python3 .github/scripts/synthetic_jobs.py "$WORK/synthetic/data"
    export E2E_JOBS_DATA="$WORK/synthetic/data"
    ;;
  identity) export E2E_ADMIN_EMAIL=e2e-admin@example.org E2E_PASSWORD_THROTTLE=1 ;;
  sweep) export E2E_SKIP_REAL_SWEEP=1 ;;
  esac
  specs="$(journey_specs "$group")"
  echo "stack $project on $E2E_CADDY_URL"
  docker compose -p "$project" "${files[@]}" up -d --no-build --wait --wait-timeout 300 &&
    docker compose -p "$project" -f compose.yml exec -T api alembic upgrade head &&
    journey_play "$group" "$specs" || rc=$?
  docker compose -p "$project" -f compose.yml -f tests/ats-stub.compose.yml logs --no-color >"$LOGS/stack-$group.log" 2>&1 || true
  docker compose -p "$project" -f compose.yml -f tests/ats-stub.compose.yml down -v --remove-orphans --timeout 0 || true
  return "$rc"
}

lane_journeys() {
  local groups=() group running=0 why=""
  if wanted checks; then groups+=(apps ingestion); fi
  if wanted journeys; then groups+=(identity sweep); fi
  ((${#groups[@]})) || return 0
  while [[ ! -f "$WORK/images.done" || ! -f "$WORK/web.done" ]]; do sleep 1; done
  [[ "$(cat "$WORK/images.done")" == 0 ]] || why="the images were not built"
  [[ "$(cat "$WORK/web.done")" == 0 ]] || why="the web dependencies were not installed"
  [[ -n "$why" ]] || run_step chromium s_chromium || why="Chromium for Playwright was not installed"
  if [[ -n "$why" ]]; then
    for group in "${groups[@]}"; do not_run "journeys-$group" "$why"; done
    return 0
  fi
  for group in "${groups[@]}"; do
    run_step "journeys-$group" s_journey "$group" &
    running=$((running + 1))
    if ((running >= STACKS)); then
      wait
      running=0
    fi
  done
  wait
}

# --- the steps: checks -----------------------------------------------------------------------------
s_precommit() {
  # As the CI step: the hooks that have a step of their own are skipped here (ruff in api, eslint
  # in lint, shellcheck and the secret scan below).
  SKIP=ruff-check,ruff-format,eslint,shellcheck,gitleaks uvx --from pre-commit==4.6.2 pre-commit run --all-files
}

# The commits between the base and the merged head, read as CI read the commits of a push
# (secret_scan.sh: each commit, each merge commit, the messages, then the tip as a whole).
s_secret_scan() {
  env -u SKIP GITHUB_EVENT_NAME=push CI_PUSH_BEFORE="$base_sha" \
    uvx --from pre-commit==4.6.2 pre-commit run gitleaks-commits --hook-stage manual --verbose
}

shellcheck_files() { # the list of the "Shellcheck" step of ci.yml
  awk '/koalaman\/shellcheck:/ { on = 1; next } on && /^ *(#|- )/ { exit } on { print }' .github/workflows/ci.yml
}
shellcheck_image() { # no `| head -n 1`: under pipefail a writer cut short by head fails the step
  awk 'match($0, /koalaman\/shellcheck:[^ ]*/) { print substr($0, RSTART, RLENGTH); exit }' .github/workflows/ci.yml
}

s_shellcheck() {
  local files image
  files="$(shellcheck_files | tr '\n' ' ')"
  image="$(shellcheck_image)"
  [[ -n "${files// /}" && -n "$image" ]] || {
    echo "the Shellcheck step of .github/workflows/ci.yml was not found: its file list is the one read here"
    return 1
  }
  # shellcheck disable=SC2086
  docker run --rm --name "$PREFIX-shellcheck" --label "jobapp.local-check=$CHECK_ID" \
    -v "$TREE:/mnt:ro" -w /mnt "$image" $files
}

s_e2e_groups() {
  python3 .github/scripts/e2e_groups_test.py
  python3 .github/scripts/e2e_groups.py
}
s_ci_plan() { python3 .github/scripts/ci_plan_test.py; }

shell_tests() { # every tests/*_test.sh that a step of ci.yml runs
  sed -n 's/^ *run: \(tests\/[a-z_]*_test\.sh\)$/\1/p' .github/workflows/ci.yml
}

lane_checks() {
  local script
  run_step pre-commit s_precommit || true
  run_step secret-scan s_secret_scan || true
  run_step shellcheck s_shellcheck || true
  run_step e2e-groups s_e2e_groups || true
  run_step ci-plan s_ci_plan || true
  # Side by side: each works in a temporary folder of its own, against fakes of docker, ssh and gh.
  for script in $(cd "$TREE" && shell_tests); do
    script="${script#tests/}"
    run_step "${script%.sh}" "tests/$script" &
  done
  wait
}

# --- the plan --------------------------------------------------------------------------------------
real_plan() {
  local pids=() pid lanes_failed=0
  if wanted api; then
    lane_api &
    pids+=($!)
  fi
  if wanted checks || wanted journeys; then
    lane_web &
    pids+=($!)
    lane_images &
    pids+=($!)
    lane_journeys &
    pids+=($!)
  fi
  if wanted checks; then
    lane_checks &
    pids+=($!)
  fi
  for pid in "${pids[@]}"; do wait "$pid" || lanes_failed=1; done
  ((lanes_failed == 0)) || not_run lanes "a lane of the check ended early"
  quiet_phase
}

tool_versions() {
  printf 'git\t%s\n' "$(git --version | awk '{ print $3 }')"
  printf 'bash\t%s\n' "$BASH_VERSION"
  printf 'os\t%s\n' "$(uname -srm)"
  [[ -n "${LOCAL_CHECK_STUB:-}" ]] && return 0
  printf 'uv\t%s\n' "$(uv --version | awk '{ print $2 }')"
  printf 'python3\t%s\n' "$(python3 --version | awk '{ print $2 }')"
  printf 'node\t%s\n' "$(node --version)"
  printf 'pnpm\t%s\n' "$(pnpm --version)"
  printf 'docker\t%s\n' "$(docker version --format '{{.Server.Version}}')"
  printf 'compose\t%s\n' "$(docker compose version --short)"
}

if [[ -n "${LOCAL_CHECK_STUB:-}" ]]; then
  [[ -f "$LOCAL_CHECK_STUB" ]] || usage_error "LOCAL_CHECK_STUB names no file: $LOCAL_CHECK_STUB"
  # shellcheck disable=SC1090
  . "$LOCAL_CHECK_STUB"
else
  for tool in docker uv pnpm node python3; do
    command -v "$tool" >/dev/null || fail_prepare "$tool is not installed"
  done
  docker_up || fail_prepare "the Docker daemon does not answer: start Docker Desktop"
  # CI=true as on a runner: the steps behave as they did there (Playwright retries a failed test
  # once and forbids test.only; tests/tailscale_test.sh skips its live start of a tailscale
  # container, which stays a test run by hand).
  export UV_NATIVE_TLS="${UV_NATIVE_TLS:-1}" FORCE_COLOR=0 CI=true
  cores="$(getconf _NPROCESSORS_ONLN)"
  [[ -n "$JOBS" ]] || JOBS=$((cores > 3 ? cores - 2 : 1))
  # Vitest keeps the 3 workers CI gave it (about 50 s).
  WEB_JOBS="${LOCAL_CHECK_VITEST_WORKERS:-3}"
  STACKS="${LOCAL_CHECK_STACKS:-}"
  if [[ -z "$STACKS" ]]; then
    STACKS=$(($(docker info --format '{{.MemTotal}}') / 4294967296))
    ((STACKS <= 4)) || STACKS=4
    ((STACKS >= 1)) || STACKS=1
  fi
  [[ "$STACKS" =~ ^[1-9][0-9]*$ ]] || usage_error "LOCAL_CHECK_STACKS must be a number, 1 or more"
fi

say "local check $CHECK_ID: base $BASE ($base_sha), $(wc -l <"$WORK/merges" | tr -d ' ') merged, tree $tree_sha"
say "logs: $LOGS"
if [[ -n "${LOCAL_CHECK_STUB:-}" ]]; then
  stub_plan
else
  say "pytest -n $JOBS, vitest on $WEB_JOBS, $STACKS journey stacks side by side, $free_gb GiB free"
  real_plan
fi

# --- the result ------------------------------------------------------------------------------------
total=$((SECONDS - STARTED))
failed="$(awk -F '\t' '$2 != 0 { printf "%s%s", sep, $1; sep = "," }' "$WORK/results")"
[[ -s "$WORK/results" ]] || failed="no-step-ran"

# Keep the logs of the last checks only.
# shellcheck disable=SC2012
ls -1t "$RECORDS/logs" 2>/dev/null | tail -n "+$((KEEP_LOGS + 1))" | while IFS= read -r old; do
  rm -rf "${RECORDS:?}/logs/$old"
done

if [[ -n "$failed" ]]; then
  for name in ${failed//,/ }; do
    [[ -f "$LOGS/$name.log" ]] || continue
    printf '\n--- %s: the last 40 lines of %s\n' "$name" "$LOGS/$name.log"
    tail -n 40 "$LOGS/$name.log"
  done
  printf '\nlogs: %s\ntotal: %ss\n' "$LOGS" "$total"
  say "LOCAL CHECK: FAIL $tree_sha $failed"
  exit 1
fi

say "logs: $LOGS"
if ((${#ONLY[@]})); then
  say "a partial run (--only ${ONLY[*]}): no record is written, and ops/deploy-checked.sh will not deploy this tree on it"
else
  tool_versions >"$WORK/versions"
  TREE_SHA="$tree_sha" HEAD_SHA="$head_sha" BASE_REF="$BASE" BASE_SHA="$base_sha" TOTAL="$total" \
    python3 - "$RECORDS/$tree_sha.json" "$WORK" "$CHECK_ID" "${LOCAL_CHECK_STUB:-}" <<'PY'
import datetime
import json
import os
import pathlib
import sys

out, work = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])


def rows(name):
    return [line.split("\t") for line in (work / name).read_text().splitlines() if line]


record = {
    "result": "PASS",
    "tree": os.environ["TREE_SHA"],
    "head": os.environ["HEAD_SHA"],
    "base": {"ref": os.environ["BASE_REF"], "commit": os.environ["BASE_SHA"]},
    "merged": [{"ref": ref, "commit": sha} for ref, sha in rows("merges")],
    "steps": [{"name": name, "seconds": int(secs)} for name, _, secs in rows("results")],
    "seconds": int(os.environ["TOTAL"]),
    "tools": dict(rows("versions")),
    "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "check": sys.argv[3],
    "stub": bool(sys.argv[4]),
}
out.write_text(json.dumps(record, indent=2) + "\n")
PY
  say "record: $RECORDS/$tree_sha.json"
fi
say "LOCAL CHECK: PASS $tree_sha ${total}s"
