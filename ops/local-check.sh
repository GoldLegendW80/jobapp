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
#   checks    pre-commit on all files, the secret scan of the commits that no passed check has
#             read (.github/scripts/secret_scan.sh through its pre-commit hook, as CI called it;
#             the rule of its range is below, at SCAN_FROM), the shell lint of the files ci.yml
#             lists, the e2e groups and CI plan tests, every tests/*_test.sh that ci.yml runs
#             (one that neither runs nor is left out with a reason fails the step shell-tests);
#             the journey group apps and one group per web/e2e/<group>-all.config.ts other than
#             identity (today ingestion, mail, apply, browser and documents; mail on a stack that also runs
#             the fake Google of tests/gmail-stub.compose.yml, never a real account; apply on a
#             stack that also runs the fake ATS forms server of tests/ats-forms-stub.compose.yml,
#             never a real job site; browser on a stack that runs its browser and the static
#             pages of tests/browser-pages.compose.yml).
#   journeys  the web lint, type check, vitest and build; the journey groups identity and sweep.
#
# The lists that ci.yml holds (the files of its Shellcheck step, the tests/*_test.sh steps) are read from
# ci.yml, so a step added there runs here. The journey groups are found in web/e2e (journey_groups,
# below), and tests/local_check_test.sh fails when they differ from the configs on disk, or
# when their spec files differ from those of ci.yml plus the configs that ci.yml does not run.
#
# Arrangement. Four lanes start together: api (its own Postgres and Redis containers on free
# loopback ports; ruff, the suite, the coverage floor), web (install, then lint, type check
# and build side by side), images (api, worker and caddy built by compose under this
# check's own image names, with compose.extra-ca.yml when JOBAPP_EXTRA_CA is set), checks (the tests/*_test.sh side by side). The journey groups start
# when the images and the web install are done, each on a scratch stack of its own (own project,
# own port, own database; the ports are claimed once up front, and a start that fails on a port is retried on another, three starts at most), LOCAL_CHECK_STACKS of them side by side (default: one per 4 GiB of
# Docker memory, at most 4), with one Playwright worker each, as in CI. A slot that frees starts
# the next group at once, and the groups start longest first (their last time in timings.csv,
# a group never timed first of all), so the longest group does not start last. When every lane has
# ended, the tests that measure time run in a quiet phase, one at a time: Vitest, the docker
# marked tests, the test of the suite that measures its own speed, the
# coverage floor and the scoring test. The docker marked tests run on two processes there, on the
# images the step images built. Before the result, the steps that ran are compared with
# the list of the set (expected_steps): one that did not run fails the check.
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
# Timings: every step that ends, and the whole run as the step "total", is a line of
# .local-check/timings.csv in the main checkout (check id, tree, step, seconds, PASS or FAIL),
# kept across runs so a step that slows down shows (issue #280). The journey stacks start longest
# first by the last time recorded there.
#
# Output: one line per step (name, PASS or FAIL, seconds), the last 40 lines of the log of each
# failed step, the folder that keeps the full logs, then as the last line one of
#   LOCAL CHECK: PASS <tree sha> <seconds>s                       the full set, real steps: the only proof
#   LOCAL CHECK: PARTIAL PASS <tree sha> <seconds>s, only <jobs>, no record: not a proof for a merge or a deploy
#   LOCAL CHECK: STUB PASS <tree sha> <seconds>s, stub steps: not a check    (STUB PARTIAL PASS with --only)
#   LOCAL CHECK: FAIL <tree sha> <failed steps>
# A line that begins with exactly "LOCAL CHECK: PASS " is printed only by a run of the full set
# with the real steps, next to the record it wrote. On a PASS of the full set, a record .local-check/<tree sha>.json in the main checkout: the
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
  for pid in "$@"; do pattern="$pattern|^jobapp-worker:parser-test-${pid}\$|^jobapp-renderer:test-${pid}\$"; done
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

# --- the range of the secret scan ------------------------------------------------------------------
# The rule: the scan reads every commit that no passed check has read, up to the merged head. It
# starts after the newest commit on the first parent line of the base whose tree has a PASS record
# in .local-check/ (the base itself when it was checked: the range is then the merged pull
# requests, as a push in CI). When none of the last 200 commits of the base has a record, it
# starts 50 commits before the base, and reads the whole history when the base has fewer.
# So `ops/local-check.sh` with no argument, the remedy when main holds a tree that was not checked,
# reads what reached main since the last checked tree (review of pull request #121, N3: the range
# base..head was empty there, and only the tip against its first parent was read).
SCAN_FROM=""
scan_why="no commit in the last 200 of $BASE has a PASS record: from 50 commits before it"
while read -r commit tree; do
  record="$RECORDS/$tree.json"
  [[ -f "$record" ]] || continue
  grep -q '"result": "PASS"' "$record" || continue
  # A record of stub steps counts for the script's own tests only.
  if [[ -z "${LOCAL_CHECK_STUB:-}" ]] && ! grep -q '"stub": false' "$record"; then continue; fi
  SCAN_FROM="$commit"
  scan_why="the newest commit of $BASE whose tree has a PASS record"
  break
