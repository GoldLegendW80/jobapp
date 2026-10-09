#!/usr/bin/env bash
# Reduced smoke test of the deployed stack on the Jetson, run from the Mac over ssh (platform
# tasks 4, 6 and 8). Rerunnable. By default it stops and starts db once (data kept) and leaves
# every service up; JETSON_SMOKE_FULL=0 skips that outage and changes nothing on the Jetson, which
# is what ./deploy.sh smoke runs (./deploy.sh smoke --full runs the outage too).
#
#   tests/jetson_smoke.sh                  against DEPLOY_HOST (default jetson), DEPLOY_DIR ~/jobapp
#   DEPLOY_HOST=other tests/jetson_smoke.sh
#   JETSON_SMOKE_FULL=0 tests/jetson_smoke.sh
#
# The full local checks live in tests/compose_smoke.sh; this keeps the ones that matter on the
# target: health and SPA through caddy on loopback, nothing on the LAN, production settings,
# migrations, redis persistence, database outage and recovery, memory limits.
set -euo pipefail

HOST="${DEPLOY_HOST:-jetson}"
RDIR="${DEPLOY_DIR:-jobapp}"
RDIR="${RDIR#\~/}"
FULL="${JETSON_SMOKE_FULL:-1}"

CTL="$(mktemp -d /tmp/jobapp-smoke.XXXXXX)"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=5 -o ControlMaster=auto -o "ControlPath=$CTL/%C"
  -o ControlPersist=60 "$HOST")
STOPPED_DB=0
cleanup() {
  # Never leave db stopped by this script, whatever failed. A service that was already down when
  # the script started is left as it was: the smoke reports it, it does not repair it.
  if ((STOPPED_DB == 1)); then
    "${SSH[@]}" "cd $RDIR && docker compose -f compose.yml start db >/dev/null 2>&1" || true
  fi
  ssh -o "ControlPath=$CTL/%C" -O exit "$HOST" >/dev/null 2>&1 || true
  rm -rf "$CTL"
}
trap cleanup EXIT

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

r() { "${SSH[@]}" "$@"; }
rc() { r "cd $RDIR && docker compose -f compose.yml --profile tunnel $*"; }
# Remote env values, never printed.
renv() { r "sed -n 's/^$1=//p' $RDIR/.env | tail -n 1"; }

r true || fail "cannot reach $HOST"
PORT="$(renv HTTP_PORT)"
PORT="${PORT:-80}"
BASE="http://127.0.0.1:$PORT"
# rcurl <path> [curl args]: GET through caddy on the Jetson; prints the status, then the headers,
# then the body, separated by a line ---8<---.
rcurl() {
  local path="$1"
  shift
  r "d=\$(mktemp -d); code=\$(curl -s --path-as-is --max-time 10 -D \$d/h -o \$d/b -w '%{http_code}' $* '$BASE$path'); echo \$code; cat \$d/h; echo ---8\<---; cat \$d/b; rm -rf \$d"
}
status_of() { head -n 1 <<<"$1"; }
headers_of() { sed -n '2,/^---8<---$/p' <<<"$1" | tr -d '\r'; }
body_of() { sed -n '/^---8<---$/,$p' <<<"$1" | tail -n +2; }
hdr() { headers_of "$1" | grep -i "^$2:" | head -n 1 | cut -d' ' -f2-; }

step "services healthy, only caddy published, on loopback"
for svc in api caddy db redis cloudflared; do
  id="$(rc ps -q "$svc")"
  [[ -n "$id" ]] || fail "$svc is not running"
  # A service that has just (re)started is `starting` until its first healthcheck: give it a minute.
  deadline=$((SECONDS + 60))
  while health="$(r "docker inspect -f '{{.State.Health.Status}}' $id")" && [[ "$health" == "starting" ]] &&
    ((SECONDS < deadline)); do
    sleep 3
  done
  [[ "$health" == "healthy" ]] || fail "$svc is $health"
  ports="$(r "docker port $id")"
  if [[ "$svc" == "caddy" ]]; then
    [[ "$ports" == "80/tcp -> 127.0.0.1:$PORT" ]] || fail "caddy publishes: $ports"
  else
    [[ -z "$ports" ]] || fail "$svc publishes $ports"
  fi
done
ok "api, caddy, db, redis, cloudflared healthy; caddy alone on 127.0.0.1:$PORT (its tunnel listener :8080 unpublished)"
ip="$(ssh -G "$HOST" 2>/dev/null | awk '/^hostname / {print $2}')"
if curl -s --max-time 3 -o /dev/null "http://$ip:$PORT/" 2>/dev/null; then
  fail "the stack answers on the LAN at $ip:$PORT"
fi
ok "nothing answers on the LAN at $ip:$PORT"

