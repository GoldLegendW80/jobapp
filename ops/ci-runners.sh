#!/usr/bin/env bash
# The CI runners on the Mac (issue #83; README, Continuous delivery). Run on the Mac, no sudo:
#
#   ops/ci-runners.sh install [count]   build the image, register and start `count` runners
#                                       (default 4, or as many as exist); safe to run again
#   ops/ci-runners.sh status            containers, GitHub's view, memory, disk used
#   ops/ci-runners.sh stop | start      stop or start every runner container (registration kept)
#   ops/ci-runners.sh update            rebuild the image from ops/ci-runner and recreate the
#                                       containers; registrations, caches and images are kept
#   ops/ci-runners.sh prune             empty the shared tool caches and every runner's Docker
#                                       daemon (refused while a job runs)
#   ops/ci-runners.sh uninstall         unregister every runner, remove containers, volumes, image
#
# What it creates, all in Docker Desktop and nothing in the repository or the home folder:
#   image       jobapp-ci-runner:<runner version>      (ops/ci-runner/Dockerfile)
#   containers  jobapp-ci-1 .. jobapp-ci-<count>       restart unless-stopped, --privileged (they
#                                                      run a Docker daemon of their own), a CPU set
#                                                      and a memory limit each
#   volumes     jobapp-ci                              runner directories, work folders, caches
#               jobapp-ci-docker-1 .. -<count>         /var/lib/docker of each instance
#   GitHub      runners mac-ci-1 .. mac-ci-<count>, labels self-hosted, Linux, ARM64, mac-ci
# Everything carries the label jobapp.ci=runner. Only objects with these names are ever removed.
#
# The registration and removal tokens go from `gh api` straight into the container's stdin
# through a pipe: never in a variable, an argument, a file or the output. The containers get no
# variable of the calling shell (docker run passes only the -e options written below), so no
# token or key of the Mac's environment reaches a job.
#
# Settings (environment): CI_RUNNER_REPO (GoldLegendW80/jobapp), CI_RUNNER_MEMORY (5g, per
# runner), CI_RUNNER_CPUS (CPUs per runner, default: Docker's CPUs divided by the count).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONTEXT="$ROOT/ops/ci-runner"
REPO="${CI_RUNNER_REPO:-GoldLegendW80/jobapp}"
MEMORY="${CI_RUNNER_MEMORY:-5g}"
LABEL="jobapp.ci=runner"
SHARED_VOLUME="jobapp-ci"
DEFAULT_COUNT=4

die() {
  printf 'ci-runners: %s\n' "$*" >&2
  exit 1
}
step() { printf '\n== %s\n' "$*"; }
info() { printf '   %s\n' "$*"; }

runner_version() { sed -n 's/^ARG RUNNER_VERSION=//p' "$CONTEXT/Dockerfile" | head -n 1; }
image() { printf 'jobapp-ci-runner:%s' "$(runner_version)"; }
container() { printf 'jobapp-ci-%s' "$1"; }
docker_volume() { printf 'jobapp-ci-docker-%s' "$1"; }
runner_name() { printf 'mac-ci-%s' "$1"; }

