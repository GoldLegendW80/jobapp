#!/usr/bin/env bash
# Tests of ops/browser-egress.sh (browser B6, issue #230) that need no Jetson.
#
#   tests/browser_egress_test.sh
#
# Covers: the rules printed for review (every private range, DNS to the given resolvers only,
# IPv6 dropped whole, bad bridge and resolver names refused), the resolvers read as Docker reads
# them (systemd-resolved's upstream file behind 127.0.0.53, IPv6 and loopback servers left out,
# junk refused, none found means no DNS), `deploy` against a fake docker (the hop image pinned by
# digest and pulled when missing; not installed, apply or check failing, compose create or config
# failing, the hop image not pullable: the browser removed and exit 1; both passing: exit 0,
# nothing removed), `host-check`, root refused for every root command, the nightly backup naming no volume
# (pg_dump only, so `browserprofiles` is never in it), and, with Docker, the real iptables of a
# throwaway container's own network namespace (never the host's): apply twice gives one jump per
# chain and the same rules, a user rule of DOCKER-USER kept, a new bridge replaces the old jump,
# check passes then fails on a removed rule, on a rule put above the jump and on a bridge that
# moved, --boot applies the recorded bridge with no Docker, uninstall leaves nothing. The mail
# kind is installed alongside the browser in that namespace: both checks pass, reapply is
# idempotent, mail drift is detected, and either uninstall preserves the other's rules.
# Set EGRESS_TEST_DOCKER=0 to skip the Docker part.
# Behind a TLS inspecting proxy, JOBAPP_EXTRA_CA (README) reaches the package install of that part
# alone, as the BuildKit secret of a throwaway image build; a line planted in it is then looked for
# in the files, variables, labels and history of that image, and must be in none.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SCRIPT=ops/browser-egress.sh
WORK="$(mktemp -d)"
IMAGE="" # the throwaway image of the packages, built behind JOBAPP_EXTRA_CA only
CREATED="" # a container of it, created to read its files and never started
cleanup() {
  if [[ -n "$CREATED" ]]; then docker rm -f "$CREATED" >/dev/null 2>&1 || true; fi
  if [[ -n "$IMAGE" ]]; then docker rmi -f "$IMAGE" >/dev/null 2>&1 || true; fi
  rm -rf "$WORK"
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
run() { # run <expected exit> <cmd...>: output in $WORK/out
  local want="$1" got=0
  shift
  "$@" >"$WORK/out" 2>&1 || got=$?
  [[ "$got" == "$want" ]] || fail "exit $got, expected $want: $* -> $(head -c 600 "$WORK/out")"
}
out_has() { grep -qF -- "$1" "$WORK/out" || fail "output lacks '$1': $(head -c 600 "$WORK/out")"; }
out_lacks() { if grep -qF -- "$1" "$WORK/out"; then fail "output has '$1'"; fi; }

step "rules: what is printed for review"
run 0 bash "$SCRIPT" rules br-0123456789ab 192.168.1.1 9.9.9.9
for net in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 \
  192.0.0.0/24 192.0.2.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24 203.0.113.0/24 \
  224.0.0.0/4 240.0.0.0/4; do
  out_has "-A JOBAPP-EGRESS -d $net -j DROP"
done
for r in 192.168.1.1 9.9.9.9; do
  out_has "-A JOBAPP-EGRESS -d $r/32 -p udp -m udp --dport 53 -j RETURN"
  out_has "-A JOBAPP-EGRESS -d $r/32 -p tcp -m tcp --dport 53 -j RETURN"
done
[[ "$(grep -c -- '--dport 53' "$WORK/out")" == 4 ]] || fail "DNS passes to more than the two resolvers"
dns="$(grep -n -- '--dport 53' "$WORK/out" | tail -n 1 | cut -d: -f1)"
udp="$(grep -n -- '-A JOBAPP-EGRESS -p udp -j DROP' "$WORK/out" | head -n 1 | cut -d: -f1)"
first_net="$(grep -n -- '-d 0.0.0.0/8 -j DROP' "$WORK/out" | head -n 1 | cut -d: -f1)"
((dns < udp && udp < first_net)) || fail "order is DNS $dns, UDP drop $udp, ranges $first_net"
[[ "$(grep -c -- '^-I DOCKER-USER 1 -i br-0123456789ab -j JOBAPP-EGRESS$' "$WORK/out")" == 2 ]] ||
  fail "one DOCKER-USER jump per family expected"
[[ "$(grep -c -- '^-I INPUT 1 -i br-0123456789ab -j JOBAPP-EGRESS-IN$' "$WORK/out")" == 2 ]] ||
  fail "one INPUT jump per family expected"
[[ "$(grep -c -- '^-A JOBAPP-EGRESS-IN -j DROP$' "$WORK/out")" == 2 ]] || fail "INPUT is not dropped whole in both families"
sed -n '/^# IPv6$/,$p' "$WORK/out" | grep -x -- '-A JOBAPP-EGRESS -j DROP' >/dev/null || fail "IPv6 is not dropped whole"
sed -n '/^# IPv6$/,$p' "$WORK/out" | grep -- '--dport 53' >/dev/null && fail "IPv6 lets DNS through"
grep -q -- '-j ACCEPT' "$WORK/out" && fail "a rule accepts: the chains only return or drop"
ok "every non public IPv4 range dropped after DNS to the two resolvers and the UDP drop; INPUT and IPv6 dropped whole; nothing accepted"
run 0 bash "$SCRIPT" rules br-x
out_lacks "--dport 53"
out_has "-A JOBAPP-EGRESS -p udp -j DROP"
ok "no resolver: no DNS passes (fail closed)"
for bad in "" "br-0123456789abcdef" "br x" 'br;id' "br/0" "../x"; do
  run 1 bash "$SCRIPT" rules "$bad" 192.168.1.1
  out_has "usage:"
done
for bad in 256.1.1.1 010.1.1.1 1.2.3 1.2.3.4.5 "1.2.3.4;id" ::1 fd00::1 " 1.2.3.4" 1.2.3.04x ""; do
  run 1 bash "$SCRIPT" rules br-x "$bad"
  out_has "bad resolver"
done
ok "bad bridge names (too long, spaces, shell, slash) and bad resolvers (out of range, IPv6, junk) refused"

step "mail has its own firewall chains"
JOBAPP_EGRESS_KIND=mail bash "$SCRIPT" rules br-0123456789ab 1.1.1.1 >"$WORK/mail-rules"
grep -q -- '-j JOBAPP-MAIL-EGRESS$' "$WORK/mail-rules" || fail "mail does not have its own egress chain"
grep -q -- '-j JOBAPP-MAIL-EGRESS-IN$' "$WORK/mail-rules" || fail "mail does not have its own host-input chain"
grep -q -- '-d 169.254.0.0/16 -j DROP' "$WORK/mail-rules" || fail "mail can reach metadata"
ok "mail and browser chains are distinct, with the same private-address refusals"

step "resolvers: as Docker reads them"
funcs="$WORK/funcs.sh"
sed '/^main "\$@"$/d' "$SCRIPT" >"$funcs"
resolv() { # resolv <resolv.conf text> [systemd file text]: the resolvers found
  printf '%b' "$1" >"$WORK/resolv"
  if [[ $# -ge 2 ]]; then printf '%b' "$2" >"$WORK/sd"; else rm -f "$WORK/sd"; fi
  EGRESS_RESOLV="$WORK/resolv" EGRESS_SYSTEMD_RESOLV="$WORK/sd" bash -c "source '$funcs'; resolvers" | tr '\n' ' '
}
check_resolv() { # check_resolv <expected> <args of resolv...>
  local want="$1" got
  shift
  got="$(resolv "$@")"
  [[ "$got" == "$want" ]] || fail "resolvers of $(printf '%q ' "$@"): '$got', expected '$want'"
}
check_resolv "192.168.1.1 " "nameserver 192.168.1.1\nsearch home\n"
check_resolv "192.168.1.1 " "nameserver 127.0.0.53\nsearch home\n" "nameserver 192.168.1.1\nnameserver 2a01:cb19::1\nsearch home\n"
check_resolv "192.168.1.1 1.1.1.1 " "nameserver 127.0.0.53\n" "# c\nnameserver 192.168.1.1\nnameserver 192.168.1.1\nnameserver 1.1.1.1\n"
check_resolv "10.0.0.2 " "nameserver 127.0.0.1\nnameserver 10.0.0.2\n" "nameserver 192.168.1.1\n"
check_resolv "" "nameserver 127.0.0.53\n"
check_resolv "" "nameserver 127.0.0.53\n" "nameserver 127.0.0.1\nnameserver ::1\n"
check_resolv "" "nameserver ::1\n" "nameserver fd00::1\n"
check_resolv "8.8.4.4 " "nameserver 999.1.1.1\nnameserver 1.2.3\nnameserver 8.8.4.4\n"
check_resolv "" ""
check_resolv "192.168.1.1 " "#nameserver 10.9.9.9\n nameserver 192.168.1.1\n"
ok "systemd-resolved's upstream behind 127.0.0.53, loopback and IPv6 servers out, duplicates once, junk refused, none found: none"

step "root commands refuse a user"
for cmd in apply check install uninstall; do
  run 1 bash "$SCRIPT" "$cmd"
  out_has "needs root: sudo bash ops/browser-egress.sh $cmd"
done
ok "apply, check, install and uninstall exit 1 for a user and name the sudo command"

step "deploy: the browser starts only when apply and check pass"
mkdir -p "$WORK/bin"
cat >"$WORK/bin/docker" <<'EOF'
#!/usr/bin/env bash
# Fake docker: records each call; compose config answers a project name, and every compose call
# fails on FAKE_CONFIG=fail (a compose file that does not read); `run` plays the installed copy as
# FAKE_COPY says (missing, applyfail, checkfail, ok); create fails on FAKE_CREATE=fail; the hop
# image is missing on FAKE_HOP=missing and its pull fails on FAKE_HOP=nopull; `ps` by labels
# answers FAKE_LEFT, whose removal fails on FAKE_RM=fail.
echo "docker $*" >>"$FAKE_CALLS"
if [[ "$1" == compose && "${FAKE_CONFIG:-}" == fail ]]; then
  echo "yaml: line 3: did not find expected key" >&2
  exit 15
fi
case "$*" in
  *" config --format json") printf '{\n  "name": "jobapp",\n  "services": {}\n}\n' ;;
  *" create browser") [[ "${FAKE_CREATE:-}" != fail ]] || { echo "create failed" >&2; exit 1; } ;;
  "image inspect "*) [[ "${FAKE_HOP:-}" != missing && "${FAKE_HOP:-}" != nopull ]] || exit 1 ;;
  "pull "*) [[ "${FAKE_HOP:-}" != nopull ]] || { echo "pull access denied" >&2; exit 1; } ;;
  "ps -aq "*) printf '%s' "${FAKE_LEFT:-}" ;;
  "rm -f "*) [[ "${FAKE_RM:-}" != fail ]] || exit 1 ;;
  "run "*)
    case "${FAKE_COPY:-ok}:${*: -1}" in
      missing:*) echo "nsenter: failed to execute /usr/local/sbin/jobapp-browser-egress: No such file or directory" >&2; exit 127 ;;
      applyfail:apply) echo "iptables-restore: line 3 failed" >&2; exit 1 ;;
      checkfail:check) echo "browser-egress: IPv4 INPUT does not start with the jump" >&2; exit 1 ;;
      *:apply) echo "egress rules applied on br-0123456789ab" ;;
      *:check) echo "egress rules in place on br-0123456789ab (network jobapp_egress)" ;;
    esac
    ;;
