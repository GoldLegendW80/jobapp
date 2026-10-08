#!/usr/bin/env bash
# Crash recovery without a reboot (platform task 9), run from the Mac, no sudo. The partial proof of
# success criterion 4 until a human runs tests/reboot_check.sh.
#
#   tests/crash_check.sh
#
# 1. Boot persistence: docker.service and containerd.service are enabled at boot and every service
#    of the stack has restart: unless-stopped, so the daemon starts them again after a reboot.
# 2. Crash of each service: the main process of its container is killed with SIGKILL from the
#    host PID namespace (a throwaway container with --pid=host, root through the docker group, no
#    sudo), which is what the kernel's OOM killer or a segfault looks like to the daemon. The test
#    passes when the daemon restarts it by itself (RestartCount + 1) and it is healthy again.
#    Not used, and why: `docker kill` and `docker compose kill` mark the container as stopped by
#    hand, so unless-stopped leaves it down (checked below); a signal sent from inside the
#    container to its PID 1 is ignored unless the process handles it (redis even survives its own
#    SIGSEGV handler that way), so it emulates nothing.
# 3. Control: `docker kill` on redis leaves it exited (manual stop), then it is started again.
# 4. The app answers on loopback and ./deploy.sh smoke passes, with the new tunnel URL (cloudflared
#    was restarted, so the URL changed).
# Leaves every service running. Takes about two minutes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
HOST="${DEPLOY_HOST:-jetson}"
RDIR="${DEPLOY_DIR:-jobapp}"
RDIR="${RDIR#\~/}"
SERVICES=(redis db api caddy cloudflared)
# Any image already on the Jetson with a busybox `kill`: the stack's own redis image, no pull.
KILLER_IMAGE="redis:7.4.11-alpine"

CTL="$(mktemp -d /tmp/jobapp-crash.XXXXXX)"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=5 -o ControlMaster=auto -o "ControlPath=$CTL/%C"
  -o ControlPersist=120 "$HOST")
r() { "${SSH[@]}" "$@"; }
cleanup() {
  # Never leave a service down, whatever failed.
  r "cd $RDIR && docker compose -f compose.yml --profile tunnel start >/dev/null 2>&1" || true
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
cid() { r "cd $RDIR && docker compose -f compose.yml --profile tunnel ps -a -q $1"; }
state() { r "docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}} {{.RestartCount}} {{.State.Pid}}' $1"; }

# wait_healthy <id> <min restart count> <timeout s>: seconds until running, healthy, restarted
wait_healthy() {
  local id="$1" min="$2" limit="$3" t0=$SECONDS st health rc pid
  while :; do
    read -r st health rc pid <<<"$(state "$id")"
    if [[ "$st" == running && "$health" == healthy ]] && ((rc >= min)); then
      printf '%s' "$((SECONDS - t0))"
      return 0
    fi
    ((SECONDS - t0 < limit)) || return 1
    sleep 1
  done
}

step "boot persistence"
for unit in docker containerd; do
  [[ "$(r "systemctl is-enabled $unit")" == enabled ]] || fail "$unit.service is not enabled at boot"
done
ok "docker.service and containerd.service enabled at boot"
for svc in "${SERVICES[@]}"; do
  id="$(cid "$svc")"
  [[ -n "$id" ]] || fail "$svc has no container"
  pol="$(r "docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' $id")"
  [[ "$pol" == unless-stopped ]] || fail "$svc restart policy is '$pol'"
done
ok "restart: unless-stopped on ${SERVICES[*]}"
r "docker image inspect $KILLER_IMAGE >/dev/null" || fail "$KILLER_IMAGE is not on $HOST"

step "crash each service: SIGKILL of its main process from the host PID namespace"
for svc in "${SERVICES[@]}"; do
  id="$(cid "$svc")"
  read -r st health rc pid <<<"$(state "$id")"
  [[ "$st $health" == "running healthy" ]] || fail "$svc is '$st $health' before the crash"
  r "docker run --rm --pid=host --network=none $KILLER_IMAGE kill -9 $pid"
  if ! secs="$(wait_healthy "$id" $((rc + 1)) 120)"; then
    fail "$svc not restarted and healthy within 120s: $(state "$id")"
  fi
  read -r st health rc2 _ <<<"$(state "$id")"
  ok "$svc: pid $pid killed, restarted by the daemon (restart count $rc to $rc2), healthy again after ${secs}s"
done

step "control: docker kill is a manual stop, unless-stopped leaves it down"
id="$(cid redis)"
read -r _ _ rc _ <<<"$(state "$id")"
r "docker kill $id >/dev/null"
sleep 8
read -r st _ rc2 _ <<<"$(state "$id")"
[[ "$st" == exited && "$rc2" == "$rc" ]] || fail "after docker kill redis is '$st', restart count $rc to $rc2"
ok "after docker kill, redis stays exited for 8s, restart count unchanged ($rc): that is why the crash above uses SIGKILL from outside"
r "cd $RDIR && docker compose -f compose.yml start redis >/dev/null 2>&1"
secs="$(wait_healthy "$id" 0 60)" || fail "redis did not come back after start"
ok "redis started again, healthy after ${secs}s"

step "the app after the crashes"
port="$(r "sed -n 's/^HTTP_PORT=//p' $RDIR/.env | tail -n 1")"
body="$(r "curl -s --max-time 5 http://127.0.0.1:${port:-80}/api/health")"
[[ "$body" == '{"status":"ok"}' ]] || fail "/api/health answered '$body'"
ok "/api/health through caddy: $body"
./deploy.sh smoke >"$CTL/smoke.log" 2>&1 || {
  tail -n 30 "$CTL/smoke.log" >&2
  fail "./deploy.sh smoke failed after the crashes"
}
ok "./deploy.sh smoke passes through $(./deploy.sh url 2>/dev/null) (new URL: cloudflared restarted)"

printf '\nAll %d checks passed.\n' "$passed"
