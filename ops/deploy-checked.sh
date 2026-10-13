#!/usr/bin/env bash
# Deploy a commit of main to the Jetson, and only one whose tree passed ops/local-check.sh on this
# Mac (issue #116; CONTRIBUTING.md, Merging). The deploy itself is .github/workflows/deploy.yml on
# the Jetson's own runner, started here on demand: this script dispatches it, waits for it and
# asks the public health URL.
#
#   ops/deploy-checked.sh                    origin/main, after a fetch
#   ops/deploy-checked.sh <ref>              another commit that is on origin/main
#   ops/deploy-checked.sh --dry-run [<ref>]  say what would be dispatched, start nothing
#   ops/deploy-checked.sh --allow-branch <ref>   a commit that is NOT on origin/main (a hot fix
#                                                branch): it still needs a PASS record of its tree
#
# The rule it holds: the tree of the commit (git rev-parse <ref>^{tree}) must have a PASS record
# .local-check/<tree sha>.json in the main checkout, written by a full run of ops/local-check.sh.
# After a batch is merged on GitHub in the order that was checked, the tree of origin/main is the
# tree that was checked; when it is not (another order, a commit pushed in between), there is no
# record and this script refuses: run ops/local-check.sh on main, then come back.
#
# Then: gh workflow run deploy.yml -f ref=<full sha>; the run is found by the time of the dispatch
# and the commit of main the workflow runs from; gh run watch until it ends; its conclusion; on
# success, the public health URL (README.md, Tailscale: https://jobapp.taild83d16.ts.net, or
# DEPLOY_HEALTH_URL) must answer.
#
# Exit status: 0 deployed and healthy (or a dry run that would dispatch), 1 refused or the deploy
# failed, 64 wrong arguments.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROG="ops/deploy-checked.sh"
WORKFLOW="deploy.yml"
HEALTH_URL="${DEPLOY_HEALTH_URL:-https://jobapp.taild83d16.ts.net/api/health}"

die() {
  printf '%s: %s\n' "$PROG" "$*" >&2
  exit 1
}
usage_error() {
  printf '%s: %s\n' "$PROG" "$*" >&2
  printf 'usage: %s [--dry-run] [--allow-branch] [<ref>]\n' "$PROG" >&2
  exit 64
}
info() { printf '   %s\n' "$*"; }

DRY_RUN=0
ALLOW_BRANCH=0
REF=""
while (($#)); do
  case "$1" in
  --dry-run) DRY_RUN=1 ;;
  --allow-branch) ALLOW_BRANCH=1 ;;
  -h | --help)
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  -*) usage_error "unknown argument: $1" ;;
  *)
    [[ -z "$REF" ]] || usage_error "one ref at most, got '$REF' and '$1'"
    REF="$1"
    ;;
  esac
  shift
done
REF="${REF:-origin/main}"

COMMON="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" ||
  die "$ROOT is not in a git repository"
RECORDS="$(dirname "$COMMON")/.local-check"

git -C "$ROOT" fetch -q origin 2>/dev/null || die "git fetch origin failed: what is on main cannot be known, nothing was dispatched"
sha="$(git -C "$ROOT" rev-parse --verify -q "$REF^{commit}")" || die "$REF names no commit"
tree="$(git -C "$ROOT" rev-parse "$sha^{tree}")"
main_sha="$(git -C "$ROOT" rev-parse --verify -q 'origin/main^{commit}')" || die "origin/main names no commit"

if ! git -C "$ROOT" merge-base --is-ancestor "$sha" "$main_sha"; then
  ((ALLOW_BRANCH)) || die "refused: $REF ($sha) is not on origin/main. Only main is deployed; --allow-branch deploys a branch on purpose."
  info "NOTE: $sha is not on origin/main (--allow-branch)"
fi

record="$RECORDS/$tree.json"
[[ -f "$record" ]] ||
  die "refused: no PASS record for the tree of $REF ($tree, commit $sha). Run ops/local-check.sh on it first; a record is written only by a full check that passed."
verdict="$(python3 - "$record" "$tree" <<'PY'
import json
import sys

try:
    record = json.load(open(sys.argv[1]))
except (OSError, ValueError) as error:
    print(f"unreadable ({error.__class__.__name__})")
    raise SystemExit
if record.get("result") != "PASS" or record.get("tree") != sys.argv[2]:
    print("not a PASS of this tree")
elif record.get("stub"):
    print("written by a test run with stub steps, not by the real checks")
else:
    print(f"ok checked {record.get('date', 'at an unknown date')} in {record.get('seconds', '?')} s, {len(record.get('steps', []))} steps")
PY
)"
[[ "$verdict" == ok\ * ]] || die "refused: the record $record is $verdict"

info "commit $sha"
info "tree   $tree (${verdict#ok })"
if ((DRY_RUN)); then
  info "dry run, nothing started. It would run:"
  printf 'gh workflow run %s -f ref=%s\n' "$WORKFLOW" "$sha"
  info "then wait for that run and ask $HEALTH_URL"
  exit 0
fi

command -v gh >/dev/null || die "gh is not installed"
cd "$ROOT"
since="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
gh workflow run "$WORKFLOW" -f "ref=$sha" >/dev/null || die "the dispatch of $WORKFLOW failed, nothing was deployed"
info "dispatched $WORKFLOW for $sha at $since"

# The run of a dispatch has the commit of main the workflow file comes from as its head, and is
# created after the dispatch: the first such run is ours (the workflow's concurrency group lets
# one deploy of a ref run at a time).
run=""
for _ in $(seq 1 30); do
  run="$(gh run list --workflow "$WORKFLOW" --event workflow_dispatch --limit 10 \
    --json databaseId,createdAt,headSha \
    --jq "[.[] | select(.createdAt >= \"$since\" and .headSha == \"$main_sha\")] | sort_by(.createdAt) | .[0].databaseId // empty")" || run=""
  [[ -z "$run" ]] || break
  sleep 2
done
[[ -n "$run" ]] || die "the run of the dispatch did not appear in a minute (gh run list --workflow $WORKFLOW): its result is unknown, look before dispatching again"
info "run $run: waiting for the Jetson"
gh run watch "$run" --exit-status >/dev/null 2>&1 || true
conclusion="$(gh run view "$run" --json conclusion,url --jq '.conclusion + " " + .url')"
info "deploy: $conclusion"
[[ "$conclusion" == success\ * ]] || die "the deploy did not succeed (${conclusion%% *}): gh run view $run --log-failed"

curl -fsS --max-time 20 --retry 5 --retry-delay 3 --retry-all-errors -o /dev/null "$HEALTH_URL" ||
  die "the deploy succeeded and $HEALTH_URL does not answer"
info "healthy: $HEALTH_URL"
printf 'DEPLOYED %s\n' "$sha"