esac
EOF
chmod +x "$WORK/bin/docker"
dep() { # dep <expected exit> [env assignments]
  local want="$1"
  shift
  : >"$WORK/calls"
  run "$want" env PATH="$WORK/bin:$PATH" FAKE_CALLS="$WORK/calls" "$@" \
    COMPOSE="docker compose -f compose.yml --profile tunnel" bash "$SCRIPT" deploy
}
removed() { grep -qx "docker compose -f compose.yml --profile tunnel --profile browser rm -s -f browser" "$WORK/calls"; }
hop_image="$(sed -n 's/^HOP_IMAGE=//p' "$SCRIPT")"
# The privileged hop runs as root on the host: a minimal image pinned by digest, never one this
# project builds (PR #307 review, blocking 1).
[[ "$hop_image" =~ ^debian:bookworm-slim@sha256:[0-9a-f]{64}$ ]] || fail "HOP_IMAGE is not pinned by digest: '$hop_image'"
grep -n "jobapp-browser:latest\|--entrypoint nsenter jobapp" "$SCRIPT" tests/reboot_check.sh && fail "the hop runs a project image"
grep -q 'bash ops/browser-egress.sh host-check' tests/reboot_check.sh || fail "reboot_check.sh does not check through host-check"
dep 0
out_has "browser: egress rules in place on br-0123456789ab (network jobapp_egress)"
removed && fail "the browser was removed although the rules passed"
grep -qxF "docker run --rm --privileged --pid=host --network=host --entrypoint nsenter $hop_image -t 1 -m -u -i -n -p -- /usr/local/sbin/jobapp-browser-egress apply" "$WORK/calls" ||
  fail "apply not run through the installed copy in the host's namespaces: $(cat "$WORK/calls")"
