#!/usr/bin/env bash
# Tests of the batch check and of the checked deploy (ops/local-check.sh, ops/deploy-checked.sh,
# issue #116), on throwaway repositories: a bare "origin", a clone that holds the two scripts, and
# stub steps given through LOCAL_CHECK_STUB, so the whole file runs in seconds without the real
# suites. gh and curl are fakes on PATH: nothing is dispatched, nothing leaves this machine.
#
#   tests/local_check_test.sh
#
# With a Docker daemon it also starts a container, a volume and a bystander container to show
# what the cleanup removes and what it leaves; without one that part is skipped and says so.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
WORK="$(cd "$WORK" && pwd -P)"
BYSTANDER="jobapp-lctest-bystander-$$"
IMAGE="redis:7.4.11-alpine"
with_docker=0
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then with_docker=1; fi

cleanup() {
  if ((with_docker)); then docker rm -f "$BYSTANDER" >/dev/null 2>&1 || true; fi
  rm -rf "$WORK"
}
trap cleanup EXIT

passed=0
ok() {
  passed=$((passed + 1))
  printf 'ok   %s\n' "$*"
}
fail() {
  printf 'FAIL %s\n' "$*" >&2
  exit 1
}
run() { # run <expected exit> <command ...>: output in $WORK/out
  local want="$1" got=0
  shift
  "$@" >"$WORK/out" 2>&1 || got=$?
  [[ "$got" == "$want" ]] || fail "exit $got, expected $want: $* -- $(tail -c 800 "$WORK/out")"
  # Every check of this file runs stub steps: none may print the line that proves a real, full pass.
  ! grep -q '^LOCAL CHECK: PASS' "$WORK/out" || fail "a run with stub steps printed LOCAL CHECK: PASS: $*"
}
out_has() { grep -qF -- "$1" "$WORK/out" || fail "output lacks '$1': $(tail -c 800 "$WORK/out")"; }
out_lacks() { ! grep -qF -- "$1" "$WORK/out" || fail "output holds '$1': $(tail -c 800 "$WORK/out")"; }
last_line() { tail -n 1 "$WORK/out"; }

# --- the repositories ------------------------------------------------------------------------------
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.org GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.org
ORIGIN="$WORK/origin.git"
REPO="$WORK/repo"
export TMPDIR="$WORK/tmp"
# The seconds an interrupted check gives its steps to end on SIGINT before it stops them.
export LOCAL_CHECK_INT_GRACE=1
mkdir -p "$TMPDIR"
git init -q --bare -b main "$ORIGIN"
git clone -q "$ORIGIN" "$REPO" 2>/dev/null
mkdir -p "$REPO/ops"
cp "$ROOT/ops/local-check.sh" "$ROOT/ops/deploy-checked.sh" "$REPO/ops/"
printf '.local-check/\n' >"$REPO/.gitignore"
printf 'one\n' >"$REPO/file.txt"
git -C "$REPO" add -A
git -C "$REPO" commit -q -m "base"
git -C "$REPO" branch -M main
git -C "$REPO" push -q origin main
branch() { # branch <name> <file> <content>: a branch of main with one commit, pushed
  git -C "$REPO" checkout -q -b "$1" main
  mkdir -p "$(dirname "$REPO/$2")"
  printf '%s\n' "$3" >"$REPO/$2"
  git -C "$REPO" add -A
  git -C "$REPO" commit -q -m "$1"
  git -C "$REPO" push -q origin "$1"
  git -C "$REPO" checkout -q main
}
branch feature-a a.txt "a"
branch feature-b b.txt "b"
branch conflict-a file.txt "two"
branch conflict-b file.txt "three"
# The folders and files the real step functions read: s_ruff does `cd api`, s_shell_tests reads
# tests/*_test.sh and the `run:` lines of ci.yml. ci.yml runs a_test.sh only: b_test.sh is
# neither run nor left out with a reason, migrate_test.sh is left out by the script.
git -C "$REPO" checkout -q -b shape main
mkdir -p "$REPO/api" "$REPO/tests" "$REPO/.github/workflows"
: >"$REPO/api/.keep"
for name in a b migrate; do printf '#!/bin/sh\necho %s\n' "$name" >"$REPO/tests/${name}_test.sh"; done
printf 'jobs:\n  checks:\n    steps:\n      - name: A\n        run: tests/a_test.sh\n' >"$REPO/.github/workflows/ci.yml"
git -C "$REPO" add -A
git -C "$REPO" commit -q -m "shape"
git -C "$REPO" push -q origin shape
printf '      - name: B\n        run: tests/b_test.sh\n' >>"$REPO/.github/workflows/ci.yml"
git -C "$REPO" checkout -q -b shape-whole
git -C "$REPO" commit -q -a -m "shape-whole"
git -C "$REPO" push -q origin shape-whole
# A base of more than 50 commits, none of them checked.
git -C "$REPO" checkout -q -b long main
for n in $(seq 1 51); do
  printf '%s\n' "$n" >"$REPO/long.txt"
  git -C "$REPO" add -A
  git -C "$REPO" commit -q -m "long $n"
done
git -C "$REPO" push -q origin long
git -C "$REPO" checkout -q main

CHECK="$REPO/ops/local-check.sh"
DEPLOY="$REPO/ops/deploy-checked.sh"
RECORDS="$REPO/.local-check"

cat >"$WORK/pass.sh" <<'EOF'
stub_plan() {
  run_step one true || true
  run_step two sh -c 'echo two' || true
}
EOF
cat >"$WORK/fail.sh" <<'EOF'
stub_plan() {
  run_step good true || true
  run_step broken sh -c 'echo "the reason it broke"; exit 7' || true
  run_step after true || true
}
EOF
# A step that lasts: it says where the check is (prefix of its Docker names) and waits.
cat >"$WORK/slow.sh" <<'EOF'
stub_plan() {
  if [[ -n "${STUB_IMAGE:-}" ]]; then
    docker run -d --rm --name "$PREFIX-stub" --label "jobapp.local-check=$CHECK_ID" "$STUB_IMAGE" >/dev/null
    docker volume create "${PREFIX}_data" >/dev/null
  fi
  printf '%s\n' "$PREFIX" >"$STUB_MARK"
  run_step slow sleep "$STUB_SLEEP" || true
}
EOF

nothing_left() { # nothing_left <what>: no lock, no worktree, no temporary folder
  [[ ! -e "$RECORDS/lock" ]] || fail "$1: the lock is still there"
  [[ "$(git -C "$REPO" worktree list | wc -l | tr -d ' ')" == 1 ]] || fail "$1: a worktree is left: $(git -C "$REPO" worktree list)"
  [[ -z "$(ls -A "$TMPDIR")" ]] || fail "$1: the temporary folder is left: $(ls -A "$TMPDIR")"
}
records() { find "$RECORDS" -maxdepth 1 -name '*.json' 2>/dev/null | wc -l | tr -d ' '; }
wait_for() { # wait_for <file>: at most 30 s
  local tries
  for tries in $(seq 1 300); do
    [[ -s "$1" ]] && return 0
    sleep 0.1
  done
  fail "$1 did not appear ($tries tries)"
}

# --- arguments -------------------------------------------------------------------------------------
run 64 "$CHECK" --only web
out_has "--only takes api, checks or journeys"
run 64 "$CHECK" --jobs 0
run 64 "$CHECK" --merge
run 64 "$CHECK" --base
run 64 "$CHECK" --frobnicate
out_has "unknown argument: --frobnicate"
run 0 "$CHECK" --help
out_has "ops/local-check.sh --merge"
run 64 "$DEPLOY" one two
run 64 "$DEPLOY" --frobnicate
run 4 env LOCAL_CHECK_STUB="$WORK/pass.sh" "$CHECK" --base origin/no-such-branch
out_has "names no commit"
run 4 env LOCAL_CHECK_STUB="$WORK/pass.sh" "$CHECK" --merge origin/no-such-branch
nothing_left "a ref that does not exist"
ok "wrong arguments are exit 64 with the usage, a ref that names nothing is exit 4, nothing is left behind"

# --- the disk floor --------------------------------------------------------------------------------
run 3 env LOCAL_CHECK_STUB="$WORK/pass.sh" LOCAL_CHECK_MIN_FREE_GB=99999999 "$CHECK"
out_has "GiB free"
out_has "under the 99999999 GiB"
out_lacks "LOCAL CHECK: PASS"
nothing_left "the disk refusal"
[[ "$(records)" == 0 ]] || fail "the disk refusal wrote a record"
ok "under the free disk floor the check refuses to start (exit 3) and says so"

