#!/usr/bin/env bash
# The CI runners on the Mac (issue #83; README, Continuous delivery). Run on the Mac, no sudo:
#
#   ops/ci-runners.sh install [count]   build the image, register and start `count` runners
#                                       (default: as many as exist, 3 on a Mac that has none;
#                                       3 is the most, see the budget below); safe to run again
#   ops/ci-runners.sh status            containers, GitHub's view, memory, disk used
#   ops/ci-runners.sh stop | start      stop or start every runner container (registration kept)
#   ops/ci-runners.sh update            rebuild the image from ops/ci-runner and recreate the
#                                       containers that do not run it yet, one at a time, each
#                                       when its runner is idle (it waits for a running job, up
#                                       to CI_UPDATE_WAIT_S, an hour) and until its listener
#                                       says "Listening for Jobs"; registrations, caches and
#                                       images are kept, a runner stopped with `stop` stays
#                                       stopped. Safe to run again and while jobs queue
#   ops/ci-runners.sh prune             empty the shared tool caches and every runner's Docker
#                                       daemon (refused while a job runs); remove what a trial
#                                       left behind
#   ops/ci-runners.sh try [command...]  a container shaped like a runner but registered nowhere,
#                                       to run a CI step by hand (bash when no command is given);
#                                       its two scratch volumes are removed when it ends
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
#   trials      the container jobapp-ci-try and the volumes jobapp-ci-try, jobapp-ci-try-docker,
#               only while `try` runs; status shows any that were left, prune and uninstall
#               remove them
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
# 3(n-1) .. 3n-1) and limited to 4 GB, swap included. Three runners, one per job of a CI run
# (issue #91): CPUs 0-8 and 12 GB at most. Measured on 2026-10-10, a whole run used 4 to 6
# cores' worth and about 7 GB: the api tests fill their 3 CPUs, an e2e group about 0.6 of them.
#
# Settings (environment): CI_RUNNER_REPO (GoldLegendW80/jobapp), CI_RUNNER_MEMORY (4g, per
# runner), CI_RUNNER_CPUS (CPUs per runner, 3), CI_RUNNER_MIN_FREE_GB (free space of the Mac's
# disk below which install refuses; default 4 GB per runner plus 6), CI_LISTEN_WAIT_S (how long
# a started container may take to say "Listening for Jobs", 300: a listener that had to wait
# out the session of the container it replaced took 161 s on 2026-10-10).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONTEXT="$ROOT/ops/ci-runner"
REPO="${CI_RUNNER_REPO:-GoldLegendW80/jobapp}"
MEMORY="${CI_RUNNER_MEMORY:-4g}"
CPUS="${CI_RUNNER_CPUS:-3}"
LABEL="jobapp.ci=runner"
SHARED_VOLUME="jobapp-ci"
PROBE_DIR="${CI_RUNNER_DIR:-$HOME/actions-runners/jobapp-ci}/disk"
DEFAULT_COUNT=3
MAX_COUNT=3
TRY_CONTAINER="jobapp-ci-try"
TRY_VOLUMES=(jobapp-ci-try jobapp-ci-try-docker)

die() {
  printf 'ci-runners: %s\n' "$*" >&2
  exit 1
}
step() { printf '\n== %s\n' "$*"; }
info() { printf '   %s\n' "$*"; }