grep -q "^docker pull" "$WORK/calls" && fail "the hop image was pulled although present"
create="$(grep -n "create browser" "$WORK/calls" | cut -d: -f1)" apply="$(grep -n " apply$" "$WORK/calls" | cut -d: -f1)" chk="$(grep -n " check$" "$WORK/calls" | cut -d: -f1)"
((create < apply && apply < chk)) || fail "order is create $create, apply $apply, check $chk"
ok "create (the networks), then apply, then check, through the root owned copy; exit 0, nothing removed"
dep 1 FAKE_COPY=missing
out_has "not started, the host firewall is not installed. Once, on the Jetson: sudo bash ~/jobapp/ops/browser-egress.sh install jobapp_egress"
removed || fail "the browser was not removed when the firewall is missing"
grep -q " check$" "$WORK/calls" && fail "check ran after a failed apply"
dep 1 FAKE_COPY=applyfail
out_has "not started, apply failed: iptables-restore: line 3 failed"
removed || fail "the browser was not removed after a failed apply"
dep 1 FAKE_COPY=checkfail
out_has "not started, check failed: browser-egress: IPv4 INPUT does not start with the jump"
removed || fail "the browser was not removed after a failed check"
dep 1 FAKE_CREATE=fail
out_has "browser: not started, compose create failed: create failed; no browser container left"
removed || fail "the previous browser was not removed after a failed create"
grep -q "^docker run" "$WORK/calls" && fail "apply ran after a failed create"
run 1 bash "$SCRIPT" deploy
out_has "set COMPOSE"
ok "not installed, apply failing, check failing, create failing: the browser removed, exit 1 with the reason, nothing applied after a failed create"

