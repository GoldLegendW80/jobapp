#!/usr/bin/env bash
# The CI runners on the Mac (issue #83; README, Continuous delivery). Run on the Mac, no sudo:
#
#   ops/ci-runners.sh install [count]   build the image, register and start `count` runners
#                                       (default 2, or as many as exist; 3 at most on this Mac,
#                                       see the budget below); safe to run again
#   ops/ci-runners.sh status            containers, GitHub's view, memory, disk used
#   ops/ci-runners.sh stop | start      stop or start every runner container (registration kept)
#   ops/ci-runners.sh update            rebuild the image from ops/ci-runner and recreate the
#                                       containers that do not run it yet, one at a time, each
#                                       when its runner is idle (it waits for a running job, up
#                                       to CI_UPDATE_WAIT_S, an hour); registrations, caches and
#                                       images are kept. Safe to run again and while jobs queue
#   ops/ci-runners.sh prune             empty the shared tool caches and every runner's Docker
#                                       daemon (refused while a job runs)
#   ops/ci-runners.sh uninstall         unregister every runner, remove containers, volumes, image
#
# What it creates, all in Docker Desktop but one empty folder, and nothing in the repository:
#   image       jobapp-ci-runner:<runner version>      (ops/ci-runner/Dockerfile)
#   containers  jobapp-ci-1 .. jobapp-ci-<count>       restart unless-stopped, --privileged (they
#                                                      run a Docker daemon of their own), a CPU set
#                                                      and a memory limit each
#   volumes     jobapp-ci                              runner directories, work folders, caches
#               jobapp-ci-docker-1 .. -<count>         /var/lib/docker of each instance
#   GitHub      runners mac-ci-1 .. mac-ci-<count>, labels self-hosted, Linux, ARM64, mac-ci
#   folder      ~/actions-runners/jobapp-ci/disk       empty, mounted read only at /host-disk so
#                                                      the job-started hook can read the Mac's
#                                                      free disk space (CI_RUNNER_DIR moves it)
# Everything carries the label jobapp.ci=runner. Only objects with these names are ever removed.
#
# The registration and removal tokens go from `gh api` straight into the container's stdin
# through a pipe: never in a variable, an argument, a file or the output. The containers get no
# variable of the calling shell (docker run passes only the -e options written below), so no
# token or key of the Mac's environment reaches a job.
#
# Budget. The Mac is shared with the owner's own work and with agents that run scratch stacks
# and test suites in the same Docker Desktop (12 CPUs, about 18 GB). On 2026-10-10 three runner
# trials on top of four agents took the load average to 82 and the Mac went down. So CI gets a
# fixed share and no more: each runner is pinned to 3 CPUs of its own (runner n to CPUs
# 3(n-1) .. 3n-1) and limited to 4 GB, swap included. Two runners: CPUs 0-5 and 8 GB at most,
# half of Docker's CPUs and under half the memory.
#
# Settings (environment): CI_RUNNER_REPO (GoldLegendW80/jobapp), CI_RUNNER_MEMORY (4g, per
# runner), CI_RUNNER_CPUS (CPUs per runner, 3), CI_RUNNER_MIN_FREE_GB (free space of the Mac's
# disk below which install refuses; default 4 GB per runner plus 6).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONTEXT="$ROOT/ops/ci-runner"
REPO="${CI_RUNNER_REPO:-GoldLegendW80/jobapp}"
MEMORY="${CI_RUNNER_MEMORY:-4g}"
CPUS="${CI_RUNNER_CPUS:-3}"
LABEL="jobapp.ci=runner"
SHARED_VOLUME="jobapp-ci"
PROBE_DIR="${CI_RUNNER_DIR:-$HOME/actions-runners/jobapp-ci}/disk"
DEFAULT_COUNT=2
MAX_COUNT=3

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

