#!/usr/bin/env bash
# What a CI job leaves in its runner on the Mac, removed (ops/ci-runner/, issue #83).
#
#   ci-clean.sh all   before a job and when the container starts: every container, network and
#                     volume of this instance's Docker daemon. Nothing legitimate exists then.
#   ci-clean.sh job   after a job: the same, except the service containers and the network the
#                     runner made for `services:` (named github_network_*), which the runner
#                     removes itself in its own last step; then the disk bounds below.
#
# `docker` here is the daemon inside this runner's container, never the Mac's: it holds only
# what CI jobs of this instance created, so removing all of it touches nobody else's containers,
# images, networks or volumes. Images and the build cache are kept between jobs (they are the
# layer cache of the e2e builds) within these bounds:
#   CI_BUILD_CACHE_KEEP   build cache kept after a job (default 1GB; older layers go first)
#   CI_DOCKER_MAX_MB      size of /var/lib/docker above which everything is pruned and the next
#                         job starts cold (default 4000)
# Never fails a job: every command is best effort and the script ends with status 0.
set -uo pipefail

mode="${1:-job}"
keep="${CI_BUILD_CACHE_KEEP:-1GB}"
max_mb="${CI_DOCKER_MAX_MB:-4000}"
say() { printf 'ci-clean: %s\n' "$*"; }

docker info >/dev/null 2>&1 || {
  say "the Docker daemon of this runner does not answer: nothing cleaned"
  exit 0
}

# No mapfile: the tests of this script also run under the Mac's bash 3.2.
containers=()
networks=()
kept=" "
if [[ "$mode" != all ]]; then
  for net in $(docker network ls -q --filter 'name=^github_network_' 2>/dev/null); do
    for id in $(docker ps -aq --no-trunc --filter "network=$net" 2>/dev/null); do kept+="$id "; done
  done
fi
for id in $(docker ps -aq --no-trunc 2>/dev/null); do
  [[ "$kept" == *" $id "* ]] || containers+=("$id")
done
while read -r id name; do
  [[ -n "$id" ]] || continue
  [[ "$mode" != all && "$name" == github_network_* ]] || networks+=("$id")
done < <(docker network ls --filter type=custom --format '{{.ID}} {{.Name}}' 2>/dev/null)

if ((${#containers[@]})); then
  say "removing ${#containers[@]} container(s)"
  timeout 120 docker rm -f -v "${containers[@]}" >/dev/null 2>&1 || true
fi
if ((${#networks[@]})); then
  say "removing ${#networks[@]} network(s)"
  timeout 60 docker network rm "${networks[@]}" >/dev/null 2>&1 || true
fi
volumes="$(timeout 120 docker volume prune -af 2>/dev/null | tail -n 1)"
say "volumes: ${volumes:-none}"

# Disk bounds. Dangling images are what a rebuilt jobapp-api:latest leaves behind.
timeout 120 docker image prune -f >/dev/null 2>&1 || true
timeout 300 docker builder prune -f --keep-storage "$keep" >/dev/null 2>&1 || true
used_mb="$(sudo -n du -sm /var/lib/docker 2>/dev/null | cut -f1)"
if [[ "$used_mb" =~ ^[0-9]+$ ]]; then
  if ((used_mb > max_mb)); then
    say "/var/lib/docker holds ${used_mb} MB, over the bound of ${max_mb} MB: pruning every image and the build cache"
    if [[ "$mode" == all ]]; then
      timeout 300 docker system prune -af --volumes >/dev/null 2>&1 || true
    else
      timeout 300 docker image prune -af >/dev/null 2>&1 || true
    fi
    timeout 300 docker builder prune -af >/dev/null 2>&1 || true
    used_mb="$(sudo -n du -sm /var/lib/docker 2>/dev/null | cut -f1)"
  fi
  say "/var/lib/docker: ${used_mb} MB (bound ${max_mb} MB)"
fi
exit 0