# --- a merge conflict ------------------------------------------------------------------------------
run 2 env LOCAL_CHECK_STUB="$WORK/pass.sh" "$CHECK" --merge origin/conflict-a --merge origin/conflict-b
out_has "MERGE CONFLICT: origin/conflict-b"
out_has "file.txt"
[[ "$(last_line)" =~ ^LOCAL\ CHECK:\ FAIL\ [0-9a-f]{40}\ merge-conflict$ ]] || fail "last line of a conflict: $(last_line)"
nothing_left "a merge conflict"
[[ "$(records)" == 0 ]] || fail "a merge conflict wrote a record"
ok "a merge conflict names the ref and the file, is exit 2, and cleans up"

# --- a failing step --------------------------------------------------------------------------------
base_tree="$(git -C "$REPO" rev-parse 'origin/main^{tree}')"
run 1 env LOCAL_CHECK_STUB="$WORK/fail.sh" "$CHECK"
out_has "broken                     FAIL"
out_has "good                       PASS"
out_has "after                      PASS"
out_has "the reason it broke"
[[ "$(last_line)" == "LOCAL CHECK: FAIL $base_tree broken" ]] || fail "last line of a failure: $(last_line)"
nothing_left "a failing step"
[[ "$(records)" == 0 ]] || fail "a failing check wrote a record"
out_has "secret scan: 1 commits, the whole history up to $(git -C "$REPO" rev-parse origin/main) (no commit of origin/main has a PASS record and it has fewer than 50)"
logs="$(sed -n 's/^logs: //p' "$WORK/out" | head -n 1)"
grep -q "the reason it broke" "$logs/broken.log" || fail "the full log of the failed step is not kept in $logs"
run 1 env LOCAL_CHECK_STUB="$WORK/fail.sh" "$CHECK" --base origin/long
out_has "secret scan: 50 commits, after $(git -C "$REPO" rev-parse 'origin/long~50') up to $(git -C "$REPO" rev-parse origin/long) (no commit in the last 200 of origin/long has a PASS record: from 50 commits before it)"
ok "a failing step is exit 1 with the step named, the end of its log shown, its log kept, and no record"

# --- the step watcher (issue #367) -----------------------------------------------------------------
# The stub's kill_tree lets the watcher run on after its sleep is stopped, the race that once wrote
# "ran longer than" into the log of a step that passed. A fake sleep orders step and watcher through
# a fifo, no clock: the watcher sleeps 7319 s (over at once with WATCH_TIME_UP=1), the step 113 s.
mkdir -p "$WORK/watchbin"
mkfifo "$WORK/watch.fifo"
real_sleep="$(command -v sleep)"
cat >"$WORK/watchbin/sleep" <<EOF
#!/usr/bin/env bash
case "\$1:\${WATCH_TIME_UP:-}" in
7319:1)
  read -r _ <"$WORK/watch.fifo"
  exit 0
  ;;
7319:*) printf 'the watcher sleeps\n' >"$WORK/watch.fifo" ;;
113:*) printf 'the step sleeps\n' >"$WORK/watch.fifo" ;;
esac
exec -a "$WORK/watchbin/sleep" "$real_sleep" "\$@"
EOF
chmod +x "$WORK/watchbin/sleep"
cat >"$WORK/watch.sh" <<'EOF'
kill_tree() { # the worst order: the children stopped, the parent left to run to its end, then stopped
  local pid
  for pid in $(descendants "$1"); do kill -TERM "$pid" 2>/dev/null || true; done
  wait "$1" 2>/dev/null || true
  kill -TERM "$1" 2>/dev/null || true
}
s_quick() {
  read -r _ <"$WATCH_FIFO"
  echo "the step's own line"
}
s_long() {
  echo "the step's own line"
  sleep 113
  echo "the step ended by itself"
}
stub_plan() {
  if [[ "${WATCH_TIME_UP:-}" == 1 ]]; then
    run_step long s_long || true
  else
    run_step quick s_quick || true
  fi
}
EOF
# The check on the bash of PATH, and on /bin/bash (3.2 on macOS).
shells=("")
[[ ! -x /bin/bash ]] || shells+=(/bin/bash)
watch_env=(PATH="$WORK/watchbin:$PATH" WATCH_FIFO="$WORK/watch.fifo" LOCAL_CHECK_STEP_TIMEOUT=7319 LOCAL_CHECK_STUB="$WORK/watch.sh")
for shell in "${shells[@]}"; do
  # shellcheck disable=SC2086
  run 0 env "${watch_env[@]}" $shell "$CHECK" --only api
  grep -qE '^quick +PASS' "$WORK/out" || fail "${shell:-bash of PATH}: the quick step is not PASS: $(cat "$WORK/out")"
  logs="$(sed -n 's/^logs: //p' "$WORK/out" | head -n 1)"
  [[ "$(cat "$logs/quick.log")" == "the step's own line" ]] ||
    fail "${shell:-bash of PATH}: the watcher of a step that ended first wrote into its log: $(cat "$logs/quick.log")"
  # shellcheck disable=SC2086
  run 1 env "${watch_env[@]}" WATCH_TIME_UP=1 $shell "$CHECK" --only api
  grep -qE '^long +FAIL' "$WORK/out" || fail "${shell:-bash of PATH}: the step past its time is not FAIL: $(cat "$WORK/out")"
  logs="$(sed -n 's/^logs: //p' "$WORK/out" | head -n 1)"
  # Bash may add its "Terminated" line for the step's sleep after the reason; it must not cover it.
  [[ "$(head -n 3 "$logs/long.log")" == "the step's own line"$'\n\n'"ops/local-check.sh: step long ran longer than 7319 s, stopped" ]] ||
    fail "${shell:-bash of PATH}: the step past its time was not stopped with its reason: $(cat "$logs/long.log")"
  ! grep -qF "the step ended by itself" "$logs/long.log" || fail "${shell:-bash of PATH}: the step past its time ran on"
  ! pgrep -f "$WORK/watchbin/sleep" >/dev/null || fail "${shell:-bash of PATH}: a sleep of the watcher or of the step is still running"
done
nothing_left "the step watcher"
ok "the watcher of a step that ends first writes nothing into its log, even when it runs on after its sleep is stopped; a step past its time is stopped, FAIL, and its log says why, a line the step writes after it covering nothing"

# --- the ports of the journey stacks ---------------------------------------------------------------
# The stub gives the real s_journey a fake docker whose first `compose up` fails the way the
# daemon answered in the second full run ("failed to bind port 127.0.0.1:<port>/tcp"): that
# stack is taken down and started again on another port, one line says so, three starts at most.
# Four groups claim their ports up front, and no two are the same.
cat >"$WORK/ports.sh" <<'EOF'
docker() {
  echo "docker $*" >>"$STUB_CALLS"
  case " $* " in
  *" up "*)
    echo "up $HTTP_PORT" >>"$STUB_CALLS"
    if [[ "$(grep -c '^up ' "$STUB_CALLS")" -le "$STUB_UP_FAILS" ]]; then
      echo "Error response from daemon: driver failed programming external connectivity on endpoint x: failed to bind port 127.0.0.1:$HTTP_PORT/tcp: address already in use" >&2
      return 1
    fi
    ;;
  esac
  return 0
}
journey_play() { true; }
stub_plan() {
  pick_journey_ports apps ingestion identity sweep
  cat "$WORK/ports/apps" "$WORK/ports/ingestion" "$WORK/ports/identity" "$WORK/ports/sweep" >"$STUB_CALLS.picked"
  run_step journeys-sweep s_journey sweep || true
}
EOF
export STUB_CALLS="$WORK/stack-calls"
ups() { grep -c '^up ' "$STUB_CALLS" || true; }
downs() { grep -c ' down ' "$STUB_CALLS" || true; }

: >"$STUB_CALLS"
run 0 env LOCAL_CHECK_STUB="$WORK/ports.sh" STUB_UP_FAILS=0 "$CHECK" --only journeys
out_has "journeys-sweep             PASS"
[[ "$(ups)" == 1 && "$(downs)" == 1 ]] || fail "a stack that starts at once: $(ups) starts, $(downs) teardowns"
[[ "$(wc -l <"$STUB_CALLS.picked" | tr -d ' ')" == 4 && -z "$(sort "$STUB_CALLS.picked" | uniq -d)" ]] ||
  fail "the four stacks were given ports that are not four different ones: $(tr '\n' ' ' <"$STUB_CALLS.picked")"

: >"$STUB_CALLS"
run 0 env LOCAL_CHECK_STUB="$WORK/ports.sh" STUB_UP_FAILS=1 "$CHECK" --only journeys
out_has "journeys-sweep             PASS"
logs="$(sed -n 's/^logs: //p' "$WORK/out" | head -n 1)"
[[ "$(ups)" == 2 && "$(downs)" == 2 ]] || fail "one failed start: $(ups) starts, $(downs) teardowns (expected 2 and 2)"
[[ "$(grep -c '^retry ' "$logs/journeys-sweep.log")" == 1 ]] || fail "one retry should leave one line in the log of the step"
[[ "$(grep '^up ' "$STUB_CALLS" | sort -u | wc -l | tr -d ' ')" == 2 ]] || fail "the second start used the port that was taken"