step "deploy: a compose file that does not read still removes the previous browser"
dep 1 FAKE_CONFIG=fail FAKE_LEFT=c0ffee
out_has "browser: not started, compose config failed: yaml: line 3: did not find expected key; no browser container left"
grep -qx "docker ps -aq --filter label=com.docker.compose.project=jobapp --filter label=com.docker.compose.service=browser" "$WORK/calls" ||
  fail "the browser was not looked up by its labels: $(cat "$WORK/calls")"
grep -qx "docker rm -f c0ffee" "$WORK/calls" || fail "the previous browser was not removed: $(cat "$WORK/calls")"
grep -q "^docker run\|create browser" "$WORK/calls" && fail "create or apply ran after a failed config"
dep 1 FAKE_CONFIG=fail FAKE_LEFT=
out_has "no browser container left"
grep -q "^docker rm" "$WORK/calls" && fail "rm ran with no container"
dep 1 FAKE_CONFIG=fail FAKE_LEFT=c0ffee FAKE_RM=fail
out_has "removing the previous browser container failed, it may still run: docker rm -f jobapp-browser-1"
out_lacks "no browser container left"
ok "config failing: the previous browser removed by its compose labels, none left said only when true"

step "deploy: the hop image is pulled when missing"
dep 0 FAKE_HOP=missing
grep -qxF "docker pull -q $hop_image" "$WORK/calls" || fail "the hop image was not pulled: $(cat "$WORK/calls")"
dep 1 FAKE_HOP=nopull
out_has "not started, apply failed: cannot pull $hop_image: pull access denied"
removed || fail "the browser was not removed when the hop image cannot be pulled"
ok "a missing hop image is pulled; a failed pull removes the browser, exit 1"

step "host-check: check through the installed copy, no root"
: >"$WORK/calls"
run 0 env PATH="$WORK/bin:$PATH" FAKE_CALLS="$WORK/calls" bash "$SCRIPT" host-check
out_has "egress rules in place on br-0123456789ab (network jobapp_egress)"
grep -qxF "docker run --rm --privileged --pid=host --network=host --entrypoint nsenter $hop_image -t 1 -m -u -i -n -p -- /usr/local/sbin/jobapp-browser-egress check" "$WORK/calls" ||
  fail "host-check did not run check through the hop: $(cat "$WORK/calls")"