done < <(git -C "$TREE" log --first-parent -n 200 --format='%H %T' "$base_sha")
if [[ -z "$SCAN_FROM" ]]; then
  SCAN_FROM="$(git -C "$TREE" rev-parse -q --verify "$base_sha~50^{commit}" 2>/dev/null)" || {
    SCAN_FROM=""
    scan_why="no commit of $BASE has a PASS record and it has fewer than 50"
  }
fi
scan_count="$(git -C "$TREE" rev-list --count "${SCAN_FROM:+$SCAN_FROM..}$head_sha")"
if [[ -n "$SCAN_FROM" ]]; then
  scan_line="secret scan: $scan_count commits, after $SCAN_FROM up to $head_sha ($scan_why)"
else
  scan_line="secret scan: $scan_count commits, the whole history up to $head_sha ($scan_why)"
fi

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
STEP_ENV=(ROOT MAIN RECORDS TREE WORK LOGS PREFIX CHECK_ID PROG JOBS WEB_JOBS STACKS base_sha head_sha SCAN_FROM scan_line ALONE_TEST NET_BASE STACK_NETWORKS JOURNEY_SHARDS)
TIMINGS="$RECORDS/timings.csv"
timing() { # timing <step> <exit status> <seconds>: one line of timings.csv
  local result=PASS
  [[ "$2" == 0 ]] || result=FAIL
  printf '%s,%s,%s,%s,%s\n' "$CHECK_ID" "${tree_sha:-}" "$1" "$3" "$result" >>"$TIMINGS" 2>/dev/null || true
}

run_step() {
  local name="$1" start=$SECONDS rc=0 pid watcher
  shift
  : >"$LOGS/$name.log"
  # Appended to, as the watcher's line is: what the step writes after it never overwrites it.
  (
    cd "$TREE"
    export "${STEP_ENV[@]}"
    exec "$BASH" -c "$(declare -f)"$'\n''set -euo pipefail; "$@"' "$PROG" "$@"
  ) >>"$LOGS/$name.log" 2>&1 </dev/null &
  pid=$!
  (
    # A sleep stopped before its time (the step ended, or the cleanup) leaves nothing to write or stop.
    sleep "$STEP_TIMEOUT" || exit 0
    printf '\n%s: step %s ran longer than %s s, stopped\n' "$PROG" "$name" "$STEP_TIMEOUT" >>"$LOGS/$name.log"
    kill_tree "$pid"
  ) >/dev/null 2>&1 &
  watcher=$!
  wait "$pid" || rc=$?
  kill_tree "$watcher"
  wait "$watcher" 2>/dev/null || true
  printf '%s\t%s\t%s\n' "$name" "$rc" "$((SECONDS - start))" >>"$WORK/results"
  timing "$name" "$rc" "$((SECONDS - start))"
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
  timing "$1" 1 0
  printf '%-26s FAIL %5ss  (%s)\n' "$1" 0 "$2"
}

# A step started here is a background job, and bash starts those with SIGINT ignored, which a
# program inherits. pytest is given the default back: on Ctrl+C the cleanup sends it SIGINT and
# its fixtures remove the containers they started (their names carry nothing of this check).
interruptible() {
  python3 -c 'import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.execvp(sys.argv[1], sys.argv[1:])' "$@"
}

# The ports of the journey stacks. Each stack publishes its caddy on a loopback port that the
# check chooses (HTTP_PORT). They are claimed once, up front, from 20000 to 29999 (under the
# ephemeral ranges of macOS and Linux, which outgoing connections take at random): a claim is a
# folder in $WORK/ports, so no two stacks of one check can hold the same number, and a number is
# bound for a moment to see that it is free now.
port_free() { python3 -c 'import socket, sys; socket.socket().bind(("127.0.0.1", int(sys.argv[1])))' "$1" 2>/dev/null; }

claim_port() { # prints a port that is free and that no stack of this check holds
  local port tries=0
  mkdir -p "$WORK/ports"
  while ((tries++ < 500)); do
    port=$((20000 + RANDOM % 10000))
    if mkdir "$WORK/ports/$port" 2>/dev/null && port_free "$port"; then
      printf '%s\n' "$port"
      return 0
    fi
  done
  echo "no free loopback port found" >&2
  return 1
}

pick_journey_ports() { # pick_journey_ports <group ...>: one claimed port per group, in $WORK/ports/<group>
  local group port
  mkdir -p "$WORK/ports"
  for group in "$@"; do
    port="$(claim_port)" || return 1
    printf '%s\n' "$port" >"$WORK/ports/$group"
  done
}

# The networks of the journey stacks. A stack has five (default, parsing, rendering, browsing,
# egress), and Docker Desktop's predefined address pools hold about thirty: six stacks side by side
# plus the stacks of agents ran out ("all predefined address pools have been fully subnetted",
# check lc1014171255-32596). So each stack takes a slot, claimed like a port in $WORK/nets and
# freed with the stack, and its networks get /27 subnets of that slot out of NET_BASE.0.0/16,
# outside the pools Docker picks from: slot s, network j is NET_BASE.(5s + j).0/27.
NET_BASE="${LOCAL_CHECK_NET_BASE:-10.213}"
STACK_NETWORKS="default parsing rendering browsing egress"