runner_version() { awk '/^ARG RUNNER_VERSION=/ { sub(/^ARG RUNNER_VERSION=/, ""); print; exit }' "$CONTEXT/Dockerfile"; }
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
disk_check() { # disk_check <GB needed when CI_RUNNER_MIN_FREE_GB is not set>
  local need="${CI_RUNNER_MIN_FREE_GB:-$1}" free
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

# The lowest of them, nothing when there is none.
first_existing() {
  local all
  all="$(existing)"
  printf '%s\n' "${all%%$'\n'*}"
}

# github_state <n>: "<id> <status> <busy>" of runner n on GitHub, nothing when GitHub does not
# list it, and status 1 when GitHub did not answer. A failed call is "unknown": no caller may
# read it as "idle", "not registered" or "gone" (each handles the status).
github_state() {
  local out
  out="$(gh api "repos/$REPO/actions/runners" --paginate \
    --jq ".runners[] | select(.name == \"$(runner_name "$1")\") | \"\(.id) \(.status) \(.busy)\"")" || return 1
  printf '%s\n' "${out%%$'\n'*}"
}

# known_state <n>: github_state, or stop: nothing is changed on a runner whose state is unknown.
known_state() {
  github_state "$1" || die "GitHub did not answer about $(runner_name "$1") (gh api failed): its state is unknown, nothing was changed on it. Run the command again"
}

any_busy() {
  local names
  names="$(gh api "repos/$REPO/actions/runners" --paginate \
    --jq '.runners[] | select((.name | startswith("mac-ci-")) and .busy) | .name')" ||
    die "GitHub did not answer (gh api failed): whether a job is running is unknown, nothing was changed"
  [[ -n "$names" ]]
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
  info "built from ops/ci-runner (linux/arm64, actions runner $(runner_version))"
}

# The builds this one replaced, untagged once no container runs them: only dangling images that
# carry this label. Called after the last container is recreated, when the old image is free.
prune_replaced_images() {
  docker image prune -f --filter "label=$LABEL" >/dev/null 2>&1 || true
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
# Only the answer "<id> <status> false" is idle. No answer stops the script (known_state), and a
# runner GitHub does not list is not recreated either: install registers it again.
wait_idle() {
  local n="$1" limit="${CI_UPDATE_WAIT_S:-3600}" told=0 deadline state
  deadline=$((SECONDS + limit))
  while :; do
    state="$(known_state "$n")" || exit 1
    case "$state" in
      *" false") return 0 ;;
      *" true") ;;
      *) die "$(runner_name "$n") is not registered at GitHub any more (it drops a runner that stayed offline for 14 days): ops/ci-runners.sh install registers it again" ;;
    esac
    ((SECONDS < deadline)) || die "$(runner_name "$n") is still running a job after ${limit}s: nothing was changed on it, run the command again later"
    ((told)) || info "$(runner_name "$n") is running a job: waiting for it to end before recreating $(container "$n")"
    told=1
    sleep 5
  done
}

# wait_listening <n>: until the listener in the container just started says it takes jobs.
# GitHub shows a runner "online" before that, and while the listener of the container it
# replaces still held its session ("A session for this runner already exists") a run queued in
# between was never assigned (2026-10-10, run 38071312967). So nothing returns before this line.
wait_listening() {
  local n="$1" limit="${CI_LISTEN_WAIT_S:-300}" deadline
  deadline=$((SECONDS + limit))
  until listening "$n"; do
    ((SECONDS < deadline)) || die "$(container "$n") did not say \"Listening for Jobs\" within ${limit}s: docker logs $(container "$n")"
    sleep 3
  done
  info "$(container "$n"): listening for jobs"
}

# listening <n>: the log of the container since its last start holds the listener's line.
# grep reads the log to its end, never `grep -q`: that one leaves at the first match, docker is
# then killed by SIGPIPE while it writes the rest, and under pipefail the pipeline says 141, "not
# listening", for every runner whose log goes on after the line, which is every runner that has
# run a job (measured on both live runners on 2026-10-10, 6 tries of 6). The same holds for
# every pipe in this script: its last command reads all it is given (`| head` is not used).
listening() {
  local started
  started="$(docker inspect -f '{{.State.StartedAt}}' "$(container "$1")" 2>/dev/null)" || return 1
  docker logs --since "$started" "$(container "$1")" 2>&1 | grep 'Listening for Jobs' >/dev/null
}

# refresh <n> <install|update>: leave a container that already runs the built image; recreate any
# other when its runner is idle and wait until it listens. On update a container that is stopped
# (ops/ci-runners.sh stop) gets the new image and stays stopped.
refresh() {
  local n="$1" mode="$2" running
  if current "$n"; then
    info "$(container "$n"): already running $(image), left as it is"
    return 0
  fi
  running="$(docker inspect -f '{{.State.Running}}' "$(container "$n")" 2>/dev/null || echo none)"
  if [[ "$mode" == update && "$running" == false ]]; then
    create_container "$n"
    info "$(container "$n"): was stopped and stays stopped, on the new image (ops/ci-runners.sh start)"
    return 0
  fi
  [[ "$running" == none ]] || wait_idle "$n"
  create_container "$n"
  docker start "$(container "$n")" >/dev/null || die "$(container "$n") did not start"
  wait_listening "$n"
}

is_registered() { # the local registration file and GitHub's record both exist
  local n="$1" state
  docker run --rm --entrypoint test "${OPTS[@]}" "$(image)" -f "/ci/runner-$n/.runner" 2>/dev/null ||
    return 1
  state="$(known_state "$n")" || exit 1
  [[ -n "$state" ]]
}