: >"$STUB_CALLS"
run 1 env LOCAL_CHECK_STUB="$WORK/ports.sh" STUB_UP_FAILS=3 "$CHECK" --only journeys
out_has "journeys-sweep             FAIL"
[[ "$(ups)" == 3 ]] || fail "a port that is always taken: $(ups) starts, expected 3"
ok "a stack that cannot bind its port is taken down and started on another, three starts at most, and four stacks hold four different ports"

# The mail group also publishes the fake Google on a port of its own: when that one is taken, the
# retry changes it too, instead of failing the three starts on the same stub port.
cat >"$WORK/ports-mail.sh" <<'EOF'
docker() {
  echo "docker $*" >>"$STUB_CALLS"
  case " $* " in
  *" up "*)
    echo "up $HTTP_PORT $GMAIL_STUB_PORT $E2E_GMAIL_STUB_URL" >>"$STUB_CALLS"
    if [[ "$(grep -c '^up ' "$STUB_CALLS")" -le "$STUB_UP_FAILS" ]]; then
      echo "Error response from daemon: driver failed programming external connectivity on endpoint x: failed to bind port 127.0.0.1:$GMAIL_STUB_PORT/tcp: address already in use" >&2
      return 1
    fi
    ;;
  esac
  return 0
}
journey_play() { true; }
stub_plan() {
  pick_journey_ports mail
  run_step journeys-mail s_journey mail || true
}
EOF
: >"$STUB_CALLS"
run 0 env LOCAL_CHECK_STUB="$WORK/ports-mail.sh" STUB_UP_FAILS=1 "$CHECK" --only journeys
out_has "journeys-mail              PASS"
[[ "$(ups)" == 2 ]] || fail "mail, one failed start: $(ups) starts, expected 2"
[[ "$(grep '^up ' "$STUB_CALLS" | awk '{print $2}' | sort -u | wc -l | tr -d ' ')" == 2 ]] || fail "the mail stack kept its port"
[[ "$(grep '^up ' "$STUB_CALLS" | awk '{print $3}' | sort -u | wc -l | tr -d ' ')" == 2 ]] || fail "the retry of the mail stack kept the port of the fake Google"
while read -r _ _ stub url; do
  [[ "$url" == "http://127.0.0.1:$stub" ]] || fail "the address of the fake Google ($url) does not follow its port ($stub)"
done < <(grep '^up ' "$STUB_CALLS")
ok "the retry of the mail stack takes a new port for the fake Google too"

# The apply group publishes the fake ATS forms server on a port of its own, taken anew too.
sed -e 's/GMAIL_STUB_PORT/ATS_FORMS_STUB_PORT/g' -e 's/E2E_GMAIL_STUB_URL/E2E_ATS_FORMS_URL/g' \
  -e 's/journeys-mail/journeys-apply/' -e 's/pick_journey_ports mail/pick_journey_ports apply/' \
  -e 's/s_journey mail/s_journey apply/' "$WORK/ports-mail.sh" >"$WORK/ports-apply.sh"
: >"$STUB_CALLS"
run 0 env LOCAL_CHECK_STUB="$WORK/ports-apply.sh" STUB_UP_FAILS=1 "$CHECK" --only journeys
out_has "journeys-apply             PASS"
[[ "$(ups)" == 2 ]] || fail "apply, one failed start: $(ups) starts, expected 2"
[[ "$(grep '^up ' "$STUB_CALLS" | awk '{print $3}' | sort -u | wc -l | tr -d ' ')" == 2 ]] || fail "the retry of the apply stack kept the port of the fake forms server"
while read -r _ _ stub url; do
  [[ "$url" == "http://127.0.0.1:$stub" ]] || fail "the address of the fake forms server ($url) does not follow its port ($stub)"
done < <(grep '^up ' "$STUB_CALLS")
grep -q 'tests/ats-forms-stub.compose.yml' "$STUB_CALLS" || fail "the apply stack did not include the fake forms server"
ok "the retry of the apply stack takes a new port for the fake forms server too"

# --- the journey groups are found in web/e2e (issue #279) -------------------------------------------
# A tree whose web/e2e holds the names of this repository's group configs and spec files (empty
# files), one group config more (zeta) and files that are no group config. The real lane_journeys
# and expected_steps run on it, with stub steps for Chromium and the stacks.
git -C "$REPO" checkout -q -b groups origin/shape
mkdir -p "$REPO/web/e2e/sub"
for path in "$ROOT"/web/e2e/*-all.config.ts "$ROOT"/web/e2e/*.spec.ts; do : >"$REPO/web/e2e/${path##*/}"; done
: >"$REPO/web/e2e/zeta-all.config.ts"
for decoy in other.config.ts zeta-all.config.ts.bak sub/inner-all.config.ts all.config.ts; do : >"$REPO/web/e2e/$decoy"; done
git -C "$REPO" add -A
git -C "$REPO" commit -q -m "groups"
git -C "$REPO" push -q origin groups
git -C "$REPO" checkout -q main
cat >"$WORK/groups.sh" <<'EOF'
s_chromium() { true; }
s_journey() { journey_specs "$1" >>"$STUB_CALLS.specs"; }
stub_plan() {
  STACKS=2
  echo 0 >"$WORK/images.done"
  echo 0 >"$WORK/web.done"
  lane_journeys
  { journey_groups checks; echo --; journey_groups journeys; } >"$STUB_CALLS.groups"
  LOCAL_CHECK_STUB="" expected_steps >"$STUB_CALLS.expected"
}
EOF
: >"$STUB_CALLS.specs"
run 0 env LOCAL_CHECK_STUB="$WORK/groups.sh" "$CHECK" --base origin/groups --only checks --only journeys
real_groups="$(for path in "$ROOT"/web/e2e/*-all.config.ts; do name="${path##*/}"; echo "${name%-all.config.ts}"; done)"
[[ -n "$real_groups" ]] || fail "this repository has no web/e2e/*-all.config.ts"
want_checks="$(printf '%s\n' apps; printf '%s\nzeta\n' "$real_groups" | grep -vxF identity | LC_ALL=C sort)"
want_journeys="$(printf '%s\n' "$real_groups" | grep -xF identity || true; echo sweep)"
[[ "$(cat "$STUB_CALLS.groups")" == "$want_checks"$'\n--\n'"$want_journeys" ]] ||
  fail "the journey groups found: $(tr '\n' ' ' <"$STUB_CALLS.groups"), expected $(printf '%s -- %s' "$want_checks" "$want_journeys" | tr '\n' ' ')"
while IFS= read -r group; do
  [[ "$group" == -- ]] && continue
  out_has "journeys-$group"
  grep -qxF "journeys-$group" "$STUB_CALLS.expected" || fail "journeys-$group is not a step of the set"
  case "$group" in apps | sweep) ;; *)
    grep -qxF -- "-c e2e/$group-all.config.ts" "$STUB_CALLS.specs" || fail "the group $group does not run its config"
    git -C "$REPO" cat-file -e "groups:web/e2e/$group-all.config.ts" || fail "the group $group has no config"
    ;;
  esac
done <"$STUB_CALLS.groups"
[[ "$(grep -c '^journeys-' "$STUB_CALLS.expected")" == "$(grep -cvxF -- -- "$STUB_CALLS.groups")" ]] ||
  fail "the set holds journey steps of no group: $(grep '^journeys-' "$STUB_CALLS.expected" | tr '\n' ' ')"
[[ "$(wc -l <"$STUB_CALLS.specs" | tr -d ' ')" == "$(grep -cvxF -- -- "$STUB_CALLS.groups")" ]] || fail "not one stack per group: $(tr '\n' ' ' <"$STUB_CALLS.specs")"
# The decoys (other.config.ts, a .bak, a config in a subfolder) are no group: the list above is exact.
# Each spec a group names one by one (apps, sweep) is a file of web/e2e.
while IFS= read -r spec; do
  [[ -f "$ROOT/web/e2e/$spec" ]] || fail "a journey group runs $spec, which is not in web/e2e"
done < <(grep -o '[a-z0-9-]*\.spec\.ts' "$STUB_CALLS.specs")
grep -vxF -- "-c e2e/zeta-all.config.ts" "$STUB_CALLS.specs" >"$WORK/gate-specs" # this repository's groups only
ok "every web/e2e/<group>-all.config.ts is a journey group of its own step, and every group has its config or its spec files"