# A check that died without its trap (SIGKILL, Docker Desktop or the Mac restarted) leaves its
# stacks' networks behind, on the same slots as this check's: a slot is claimed only when no Docker
# network holds one of its subnets (one query per claim), and stack_up moves a stack that still
# meets "Pool overlaps" to another slot.
claim_net_slot() { # prints the first slot (0 to 49) that no stack of this check holds and no network uses
  local slot j ids used="" taken
  mkdir -p "$WORK/nets"
  ids="$(docker network ls -q 2>/dev/null)" || ids=""
  # shellcheck disable=SC2086
  [[ -z "$ids" ]] || used=" $(docker network inspect --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' $ids 2>/dev/null | tr '\n' ' ') " || used=""
  for slot in $(seq 0 49); do
    taken=""
    for j in 0 1 2 3 4; do
      [[ "$used" != *" $NET_BASE.$((slot * 5 + j)).0/27 "* ]] || taken=1
    done
    [[ -z "$taken" ]] || continue
    if mkdir "$WORK/nets/$slot" 2>/dev/null; then
      printf '%s\n' "$slot"
      return 0
    fi
  done
  echo "no free network slot for a stack" >&2
  return 1
}

stack_networks() { # stack_networks <group> <slot>: writes the compose override of its subnets, prints its path
  local file="$WORK/networks-$1.yml" j=0 net
  {
    echo "networks:"
    for net in $STACK_NETWORKS; do
      printf '  %s:\n    ipam:\n      config:\n        - subnet: %s.%s.0/27\n' "$net" "$NET_BASE" "$(($2 * 5 + j))"
      j=$((j + 1))
    done
  } >"$file"
  printf '%s\n' "$file"
}

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
  uv run --frozen ruff check . ../browser
  uv run --frozen ruff format --check . ../browser
}

# One test of the suite measures its own speed and failed once in five runs next to the four
# journey stacks (review of pull request #121, N17): a check that is flaky gets run again until it
# is green. It is taken out of the parallel suite and run alone in the quiet phase, its coverage
# added to the suite's, and the coverage floor is read after it. A test of that name that is gone
# fails the step pytest-alone (pytest finds nothing to run).
ALONE_TEST="tests/agents/test_live_hardening.py::test_a_run_on_a_store_that_never_answers_keeps_its_speed"

s_pytest() {
  cd api
  # shellcheck disable=SC1091
  . "$WORK/api.env"
  interruptible uv run --frozen pytest tests -m "not docker" -p no:cacheprovider --deselect "$ALONE_TEST" \
    -n "$JOBS" --dist worksteal --cov --cov-report= --cov-fail-under=0 --durations=25
}

s_pytest_alone() {
  cd api
  # shellcheck disable=SC1091
  . "$WORK/api.env"
  interruptible uv run --frozen pytest "$ALONE_TEST" -p no:cacheprovider \
    --cov --cov-append --cov-report= --cov-fail-under=0
}

# A few tests that measure time fail now and then while agents load the machine (2026-10-14: a burst
# of sign-ins over its time bound, the scrub before clip, the parser's client calls), and each cost
# a gate. When the parallel suite failed only on tests, at most FLAKY_MAX of them and no error,
# the quiet phase runs exactly those again, one process, as pytest-alone: if they all pass, the step
# pytest counts as passed and the summary and the record list them under `flaky`, so an issue can
# be filed. One failing again, more of them, or an error (collection, fixture) fail as before.
FLAKY_MAX=3

flaky_candidates() { # prints the failed node ids of the step pytest when a rerun may clear them
  local log="$WORK/pytest-plain.log" summary n ids
  # pytest colours its output (ESC[31m before "FAILED" and the summary): read a copy without.
  sed $'s/\x1b\\[[0-9;]*m//g' "$LOGS/pytest.log" >"$log"
  awk -F '\t' '$1 == "pytest" && $2 == 1 { found = 1 } END { exit !found }' "$WORK/results" || return 1
  summary="$(grep -E '^=+ .* in [0-9.]+s' "$log" | tail -n 1)"
  [[ "$summary" =~ ([0-9]+)\ failed ]] || return 1
  n="${BASH_REMATCH[1]}"
  # A pytest error line names a test file ("ERROR tests/x.py::test - ..."); a captured log line
  # of the app ("ERROR    app:main.py:207 unhandled error") is not one.
  ((n <= FLAKY_MAX)) && [[ "$summary" != *error* ]] && ! grep -qE '^ERROR [^ ]+\.py' "$log" || return 1
  # A node id may hold spaces (a parametrized id like [unicode-1-api key]): it ends at " - ".
  ids="$(awk 'sub(/^FAILED /, "") { i = index($0, " - "); print(i ? substr($0, 1, i - 1) : $0) }' "$log" | sort -u)"
  [[ "$(grep -c . <<<"$ids")" == "$n" ]] || return 1
  printf '%s\n' "$ids"
}

s_pytest_flaky() {
  local ids=() one
  cd api
  # shellcheck disable=SC1091
  . "$WORK/api.env"
  while IFS= read -r one; do ids+=("$one"); done <"$WORK/flaky-ids"
  interruptible uv run --frozen pytest -p no:cacheprovider -p no:xdist "${ids[@]}"
}

s_coverage() {
  cd api
  uv run --frozen coverage report --fail-under=95 | tail -n 15
  return "${PIPESTATUS[0]}"
}

