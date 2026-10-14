#!/usr/bin/env bash
# D3 of the documents plan: the renderer service on the Jetson, for real.
#
#   tests/documents_render_jetson.sh            dry: prints every command, runs nothing
#   tests/documents_render_jetson.sh --run      runs it on the Jetson
#   options: --host <ssh host> (jetson), --dir <remote folder> (d3spike-..., a name, never a
#            path), --renders N (50), --keep (no clean up)
#
# What it does, in the Jetson's home, in a folder of its own, with an image tagged
# d3spike-renderer, containers named d3spike-*, and an internal network d3spike-net-*:
# 1. builds the renderer's image from renderer/ of this checkout;
# 2. starts it as compose.yml does (uid 10003, read only root, 64m tmpfs on /tmp, no capability,
#    no new privilege, ipc none, 128 pids, 384m, two CPUs, no environment, the internal network
#    alone), and samples its cgroup's memory every 0.2 s;
# 3. runs tests/documents_render_jetson.py in a container of jobapp-worker:latest (read only,
#    never retagged) with this checkout's render_client.py and fit.py: the command line's build
#    table (file, step, pages, status) for D1's fixtures through the real fit loop, then N renders
#    of the typical CV, the worker side's memory before and after;
# 4. prints the renderer's memory: after the first render, after the N renders, and the peak.
# It never runs docker compose and never touches ~/jobapp; it waits while the Jetson has under
# 2,500 MiB available (once a minute, at most 10 times), then stops.
# Remote commands are built on this side on purpose ($DIR, $NET): SC2029 is expected.
# shellcheck disable=SC2029
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST=jetson
RUN=0
KEEP=0
RENDERS=50
DIR="d3spike-$RANDOM$RANDOM"

while [ $# -gt 0 ]; do
  case "$1" in
    --run) RUN=1 ;;
    --keep) KEEP=1 ;;
    --host) HOST="$2"; shift ;;
    --dir) DIR="$2"; shift ;;
    --renders) RENDERS="$2"; shift ;;
    *) echo "unknown option: $1" >&2; exit 64 ;;
  esac
  shift