# --- the journey stacks: longest first, a freed slot starts the next one (issue #280) ---------------
# Four groups on two slots. timings.csv says long took 300 s and apps 100 s, short 10 s (its last
# line); fresh was never timed. The order must be fresh, long, apps, short, and long waits until
# short has started: with batches of STACKS (start two, wait for both, as before) short never
# starts while long runs, and long gives up after 20 s and fails.
cat >"$WORK/pool.sh" <<'EOF'
s_chromium() { true; }
journey_groups() { [[ "$1" != checks ]] || printf '%s\n' apps short long fresh; }
s_journey() {
  echo "$1" >>"$STUB_CALLS.started"
  [[ "$1" == long ]] || return 0
  local tries=0
  until grep -qx short "$STUB_CALLS.started"; do
    ((tries++ < 200)) || return 1
    sleep 0.1
  done
}
stub_plan() {
  STACKS=2
  echo 0 >"$WORK/images.done"
  echo 0 >"$WORK/web.done"
  lane_journeys
}
EOF
printf '%s\n' x,t,journeys-short,500,PASS x,t,journeys-long,300,PASS x,t,journeys-apps,100,PASS x,t,journeys-short,10,FAIL >>"$RECORDS/timings.csv"
: >"$STUB_CALLS.started"
run 0 env LOCAL_CHECK_STUB="$WORK/pool.sh" "$CHECK" --only checks
# fresh and long start together on the two slots: which one writes its line first is a race.
[[ "$({ head -n 2 "$STUB_CALLS.started" | sort; tail -n +3 "$STUB_CALLS.started"; } | tr '\n' ' ')" == "fresh long apps short " ]] ||
  fail "the journey stacks started in the order $(tr '\n' ' ' <"$STUB_CALLS.started"), expected fresh long apps short"
out_has "journeys-long              PASS"
ok "the journey stacks start longest first by their last time (an untimed one first), and a freed slot starts the next at once"

# --- timings.csv -----------------------------------------------------------------------------------
check_id_of_out() { sed -n 's/^local check \(lc[0-9]*-[0-9]*\):.*/\1/p' "$WORK/out"; }
tree_of_out() { sed -n 's/^local check .*, tree \([0-9a-f]*\)$/\1/p' "$WORK/out"; }
check_id="$(check_id_of_out)"
tree="$(tree_of_out)"
[[ -n "$check_id" && -n "$tree" ]] || fail "no check id or tree in the output"
[[ "$(head -n 1 "$RECORDS/timings.csv")" == "check,tree,step,seconds,result" ]] || fail "timings.csv has no header: $(head -n 1 "$RECORDS/timings.csv")"
for step in journeys-fresh journeys-long journeys-apps journeys-short total; do
  grep -qE "^$check_id,$tree,$step,[0-9]+,PASS\$" "$RECORDS/timings.csv" || fail "timings.csv has no PASS line for $step of $check_id: $(grep "^$check_id," "$RECORDS/timings.csv")"
done
run 1 env LOCAL_CHECK_STUB="$WORK/fail.sh" "$CHECK"
check_id="$(check_id_of_out)"
tree="$(tree_of_out)"
grep -qE "^$check_id,$tree,broken,[0-9]+,FAIL\$" "$RECORDS/timings.csv" || fail "the failed step is not a FAIL line of timings.csv"
grep -qE "^$check_id,$tree,good,[0-9]+,PASS\$" "$RECORDS/timings.csv" || fail "the passed step is not a PASS line of timings.csv"
grep -qE "^$check_id,$tree,total,[0-9]+,FAIL\$" "$RECORDS/timings.csv" || fail "the failed run has no total line"
[[ "$(grep -c '^check,' "$RECORDS/timings.csv")" == 1 ]] || fail "timings.csv has its header more than once"
ok "timings.csv: one line per step and one for the total (check id, tree, step, seconds, PASS or FAIL), across runs"

# --- the docker marked tests run on the images the step images built (issue #280) -----------------
# The real s_pytest_docker with a fake uv that writes what it was given: the three image names
# when the images were built, none when they were not (the tests then build their own), and two
# processes, one file each.
mkdir -p "$WORK/fakeuv"
cat >"$WORK/fakeuv/uv" <<'EOF'
#!/usr/bin/env bash
printf 'parser=%s renderer=%s browser=%s args=%s\n' "${PARSER_TEST_IMAGE:-}" "${RENDERER_TEST_IMAGE:-}" "${BROWSER_TEST_IMAGE:-}" "$*"
EOF
chmod +x "$WORK/fakeuv/uv"
cat >"$WORK/docker-tests.sh" <<'EOF'
stub_plan() {
  mkdir -p "$TREE/api"
  : >"$WORK/api.env"
  echo 0 >"$WORK/images.done"
  run_step docker-built s_pytest_docker || true
  echo 1 >"$WORK/images.done"
  run_step docker-own s_pytest_docker || true
}
EOF
run 0 env PATH="$WORK/fakeuv:$PATH" LOCAL_CHECK_STUB="$WORK/docker-tests.sh" "$CHECK" --only api
check_id="$(check_id_of_out)"
built="$(cat "$RECORDS/logs/$check_id/docker-built.log")"
[[ "$built" == "parser=jobapp-$check_id-worker:latest renderer=jobapp-$check_id-renderer:latest browser=jobapp-$check_id-browser:latest args=run --frozen pytest -m docker -p no:cacheprovider -n 2 --dist loadfile --durations=10" ]] ||
  fail "the docker marked tests were not given the images of the check: $built"
own="$(cat "$RECORDS/logs/$check_id/docker-own.log")"
[[ "$own" == "parser= renderer= browser= args=run --frozen pytest -m docker -p no:cacheprovider -n 2 --dist loadfile --durations=10" ]] ||
  fail "with no images built, the docker marked tests should build their own: $own"
grep -q '^  browser:$' <(sed -n '/^s_images()/,/^}/p' "$ROOT/ops/local-check.sh") || fail "images.yml names no browser image"
grep -qE 'build api worker caddy renderer browser$' "$ROOT/ops/local-check.sh" || fail "the step images does not build the browser"
ok "the docker marked tests use the images the check built (worker, renderer, browser), else their own, on -n 2 by file"

# --- the networks of the journey stacks: subnets of their own (issue #280) -------------------------
# Twelve stacks claim slots: their 60 networks get distinct /27 subnets of 10.213.0.0/16, outside
# Docker's predefined pools, and a slot freed by a stack that ended is taken again.
cat >"$WORK/nets.sh" <<'EOF'
docker() { return 0; } # no network of the daemon is in use
stub_plan() {
  local group slot
  for group in a b c d e f g h i j k l; do
    slot="$(claim_net_slot)"
    stack_networks "$group" "$slot" >/dev/null
  done
  cat "$WORK"/networks-*.yml >"$STUB_CALLS.nets"
  rmdir "$WORK/nets/3"
  claim_net_slot >"$STUB_CALLS.reused"
  run_step nets true || true
}
EOF
run 0 env LOCAL_CHECK_STUB="$WORK/nets.sh" "$CHECK" --only checks
subnets="$(sed -n 's/^ *- subnet: //p' "$STUB_CALLS.nets")"
[[ "$(wc -l <<<"$subnets" | tr -d ' ')" == 60 ]] || fail "not five subnets per stack: $subnets"
[[ "$(sort -u <<<"$subnets" | wc -l | tr -d ' ')" == 60 ]] || fail "two networks share a subnet: $(sort <<<"$subnets" | uniq -d)"
if grep -vE '^10\.213\.([0-9]|[1-9][0-9]|1[0-9][0-9]|2[0-4][0-9])\.0/27$' <<<"$subnets"; then fail "a subnet outside 10.213.0.0/16"; fi
[[ "$(grep -c '^  [a-z]*:$' "$STUB_CALLS.nets")" == 60 ]] || fail "the overrides do not name the five networks of each stack"
[[ "$(cat "$STUB_CALLS.reused")" == 3 ]] || fail "a freed slot was not taken again: $(cat "$STUB_CALLS.reused")"
ok "each journey stack's networks get /27 subnets of their own out of 10.213.0.0/16, a freed slot is reused"

# --- the network slots of the journey stacks --------------------------------------------------------
# The real s_journey and journey_play, with a fake docker and a fake pnpm that note the folder they
# run in. Like the real one, the fake `docker compose` needs compose.yml in that folder. Two groups
# run one after the other, so the second shows which slot the first left claimed.
cat >"$WORK/slots.sh" <<'EOF2'
docker() {
  local f
  echo "docker $PWD $*" >>"$STUB_CALLS"
  case " $* " in
  *" network ls "*) [[ -z "${STUB_SUBNETS:-}" ]] || echo net-a ;;
  *" network inspect "*) printf '%s \n' "${STUB_SUBNETS:-}" ;;
  *" compose "*)
    if [[ ! -f compose.yml ]]; then
      echo "open $PWD/compose.yml: no such file or directory" >&2
      return 1
    fi
    case " $* " in
    *" up "*)
      for f in "$@"; do [[ "$f" != */networks-*.yml ]] || echo "up $(sed -n 's/.*subnet: //p' "$f" | head -n 1)" >>"$STUB_CALLS"; done
      if [[ "$(grep -c '^up ' "$STUB_CALLS")" -le "${STUB_POOL_FAILS:-0}" ]]; then
        echo "Error response from daemon: invalid pool request: Pool overlaps with other one on this address space" >&2
        return 1
      fi
      ;;
    *" down "*) [[ -z "${STUB_DOWN_FAILS:-}" ]] || return 1 ;;
    esac
    ;;
  esac
  return 0
}
pnpm() { echo "pnpm $PWD $*" >>"$STUB_CALLS"; }
stub_plan() {
  : >"$TREE/compose.yml"
  mkdir -p "$TREE/web"
  run_step journeys-sweep s_journey sweep || true
  run_step journeys-identity s_journey identity || true
  find "$WORK/nets" -mindepth 1 -maxdepth 1 -exec basename {} \; | sort | tr '\n' ' ' >"$STUB_CALLS.nets"
}
EOF2
NET0=10.213.0.0/27 NET1=10.213.5.0/27
subnets_up() { grep '^up ' "$STUB_CALLS" | awk '{print $2}' | tr '\n' ' '; }
nets_left() { cat "$STUB_CALLS.nets"; }