register() {
  local n="$1"
  base_opts "$n"
  if is_registered "$n"; then
    info "$(runner_name "$n"): already registered"
    return 0
  fi
  remove_container "$n"
  # The token: from gh into the container's stdin, nowhere else. configure first clears a
  # registration GitHub no longer knows (it drops a runner that stayed offline for 14 days).
  gh api -X POST "repos/$REPO/actions/runners/registration-token" --jq .token |
    docker run --rm -i "${OPTS[@]}" "$(image)" configure >/dev/null ||
    die "registration of $(runner_name "$n") failed: gh needs admin rights on $REPO to create the token, or config.sh refused (run it again to see: docker logs shows nothing for a removed container)"
  info "$(runner_name "$n"): registered"
}

# remove_container <n>: stop first, so the listener ends its session at GitHub itself (the
# entrypoint passes the signal on); a container killed outright leaves a session behind that the
# next listener has to wait out.
remove_container() {
  docker stop -t 30 "$(container "$1")" >/dev/null 2>&1 || true
  docker rm -f "$(container "$1")" >/dev/null 2>&1 || true
}

# create_container <n>: the container of runner n on the built image, not started.
create_container() {
  local n="$1" total per first last
  base_opts "$n"
  total="$(docker info --format '{{.NCPU}}')"
  per="$CPUS"
  first=$(((n - 1) * per))
  last=$((first + per - 1))
  ((last < total)) || die "runner $n would need CPUs $first-$last and Docker has $total: fewer runners or CI_RUNNER_CPUS"
  remove_container "$n"
  docker create --name "$(container "$n")" --hostname "$(runner_name "$n")" \
    --restart unless-stopped --privileged \
    --cpuset-cpus "$first-$last" --memory "$MEMORY" --memory-swap "$MEMORY" \
    --log-opt max-size=10m --log-opt max-file=3 \
    -v "$(docker_volume "$n"):/var/lib/docker" \
    -v "$PROBE_DIR:/host-disk:ro" \
    "${OPTS[@]}" "$(image)" run >/dev/null || die "$(container "$n") could not be created"
  info "$(container "$n"): CPUs $first-$last, memory $MEMORY"
}

wait_online() {
  local n deadline=$((SECONDS + 120)) pending
  while :; do
    pending=""
    for n in "$@"; do
      # A call GitHub did not answer counts as not online yet.
      [[ "$(github_state "$n" || true)" == *" online "* ]] || pending+=" $(runner_name "$n")"
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
  disk_check "$((count * 4 + 6))"
  build_image
  docker volume create --label "$LABEL" "$SHARED_VOLUME" >/dev/null
  mkdir -p "$PROBE_DIR"
  step "registration on $REPO (labels self-hosted, Linux, ARM64, mac-ci)"
  for n in $(seq 1 "$count"); do
    docker volume create --label "$LABEL" "$(docker_volume "$n")" >/dev/null
    register "$n"
    indexes+=("$n")
  done
  step "containers (restart unless-stopped), one at a time"
  for n in "${indexes[@]}"; do refresh "$n" install; done
  wait_online "${indexes[@]}"
  prune_replaced_images
  info "online on GitHub and listening: $count runner(s)"
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
  # The build needs about 2 GB the first time; under 10 GB free no job starts anyway.
  disk_check 10
  build_image
  step "containers on $(image), one at a time, each when its runner is idle"
  local started=()
  for n in "${indexes[@]}"; do
    refresh "$n" update
    if [[ "$(docker inspect -f '{{.State.Running}}' "$(container "$n")" 2>/dev/null)" == true ]]; then
      wait_online "$n"
      started+=("$n")
    fi
  done
  prune_replaced_images
  info "online on GitHub and listening: ${#started[@]} of $count runner(s)"
}

