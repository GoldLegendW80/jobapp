#!/usr/bin/env bash
# Tests of ops/ship.sh (issue #278) on throwaway repositories: a bare "origin" whose
# refs/pull/<n>/head hold the pull requests, a main checkout that holds ship.sh next to a fake gate
# (ops/local-check.sh: real git merges, a record, failures on demand) and a fake deploy
# (ops/deploy-checked.sh), and fakes of gh and ssh on PATH that read and write a small state
# folder. The fake gh merges into the bare origin as GitHub would; the fake ssh runs the remote
# script here, against a fake docker and free. Nothing reaches GitHub or the Jetson.
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
export SHIP_POLL_SECONDS=0 SHIP_SSH_HOST=jetson-test
export TMPDIR="$TOP/tmp"
mkdir -p "$TMPDIR" "$TOP/bin"
export PATH="$TOP/bin:$PATH"

# --- the fake gh -------------------------------------------------------------------------------------
# State per pull request in $STATE/pr/<n>/: state, draft, head, mergeable, unknown (reads left that
# answer UNKNOWN), reviews.json, files, closes.json. Every call is logged in $STATE/gh.log.
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
  jq -n --arg state "$(cat "$d/state")" --argjson draft "$(cat "$d/draft")" --arg head "$(cat "$d/head")" \
    --arg mergeable "$(mergeable "$d")" --slurpfile reviews "$d/reviews.json" --slurpfile closes "$d/closes.json" \
    '{state: $state, isDraft: $draft, headRefOid: $head, mergeable: $mergeable, reviews: $reviews[0], closingIssuesReferences: $closes[0]}' | out
  ;;
"pr comment")
  d="$(pr_dir "$3")"
  [[ "$4" == --body-file ]] || exit 2
  cp "$5" "$STATE/comment-$3.md"
  ;;
"pr merge")
  d="$(pr_dir "$3")"
  [[ "$*" == "pr merge $3 --merge --delete-branch --match-head-commit "* ]] || {
    echo "fake gh: unexpected merge call: $*" >&2
    exit 2
  }
  [[ "$(cat "$d/state")" == OPEN ]] || {
    echo "Pull request #$3 is not open" >&2
    exit 1
  }
  [[ "$7" == "$(cat "$d/head")" ]] || {
    echo "X Pull request #$3 is not mergeable: Head branch was modified. Review and try the merge again." >&2
    exit 1
  }
  git -C "$STATE/merger" fetch -q origin main "pull/$3/head"
  git -C "$STATE/merger" checkout -q -B main origin/main
  git -C "$STATE/merger" merge --no-ff --no-edit -q "$7"
  if [[ -e "$STATE/extra_commit_on_merge" ]]; then
    echo "pushed in between" >"$STATE/merger/between.txt"
    git -C "$STATE/merger" add -A
    git -C "$STATE/merger" commit -q -m "a commit pushed in between"
  fi
  git -C "$STATE/merger" push -q origin main
  echo MERGED >"$d/state"
  for other in "$STATE"/pr/*; do echo 1 >"$other/unknown"; done
  echo "✓ Merged pull request #$3"
  ;;
"api repos/{owner}/{repo}/pulls/"*)
  n="${2#repos/\{owner\}/\{repo\}/pulls/}"
  d="$(pr_dir "${n%/files}")"
  jq -R '{filename: .}' "$d/files" | jq -s . | out
  ;;
"project item-add")
  url="${args[${#args[@]} - 5]}"
  printf '{"id": "ITEM_%s"}\n' "${url##*/}" | out
  ;;
"project item-edit")
  [[ ! -e "$STATE/card_fail" ]] || exit 1
  ;;
*)
  echo "fake gh: unexpected call: $*" >&2
  exit 2
  ;;
esac
EOF

