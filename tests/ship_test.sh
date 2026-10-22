#!/usr/bin/env bash
# Tests of ops/ship.sh (issue #278) on throwaway repositories: a bare "origin" whose
# refs/pull/<n>/head hold the pull requests, a main checkout that holds ship.sh next to a fake gate
# (ops/local-check.sh: real git merges, a record, failures on demand) and a fake deploy
# (ops/deploy-checked.sh, which prints the JOBAPP-VERIFY line of its run as the real one does), a
# fake board sync (ops/board-sync.py, which logs its calls and the batch file), and a fake gh on
# PATH that reads and writes a small state folder. The fake gh merges into the bare origin as
# GitHub would. Nothing reaches GitHub or the Jetson, and nothing uses ssh: the
# figures of the Jetson come from the deploy's output (ops/jetson-verify.sh is tested by
# tests/remote_ops_test.sh).
#
#   tests/ship_test.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOP="$(mktemp -d)"
TOP="$(cd "$TOP" && pwd -P)"
trap 'rm -rf "$TOP"' EXIT
command -v jq >/dev/null || {
  printf 'FAIL tests/ship_test.sh needs jq (the fake gh answers --jq with it)\n' >&2
  exit 1
}

passed=0
ok() {
  passed=$((passed + 1))
  printf 'ok   %s\n' "$*"
}
fail() {
  printf 'FAIL %s\n' "$*" >&2
  exit 1
}
OUT="$TOP/out"
run() { # run <expected exit> <command ...>: output in $OUT
  local want="$1" got=0
  shift
  "$@" >"$OUT" 2>&1 || got=$?
  [[ "$got" == "$want" ]] || fail "exit $got, expected $want: $* -- $(tail -c 1500 "$OUT")"
}
out_has() { grep -qF -- "$1" "$OUT" || fail "output lacks '$1': $(tail -c 1500 "$OUT")"; }
out_lacks() { ! grep -qF -- "$1" "$OUT" || fail "output holds '$1': $(tail -c 1500 "$OUT")"; }
last_line() { tail -n 1 "$OUT"; }
log_has() { grep -qF -- "$2" "$STATE/$1" 2>/dev/null || fail "$1 lacks '$2': $(cat "$STATE/$1" 2>/dev/null)"; }
log_lacks() { ! grep -qF -- "$2" "$STATE/$1" 2>/dev/null || fail "$1 holds '$2': $(cat "$STATE/$1")"; }

export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.org GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.org
export SHIP_POLL_SECONDS=0
export TMPDIR="$TOP/tmp"
mkdir -p "$TMPDIR" "$TOP/bin"
export PATH="$TOP/bin:$PATH"