: >"$STUB_CALLS"
run 0 env LOCAL_CHECK_STUB="$WORK/slots.sh" "$CHECK" --only journeys
out_has "journeys-sweep             PASS"
out_has "journeys-identity          PASS"
logs="$(sed -n 's/^logs: //p' "$WORK/out" | head -n 1)"
root="$(awk '$1 == "docker" && / up / { print $2; exit }' "$STUB_CALLS")"
[[ -n "$root" && "$(awk '$1 == "pnpm" { print $2; exit }' "$STUB_CALLS")" == "$root/web" ]] || fail "Playwright did not run in web/ of the tree ($root)"
after="$(awk '$1 == "pnpm" { on = 1; next } on && $1 == "docker" && (/ logs / || / down /) { print $2 }' "$STUB_CALLS" | sort -u)"
[[ "$after" == "$root" ]] || fail "the logs and the teardown after Playwright ran from $after, not from the tree root $root"
[[ "$(awk '$1 == "docker" && / down /' "$STUB_CALLS" | wc -l | tr -d ' ')" == 2 ]] || fail "each stack is taken down once"
! grep -q 'no such file' "$logs/stack-sweep.log" || fail "the stack log holds: $(cat "$logs/stack-sweep.log")"
[[ "$(subnets_up)" == "$NET0 $NET0 " && -z "$(nets_left)" ]] || fail "a stack taken down frees its slot: subnets $(subnets_up), slots left $(nets_left)"

: >"$STUB_CALLS"
run 0 env LOCAL_CHECK_STUB="$WORK/slots.sh" STUB_DOWN_FAILS=1 "$CHECK" --only journeys
[[ "$(subnets_up)" == "$NET0 $NET1 " && "$(nets_left)" == "0 1 " ]] || fail "a stack that could not be taken down kept no slot: subnets $(subnets_up), slots left $(nets_left)"
logs="$(sed -n 's/^logs: //p' "$WORK/out" | head -n 1)"
grep -q 'network slot 0 stays claimed' "$logs/journeys-sweep.log" || fail "the step does not say its slot stays claimed"
ok "Playwright runs in a subshell, the stack is taken down from the tree root, and only a stack taken down frees its network slot"

# A check that died without its trap left its networks: the slots they use are skipped, and a start
# that still meets "Pool overlaps" is taken down and started on another slot.
: >"$STUB_CALLS"
run 0 env LOCAL_CHECK_STUB="$WORK/slots.sh" STUB_POOL_FAILS=1 "$CHECK" --only journeys
out_has "journeys-sweep             PASS"
logs="$(sed -n 's/^logs: //p' "$WORK/out" | head -n 1)"
[[ "$(subnets_up)" == "$NET0 $NET1 $NET1 " && "$(nets_left)" == "0 " ]] ||
  fail "Pool overlaps once: subnets $(subnets_up), slots left $(nets_left) (expected $NET0 $NET1 $NET1, slot 0 left)"
[[ "$(grep -c '^retry 1 of 2: a network already uses the subnets of slot 0' "$logs/journeys-sweep.log")" == 1 ]] || fail "the retry on another slot is not said once"
[[ "$(awk '$1 == "docker" && / down /' "$STUB_CALLS" | wc -l | tr -d ' ')" == 3 ]] || fail "the stack that met Pool overlaps was not taken down before its new start"

: >"$STUB_CALLS"
run 1 env LOCAL_CHECK_STUB="$WORK/slots.sh" STUB_POOL_FAILS=9 "$CHECK" --only journeys
out_has "journeys-sweep             FAIL"
out_has "journeys-identity          FAIL"
[[ "$(grep -c '^up ' "$STUB_CALLS")" == 6 ]] || fail "Pool overlaps every time: $(grep -c '^up ' "$STUB_CALLS") starts for two groups, expected 3 each"

: >"$STUB_CALLS"
run 0 env LOCAL_CHECK_STUB="$WORK/slots.sh" STUB_SUBNETS="172.17.0.0/16 $NET0" "$CHECK" --only journeys
[[ "$(subnets_up)" == "$NET1 $NET1 " ]] || fail "10.213.0.0/27 in use: subnets $(subnets_up), expected slot 1 twice"
: >"$STUB_CALLS"
run 0 env LOCAL_CHECK_STUB="$WORK/slots.sh" STUB_SUBNETS="$NET0 10.213.9.0/27 192.168.0.0/20" "$CHECK" --only journeys
[[ "$(subnets_up)" == "10.213.10.0/27 10.213.10.0/27 " ]] || fail "slots 0 and 1 in use: subnets $(subnets_up), expected slot 2 twice"
ok "a slot whose subnets a network already holds is skipped, and Pool overlaps moves the stack to another slot, three starts at most"

# --- flaky tests of the parallel suite: run again alone ---------------------------------------------
# A fake uv on PATH stands for pytest: the parallel run (with xdist) fails the tests named in
# STUB_FAILED, with STUB_ERROR one error too; the run again (-p no:xdist) fails those in STUB_AGAIN.
mkdir -p "$WORK/fakeuv"
cat >"$WORK/fakeuv/uv" <<'EOF'
#!/usr/bin/env bash
printf 'uv %s\n' "$*" >>"$STUB_CALLS"
case " $* " in
*" no:xdist "*)
  failed=0
  for id in "$@"; do
    [[ " ${STUB_AGAIN:-} " != *" $id "* ]] || { echo "FAILED $id - assert 4.237 < 3.0 + 1.0"; failed=$((failed + 1)); }
  done
  if ((failed)); then echo "===== $failed failed in 3.10s ====="; exit 1; fi
  echo "===== $(($# - 6)) passed in 3.10s ====="
  ;;
*" -m not docker "*)
  failed=0
  red="" off=""
  [[ -z "${STUB_COLOR:-}" ]] || { red=$'\e[31m' off=$'\e[0m'; }
  for id in ${STUB_FAILED:-}; do echo "${red}FAILED${off} $id - assert 4.237 < 3.0 + 1.0"; failed=$((failed + 1)); done
  [[ -z "${STUB_LOGLINE:-}" ]] || echo "ERROR    app:main.py:207 unhandled error"
  if [[ -n "${STUB_ERROR:-}" ]]; then
    echo "ERROR tests/c_test.py - ImportError: no module"
    echo "===== $failed failed, 9 passed, 1 error in 12.00s ====="
    exit 1
  fi
  if ((failed)); then echo "${red}===== ${off}${red}$failed failed${off}, 9 passed in 12.00s ${red}=====${off}"; exit 1; fi
  echo "===== 9 passed in 12.00s ====="
  ;;
esac
exit 0
EOF
chmod +x "$WORK/fakeuv/uv"
cat >"$WORK/flaky.sh" <<'EOF'
stub_plan() {
  : >"$WORK/api.env"
  mkdir -p "$TREE/api"
  run_step pytest s_pytest || true
  quiet_phase
}
EOF
A="tests/a_test.py::test_burst[ten]" B="tests/b_test.py::test_scrub"
flaky_run() { # flaky_run <expected exit> <env ...>
  local want="$1"
  shift
  : >"$STUB_CALLS"
  run "$want" env PATH="$WORK/fakeuv:$PATH" LOCAL_CHECK_STUB="$WORK/flaky.sh" "$@" "$CHECK"
}
ALONE="$(sed -n 's/^ALONE_TEST="\(.*\)"$/\1/p' "$CHECK")"
[[ -n "$ALONE" ]] || fail "ALONE_TEST not found in $CHECK"
rerun() { grep ' no:xdist ' "$STUB_CALLS" || true; }