checks() {
  [[ "$REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "CI_RUNNER_REPO must be owner/name"
  command -v docker >/dev/null || die "docker is needed"
  command -v gh >/dev/null || die "gh is needed for the runner tokens"
  docker info >/dev/null 2>&1 || die "Docker Desktop is not running"
  [[ -n "$(runner_version)" ]] || die "ops/ci-runner/Dockerfile pins no RUNNER_VERSION"
}

# The indexes of the runner containers that exist, in order.
existing() {
  docker ps -a --filter "label=$LABEL" --format '{{.Names}}' |
    sed -n 's/^jobapp-ci-\([0-9][0-9]*\)$/\1/p' | sort -n
}

# github_state <n>: "<id> <status> <busy>" of runner n on GitHub, or nothing.
github_state() {
  gh api "repos/$REPO/actions/runners" --paginate \
    --jq ".runners[] | select(.name == \"$(runner_name "$1")\") | \"\(.id) \(.status) \(.busy)\"" |
    head -n 1
}

any_busy() {
  [[ -n "$(gh api "repos/$REPO/actions/runners" --paginate \
    --jq '.runners[] | select((.name | startswith("mac-ci-")) and .busy) | .name')" ]]
}

# The options shared by configure, remove and run for instance <n>.
base_opts() {
  local n="$1"
  OPTS=(--label "$LABEL" -e "CI_RUNNER_INDEX=$n" -v "$SHARED_VOLUME:/ci"
    -e "RUNNER_URL=https://github.com/$REPO" -e "RUNNER_NAME=$(runner_name "$n")"
    -e "RUNNER_LABELS=mac-ci")
}

build_image() {
  step "image $(image)"
  docker build -q --label "$LABEL" -t "$(image)" "$CONTEXT" >/dev/null || die "docker build failed"
  info "built from ops/ci-runner (linux/arm64, actions runner $(runner_version))"
}

is_registered() { # the local registration file and GitHub's record both exist
  local n="$1"
  docker run --rm --entrypoint test "${OPTS[@]}" "$(image)" -f "/ci/runner-$n/.runner" 2>/dev/null &&
    [[ -n "$(github_state "$n")" ]]
}

register() {
  local n="$1"
  base_opts "$n"
  if is_registered "$n"; then
    info "$(runner_name "$n"): already registered"
    return 0
  fi
  docker rm -f "$(container "$n")" >/dev/null 2>&1 || true
  # The token: from gh into the container's stdin, nowhere else.
  gh api -X POST "repos/$REPO/actions/runners/registration-token" --jq .token |
    docker run --rm -i "${OPTS[@]}" "$(image)" configure >/dev/null ||
    die "registration of $(runner_name "$n") failed (gh needs admin rights on $REPO)"
  info "$(runner_name "$n"): registered"
}

start_container() {
  local n="$1" count="$2" total per first last
  base_opts "$n"
  total="$(docker info --format '{{.NCPU}}')"
  per="${CI_RUNNER_CPUS:-$((total / count))}"
  ((per >= 1)) || per=1
  first=$(((n - 1) * per % total))
  last=$((first + per - 1))
  ((last < total)) || last=$((total - 1))
  docker rm -f "$(container "$n")" >/dev/null 2>&1 || true
  docker run -d --name "$(container "$n")" --hostname "$(runner_name "$n")" \
    --restart unless-stopped --privileged \
    --cpuset-cpus "$first-$last" --memory "$MEMORY" --memory-swap "$MEMORY" \
    --log-opt max-size=10m --log-opt max-file=3 \
    -v "$(docker_volume "$n"):/var/lib/docker" \
    "${OPTS[@]}" "$(image)" run >/dev/null || die "$(container "$n") did not start"
  info "$(container "$n"): CPUs $first-$last, memory $MEMORY"
}

wait_online() {
  local n deadline=$((SECONDS + 120)) pending
  while :; do
    pending=""
    for n in "$@"; do
      [[ "$(github_state "$n")" == *" online "* ]] || pending+=" $(runner_name "$n")"
    done
    [[ -z "$pending" ]] && return 0
    ((SECONDS < deadline)) || die "not online on GitHub after 120s:$pending (docker logs jobapp-ci-<n>)"
    sleep 4
  done
}

cmd_install() {
  local count="${1:-}" n indexes=()
  if [[ -z "$count" ]]; then
    count="$(existing | wc -l | tr -d ' ')"
    ((count > 0)) || count=$DEFAULT_COUNT
  fi
  [[ "$count" =~ ^[1-9][0-9]?$ ]] || die "count must be a number from 1 to 99"
  build_image
  docker volume create --label "$LABEL" "$SHARED_VOLUME" >/dev/null
  step "registration on $REPO (labels self-hosted, Linux, ARM64, mac-ci)"
  for n in $(seq 1 "$count"); do
    docker volume create --label "$LABEL" "$(docker_volume "$n")" >/dev/null
    register "$n"
    indexes+=("$n")
  done
  step "containers (restart unless-stopped)"
  for n in "${indexes[@]}"; do start_container "$n" "$count"; done
  wait_online "${indexes[@]}"
  info "online on GitHub: $count runner(s)"
  info "they start with Docker Desktop; see README, Continuous delivery, for login and sleep"
}

cmd_update() {
  local n count indexes=()
  while IFS= read -r n; do indexes+=("$n"); done < <(existing)
  count="${#indexes[@]}"
  ((count > 0)) || die "no runner installed: ops/ci-runners.sh install"
  any_busy && die "a job is running on a Mac runner: try again when it is done"
  build_image
  step "containers recreated from $(image)"
  for n in "${indexes[@]}"; do start_container "$n" "$count"; done
  wait_online "${indexes[@]}"
  info "online on GitHub: $count runner(s)"
}

cmd_status() {
  local n state line ok=0 seen=0
  step "runners on $REPO"
  for n in $(existing); do
    seen=1
    line="$(docker inspect -f '{{.State.Status}}, restarts {{.RestartCount}}, CPUs {{.HostConfig.CpusetCpus}}, image {{.Config.Image}}' "$(container "$n")")"
    state="$(github_state "$n" || true)"
    info "$(container "$n"): $line; GitHub: ${state:-not registered} (id, status, busy)"
    [[ "$line" == running* && "$state" == *" online "* ]] || ok=1
  done
  ((seen)) || {
    info "none installed"
    return 1
  }
  step "memory and disk"
  local names=()
  for n in $(existing); do names+=("$(container "$n")"); done
  docker stats --no-stream --format '   {{.Name}}: {{.MemUsage}}, CPU {{.CPUPerc}}' "${names[@]}" 2>/dev/null || true
  n="$(existing | head -n 1)"
  if [[ "$(docker inspect -f '{{.State.Running}}' "$(container "$n")" 2>/dev/null)" == true ]]; then
    docker exec "$(container "$n")" bash -c 'du -sm /ci/cache/* /ci/runner-* 2>/dev/null | sed "s/^/   MB /"' || true
  fi
  for n in $(existing); do
    if [[ "$(docker inspect -f '{{.State.Running}}' "$(container "$n")")" == true ]]; then
      info "$(docker_volume "$n"): $(docker exec "$(container "$n")" du -sm /var/lib/docker 2>/dev/null | cut -f1) MB"
    fi
  done
  return "$ok"
}

cmd_stop() {
  local n
  for n in $(existing); do
    docker stop -t 30 "$(container "$n")" >/dev/null && info "$(container "$n") stopped"
  done
  info "queued CI jobs now wait for ops/ci-runners.sh start (GitHub keeps them for 24 hours)"
}

cmd_start() {
  local n indexes=()
  for n in $(existing); do
    docker start "$(container "$n")" >/dev/null && info "$(container "$n") started"
    indexes+=("$n")
  done
  ((${#indexes[@]} > 0)) || die "no runner installed: ops/ci-runners.sh install"
  wait_online "${indexes[@]}"
}

cmd_prune() {
  local n
  any_busy && die "a job is running on a Mac runner: try again when it is done"
  for n in $(existing); do
    docker exec "$(container "$n")" bash -c 'docker system prune -af --volumes >/dev/null; docker builder prune -af >/dev/null; du -sm /var/lib/docker | cut -f1' |
      sed "s/^/   $(docker_volume "$n"): MB /"
  done
  n="$(existing | head -n 1)"
  [[ -n "$n" ]] || die "no runner installed"
  docker exec "$(container "$n")" bash -c 'rm -rf /ci/cache/* && echo "   /ci/cache emptied"'
}

cmd_uninstall() {
  local n id indexes=()
  while IFS= read -r n; do indexes+=("$n"); done < <(existing)
  # Runners GitHub still lists without a container here (a half finished install) count too.
  while IFS= read -r n; do
    [[ " ${indexes[*]:-} " == *" $n "* ]] || indexes+=("$n")
  done < <(gh api "repos/$REPO/actions/runners" --paginate --jq '.runners[].name' | sed -n 's/^mac-ci-\([0-9][0-9]*\)$/\1/p')
  step "unregister from $REPO"
  for n in ${indexes[@]+"${indexes[@]}"}; do
    docker rm -f "$(container "$n")" >/dev/null 2>&1 || true
    base_opts "$n"
    if docker image inspect "$(image)" >/dev/null 2>&1 &&
      gh api -X POST "repos/$REPO/actions/runners/remove-token" --jq .token |
      docker run --rm -i "${OPTS[@]}" "$(image)" remove >/dev/null 2>&1; then
      :
    else
      # No image or no local registration left: delete GitHub's record by id.
      id="$(github_state "$n" | cut -d' ' -f1)"
      [[ -z "$id" ]] || gh api -X DELETE "repos/$REPO/actions/runners/$id" || die "could not delete runner $id on GitHub"
    fi
    [[ -z "$(github_state "$n")" ]] || die "$(runner_name "$n") is still listed on GitHub"
    docker volume rm "$(docker_volume "$n")" >/dev/null 2>&1 || true
    info "$(runner_name "$n"): unregistered, container and $(docker_volume "$n") removed"
  done
  docker volume rm "$SHARED_VOLUME" >/dev/null 2>&1 || true
  docker image rm "$(image)" >/dev/null 2>&1 || true
  info "volume $SHARED_VOLUME and image $(image) removed"
}

main() {
  local cmd="${1:-}"
  [[ $# -eq 0 ]] || shift
  case "$cmd" in
    install)
      [[ $# -le 1 ]] || die "usage: ops/ci-runners.sh install [count]"
      checks
      cmd_install "$@"
      ;;
    status | stop | start | update | prune | uninstall)
      [[ $# -eq 0 ]] || die "usage: ops/ci-runners.sh $cmd"
      checks
      "cmd_$cmd"
      ;;
    *) die "usage: ops/ci-runners.sh install [count] | status | stop | start | update | prune | uninstall" ;;
  esac
}

main "$@"
