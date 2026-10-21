#!/usr/bin/env bash
# The jobs journeys on the Jetson through the public URL (./deploy.sh url), with e2e accounts and
# synthetic data loaded over HTTP only (E2E_SYNTHETIC=1), framed by the proof that Harald's jobs
# are untouched.
#
#   tests/e2e_jobs_jetson.sh        J4, web/e2e/jobs-edit.spec.ts: synthetic skills and a dozen
#                                   synthetic postings; evidence in tasks/evidence/jobs-j4-jetson/
#   tests/e2e_jobs_jetson.sh view   J7, web/e2e/jobs.spec.ts and jobs-map.spec.ts: 433 synthetic
#                                   postings with files, then the map's synthetic places; evidence
#                                   in tasks/evidence/jobs-j7-jetson/
#
# 1. Removes jobapp-test-pg-* containers stuck in "Created" on the Mac; refuses to start while
#    e2e rows are left on the Jetson unless E2E_CLEAN_FIRST=1.
# 2. Snapshots Harald's jobs: rows per status, sum of fit, fits set by hand, max updated_at,
#    events, files, and an md5 over every row's id, status, fit, notes and updated_at.
# 3. Runs the specs, one worker: jobs-edit with e2e-admin@example.org, the view specs with an
#    e2e-jobs-<run> and an e2e-jobs-map-<run> account each (all made with ./deploy.sh admin create).
# 4. Whatever happened, deletes the e2e accounts (their jobs, events, files and profile go with
#    them) and their sign in attempts, then proves the snapshot identical, no e2e row left, and no
#    failed sign in left for Harald's email in the 15 minute window.
#
# Settings as deploy.sh: DEPLOY_HOST (default jetson), DEPLOY_DIR (default ~/jobapp).
# OWNER (required): the e-mail address of the owner's account on the Jetson.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
HOST="${DEPLOY_HOST:-jetson}"
RDIR="${DEPLOY_DIR:-jobapp}"
RDIR="${RDIR#\~/}"
HARALD="${OWNER:-}"
ADMIN="e2e-admin@example.org"
E2E_LIKE="e2e-%@example.org"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

step() { printf '\n== %s\n' "$*"; }
info() { printf '   %s\n' "$*"; }
die() {
  printf 'e2e_jobs_jetson.sh: %s\n' "$*" >&2
  exit 1
}
[[ -n "$HARALD" ]] || die "OWNER is not set: run it again with OWNER=<the e-mail address of the owner's account on the Jetson>"
[[ "$HARALD" =~ ^[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+$ ]] || die "OWNER is not an email address"
USAGE="usage: tests/e2e_jobs_jetson.sh [edit|view] (no argument: edit, the J4 journey)"
(($# <= 1)) || die "$USAGE"
JOURNEY="${1:-edit}"
case "$JOURNEY" in
edit)
  EVIDENCE_NAME=jobs-j4-jetson
  SPECS=(jobs-edit.spec.ts)
  RUN_ENV=(E2E_ADMIN_EMAIL="$ADMIN")
  ;;
view)
  EVIDENCE_NAME=jobs-j7-jetson
  SPECS=(jobs.spec.ts jobs-map.spec.ts)
  # Both specs name a shot "filtered": the prefix keeps them apart in one folder.
  RUN_ENV=(E2E_SHOT_PREFIX='{spec}-')
  ;;
*) die "$USAGE" ;;
esac
EVIDENCE="$ROOT/tasks/evidence/$EVIDENCE_NAME"
LOG="$EVIDENCE/run.log"

# Replaces the owner's address in all that is printed through it, remote errors and bash -x traces included.
masked() {
  LC_ALL=C awk -v owner="$HARALD" -v shown="(the owner's address)" '
    BEGIN { o = tolower(owner); n = length(o) }
    {
      line = $0
      out = ""
      while (n && (i = index(tolower(line), o))) {
        out = out substr(line, 1, i - 1) shown
        line = substr(line, i + n)
      }
      print out line
      fflush()
    }'
}

sql() { # the SQL on stdin in the Jetson's database, unaligned, fields separated by |
  # terse: no LINE context, which quotes the owner's address cut to 60 columns where masked cannot find it
  # shellcheck disable=SC2016 # $POSTGRES_* expand in the db container
  ssh -o BatchMode=yes "$HOST" "cd $(printf '%q' "$RDIR") && docker compose -f compose.yml exec -T db sh -c" \
    "'psql -X -q -v ON_ERROR_STOP=1 -v VERBOSITY=terse -tA -F \"|\" -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"'"
}

