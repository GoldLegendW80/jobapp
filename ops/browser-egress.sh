#!/usr/bin/env bash
# The host firewall of the browser's `egress` network (browser B6, issue #230; SPEC-browser.md,
# "Isolation and network"). The browser container has no NET_ADMIN, so what leaves its network is
# filtered on the host, from the network's bridge:
#   DOCKER-USER (forwarded traffic): DNS to the resolvers Docker forwards to passes; every other
#     UDP is dropped; then private, loopback, link local, CGNAT (the tailnet), documentation,
#     benchmark, multicast and reserved IPv4 ranges are dropped; the rest (the internet) passes;
#   INPUT (traffic to the host itself, whichever of its addresses): everything is dropped;
#   IPv6: everything is dropped in both, since `egress` has no IPv6 (compose.yml), so a packet
#     of it is unexpected and the home network's IPv6 prefix is public.
# Both families sit in two chains of their own, JOBAPP-EGRESS and JOBAPP-EGRESS-IN, jumped to as
# the first rule of DOCKER-USER and INPUT, written in one iptables-restore per family (atomic: no
# moment without the rules), so a rerun replaces them and never adds a duplicate.
#
# Root, on the Jetson (the owner types sudo; nothing else here asks for it):
#   sudo bash ops/browser-egress.sh install [network]
#       once: a root owned copy at /usr/local/sbin/jobapp-browser-egress, the record
#       /etc/jobapp-browser-egress.conf, the boot unit jobapp-browser-egress.service (the rules of
#       the recorded bridge before docker starts, so a reboot never starts the browser without
#       them), then apply. network: the compose network, default jobapp_egress
#   sudo bash ops/browser-egress.sh uninstall
#       the jumps, the chains, the unit, the record and the copy removed; the browser is left
#       as it is (deploy stops it at the next run)
#   jobapp-browser-egress apply [--boot]
#       the rules for the network's bridge as Docker names it now, recorded; --boot: the recorded
#       bridge and resolvers, Docker not running yet
#   jobapp-browser-egress check
#       exit 0 when the live rules are exactly these, each jump once and first in its chain
# As the deploy user (docker group), in the deploy directory, by deploy.sh and deploy.yml:
#   COMPOSE="docker compose -f compose.yml --profile tunnel" bash ops/browser-egress.sh deploy
#       creates the browser's networks, then apply and check through the installed copy, run in
#       the host's namespaces by a one off privileged container of the browser image (the docker
#       group is root on the host already; only the owner's root owned copy runs). Exit 0: start
#       the browser (--profile browser). Otherwise the browser container is removed and the
#       reason printed, exit 1: the rest of the stack deploys without it.
# Anywhere, no root:
#   ops/browser-egress.sh rules <bridge> [resolver ...]
#       print the iptables-restore input of both families, for review and tests
#
# Settings for tests only: EGRESS_ROOT (prefix of /etc, /usr/local/sbin, /run and the unit
# directory), EGRESS_RESOLV and EGRESS_SYSTEMD_RESOLV (the two resolver files Docker reads).
set -euo pipefail

ROOTFS="${EGRESS_ROOT:-}"
COPY="$ROOTFS/usr/local/sbin/jobapp-browser-egress"
CONF="$ROOTFS/etc/jobapp-browser-egress.conf"
UNIT="$ROOTFS/etc/systemd/system/jobapp-browser-egress.service"
RESOLV="${EGRESS_RESOLV:-/etc/resolv.conf}"
SYSTEMD_RESOLV="${EGRESS_SYSTEMD_RESOLV:-/run/systemd/resolve/resolv.conf}"
CHAIN=JOBAPP-EGRESS
CHAIN_IN=JOBAPP-EGRESS-IN
# Every IPv4 range that is not the internet (RFC 6890 and the proxy's own list in
# browser/browserd/proxy.py): this network, private, CGNAT and the tailnet, loopback, link local
# and cloud metadata, IETF protocol assignments, the three documentation nets, benchmarking,
# multicast, reserved and broadcast.
PRIVATE4=(0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12
  192.0.0.0/24 192.0.2.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24 203.0.113.0/24
  224.0.0.0/4 240.0.0.0/4)

say() { printf '%s\n' "$*"; }
die() {
  printf 'browser-egress: %s\n' "$*" >&2
  exit 1
}
usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