# --- the fake gh -------------------------------------------------------------------------------------
# State per pull request in $STATE/pr/<n>/: state, draft, head, mergeable, unknown (reads left that
# answer UNKNOWN), reviews.json, files, closes.json, body (optional). Every call is logged in
# $STATE/gh.log. A merge of the number in $STATE/merge_fail is refused; one in
# $STATE/merge_then_fail merges, then fails as gh does on a branch used by a worktree; after a
# merge, the pull request named in $STATE/move_on_merge gets a new head (approved again).
cat >"$TOP/bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$STATE/gh.log"
jqf=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  if [[ "${args[i]}" == --jq ]]; then jqf="${args[i + 1]}"; fi
done
out() { if [[ -n "$jqf" ]]; then jq -r "$jqf"; else cat; fi; }
pr_dir() {
  [[ -d "$STATE/pr/$1" ]] || {
    echo "GraphQL: Could not resolve to a PullRequest with the number of $1. (repository.pullRequest)" >&2
    exit 1
  }
  printf '%s' "$STATE/pr/$1"
}
mergeable() { # GitHub computes it lazily: a few reads may answer UNKNOWN
  local d="$1" left
  left="$(cat "$d/unknown" 2>/dev/null || echo 0)"
  if ((left > 0)); then
    echo $((left - 1)) >"$d/unknown"
    echo UNKNOWN
  else
    cat "$d/mergeable"
  fi
}
case "$1 $2" in
"pr list")
  for d in "$STATE"/pr/*; do
    if [[ "$(cat "$d/state")" == OPEN ]]; then basename "$d"; fi
  done | jq -R 'tonumber' | jq -s 'map({number: .})' | out
  ;;
"pr view")
  d="$(pr_dir "$3")"
  # The state read right after a merge fails once (the file names the pull request).
  if [[ "$*" == "pr view $3 --json state --jq .state" && "$(cat "$d/state")" == MERGED && "$(cat "$STATE/state_read_fail" 2>/dev/null)" == "$3" ]]; then
    rm -f "$STATE/state_read_fail"
    echo "error connecting to api.github.com" >&2
    exit 1
  fi
  jq -n --arg state "$(cat "$d/state")" --argjson draft "$(cat "$d/draft")" --arg head "$(cat "$d/head")" \
    --arg mergeable "$(mergeable "$d")" --slurpfile reviews "$d/reviews.json" --slurpfile closes "$d/closes.json" \
    --arg body "$(cat "$d/body" 2>/dev/null || true)" \
    '{state: $state, isDraft: $draft, headRefOid: $head, mergeable: $mergeable, reviews: $reviews[0], closingIssuesReferences: $closes[0], body: $body}' | out
  ;;
"pr comment")
  d="$(pr_dir "$3")"
  [[ "$4" == --body-file ]] || exit 2
  cp "$5" "$STATE/comment-$3.txt"
  ;;
"pr merge")
  d="$(pr_dir "$3")"
  [[ "$*" == "pr merge $3 --repo GoldLegendW80/jobapp --merge --delete-branch --match-head-commit "* ]] || {
    echo "fake gh: unexpected merge call: $*" >&2
    exit 2
  }
  [[ "$(cat "$STATE/merge_fail" 2>/dev/null)" != "$3" ]] || {
    echo "GraphQL: Base branch was modified. Review and try the merge again. (mergePullRequest)" >&2
    exit 1
  }
  [[ "$(cat "$d/state")" == OPEN ]] || {
    echo "Pull request #$3 is not open" >&2
    exit 1
  }
  [[ "$9" == "$(cat "$d/head")" ]] || {
    echo "X Pull request #$3 is not mergeable: Head branch was modified. Review and try the merge again." >&2
    exit 1
  }
  git -C "$STATE/merger" fetch -q origin main "pull/$3/head"
  git -C "$STATE/merger" checkout -q -B main origin/main
  git -C "$STATE/merger" merge --no-ff --no-edit -q "$9"
  if [[ -e "$STATE/extra_commit_on_merge" ]]; then
    echo "pushed in between" >"$STATE/merger/between.txt"
    git -C "$STATE/merger" add -A
    git -C "$STATE/merger" commit -q -m "a commit pushed in between"
  fi
  git -C "$STATE/merger" push -q origin main
  echo MERGED >"$d/state"
  for other in "$STATE"/pr/*; do echo 1 >"$other/unknown"; done
  if [[ -s "$STATE/move_on_merge" ]]; then "$STATE/move-head" "$(cat "$STATE/move_on_merge")" >/dev/null 2>&1; fi
  echo "✓ Merged pull request #$3"
  # A TERM to ship.sh right after the merge of the number in $STATE/term_after_merge.
  if [[ "$(cat "$STATE/term_after_merge" 2>/dev/null)" == "$3" ]]; then kill -TERM "$(cat "$STATE/../main/.local-check/ship.lock/pid")"; fi
  if [[ "$(cat "$STATE/merge_then_fail" 2>/dev/null)" == "$3" ]]; then
    echo "failed to delete local branch pr$3: error: cannot delete branch 'pr$3' used by worktree" >&2
    exit 1
  fi
  ;;
"repo view")
  echo '{"nameWithOwner": "GoldLegendW80/jobapp"}' | out
  ;;
"api repos/{owner}/{repo}/pulls/"*)
  n="${2#repos/\{owner\}/\{repo\}/pulls/}"
  d="$(pr_dir "${n%/files}")"
  jq -R '{filename: .}' "$d/files" | jq -s . | out
  ;;
*)
  echo "fake gh: unexpected call: $*" >&2
  exit 2
  ;;
esac
EOF

# No ssh at all: one that is called fails the test.
cat >"$TOP/bin/ssh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$STATE/ssh.log"
exit 255
EOF
chmod +x "$TOP/bin/gh" "$TOP/bin/ssh"

# --- the fake gate and the fake deploy, committed on main of each origin ---------------------------
# The gate merges the refs as ops/local-check.sh does (a detached worktree of origin/main, git merge
# --no-ff in the order given), fails each step that a line "<n> <step>" of $STATE/gate_fail names
# for a merged pull request (or "main"), conflicts on the numbers of $STATE/conflict, writes the PASS
# record, and logs "<which gate> <merges>" in $STATE/gate.log. Line k of $STATE/gate_plan, when
# there is one, decides run k instead: "pass", "auto" (gate_fail decides) or the steps that fail.
# The log of a failed step is $STATE/fail_tail-<step>, else $STATE/fail_tail. WHO is "main" here;
# a pull request that changes the gate sets it to "pr". Each --merge is <n>@<sha> as ship.sh pins it
# (issue #468): that commit is merged, after the check that it is in the history of the head, and
# "<run> <n> <sha>" goes to $STATE/gate_merged.log; the pull request named in $STATE/move_in_gate
# gets a new head (approved again) at the start of the next run, before its fetch.
cat >"$TOP/fake-gate" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
WHO=main
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MAIN="$(dirname "$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)")"
merges=()
pins=()
while (($#)); do
  [[ "$1" == --merge ]] || exit 64
  # <n>@<full sha>, as ops/ship.sh pins each approved head (issue #468); a bare number is refused.
  [[ "$2" =~ ^([0-9]+)@([0-9a-f]{40})$ ]] || exit 64
  merges+=("${BASH_REMATCH[1]}")
  pins+=("${BASH_REMATCH[2]}")
  shift 2
done
printf '%s %s\n' "$WHO" "${merges[*]-}" >>"$STATE/gate.log"
# the batch file ship.sh wrote for the board, as this gate run saw it
printf '%s\n' "$(tr '\n' ' ' <"$MAIN/.local-check/ship.lock/batch" 2>/dev/null | sed 's/ $//')" >>"$STATE/gate_batch.log"
run="$(wc -l <"$STATE/gate.log" | tr -d ' ')"
if [[ -e "$STATE/gate_exit" ]]; then
  echo "ops/local-check.sh: another check is running (process 1). One at a time."
  exit "$(cat "$STATE/gate_exit")"
fi
work="$(mktemp -d)"
git -C "$MAIN" fetch -q origin main
base="$(git -C "$MAIN" rev-parse FETCH_HEAD)"
git -C "$MAIN" worktree add -q --detach "$work/t" "$base"
: >"$work/merged"
failed=""
# A hook of the tests: a push to the pull request named in $STATE/move_in_gate, during this run.
if [[ -s "$STATE/move_in_gate" ]]; then
  "$STATE/move-head" "$(cat "$STATE/move_in_gate")" >/dev/null 2>&1
  rm -f "$STATE/move_in_gate"
fi
for i in ${merges[@]+"${!merges[@]}"}; do
  n="${merges[i]}"
  git -C "$MAIN" fetch -q origin "pull/$n/head"
  # As ops/local-check.sh: the pinned commit, which must be in the history of the head.
  sha="${pins[i]}"
  git -C "$MAIN" merge-base --is-ancestor "$sha" FETCH_HEAD || {
    echo "ops/local-check.sh: pull request $n: $sha is not in the history of its head"
    exit 4
  }
  printf '%s %s %s\n' "$run" "$n" "$sha" >>"$STATE/gate_merged.log"
  if grep -qx "$n" "$STATE/conflict" 2>/dev/null; then
    echo "MERGE CONFLICT: pull request $n ($sha) does not merge into origin/main after the refs before it. Files:"
    echo "  file.txt"
    echo "Rebase or merge that branch by hand, or give another order. Nothing was checked."
    echo "LOCAL CHECK: FAIL $(git -C "$work/t" rev-parse 'HEAD^{tree}') merge-conflict"
    git -C "$MAIN" worktree remove --force "$work/t"
    exit 2
  fi
  git -C "$work/t" merge --no-ff --no-edit -q "$sha"
  printf 'pull request %s\t%s\n' "$n" "$sha" >>"$work/merged"
done
tree="$(git -C "$work/t" rev-parse 'HEAD^{tree}')"
head="$(git -C "$work/t" rev-parse HEAD)"
git -C "$MAIN" worktree remove --force "$work/t"
plan="$(sed -n "${run}p" "$STATE/gate_plan" 2>/dev/null || true)"
if [[ -z "$plan" || "$plan" == auto ]]; then
  while read -r who step; do
    [[ -n "$who" ]] || continue
    if [[ "$who" == main || " ${merges[*]-} " == *" $who "* ]]; then failed="${failed:+$failed,}$step"; fi
  done <"$STATE/gate_fail"
elif [[ "$plan" != pass ]]; then
  failed="$plan"
fi
if [[ -n "$failed" ]]; then
  printf 'api-install                PASS     1s\n'
  for step in ${failed//,/ }; do printf '%-26s FAIL     3s\n' "$step"; done
  for step in ${failed//,/ }; do
    printf '\n--- %s: the last 40 lines of %s/logs/%s.log\n' "$step" "$MAIN/.local-check" "$step"
    if [[ -f "$STATE/fail_tail-$step" ]]; then cat "$STATE/fail_tail-$step"; else cat "$STATE/fail_tail"; fi
  done
  printf '\nlogs: %s/logs\ntotal: 4s\n' "$MAIN/.local-check"
  echo "LOCAL CHECK: FAIL $tree $failed"
  exit 1
fi
mkdir -p "$MAIN/.local-check"
# $STATE/journeys, when it exists, adds the journeys of issue #552: "scoped" (the digest of the
# files of head against base), "scoped-other" (a digest of another change set) or "full".
python3 - "$MAIN/.local-check/$tree.json" "$work/merged" "$tree" "$head" "$base" "$(cat "$STATE/journeys" 2>/dev/null)" <<'PY'
import hashlib
import json
import subprocess
import sys

out, merged, tree, head, base, journeys = sys.argv[1:]
rows = [line.split("\t") for line in open(merged).read().splitlines() if line]
record = {"result": "PASS", "tree": tree, "head": head, "base": {"ref": "origin/main", "commit": base},
          "merged": [{"ref": ref, "commit": sha} for ref, sha in rows], "stub": False}
if journeys:
    names = subprocess.run(["git", "diff", "--name-only", "--no-renames", "-z", base, head],
                           capture_output=True, check=True).stdout
    paths = sorted(name for name in names.split(b"\0") if name)
    listed = paths + ([b"another.txt"] if journeys == "scoped-other" else [])
    if journeys == "scoped-collision":
        listed = [b"\n".join(paths)]
    record["journeys"] = {
        "scope": "full" if journeys == "full" else "scoped",
        "full_because": "LOCAL_CHECK_FULL=1" if journeys == "full" else None,
        "changed_files": len(listed) + (1 if journeys == "scoped-count" else 0),
        "changes": hashlib.sha256(b"".join(p + b"\0" for p in sorted(listed))).hexdigest(),
        "ran": [{"group": "apps", "why": "web/src/features/jobs/x.tsx: the module jobs"}]
        + ([{"group": "mail", "why": "full: LOCAL_CHECK_FULL=1"}] if journeys == "full" else []),
        "skipped": [] if journeys == "full" else [{"group": "mail", "why": "no changed file touches it"}],
    }
open(out, "w").write(json.dumps(record, indent=2) + "\n")
PY
echo "record: $MAIN/.local-check/$tree.json"
echo "LOCAL CHECK: PASS $tree 4s"
# Hooks of the tests: something happens between the gate and the merge.
if [[ -s "$STATE/move_after_gate" ]]; then "$STATE/move-head" "$(cat "$STATE/move_after_gate")"; fi
if [[ -e "$STATE/push_main_after_gate" ]]; then "$STATE/push-main"; fi
EOF
# As ops/deploy-checked.sh: some lines of its own (one that quotes a look-alike of the figures
# mid line, with a planted secret), the JOBAPP-VERIFY line of the run ($STATE/verify, when it
# exists), then its last line.
cat >"$TOP/fake-deploy" <<'EOF'
#!/usr/bin/env bash
printf 'deploy %s\n' "$*" >>"$STATE/deploy.log"
code="$(cat "$STATE/deploy_exit" 2>/dev/null || echo 0)"
printf '   run 4242: waiting for the Jetson\n   api-1 | said JOBAPP-VERIFY services=1 result=ok %s\n' "$CANARY"
if [[ -f "$STATE/verify" ]]; then cat "$STATE/verify"; fi
if [[ "$code" == 0 ]]; then echo "deployed $1, health ok"; else echo "ops/deploy-checked.sh: the deploy run failed"; fi
if [[ -f "$STATE/deploy_last" ]]; then cat "$STATE/deploy_last"; fi
exit "$code"
EOF
# As ops/board-sync.py: one line per call, its arguments and the batch file ship.sh wrote
# (.local-check/ship.lock/batch of the main checkout), then a sleep of $STATE/board_sleep
# seconds (a hung GitHub), then the exit of $STATE/board_exit.
cat >"$TOP/fake-board-sync" <<'EOF'
#!/usr/bin/env bash
batch="$(dirname "$0")/../.local-check/ship.lock/batch"
printf '%s batch=%s\n' "$*" "$(tr '\n' ' ' <"$batch" 2>/dev/null | sed 's/ $//')" >>"$STATE/board.log"
if [[ -s "$STATE/board_sleep" ]]; then sleep "$(cat "$STATE/board_sleep")"; fi
code="$(cat "$STATE/board_exit" 2>/dev/null || echo 0)"
[[ "$code" == 0 ]] || echo "gh api graphql failed: HTTP 502, token=$CANARY"
exit "$code"
EOF
chmod +x "$TOP/fake-gate" "$TOP/fake-deploy" "$TOP/fake-board-sync"
board_calls() { # board_calls <line ...>: the calls of the fake board-sync, exactly
  local want
  want="$(printf '%s\n' "$@")"
  [[ "$(cat "$STATE/board.log" 2>/dev/null)" == "$want" ]] || fail "board-sync calls: $(cat "$STATE/board.log" 2>/dev/null), expected: $want"
}

# verify_line <services> <healthy> <missing> <unhealthy> <alembic> <head> <errors> <browser>
# <unwell> <absent> <result>: a line as ops/jetson-verify.sh prints it.
verify_line() {
  printf 'JOBAPP-VERIFY services=%s healthy=%s missing=%s unhealthy=%s alembic=%s head=%s' "$1" "$2" "$3" "$4" "$5" "$6"
  printf ' mem_used=3120 mem_total=7620 errors=%s browser=%s commit=0123456789ab unwell=%s absent=%s result=%s\n' "$7" "$8" "$9" "${10}" "${11}"
}

# --- one scenario: fresh origin, main checkout, developer clone and state ---------------------------
N=0
fresh() {
  N=$((N + 1))
  local base="$TOP/s$N"
  export STATE="$base/state"
  ORIGIN="$base/origin.git"
  MAIN="$base/main"
  DEV="$base/dev"
  mkdir -p "$STATE/pr"
  : >"$STATE/gate_fail"
  printf 'E   assert 1 == 2\n' >"$STATE/fail_tail"
  verify_line 8 8 0 0 0042_ship 1 0 off - - ok >"$STATE/verify"
  git init -q --bare -b main "$ORIGIN"
  git clone -q "$ORIGIN" "$MAIN" 2>/dev/null
  mkdir -p "$MAIN/ops"
  cp "$ROOT/ops/ship.sh" "$MAIN/ops/ship.sh"
  cp "$TOP/fake-gate" "$MAIN/ops/local-check.sh"
  cp "$TOP/fake-deploy" "$MAIN/ops/deploy-checked.sh"
  cp "$TOP/fake-board-sync" "$MAIN/ops/board-sync.py"
  printf '.local-check/\n' >"$MAIN/.gitignore"
  printf 'one\n' >"$MAIN/file.txt"
  git -C "$MAIN" add -A
  git -C "$MAIN" commit -q -m base
  git -C "$MAIN" push -q origin main
  git clone -q "$ORIGIN" "$DEV" 2>/dev/null
  git clone -q "$ORIGIN" "$STATE/merger" 2>/dev/null
  cat >"$STATE/move-head" <<EOF
#!/usr/bin/env bash
# a push after the gate, approved again on its new head: only the gate's record shows the move
set -e
git -C "$DEV" checkout -q "pr\$1"
echo moved >>"$DEV/moved-\$1.txt"
git -C "$DEV" add -A
git -C "$DEV" commit -q -m "moved"
git -C "$DEV" push -q origin "HEAD:refs/pull/\$1/head"
git -C "$DEV" rev-parse HEAD >"$STATE/pr/\$1/head"
jq -n --arg b "REVIEW VERDICT: APPROVE"\$'\n'"Head: \$(cat "$STATE/pr/\$1/head")" '[{body: \$b}]' >"$STATE/pr/\$1/reviews.json"
EOF
  cat >"$STATE/push-main" <<EOF
#!/usr/bin/env bash
set -e
git -C "$DEV" fetch -q origin main
git -C "$DEV" checkout -q -B main origin/main
echo hotfix >"$DEV/hotfix.txt"
git -C "$DEV" add -A
git -C "$DEV" commit -q -m hotfix
git -C "$DEV" push -q origin main
EOF
  chmod +x "$STATE/move-head" "$STATE/push-main"
}
verdict() { # verdict <n> <line 1 tail> <head line value>: the latest review, after an older one
  local body
  body="$(printf 'REVIEW VERDICT: %s\nHead: %s\n\nThe review.' "$2" "$3")"
  jq -n --arg b "$body" '[{body: "REVIEW VERDICT: REQUEST CHANGES\nHead: 0000000000000000000000000000000000000000"}, {body: $b}, {body: "Thanks, a comment after the verdict."}]' \
    >"$STATE/pr/$1/reviews.json"
}
mkpr() { # mkpr <n> [<path changed>] [<content>]: a pull request approved on its head
  local n="$1" path="${2:-f$1.txt}" content="${3:-pr $1}" d="$STATE/pr/$1"
  git -C "$DEV" fetch -q origin main
  git -C "$DEV" checkout -q -B "pr$n" origin/main
  mkdir -p "$(dirname "$DEV/$path")"
  printf '%s\n' "$content" >"$DEV/$path"
  if [[ "$path" == *.sh ]]; then chmod +x "$DEV/$path"; fi
  git -C "$DEV" add -A
  git -C "$DEV" commit -q -m "pull request $n"
  git -C "$DEV" push -q origin "HEAD:refs/pull/$n/head"
  mkdir -p "$d"
  echo OPEN >"$d/state"
  echo false >"$d/draft"
  echo MERGEABLE >"$d/mergeable"
  git -C "$DEV" rev-parse HEAD >"$d/head"
  git -C "$DEV" diff --name-only origin/main HEAD >"$d/files"
  printf '[{"number": %s, "url": "https://github.com/GoldLegendW80/jobapp/issues/%s"}]\n' $((n + 1000)) $((n + 1000)) >"$d/closes.json"
  verdict "$n" APPROVE "$(cat "$d/head")"
}
gate_pr() { # gate_pr <n>: a pull request that changes the gate script (WHO=pr)
  mkpr "$1" ops/local-check.sh "$(sed 's/^WHO=main$/WHO=pr/' "$TOP/fake-gate")"
}
ship() { (cd "$MAIN" && ops/ship.sh "$@"); }
snapshot() { # what --plan must leave untouched
  {
    git -C "$ORIGIN" for-each-ref
    git -C "$MAIN" worktree list --porcelain
    git -C "$MAIN" status --porcelain --ignored
    ls -a "$MAIN"
  } 2>&1
}
CANARY="canary$RANDOM$RANDOM$RANDOM"

# === arguments =======================================================================================
fresh
run 64 ship
out_has "name pull requests, or give --approved"
run 64 ship --approved 11
run 64 ship 11 11
out_has "given twice"
run 64 ship abc
run 64 ship 0
run 64 ship --bogus
ok "wrong arguments exit 64 with a reason"

# === selection and order, --plan touches nothing =====================================================
fresh
mkpr 11
mkpr 12 && echo true >"$STATE/pr/12/draft"
mkpr 13 && verdict 13 "REQUEST CHANGES" "$(cat "$STATE/pr/13/head")"
mkpr 14 && verdict 14 APPROVE "1111111111111111111111111111111111111111"
mkpr 15 && echo CONFLICTING >"$STATE/pr/15/mergeable"
mkpr 16 && verdict 16 APPROVE "$(cut -c1-7 "$STATE/pr/16/head")"
gate_pr 17
mkpr 18 && echo 2 >"$STATE/pr/18/unknown"
mkpr 19 && verdict 19 "APPROVE with nits" "$(cat "$STATE/pr/19/head")"
mkpr 20
mkpr 21 && jq -n --arg b "REVIEW VERDICT: APPROVE"$'\r\n'"Head: $(cat "$STATE/pr/21/head")"$'\r\n' '[{body: $b}]' >"$STATE/pr/21/reviews.json"
mkpr 22 ops/local-check.sh "another gate"
mkpr 23 && echo '[{"body": "LGTM"}]' >"$STATE/pr/23/reviews.json"
mkpr 24 && echo UNKNOWN >"$STATE/pr/24/mergeable"
mkpr 25 && echo CLOSED >"$STATE/pr/25/state"
before="$(snapshot)"
run 0 ship --plan --approved
[[ "$(last_line)" == "SHIP: PLAN, nothing was done" ]] || fail "plan last line: $(last_line)"
h() { cut -c1-7 "$STATE/pr/$1/head"; }
out_has "plan   ship in this order: #17 $(h 17), #11 $(h 11), #18 $(h 18), #20 $(h 20), #21 $(h 21)"
out_has "skip   #12: a draft"
out_has "skip   #13: the latest verdict is REQUEST CHANGES"
out_has "skip   #14: the approval names 1111111111111111111111111111111111111111, the head is $(h 14)"
out_has "skip   #15: GitHub says CONFLICTING"
out_has "skip   #16: the approval names $(h 16), the head is $(h 16)"
out_has "skip   #19: the latest verdict is APPROVE with nits"
out_has "skip   #23: no review verdict"
out_has "skip   #24: GitHub says UNKNOWN"
out_lacks "#25"
out_has "defer  #22: it changes ops/local-check.sh as #17 does"
out_has "plan   gate: the ops/local-check.sh of #17 at $(h 17) (it changes the gate)"
out_has "plan   ops/local-check.sh --merge 17@$(cat "$STATE/pr/17/head") --merge 11@$(cat "$STATE/pr/11/head") --merge 18@$(cat "$STATE/pr/18/head") --merge 20@$(cat "$STATE/pr/20/head") --merge 21@$(cat "$STATE/pr/21/head");"
out_has "plan   ops/board-sync.py --apply at the start of the gate (the batch in $MAIN/.local-check/ship.lock/batch), after the merges and after the deploy"
[[ ! -e "$STATE/board.log" ]] || fail "--plan ran board-sync: $(cat "$STATE/board.log")"
[[ "$before" == "$(snapshot)" ]] || fail "--plan changed the repositories or the checkout"
for f in gate.log deploy.log ssh.log board.log; do [[ ! -e "$STATE/$f" ]] || fail "--plan ran $f"; done
! grep -qE '^(pr merge|pr comment|project )' "$STATE/gh.log" || fail "--plan wrote to GitHub: $(cat "$STATE/gh.log")"
ok "--approved picks approved heads only (drafts, other verdicts, old or short heads, conflicts, UNKNOWN after 5 reads skipped; CRLF and later comments fine), gate changer first, a second one deferred"
ok "--plan touches nothing: no merge, comment, board sync, gate, deploy, ssh, ref or file"

run 0 ship --plan 20 11
out_has "plan   ship in this order: #20 $(h 20), #11 $(h 11)"
run 1 ship --plan 11 13
out_has "SHIP: FAIL #13 cannot ship: the latest verdict is REQUEST CHANGES"
run 1 ship 12
out_has "#12 cannot ship: a draft"
[[ ! -e "$STATE/gate.log" ]] || fail "a refused number ran the gate"
ok "numbers keep their order; a named pull request that is not approved stops the run"

echo true >"$STATE/pr/11/draft"
for n in 13 14 15 16 17 18 19 20 21 22 23 24; do echo CLOSED >"$STATE/pr/$n/state"; done
run 0 ship --approved
[[ "$(last_line)" == "SHIP: NOTHING TO SHIP" ]] || fail "nothing to ship: $(last_line)"
ok "nothing approved: SHIP: NOTHING TO SHIP, exit 0"

# === dependencies =====================================================================================
fresh
mkpr 70 && echo MERGED >"$STATE/pr/70/state"
mkpr 71 && printf 'The fix.\r\n\r\nDepends on #72\r\n' >"$STATE/pr/71/body"
mkpr 72
mkpr 73 && verdict 73 "REQUEST CHANGES" "$(cat "$STATE/pr/73/head")"
mkpr 74 && printf 'Depends on #73\n\nStacked on #73.\n' >"$STATE/pr/74/body"
mkpr 75 && printf 'stacked on #74\n' >"$STATE/pr/75/body"
mkpr 76 && printf 'Depends on #70\n' >"$STATE/pr/76/body"
mkpr 77 && printf -- '- Depends on #72, #99\n' >"$STATE/pr/77/body"
gate_pr 78 && printf 'Depends on #72\n' >"$STATE/pr/78/body"
mkpr 79 && printf 'Depends on #78\n' >"$STATE/pr/79/body"
mkpr 80 && printf 'Closes #5. This one does not depend on #72 (a mention, not a line that begins so).\n' >"$STATE/pr/80/body"
run 0 ship --plan --approved
h() { cut -c1-7 "$STATE/pr/$1/head"; }
out_has "skip   #74: waits for #73"
out_has "skip   #75: waits for #74"
out_has "skip   #77: waits for #99"
out_has "defer  #78: waits for #72"
out_has "defer  #79: waits for #78"
out_has "plan   ship in this order: #72 $(h 72), #71 $(h 71), #76 $(h 76), #80 $(h 80)"
out_has "plan   gate: the ops/local-check.sh of the main checkout"
ok "dependencies: ordered after what they depend on, MERGED ones fine, any case and list marks, unapproved or unknown ones skip the dependent (transitively), a gate changer with a dependency in the batch deferred with its dependents"
run 1 ship 74
out_has "SHIP: FAIL #74 cannot ship: waits for #73"
run 1 ship 71 72
out_has "SHIP: FAIL #71 cannot ship: waits for #72"
run 0 ship --plan 72 71 76
out_has "plan   ship in this order: #72 $(h 72), #71 $(h 71), #76 $(h 76)"
[[ ! -e "$STATE/gate.log" ]] || fail "a refused dependency ran the gate"
ok "named pull requests: a dependency neither merged nor named before it stops the run"
printf 'Depends on #72\n' >"$STATE/pr/72/body"
run 0 ship --plan 76
run 0 ship --plan --approved
out_has "skip   #72: waits for #72"
out_has "skip   #71: waits for #72"
ok "a pull request that depends on itself waits, and so do its dependents"

# === the whole way: gate of the pull request that changes it, merge, pull, deploy, Jetson, board ===
fresh
mkpr 31
gate_pr 32
run 0 ship --approved
[[ "$(last_line)" == "SHIP: OK" ]] || fail "happy path: $(last_line)"
[[ "$(cat "$STATE/gate.log")" == "pr 32 31" ]] || fail "the gate of #32 should have run once on 32 31: $(cat "$STATE/gate.log")"
grep -E '^pr merge' "$STATE/gh.log" >"$TOP/merges"
[[ "$(sed -n 1p "$TOP/merges")" == "pr merge 32 --repo GoldLegendW80/jobapp --merge --delete-branch --match-head-commit $(cat "$STATE/pr/32/head")" ]] || fail "first merge: $(cat "$TOP/merges")"
[[ "$(sed -n 2p "$TOP/merges")" == "pr merge 31 --repo GoldLegendW80/jobapp --merge --delete-branch --match-head-commit $(cat "$STATE/pr/31/head")" ]] || fail "second merge: $(cat "$TOP/merges")"
main_sha="$(git -C "$MAIN" rev-parse HEAD)"
[[ "$main_sha" == "$(git -C "$ORIGIN" rev-parse main)" ]] || fail "the main checkout was not pulled"
[[ "$(cat "$STATE/deploy.log")" == "deploy $main_sha" ]] || fail "deploy: $(cat "$STATE/deploy.log")"
out_has "jetson:   8 services, 8 healthy, 0 missing; alembic 0042_ship; memory 3120 of 7620 MB used; 0 error lines in 10 minutes; browser off"
out_has "check  the figures of the deploy run (JOBAPP-VERIFY)"
[[ ! -e "$STATE/ssh.log" ]] || fail "ship.sh called ssh: $(cat "$STATE/ssh.log")"
board_calls "--apply batch=32 31" "--apply batch=" "--apply batch="
out_has "board:    3 sync(s)"
log_lacks gh.log "project "
out_lacks "$CANARY"
[[ "$(git -C "$MAIN" worktree list | wc -l | tr -d ' ')" == 1 ]] || fail "the gate worktree was left: $(git -C "$MAIN" worktree list)"
[[ ! -e "$MAIN/.local-check/ship.lock" ]] || fail "the lock was left"
[[ -z "$(git -C "$MAIN" status --porcelain)" ]] || fail "the main checkout is dirty after a ship"
ok "approved to production: PR gate from its head, merges in order with --match-head-commit, pull, deploy of that commit, Jetson figures, the board synced at the gate (batch file), after the merges and after the deploy, no log line printed"

# === bisect ===========================================================================================
fresh
for n in 41 42 43 44; do mkpr "$n"; done
printf '43 api-pytest\n' >"$STATE/gate_fail"
{
  printf 'tests/test_x.py::test_answer_table_refuses_another_country FAILED\n'
  printf 'E   assert 1 == 2\n'
  printf 'E   settings: ANTHROPIC_API_KEY=%s\n' "$CANARY"
  printf 'E   got %s\n' "Zx81Qp0LmN4vT7rKs2Yb6Hc9Wd3Fj5Ga"
  printf 'E   url postgresql://app:%s@db:5432/app\n' "$CANARY"
} >"$STATE/fail_tail"
run 0 ship --approved
expected="$(printf 'main 41 42 43 44\nmain 41 42 43 44\nmain 41 42\nmain 41 42 43\nmain 41 42 44')"
[[ "$(cat "$STATE/gate.log")" == "$expected" ]] || fail "bisect runs: $(cat "$STATE/gate.log")"
out_has "drop   #43: api-pytest"
out_has "dropped:  #43 (api-pytest)"
grep -c '^pr merge' "$STATE/gh.log" | grep -qx 3 || fail "three merges expected: $(grep '^pr merge' "$STATE/gh.log")"
log_lacks gh.log "pr merge 43"
[[ -f "$STATE/comment-43.txt" ]] || fail "no comment on the dropped pull request"
grep -qF "failed at \`api-pytest\`" "$STATE/comment-43.txt" || fail "comment lacks the step: $(cat "$STATE/comment-43.txt")"
grep -qF "after #41, #42" "$STATE/comment-43.txt" || fail "comment lacks the order: $(cat "$STATE/comment-43.txt")"
grep -qF "E   assert 1 == 2" "$STATE/comment-43.txt" || fail "comment lacks the log tail"
grep -qF "test_answer_table_refuses_another_country FAILED" "$STATE/comment-43.txt" || fail "comment withheld a test name"
[[ "$(grep -cF '[line withheld: it may hold a secret]' "$STATE/comment-43.txt")" -ge 3 ]] || fail "the three secret lines should be withheld: $(cat "$STATE/comment-43.txt")"
! grep -qF "$CANARY" "$STATE/comment-43.txt" || fail "the comment holds the planted secret"
while read -r gate_run n sha; do
  [[ "$sha" == "$(cat "$STATE/pr/$n/head")" ]] || fail "gate run $gate_run merged $sha for #$n, not its approved head"
done <"$STATE/gate_merged.log"
[[ "$(wc -l <"$STATE/gate_merged.log" | tr -d ' ')" == 16 ]] || fail "the gate runs merged: $(cat "$STATE/gate_merged.log")"
! grep -qF "Zx81Qp0LmN4vT7rKs2Yb6Hc9Wd3Fj5Ga" "$STATE/comment-43.txt" || fail "the comment holds a token"
out_lacks "$CANARY"
! grep -qF "the last 40 lines of" "$STATE/comment-43.txt" || fail "the comment holds the local log header"
for n in 41 42 44; do [[ ! -e "$STATE/comment-$n.txt" ]] || fail "#$n got a comment"; done
[[ "$(last_line)" == "SHIP: OK" ]] || fail "bisect then ship: $(last_line)"
out_lacks "flaky:"
board_calls "--apply batch=41 42 43 44" "--apply batch=" "--apply batch="
[[ "$(tail -n 1 "$STATE/gate_batch.log")" == "41 42 44" ]] || fail "the batch file after the drop: $(cat "$STATE/gate_batch.log")"
ok "bisect: the same failure twice, then the shortest failing prefix drops #43 (5 gate runs), comment with step, order and a redacted tail, the rest shipped"

fresh
for n in 41 42 43; do mkpr "$n"; done
printf '42 api-pytest\n42 web-lint\n' >"$STATE/gate_fail"
for i in $(seq 1 45); do printf 'api line %s\n' "$i"; done >"$STATE/fail_tail-api-pytest"
printf 'web-lint said %s\n' "$CANARY" >"$STATE/fail_tail-web-lint"
run 0 ship --approved
out_has "drop   #42: api-pytest,web-lint"
grep -qF "failed at \`api-pytest\`" "$STATE/comment-42.txt" || fail "comment names another step: $(cat "$STATE/comment-42.txt")"
grep -qxF "api line 45" "$STATE/comment-42.txt" || fail "comment lacks the last line"
grep -qxF "api line 16" "$STATE/comment-42.txt" || fail "comment lacks line 16"
! grep -qxF "api line 15" "$STATE/comment-42.txt" || fail "the comment holds more than 30 lines of the log"
! grep -qF "web-lint said" "$STATE/comment-42.txt" || fail "the comment holds the log of a step not blamed"
fence='```'
[[ "$(sed -n "/^${fence}text\$/,/^${fence}\$/p" "$STATE/comment-42.txt" | wc -l | tr -d ' ')" == 32 ]] || fail "30 lines expected between the fences: $(cat "$STATE/comment-42.txt")"
ok "two steps fail: the comment holds the last 30 lines of the first step's log only, without the local header"

fresh
for n in 81 82 83; do mkpr "$n"; done
printf 'Stacked on #82\n' >"$STATE/pr/83/body"
printf '82 api-pytest\n' >"$STATE/gate_fail"
run 0 ship --approved
[[ "$(cat "$STATE/gate.log")" == "$(printf 'main 81 82 83\nmain 81 82 83\nmain 81 82\nmain 81')" ]] || fail "dependent bisect runs: $(cat "$STATE/gate.log")"
out_has "dropped:  #82 (api-pytest), #83 (waits for #82)"
out_has "gate   run 4 already passed this batch, not run again"
out_has "gate:     PASS"
grep -q "PASS .* after 4 run(s)" "$OUT" || fail "the summary should count 4 runs: $(tail -c 800 "$OUT")"
[[ -f "$STATE/comment-82.txt" && ! -e "$STATE/comment-83.txt" ]] || fail "#82 alone gets a comment"
grep '^pr merge' "$STATE/gh.log" | grep -qv '^pr merge 81 ' && fail "only #81 should merge: $(grep '^pr merge' "$STATE/gh.log")"
log_has gh.log "pr merge 81 "
[[ "$(last_line)" == "SHIP: OK" ]] || fail "dependent drop: $(last_line)"
ok "a bisect that drops a pull request drops its dependents too, without a comment, and ships the rest; the prefix the bisect already passed is not gated again"

# === the flake guard ==================================================================================
fresh
for n in 85 86; do mkpr "$n"; done
printf 'api-pytest\nweb-lint\n' >"$STATE/gate_plan"
run 1 ship --approved
[[ "$(grep -v '^ops/ship.sh: ' "$OUT" | tail -n 1)" == "SHIP: FAIL flaky gate" ]] || fail "flaky last line: $(tail -n 3 "$OUT")"
[[ "$(last_line)" == "ops/ship.sh: flaky gate" ]] || fail "flaky stderr: $(last_line)"
out_has "flaky:    run 1 failed api-pytest, run 2 failed web-lint"
[[ "$(wc -l <"$STATE/gate.log" | tr -d ' ')" == 2 ]] || fail "no bisect after a flaky gate: $(cat "$STATE/gate.log")"
ls "$STATE"/comment-*.txt >/dev/null 2>&1 && fail "a flaky gate commented"
log_lacks gh.log "pr merge"
ok "the full batch fails at other steps on the second run: SHIP: FAIL flaky gate, both runs named, nothing dropped, commented or merged"

fresh
for n in 85 86; do mkpr "$n"; done
printf 'api-pytest\npass\n' >"$STATE/gate_plan"
run 0 ship --approved
[[ "$(last_line)" == "SHIP: OK" ]] || fail "fail then pass: $(last_line)"
out_has "flaky:    run 1 failed api-pytest"
out_has "gate:     PASS"
log_has gh.log "pr merge 85 "
log_has gh.log "pr merge 86 "
ok "a failure that does not repeat: ships on the PASS of the second run, the first named flaky"

fresh
for n in 87 88 89; do mkpr "$n"; done
printf '89 api-pytest\n' >"$STATE/gate_fail"
printf 'auto\nauto\nweb-lint\n' >"$STATE/gate_plan"
run 0 ship --approved
out_has "drop   #89: api-pytest"
[[ ! -e "$STATE/comment-88.txt" ]] || fail "#88 was blamed for a prefix that failed at another step"
out_has "run 3 failed web-lint, a bisect prefix counted as passing"
ok "a bisect prefix that fails at another step counts as passing (flaky), the real culprit is dropped"

fresh
for n in 45 46 47; do mkpr "$n"; done
echo 46 >"$STATE/conflict"
run 0 ship --approved
[[ "$(cat "$STATE/gate.log")" == "$(printf 'main 45 46 47\nmain 45 47')" ]] || fail "conflict runs: $(cat "$STATE/gate.log")"
grep -qF "failed at \`merge-conflict\`" "$STATE/comment-46.txt" || fail "conflict comment: $(cat "$STATE/comment-46.txt")"
grep -qxF "  file.txt" "$STATE/comment-46.txt" || fail "conflict comment lacks the files: $(cat "$STATE/comment-46.txt")"
ok "a merge conflict drops the pull request it names, without bisecting"

fresh
mkpr 51
mkpr 52
printf '51 web-lint\n' >"$STATE/gate_fail"
run 0 ship --approved
[[ "$(cat "$STATE/gate.log")" == "$(printf 'main 51 52\nmain 51 52\nmain 51\nmain \nmain 52')" ]] || fail "first alone: $(cat "$STATE/gate.log")"
out_has "drop   #51: web-lint"
ok "the first pull request alone fails: main is checked alone before #51 is blamed"

fresh
mkpr 53
printf 'main api-pytest\n' >"$STATE/gate_fail"
run 1 ship --approved
out_has "SHIP: FAIL main itself fails the gate at api-pytest"
[[ ! -e "$STATE/comment-53.txt" ]] || fail "#53 was blamed for a broken main"
log_lacks gh.log "pr merge"
ok "a main that fails the gate stops the run, nobody is blamed"

fresh
mkpr 54
printf '54 api-pytest\n' >"$STATE/gate_fail"
run 1 ship --approved
out_has "SHIP: FAIL every pull request of the batch failed the gate"
[[ -f "$STATE/comment-54.txt" ]] || fail "#54 got no comment"
ok "every pull request dropped: exit 1"

fresh
mkpr 55
echo 3 >"$STATE/gate_exit"
run 1 ship --approved
out_has "SHIP: FAIL the gate refused to start, a check holds the lock or the disk is short"
log_lacks gh.log "pr comment"
board_calls "--apply batch=55" "--apply batch="
[[ ! -e "$MAIN/.local-check/ship.lock" ]] || fail "the lock was left after a failed gate"
ok "a gate that cannot start (lock, disk) stops the run without blaming anyone, and one more sync on the way out takes the batch off Gating"

# === refusals around the merge ======================================================================
fresh
mkpr 61
mkpr 62
echo 62 >"$STATE/move_after_gate"
run 1 ship --approved
out_has "SHIP: FAIL the head of #62 moved since the gate"
out_has "nothing merged"
log_lacks gh.log "pr merge"
[[ ! -e "$STATE/deploy.log" ]] || fail "deployed after a head moved"
ok "a head that moved after the gate (and was approved again) is refused before any merge"

# Issue #468: a push after the approval is never what the gate checks, and stops the run.
fresh
mkpr 65
mkpr 66
approved66="$(cat "$STATE/pr/66/head")"
echo 66 >"$STATE/move_in_gate"
printf 'api-pytest\n' >"$STATE/gate_plan"
run 1 ship --approved
[[ "$(cat "$STATE/pr/66/head")" != "$approved66" ]] || fail "the test did not move the head of #66"
[[ "$(cat "$STATE/gate_merged.log")" == "$(printf '1 65 %s\n1 66 %s' "$(cat "$STATE/pr/65/head")" "$approved66")" ]] ||
  fail "the gate did not check the approved heads: $(cat "$STATE/gate_merged.log")"
[[ "$(cat "$STATE/gate.log")" == "main 65 66" ]] || fail "gate runs after the head moved: $(cat "$STATE/gate.log")"
out_has "SHIP: FAIL the head of #66 moved while shipping ($(cut -c1-7 <<<"$approved66") approved, $(cut -c1-7 "$STATE/pr/66/head") now): nothing merged, run ops/ship.sh again"
log_lacks gh.log "pr merge"
log_lacks gh.log "pr comment"
[[ ! -e "$STATE/deploy.log" ]] || fail "deployed after a head moved during the ship"
ok "a head pushed during the ship: the gate checked the approved head, the run stops before the next gate run, no bisect, no comment, nothing merged"

fresh
mkpr 67
approved67="$(cat "$STATE/pr/67/head")"
echo 67 >"$STATE/move_in_gate"
run 1 ship --approved
[[ "$(cat "$STATE/gate_merged.log")" == "1 67 $approved67" ]] || fail "the gate did not check the approved head: $(cat "$STATE/gate_merged.log")"
record="$(ls "$MAIN"/.local-check/*.json)"
[[ "$(jq -r '.merged[0].commit' "$record")" == "$approved67" ]] || fail "the record lists $(jq -r '.merged[0].commit' "$record")"
out_has "SHIP: FAIL the head of #67 moved since the gate ($(cut -c1-7 <<<"$approved67") checked, $(cut -c1-7 "$STATE/pr/67/head") now): nothing merged"
log_lacks gh.log "pr merge"
[[ ! -e "$STATE/deploy.log" ]] || fail "deployed after a head moved during the gate"
ok "a head pushed during a passing gate run: the PASS is for the approved head, the merge step refuses the new one"

fresh
mkpr 63
touch "$STATE/push_main_after_gate"
run 1 ship --approved
out_has "SHIP: FAIL main moved since the gate"
log_lacks gh.log "pr merge"
ok "main that moved after the gate: nothing merged"

fresh
mkpr 64
echo "local change" >"$MAIN/scratch.txt"
run 1 ship --approved
out_has "has uncommitted or untracked files"
[[ ! -e "$STATE/gate.log" ]] || fail "a dirty checkout ran the gate"
run 0 ship --plan --approved
out_has "a real run would refuse now, the main checkout $MAIN has uncommitted or untracked files"
rm "$MAIN/scratch.txt"
git -C "$MAIN" checkout -q -b elsewhere
run 1 ship --approved
out_has "is on elsewhere, not main"
[[ ! -e "$STATE/gate.log" ]] || fail "a checkout off main ran the gate"
ok "a dirty main checkout or one off main is refused before the gate (and named by --plan)"

fresh
mkpr 65
touch "$STATE/extra_commit_on_merge"
run 1 ship --approved
out_has "differs from the gate's tree"
out_has "nothing deployed"
[[ ! -e "$STATE/deploy.log" ]] || fail "deployed a tree that was not checked"
ok "a main tree that differs from the gate's tree stops before the deploy"

# === the journey groups of the record (issue #552) ===================================================
fresh
mkpr 91
echo scoped >"$STATE/journeys"
run 0 ship --approved
[[ "$(last_line)" == "SHIP: OK" ]] || fail "scoped record: $(last_line)"
out_has "journeys: scoped on 1 changed files, ran apps; skipped mail (no changed file touches it)"
fresh
mkpr 92
echo full >"$STATE/journeys"
run 0 ship --approved
out_has "journeys: full (LOCAL_CHECK_FULL=1), ran apps, mail"
fresh
mkpr 93
run 0 ship --approved
out_has "journeys: not recorded (a gate before issue #552: every group ran)"
fresh
mkpr 94
echo scoped-other >"$STATE/journeys"
run 1 ship --approved
out_has "its journey scope was computed for another change set: nothing merged"
log_lacks gh.log "pr merge"
[[ ! -e "$STATE/deploy.log" ]] || fail "deployed on a record of another change set"
ok "the summary names the journey groups the gate ran and skipped (scoped, full, a record without them); a scoped record of another change set is refused before any merge"

for mode in scoped-collision scoped-count; do
  fresh
  mkpr 95 a
  mkpr 96 b
  echo "$mode" >"$STATE/journeys"
  run 1 ship --approved
  out_has "its journey scope was computed for another change set: nothing merged"
  log_lacks gh.log "pr merge"
  [[ ! -e "$STATE/deploy.log" ]] || fail "deployed on $mode metadata"
done
ok "pathname-boundary collisions and incorrect recorded counts stop before any merge"

fresh
mkpr 97 $'line\nbreak\t"quoted".txt'
echo scoped >"$STATE/journeys"
run 0 ship --approved
out_has "journeys: scoped on 1 changed files"
ok "a real newline, tab and quote in a Git pathname remain one attested change"

fresh
mkpr 66
echo 1 >"$STATE/deploy_exit"
rm -f "$STATE/verify"
run 1 ship --approved
out_has "SHIP: FAIL ops/deploy-checked.sh failed (exit 1): ops/deploy-checked.sh: the deploy run failed"
out_lacks "jetson:"
out_lacks "check  the figures"
board_calls "--apply batch=66" "--apply batch=" "--apply batch="
out_has "board:    3 sync(s)"
ok "a failed deploy with no figures exits 1, no Jetson check, the board synced after the merge and after the deploy"

fresh
mkpr 66
echo 1 >"$STATE/deploy_exit"
printf 'token=%s\n' "$CANARY" >"$STATE/deploy_last"
run 1 ship --approved
out_has "SHIP: FAIL ops/deploy-checked.sh failed (exit 1): [line withheld: it may hold a secret]"
out_lacks "$CANARY"
ok "the deploy log's last line is redacted before it is printed"

fresh
mkpr 67
verify_line 5 1 1 3 0041_old 0 5 off worker:exited,caddy:restarting,parser:unhealthy tailscale fail >"$STATE/verify"
run 1 ship --approved
out_has "not healthy: worker exited, caddy restarting, parser unhealthy"
out_has "missing: tailscale"
out_has "jetson:   5 services, 1 healthy, 1 missing; alembic 0041_old"
out_has "alembic is not at head"
out_has "5 error lines in 10 minutes"
out_lacks "$CANARY"
board_calls "--apply batch=67" "--apply batch=" "--apply batch="
ok "an unwell Jetson (stopped, restarting, unhealthy and missing services, alembic, error lines) exits 1, prints figures only, the board synced already"

fresh
mkpr 71
echo 1 >"$STATE/deploy_exit"
verify_line 8 8 0 0 0042_ship 1 0 running:unhealthy - - fail >"$STATE/verify"
run 1 ship --approved
out_has "deploy:   FAIL, exit 1"
out_has "SHIP: FAIL the Jetson after the deploy: browser running unhealthy"
out_has "browser running:unhealthy"
ok "a run that failed at its verify step names the figures that failed (a running browser that is not healthy)"

fresh
mkpr 68
rm -f "$STATE/verify"
run 1 ship --approved
out_has "SHIP: FAIL the deploy run printed no JOBAPP-VERIFY line"
out_has "deployed, the Jetson is not checked (ops/remote.sh status)"
ok "a deploy whose run printed no figures exits 1 and says the Jetson is not checked"

fresh
mkpr 70
printf 'JOBAPP-VERIFY services=8 healthy=8 missing=0 unhealthy=0 alembic=- head=0 mem_used=- mem_total=- errors=- browser=off commit=- unwell=- absent=- result=fail\n' >"$STATE/verify"
run 1 ship --approved
out_has "SHIP: FAIL the Jetson after the deploy: alembic is not at head, memory not read, error lines not read"
out_has "alembic -; memory - of - MB used"
ok "figures the Jetson could not read are failures that say so"

fresh
mkpr 72
printf 'JOBAPP-VERIFY services=8 healthy=8 missing=0 unhealthy=0 alembic=0042_ship head=1 mem_used=3120 mem_total=7620 errors=0 browser=off commit=- unwell=- absent=- result=fail\n' >"$STATE/verify"
run 1 ship --approved
out_has "SHIP: FAIL the Jetson after the deploy: the check on the Jetson failed"
ok "a result=fail is a failure even when every figure ship.sh knows reads well"

fresh
mkpr 69
echo 1 >"$STATE/board_exit"
run 0 ship --approved
[[ "$(last_line)" == "SHIP: OK" ]] || fail "a failed board sync failed the ship: $(last_line)"
[[ "$(grep -c '^board  sync .* failed (exit 1, ' "$OUT")" == 3 ]] || fail "one line per failed sync expected: $(grep '^board' "$OUT")"
out_has "board  sync at the start of the gate failed (exit 1, "
out_has "board:    3 sync(s), failed at the start of the gate, after the merges, after the deploy"
out_has "[line withheld: it may hold a secret]"
out_lacks "$CANARY"
[[ -s "$STATE/deploy.log" ]] || fail "a failed board sync stopped the deploy"
out_has "jetson:   8 services, 8 healthy"
ok "a board sync that fails prints one redacted line each time and changes nothing else: merged, deployed, checked, SHIP: OK"

# === a stop between two merges, and gh failing after GitHub merged ==================================
fresh
for n in 81 82 83; do mkpr "$n"; done
echo 82 >"$STATE/merge_fail"
main_before="$(git -C "$MAIN" rev-parse HEAD)"
run 1 ship --approved
out_has "merged:   #81"
out_has "not merged: #82, #83"
out_has "SHIP: FAIL gh pr merge #82 failed"
[[ ! -e "$STATE/deploy.log" ]] || fail "deployed after a stop between two merges"
[[ "$(git -C "$MAIN" rev-parse HEAD)" == "$main_before" ]] || fail "the main checkout was pulled after a stop"
board_calls "--apply batch=81 82 83" "--apply batch="
out_has "board:    2 sync(s)"
log_lacks gh.log "pr merge 83"
ok "a merge refused midway: merged and not merged named, the board synced after the merges, no pull, no deploy"

fresh
for n in 84 85; do mkpr "$n"; done
echo 85 >"$STATE/move_on_merge"
run 1 ship --approved
out_has "SHIP: FAIL #85 changed during the merges (head"
out_has "merged:   #84"
out_has "not merged: #85"
log_lacks gh.log "pr merge 85"
[[ ! -e "$STATE/deploy.log" ]] || fail "deployed after a head moved between two merges"
ok "a head that moves between two merges stops the run with what merged and what did not"

fresh
mkpr 91
mkpr 92
echo 91 >"$STATE/state_read_fail"
run 0 ship --approved
[[ ! -e "$STATE/state_read_fail" ]] || fail "the state read of #91 after its merge never failed"
[[ "$(last_line)" == "SHIP: OK" ]] || fail "state read failed once after a merge: $(last_line)"
out_has "merged:   #91, #92"
[[ "$(grep -cxF 'pr view 91 --json state --jq .state' "$STATE/gh.log")" -ge 2 ]] || fail "the state of #91 was not read again"
ok "a state read that fails right after a merge is retried once, the pull request counts as merged"

fresh
for n in 93 94 95; do mkpr "$n"; done
echo 94 >"$STATE/term_after_merge"
run 130 ship --approved
out_has "merged:   #93, #94"
out_has "not merged: #95"
out_has "interrupted: while merging #94, which is MERGED on GitHub"
out_has "SHIP: FAIL interrupted during the merges: nothing deployed"
board_calls "--apply batch=93 94 95" "--apply batch="
log_lacks gh.log "pr merge 95"
[[ ! -e "$STATE/deploy.log" ]] || fail "deployed after an interrupt"
[[ ! -e "$MAIN/.local-check/ship.lock" ]] || fail "the lock was left after an interrupt"
ok "TERM during the merges prints what merged and what did not, syncs the board, exits 130, deploys nothing"

# === a sync that hangs: the watchdog kills it, the ship goes on =====================================
no_sleeper() { # no_sleeper: the fake sync's sleep was killed with its group
  ! pgrep -f "sleep 37.25" >/dev/null || fail "a sync was left running: $(pgrep -fl 'sleep 37.25')"
}
fresh
mkpr 96
echo 37.25 >"$STATE/board_sleep"
started="$SECONDS"
SHIP_SYNC_SECONDS=2 run 0 ship --approved
((SECONDS - started < 30)) || fail "three hung syncs took $((SECONDS - started)) s with SHIP_SYNC_SECONDS=2"
[[ "$(last_line)" == "SHIP: OK" ]] || fail "a hung sync failed the ship: $(last_line)"
[[ "$(grep -c '^board  sync .* failed (killed after 2 s, ' "$OUT")" == 3 ]] || fail "one line per killed sync expected: $(grep '^board' "$OUT")"
out_has "board  sync at the start of the gate failed (killed after 2 s, "
out_has "board:    3 sync(s), failed at the start of the gate, after the merges, after the deploy"
out_has "merged:   #96"
[[ "$(cat "$STATE/deploy.log")" == "deploy $(git -C "$MAIN" rev-parse HEAD)" ]] || fail "a hung sync stopped the deploy"
board_calls "--apply batch=96" "--apply batch=" "--apply batch="
[[ ! -e "$MAIN/.local-check/ship.lock" ]] || fail "the lock was left after a hung sync"
no_sleeper
ok "a sync that hangs is killed after SHIP_SYNC_SECONDS with its process group, one line each time: merged, deployed, checked, SHIP: OK, no lock left"

fresh
for n in 97 98; do mkpr "$n"; done
echo 97 >"$STATE/term_after_merge"
echo 37.25 >"$STATE/board_sleep"
started="$SECONDS"
SHIP_SYNC_SECONDS=2 run 130 ship --approved
((SECONDS - started < 30)) || fail "the TERM path waited $((SECONDS - started)) s on a hung sync"
out_has "merged:   #97"
out_has "not merged: #98"
out_has "board  sync after the merges failed (killed after 2 s, "
out_has "SHIP: FAIL interrupted during the merges: nothing deployed"
board_calls "--apply batch=97 98" "--apply batch="
[[ ! -e "$STATE/deploy.log" ]] || fail "deployed after an interrupt"
[[ ! -e "$MAIN/.local-check/ship.lock" ]] || fail "the lock was left after TERM and a hung sync"
no_sleeper
ok "TERM during the merges with a hung sync: on_signal's sync is killed by the watchdog (it ignores INT and TERM), exit 130, no lock left"

fresh
mkpr 99
echo 3 >"$STATE/gate_exit"
echo 37.25 >"$STATE/board_sleep"
SHIP_SYNC_SECONDS=2 run 1 ship --approved
out_has "SHIP: FAIL the gate refused to start"
out_has "board  sync after the stop failed (killed after 2 s, "
board_calls "--apply batch=99" "--apply batch="
[[ ! -e "$MAIN/.local-check/ship.lock" ]] || fail "the lock was left"
no_sleeper
ok "the sync on the way out of a failed gate is bounded too (its line goes to stderr, after SHIP: FAIL), no lock left"

fresh
mkpr 86
mkpr 87
echo 86 >"$STATE/merge_then_fail"
run 0 ship --approved
[[ "$(last_line)" == "SHIP: OK" ]] || fail "merged then gh failed: $(last_line)"
out_has "merged:   #86, #87"
out_has "warnings: gh pr merge #86 exited 1 after the merge"
ok "gh pr merge that fails after GitHub merged (a branch used by a worktree) counts as merged"

fresh
mkpr 88
mkdir -p "$MAIN/.local-check/ship.lock"
echo 999999 >"$MAIN/.local-check/ship.lock/pid"
run 1 ship --approved
out_has "(process 999999, not running; remove the folder if no ship is running)"
echo $$ >"$MAIN/.local-check/ship.lock/pid"
run 1 ship --approved
out_has "(process $$, running;"
ok "a held ship.lock names its holder and whether it runs"

# === redact: the accept and refuse table ===========================================================
keep=(
  "E   assert 1 == 2"
  "tests/test_answer_table.py::test_refuses_work_authorisation_for_another_country FAILED"
  "FAILED tests/api/test_mail.py::test_reply_is_not_sent_twice[fr-FR] - AssertionError"
  "api-pytest                 FAIL   312s"
  "  File \"/app/app/routes/jobs.py\", line 88, in create_job"
  "ValueError: invalid literal for int() with base 10: 'abc'"
  "error TS2345: Argument of type 'string' is not assignable to parameter of type 'number'."
  "  1 failed, 2310 passed, 4 skipped in 512.31s (0:08:32)"
  "web/src/features/jobs/JobList.test.tsx > shows the empty state"
  "Expected: \"Saved\"  Received: \"Saving\""
  "at Object.<anonymous> (web/src/lib/api.ts:42:13)"
  "    def test_scoring_takes_under_a_second(benchmark):"
  "docker: Error response from daemon: Conflict. The container name is already in use."
  "E   KeyError: 'company'"
  "playwright: 1 failed [chromium] e2e/apply-review.spec.ts:57:3 › stages a form"
  "ruff: F401 [*] os imported but unused"
  "coverage: total 94.87%, the floor is 95"
  "Error: Timed out 5000ms waiting for expect(locator).toBeVisible()"
  "  short ids like abc1234 and 0042_ship stay"
  "a desk-top lamp, a risk-based check"
  "the author of the commit, work authorisation for France"
  "msg.send(reply) returned None"
  "the monkey ate the keyboard"
  "def test_hf_ratio_is_kept(): pass"
  "tests/test_metrics.py::test_hf_ratio FAILED"
  "KeyError: 'access'"
  "keys pressed: 3"
  "accessibility: the button has no label"
  "the timezone Asia/Tokyo is used, asia-northeast1 too"
  "npm_config_cache is set"
  "pypi-mirror-eu is the index"
  "the acca of the gate, the sgx module"
  "ssl.create_default_context() was called"
  "the file sl.py has 3 lines, hvs.py has 4"
  "r8_ab, glc_ab and gsk_ab are short names"
  "a vault path hvs. is documented"
  "the pplx module and the figd module"
  ""
)
# The secret shapes are cut in two in this file, so that the secret scan of the repository and
# detect-private-key do not read them as secrets; the shell joins them again.
withhold=(
  "ANTHROPIC_API_KEY=sk-""ant-api03-abc"
  "export OPENAI_KEY=sk-""proj-Zx81Qp0LmN"
  "token: Zx81Qp0Lm""N4vT7rKs2Yb6Hc9Wd3Fj5Ga"
  "Authorization: Bearer abcdef"
  "password=hunter2"
  "DB_PASSWORD hunter2"
  "Set-Cookie: session=abc"
  "postgresql://app:hunter2@db:5432/app"
  "redis://:hunter2@redis:6379/0"
  "gh""p_0123456789abcdefABCDEF0123456789abcd"
  "github""_pat_11ABCDEFG0123456789"
  "xo""xb-1234-5678-abcdef"
  "AK""IAIOSFODNN7EXAMPLE"
  "AI""zaSyD-1234567890abcdefghij"
  "ya""29.a0AfH6SMBx"
  "GOC""SPX-abcdefghijklmnop"
  "ey""JhbGciOiJIUzI1NiJ9.ey""JzdWIiOiIxIn0.sig"
  "-----BEGIN PRIV""ATE KEY-----"
  "got Zx81Qp0LmN4vT7rKs2Yb6Hc9Wd3Fj5Ga in the body"
  "value 5f4dcc3b5aa765d61d8327deb882cf99 compared"
  "LOCAL CHECK: FAIL 3308edfe35a420651bbbca676fed031be6baeeed api-pytest"
  "SECRET_KEY changed"
  "the credentials file was read"
  "client_secret=abc"
  "Fernet key gAAAAABkZx81Qp0LmN4vT7rKs2Yb6Hc9Wd3Fj5Ga=="
  "dsn=postgres://x"
  "x-api-key: abc"
  "auth=abc"
  "test_auth_flow FAILED"
  "oauth callback code=abc"
  "gl""pat-abcdef123456"
  "hf""_abcdefghijklmnopqr"
  "token npm""_abcdefghij0123456789"
  "sk""_live_abcdef1234"
  "rk""_live_abcdef1234"
  "sk""_test_abcdef1234"
  "SG"".abcdefghijkl.mnopqrstuvwx"
  "AS""IAIOSFODNN7EXAMPLE"
  "AB""IAIOSFODNN7EXAMPLE"
  "AC""CAIOSFODNN7EXAMPLE"
  "A3""TXIOSFODNN7EXAMPLE"
  "do""p_v1_abcdef0123456789"
  "py""pi-AgEIcHlwaS5vcmc"
  "sh""pat_abcdef0123456789"
  "sh""pss_abcdef0123456789"
  "sh""pca_abcdef0123456789"
  "sh""ppa_abcdef0123456789"
  "xa""pp-1-A0123456789-abcdef"
  "li""n_api_abcdef0123456789"
  "DefaultEndpointsProtocol=https;Account""Key=abcdef=="
  "ts""key-client-kABC123CNTRL-abcdef"
  "ts""key-auth-kABC123CNTRL-abcdef"
  "FERNET""_KEY=abc"
  "access""_key=abc"
  "aws_secret_access""_key: abc"
  "accessKey""Id = abc"
  "{'api_key': 'abc'}"
  "my_creds"": abc"
  "\"key\""": \"abc\""
  "gs""k_abcdef0123456789"
  "gl""c_abcdef0123456789"
  "gl""sa_abcdef0123456789"
  "gl""ptt-abcdef0123456789"
  "dck""r_pat_abcdef0123456789"
  "snt""rys_abcdef0123456789"
  "fig""d_abcdef0123456789"
  "r""8_abcdef0123456789"
  "pp""lx-abcdef0123456789"
  "ls""v2_pt_abcdef0123456789"
  "xo""xe-1-abcdef0123456789"
  "xo""xe.xoxp-1-abcdef0123456789"
  "token h""vs.abcdef0123456789"
  "dropbox s""l.abcdef0123456789"
)
for line in "${keep[@]}"; do
  got="$(printf '%s\n' "$line" | "$ROOT/ops/ship.sh" --redact)"
  [[ "$got" == "$line" ]] || fail "redact withheld a harmless line: '$line' -> '$got'"
done
for line in "${withhold[@]}"; do
  got="$(printf '%s\n' "$line" | "$ROOT/ops/ship.sh" --redact)"
  [[ "$got" == "[line withheld: it may hold a secret]" ]] || fail "redact let through: '$line'"
done
long="$(printf 'x%.0s' $(seq 1 1000))"
got="$(printf '%s\n' "$long" | "$ROOT/ops/ship.sh" --redact)"
[[ "${#got}" == 400 ]] || fail "a long line is not cut at 400: ${#got}"
ok "redact: ${#keep[@]} harmless lines kept, ${#withhold[@]} secret shapes withheld, long lines cut"

printf '\n%s passed\n' "$passed"