# The docker marked tests build the worker, renderer and browser images under tags of their own
# unless PARSER_TEST_IMAGE, RENDERER_TEST_IMAGE and BROWSER_TEST_IMAGE name them: the step images
# built the same Dockerfiles and targets (compose.yml) in the parallel lanes, so the quiet phase
# no longer builds them again, file after file (issue #280). Two processes, one test file each
# (--dist loadfile, a file's stack is a module fixture): measured on 2026-10-14, the 448 tests
# passed on -n 2 beside other agents' suites, the parser's CPU and wall clock limits included.
s_pytest_docker() {
  cd api
  # shellcheck disable=SC1091
  . "$WORK/api.env"
  if [[ "$(cat "$WORK/images.done" 2>/dev/null)" == 0 ]]; then
    export PARSER_TEST_IMAGE="$PREFIX-worker:latest" RENDERER_TEST_IMAGE="$PREFIX-renderer:latest" \
      BROWSER_TEST_IMAGE="$PREFIX-browser:latest"
  fi
  interruptible uv run --frozen pytest -m docker -p no:cacheprovider -n 2 --dist loadfile --durations=10
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
  run_step pytest s_pytest || true
}

passed() { # passed <step>: true when that step ran and passed
  awk -F '\t' -v name="$1" '$1 == name && $2 == 0 { ok = 1 } END { exit !ok }' "$WORK/results"
}

# The tests that measure time run once every lane has ended: Vitest (a countdown of the account
# page and a sort of the search tab read the real clock), the docker marked tests (the parser's
# CPU and wall clock limits, a paused database), the test of the suite that measures its own
# speed and the scoring bound.
# Measured on 2026-10-13, next to the suite and four journey stacks (load 15 on 12 cores): Vitest
# failed in 2 runs of 4, two CPU limit tests and two `docker run` of the docker marked tests'
# own Postgres failed in 1 run; alone they passed every time.
# Pull request #121 ran Vitest next to the docker marked tests; the second full run showed that
# cannot work (two parser tests got wall_limit for cpu_limit), so they run one after the other
# again and every step of the phase has the machine to itself.
quiet_phase() {
  local api=0
  journeys_again
  if wanted api && [[ -f "$WORK/api.env" ]]; then api=1; fi
  if wanted journeys && [[ "$(cat "$WORK/web.done" 2>/dev/null)" == 0 ]]; then
    run_step vitest s_vitest || true
  fi
  # Alone: two parser tests burn 45 s of CPU within 60 s of wall time by construction, which
  # needs most of a core for a minute; beside Vitest they end on wall_limit.
  if ((api)); then run_step pytest-docker s_pytest_docker || true; fi
  ((api)) || return 0
  if flaky_candidates >"$WORK/flaky-ids" && run_step pytest-flaky s_pytest_flaky; then
    awk -F '\t' -v OFS='\t' '$1 == "pytest" { $2 = 0 } { print }' "$WORK/results" >"$WORK/results.new"
    mv "$WORK/results.new" "$WORK/results"
    cat "$WORK/flaky-ids" >>"$WORK/flaky"
    echo "pytest: counted as passed, its failed tests passed when run again alone: $(tr '\n' ' ' <"$WORK/flaky-ids")"
    rm -f "$WORK/flaky-ids"
  fi
  if passed pytest; then
    run_step pytest-alone s_pytest_alone || true
    run_step coverage s_coverage || true
  fi
  run_step scoring s_scoring || true
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
  renderer:
    image: $PREFIX-renderer:latest
  browser:
    image: $PREFIX-browser:latest
EOF
  # CI built with `docker buildx bake -f compose.yml`. The bake of Docker Desktop on this Mac
  # refuses that file ("service worker refers to undefined network default", measured on
  # 2026-10-13), so the same images, and the renderer's (documents D3), are built by compose,
  # which starts them below, under this check's names: the shared jobapp-*:latest tags are not
  # written.
  while IFS= read -r one; do files+=("$one"); done < <(stack_files)
  # Behind a TLS inspecting proxy (README): the bundle reaches these builds only, never a stack.
  if [[ -n "${JOBAPP_EXTRA_CA:-}" ]]; then
    if [[ "$JOBAPP_EXTRA_CA" != /* || ! -f "$JOBAPP_EXTRA_CA" || ! -r "$JOBAPP_EXTRA_CA" ]]; then
      echo "JOBAPP_EXTRA_CA is set but does not name a readable file by its absolute path"
      return 1
    fi
    echo "JOBAPP_EXTRA_CA is set: the builds also trust its bundle (compose.extra-ca.yml)"
    files+=(-f compose.extra-ca.yml)
  fi
  # The browser image runs in the apply and browser stacks, and in the docker marked tests (BROWSER_TEST_IMAGE).
  docker compose --progress plain -p "$PREFIX-build" "${files[@]}" build api worker caddy renderer browser
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

# The journey groups. Two name their spec files one by one, as ci.yml has them: apps and sweep.
# Every other group is a Playwright config of the tree, web/e2e/<group>-all.config.ts, found
# here: a module adds its group by adding that file, with no line of this script to edit
# (issue #279). identity and sweep were the e2e groups of the CI job journeys and run in that
# job here; every other group runs in the job checks. The order is apps, the configs by name,
# then sweep: the stacks start in that order, LOCAL_CHECK_STACKS at a time.
config_groups() { # the <group> of every web/e2e/<group>-all.config.ts of the tree, sorted
  local config name
  for config in "$TREE"/web/e2e/*-all.config.ts; do
    [[ -f "$config" ]] || continue # no match leaves the pattern itself
    name="${config##*/}"
    printf '%s\n' "${name%-all.config.ts}"
  done | LC_ALL=C sort
}

journey_groups() { # journey_groups <checks|journeys>: the groups of that CI job, in run order
  local group
  for group in apps $(config_groups) sweep; do
    case "$group" in
    identity | sweep) [[ "$1" != journeys ]] || printf '%s\n' "$group" ;;
    *) [[ "$1" != checks ]] || printf '%s\n' "$group" ;;
    esac
  done
}

