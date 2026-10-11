#!/usr/bin/env bash
# Tests of the secret scan of CI: .github/scripts/secret_scan.sh, run as CI runs it, through the
# hook gitleaks-commits of .pre-commit-config.yaml (the gitleaks pre-commit pins).
#
#   tests/secret_scan_test.sh
#
# Each case is a throwaway repository under a temporary folder that holds this checkout's
# .pre-commit-config.yaml and scan script and a history made for the case, checked out as CI
# checks it out. A secret is planted where the case says, and the command of the CI step must
# fail, or pass, as the case says. The planted secret is the example of gitleaks' own README
# (rule sidekiq-secret, a made-up value); it is put together from parts here, so that this
# file holds no line a scanner reports.
#
# Needs git, and pre-commit (or uvx, which fetches the version ci.yml names) with network
# access the first time, to build the pinned gitleaks. PRE_COMMIT overrides the command.
# shellcheck disable=SC2016
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Run from a git hook, this script would inherit GIT_DIR and GIT_INDEX_FILE, and every git below
# would then work on the repository of that hook whatever folder it is given (it happened once
# to ci_plan_test.py). No GIT_ variable gets through.
for name in $(env | sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/\1/p'); do unset "$name"; done
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
out_has() { grep -qF -- "$1" "$WORK/out" || fail "output lacks '$1': $(tail -c 1500 "$WORK/out")"; }
out_lacks() { if grep -qF -- "$1" "$WORK/out"; then fail "output holds '$1'"; fi; }

# The state of this checkout, to compare at the end: nothing here may touch it. Where the files
# are no git checkout (an extracted archive) there is nothing to compare, and the tests run all
# the same: they only need the files.
in_checkout=1
if ! git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  in_checkout=0
  printf '   note: %s is no git checkout: the check that these tests leave it as it was is skipped\n' "$ROOT"
fi
checkout_state() {
  ((in_checkout)) || return 0
  git -C "$ROOT" rev-parse HEAD 2>/dev/null || true
  git -C "$ROOT" config --get core.bare 2>/dev/null || true
  { git -C "$ROOT" status --porcelain 2>/dev/null || true; } | cksum
}
state_before="$(checkout_state)"

# The command of the CI step, read from the workflow, word for word: no SKIP reaches it, and
# pre-commit is asked for the hook by its name and stage.
ci_line="$(sed -n 's/^        run: \(env -u SKIP uvx --from pre-commit==[0-9.]* pre-commit run gitleaks-commits.*\)$/\1/p' "$ROOT/.github/workflows/ci.yml")"
ci_precommit="$(sed -n 's/^        run: env -u SKIP uvx --from \(pre-commit==[0-9.]*\) pre-commit run gitleaks-commits.*$/\1/p' "$ROOT/.github/workflows/ci.yml")"
ci_args="run gitleaks-commits --hook-stage manual --verbose"
[[ -n "$ci_precommit" && "$ci_line" == "env -u SKIP uvx --from $ci_precommit pre-commit $ci_args" ]] ||
  fail "ci.yml does not run the secret scan as expected (env -u SKIP uvx --from pre-commit==<version> pre-commit $ci_args): '$ci_line'"
if [[ -n "${PRE_COMMIT:-}" ]]; then
  read -r -a precommit <<<"$PRE_COMMIT"
elif command -v pre-commit >/dev/null; then
  precommit=(pre-commit)
elif command -v uvx >/dev/null; then
  precommit=(uvx --from "$ci_precommit" pre-commit)
else
  fail "pre-commit is needed, or uvx to fetch it"
fi

example="$(printf 'export BUNDLE_ENTERPRISE__CONTRIBSYS__COM=%s:%s' cafebabe deadbeef)"
value="$(printf '%s:%s' cafebabe deadbeef)"
repo=""
count=0
g() { git -C "$repo" -c core.hooksPath=/dev/null -c commit.gpgsign=false -c user.name=t -c user.email=t@example.org "$@"; }
# new_repo: a repository whose main holds the configuration and the script of this checkout.
new_repo() {
  count=$((count + 1))
  repo="$WORK/repo-$count"
  mkdir -p "$repo/.github/scripts"
  git init -q -b main "$repo"
  cp "$ROOT/.pre-commit-config.yaml" "$repo/"
  cp "$ROOT/.github/scripts/secret_scan.sh" "$repo/.github/scripts/"
  echo "base" >"$repo/base.txt"
  g add -A
  g commit -q -m "base"
}
# commit <file> <content>: one commit on the current branch.
commit() {
  printf '%s\n' "$2" >"$repo/$1"
  g add -A
  g commit -q -m "change $1"
}
# as_pull_request <branch>: HEAD becomes what CI checks out for a pull request of that branch,
# the merge commit of main and the branch, on a clean tree.
as_pull_request() {
  g checkout -q --detach main
  g merge -q --no-ff -m "merge like refs/pull/N/merge" "$1"
  [[ -z "$(g status --porcelain)" && -z "$(g diff --cached --name-only)" ]] || fail "the checkout is not clean"
}
# ci_scan <expected exit> <VAR=value...>: the command of the CI step in $repo, with the
# variables a run of that kind has (given first, as the environment of the step; then the
# command as ci.yml writes it, `env -u SKIP` included); stdout and stderr together in $WORK/out.
ci_scan() {
  local want="$1" got=0
  shift
  # shellcheck disable=SC2086
  (cd "$repo" && env -u GITHUB_EVENT_NAME -u CI_PUSH_BEFORE -u SKIP "$@" env -u SKIP "${precommit[@]}" $ci_args) >"$WORK/out" 2>&1 || got=$?
  [[ "$got" == "$want" ]] || fail "the secret scan exited $got, expected $want ($*): $(tail -c 1500 "$WORK/out")"
}

step "the hook CI ran until now reads nothing in a checkout: a committed secret passes it"
new_repo
g checkout -q -b feature
commit leaked.env "$example"
as_pull_request feature
got=0
(cd "$repo" && "${precommit[@]}" run gitleaks --all-files --verbose) >"$WORK/out" 2>&1 || got=$?
[[ "$got" == 0 ]] || fail "the staged scan failed on a clean checkout (exit $got): if gitleaks now reads commits there, this file's reason has changed: $(tail -c 800 "$WORK/out")"
out_has "0 commits scanned"
ok "pre-commit run gitleaks --all-files on a pull request that commits the example secret: 0 commits scanned, passed"

step "the scan of CI: a pull request"
ci_scan 1 GITHUB_EVENT_NAME=pull_request
out_has "sidekiq-secret"
out_has "leaked.env"
out_has "pull request, each of its 1 commits"
out_has "REDACTED"
out_lacks "$value"
ok "a pull request that commits the example secret fails, with the rule and the file, and the value is never printed"
new_repo
g checkout -q -b feature
commit notes.txt "nothing to hide"
as_pull_request feature
ci_scan 0 GITHUB_EVENT_NAME=pull_request
ok "a clean pull request passes"
new_repo
g checkout -q -b feature
commit leaked.env "$example"
g rm -q leaked.env
g commit -q -m "remove it again"
as_pull_request feature
[[ ! -e "$repo/leaked.env" ]] || fail "the file should be gone from the tree"
ci_scan 1 GITHUB_EVENT_NAME=pull_request
out_has "leaked.env"
ok "a secret added by one commit and removed by the next is found: both commits would reach main"
new_repo
g checkout -q -b feature
commit a.txt "a"
g checkout -q -b other main
commit b.txt "b"
g checkout -q feature
g merge -q --no-ff --no-commit other >/dev/null 2>&1
printf '%s\n' "$example" >"$repo/by-hand.env"
g add -A
g commit -q -m "merge other into feature, with a line added by hand"
as_pull_request feature
ci_scan 1 GITHUB_EVENT_NAME=pull_request
out_has "by-hand.env"
out_has "as a whole"
ok "a secret put in by hand in a merge commit inside the branch, which no commit of its own shows, is found by the reading of the whole"
ci_scan 1 GITHUB_EVENT_NAME=pull_request SKIP=gitleaks
out_has "by-hand.env"
ci_scan 1 GITHUB_EVENT_NAME=pull_request SKIP=ruff-check,ruff-format,eslint,shellcheck,gitleaks
out_has "by-hand.env"
# Why ci.yml writes `env -u SKIP`: the hook of the scan has the id gitleaks, as the hook of a
# commit has, and pre-commit skips by id. Without it the same command passes, with nothing read.
got=0
# shellcheck disable=SC2086
(cd "$repo" && GITHUB_EVENT_NAME=pull_request SKIP=gitleaks "${precommit[@]}" $ci_args) >"$WORK/out" 2>&1 || got=$?
[[ "$got" == 0 ]] || fail "with SKIP=gitleaks and no env -u SKIP the scan exited $got: if pre-commit no longer skips it, the reason for env -u SKIP has changed"
out_has "Skipped"
ok "SKIP in the environment of the step does not reach the scan (env -u SKIP), which pre-commit would skip without a word otherwise"

step "the scan of CI: what a merge commit inside the branch held and the next commit took out"
# The case of the review, round 2: the secret is typed into a merge commit of the branch and
# removed by the commit after it. No commit of its own shows it and the tree at the end does
# not hold it; both commits reach main. As a pull request, as a push, as a manual run.
new_repo
before="$(g rev-parse main)"
g checkout -q -b feature
commit a.txt "a"
g checkout -q -b other main
commit b.txt "b"
g checkout -q feature
g merge -q --no-ff --no-commit other >/dev/null 2>&1
printf '%s\n' "$example" >"$repo/by-hand.env"
g add -A
g commit -q -m "merge other into feature, with a line added by hand"
g rm -q by-hand.env
g commit -q -m "take the line out again"
as_pull_request feature
[[ ! -e "$repo/by-hand.env" ]] || fail "the file should be gone from the tree"
ci_scan 1 GITHUB_EVENT_NAME=pull_request
out_has "by-hand.env"
out_has "sidekiq-secret"
out_lacks "$value"
g checkout -q main
g merge -q --no-ff -m "Merge pull request" feature
ci_scan 1 GITHUB_EVENT_NAME=push "CI_PUSH_BEFORE=$before"
out_has "by-hand.env"
ci_scan 1 GITHUB_EVENT_NAME=workflow_dispatch
out_has "the whole history of HEAD"
out_has "by-hand.env"
ok "a secret typed into a merge commit of the branch and removed by the next commit is found as a pull request, as a push and as a manual run"
# The same in the place where it happens: a conflict resolved by hand.
new_repo
g checkout -q -b feature
commit shared.txt "the line of feature"
g checkout -q -b other main
commit shared.txt "the line of other"
g checkout -q feature
if g merge -q --no-ff other >/dev/null 2>&1; then fail "the two branches were meant to conflict"; fi
printf '%s\n' "the line of feature" "$example" >"$repo/shared.txt"
g add -A
g commit -q -m "merge other into feature, the conflict resolved by hand"
commit shared.txt "the line of feature"
as_pull_request feature
if grep -qF "CONTRIBSYS" "$repo/shared.txt"; then fail "the secret should be gone from the tree"; fi
ci_scan 1 GITHUB_EVENT_NAME=pull_request
out_has "shared.txt"
ok "the same when the secret came in with a conflict resolved by hand"

step "the scan of CI: commit messages, and a pull request that changes the scan's own rules"
new_repo
g checkout -q -b feature
printf 'clean\n' >"$repo/notes.txt"
g add -A
g commit -q -m "notes" -m "for the record: $example"
leaking="$(g rev-parse HEAD)"
commit more.txt "more"
as_pull_request feature
ci_scan 1 GITHUB_EVENT_NAME=pull_request
out_has "the messages of its 2 commits"
out_has "a finding in the message of commit $leaking"
out_lacks "$value"
ok "a secret in a commit message alone, with clean files, is found and its commit named"
new_repo
g checkout -q -b feature
commit allowed.env "$example # gitleaks:allow"
as_pull_request feature
ci_scan 0 GITHUB_EVENT_NAME=pull_request
out_has "NOTE: this pull request changes what the scan itself goes by"
out_has "Added lines that hold gitleaks:allow: 1"
ok "a line marked gitleaks:allow passes, as it does at a commit (the same rules and the same allowlist), and the log says the pull request added one"
new_repo
g checkout -q -b feature
printf '[extend]\nuseDefault = true\n\n[[allowlists]]\ndescription = "everything"\npaths = [%s]\n' "'''.*'''" >"$repo/.gitleaks.toml"
commit leaked.env "$example"
as_pull_request feature
ci_scan 0 GITHUB_EVENT_NAME=pull_request
out_has "NOTE: this pull request changes what the scan itself goes by"
out_has ".gitleaks.toml"
ok "a pull request is judged by the rules of its own tree: one that brings a .gitleaks.toml allowing every path passes, and the log says that it changes the scan's rules"
new_repo
g checkout -q -b feature
commit notes.txt "nothing to hide"
as_pull_request feature
ci_scan 0 GITHUB_EVENT_NAME=pull_request
out_lacks "NOTE:"
ok "a pull request that touches none of them gets no such line"

step "the scan of CI: a push to main, a manual run"
new_repo
before="$(g rev-parse main)"
g checkout -q -b feature
commit leaked.env "$example"
g checkout -q main
g merge -q --no-ff -m "Merge pull request" feature
ci_scan 1 GITHUB_EVENT_NAME=push "CI_PUSH_BEFORE=$before"
out_has "push, each of its 2 commits"
out_has "leaked.env"
commit later.txt "a later push, clean"
ci_scan 0 GITHUB_EVENT_NAME=push "CI_PUSH_BEFORE=$(g rev-parse HEAD^)"
ok "a push is read from the commit main was at before it: the push that brings the secret fails, a later clean push passes"
ci_scan 1 GITHUB_EVENT_NAME=push CI_PUSH_BEFORE=0000000000000000000000000000000000000000
out_has "everything is read"
ci_scan 1 GITHUB_EVENT_NAME=push CI_PUSH_BEFORE=1234567890123456789012345678901234567890
out_has "everything is read"
ci_scan 1 GITHUB_EVENT_NAME=push
ci_scan 1 GITHUB_EVENT_NAME=workflow_dispatch
out_has "the whole history of HEAD"
ci_scan 1
ok "a push without a readable before, a manual run and a run with no event read the whole history, and find the old secret"
g checkout -q -b clean-feature
commit notes.txt "nothing to hide"
as_pull_request clean-feature
ci_scan 0 GITHUB_EVENT_NAME=pull_request
ok "a pull request is judged on what it brings: a secret that main already holds is the business of main's own run"

step "a checkout without the history is refused"
new_repo
g checkout -q -b feature
commit leaked.env "$example"
commit more.txt "more"
as_pull_request feature
git clone -q --depth 2 "file://$repo" "$WORK/shallow" 2>/dev/null
[[ "$(git -C "$WORK/shallow" rev-parse HEAD)" == "$(g rev-parse HEAD)" ]] || fail "the shallow clone is not at the merge commit"
repo="$WORK/shallow"
ci_scan 1 GITHUB_EVENT_NAME=pull_request
out_has "this checkout is shallow"
out_lacks "no leaks found"
ok "a shallow checkout (fetch-depth 2) is refused: the commit that holds the secret would not be read"

step "the script by hand"
new_repo
g checkout -q -b feature
commit leaked.env "$example"
got=0
(cd "$repo" && .github/scripts/secret_scan.sh no-such-branch..HEAD) >"$WORK/out" 2>&1 || got=$?
[[ "$got" == 1 ]] || fail "a range git cannot read exited $got"
if command -v gitleaks >/dev/null; then out_has "git cannot read the range"; fi
got=0
(cd "$repo" && .github/scripts/secret_scan.sh a b) >"$WORK/out" 2>&1 || got=$?
[[ "$got" == 1 ]] || fail "two arguments exited $got"
got=0
(cd "$WORK" && "$ROOT/.github/scripts/secret_scan.sh") >"$WORK/out" 2>&1 || got=$?
[[ "$got" == 1 ]] || fail "outside a repository the script exited $got"
ok "a range git cannot read, a wrong number of arguments and a folder that is no repository fail"

[[ "$(checkout_state)" == "$state_before" ]] || fail "this checkout was changed by the tests"
printf '\nsecret_scan_test: %s checks passed\n' "$passed"
