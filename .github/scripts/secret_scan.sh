#!/usr/bin/env bash
# The secret scan of a CI run: gitleaks on the commits the run brings (review of #93).
#
#   pre-commit run gitleaks-commits --hook-stage manual    how CI runs it, and a person: pre-commit
#                                                          puts the gitleaks it pins on PATH
#   .github/scripts/secret_scan.sh [git log range]         with a gitleaks of your own on PATH,
#                                                          for instance origin/main..HEAD
#
# Why it exists. The gitleaks hook of pre-commit is `gitleaks git --pre-commit --staged`: it
# reads the staged changes. That is right at a commit, and it is nothing in a checkout, where
# nothing is staged: `pre-commit run --all-files` in CI passed whatever had been committed. Shown
# on 2026-10-11 in a throwaway clone with the example secret of gitleaks' own README, committed
# on a branch and merged as CI checks a pull request out: "0 commits scanned", passed.
#
# This script gives the same program, at the version .pre-commit-config.yaml pins, with the same
# rules (the repository's .gitleaks.toml and .gitleaksignore when it has them; today it has
# neither, so the default rules and no allowlist), the commits to read:
#
#   a pull request    the commits of the pull request (what the head has and the base has not:
#                     HEAD^1..HEAD^2 of the merge commit CI checks out), then the pull request
#                     as a whole (that merge commit against the base)
#   a push            the commits of the push (CI_PUSH_BEFORE..HEAD, with github.event.before),
#                     then the tip against its first parent (for the merge of a pull request:
#                     the pull request as a whole)
#   anything else     the whole history of HEAD: a manual run, a run by hand, and a pull request
#                     or a push whose range cannot be read (no merge commit, no "before", a
#                     forced push whose old tip is gone)
#
# The commits of a range are read three ways:
# - each commit on its own, so a secret added by one commit and removed by the next is found:
#   both commits reach main when the pull request is merged;
# - each merge commit for the lines somebody typed into it (git log --remerge-diff: what differs
#   from the mechanical merge). Plain git log prints no patch for a merge commit, so a secret
#   put in while resolving a merge inside the branch and taken out by the next commit was read
#   by nothing (review of #93, round 2);
# - the commit messages, through gitleaks stdin: a patch does not hold them.
#
# Not read, here or at a commit: a binary file (git prints no patch for one). And no scan in CI
# reads a commit whose run never ran: pushed, then removed by a forced push while the next push
# cancelled its run. Such a commit does not reach main, and stays on GitHub all the same.
#
# It needs the history of the range. A shallow checkout is refused: commits of the range may be
# missing there, and a commit whose parent is missing reads as if it had added every file.
# ci.yml fetches the whole history in the job that runs this (fetch-depth: 0).
#
# A pull request is judged by the rules of its own tree, as it is by its own copy of the
# workflow: it can bring a .gitleaks.toml, a .gitleaksignore or a gitleaks:allow comment that
# lets its secret through. The script says so in its log when a pull request touches any of
# them, or this script, or the hooks; a reviewer reads those as a change to the gate.
#
# Findings are printed with the secret redacted. Exit status 0: nothing found. 1: a finding, or a
# scan that could not run.
set -euo pipefail

die() {
  printf 'secret_scan: %s\n' "$*" >&2
  exit 1
}
info() { printf 'secret_scan: %s\n' "$*"; }

command -v gitleaks >/dev/null ||
  die "gitleaks is not on PATH. Run it through pre-commit: pre-commit run gitleaks-commits --hook-stage manual"
root="$(git rev-parse --show-toplevel 2>/dev/null)" || die "not inside a git repository"
cd "$root"
[[ "$(git rev-parse --is-shallow-repository)" == false ]] ||
  die "this checkout is shallow: commits of the range may be missing, and a commit without its parent reads as if it added every file. Fetch the whole history (actions/checkout: fetch-depth: 0)"
# Two options of git log carry the readings. A git that lacks one must stop the scan here, loudly:
# gitleaks would only log git's refusal and pass.
git log --remerge-diff -1 --format= HEAD >/dev/null 2>&1 ||
  die "this git ($(git --version)) does not know git log --remerge-diff (git 2.36 or later is needed): without it the lines typed into a merge commit are not read"
git log --diff-merges=first-parent -1 --format= HEAD >/dev/null 2>&1 ||
  die "this git ($(git --version)) does not know --diff-merges=first-parent (git 2.31 or later is needed)"