flaky_run 0 STUB_FAILED="$A $B"
out_has "pytest-flaky               PASS"
out_has "flaky: $A $B"
[[ "$(last_line)" == "LOCAL CHECK: STUB PASS "* ]] || fail "flaky tests that passed alone: $(last_line)"
[[ "$(rerun)" == "uv run --frozen pytest -p no:cacheprovider -p no:xdist $A $B" ]] || fail "the run again: $(rerun)"
grep -qF "uv run --frozen pytest $ALONE " "$STUB_CALLS" || fail "pytest-alone did not run after the flaky tests passed"
record="$(sed -n 's/^record: //p' "$WORK/out")"
python3 -c 'import json, sys; r = json.load(open(sys.argv[1])); assert r["flaky"] == sys.argv[2:], r["flaky"]' "$record" "$A" "$B" ||
  fail "the record does not list the flaky tests"
rm -f "$record"

flaky_run 0 STUB_FAILED="$A"
out_has "flaky: $A"
rm -f "$(sed -n 's/^record: //p' "$WORK/out")"

flaky_run 1 STUB_FAILED="$A $B" STUB_AGAIN="$B"
out_has "pytest                     FAIL"
out_has "pytest-flaky               FAIL"
out_lacks "flaky: $A"
[[ "$(last_line)" == *" pytest,pytest-flaky" ]] || fail "a test that failed again: $(last_line)"
! grep -qF "uv run --frozen pytest $ALONE " "$STUB_CALLS" || fail "pytest-alone ran after a failed pytest"

flaky_run 1 STUB_FAILED="$A $B tests/c_test.py::test_c tests/d_test.py::test_d"
[[ -z "$(rerun)" ]] || fail "four failed tests were run again"
[[ "$(last_line)" == *" pytest" ]] || fail "four failed tests: $(last_line)"

flaky_run 0 STUB_FAILED="$A" STUB_LOGLINE=1
out_has "flaky: $A"
rm -f "$(sed -n 's/^record: //p' "$WORK/out")"

flaky_run 0 STUB_FAILED="$A $B" STUB_COLOR=1
out_has "flaky: $A $B"
rm -f "$(sed -n 's/^record: //p' "$WORK/out")"

flaky_run 1 STUB_FAILED="$A" STUB_ERROR=1
[[ -z "$(rerun)" ]] || fail "a run with an error was run again"
[[ "$(last_line)" == *" pytest" ]] || fail "a failed test and an error: $(last_line)"
ok "at most 3 failed tests and no error: run again alone, PASS and listed under flaky (summary and record) when they pass, FAIL when one fails again; more, or an error, are not run again; a captured ERROR log line of the app is not a pytest error; a coloured log reads the same"

# --- a step of several commands --------------------------------------------------------------------
# Bash ignores errexit below a command whose status is tested, so a step called as the lanes call
# it (`|| true`, `if`, `!`) once passed when its first command failed and its last succeeded
# (review of pull request #121, B1). Each step now runs in a bash of its own: the same function
# fails however run_step is called, on the bash of PATH and on /bin/bash (3.2 on macOS).
cat >"$WORK/two.sh" <<'EOF'
s_two() {
  sh -c 'echo "the first command failed"; exit 1'
  sh -c 'echo "the second command passed"'
}
s_pipe() {
  sh -c 'echo "the writer of the pipe failed"; exit 1' | cat
  echo "the command after the pipe ran"
}
stub_plan() {
  run_step or-true s_two || true
  if run_step in-if s_two; then :; fi
  ! run_step negated s_two || true
  run_step plain s_two &
  wait
  run_step piped s_pipe || true
}
EOF
mkdir -p "$WORK/fakeuv"
cat >"$WORK/fakeuv/uv" <<'EOF'
#!/usr/bin/env bash
case "$*" in *"ruff check"*)
  echo "F821 undefined name"
  exit 1
  ;;
esac
echo "fake uv, clean: $*"
EOF
chmod +x "$WORK/fakeuv/uv"
cat >"$WORK/ruff.sh" <<'EOF'
stub_plan() { run_step ruff s_ruff || true; }
EOF
for shell in "${shells[@]}"; do
  # shellcheck disable=SC2086
  run 1 env LOCAL_CHECK_STUB="$WORK/two.sh" $shell "$CHECK"
  for name in or-true in-if negated plain piped; do
    grep -qE "^$name +FAIL" "$WORK/out" || fail "${shell:-bash of PATH}: the step $name, whose first command fails, is not FAIL: $(cat "$WORK/out")"
  done
  out_has "the first command failed"
  out_lacks "the second command passed"
  out_lacks "the command after the pipe ran"
  [[ "$(last_line)" == "LOCAL CHECK: FAIL $base_tree or-true,in-if,negated,plain,piped" ]] || fail "last line of the multi command steps: $(last_line)"
  [[ "$(records)" == 0 ]] || fail "a step whose first command failed left a record"
  # The real s_ruff, with a uv whose `ruff check` fails and whose `ruff format --check` passes.
  # shellcheck disable=SC2086
  run 1 env LOCAL_CHECK_STUB="$WORK/ruff.sh" PATH="$WORK/fakeuv:$PATH" $shell "$CHECK" --merge origin/shape
  out_has "F821 undefined name"
  out_lacks "ruff format --check"
  [[ "$(last_line)" =~ ^LOCAL\ CHECK:\ FAIL\ [0-9a-f]{40}\ ruff$ ]] || fail "last line of a failing ruff check: $(last_line)"
  [[ "$(records)" == 0 ]] || fail "a failing ruff check left a record"
done
nothing_left "the multi command steps"
ok "a step whose first command fails and whose last passes is FAIL, exit 1, no record, called with || true, if, ! or alone, a pipe included; the real s_ruff too (bash of PATH: $(bash -c 'echo $BASH_VERSION'); /bin/bash: $(/bin/bash -c 'echo $BASH_VERSION' 2>/dev/null || echo none))"

# --- a pass: the record and its key ----------------------------------------------------------------
# What GitHub will make of the same merges in the same order, computed apart from the script.
GITHUB="$WORK/github"
git clone -q "$ORIGIN" "$GITHUB"
git -C "$GITHUB" merge -q --no-ff --no-edit origin/feature-a
git -C "$GITHUB" merge -q --no-ff --no-edit origin/feature-b
want_tree="$(git -C "$GITHUB" rev-parse 'HEAD^{tree}')"
[[ "$want_tree" != "$base_tree" ]] || fail "the test's merges change nothing"

run 0 env LOCAL_CHECK_STUB="$WORK/pass.sh" "$CHECK" --only api --merge origin/feature-a --merge origin/feature-b
out_has "a partial run (--only api): no record is written"
[[ "$(records)" == 0 ]] || fail "a partial run wrote a record"
[[ "$(last_line)" =~ ^LOCAL\ CHECK:\ STUB\ PARTIAL\ PASS\ $want_tree\ [0-9]+s,\ only\ api,\ no\ record ]] || fail "last line of a partial run: $(last_line)"
run 0 env LOCAL_CHECK_STUB="$WORK/pass.sh" "$CHECK" --only api --only checks
[[ "$(last_line)" == *"PARTIAL PASS $base_tree "*", only api,checks, no record: not a proof for a merge or a deploy"* ]] || fail "last line of a run of two jobs: $(last_line)"
[[ "$(records)" == 0 ]] || fail "a run of two jobs wrote a record"
ok "a partial run (--only) ends with LOCAL CHECK: ... PARTIAL PASS, names its jobs and writes no record"

run 0 env LOCAL_CHECK_STUB="$WORK/pass.sh" "$CHECK" --merge origin/feature-a --merge origin/feature-b
[[ "$(last_line)" =~ ^LOCAL\ CHECK:\ STUB\ PASS\ $want_tree\ [0-9]+s,\ stub\ steps:\ not\ a\ check$ ]] || fail "last line of a pass with stub steps: $(last_line), expected the tree $want_tree"
[[ -f "$RECORDS/$want_tree.json" ]] || fail "no record $RECORDS/$want_tree.json"
python3 - "$RECORDS/$want_tree.json" "$want_tree" "$(git -C "$REPO" rev-parse origin/main)" \
  "$(git -C "$REPO" rev-parse origin/feature-a)" "$(git -C "$REPO" rev-parse origin/feature-b)" <<'PY' || fail "the record is not what was checked"
import json
import sys

record = json.load(open(sys.argv[1]))
tree, base, a, b = sys.argv[2:6]
assert record["result"] == "PASS" and record["tree"] == tree, record
assert record["base"] == {"ref": "origin/main", "commit": base}, record["base"]
assert record["merged"] == [
    {"ref": "origin/feature-a", "commit": a},
    {"ref": "origin/feature-b", "commit": b},
], record["merged"]
assert [step["name"] for step in record["steps"]] == ["one", "two"], record["steps"]
assert all(isinstance(step["seconds"], int) for step in record["steps"])
assert record["stub"] is True and record["date"].endswith("Z") and "git" in record["tools"], record
PY
nothing_left "a pass"
ok "a pass with stub steps ends with LOCAL CHECK: STUB PASS, never LOCAL CHECK: PASS, and its record says stub"
ok "a pass writes .local-check/<tree sha>.json: the tree GitHub gets from the same merges, base, merged commits, steps, tools, date"

