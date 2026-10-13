#!/usr/bin/env bash
# A fresh CI run of an open pull request, tested against the base branch as it is now
# (issue #91).
# Since issue #116 ci.yml has no pull request trigger, so the close and reopen done here starts
# no run: the script is kept for the day that trigger comes back. What a merge waits for is
# ops/local-check.sh.
#
#   ops/ci-rerun.sh <pull request number>            wait for the fresh merge commit, close, reopen
#   ops/ci-rerun.sh <pull request number> --check    only say whether the merge commit is fresh
#
# Why a script. CI runs a pull request at its merge commit (refs/pull/<n>/merge): the branch
# merged into the base, the workflow file included. When main moves, GitHub recomputes that
# commit lazily, and every obvious way to ask for a new run misses it:
# - `gh run rerun` runs the old commit again, with the workflow of that day;
# - `gh workflow run ci.yml --ref <branch>` runs the branch alone, with the branch's own copy of
#   the workflow, and is not a pull request run (it runs every unit, it is not what merges);
# - closing and reopening at once starts a run from the merge commit GitHub still has, the stale
#   one (seen on 2026-10-10: runs started on the old workflow and failed within seconds).
# What works: ask GitHub for the pull request until the merge commit it reports is the merge of
# the base as it is now and the branch's head, then close and reopen. The branch gets no commit,
# so a review of its head stays valid. A branch that conflicts with the base has no merge commit
# and no run: this script says so and changes nothing. A draft is not refused: CI runs on drafts,
# and a draft that is closed and reopened is still a draft. A merged pull request is (its state
# is closed).
#
# Seen once, on 2026-10-11, with a draft pull request against another branch than main (#94):
# after three pushes to its base GitHub kept reporting the merge commit of the old base for as
# long as it was asked. The script then gives up after its wait with nothing changed, which is
# its job; there only a commit on the branch gave a fresh run.
#
# Between the close and the reopen the pull request is closed for a moment. If the script is
# stopped there (Ctrl C, a kill, a closed terminal), it asks GitHub for the state and reopens;
# when that fails too it says, in capitals, that the pull request is closed, with the command.
# A project board whose built in workflow moves closed items may move the card on the close and
# again on the reopen (not checked on this repository's board).
#
# Needs gh, signed in with write access. Settings: CI_RUNNER_REPO (GoldLegendW80/jobapp),
# CI_RERUN_WAIT_S (how long to wait for GitHub's merge commit, default 120), CI_RERUN_POLL_S
# (seconds between two questions, default 3).
set -euo pipefail

REPO="${CI_RUNNER_REPO:-GoldLegendW80/jobapp}"
WAIT="${CI_RERUN_WAIT_S:-120}"
POLL="${CI_RERUN_POLL_S:-3}"

die() {
  printf 'ci-rerun: %s\n' "$*" >&2
  exit 1
}
info() { printf 'ci-rerun: %s\n' "$*"; }

pr="${1:-}"
check="${2:-}"
[[ $# -ge 1 && $# -le 2 && "$pr" =~ ^[0-9]+$ && ("$check" == "" || "$check" == --check) ]] ||
  die "usage: ops/ci-rerun.sh <pull request number> [--check]"
[[ "$REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "CI_RUNNER_REPO must be owner/name"
[[ "$WAIT" =~ ^[0-9]+$ && "$POLL" =~ ^[0-9]+$ ]] || die "CI_RERUN_WAIT_S and CI_RERUN_POLL_S must be numbers"
command -v gh >/dev/null || die "gh is needed"

# pull <jq filter>: one field of the pull request, read from GitHub now.
pull() {
  gh api "repos/$REPO/pulls/$pr" --jq "$1" || die "GitHub did not answer about pull request $pr"
}

state="$(pull .state)" || exit 1
[[ "$state" == open ]] || die "pull request $pr is $state: only an open one can be run again"
base_ref="$(pull .base.ref)" || exit 1
head_ref="$(pull .head.ref)" || exit 1
head_sha="$(pull .head.sha)" || exit 1
base_sha="$(gh api "repos/$REPO/commits/$base_ref" --jq .sha)" || die "GitHub did not answer about $base_ref"
info "pull request $pr: $head_ref at ${head_sha:0:7}, to be merged into $base_ref at ${base_sha:0:7}"

deadline=$((SECONDS + WAIT))
while :; do
  mergeable="$(pull '.mergeable | tostring')" || exit 1
  merge_sha="$(pull '.merge_commit_sha // ""')" || exit 1
  if [[ "$mergeable" == false ]]; then
    die "pull request $pr conflicts with $base_ref: GitHub makes no merge commit for it, so CI cannot run. Merge or rebase $base_ref into $head_ref first; nothing was changed"
  fi
  if [[ "$mergeable" == true && -n "$merge_sha" ]]; then
    parents="$(gh api "repos/$REPO/commits/$merge_sha" --jq '[.parents[].sha] | join(" ")')" ||
      die "GitHub did not answer about the merge commit $merge_sha"
    [[ "$parents" != "$base_sha $head_sha" ]] || break
  fi
  ((SECONDS < deadline)) ||
    die "after ${WAIT}s GitHub still reports a merge commit that is not $base_ref at ${base_sha:0:7} plus ${head_sha:0:7} (did $base_ref or the branch move meanwhile?). Nothing was changed; run it again"
  sleep "$POLL"
done
if [[ "$check" == --check ]]; then
  info "GitHub's merge commit ${merge_sha:0:7} is $base_ref at ${base_sha:0:7} plus the branch: a run started now would test the right commit. Nothing was changed"
  exit 0
fi
info "GitHub's merge commit ${merge_sha:0:7} is $base_ref at ${base_sha:0:7} plus the branch: closing and reopening"

# closing: 1 from just before the close until the reopen is known to have worked or has been
# given up with its own loud message. While it is 1, the end of the script, whatever ends it,
# looks at the pull request and reopens it when it is closed.
closing=0
closed_loudly() {
  printf 'ci-rerun: PULL REQUEST %s IS CLOSED and could not be reopened: gh pr reopen %s --repo %s\n' "$pr" "$pr" "$REPO" >&2
}
on_exit() {
  local now
  ((closing)) || return 0
  closing=0
  now="$(gh api "repos/$REPO/pulls/$pr" --jq .state 2>/dev/null)" || now=unknown
  [[ "$now" != open ]] || return 0
  if gh pr reopen "$pr" --repo "$REPO" >/dev/null 2>&1; then
    printf 'ci-rerun: stopped between the close and the reopen: pull request %s is open again, and a run starts on its merge commit\n' "$pr" >&2
  else
    closed_loudly
  fi
}
trap on_exit EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

closing=1
if ! gh pr close "$pr" --repo "$REPO" >/dev/null; then
  closing=0
  die "could not close pull request $pr; nothing else was done"
fi
reopened=0
for _ in 1 2 3; do
  if gh pr reopen "$pr" --repo "$REPO" >/dev/null; then
    reopened=1
    break
  fi
  sleep "$POLL"
done
closing=0
if ((!reopened)); then
  closed_loudly
  exit 1
fi
info "reopened: a pull request run of CI starts on the fresh merge commit"
info "watch it: gh run list --repo $REPO --branch $head_ref --event pull_request --limit 3"