status=0
# failed <output of the scanner>: an ERR line in the scanner's own log counts as a failure
# whatever its exit status. gitleaks 8.30.1 exits 0 when the git log it starts fails ("0 commits
# scanned", an ERR line and a pass).
failed() {
  if grep -Eq '^[^ ]+ (ERR|FTL) ' <<<"$1"; then
    info "the scanner reported an error: counted as a failed reading"
    status=1
  fi
}
# scan <what it is, for the log> <git log options...>: one reading of patches. A finding or a
# failure of the scanner is remembered, and the readings after it still run. git log is tried
# with the same options first, for the reason given at `failed`.
scan() {
  local what="$1" out
  shift
  info "$what: git log -p $*"
  git log --format= "$@" >/dev/null 2>&1 || die "git log cannot read: $*"
  out="$(gitleaks git --no-banner --no-color --redact --verbose --log-opts="$*" . 2>&1)" || status=1
  printf '%s\n' "$out"
  failed "$out"
}
# messages <what it is> <range>: the commit messages of a range, as one text. A line of words
# closes each message, so that the end of one and the start of the next never read as one
# assignment. When the text holds a finding, each message is read again on its own to name the
# commit: the scanner cannot, it sees a text and no commits.
messages() {
  local what="$1" range="$2" out commit
  info "$what: git log --format=%B $range, through gitleaks stdin"
  out="$(git log --format='%B%n(end of the message)%n' "$range" | gitleaks stdin --no-banner --no-color --redact --verbose 2>&1)" || {
    status=1
    for commit in $(git rev-list "$range"); do
      git log -1 --format=%B "$commit" | gitleaks stdin --no-banner --no-color --redact >/dev/null 2>&1 ||
        info "a finding in the message of commit $commit"
    done
  }
  printf '%s\n' "$out"
  failed "$out"
}
# commits <range>: how many commits a range holds; stops the script on a range git cannot read.
commits() {
  git rev-list --count "$1" 2>/dev/null || die "git cannot read the range $1"
}
# read_range <what it is> <range>: the three readings of a range of commits.
read_range() {
  local what="$1" range="$2" count
  count="$(commits "$range")" || exit 1
  scan "$what, each of its $count commits, a merge commit for the lines typed into it" --remerge-diff "$range"
  messages "$what, the messages of its $count commits" "$range"
}

range="${1:-}"
event="${GITHUB_EVENT_NAME:-}"
before="${CI_PUSH_BEFORE:-}"
read -r -a parents <<<"$(git rev-list --parents -n 1 HEAD)"
whole="--diff-merges=first-parent -1 HEAD"

if [[ $# -gt 1 ]]; then
  die "usage: .github/scripts/secret_scan.sh [git log range]"
elif [[ -n "$range" ]]; then
  read_range "the range $range" "$range"
elif [[ "$event" == pull_request && "${#parents[@]}" -eq 3 ]]; then
  # What the scan itself goes by, when this pull request changes it: said, not judged.
  own="$(git diff --name-only HEAD^1 HEAD -- .gitleaks.toml .gitleaksignore .pre-commit-config.yaml .github/scripts/secret_scan.sh | tr '\n' ' ')"
  allows="$(git diff -U0 HEAD^1 HEAD | grep -c '^+.*gitleaks:allow' || true)"
  if [[ -n "$own" || "$allows" != 0 ]]; then
    info "NOTE: this pull request changes what the scan itself goes by. Files: ${own:-none}. Added lines that hold gitleaks:allow: $allows. To be reviewed as a change to the gate"
  fi
  read_range "pull request" "HEAD^1..HEAD^2"
  # shellcheck disable=SC2086
  scan "pull request, as a whole (its merge commit against the base)" $whole
elif [[ "$event" == push && "$before" =~ ^[0-9a-f]{40}$ && ! "$before" =~ ^0+$ ]] &&
  git cat-file -e "$before^{commit}" 2>/dev/null; then
  read_range "push" "$before..HEAD"
  # shellcheck disable=SC2086
  scan "push, its tip against its first parent" $whole
else
  info "no range to read for this run (event '${event:-none}'): everything is read"
  read_range "the whole history of HEAD" HEAD
fi

if ((status)); then
  die "a secret was found, or a reading failed (above). A secret that was committed is to be revoked where it is valid, whatever happens to the commit; a false alarm gets a gitleaks:allow comment on its line, or its fingerprint in .gitleaksignore, in the same pull request"
fi
info "nothing found"