# Docker Desktop keeps everything in one disk image in the home folder, which grows with what the
# runners pull, build and cache and takes the Mac's free space: at most 4 GB of Docker data per
# runner (CI_DOCKER_MAX_MB in ci-clean.sh) and a few GB of image and shared caches. On
# 2026-10-10 a trial of three e2e groups at once filled a disk that had 22 GB free, and every
# container of the Mac got input/output errors. So: no install on a Mac that is short of room.
disk_check() { # disk_check <count>
  local need="${CI_RUNNER_MIN_FREE_GB:-$(($1 * 4 + 6))}" free
  [[ "$need" =~ ^[0-9]+$ ]] || die "CI_RUNNER_MIN_FREE_GB must be a number"
  free="$(df -Pk "$HOME" | awk 'NR==2 {print int($4 / 1048576)}')"
  [[ "$free" =~ ^[0-9]+$ ]] || die "could not read the free space of $HOME"
  ((free >= need)) || die "only ${free} GB free on this Mac: the runners need room for images and caches (at least ${need} GB free, CI_RUNNER_MIN_FREE_GB). Free some space first, for instance Docker's unused volumes and images"
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
  step "image $(image) (a first build takes a few minutes, a rebuild seconds)"
  docker build --label "$LABEL" -t "$(image)" "$CONTEXT" || die "docker build failed"
  # The build it replaces, now untagged: only dangling images that carry this label.
  docker image prune -f --filter "label=$LABEL" >/dev/null 2>&1 || true
  info "built from ops/ci-runner (linux/arm64, actions runner $(runner_version))"
}

# current <n>: the container of runner n is running the image just built.
current() {
  local have want
  have="$(docker inspect -f '{{.State.Running}} {{.Image}}' "$(container "$1")" 2>/dev/null || true)"
  want="$(docker image inspect -f '{{.Id}}' "$(image)" 2>/dev/null || true)"
  [[ -n "$want" && "$have" == "true $want" ]]
}

# wait_idle <n>: until GitHub says runner n runs no job. A container is only ever recreated on
# an idle runner; a job that GitHub hands over in the very second of the recreation is lost and
# shows as failed (gh run rerun <id> --failed).
wait_idle() {
  local n="$1" limit="${CI_UPDATE_WAIT_S:-3600}" told=0 deadline
  deadline=$((SECONDS + limit))
  while [[ "$(github_state "$n")" == *" true" ]]; do
    ((SECONDS < deadline)) || die "$(runner_name "$n") is still running a job after ${limit}s: nothing was changed on it, run the command again later"
    ((told)) || info "$(runner_name "$n") is running a job: waiting for it to end before recreating $(container "$n")"
    told=1
    sleep 5
  done
}

# refresh <n>: leave a container that already runs the built image, recreate any other when idle.
refresh() {
  local n="$1"
  if current "$n"; then
    info "$(container "$n"): already running $(image), left as it is"
    return 0
  fi
  wait_idle "$n"
  start_container "$n"
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
  # The token: from gh into the container's stdin, nowhere else. configure first clears a
  # registration GitHub no longer knows (it drops a runner that stayed offline for 14 days).
  gh api -X POST "repos/$REPO/actions/runners/registration-token" --jq .token |
    docker run --rm -i "${OPTS[@]}" "$(image)" configure >/dev/null ||
    die "registration of $(runner_name "$n") failed: gh needs admin rights on $REPO to create the token, or config.sh refused (run it again to see: docker logs shows nothing for a removed container)"
  info "$(runner_name "$n"): registered"
}