snapshot() { # Harald's jobs, one field per line
  sql <<SQL
with me as (select id from users where email = '$HARALD'),
     j as (select * from jobs where user_id = (select id from me))
select 'jobs=' || (select count(*) from j),
       'per_status=' || coalesce((select string_agg(status::text || ':' || n, ',' order by status)
                                  from (select status, count(*) n from j group by status) s), 'none'),
       'fit_sum=' || coalesce((select sum(fit) from j), 0),
       'fits_manual=' || (select count(*) from j where fit_overridden),
       'max_updated_at=' || coalesce((select max(updated_at)::text from j), 'none'),
       'events=' || (select count(*) from job_events where user_id = (select id from me)),
       'files=' || (select count(*) from job_files f join j on j.id = f.job_id),
       'rows_md5=' || coalesce((select md5(string_agg(id::text || status::text || coalesce(fit::text, '-')
                                  || md5(notes) || updated_at::text, ',' order by id)) from j), 'none'),
       'profile_revision=' || coalesce((select revision::text from profiles where user_id = (select id from me)), 'none');
SQL
}

residue() {
  sql <<SQL
select 'users=' || (select count(*) from users where email like '$E2E_LIKE'),
       'jobs=' || (select count(*) from jobs j join users u on u.id = j.user_id where u.email like '$E2E_LIKE'),
       'events=' || (select count(*) from job_events e join users u on u.id = e.user_id where u.email like '$E2E_LIKE'),
       'profiles=' || (select count(*) from profiles p join users u on u.id = p.user_id where u.email like '$E2E_LIKE'),
       'invites=' || (select count(*) from invites where email like '$E2E_LIKE'),
       'login_attempts=' || (select count(*) from login_attempts where email like '$E2E_LIKE');
SQL
}

clean_e2e() { # jobs, events, files, sessions and profile rows cascade with the user
  sql <<SQL
begin;
delete from invites where email like '$E2E_LIKE'
  or created_by in (select id from users where email like '$E2E_LIKE');
delete from login_attempts where email like '$E2E_LIKE';
delete from users where email like '$E2E_LIKE';
commit;
SQL
}

stuck_containers() {
  local ids
  ids="$(docker ps -a --filter 'name=jobapp-test-pg-' --filter status=created -q 2>/dev/null || true)"
  if [[ -n "$ids" ]]; then
    # shellcheck disable=SC2086 # one id per word
    docker rm -v $ids >/dev/null
    info "removed $(wc -w <<<"$ids" | tr -d ' ') jobapp-test-pg-* container(s) stuck in Created"
  else
    info "no jobapp-test-pg-* container stuck in Created"
  fi
}

CLEAN="users=0|jobs=0|events=0|profiles=0|invites=0|login_attempts=0"
mkdir -p "$EVIDENCE"
exec > >(masked | tee "$LOG") 2>&1
printf 'tests/e2e_jobs_jetson.sh %s, %s\n' "$JOURNEY" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

step "before"
stuck_containers
URL="$(./deploy.sh url 2>/dev/null)" || die "no public URL (./deploy.sh url)"
info "public URL: $URL"
left="$(residue)"
if [[ "$left" != "$CLEAN" ]]; then
  [[ "${E2E_CLEAN_FIRST:-0}" == "1" ]] || die "e2e rows left by an earlier run ($left); rerun with E2E_CLEAN_FIRST=1"
  clean_e2e >/dev/null
  info "removed e2e rows of an earlier run: $left"
fi
snapshot | tr '|' '\n' >"$WORK/before"
grep -q '^jobs=[1-9]' "$WORK/before" || die "no jobs in the owner's account on $HOST"
sed 's/^/   /' "$WORK/before"
rm -f "$EVIDENCE"/*.png

step "Playwright: ${SPECS[*]} through $URL (synthetic data)"
status=0
env E2E_CADDY_URL="$URL" E2E_ADMIN_VIA_DEPLOY=1 E2E_SYNTHETIC=1 E2E_EVIDENCE="$EVIDENCE_NAME" "${RUN_ENV[@]}" \
  pnpm --dir web exec playwright test "${SPECS[@]}" --workers 1 || status=$?

step "after: e2e rows removed, Harald's jobs unchanged"
info "e2e rows before the cleanup: $(residue)"
clean_e2e >/dev/null
left="$(residue)"
info "e2e rows after the cleanup: $left"
[[ "$left" == "$CLEAN" ]] || status=1
snapshot | tr '|' '\n' >"$WORK/after"
if diff -u "$WORK/before" "$WORK/after"; then
  info "Harald's jobs: identical before and after ($(wc -l <"$WORK/after" | tr -d ' ') fields)"
else
  printf '   FAIL: Harald'"'"'s jobs changed\n'
  status=1
fi
failures="$(sql <<SQL
select count(*) from login_attempts where email = '$HARALD' and not success and at > now() - interval '15 minutes';
SQL
)"
info "failed sign ins of the owner's account in the 15 minute window: $failures"
[[ "$failures" == "0" ]] || status=1
stuck_containers
info "screenshots: $(find "$EVIDENCE" -name '*.png' | wc -l | tr -d ' ')"
if ((status == 0)); then info "PASS"; else info "FAIL"; fi
exit "$status"