# One journey group on a scratch stack of its own, as the CI steps "Start the stack and migrate",
# the group's Playwright command, "Stack logs" and "Tear down the scratch stack".
journey_specs() { # journey_specs <part>: the Playwright arguments of a group (as ci.yml has them) or of one of its shards
  local group="${1%%.*}" shard=""
  [[ "$1" != *.* ]] || shard=" --shard=${1#*.}/$(shard_count "$group")"
  case "$group" in
  apps) echo "jobs.spec.ts jobs-map.spec.ts agents-runs.spec.ts agents-triage.spec.ts agents-chat.spec.ts agents-streaming.spec.ts --workers=1$shard" ;;
  sweep) echo "jobs-sweep.spec.ts --workers=1$shard" ;;
  *) echo "-c e2e/$group-all.config.ts$shard" ;;
  esac
}

# The apply group fills forms at a person's pace (browserd/tools/pacing.py), one fill at a time on
# its stack's one browser: alone it took 1135 s of a 1697 s check (2026-10-20), the longest step by
# far. A group named in JOURNEY_SHARDS (<group>:<shards>, separated by spaces) is split by
# Playwright's --shard across that many stacks side by side, each a step journeys-<group>.<i> of
# its own; with fullyParallel false (every <group>-all.config.ts) Playwright puts each spec file of
# the group in exactly one shard, so a spec added to the group runs without a list to edit here.
# A group name holds no dot: the dot separates the group from its shard.
JOURNEY_SHARDS="${LOCAL_CHECK_JOURNEY_SHARDS-apply:2}"

shard_count() { # shard_count <group>: how many stacks the group is split across, 1 when it is not
  local spec n=1
  for spec in $JOURNEY_SHARDS; do
    [[ "${spec%%:*}" != "$1" ]] || n="${spec#*:}"
  done
  [[ "$n" =~ ^[1-9][0-9]*$ ]] || n=1
  printf '%s\n' "$n"
}

journey_parts() { # journey_parts <group ...>: each group, or its shards <group>.1 to <group>.<n>
  local group n i
  for group in "$@"; do
    n="$(shard_count "$group")"
    if ((n == 1)); then
      printf '%s\n' "$group"
    else
      for i in $(seq 1 "$n"); do printf '%s.%s\n' "$group" "$i"; done
    fi
  done
}

journey_play() ( # journey_play <group> <Playwright arguments>
  # A subshell: the `cd web` stays in it, so s_journey takes its stack down from the tree root.
  # Called below `&&` in s_journey, where errexit is off: the two commands are chained.
  # shellcheck disable=SC2086
  cd web && pnpm exec playwright test $2 --reporter=list --output="test-results/$1"
)

# Starts a stack on the port picked for its group. The port is bound once more just before the
# start; when the start still fails on a port (another program took it in between), this stack,
# and only this one, is taken down and started again on another port (the mail stack also on
# another one for its fake Google, the apply stack and the opportunities stack also on another one for its fake forms
# server), three starts at most.
# A start that fails on "Pool overlaps" (a network left by a check that died) is taken down and
# started again on another network slot, within the same three starts: the bad slot stays claimed,
# and `slot`, s_journey's local, names the new one so that s_journey frees that one.
stack_up() { # stack_up <part> <project> <compose file arguments>: a group, or one shard of it
  local part="$1" project="$2" port="" out attempt=1 rc why group="${1%%.*}"
  shift 2
  out="$WORK/up-$part.log"
  [[ ! -s "$WORK/ports/$part" ]] || port="$(cat "$WORK/ports/$part")"
  while :; do
    if [[ -z "$port" ]] || ! port_free "$port"; then port="$(claim_port)" || return 1; fi
    export HTTP_PORT="$port" E2E_CADDY_URL="http://127.0.0.1:$port"
    echo "stack $project on $E2E_CADDY_URL"
    rc=0
    docker compose -p "$project" "$@" up -d --no-build --wait --wait-timeout 300 >"$out" 2>&1 || rc=$?
    cat "$out"
    ((rc != 0)) || return 0
    ((attempt < 3)) || return "$rc"
    if grep -qiE 'failed to bind|address already in use|port is already allocated' "$out"; then
      why=port
      echo "retry $attempt of 2: port $port was taken before $project started, taking it down, picking another port"
    elif grep -qiE 'pool overlaps' "$out"; then
      why=pool
      echo "retry $attempt of 2: a network already uses the subnets of slot $slot, taking $project down, picking another slot"
    else
      return "$rc"
    fi
    docker compose -p "$project" -f compose.yml -f tests/ats-stub.compose.yml down -v --remove-orphans --timeout 0 || true
    attempt=$((attempt + 1))
    if [[ "$why" == pool ]]; then # same file, new subnets: the compose arguments stay valid
      slot="$(claim_net_slot)" || return 1
      stack_networks "$part" "$slot" >/dev/null
      continue
    fi
    port=""
    if [[ "$group" == mail ]]; then # the fake Google has a loopback port of its own, taken anew too
      GMAIL_STUB_PORT="$(claim_port)" || return 1
      export GMAIL_STUB_PORT E2E_GMAIL_STUB_URL="http://127.0.0.1:$GMAIL_STUB_PORT"
    fi
    if [[ "$group" == apply || "$group" == opportunities ]]; then # so has the fake ATS forms server
      ATS_FORMS_STUB_PORT="$(claim_port)" || return 1
      export ATS_FORMS_STUB_PORT E2E_ATS_FORMS_URL="http://127.0.0.1:$ATS_FORMS_STUB_PORT"
    fi
  done
}