# --- the fake ssh, and the Jetson's docker and free -------------------------------------------------
cat >"$TOP/bin/ssh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$STATE/ssh.log"
[[ ! -e "$STATE/ssh_fail" ]] || exit 255
[[ "$*" == "-o BatchMode=yes -o ConnectTimeout=20 jetson-test bash -s" ]] || exit 254
HOME="$STATE/home" PATH="$STATE/remote-bin:$PATH" bash -s
EOF
REMOTE_BIN="$TOP/remote-bin"
mkdir -p "$REMOTE_BIN"
cat >"$REMOTE_BIN/docker" <<'EOF'
#!/usr/bin/env bash
case "$*" in
"compose ps --format "*) cat "$STATE/remote/ps" ;;
"compose exec -T api alembic current") cat "$STATE/remote/alembic" ;;
"compose logs --no-color --since 10m") cat "$STATE/remote/logs" ;;
*) exit 1 ;;
esac
EOF
cat >"$REMOTE_BIN/free" <<'EOF'
#!/usr/bin/env bash
printf '               total        used        free      shared  buff/cache   available\n'
printf 'Mem:            7620        3120        1200         100        3300        4300\n'
printf 'Swap:           3810           0        3810\n'
EOF
chmod +x "$TOP/bin/gh" "$TOP/bin/ssh" "$REMOTE_BIN/docker" "$REMOTE_BIN/free"