valid_bridge() { [[ "$1" =~ ^[A-Za-z0-9_.-]{1,15}$ ]]; }
valid_ipv4() {
  local IFS=. octet
  [[ "$1" =~ ^(0|[1-9][0-9]{0,2})(\.(0|[1-9][0-9]{0,2})){3}$ ]] || return 1 # no leading zero: never read as octal
  # shellcheck disable=SC2086 # split on the dots on purpose
  for octet in $1; do ((10#$octet <= 255)) || return 1; done
}

# resolvers: the IPv4 servers Docker's embedded DNS forwards to from the container's own network
# namespace, read as Docker reads them: /etc/resolv.conf, or systemd-resolved's upstream file when
# the first one names loopback servers only (127.0.0.53). Loopback servers are queried from the
# host's namespace by Docker and never cross the bridge. None found: no DNS passes (fail closed).
resolvers() {
  local file="$RESOLV" servers
  servers="$(awk '$1 == "nameserver" { print $2 }' "$file" 2>/dev/null || true)"
  if [[ -z "$(grep -Ev '^(127\.|::1$)' <<<"$servers" || true)" && -r "$SYSTEMD_RESOLV" ]]; then
    servers="$(awk '$1 == "nameserver" { print $2 }' "$SYSTEMD_RESOLV")"
  fi
  local s
  for s in $servers; do
    if valid_ipv4 "$s" && [[ "$s" != 127.* ]]; then printf '%s\n' "$s"; fi
  done | awk '!seen[$0]++'
}

# The chains' rules as `iptables -S` prints them, so check compares text with text.
chain4() { # chain4 <resolver ...>
  local r p net
  printf -- '-N %s\n' "$CHAIN"
  for r in "$@"; do
    for p in udp tcp; do printf -- '-A %s -d %s/32 -p %s -m %s --dport 53 -j RETURN\n' "$CHAIN" "$r" "$p" "$p"; done
  done
  printf -- '-A %s -p udp -j DROP\n' "$CHAIN"
  for net in "${PRIVATE4[@]}"; do printf -- '-A %s -d %s -j DROP\n' "$CHAIN" "$net"; done
}
chain6() { printf -- '-N %s\n-A %s -j DROP\n' "$CHAIN" "$CHAIN"; }
chain_in() { printf -- '-N %s\n-A %s -j DROP\n' "$CHAIN_IN" "$CHAIN_IN"; }

# restore_input <family 4|6> <bridge> <current jumps, `iptables -S` lines> [resolver ...]: one
# transaction that empties both chains, removes every jump to them (an old bridge's too), puts
# one jump first in DOCKER-USER and in INPUT, and fills the chains.
restore_input() {
  local family="$1" bridge="$2" jumps="$3" line
  shift 3
  printf '*filter\n:%s - [0:0]\n:%s - [0:0]\n' "$CHAIN" "$CHAIN_IN"
  while IFS= read -r line; do
    [[ "$line" == "-A "* ]] && printf -- '-D %s\n' "${line#-A }"
  done <<<"$jumps"
  printf -- '-I DOCKER-USER 1 -i %s -j %s\n' "$bridge" "$CHAIN"
  printf -- '-I INPUT 1 -i %s -j %s\n' "$bridge" "$CHAIN_IN"
  if [[ "$family" == 4 ]]; then chain4 "$@"; else chain6; fi | grep -v '^-N '
  chain_in | grep -v '^-N '
  printf 'COMMIT\n'
}

ipt() { if [[ "$1" == 4 ]]; then iptables -w "${@:2}"; else ip6tables -w "${@:2}"; fi; }
ipt_restore() { if [[ "$1" == 4 ]]; then iptables-restore -w --noflush; else ip6tables-restore -w --noflush; fi; }
our_jumps() { # our_jumps <family>: the jumps to our chains in DOCKER-USER and INPUT
  { ipt "$1" -S DOCKER-USER 2>/dev/null || true; ipt "$1" -S INPUT; } |
    awk -v a="$CHAIN" -v b="$CHAIN_IN" '$NF == a || $NF == b'
}

# Docker makes DOCKER-USER and its jump from FORWARD when it starts; before it (--boot) they are
# made here, the way Docker makes them, and Docker keeps both.
ensure_docker_user() {
  ipt "$1" -S DOCKER-USER >/dev/null 2>&1 || ipt "$1" -N DOCKER-USER
  ipt "$1" -C FORWARD -j DOCKER-USER 2>/dev/null || ipt "$1" -I FORWARD 1 -j DOCKER-USER
}

conf_get() { # conf_get <NAME>: a value of the record, never sourced
  [[ -r "$CONF" ]] || return 0
  sed -n "s/^$1=//p" "$CONF" | tail -n 1
}

bridge_of() { # bridge_of <network>: the bridge Docker made for it
  local out name id
  out="$(docker network inspect -f '{{index .Options "com.docker.network.bridge.name"}}|{{.Id}}' "$1" 2>/dev/null)" ||
    return 1
  name="${out%%|*}" id="${out#*|}"
  [[ -n "$name" ]] || name="br-${id:0:12}"
  valid_bridge "$name" && printf '%s\n' "$name"
}

need_root() { [[ "$(id -u)" == 0 ]] || die "$1 needs root: sudo bash ops/browser-egress.sh $1"; }

cmd_apply() {
  need_root apply
  local network bridge list=() f
  network="$(conf_get NETWORK)"
  [[ -n "$network" ]] || die "not installed: sudo bash ops/browser-egress.sh install"
  if [[ "${1:-}" == --boot ]]; then
    bridge="$(conf_get BRIDGE)"
    read -r -a list <<<"$(conf_get RESOLVERS)"
    [[ -n "$bridge" ]] || { say "no bridge recorded yet: nothing to guard before docker"; return 0; }
  else
    bridge="$(bridge_of "$network")" || die "no Docker network $network: create the browser first (deploy does)"
    read -r -a list <<<"$(resolvers | tr '\n' ' ')"
  fi
  valid_bridge "$bridge" || die "bad bridge name: $bridge"
  for f in ${list[@]+"${list[@]}"}; do valid_ipv4 "$f" || die "bad resolver: $f"; done
  for f in 4 6; do
    ensure_docker_user "$f"
    restore_input "$f" "$bridge" "$(our_jumps "$f")" ${list[@]+"${list[@]}"} | ipt_restore "$f"
  done
  if [[ "${1:-}" != --boot ]]; then
    printf 'NETWORK=%s\nBRIDGE=%s\nRESOLVERS=%s\n' "$network" "$bridge" "${list[*]-}" >"$CONF.new"
    mv -f "$CONF.new" "$CONF"
  fi
  say "egress rules applied on $bridge (network $network; DNS to ${list[*]:-no resolver})"
}

cmd_check() {
  need_root check
  local network bridge list=() f want got problems=()
  network="$(conf_get NETWORK)"
  [[ -n "$network" ]] || die "not installed: sudo bash ops/browser-egress.sh install"
  bridge="$(conf_get BRIDGE)"
  read -r -a list <<<"$(conf_get RESOLVERS)"
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    f="$(bridge_of "$network" || true)"
    [[ "$f" == "$bridge" ]] || problems+=("network $network is on bridge '${f:-none}', the rules on '$bridge'")
  fi
  for f in 4 6; do
    if [[ "$f" == 4 ]]; then want="$(chain4 ${list[@]+"${list[@]}"})"; else want="$(chain6)"; fi
    got="$(ipt "$f" -S "$CHAIN" 2>/dev/null || true)"
    [[ "$got" == "$want" ]] || problems+=("IPv$f $CHAIN differs from the expected rules")
    got="$(ipt "$f" -S "$CHAIN_IN" 2>/dev/null || true)"
    [[ "$got" == "$(chain_in)" ]] || problems+=("IPv$f $CHAIN_IN differs from the expected rules")
    got="$(our_jumps "$f")"
    want="$(printf -- '-A DOCKER-USER -i %s -j %s\n-A INPUT -i %s -j %s' "$bridge" "$CHAIN" "$bridge" "$CHAIN_IN")"
    [[ "$got" == "$want" ]] || problems+=("IPv$f jumps are not exactly one per chain for $bridge")
    [[ "$(ipt "$f" -S DOCKER-USER 2>/dev/null | sed -n 2p)" == "-A DOCKER-USER -i $bridge -j $CHAIN" ]] ||
      problems+=("IPv$f DOCKER-USER does not start with the jump")
    [[ "$(ipt "$f" -S INPUT | grep -m 1 '^-A ' || true)" == "-A INPUT -i $bridge -j $CHAIN_IN" ]] ||
      problems+=("IPv$f INPUT does not start with the jump")
    ipt "$f" -C FORWARD -j DOCKER-USER 2>/dev/null || problems+=("IPv$f FORWARD does not jump to DOCKER-USER")
  done
  if ((${#problems[@]})); then
    printf 'browser-egress: %s\n' "${problems[@]}" >&2
    return 1
  fi
  say "egress rules in place on $bridge (network $network)"
}

cmd_install() {
  need_root install
  local network="${1:-jobapp_egress}" self
  [[ "$network" =~ ^[a-z0-9][a-z0-9_.-]*$ ]] || die "bad network name: $network"
  self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
  mkdir -p "$(dirname "$COPY")" "$(dirname "$UNIT")" "$(dirname "$CONF")"
  install -o 0 -g 0 -m 0755 "$self" "$COPY.new" && mv -f "$COPY.new" "$COPY"
  if [[ "$(conf_get NETWORK)" != "$network" ]]; then
    printf 'NETWORK=%s\nBRIDGE=\nRESOLVERS=\n' "$network" >"$CONF.new"
    mv -f "$CONF.new" "$CONF"
  fi
  chmod 0644 "$CONF"
  cat >"$UNIT" <<EOF
[Unit]
Description=jobapp: host firewall of the browser's egress network (ops/browser-egress.sh)
DefaultDependencies=no
After=local-fs.target
Before=docker.service network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/jobapp-browser-egress apply --boot

[Install]
WantedBy=multi-user.target docker.service
EOF
  if [[ -z "$ROOTFS" ]]; then
    systemctl daemon-reload
    systemctl enable jobapp-browser-egress.service
  fi
  say "installed: $COPY, $CONF, $UNIT"
  if bridge_of "$network" >/dev/null; then
    "$COPY" apply
  else
    say "network $network does not exist yet: the next deploy creates it and applies the rules"
  fi
}

cmd_uninstall() {
  need_root uninstall
  local f line chain rule=()
  for f in 4 6; do
    while IFS= read -r line; do
      [[ -n "$line" ]] || continue
      read -r -a rule <<<"${line#-A }"
      ipt "$f" -D "${rule[@]}" || true
    done <<<"$(our_jumps "$f")"
    for chain in "$CHAIN" "$CHAIN_IN"; do
      ipt "$f" -F "$chain" 2>/dev/null || true
      ipt "$f" -X "$chain" 2>/dev/null || true
    done
  done
  if [[ -z "$ROOTFS" && -f "$UNIT" ]]; then systemctl disable jobapp-browser-egress.service || true; fi
  rm -f "$UNIT" "$CONF" "$COPY"
  [[ -n "$ROOTFS" ]] || systemctl daemon-reload
  say "uninstalled: no egress rule, chain, unit, record or copy left; the browser stays as it is until the next deploy stops it"
}

cmd_deploy() {
  : "${COMPOSE:?set COMPOSE to the docker compose command of the stack}"
  local image=jobapp-browser:latest project network out
  # shellcheck disable=SC2086 # COMPOSE is a command line
  project="$($COMPOSE config --format json | sed -n 's/^  "name": "\(.*\)",$/\1/p' | awk 'NR == 1')"
  [[ -n "$project" ]] || die "cannot read the compose project name"
  network="${project}_egress"
  # shellcheck disable=SC2086
  if ! $COMPOSE --profile browser create browser >/dev/null; then
    say "browser: compose create failed, the browser stays stopped" >&2
    return 1
  fi
  local run=(docker run --rm --privileged --pid=host --network=host --entrypoint nsenter "$image"
    -t 1 -m -u -i -n -p -- /usr/local/sbin/jobapp-browser-egress)
  local reason=""
  if ! out="$("${run[@]}" apply 2>&1)"; then
    reason="apply failed: ${out:-no output}"
  elif ! out="$("${run[@]}" check 2>&1)"; then
    reason="check failed: ${out:-no output}"
  fi
  if [[ "$out" == *"No such file"* || "$out" == *"not installed"* ]]; then
    reason="the host firewall is not installed. Once, on the Jetson: sudo bash ~/jobapp/ops/browser-egress.sh install $network"
  fi
  if [[ -n "$reason" ]]; then
    # shellcheck disable=SC2086
    $COMPOSE --profile browser rm -s -f browser >/dev/null 2>&1 || true
    printf 'browser: not started, %s\n' "$reason" >&2
    return 1
  fi
  printf 'browser: %s\n' "$out"
}

main() {
  local cmd="${1:-}"
  [[ $# -eq 0 ]] || shift
  case "$cmd" in
    install) cmd_install "$@" ;;
    uninstall) cmd_uninstall ;;
    apply) cmd_apply "$@" ;;
    check) cmd_check ;;
    deploy) cmd_deploy ;;
    rules)
      if [[ $# -lt 1 ]] || ! valid_bridge "$1"; then die "usage: ops/browser-egress.sh rules <bridge> [resolver ...]"; fi
      local r
      for r in "${@:2}"; do valid_ipv4 "$r" || die "bad resolver: $r"; done
      printf '# IPv4\n'
      restore_input 4 "$1" "" "${@:2}"
      printf '# IPv6\n'
      restore_input 6 "$1" ""
      ;;
    -h | --help | help) usage ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
