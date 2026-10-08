#!/usr/bin/env bash
# Reboot recovery check (platform task 9, success criterion 4 of SPEC-platform.md). Rebooting the
# Jetson needs sudo, so a human runs this on the Mac:
#
#   tests/reboot_check.sh 2>&1 | tee tasks/evidence/task9/reboot.log
#       reboots the Jetson (ssh -t asks for the sudo password once), then waits and checks
#   tests/reboot_check.sh --no-reboot
#       no reboot and no sudo: the same checks on the current boot (dry run, or after a reboot or a
#       power cut that already happened)
#
# Steps: record the boot id and the tunnel URL; `sudo systemctl reboot`; wait for ssh to answer
# with a new boot id (up to REBOOT_SSH_TIMEOUT_S, 300s); wait until api, caddy, db, redis and
# cloudflared are running and healthy (up to 300s more), noting the Jetson's uptime when they are;
# run ./deploy.sh smoke; print the new URL, the boot time, the uptime when the stack was healthy
# and when the smoke passed. Pass: the stack healthy within REBOOT_LIMIT_S (120s) of boot and the
# smoke green. Exit 0 on pass, 1 otherwise.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
HOST="${DEPLOY_HOST:-jetson}"
RDIR="${DEPLOY_DIR:-jobapp}"
RDIR="${RDIR#\~/}"
LIMIT="${REBOOT_LIMIT_S:-120}"
SSH_WAIT="${REBOOT_SSH_TIMEOUT_S:-300}"
SERVICES=(api caddy db redis cloudflared)
REBOOT=1
case "${1:-}" in
  "") ;;
  --no-reboot) REBOOT=0 ;;
  -h | --help)
    sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *)
    echo "usage: tests/reboot_check.sh [--no-reboot]" >&2
    exit 2
    ;;
esac

r() { ssh -o BatchMode=yes -o ConnectTimeout=5 "$HOST" "$@"; }
say() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }
die() {
  say "FAIL: $*" >&2
  exit 1
}
boot_id() { r cat /proc/sys/kernel/random/boot_id 2>/dev/null; }
uptime_s() { r "awk '{print int(\$1)}' /proc/uptime"; }

old_id="$(boot_id)" || die "cannot reach $HOST over ssh"
old_url="$(./deploy.sh url 2>/dev/null || echo none)"
say "$HOST boot id $old_id, up since $(r 'uptime -s'), tunnel $old_url"
say "docker.service at boot: $(r 'systemctl is-enabled docker'), containerd: $(r 'systemctl is-enabled containerd')"

if ((REBOOT)); then
  say "rebooting $HOST: type the sudo password of $(r whoami) if asked"
  ssh -t "$HOST" 'sudo systemctl reboot' || true # the connection drops as the Jetson goes down
  t0=$SECONDS
  say "waiting up to ${SSH_WAIT}s for ssh with a new boot id"
  while :; do
    id="$(boot_id || true)"
    [[ -n "$id" && "$id" != "$old_id" ]] && break
    ((SECONDS - t0 < SSH_WAIT)) || die "$HOST did not come back with a new boot id within ${SSH_WAIT}s"
    sleep 3
  done
  say "ssh back $((SECONDS - t0))s after the reboot command, new boot id $id"
else
  say "--no-reboot: checking the current boot, timing is not judged"
fi
booted="$(r 'uptime -s')"
say "booted at $booted, uptime now $(uptime_s)s"

say "waiting for ${SERVICES[*]} to be running and healthy"
t0=$SECONDS
last=""
while :; do
  # shellcheck disable=SC2016 # expanded on the Jetson
  states="$(r "cd $RDIR && for s in ${SERVICES[*]}; do
      id=\$(docker compose -f compose.yml --profile tunnel ps -a -q \$s 2>/dev/null)
      if [ -z \"\$id\" ]; then echo \"\$s: no container\"; else
        echo \"\$s: \$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' \$id)\"; fi
    done" 2>/dev/null || echo "docker not answering yet")"
  if [[ "$states" != "$last" ]]; then
    say "uptime $(uptime_s 2>/dev/null || echo ?)s: $(tr '\n' ',' <<<"$states" | sed 's/,$//; s/,/, /g')"
    last="$states"
  fi
  if [[ "$(grep -c ': running healthy$' <<<"$states")" == "${#SERVICES[@]}" ]]; then
    healthy_at="$(uptime_s)"
    break
  fi
  ((SECONDS - t0 < 300)) || die "the stack was not healthy within 300s: $states"
  sleep 2
done
say "all ${#SERVICES[@]} services healthy at uptime ${healthy_at}s"
r "docker inspect -f '{{.Name}} restart={{.HostConfig.RestartPolicy.Name}} started={{.State.StartedAt}} restarts={{.RestartCount}}' \$(cd $RDIR && docker compose -f compose.yml --profile tunnel ps -q)" |
  sed 's/^/          /'

say "./deploy.sh smoke"
smoke_ok=1
./deploy.sh smoke || smoke_ok=0
smoke_at="$(uptime_s)"
new_url="$(./deploy.sh url 2>/dev/null || echo none)"

printf '\n== reboot check on %s\n' "$HOST"
printf '   booted at          %s\n' "$booted"
printf '   stack healthy at   %ss after boot\n' "$healthy_at"
printf '   smoke %s at    %ss after boot\n' "$( ((smoke_ok)) && echo passed || echo FAILED)" "$smoke_at"
printf '   tunnel URL before  %s\n' "$old_url"
printf '   tunnel URL now     %s\n' "$new_url"
printf '   memory             %s\n' "$(r "free -m | awk '/^Mem:/ {print \$3\"M used of \"\$2\"M, \"\$7\"M available\"}'")"
if ((REBOOT == 0)); then
  ((smoke_ok)) || die "smoke failed"
  say "no reboot done: services healthy and smoke green on the current boot"
  exit 0
fi
((smoke_ok)) || die "smoke failed after the reboot"
[[ "$new_url" != "$old_url" ]] || say "note: the tunnel URL did not change, unexpected after a reboot"
((healthy_at <= LIMIT)) || die "the stack was healthy ${healthy_at}s after boot, over the ${LIMIT}s limit"
say "PASS: the stack came back by itself, healthy ${healthy_at}s after boot (limit ${LIMIT}s), smoke green"