# --- the lock --------------------------------------------------------------------------------------
# A length of its own, so that the test can look for this step among the processes.
export STUB_MARK="$WORK/mark" STUB_SLEEP="$((100000 + $$))"
LOCAL_CHECK_STUB="$WORK/slow.sh" "$CHECK" >"$WORK/first.out" 2>&1 &
first=$!
wait_for "$STUB_MARK"
run 3 env LOCAL_CHECK_STUB="$WORK/pass.sh" "$CHECK"
out_has "another check is running (process $first"
kill -0 "$first" 2>/dev/null || fail "the first check did not survive the second call: $(cat "$WORK/first.out")"
[[ -d "$RECORDS/lock" ]] || fail "the second call removed the lock of the first"
kill -TERM "$first"
wait "$first" 2>/dev/null && fail "a stopped check ended with exit 0"
nothing_left "a stopped check"
mkdir -p "$RECORDS/lock"
echo 999999 >"$RECORDS/lock/pid"
run 0 env LOCAL_CHECK_STUB="$WORK/pass.sh" "$CHECK"
out_has "taken over"
nothing_left "a lock left by a dead process"
ok "one check at a time: the second says who holds the lock and exits 3; a lock whose holder is dead is taken over"

# --- the set is whole ------------------------------------------------------------------------------
# main's own tree has a record here (the takeover above passed on it), so a check that merges one
# branch reads the commits of that branch only: the range starts at the checked commit.
cat >"$WORK/set.sh" <<'EOF'
stub_expected() { printf '%s\n' one two three; }
stub_plan() {
  run_step one true || true
  run_step two true || true
  [[ -z "${STUB_THREE:-}" ]] || run_step three true || true
}
EOF
run 1 env LOCAL_CHECK_STUB="$WORK/set.sh" "$CHECK" --merge origin/feature-a
out_has "secret scan: 2 commits, after $(git -C "$REPO" rev-parse origin/main) up to "
out_has "(the newest commit of origin/main whose tree has a PASS record)"
grep -qE '^three +FAIL .*a step of the set that did not run' "$WORK/out" || fail "a step of the set that did not run is not a failed step: $(cat "$WORK/out")"
[[ "$(last_line)" =~ ^LOCAL\ CHECK:\ FAIL\ [0-9a-f]{40}\ three$ ]] || fail "last line when a step of the set did not run: $(last_line)"
[[ "$(records)" == 2 ]] || fail "a check that lacked a step of its set wrote a record"
run 0 env LOCAL_CHECK_STUB="$WORK/set.sh" STUB_THREE=1 "$CHECK" --merge origin/feature-a
[[ "$(records)" == 3 ]] || fail "a check whose whole set ran wrote no record"
ok "a step of the set that did not run fails the check, with no record; with the whole set the check passes"

cat >"$WORK/select.sh" <<'EOF'
stub_plan() { run_step shell-tests s_shell_tests || true; }
EOF
run 1 env LOCAL_CHECK_STUB="$WORK/select.sh" "$CHECK" --merge origin/shape
out_has "run       tests/a_test.sh"
out_has "left out  tests/migrate_test.sh: run by hand"
out_has "MISSED    tests/b_test.sh: no step of ci.yml runs it"
[[ "$(last_line)" =~ ^LOCAL\ CHECK:\ FAIL\ [0-9a-f]{40}\ shell-tests$ ]] || fail "last line when a shell test is not selected: $(last_line)"
run 0 env LOCAL_CHECK_STUB="$WORK/select.sh" "$CHECK" --merge origin/shape-whole
run 1 env LOCAL_CHECK_STUB="$WORK/select.sh" "$CHECK"
out_has "shell-tests                FAIL"
ok "a tests/*_test.sh that ci.yml does not run and that is not left out with a reason fails the step shell-tests; so does a ci.yml that names none"

# --- Ctrl+C ----------------------------------------------------------------------------------------
# SIGINT as a terminal sends it. The check is started by python with the default disposition
# restored: a script started in the background has SIGINT ignored, and bash cannot trap that.
interrupt() { # interrupt [NAME=value ...]: start the slow check, interrupt it, print its exit status
  rm -f "$STUB_MARK"
  env "$@" LOCAL_CHECK_STUB="$WORK/slow.sh" python3 - "$CHECK" "$STUB_MARK" "$WORK/out" <<'PY'
import os
import signal
import subprocess
import sys
import time

script, mark, out = sys.argv[1:4]
child = subprocess.Popen(
    [script],
    stdout=open(out, "w"),
    stderr=subprocess.STDOUT,
    preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),
)
for _ in range(600):
    if os.path.exists(mark) and os.path.getsize(mark):
        break
    time.sleep(0.1)
time.sleep(0.5)
child.send_signal(signal.SIGINT)
print(child.wait(timeout=90))
PY
}
got="$(interrupt)"
[[ "$got" == 130 ]] || fail "an interrupted check ended with exit $got, expected 130: $(cat "$WORK/out")"
out_has "interrupted, cleaning up"
out_lacks "LOCAL CHECK: PASS"
nothing_left "Ctrl+C"
! pgrep -f "sleep $STUB_SLEEP" >/dev/null || fail "the step of an interrupted check is still running"
ok "Ctrl+C: exit 130, the running step stopped, no lock, no worktree, no temporary folder, no record"

if ((with_docker)); then
  docker run -d --rm --name "$BYSTANDER" "$IMAGE" >/dev/null
  got="$(interrupt STUB_IMAGE="$IMAGE")"
  [[ "$got" == 130 ]] || fail "an interrupted check with containers ended with exit $got: $(cat "$WORK/out")"
  prefix="$(cat "$STUB_MARK")"
  [[ "$prefix" == jobapp-lc* ]] || fail "the check's prefix is '$prefix'"
  left="$(docker ps -a --format '{{.Names}}' | grep -F "$prefix" || true)$(docker volume ls -q | grep -F "$prefix" || true)"
  [[ -z "$left" ]] || fail "Ctrl+C left Docker things of the check: $left"
  [[ "$(docker ps --format '{{.Names}}' | grep -cx "$BYSTANDER")" == 1 ]] || fail "the cleanup removed a container it did not start"
  nothing_left "Ctrl+C with containers"
  ok "Ctrl+C with a container and a volume of the check: both removed, a container of someone else untouched"
else
  printf 'skip the Docker part of the cleanup: no Docker daemon here\n'
fi

# --- the checked deploy ----------------------------------------------------------------------------
mkdir -p "$WORK/bin"
cat >"$WORK/bin/gh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$FAKE_LOG"
case "$1 $2" in
"run list") echo 4242 ;;
"run view")
  if [[ "$*" == *--log* ]]; then
    # As GitHub prints it: job, step, a time, then the line; a commit subject that holds the
    # words of the line (Preflight), then the step's own text.
    printf 'deploy to the Jetson\tPreflight\t2026-10-15T09:59:00.0Z target: main, commit 0123 (docs: JOBAPP-VERIFY services=9 healthy=9 result=ok)\n'
    printf 'deploy to the Jetson\tVerify the Jetson\t2026-10-15T10:00:00.0Z ##[group]Run bash ops/jetson-verify.sh "$APP"\n'
    printf 'deploy to the Jetson\tVerify the Jetson\t2026-10-15T10:00:01.0Z %s\r\n' "${FAKE_VERIFY:-JOBAPP-VERIFY services=8 healthy=8 missing=0 result=ok}"
  else
    echo "${FAKE_CONCLUSION:-success} https://example.org/runs/4242"
  fi
  ;;
esac
EOF
cat >"$WORK/bin/curl" <<'EOF'
#!/usr/bin/env bash
printf 'curl %s\n' "$*" >>"$FAKE_LOG"
EOF
chmod +x "$WORK/bin/gh" "$WORK/bin/curl"
export FAKE_LOG="$WORK/fake.log"
: >"$FAKE_LOG"
deploy() { # deploy <expected exit> <arguments ...>, gh and curl being the fakes
  local want="$1"
  shift
  run "$want" env PATH="$WORK/bin:$PATH" DEPLOY_HEALTH_URL=https://jobapp.example.org/api/health DEPLOY_POLL_SECONDS=0 "$DEPLOY" "$@"
}

# main has not moved, and its own tree has no record (the takeover above passed on it with stub
# steps: that record goes; the one that stays is of main plus the two branches).
rm -f "$RECORDS/$base_tree.json"
deploy 1 --dry-run
out_has "refused: no PASS record for the tree of origin/main ($base_tree"
deploy 1
out_has "refused: no PASS record"

# GitHub merges the two pull requests in the order that was checked: main's tree is the record's.
git -C "$GITHUB" push -q origin HEAD:main
git -C "$REPO" fetch -q origin
main_sha="$(git -C "$REPO" rev-parse origin/main)"
[[ "$(git -C "$REPO" rev-parse 'origin/main^{tree}')" == "$want_tree" ]] || fail "the merged main does not have the checked tree"
deploy 1 --dry-run
out_has "written by a test run with stub steps"
python3 - "$RECORDS/$want_tree.json" <<'PY'
import json
import sys

