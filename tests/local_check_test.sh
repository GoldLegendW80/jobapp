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
# The folders the real step functions enter (s_ruff does `cd api`).
branch shape api/.keep ""

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
logs="$(sed -n 's/^logs: //p' "$WORK/out" | head -n 1)"
grep -q "the reason it broke" "$logs/broken.log" || fail "the full log of the failed step is not kept in $logs"
ok "a failing step is exit 1 with the step named, the end of its log shown, its log kept, and no record"

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
shells=("")
[[ ! -x /bin/bash ]] || shells+=(/bin/bash)
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

run 0 env LOCAL_CHECK_STUB="$WORK/pass.sh" "$CHECK" --merge origin/feature-a --merge origin/feature-b
[[ "$(last_line)" =~ ^LOCAL\ CHECK:\ PASS\ $want_tree\ [0-9]+s$ ]] || fail "last line of a pass: $(last_line), expected the tree $want_tree"
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
"run view") echo "${FAKE_CONCLUSION:-success} https://example.org/runs/4242" ;;
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
  run "$want" env PATH="$WORK/bin:$PATH" DEPLOY_HEALTH_URL=https://jobapp.example.org/api/health "$DEPLOY" "$@"
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
out_has "refused: no PASS record"
[[ ! -s "$FAKE_LOG" ]] || fail "a refused branch called gh or curl: $(cat "$FAKE_LOG")"
ok "a commit that is not on origin/main is refused; --allow-branch still needs a record of its tree"

deploy 0
grep -qx "workflow run deploy.yml -f ref=$main_sha" "$FAKE_LOG" || fail "the dispatch: $(cat "$FAKE_LOG")"
grep -q "^run watch 4242" "$FAKE_LOG" || fail "the run was not waited for: $(cat "$FAKE_LOG")"
grep -q "^curl .*https://jobapp.example.org/api/health" "$FAKE_LOG" || fail "the health URL was not asked: $(cat "$FAKE_LOG")"
out_has "deploy: success"
[[ "$(last_line)" == "DEPLOYED $main_sha" ]] || fail "last line of a deploy: $(last_line)"
: >"$FAKE_LOG"
FAKE_CONCLUSION=failure deploy 1
out_has "the deploy did not succeed (failure)"
! grep -q '^curl' "$FAKE_LOG" || fail "the health URL was asked after a failed deploy"
ok "a checked main is dispatched with its full sha, waited for and asked for its health; a failed run is exit 1"

# --- the files of the repository -------------------------------------------------------------------
cd "$ROOT"
specs_of() { grep -v '^ *#' "$1" | grep -o '[a-z0-9-]*\.spec\.ts\|e2e/[a-z-]*\.config\.ts' | sort -u; }
[[ -n "$(specs_of .github/workflows/ci.yml)" ]] || fail "no journey spec found in ci.yml"
[[ "$(specs_of .github/workflows/ci.yml)" == "$(specs_of ops/local-check.sh)" ]] ||
  fail "the journey groups of ops/local-check.sh (journey_specs) and of ci.yml differ: $(diff <(specs_of .github/workflows/ci.yml) <(specs_of ops/local-check.sh) | tr '\n' ' ')"
ok "ops/local-check.sh runs the journey specs and configs that ci.yml names"

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