start_container() {
  local n="$1" total per first last
  base_opts "$n"
  total="$(docker info --format '{{.NCPU}}')"
  per="$CPUS"
  first=$(((n - 1) * per))
  last=$((first + per - 1))
  ((last < total)) || die "runner $n would need CPUs $first-$last and Docker has $total: fewer runners or CI_RUNNER_CPUS"
  docker rm -f "$(container "$n")" >/dev/null 2>&1 || true
  docker run -d --name "$(container "$n")" --hostname "$(runner_name "$n")" \
    --restart unless-stopped --privileged \
    --cpuset-cpus "$first-$last" --memory "$MEMORY" --memory-swap "$MEMORY" \
    --log-opt max-size=10m --log-opt max-file=3 \
    -v "$(docker_volume "$n"):/var/lib/docker" \
    -v "$PROBE_DIR:/host-disk:ro" \
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
  [[ "$count" =~ ^[1-9][0-9]?$ ]] || die "count must be a number from 1 to $MAX_COUNT"
  ((count <= MAX_COUNT)) || die "at most $MAX_COUNT runners on this Mac (the budget in the header of this script), asked for $count"
  [[ "$CPUS" =~ ^[1-9][0-9]?$ ]] || die "CI_RUNNER_CPUS must be a number"
  local total
  total="$(docker info --format '{{.NCPU}}')"
  ((count * CPUS <= total)) ||
    die "runner $count would need CPUs $(((count - 1) * CPUS))-$((count * CPUS - 1)) and Docker has $total: fewer runners or CI_RUNNER_CPUS"
  local highest
  highest="$(existing | tail -n 1)"
  [[ -z "$highest" ]] || ((count >= highest)) ||
    die "$highest runners exist and $count were asked for: ops/ci-runners.sh uninstall first, then install $count"
  disk_check "$count"
  build_image
  docker volume create --label "$LABEL" "$SHARED_VOLUME" >/dev/null
  mkdir -p "$PROBE_DIR"
  step "registration on $REPO (labels self-hosted, Linux, ARM64, mac-ci)"
  for n in $(seq 1 "$count"); do
    docker volume create --label "$LABEL" "$(docker_volume "$n")" >/dev/null
    register "$n"
    indexes+=("$n")
  done
  step "containers (restart unless-stopped)"
  for n in "${indexes[@]}"; do refresh "$n"; done
  wait_online "${indexes[@]}"
  info "online on GitHub: $count runner(s)"
  autostart_note
}

# The runner containers restart with the Docker daemon (restart unless-stopped). Whether the
# daemon itself starts when the owner logs in is Docker Desktop's setting, read here, never
# written.
autostart_note() {
  local settings="$HOME/Library/Group Containers/group.com.docker/settings-store.json"
  if [[ -f "$settings" ]] && grep -Eq '"AutoStart"[[:space:]]*:[[:space:]]*true' "$settings"; then
    info "Docker Desktop starts at login: the runners come back by themselves after a reboot"
  else
    info "WARNING: Docker Desktop does not start at login, so after a reboot the runners stay off"
    info "until it is opened. Docker Desktop, Settings, General: tick"
    info "\"Start Docker Desktop when you sign in to your computer\" (nothing was changed for you)"
  fi
}

cmd_update() {
  local n count indexes=()
  mkdir -p "$PROBE_DIR"
  while IFS= read -r n; do indexes+=("$n"); done < <(existing)
  count="${#indexes[@]}"
  ((count > 0)) || die "no runner installed: ops/ci-runners.sh install"
  build_image
  step "containers on $(image), one at a time, each when its runner is idle"
  for n in "${indexes[@]}"; do
    refresh "$n"
    wait_online "$n"
  done
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
  autostart_note
  step "memory and disk (the Mac has $(df -Pk "$HOME" | awk 'NR==2 {print int($4 / 1048576)}') GB free; a job is refused under 10)"
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
  # Every tag of the runner image, an older pinned version included.
  local tag left=""
  for tag in $(docker image ls --format '{{.Repository}}:{{.Tag}}' jobapp-ci-runner 2>/dev/null); do
    docker image rm "$tag" >/dev/null 2>&1 || left+=" image:$tag"
  done
  rmdir "$PROBE_DIR" "$(dirname "$PROBE_DIR")" 2>/dev/null || true
  rmdir "$(dirname "$(dirname "$PROBE_DIR")")" 2>/dev/null || true # ~/actions-runners, if empty
  for n in "$SHARED_VOLUME" $(docker volume ls -q --filter name=jobapp-ci-docker- 2>/dev/null); do
    if docker volume inspect "$n" >/dev/null 2>&1; then left+=" volume:$n"; fi
  done
  if [[ -n "$left" ]]; then
    info "WARNING: could not remove:$left (in use? docker ps -a, then remove by hand)"
  else
    info "volume $SHARED_VOLUME, the runner images and the empty folder $PROBE_DIR removed"
  fi
  info "not touched: the volumes jobapp-ci-try and jobapp-ci-try-docker of a step tried by hand (README)"
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