record = json.load(open(sys.argv[1]))
record["stub"] = False
open(sys.argv[1], "w").write(json.dumps(record))
PY
deploy 0 --dry-run
out_has "gh workflow run deploy.yml -f ref=$main_sha"
out_has "dry run, nothing started"
[[ ! -s "$FAKE_LOG" ]] || fail "a refusal or a dry run called gh or curl: $(cat "$FAKE_LOG")"
ok "deploy-checked.sh refuses a tree without a record and a record of stub steps; --dry-run prints the dispatch and starts nothing"

deploy 1 --dry-run origin/conflict-a
out_has "is not on origin/main"
deploy 1 --dry-run --allow-branch origin/conflict-a
out_has "refused: --allow-branch deploys a branch named hotfix/<name> only"
# A hot fix: a commit off main whose tree is the checked one, pushed as hotfix/fix-1.
hotfix_sha="$(git -C "$GITHUB" commit-tree "$want_tree" -p "$(git -C "$GITHUB" rev-parse 'HEAD~1')" -m "hotfix")"
git -C "$GITHUB" push -q origin "$hotfix_sha:refs/heads/hotfix/fix-1"
git -C "$GITHUB" push -q origin "$hotfix_sha:refs/heads/hotfixes-not"
git -C "$REPO" fetch -q origin
deploy 1 --dry-run origin/hotfix/fix-1
out_has "is not on origin/main"
deploy 0 --dry-run --allow-branch origin/hotfix/fix-1
out_has "gh workflow run deploy.yml -f ref=hotfix/fix-1 -f allow_branch=true"
deploy 1 --dry-run --allow-branch "$hotfix_sha"
out_has "deploys a branch named hotfix/<name> only"
deploy 1 --dry-run --allow-branch origin/hotfixes-not
out_has "deploys a branch named hotfix/<name> only"
deploy 1 --dry-run --allow-branch "origin/hotfix/fix-1~0"
out_has "deploys a branch named hotfix/<name> only"
git -C "$GITHUB" push -q origin --delete hotfix/fix-1 hotfixes-not
git -C "$REPO" fetch -q --prune origin
[[ ! -s "$FAKE_LOG" ]] || fail "a refused branch called gh or curl: $(cat "$FAKE_LOG")"
ok "a commit that is not on origin/main is refused; --allow-branch takes the tip of a hotfix/<name> branch only, by its name (deploy.yml's rule), and dispatches it with allow_branch"

deploy 0
grep -qx "workflow run deploy.yml -f ref=$main_sha" "$FAKE_LOG" || fail "the dispatch: $(cat "$FAKE_LOG")"
grep -q "^run watch 4242" "$FAKE_LOG" || fail "the run was not waited for: $(cat "$FAKE_LOG")"
grep -q "^curl .*https://jobapp.example.org/api/health" "$FAKE_LOG" || fail "the health URL was not asked: $(cat "$FAKE_LOG")"
out_has "deploy: success"
grep -qx "JOBAPP-VERIFY services=8 healthy=8 missing=0 result=ok" "$WORK/out" || fail "the figures of the run were not printed alone on their line: $(cat "$WORK/out")"
out_lacks "##[group]"
[[ "$(last_line)" == "DEPLOYED $main_sha" ]] || fail "last line of a deploy: $(last_line)"
: >"$FAKE_LOG"
FAKE_CONCLUSION=failure FAKE_VERIFY="JOBAPP-VERIFY services=8 healthy=7 missing=0 result=fail" deploy 1
out_has "the deploy did not succeed (failure)"
grep -qx "JOBAPP-VERIFY services=8 healthy=7 missing=0 result=fail" "$WORK/out" || fail "the figures of a failed run were not printed: $(cat "$WORK/out")"
! grep -q '^curl' "$FAKE_LOG" || fail "the health URL was asked after a failed deploy"
FAKE_CONCLUSION=failure FAKE_VERIFY="no figures here" deploy 1
out_lacks "JOBAPP-VERIFY"
FAKE_CONCLUSION=failure FAKE_VERIFY="echo JOBAPP-VERIFY services=8 healthy=8 missing=0 result=ok" deploy 1
out_lacks "JOBAPP-VERIFY"
ok "a checked main is dispatched with its full sha, waited for and asked for its health, the run's JOBAPP-VERIFY line printed; a failed run is exit 1, with its figures when it has some; the words of the line inside another line (a commit subject, a command) are not taken"

# main moves on with a commit that no check read: a check of main as it is, with no --merge, reads
# the commits since the last tree that has a PASS record (the range base..head would be empty).
printf 'later\n' >"$GITHUB/later.txt"
git -C "$GITHUB" add -A
git -C "$GITHUB" commit -q -m "later"
git -C "$GITHUB" push -q origin HEAD:main
run 0 env LOCAL_CHECK_STUB="$WORK/pass.sh" "$CHECK"
out_has "secret scan: 1 commits, after $main_sha up to $(git -C "$GITHUB" rev-parse HEAD) (the newest commit of origin/main whose tree has a PASS record)"
ok "the secret scan of main as it is starts after the last commit whose tree has a PASS record; with no record, 50 commits before the base or the whole history"

# --- the files of the repository -------------------------------------------------------------------
cd "$ROOT"
specs_of() { grep -v '^ *#' "$1" | grep -o '[a-z0-9-]*\.spec\.ts\|e2e/[a-z0-9-]*\.config\.ts' | sort -u; }
[[ -n "$(specs_of .github/workflows/ci.yml)" ]] || fail "no journey spec found in ci.yml"
# The groups that only the local check runs are the group configs that ci.yml does not run
# (local_check_configs of .github/scripts/e2e_groups.py: mail, with its fake Google, apply, with
# its fake ATS forms server, and the groups added since). Nothing else may differ: the specs and
# configs the gate ran above, on this repository's names, are those of ci.yml plus those.
local_only="$(python3 -c 'import sys; sys.path.insert(0, ".github/scripts"); import e2e_groups; print("\n".join(e2e_groups.local_check_configs(__import__("pathlib").Path("."))))')"
[[ -n "$local_only" ]] || fail "the local check runs no group config of its own (mail and apply had one)"
expected_specs() { { specs_of .github/workflows/ci.yml; printf '%s\n' "$local_only"; } | sort -u; }
[[ "$(expected_specs)" == "$(specs_of "$WORK/gate-specs")" ]] ||
  fail "the journey groups of ops/local-check.sh differ from those of ci.yml plus the local check's own: $(diff <(expected_specs) <(specs_of "$WORK/gate-specs") | tr '\n' ' ')"
while IFS= read -r config; do
  ! grep -qxF -- "$config" <(specs_of .github/workflows/ci.yml) || fail "$config is run by ci.yml and counted as the local check's own"
done <<<"$local_only"
ok "ops/local-check.sh runs the journey specs and configs that ci.yml names, and the group configs that ci.yml does not run"

listed="$(awk '/koalaman\/shellcheck:/ { on = 1; next } on && /^ *(#|- )/ { exit } on { print }' .github/workflows/ci.yml | tr '\n' ' ')"
for script in ops/local-check.sh ops/deploy-checked.sh tests/local_check_test.sh; do
  [[ " $listed " == *" $script "* ]] || fail "$script is not in the Shellcheck step of ci.yml, which ops/local-check.sh reads"
done
grep -q '^ *run: tests/local_check_test\.sh$' .github/workflows/ci.yml || fail "ci.yml has no step for tests/local_check_test.sh, so the batch check would not run it"
ok "the two scripts and this test are in the lists of ci.yml that the batch check reads"

triggers() { awk '/^on:/ { on = 1; next } on && /^[^ #]/ { exit } on && NF && $1 !~ /^#/ { print $1 }' "$1" | tr '\n' ' '; }
[[ "$(triggers .github/workflows/ci.yml)" == "workflow_dispatch: " ]] || fail "ci.yml is started by: $(triggers .github/workflows/ci.yml)"
[[ "$(triggers .github/workflows/deploy.yml)" == "workflow_dispatch: inputs: ref: description: required: default: " ||
  "$(triggers .github/workflows/deploy.yml)" == workflow_dispatch:\ * ]] || fail "deploy.yml is started by: $(triggers .github/workflows/deploy.yml)"
! grep -v '^ *#' .github/workflows/deploy.yml | grep -q 'workflow_run' || fail "deploy.yml still reads a workflow_run event"
grep -q '^\.local-check/$' .gitignore || fail ".local-check/ is not git ignored"
ok "ci.yml and deploy.yml start by hand only (workflow_dispatch), and the records are git ignored"

printf '\nAll %d checks passed.\n' "$passed"