done
# The folder is a name in the Jetson's home, never a path: the clean up removes it.
case "$DIR" in */* | *..*) echo "--dir is a folder name: no / and no .." >&2; exit 64 ;; esac
[[ "$DIR" =~ ^d3spike-[A-Za-z0-9_-]+$ ]] || { echo "--dir must match d3spike-[A-Za-z0-9_-]+" >&2; exit 64; }
[[ "$RENDERS" =~ ^[0-9]+$ ]] || { echo "--renders is a number" >&2; exit 64; }
NET="d3spike-net-${DIR#d3spike-}"

# compose.yml's renderer, flag by flag (api/tests/documents/test_compose_renderer.py holds them).
RENDERER="--user 10003:10003 --read-only --tmpfs /tmp:size=64m,mode=1777 --cap-drop ALL --security-opt no-new-privileges:true --ipc none --pids-limit 128 --memory 384m --cpus 2 --network $NET --network-alias renderer"

say() { printf '\n== %s\n' "$*"; }
remote() {
  echo "+ ssh $HOST $*"
  if [ "$RUN" = 1 ]; then ssh "$HOST" "$@"; fi
}

wait_memory() {
  local tries=0 available
  while :; do
    if [ "$RUN" = 0 ]; then echo "+ (check free -m: at least 2500 MiB available)"; return 0; fi
    available="$(ssh "$HOST" "free -m | awk '/^Mem:/{print \$7}'")"
    echo "available: ${available} MiB"
    if [ "$available" -ge 2500 ]; then return 0; fi
    tries=$((tries + 1))
    if [ "$tries" -ge 10 ]; then echo "the Jetson stayed under 2500 MiB available: stopping" >&2; exit 75; fi
    sleep 60
  done
}

cleanup() {
  [ "$RUN" = 1 ] || return 0
  [ "$KEEP" = 1 ] && { echo "kept: $HOST:~/$DIR, d3spike-renderer, $NET"; return 0; }
  say "clean up"
  ssh "$HOST" "docker rm -f d3spike-renderer d3spike-driver >/dev/null 2>&1 || true; docker network rm $NET >/dev/null 2>&1 || true; docker rmi d3spike-renderer >/dev/null 2>&1 || true; rm -rf \"\$HOME/$DIR\"; docker images | grep d3spike || echo 'no d3spike image left'; docker network ls | grep d3spike || echo 'no d3spike network left'; free -m; df -h /"
}
trap cleanup EXIT

say "stage ($DIR)"
STAGE="$(mktemp -d)"
mkdir -p "$STAGE/renderer" "$STAGE/documents" "$STAGE/d3/tests/documents/fixtures"
cp "$HERE/renderer/Dockerfile" "$HERE/renderer/server.py" "$STAGE/renderer/"
cp "$HERE/api/app/documents/render_client.py" "$HERE/api/app/documents/fit.py" "$STAGE/documents/"
cp "$HERE/tests/documents_render_jetson.py" "$STAGE/d3/"
cp "$HERE/api/tests/documents/render_html.py" "$STAGE/d3/tests/documents/"
touch "$STAGE/d3/tests/__init__.py" "$STAGE/d3/tests/documents/__init__.py"
cp -R "$HERE/api/tests/documents/fixtures/content" "$STAGE/d3/tests/documents/fixtures/"
echo "+ rsync $STAGE/ $HOST:~/$DIR/"
if [ "$RUN" = 1 ]; then
  rsync -a "$STAGE/" "$HOST:$DIR/"
  ssh "$HOST" "chmod -R a+rX \"\$HOME/$DIR\""
fi
rm -rf "$STAGE"

say "build the renderer's image"
wait_memory
remote "cd \$HOME/$DIR/renderer && docker build -q -t d3spike-renderer . && docker image inspect -f '{{.Size}}' d3spike-renderer"

say "the renderer as compose.yml starts it, on an internal network of its own"
remote "docker network create --internal $NET >/dev/null && docker run -d --rm --name d3spike-renderer $RENDERER d3spike-renderer >/dev/null && for i in \$(seq 1 60); do docker exec d3spike-renderer python -I /renderer/server.py --check && break; sleep 1; done && docker inspect -f '{{.Config.User}} ro={{.HostConfig.ReadonlyRootfs}} pids={{.HostConfig.PidsLimit}} mem={{.HostConfig.Memory}} ipc={{.HostConfig.IpcMode}} caps={{.HostConfig.CapDrop}} env={{len .Config.Env}}' d3spike-renderer"

say "memory sampler, the build table, $RENDERS renders"
wait_memory
remote "cg=\$(find /sys/fs/cgroup -maxdepth 3 -type d -name \"*\$(docker inspect -f '{{.Id}}' d3spike-renderer)*\" | head -n 1); echo \"cgroup: \$cg\"; echo \"renderer at rest: \$((\$(cat \$cg/memory.current) / 1048576)) MiB\"; (while [ -e \$cg/memory.current ]; do cat \$cg/memory.current; sleep 0.2; done > \$HOME/$DIR/samples.txt 2>/dev/null </dev/null &) ; docker run --rm --name d3spike-driver --network $NET --user 10001:10001 --read-only --cap-drop ALL --security-opt no-new-privileges:true --memory 512m --cpus 2 -v \$HOME/$DIR/documents:/app/app/documents:ro -v \$HOME/$DIR/d3:/d3:ro jobapp-worker:latest python -I /d3/documents_render_jetson.py $RENDERS; echo \"renderer after: \$((\$(cat \$cg/memory.current) / 1048576)) MiB\"; sort -n \$HOME/$DIR/samples.txt | tail -n 1 | awk '{printf \"renderer peak (0.2 s samples): %d MiB\\n\", \$1 / 1048576}'; [ -e \$cg/memory.peak ] && echo \"renderer memory.peak: \$((\$(cat \$cg/memory.peak) / 1048576)) MiB\"; grep -E '^(anon|file) ' \$cg/memory.stat; docker exec d3spike-renderer sh -c 'ls /proc | grep -c \"^[0-9]\"' | sed 's/^/processes in the container: /'; docker logs d3spike-renderer 2>&1 | grep -c '\"outcome\": \"ok\"' | sed 's/^/renders answered ok: /'"