run 1 env PATH="$WORK/bin:$PATH" FAKE_CALLS="$WORK/calls" FAKE_COPY=checkfail bash "$SCRIPT" host-check
out_has "browser-egress: IPv4 INPUT does not start with the jump"
ok "host-check exits 0 when the rules are in place, 1 with the reason otherwise"

step "skip: a browser image that did not build removes the previous container, no firewall step"
: >"$WORK/calls"
run 1 env PATH="$WORK/bin:$PATH" FAKE_CALLS="$WORK/calls" COMPOSE="docker compose -f compose.yml --profile tunnel" \
  bash "$SCRIPT" skip "the browser image did not build"
out_has "browser: not started, the browser image did not build; no browser container left"
removed || fail "the previous browser was not removed: $(cat "$WORK/calls")"
grep -q "^docker run\|create browser\|^docker pull" "$WORK/calls" && fail "skip touched the firewall or created the browser: $(cat "$WORK/calls")"
: >"$WORK/calls"
run 1 env PATH="$WORK/bin:$PATH" FAKE_CALLS="$WORK/calls" FAKE_CONFIG=fail FAKE_LEFT=c0ffee \
  COMPOSE="docker compose -f compose.yml --profile tunnel" bash "$SCRIPT" skip "the browser image did not build"
grep -qx "docker rm -f c0ffee" "$WORK/calls" || fail "skip with a compose file that does not read left the browser: $(cat "$WORK/calls")"
run 1 env PATH="$WORK/bin:$PATH" FAKE_CALLS="$WORK/calls" COMPOSE="docker compose -f compose.yml" bash "$SCRIPT" skip
out_has "usage: ops/browser-egress.sh skip <reason>"
ok "skip removes the previous browser (through compose, by labels when the file does not read), exits 1, never creates it or runs the hop; no reason: usage"

step "boot: the browser has no restart policy and nothing at boot starts it"
restart="$(awk '/^  browser:$/ { f = 1; next } f && /^  [a-z]/ { exit } f && /^    restart:/ { print }' compose.yml)"
[[ "$restart" == '    restart: "no"' ]] || fail "the browser service restart policy is '$restart', not \"no\""
unit_start="$(sed -n 's/^ExecStart=//p' "$SCRIPT")"
[[ "$unit_start" == "/usr/local/sbin/jobapp-\$EGRESS_NAME-egress apply --boot" ]] || fail "the boot unit runs '$unit_start'"
grep -n "profile browser\|compose.* up\|create browser" tests/reboot_check.sh && fail "tests/reboot_check.sh starts the browser"
ok "compose: browser restart is \"no\"; the boot unit only applies the rules; the reboot check never starts the browser"

step "backups: the browser profiles are never in them"
# The nightly backup is pg_dump of the database only (ops/db.sh), never a volume: the profiles
# (logins) are out by construction (SPEC-browser, "Persistent profiles and logins").
grep -q "pg_dump" ops/db.sh || fail "ops/db.sh no longer backs up with pg_dump"
grep -Eqi "browserprofiles|docker (volume|run)[^|]*-v |tar .*volumes" ops/db.sh &&
  fail "ops/db.sh reads a volume: the browser profiles must stay out of every backup"
grep -qi "browserprofiles" deploy.sh .github/workflows/deploy.yml ops/previous.sh &&
  fail "a deploy or backup script names the browser profiles volume"
ok "ops/db.sh backs up with pg_dump only and names no volume; no deploy script names browserprofiles"