s_journey() { # s_journey <part> [again]: a group or one of its shards; `again` keeps the first run's stack log
  # The run again is a compose project of its own: the first run's stack may still be up (a step
  # stopped by STEP_TIMEOUT is killed before its `down`), so it is never reused.
  local part="$1" group="${1%%.*}" run="$1${2:+-$2}" rc=0 files=() specs one slot project
  project="$PREFIX-${run//./-}"
  while IFS= read -r one; do files+=("$one"); done < <(stack_files)
  slot="$(claim_net_slot)" || return 1
  files+=(-f "$(stack_networks "$part" "$slot")")
  export E2E_COMPOSE_PROJECT="$project" E2E_EVIDENCE=ci
  case "$group" in
  apps)
    python3 .github/scripts/synthetic_jobs.py "$WORK/synthetic/data"
    export E2E_JOBS_DATA="$WORK/synthetic/data"
    ;;
  identity) export E2E_ADMIN_EMAIL=e2e-admin@example.org E2E_PASSWORD_THROTTLE=1 ;;
  sweep) export E2E_SKIP_REAL_SWEEP=1 ;;
  mail)
    # The fake Google beside the stack. The browser of the journey opens its consent page, so
    # it is published on a loopback port of its own, claimed like the stack's.
    GMAIL_STUB_PORT="$(claim_port)" || return 1
    export GMAIL_STUB_PORT E2E_GMAIL_STUB_URL="http://127.0.0.1:$GMAIL_STUB_PORT"
    files+=(-f tests/gmail-stub.compose.yml)
    ;;
  apply)
    # The fake ATS forms server beside the stack (never a real job site). The browser of a
    # journey opens its pages, so it is published on a loopback port of its own, claimed like
    # the stack's.
    ATS_FORMS_STUB_PORT="$(claim_port)" || return 1
    export ATS_FORMS_STUB_PORT E2E_ATS_FORMS_URL="http://127.0.0.1:$ATS_FORMS_STUB_PORT"
    files+=(-f tests/ats-forms-stub.compose.yml)
    export COMPOSE_PROFILES=browser # the browser that fills the fake forms (apply A7's journey)
    ;;
  browser)
    # The stack's own browser, and the static pages it opens (never a real site).
    files+=(-f tests/browser-pages.compose.yml)
    export COMPOSE_PROFILES=browser
    ;;
  opportunities)
    # The fake ATS forms server the deck's postings point at (never a real job site), published
    # on a claimed loopback port as for apply; no browser: a Like only queues its application.
    ATS_FORMS_STUB_PORT="$(claim_port)" || return 1
    export ATS_FORMS_STUB_PORT E2E_ATS_FORMS_URL="http://127.0.0.1:$ATS_FORMS_STUB_PORT"
    files+=(-f tests/ats-forms-stub.compose.yml)
    ;;
  esac
  specs="$(journey_specs "$part")"
  stack_up "$part" "$project" "${files[@]}" &&
    docker compose -p "$project" -f compose.yml exec -T api alembic upgrade head &&
    journey_play "$run" "$specs" || rc=$?
  docker compose -p "$project" "${files[@]}" logs --no-color >"$LOGS/stack-$run.log" 2>&1 || true
  # The slot is free again only when its stack is gone: one that could not be taken down keeps
  # its networks, so it keeps its slot until the trap removes everything under the prefix.
  if docker compose -p "$project" -f compose.yml -f tests/ats-stub.compose.yml down -v --remove-orphans --timeout 0; then
    rmdir "$WORK/nets/$slot" 2>/dev/null || true
  else
    echo "the stack $project could not be taken down: network slot $slot stays claimed"
  fi
  return "$rc"
}

# A journey part that failed is run once more on a fresh stack, as soon as every part has run
# (beside the end of the api lane, not after it): on 2026-10-20 two gates of one batch failed on
# one journey group each (browser, then nothing but a timing test) and each cost a whole new check
# of 28 minutes, where the run again of that group alone took 3. The run again is the step
# journeys-<part>-again; when it passes, the quiet phase counts the part as passed and lists it
# under `flaky` in the summary and the record, as the flaky tests of pytest. More than
# JOURNEY_AGAIN_MAX failed parts is no flake (a broken stack, a broken change): none runs again.
JOURNEY_AGAIN_MAX=2