step "production settings"
[[ "$(r "stat -c %a $RDIR/.env")" == "600" ]] || fail ".env mode"
[[ "$(renv APP_ENV)" == "production" ]] || fail "APP_ENV is not production"
[[ "$(rc exec -T api printenv APP_ENV | tr -d '\r')" == "production" ]] || fail "api APP_ENV"
secret="$(renv SECRET_KEY)"
{ ((${#secret} >= 32)) && [[ "$secret" != "change-me" ]]; } || fail "weak SECRET_KEY"
pw="$(renv POSTGRES_PASSWORD)"
{ ((${#pw} >= 32)) && [[ "$pw" != "change-me" ]]; } || fail "weak POSTGRES_PASSWORD"
unset secret pw
ok ".env mode 600, APP_ENV production in the api, strong SECRET_KEY and POSTGRES_PASSWORD"
if rc exec -T api python -c "'import pytest'" >/dev/null 2>&1; then
  fail "runtime image ships dev dependencies"
fi
[[ "$(rc exec -T api id -u | tr -d '\r')" == "10001" ]] || fail "api does not run as uid 10001"
ok "runtime image: no dev dependencies, runs as uid 10001"
[[ "$(r "docker inspect -f '{{.Architecture}}' jobapp-api:latest jobapp-caddy:latest" | sort -u)" == "arm64" ]] ||
  fail "images are not arm64"
ok "jobapp-api and jobapp-caddy images are arm64"

step "health and the React shell through caddy"
res="$(rcurl /api/health)"
[[ "$(status_of "$res")" == "200" && "$(body_of "$res")" == '{"status":"ok"}' ]] || fail "/api/health: $res"
ok "/api/health: $(body_of "$res")"
for route in / /some/route "/some/deep/route?x=1" /jobs/42/ /caf%C3%A9; do
  res="$(rcurl "$route")"
  { [[ "$(status_of "$res")" == "200" ]] && grep -q '<div id="root">' <<<"$(body_of "$res")"; } ||
    fail "$route is not the shell"
done
ok "/, deep routes, query string, trailing slash, unicode: index.html"
res="$(rcurl /)"
[[ "$(hdr "$res" Cache-Control)" == "no-cache" && "$(hdr "$res" X-Frame-Options)" == "DENY" ]] ||
  fail "index headers"
[[ -z "$(hdr "$res" Server)" ]] || fail "Server header leaks"
asset="$(body_of "$res" | grep -o '/assets/[^"]*\.js' | head -n 1)"
res="$(rcurl "$asset")"
[[ "$(status_of "$res")" == "200" && "$(hdr "$res" Cache-Control)" == "public, max-age=31536000, immutable" ]] ||
  fail "$asset"
ok "index no-cache, DENY, no Server header; $asset immutable"
res="$(rcurl "$asset" -H "'Accept-Encoding: zstd'")"
[[ "$(hdr "$res" Content-Encoding)" == "zstd" ]] || fail "no zstd"
res="$(rcurl /assets/nope.js)"
{ [[ "$(status_of "$res")" == "404" ]] && ! grep -q '<div id="root">' <<<"$(body_of "$res")"; } || fail "missing asset"
res="$(rcurl /api/nope)"
[[ "$(status_of "$res")" == "404" && "$(body_of "$res")" == '{"detail":"Not Found"}' ]] || fail "/api/nope"
ok "zstd, missing asset is a 404, /api/nope is the api's JSON 404"
for attack in /../etc/passwd /%2e%2e/%2e%2e/etc/passwd /assets/..%2f..%2f..%2fetc%2fpasswd; do
  res="$(rcurl "$attack")"
  grep -q 'root:' <<<"$(body_of "$res")" && fail "$attack leaked /etc/passwd"
done
ok "path traversal never leaves /srv"

step "loopback trusts no proxy: spoofed client headers never reach the api"
rid="jetson-smoke-$RANDOM$RANDOM"
rcurl /api/health -H "'X-Request-ID: $rid'" -H "'Cf-Connecting-Ip: 203.0.113.10'" -H "'X-Forwarded-For: 203.0.113.9'" >/dev/null
client=""
for i in 1 2 3; do
  client="$(rc logs --no-log-prefix --since 2m api | grep -F "\"request_id\": \"$rid\"" |
    sed -n 's/.*"client": "\([^"]*\)".*/\1/p' | tail -n 1 || true)"
  [[ -n "$client" ]] && break
  sleep "$i"
done
python3 -c 'import ipaddress, sys; sys.exit(0 if ipaddress.ip_address(sys.argv[1]).is_private else 1)' "${client:-x}" 2>/dev/null ||
  fail "the api logged client '${client:-nothing}' for a spoofed request on loopback"
ok "the api logs the TCP peer $client, not the spoofed Cf-Connecting-Ip or X-Forwarded-For"

step "log hygiene: a failed sign in leaves no password and no email in the logs"
PGU="$(renv POSTGRES_USER)"
PGD="$(renv POSTGRES_DB)"
psql() { rc exec -T db psql -v ON_ERROR_STOP=1 -U "$PGU" -d "$PGD" -tAc "\"$1\""; }
marker="smoke$RANDOM$RANDOM"
semail="$marker@example.org"
spass="pass-$marker-secret"
res="$(rcurl /api/auth/login -X POST -H "'Content-Type: application/json'" -H "'X-Requested-With: jobapp'" \
  --data "'{\"email\": \"$semail\", \"password\": \"$spass\"}'")"
[[ "$(status_of "$res")" == "401" ]] || fail "a wrong sign in answered $(status_of "$res"): $(body_of "$res")"
recorded="$(psql "select count(*) from login_attempts where email = '$semail' and not success" | tr -d '\r')"
# The attempt is the smoke's own: remove it before judging, so a failure leaves nothing behind.
psql "delete from login_attempts where email = '$semail'" >/dev/null
[[ "$recorded" == "1" ]] || fail "the attempt was recorded $recorded times in login_attempts"
sleep 1
if rc logs --no-log-prefix --since 2m api caddy | grep -qF -e "$spass" -e "$marker"; then
  fail "the api or caddy logs hold the password or the email of a sign in"
fi
ok "401 recorded once in login_attempts (then deleted), neither password nor email in the api and caddy logs"

step "auth: protected routes answer 401 without a session"
for route in /api/auth/me /api/admin/users /api/admin/invites /api/profile /api/profile/revisions; do
  for res in "$(rcurl "$route")" "$(rcurl "$route" -H "'Cookie: jobapp_session=forged'")"; do
    [[ "$(status_of "$res")" == "401" ]] || fail "$route answered $(status_of "$res") without a session"
    grep -q '"detail"' <<<"$(body_of "$res")" || fail "$route 401 body: $(body_of "$res")"
  done
done
res="$(rcurl /api/health)"
[[ "$(status_of "$res")" == "200" ]] || fail "/api/health is not public any more"
ok "/api/auth/me, /api/admin/users, /api/admin/invites, /api/profile, /api/profile/revisions: 401 with no cookie and with a forged one; /api/health stays public"

step "migrations rerun safely"
for _ in 1 2; do rc exec -T api alembic upgrade head >/dev/null 2>&1 || fail "upgrade head"; done
current="$(rc exec -T api alembic current 2>/dev/null | tr -d '\r')"
# Any revision, as long as it is the head: later modules add migrations.
[[ "$current" =~ ^[0-9a-z_]+\ \(head\)$ ]] || fail "alembic current: $current"
ok "upgrade head twice, current is $current"

step "redis"
[[ "$(rc exec -T redis redis-cli ping | tr -d '\r')" == "PONG" ]] || fail "no PONG"
[[ "$(rc exec -T redis redis-cli config get appendonly | tail -n 1 | tr -d '\r')" == "yes" ]] || fail "AOF off"
ok "PONG, appendonly yes"

if ((FULL == 1)); then
  step "database stopped: 503 through caddy, then 200 without restarting api; data kept"
  psql "create table if not exists smoke_jetson (note text)" >/dev/null
  psql "delete from smoke_jetson; insert into smoke_jetson values ('kept ✓')" >/dev/null
  api_started="$(r "docker inspect -f '{{.State.StartedAt}}' $(rc ps -q api)")"
  wait_health() { # wait_health <code> <seconds>
    local deadline=$((SECONDS + $2)) got=""
    while ((SECONDS < deadline)); do
      got="$(status_of "$(rcurl /api/health)")"
      [[ "$got" == "$1" ]] && return 0
      sleep 1
    done
    fail "health never answered $1 within $2s (last $got)"
  }
  STOPPED_DB=1
  rc stop db >/dev/null 2>&1
  wait_health 503 30
  grep -q '"database unavailable"' <<<"$(body_of "$(rcurl /api/health)")" || fail "503 body"
  ok "db stopped: 503 database unavailable"
  rc start db >/dev/null 2>&1
  wait_health 200 90
  [[ "$(r "docker inspect -f '{{.State.StartedAt}}' $(rc ps -q api)")" == "$api_started" ]] || fail "api restarted"
  [[ "$(psql "select note from smoke_jetson" | tr -d '\r')" == "kept ✓" ]] || fail "row lost"
  psql "drop table smoke_jetson" >/dev/null
  STOPPED_DB=0
  ok "db started: 200 again, api never restarted, row kept"
else
  printf '\n== database outage skipped (JETSON_SMOKE_FULL=0)\n'
fi

step "memory under the limits"
stats="$(r "docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' \$(cd $RDIR && docker compose -f compose.yml --profile tunnel ps -q)")"
printf '%s\n' "$stats" | sed 's/^/   /'
for pair in "api 512MiB" "caddy 128MiB" "db 768MiB" "redis 256MiB" "cloudflared 128MiB"; do
  name="${pair% *}"
  limit="${pair#* }"
  grep -Eq "^jobapp-$name-[0-9]+ .* / $limit\$" <<<"$stats" || fail "$name limit is not $limit"
done
ok "api 512MiB, caddy 128MiB, db 768MiB, redis 256MiB, cloudflared 128MiB"

printf '\nAll %d checks passed on %s.\n' "$passed" "$HOST"