if [[ "${EGRESS_TEST_DOCKER:-1}" == 1 ]]; then
  step "real iptables in a throwaway container's own network namespace"
  # Never the host's: the container has its own namespace, NET_ADMIN there only. A fake docker
  # answers `network inspect` with BRIDGE, read from a file the test rewrites.
  base=alpine:3.20
  image="$base"
  install="apk add --no-cache -q bash iptables ip6tables iptables-legacy >/dev/null"
  if [[ -n "${JOBAPP_EXTRA_CA:-}" ]]; then
    [[ "$JOBAPP_EXTRA_CA" == /* && -f "$JOBAPP_EXTRA_CA" && -r "$JOBAPP_EXTRA_CA" ]] ||
      fail "JOBAPP_EXTRA_CA is set but does not name a readable file by its absolute path"
    # Behind a TLS inspecting proxy (README): the install alone reads the bundle, as the BuildKit
    # secret of a throwaway image build; a planted line in it must then be nowhere in the image.
    canary="egress-test-canary-$$-$RANDOM$RANDOM"
    { cat "$JOBAPP_EXTRA_CA" && printf '\n# %s\n' "$canary"; } >"$WORK/extra-ca.pem"
    cat >"$WORK/Dockerfile" <<EOF
FROM $base
RUN --mount=type=secret,id=extra_ca,required=true \\
    cat /etc/ssl/certs/ca-certificates.crt /run/secrets/extra_ca >/tmp/extra-ca.pem \\
    && SSL_CERT_FILE=/tmp/extra-ca.pem $install \\
    && rm -f /tmp/extra-ca.pem
EOF
    IMAGE="jobapp-egress-test:$$-$RANDOM"
    # --no-cache: a layer cached from an earlier build would hide what this one keeps.
    docker build -q --no-cache --secret id=extra_ca,src="$WORK/extra-ca.pem" -t "$IMAGE" - \
      <"$WORK/Dockerfile" >"$WORK/out" 2>&1 || fail "the install behind JOBAPP_EXTRA_CA failed: $(tail -n 20 "$WORK/out")"
    rm -f "$WORK/extra-ca.pem"
    CREATED="$(docker create "$IMAGE" true)"
    docker export -o "$WORK/files.tar" "$CREATED" || fail "the files of $IMAGE could not be read"
    docker rm -f "$CREATED" >/dev/null
    CREATED=""
    { docker image inspect "$IMAGE" && docker history --no-trunc "$IMAGE"; } >"$WORK/meta" ||
      fail "the metadata of $IMAGE could not be read"
    hits="$(cat "$WORK/files.tar" "$WORK/meta" | grep -a -c -F -- "$canary" || true)"
    [[ "$hits" == 0 ]] || fail "the bundle of JOBAPP_EXTRA_CA is in the image: its planted line found $hits times in its files or metadata"
    rm -f "$WORK/files.tar"
    ok "behind JOBAPP_EXTRA_CA: the packages installed by a throwaway image build, the bundle in none of its files, variables, labels or history"
    image="$IMAGE"
    install=":"
  fi
  { printf 'set -euo pipefail\n%s\n' "$install" && cat <<'EOF'; } >"$WORK/inner.sh"
mkdir -p /fake/bin
# Alpine's iptables is the nf_tables one; the Jetson's is legacy (1.8.7): link the legacy tools,
# and stop if one is missing (a dangling link would fall through to the nf_tables tool).
for t in iptables ip6tables iptables-restore ip6tables-restore iptables-save ip6tables-save; do
  [ -x "/sbin/${t/tables/tables-legacy}" ] || { echo "no /sbin/${t/tables/tables-legacy}"; exit 1; }
  ln -sf "/sbin/${t/tables/tables-legacy}" "/fake/bin/$t"
done
case "$(/fake/bin/iptables -V)" in *legacy*) ;; *) echo "iptables is not legacy"; exit 1 ;; esac
cat >/fake/bin/docker <<'D'
#!/bin/sh
case "$*" in
  "network inspect"*)
    file=/fake/bridge
    case "$*" in *gmail_egress*) file=/fake/mail-bridge ;; esac
    [ -s "$file" ] && printf '%s|0123456789abcdef\n' "$(cat "$file")" || exit 1 ;;
  info) exit 0 ;;
esac
D
cat >/fake/bin/install <<'D'
#!/bin/sh
cp "$7" "$8" && chmod 0755 "$8"
D
chmod +x /fake/bin/docker /fake/bin/install
export PATH="/fake/bin:$PATH" EGRESS_ROOT=/fake/root EGRESS_RESOLV=/fake/resolv EGRESS_SYSTEMD_RESOLV=/fake/sd
printf 'nameserver 127.0.0.53\n' >/fake/resolv
printf 'nameserver 192.168.1.1\nnameserver 2a01:cb19::1\n' >/fake/sd
S=/repo/ops/browser-egress.sh
C=/fake/root/usr/local/sbin/jobapp-browser-egress
say() { printf 'STEP %s\n' "$*"; }
saved() { iptables-save -t filter | grep -v '^[#:]' | grep -v '^COMMIT'; ip6tables-save -t filter | grep -v '^[#:]' | grep -v '^COMMIT'; }
jumps() { saved | grep -c -- '-j JOBAPP-EGRESS' || true; }

iptables -N DOCKER-USER
iptables -I FORWARD -j DOCKER-USER
iptables -A DOCKER-USER -s 203.0.113.9/32 -j DROP   # the owner's own rule, kept
printf '0123456789ab' | sed 's/^/br-/' >/fake/bridge
bash "$S" install >/dev/null
say "installed"
"$C" check >/dev/null && say "check passes"
first="$(saved)"
"$C" apply >/dev/null
[ "$(saved)" = "$first" ] && say "second apply identical"
[ "$(jumps)" = 4 ] && say "four jumps"
iptables -S DOCKER-USER | grep -- '-s 203.0.113.9/32 -j DROP' >/dev/null && say "user rule kept"
[ "$(iptables -S DOCKER-USER | sed -n 2p)" = "-A DOCKER-USER -i br-0123456789ab -j JOBAPP-EGRESS" ] && say "jump first"
ip6tables -S DOCKER-USER >/dev/null && say "ip6 DOCKER-USER made"
iptables -D JOBAPP-EGRESS -d 192.168.0.0/16 -j DROP
"$C" check >/dev/null 2>&1 || say "check fails on a removed rule"
"$C" apply >/dev/null && "$C" check >/dev/null && say "apply repairs"
iptables -I INPUT 1 -j ACCEPT
"$C" check >/dev/null 2>&1 || say "check fails on a rule above the jump"
"$C" apply >/dev/null && "$C" check >/dev/null && say "apply puts the jump first again"
iptables -D INPUT -j ACCEPT
printf 'br-ffffffffffff' >/fake/bridge
"$C" check >/dev/null 2>&1 || say "check fails when the bridge moved"
"$C" apply >/dev/null
[ "$(jumps)" = 4 ] && ! saved | grep br-0123456789ab >/dev/null && say "new bridge replaces the old jumps"
grep -qx 'BRIDGE=br-ffffffffffff' /fake/root/etc/jobapp-browser-egress.conf && grep -qx 'RESOLVERS=192.168.1.1' /fake/root/etc/jobapp-browser-egress.conf && say "recorded"
grep -q 'Before=docker.service' /fake/root/etc/systemd/system/jobapp-browser-egress.service && say "boot unit"
# Docker's answer for a network without a bridge name option, as compose makes them.
printf '<no value>' >/fake/bridge
"$C" apply >/dev/null && "$C" check >/dev/null && [ "$(jumps)" = 4 ] && saved | grep 'br-0123456789ab' >/dev/null &&
  grep -qx 'BRIDGE=br-0123456789ab' /fake/root/etc/jobapp-browser-egress.conf && say "no name option: the bridge from the id"
printf 'br-ffffffffffff' >/fake/bridge
"$C" apply >/dev/null
for t in iptables ip6tables; do
  $t -D DOCKER-USER -i br-ffffffffffff -j JOBAPP-EGRESS; $t -D INPUT -i br-ffffffffffff -j JOBAPP-EGRESS-IN
  $t -F JOBAPP-EGRESS; $t -X JOBAPP-EGRESS; $t -F JOBAPP-EGRESS-IN; $t -X JOBAPP-EGRESS-IN
done
: >/fake/bridge
"$C" apply --boot >/dev/null
[ "$(jumps)" = 4 ] && saved | grep 'br-ffffffffffff' >/dev/null && say "boot applies the recorded bridge without docker"
# Both kinds use real legacy iptables here, exclusively in this disposable network namespace.
# Different bridges model Compose's browser egress and isolated Gmail provider network.
printf 'br-ffffffffffff' >/fake/bridge
printf 'br-111111111111' >/fake/mail-bridge
M=/fake/root/usr/local/sbin/jobapp-mail-egress
JOBAPP_EGRESS_KIND=mail bash "$S" install >/dev/null
"$M" check >/dev/null && "$C" check >/dev/null && say "mail and browser coexist"
mail_jumps() { saved | grep -c -- '-j JOBAPP-MAIL-EGRESS' || true; }
[ "$(jumps)" = 4 ] && [ "$(mail_jumps)" = 4 ] && say "mail has four distinct jumps"
first="$(saved)"
"$M" apply >/dev/null
"$M" apply >/dev/null
[ "$(saved)" = "$first" ] && "$C" check >/dev/null && say "mail second apply identical with browser retained"
iptables -D JOBAPP-MAIL-EGRESS -d 169.254.0.0/16 -j DROP
if "$M" check >/dev/null 2>&1; then exit 1; fi
"$C" check >/dev/null && say "mail drift fails while browser passes"
"$M" apply >/dev/null && "$M" check >/dev/null && say "mail apply repairs"
ip6tables -I DOCKER-USER 1 -i br-111111111111 -j JOBAPP-MAIL-EGRESS
if "$M" check >/dev/null 2>&1; then exit 1; fi
"$M" apply >/dev/null
[ "$(mail_jumps)" = 4 ] && "$M" check >/dev/null && say "mail duplicate jump repaired"
iptables -I INPUT 1 -j ACCEPT
if "$M" check >/dev/null 2>&1; then exit 1; fi
if "$C" check >/dev/null 2>&1; then exit 1; fi
iptables -D INPUT -j ACCEPT
"$M" check >/dev/null && "$C" check >/dev/null && say "both detect unmanaged rule above jumps"
printf 'br-222222222222' >/fake/mail-bridge
if "$M" check >/dev/null 2>&1; then exit 1; fi
"$M" apply >/dev/null
[ "$(mail_jumps)" = 4 ] && ! saved | grep br-111111111111 >/dev/null &&
  "$C" check >/dev/null && "$M" check >/dev/null && say "mail bridge change preserves browser"
# Removing either kind must preserve the other, including its installed copy and record.
"$C" uninstall >/dev/null
[ "$(jumps)" = 0 ] && [ "$(mail_jumps)" = 4 ] && "$M" check >/dev/null &&
  [ -e "$M" ] && [ -e /fake/root/etc/jobapp-mail-egress.conf ] && say "browser uninstall preserves mail"
bash "$S" install >/dev/null
"$C" check >/dev/null && "$M" check >/dev/null
"$M" uninstall >/dev/null
[ "$(mail_jumps)" = 0 ] && ! saved | grep JOBAPP-MAIL >/dev/null &&
  [ ! -e "$M" ] && [ ! -e /fake/root/etc/jobapp-mail-egress.conf ] &&
  [ ! -e /fake/root/etc/systemd/system/jobapp-mail-egress.service ] &&
  "$C" check >/dev/null && say "mail uninstall preserves browser and removes mail files"
"$C" uninstall >/dev/null
[ "$(jumps)" = 0 ] && ! saved | grep JOBAPP >/dev/null && [ ! -e "$C" ] && [ ! -e /fake/root/etc/jobapp-browser-egress.conf ] && say "uninstall leaves nothing"
iptables -S DOCKER-USER | grep -- '-s 203.0.113.9/32 -j DROP' >/dev/null && say "user rule kept after uninstall"
EOF
  docker run --rm --cap-add NET_ADMIN --network bridge --label jobapp-egress-test \
    -v "$ROOT:/repo:ro" -v "$WORK/inner.sh:/inner.sh:ro" "$image" sh /inner.sh >"$WORK/out" 2>&1 ||
    fail "the container run failed: $(tail -n 20 "$WORK/out")"
  for s in "installed" "check passes" "second apply identical" "four jumps" "user rule kept" "jump first" \
    "ip6 DOCKER-USER made" "check fails on a removed rule" "apply repairs" "check fails on a rule above the jump" \
    "apply puts the jump first again" "check fails when the bridge moved" "new bridge replaces the old jumps" \
    "recorded" "boot unit" "no name option: the bridge from the id" "boot applies the recorded bridge without docker" "uninstall leaves nothing" \
    "user rule kept after uninstall" "mail and browser coexist" "mail has four distinct jumps" \
    "mail second apply identical with browser retained" "mail drift fails while browser passes" \
    "mail apply repairs" "mail duplicate jump repaired" "both detect unmanaged rule above jumps" \
    "mail bridge change preserves browser" "browser uninstall preserves mail" \
    "mail uninstall preserves browser and removes mail files"; do
    grep -qx "STEP $s" "$WORK/out" || fail "real iptables: '$s' missing: $(tail -n 30 "$WORK/out")"
  done
  ok "real iptables (legacy, as the Jetson): browser and mail coexist, idempotence, drift, bridge change, boot and independent uninstall"
fi

printf '\n%d checks passed\n' "$passed"