cmd_status() {
  local n state line heard ok=0 seen=0
  step "runners on $REPO"
  for n in $(existing); do
    seen=1
    line="$(docker inspect -f '{{.State.Status}}, restarts {{.RestartCount}}, CPUs {{.HostConfig.CpusetCpus}}, image {{.Config.Image}}' "$(container "$n")")"
    if state="$(github_state "$n")"; then
      state="${state:-not registered}"
    else
      state="unknown, GitHub did not answer"
    fi
    heard="not listening for jobs"
    if listening "$n"; then heard="listening for jobs"; fi
    info "$(container "$n"): $line, $heard; GitHub: $state (id, status, busy)"
    [[ "$line" == running* && "$state" == *" online "* && "$heard" == listening* ]] || ok=1
  done
  local left
  left="$(try_leftovers)"
  if [[ -n "$left" ]]; then
    step "left by a trial (ops/ci-runners.sh prune removes what is not running)"
    while IFS= read -r line; do info "$line"; done <<<"$left"
  fi
  ((seen)) || {
    info "none installed"
    return 1
  }
  autostart_note
  step "memory and disk (the Mac has $(df -Pk "$HOME" | awk 'NR==2 {print int($4 / 1048576)}') GB free; a job is refused under 10)"
  local names=()
  for n in $(existing); do names+=("$(container "$n")"); done
  docker stats --no-stream --format '   {{.Name}}: {{.MemUsage}}, CPU {{.CPUPerc}}' "${names[@]}" 2>/dev/null || true
  n="$(first_existing)"
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
  for n in "${indexes[@]}"; do wait_listening "$n"; done
  wait_online "${indexes[@]}"
}

# --- trials ---------------------------------------------------------------------------------
# A trial is a container of the runner image with a Docker daemon of its own and two scratch
# volumes, registered nowhere. `try` removes the container and the volumes when its command ends
# and when the script is stopped (Ctrl C, a closed terminal, a kill). A kill -9, a crash of the
# Mac or a trial started another way (an older README gave the docker command) leaves them
# behind, several GB that belong to nothing: status names them, prune and uninstall remove them.
# With three runners busy a trial is a fourth consumer: it takes Docker's last 3 CPUs and brings
# CI to 16 of about 18 GB at the limits, the share the budget above leaves to the owner.

# The trial container and volumes that exist, one per line: "container <name>" or
# "volume <name>". Exactly the three names of the header (TRY_CONTAINER, TRY_VOLUMES): Docker's
# name filter matches any name that holds the text, so the listing is cut down to those three
# here, and a volume that only looks alike (jobapp-ci-try-docker2) is nobody's to remove.
try_leftovers() {
  docker ps -a --filter "name=$TRY_CONTAINER" --format '{{.Names}}' | sed -n "s/^${TRY_CONTAINER}\$/container &/p"
  docker volume ls -q --filter "name=$TRY_CONTAINER" |
    sed -n -e "s/^${TRY_VOLUMES[0]}\$/volume &/p" -e "s/^${TRY_VOLUMES[1]}\$/volume &/p"
}