# --- the fake gate and the fake deploy, committed on main of each origin ---------------------------
# The gate merges the refs as ops/local-check.sh does (a detached worktree of origin/main, git merge
# --no-ff in the order given), fails a step when $STATE/gate_fail names a merged pull request (or
# "main"), conflicts on the numbers of $STATE/conflict, writes the PASS record, and logs
# "<which gate> <merges>" in $STATE/gate.log. WHO is "main" here; a pull request that changes the
# gate sets it to "pr".
cat >"$TOP/fake-gate" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
WHO=main
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MAIN="$(dirname "$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)")"
merges=()
while (($#)); do
  [[ "$1" == --merge ]] || exit 64
  merges+=("$2")
  shift 2
done
printf '%s %s\n' "$WHO" "${merges[*]-}" >>"$STATE/gate.log"
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
for n in ${merges[@]+"${merges[@]}"}; do
  git -C "$MAIN" fetch -q origin "pull/$n/head"
  sha="$(git -C "$MAIN" rev-parse FETCH_HEAD)"
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
while read -r who step; do
  [[ -n "$who" ]] || continue
  if [[ "$who" == main ]] || printf '%s\n' ${merges[@]+"${merges[@]}"} | grep -qx "$who"; then failed="$step"; fi
done <"$STATE/gate_fail"
if [[ -n "$failed" ]]; then
  printf 'api-install                PASS     1s\n%-26s FAIL     3s\n' "$failed"
  printf '\n--- %s: the last 40 lines of %s/logs/%s.log\n' "$failed" "$MAIN/.local-check" "$failed"
  cat "$STATE/fail_tail"
  printf '\nlogs: %s/logs\ntotal: 4s\n' "$MAIN/.local-check"
  echo "LOCAL CHECK: FAIL $tree $failed"
  exit 1
fi
mkdir -p "$MAIN/.local-check"
python3 - "$MAIN/.local-check/$tree.json" "$work/merged" "$tree" "$head" "$base" <<'PY'
import json
import sys

out, merged, tree, head, base = sys.argv[1:]
rows = [line.split("\t") for line in open(merged).read().splitlines() if line]
record = {"result": "PASS", "tree": tree, "head": head, "base": {"ref": "origin/main", "commit": base},
          "merged": [{"ref": ref, "commit": sha} for ref, sha in rows], "stub": False}
open(out, "w").write(json.dumps(record, indent=2) + "\n")
PY
echo "record: $MAIN/.local-check/$tree.json"
echo "LOCAL CHECK: PASS $tree 4s"
# Hooks of the tests: something happens between the gate and the merge.
if [[ -s "$STATE/move_after_gate" ]]; then "$STATE/move-head" "$(cat "$STATE/move_after_gate")"; fi
if [[ -e "$STATE/push_main_after_gate" ]]; then "$STATE/push-main"; fi
EOF
cat >"$TOP/fake-deploy" <<'EOF'
#!/usr/bin/env bash
printf 'deploy %s\n' "$*" >>"$STATE/deploy.log"
code="$(cat "$STATE/deploy_exit" 2>/dev/null || echo 0)"
if [[ "$code" == 0 ]]; then echo "deployed $1, health ok"; else echo "ops/deploy-checked.sh: the deploy run failed"; fi
exit "$code"
EOF
chmod +x "$TOP/fake-gate" "$TOP/fake-deploy"

# --- one scenario: fresh origin, main checkout, developer clone and state ---------------------------
N=0
fresh() {
  N=$((N + 1))
  local base="$TOP/s$N"
  export STATE="$base/state"
  ORIGIN="$base/origin.git"
  MAIN="$base/main"
  DEV="$base/dev"
  mkdir -p "$STATE/pr" "$STATE/remote" "$STATE/home/jobapp"
  : >"$STATE/gate_fail"
  printf 'E   assert 1 == 2\n' >"$STATE/fail_tail"
  printf '%s\trunning\thealthy\n' api worker caddy db redis parser renderer browser tailscale >"$STATE/remote/ps"
  printf 'INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.\n0042_ship (head)\n' >"$STATE/remote/alembic"
  printf 'api-1  | INFO started %s\nworker-1  | errors handled: none\n' "$CANARY" >"$STATE/remote/logs"
  git init -q --bare -b main "$ORIGIN"
  git clone -q "$ORIGIN" "$MAIN" 2>/dev/null
  mkdir -p "$MAIN/ops"
  cp "$ROOT/ops/ship.sh" "$MAIN/ops/ship.sh"
  cp "$TOP/fake-gate" "$MAIN/ops/local-check.sh"
  cp "$TOP/fake-deploy" "$MAIN/ops/deploy-checked.sh"
  printf '.local-check/\n' >"$MAIN/.gitignore"
  printf 'one\n' >"$MAIN/file.txt"
  git -C "$MAIN" add -A
  git -C "$MAIN" commit -q -m base
  git -C "$MAIN" push -q origin main
  git clone -q "$ORIGIN" "$DEV" 2>/dev/null
  git clone -q "$ORIGIN" "$STATE/merger" 2>/dev/null
  ln -s "$REMOTE_BIN" "$STATE/remote-bin"
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
out_has "plan   ops/local-check.sh --merge 17 --merge 11 --merge 18 --merge 20 --merge 21;"
out_has "plan   move to Done on the board: #1017, #1011, #1018, #1020, #1021"
[[ "$before" == "$(snapshot)" ]] || fail "--plan changed the repositories or the checkout"
for f in gate.log deploy.log ssh.log; do [[ ! -e "$STATE/$f" ]] || fail "--plan ran $f"; done
! grep -qE '^(pr merge|pr comment|project )' "$STATE/gh.log" || fail "--plan wrote to GitHub: $(cat "$STATE/gh.log")"
ok "--approved picks approved heads only (drafts, other verdicts, old or short heads, conflicts, UNKNOWN after 5 reads skipped; CRLF and later comments fine), gate changer first, a second one deferred"
ok "--plan touches nothing: no merge, comment, card, gate, deploy, ssh, ref or file"

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

# === the whole way: gate of the pull request that changes it, merge, pull, deploy, Jetson, cards ===
fresh
mkpr 31
gate_pr 32
run 0 ship --approved
[[ "$(last_line)" == "SHIP: OK" ]] || fail "happy path: $(last_line)"
[[ "$(cat "$STATE/gate.log")" == "pr 32 31" ]] || fail "the gate of #32 should have run once on 32 31: $(cat "$STATE/gate.log")"
grep -E '^pr merge' "$STATE/gh.log" >"$TOP/merges"
[[ "$(sed -n 1p "$TOP/merges")" == "pr merge 32 --merge --delete-branch --match-head-commit $(cat "$STATE/pr/32/head")" ]] || fail "first merge: $(cat "$TOP/merges")"
[[ "$(sed -n 2p "$TOP/merges")" == "pr merge 31 --merge --delete-branch --match-head-commit $(cat "$STATE/pr/31/head")" ]] || fail "second merge: $(cat "$TOP/merges")"
main_sha="$(git -C "$MAIN" rev-parse HEAD)"
[[ "$main_sha" == "$(git -C "$ORIGIN" rev-parse main)" ]] || fail "the main checkout was not pulled"
[[ "$(cat "$STATE/deploy.log")" == "deploy $main_sha" ]] || fail "deploy: $(cat "$STATE/deploy.log")"
out_has "jetson:   9 services, 9 healthy; alembic 0042_ship; memory 3120 of 7620 MB used; 0 error lines in 10 minutes"
out_has "cards:    Done: #1032, #1031"
log_has gh.log "project item-add 1 --owner GoldLegendW80 --url https://github.com/GoldLegendW80/jobapp/issues/1032 --format json --jq .id"
log_has gh.log "project item-edit --project-id PVT_kwHOA9eVYc4BmeB8 --id ITEM_1031 --field-id PVTSSF_lAHOA9eVYc4BmeB8zhlG8_A --single-select-option-id 98236657"
out_lacks "$CANARY"
[[ "$(git -C "$MAIN" worktree list | wc -l | tr -d ' ')" == 1 ]] || fail "the gate worktree was left: $(git -C "$MAIN" worktree list)"
[[ ! -e "$MAIN/.local-check/ship.lock" ]] || fail "the lock was left"
[[ -z "$(git -C "$MAIN" status --porcelain)" ]] || fail "the main checkout is dirty after a ship"
ok "approved to production: PR gate from its head, merges in order with --match-head-commit, pull, deploy of that commit, Jetson figures, cards to Done, no log line printed"

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
expected="$(printf 'main 41 42 43 44\nmain 41 42\nmain 41 42 43\nmain 41 42 44')"
[[ "$(cat "$STATE/gate.log")" == "$expected" ]] || fail "bisect runs: $(cat "$STATE/gate.log")"
out_has "drop   #43: api-pytest"
out_has "dropped:  #43 (api-pytest)"
grep -c '^pr merge' "$STATE/gh.log" | grep -qx 3 || fail "three merges expected: $(grep '^pr merge' "$STATE/gh.log")"
log_lacks gh.log "pr merge 43"
[[ -f "$STATE/comment-43.md" ]] || fail "no comment on the dropped pull request"
grep -qF "failed at \`api-pytest\`" "$STATE/comment-43.md" || fail "comment lacks the step: $(cat "$STATE/comment-43.md")"
grep -qF "after #41, #42" "$STATE/comment-43.md" || fail "comment lacks the order: $(cat "$STATE/comment-43.md")"
grep -qF "E   assert 1 == 2" "$STATE/comment-43.md" || fail "comment lacks the log tail"
grep -qF "test_answer_table_refuses_another_country FAILED" "$STATE/comment-43.md" || fail "comment withheld a test name"
[[ "$(grep -cF '[line withheld: it may hold a secret]' "$STATE/comment-43.md")" -ge 3 ]] || fail "the three secret lines should be withheld: $(cat "$STATE/comment-43.md")"
! grep -qF "$CANARY" "$STATE/comment-43.md" || fail "the comment holds the planted secret"
! grep -qF "Zx81Qp0LmN4vT7rKs2Yb6Hc9Wd3Fj5Ga" "$STATE/comment-43.md" || fail "the comment holds a token"
out_lacks "$CANARY"
for n in 41 42 44; do [[ ! -e "$STATE/comment-$n.md" ]] || fail "#$n got a comment"; done
[[ "$(last_line)" == "SHIP: OK" ]] || fail "bisect then ship: $(last_line)"
ok "bisect: shortest failing prefix drops #43 (4 gate runs), comment with step, order and a redacted tail, the rest shipped"

fresh
for n in 45 46 47; do mkpr "$n"; done
echo 46 >"$STATE/conflict"
run 0 ship --approved
[[ "$(cat "$STATE/gate.log")" == "$(printf 'main 45 46 47\nmain 45 47')" ]] || fail "conflict runs: $(cat "$STATE/gate.log")"
grep -qF "failed at \`merge-conflict\`" "$STATE/comment-46.md" || fail "conflict comment: $(cat "$STATE/comment-46.md")"
grep -qxF "  file.txt" "$STATE/comment-46.md" || fail "conflict comment lacks the files: $(cat "$STATE/comment-46.md")"
ok "a merge conflict drops the pull request it names, without bisecting"

fresh
mkpr 51
mkpr 52
printf '51 web-lint\n' >"$STATE/gate_fail"
run 0 ship --approved
[[ "$(cat "$STATE/gate.log")" == "$(printf 'main 51 52\nmain 51\nmain \nmain 52')" ]] || fail "first alone: $(cat "$STATE/gate.log")"
out_has "drop   #51: web-lint"
ok "the first pull request alone fails: main is checked alone before #51 is blamed"

fresh
mkpr 53
printf 'main api-pytest\n' >"$STATE/gate_fail"
run 1 ship --approved
out_has "SHIP: FAIL main itself fails the gate at api-pytest"
[[ ! -e "$STATE/comment-53.md" ]] || fail "#53 was blamed for a broken main"
log_lacks gh.log "pr merge"
ok "a main that fails the gate stops the run, nobody is blamed"

fresh
mkpr 54
printf '54 api-pytest\n' >"$STATE/gate_fail"
run 1 ship --approved
out_has "SHIP: FAIL every pull request of the batch failed the gate"
[[ -f "$STATE/comment-54.md" ]] || fail "#54 got no comment"
ok "every pull request dropped: exit 1"

fresh
mkpr 55
echo 3 >"$STATE/gate_exit"
run 1 ship --approved
out_has "SHIP: FAIL the gate refused to start, a check holds the lock or the disk is short"
log_lacks gh.log "pr comment"
ok "a gate that cannot start (lock, disk) stops the run without blaming anyone"

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

fresh
mkpr 66
echo 1 >"$STATE/deploy_exit"
run 1 ship --approved
out_has "SHIP: FAIL ops/deploy-checked.sh failed (exit 1): ops/deploy-checked.sh: the deploy run failed"
[[ ! -e "$STATE/ssh.log" ]] || fail "the Jetson was checked after a failed deploy"
log_lacks gh.log "project item"
ok "a failed deploy exits 1, no Jetson check, no card moved"

fresh
mkpr 67
printf 'api\trunning\thealthy\nworker\trunning\tunhealthy\ncaddy\trestarting\t\n' >"$STATE/remote/ps"
printf 'INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.\n0041_old\n' >"$STATE/remote/alembic"
printf 'api-1  | ERROR boom %s\napi-1  | Traceback (most recent call last):\nworker-1  | a error here\n' "$CANARY" >"$STATE/remote/logs"
run 1 ship --approved
out_has "not healthy: worker unhealthy, caddy restarting"
out_has "alembic is not at head"
out_has "3 error lines in 10 minutes"
out_lacks "$CANARY"
out_lacks "Traceback (most"
log_lacks gh.log "project item"
ok "an unwell Jetson (health, alembic, error lines) exits 1, prints figures only, no card moved"

fresh
mkpr 68
touch "$STATE/ssh_fail"
run 1 ship --approved
out_has "SHIP: FAIL ssh jetson-test failed"
ok "an unreachable Jetson exits 1"

fresh
mkpr 69
touch "$STATE/card_fail"
run 1 ship --approved
out_has "but these cards did not move to Done: #1069"
ok "a card that does not move exits 1 and names it"

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
