#!/usr/bin/env bash
# The jobs J5 real sweep (web/e2e/jobs-sweep.spec.ts, E2E_REAL_ONLY=1) on the Jetson through the
# public URL: an e2e account sweeps two real boards (Mistral AI on Ashby, Anthropic on Greenhouse)
# from the Jetson's own address, framed by the proof that Harald's jobs and sweeps are untouched.
# No sweep ever runs for Harald's account.
#
#   tests/e2e_jobs_sweep_jetson.sh   against $(./deploy.sh url), evidence in tasks/evidence/jobs-j5-jetson/
#
# 1. Removes jobapp-test-pg-* containers stuck in "Created" on the Mac; refuses to start while
#    e2e rows are left on the Jetson unless E2E_CLEAN_FIRST=1.
# 2. Snapshots Harald's jobs: rows per status, sum of fit, fits set by hand, max updated_at,
#    events, files, sweeps, and an md5 over every row's id, status, fit, notes and updated_at.
# 3. Runs the spec with e2e-admin@example.org (created with ./deploy.sh admin create), sampling
#    the worker's memory on the Jetson every few seconds.
# 4. Whatever happened, deletes the e2e accounts (their jobs, events, files and profile go with
#    them) and their sign in attempts, then proves the snapshot identical, no e2e row left, and no
#    failed sign in left for Harald's email in the 15 minute window.
#
# Settings as deploy.sh: DEPLOY_HOST (default jetson), DEPLOY_DIR (default ~/jobapp).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
HOST="${DEPLOY_HOST:-jetson}"
RDIR="${DEPLOY_DIR:-jobapp}"
RDIR="${RDIR#\~/}"
HARALD="hthautin@gmail.com"
ADMIN="e2e-admin@example.org"
E2E_LIKE="e2e-%@example.org"
EVIDENCE="$ROOT/tasks/evidence/jobs-j5-jetson"
LOG="$EVIDENCE/run.log"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

step() { printf '\n== %s\n' "$*"; }
info() { printf '   %s\n' "$*"; }
die() {
  printf 'e2e_jobs_sweep_jetson.sh: %s\n' "$*" >&2
  exit 1
}

sql() { # the SQL on stdin in the Jetson's database, unaligned, fields separated by |
  # shellcheck disable=SC2016 # $POSTGRES_* expand in the db container
  ssh -o BatchMode=yes "$HOST" "cd $(printf '%q' "$RDIR") && docker compose -f compose.yml exec -T db sh -c" \
    "'psql -X -q -v ON_ERROR_STOP=1 -tA -F \"|\" -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"'"
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
       'sweeps=' || (select count(*) from sweeps where user_id = (select id from me)),
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
       'sweeps=' || (select count(*) from sweeps s join users u on u.id = s.user_id where u.email like '$E2E_LIKE'),
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
    docker rm $ids >/dev/null
    info "removed $(wc -w <<<"$ids" | tr -d ' ') jobapp-test-pg-* container(s) stuck in Created"
  else
    info "no jobapp-test-pg-* container stuck in Created"
  fi
}

CLEAN="users=0|jobs=0|events=0|profiles=0|sweeps=0|invites=0|login_attempts=0"
mkdir -p "$EVIDENCE"
exec > >(tee "$LOG") 2>&1
printf 'tests/e2e_jobs_sweep_jetson.sh, %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

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
grep -q '^jobs=[1-9]' "$WORK/before" || die "no jobs for $HARALD on $HOST"
sed 's/^/   /' "$WORK/before"
rm -f "$EVIDENCE"/*.png

step "Playwright: web/e2e/jobs-sweep.spec.ts through $URL (E2E_REAL_ONLY: two real boards)"
rm -f "$EVIDENCE"/*.json
( # the worker's memory every few seconds while the journey runs
  while [[ ! -e "$WORK/stop" ]]; do
    ssh -o BatchMode=yes "$HOST" "docker stats --no-stream --format '{{.MemUsage}}' \$(cd $(printf '%q' "$RDIR") && docker compose -f compose.yml ps -q worker)" 2>/dev/null |
      cut -d' ' -f1 >>"$WORK/memory" || true
    sleep 2
  done
) &
sampler=$!
status=0
E2E_CADDY_URL="$URL" E2E_ADMIN_VIA_DEPLOY=1 E2E_ADMIN_EMAIL="$ADMIN" E2E_REAL_ONLY=1 \
  E2E_EVIDENCE=jobs-j5-jetson pnpm --dir web exec playwright test jobs-sweep.spec.ts || status=$?
touch "$WORK/stop"
wait "$sampler" || true
info "worker memory samples: $(wc -l <"$WORK/memory" | tr -d ' '), highest: $(sort -h "$WORK/memory" | tail -n 1)"
[[ -f "$EVIDENCE/real-sweep.json" ]] && sed 's/^/   /' "$EVIDENCE/real-sweep.json"

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
info "failed sign ins of $HARALD in the 15 minute window: $failures"
[[ "$failures" == "0" ]] || status=1
stuck_containers
info "screenshots: $(find "$EVIDENCE" -name '*.png' | wc -l | tr -d ' ')"
if ((status == 0)); then info "PASS"; else info "FAIL"; fi
exit "$status"