failed_step() { # failed_step <step>: true when that step ran and failed
  awk -F '\t' -v name="$1" '$1 == name && $2 != 0 { bad = 1 } END { exit !bad }' "$WORK/results"
}

lane_journeys() {
  local groups=() group parts=() part running=() why="" again=()
  if wanted checks; then while IFS= read -r group; do groups+=("$group"); done < <(journey_groups checks); fi
  if wanted journeys; then while IFS= read -r group; do groups+=("$group"); done < <(journey_groups journeys); fi
  ((${#groups[@]})) || return 0
  while [[ ! -f "$WORK/images.done" || ! -f "$WORK/web.done" ]]; do sleep 1; done
  [[ "$(cat "$WORK/images.done")" == 0 ]] || why="the images were not built"
  [[ "$(cat "$WORK/web.done")" == 0 ]] || why="the web dependencies were not installed"
  [[ -n "$why" ]] || run_step chromium s_chromium || why="Chromium for Playwright was not installed"
  while IFS= read -r part; do parts+=("$part"); done < <(journey_parts "${groups[@]}")
  if [[ -n "$why" ]]; then
    for part in "${parts[@]}"; do not_run "journeys-$part" "$why"; done
    return 0
  fi
  pick_journey_ports "${parts[@]}" || {
    for part in "${parts[@]}"; do not_run "journeys-$part" "no free loopback port for the stacks"; done
    return 0
  }
  for part in $(longest_first "${parts[@]}"); do
    while (($(alive "${running[@]+"${running[@]}"}") >= STACKS)); do sleep 1; done
    run_step "journeys-$part" s_journey "$part" &
    running+=($!)
  done
  wait
  for part in "${parts[@]}"; do
    if failed_step "journeys-$part"; then again+=("$part"); fi
  done
  ((${#again[@]} >= 1 && ${#again[@]} <= JOURNEY_AGAIN_MAX)) || return 0
  running=()
  for part in "${again[@]}"; do
    while (($(alive "${running[@]+"${running[@]}"}") >= STACKS)); do sleep 1; done
    run_step "journeys-$part-again" s_journey "$part" again &
    running+=($!)
  done
  wait
}

# In the quiet phase, once no lane writes $WORK/results any more: a part whose run again passed
# counts as passed, and is listed under `flaky`.
journeys_again() {
  local name part
  awk -F '\t' '$1 ~ /^journeys-.*-again$/ && $2 == 0 { print $1 }' "$WORK/results" | while IFS= read -r name; do
    part="${name%-again}"
    awk -F '\t' -v OFS='\t' -v name="$part" '$1 == name { $2 = 0 } { print }' "$WORK/results" >"$WORK/results.new"
    mv "$WORK/results.new" "$WORK/results"
    printf '%s\n' "$part" >>"$WORK/flaky"
    echo "$part: counted as passed, it failed and passed when run again on a fresh stack"
  done
}

alive() { # alive <process id ...>: how many of them still run
  local pid n=0
  for pid in "$@"; do if kill -0 "$pid" 2>/dev/null; then n=$((n + 1)); fi; done
  printf '%s\n' "$n"
}

# longest_first <group ...>: the groups by their last time in timings.csv, longest first; a group
# with no time yet comes before all (it may be the longest), and equal times keep the order given.
longest_first() {
  local i=0 group
  for group in "$@"; do
    i=$((i + 1))
    printf '%s\t%s\t%s\n' "$(awk -F , -v step="journeys-$group" '$3 == step { last = $4 } END { print (last == "" ? 999999 : last) }' "$TIMINGS" 2>/dev/null || echo 999999)" "$i" "$group"
  done | sort -t "$(printf '\t')" -k1,1nr -k2,2n | cut -f 3
}

# --- the steps: checks -----------------------------------------------------------------------------
s_precommit() {
  # As the CI step: the hooks that have a step of their own are skipped here (ruff in api, eslint
  # in lint, shellcheck and the secret scan below).
  SKIP=ruff-check,ruff-format,eslint,shellcheck,gitleaks uvx --from pre-commit==4.6.2 pre-commit run --all-files
}

# The commits after SCAN_FROM (its rule is above) up to the merged head, read as CI read the
# commits of a push (secret_scan.sh: each commit, each merge commit, the messages, then the tip
# as a whole). With no SCAN_FROM that script reads the whole history.
s_secret_scan() {
  echo "$scan_line"
  env -u SKIP GITHUB_EVENT_NAME=push CI_PUSH_BEFORE="$SCAN_FROM" \
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
  sed -n 's/^ *run: \(tests\/[A-Za-z0-9_-]*_test\.sh\)$/\1/p' .github/workflows/ci.yml
}

# The tests/*_test.sh that no step runs, each with its reason: "<file>: <why>".
shell_tests_left_out() {
  echo "tests/migrate_test.sh: run by hand, real Docker and four builds of the api image; tests/deploy_test.sh covers the same paths with fakes"
  echo "tests/backup_test.sh: run by hand from the Mac against the Jetson, which a check never touches"
}

# The selection is a step of its own: every tests/*_test.sh of the tree is either run (ci.yml has
# a line `run: <file>` for it) or left out above with a reason. A file that is neither, for
# instance one whose step is written `run: bash tests/x_test.sh` or over several lines, fails
# here: nothing is dropped silently (review of pull request #121, N2).
s_shell_tests() {
  local selected left file line why missed=0
  selected="$(shell_tests)"
  left="$(shell_tests_left_out)"
  if [[ -z "$selected" ]]; then
    echo "no step of .github/workflows/ci.yml runs a tests/*_test.sh: its lines \`run: tests/<name>_test.sh\` are the list read here"
    return 1
  fi
  for file in tests/*_test.sh; do
    [[ -e "$file" ]] || continue
    why=""
    while IFS= read -r line; do
      if [[ "$line" == "$file: "* ]]; then why="${line#*: }"; fi
    done <<<"$left"
    if grep -qxF -- "$file" <<<"$selected"; then
      echo "run       $file"
    elif [[ -n "$why" ]]; then
      echo "left out  $file: $why"
    else
      echo "MISSED    $file: no step of ci.yml runs it (a line \`run: $file\`) and shell_tests_left_out of ops/local-check.sh gives no reason to leave it out"
      missed=1
    fi
  done
  return "$missed"
}

lane_checks() {
  local script
  run_step pre-commit s_precommit || true
  run_step secret-scan s_secret_scan || true
  run_step shellcheck s_shellcheck || true
  run_step e2e-groups s_e2e_groups || true
  run_step ci-plan s_ci_plan || true
  run_step shell-tests s_shell_tests || true
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

# The steps of the set, by CI job: what a run must have run. With stub steps (the script's own
# tests) the list is the one the stub gives in stub_expected, if it defines it.
expected_steps() {
  if [[ -n "${LOCAL_CHECK_STUB:-}" ]]; then
    if declare -F stub_expected >/dev/null; then stub_expected; fi
    return 0
  fi
  if wanted api; then
    printf '%s\n' api-install api-services ruff pytest pytest-alone coverage pytest-docker scoring
  fi
  if wanted checks || wanted journeys; then printf '%s\n' web-install images chromium; fi
  if wanted checks; then
    printf '%s\n' pre-commit secret-scan shellcheck e2e-groups ci-plan shell-tests
    # shellcheck disable=SC2046 # group names are single words
    journey_parts $(journey_groups checks) | sed 's/^/journeys-/'
    (cd "$TREE" && shell_tests) | sed 's/^tests\///; s/\.sh$//'
  fi
  if wanted journeys; then
    printf '%s\n' lint typecheck build vitest
    # shellcheck disable=SC2046 # group names are single words
    journey_parts $(journey_groups journeys) | sed 's/^/journeys-/'
  fi
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

[[ -s "$TIMINGS" ]] || printf 'check,tree,step,seconds,result\n' >"$TIMINGS"
say "local check $CHECK_ID: base $BASE ($base_sha), $(wc -l <"$WORK/merges" | tr -d ' ') merged, tree $tree_sha"
say "logs: $LOGS"
say "$scan_line"
if [[ -n "${LOCAL_CHECK_STUB:-}" ]]; then
  stub_plan
else
  say "pytest -n $JOBS, vitest on $WEB_JOBS, $STACKS journey stacks side by side, $free_gb GiB free"
  real_plan
fi

# --- the result ------------------------------------------------------------------------------------
# Every step of the set must have run, as CI had "Every step the plan needs has run": a step
# that a lane left out, or that a change to this script dropped, is a failed step, so the check
# fails and no record is written (review of pull request #121, N2).
: >>"$WORK/results"
expected="$(expected_steps)" || {
  not_run expected-steps "the list of the steps of the set could not be read"
  expected=""
}
if [[ -z "$expected" && -z "${LOCAL_CHECK_STUB:-}" ]]; then
  not_run expected-steps "the list of the steps of the set is empty"
fi
while IFS= read -r name; do
  [[ -n "$name" ]] || continue
  awk -F '\t' -v name="$name" '$1 == name { found = 1 } END { exit !found }' "$WORK/results" ||
    not_run "$name" "a step of the set that did not run"
done <<<"$expected"
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
  timing total 1 "$total"
  say "LOCAL CHECK: FAIL $tree_sha $failed"
  exit 1
fi

# The last line says what ran: "LOCAL CHECK: PASS" is kept for the full set with the real steps
# (review of pull request #121, B2: a partial run and a stubbed run ended with that same line).
say "logs: $LOGS"
kind="" note=""
if [[ -n "${LOCAL_CHECK_STUB:-}" ]]; then
  kind="STUB "
  note=", stub steps: not a check"
fi
if ((${#ONLY[@]})); then
  kind="${kind}PARTIAL "
  only="${ONLY[*]}"
  note=", only ${only// /,}, no record: not a proof for a merge or a deploy$note"
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
    "flaky": (work / "flaky").read_text().split() if (work / "flaky").exists() else [],
}
out.write_text(json.dumps(record, indent=2) + "\n")
PY
  say "record: $RECORDS/$tree_sha.json"
fi
[[ ! -s "$WORK/flaky" ]] || say "flaky: $(tr '\n' ' ' <"$WORK/flaky")"
timing total 0 "$total"
say "LOCAL CHECK: ${kind}PASS $tree_sha ${total}s$note"