# Remove what a trial left: its container unless it is running, its volumes unless a container
# still uses them.
remove_try_leftovers() {
  local kind name
  while read -r kind name; do
    [[ -n "$name" ]] || continue
    if [[ "$kind" == container ]]; then
      if [[ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" == true ]]; then
        info "trial container $name is running: left (docker rm -f $name ends it)"
      else
        docker rm -v "$name" >/dev/null 2>&1 && info "trial container $name removed"
      fi
    elif docker volume rm "$name" >/dev/null 2>&1; then
      info "trial volume $name removed"
    else
      info "trial volume $name is still used by a container: left"
    fi
  done < <(try_leftovers)
}

# The end of a trial, however it ends: its container (still running when the script was stopped
# and not its command), then its volumes.
try_cleanup() {
  docker rm -f -v "$TRY_CONTAINER" >/dev/null 2>&1 || true
  remove_try_leftovers
}

cmd_try() {
  local total first status=0 opts=()
  docker image inspect "$(image)" >/dev/null 2>&1 || die "no image $(image) yet: ops/ci-runners.sh install builds it"
  [[ -z "$(try_leftovers | sed -n 's/^container //p')" ]] ||
    die "a trial container exists already: end it (docker rm -f $TRY_CONTAINER) or run ops/ci-runners.sh prune"
  # A trial pulls and builds what a job does, a few GB: not on a disk where no job would start.
  disk_check 10
  mkdir -p "$PROBE_DIR"
  # The last CPUs of Docker, away from the runners' sets when there is room.
  total="$(docker info --format '{{.NCPU}}')"
  first=$((total - CPUS))
  ((first >= 0)) || first=0
  info "a trial container shaped like a runner (CPUs $first-$((total - 1)), memory $MEMORY), registered nowhere;"
  info "it and its volumes ${TRY_VOLUMES[*]} are removed when the command ends"
  opts=(--rm --name "$TRY_CONTAINER" --label jobapp.ci=try --privileged
    --cpuset-cpus "$first-$((total - 1))" --memory "$MEMORY" --memory-swap "$MEMORY"
    -e CI_RUNNER_INDEX=99 -v "${TRY_VOLUMES[0]}:/ci" -v "${TRY_VOLUMES[1]}:/var/lib/docker"
    -v "$PROBE_DIR:/host-disk:ro" "$(image)" exec "${@:-bash}")
  # From here on the script cleans up when it ends for any reason it can notice: the command's
  # end, Ctrl C, a closed terminal, a kill (a kill -9 leaves the leftovers that status names
  # and prune removes).
  trap try_cleanup EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
  if [[ -t 0 && -t 1 ]]; then
    # At a terminal the container needs it, so docker stays in the foreground: bash acts on a
    # signal once docker has returned, and Ctrl C goes to the container's command.
    docker run -it "${opts[@]}" || status=$?
  else
    # Without a terminal docker runs beside the script, which waits for it: a signal then acts
    # at once, where a script that waits in the foreground would first let the command finish.
    docker run "${opts[@]}" &
    wait "$!" || status=$?
  fi
  return "$status"
}

cmd_prune() {
  local n
  remove_try_leftovers
  any_busy && die "a job is running on a Mac runner: try again when it is done"
  for n in $(existing); do
    docker exec "$(container "$n")" bash -c 'docker system prune -af --volumes >/dev/null; docker builder prune -af >/dev/null; du -sm /var/lib/docker | cut -f1' |
      sed "s/^/   $(docker_volume "$n"): MB /"
  done
  n="$(first_existing)"
  [[ -n "$n" ]] || die "no runner installed"
  docker exec "$(container "$n")" bash -c 'rm -rf /ci/cache/* && echo "   /ci/cache emptied"'
}

# half_way <n>: GitHub stopped answering after the container of runner n was removed. Says what
# was done and what was not, which "nothing was changed" no longer is.
half_way() {
  die "GitHub stopped answering while $(runner_name "$1") was being unregistered (gh api failed): its container is removed, its registration at GitHub may still be there, its volume $(docker_volume "$1") and everything of the runners after it are untouched. Run ops/ci-runners.sh uninstall again"
}

cmd_uninstall() {
  local n id listed indexes=()
  while IFS= read -r n; do indexes+=("$n"); done < <(existing)
  # Runners GitHub still lists without a container here (a half finished install) count too.
  # GitHub is asked before anything is touched, once for the list and once for each runner:
  # when it does not answer, the command stops with every container still there.
  listed="$(gh api "repos/$REPO/actions/runners" --paginate --jq '.runners[].name')" ||
    die "GitHub did not answer (gh api failed): which runners are registered is unknown, nothing was changed. Run the command again"
  while IFS= read -r n; do
    [[ -z "$n" || " ${indexes[*]:-} " == *" $n "* ]] || indexes+=("$n")
  done < <(printf '%s\n' "$listed" | sed -n 's/^mac-ci-\([0-9][0-9]*\)$/\1/p')
  for n in ${indexes[@]+"${indexes[@]}"}; do known_state "$n" >/dev/null; done
  step "unregister from $REPO"
  for n in ${indexes[@]+"${indexes[@]}"}; do
    remove_container "$n"
    base_opts "$n"
    if docker image inspect "$(image)" >/dev/null 2>&1 &&
      gh api -X POST "repos/$REPO/actions/runners/remove-token" --jq .token |
      docker run --rm -i "${OPTS[@]}" "$(image)" remove >/dev/null 2>&1; then
      :
    else
      # No image or no local registration left: delete GitHub's record by id.
      id="$(github_state "$n")" || half_way "$n"
      id="${id%% *}"
      [[ -z "$id" ]] || gh api -X DELETE "repos/$REPO/actions/runners/$id" || die "could not delete runner $id on GitHub (its container is removed, its volume kept)"
    fi
    id="$(github_state "$n")" || half_way "$n"
    [[ -z "$id" ]] || die "$(runner_name "$n") is still listed on GitHub (its container is removed, its volume kept)"
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
  remove_try_leftovers
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
    try)
      checks
      cmd_try "$@"
      ;;
    *) die "usage: ops/ci-runners.sh install [count] | status | stop | start | update | prune | try [command...] | uninstall" ;;
  esac
}

main "$@"
