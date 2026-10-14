#!/usr/bin/env bash
# D1 of the documents plan: the renderer, measured on the Jetson (WeasyPrint against Chromium).
#
#   tests/documents_render_jetson.sh            dry: prints every command, runs nothing
#   tests/documents_render_jetson.sh --run      runs the measurement on the Jetson
#   options: --host <ssh host> (jetson), --dir <remote folder name>, --keep (no clean up)
#
# What it does, in the Jetson's home, in a folder of its own (d1-spike-<random>), with images
# tagged d1spike-* and containers named d1spike-*, each --rm with --memory and --cpus limits:
# 1. builds d1spike-weasy FROM jobapp-worker:latest (WeasyPrint added; the sandbox's main.py of
#    this checkout with one HANDLERS line for render_pdf) and d1spike-chromium (Playwright's
#    headless shell on the worker's Python base);
# 2. turns the fixtures of api/tests/documents/fixtures/content/ into HTML (make_html.py);
# 3. WeasyPrint: every render a child of the parser sandbox (main.py: rlimits, seccomp filter),
#    in a container with the parser service's settings of compose.yml;
# 4. Chromium: launched with its own sandbox under the same settings, then with what it needs;
# 5. both again, quickly, while two busy cores and 300 MB stand in for an agent run;
# 6. page 1 side by side, the CSS probes, the font check: tests/../tasks/evidence/documents-d1/.
# It never runs docker compose, never touches ~/jobapp, and waits while the Jetson has under
# 2,500 MiB available (once a minute, at most 10 times), then stops.
# Remote commands are built on this side on purpose ($DIR, $PARSER): SC2029 is expected.
# shellcheck disable=SC2029
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPIKE="$HERE/ops/spikes/documents-render"
FIXTURES="$HERE/api/tests/documents/fixtures/content"
EVIDENCE="$HERE/tasks/evidence/documents-d1"
HOST=jetson
RUN=0
KEEP=0
DIR="d1-spike-$RANDOM$RANDOM"

while [ $# -gt 0 ]; do
  case "$1" in
    --run) RUN=1 ;;
    --keep) KEEP=1 ;;
    --host) HOST="$2"; shift ;;
    --dir) DIR="$2"; shift ;;
    *) echo "unknown option: $1" >&2; exit 64 ;;
  esac
  shift
done
case "$DIR" in d1-spike-*) ;; *) echo "--dir must start with d1-spike-" >&2; exit 64 ;; esac

# The parser service's settings (compose.yml), with no network for the internal one.
PARSER="--user 10002:10002 --read-only --cap-drop ALL --security-opt no-new-privileges:true --ipc none --pids-limit 32 --network none --memory 640m --cpus 2"
# What Chromium needs beyond them (measured): a tmpfs for /tmp (Playwright and Chromium write
# there) and more than 32 processes and threads. Its own sandbox never starts (see the evidence).
RELAXED="${PARSER/10002:10002/10003:10003} --memory 1500m --tmpfs /tmp:size=64m --pids-limit 256"
MOUNTS="-v \$HOME/$DIR/spike:/spike:ro -v \$HOME/$DIR/work:/work"

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
  [ "$KEEP" = 1 ] && { echo "kept: $HOST:~/$DIR and the d1spike images"; return 0; }
  say "clean up"
  ssh "$HOST" "docker rm -f d1spike-load >/dev/null 2>&1 || true; docker run --rm --name d1spike-clean --memory 256m --cpus 1 --network none -v \"\$HOME/$DIR\":/d python:3.12.15-slim-trixie rm -rf /d/work; docker rmi d1spike-weasy d1spike-chromium >/dev/null 2>&1 || true; rm -rf \"\$HOME/$DIR\"; docker images | grep d1spike || echo 'no d1spike image left'; free -m; df -h /"
}
trap cleanup EXIT

say "stage the build context ($DIR)"
STAGE="$(mktemp -d)"
cp "$SPIKE"/*.py "$SPIKE"/Dockerfile.* "$STAGE/"
mkdir -p "$STAGE/fixtures"
cp "$FIXTURES"/*.json "$STAGE/fixtures/"
# Two kinds on the same handler: render_pdf_strict keeps every limit of main.py (no new thread,
# which Pango needs), render_pdf allows threads up to the container's pids_limit of 32.
perl -pe 's/^(    "pdf_render": "pdf_render",\n)/$1    "render_pdf": "render_pdf",\n    "render_pdf_strict": "render_pdf",\n/; s/^(LIMITS\["pdf_render"\]\["as"\] = PDF_RENDER_AS\n)/$1LIMITS["render_pdf"]["nproc"] = 32\n/' \
  "$HERE/api/app/ingestion/sandbox/main.py" >"$STAGE/main.py"
[ "$(grep -c '"render_pdf\(_strict\)\?": "render_pdf"\|LIMITS\["render_pdf"\]\["nproc"\] = 32' "$STAGE/main.py")" = 3 ] || { echo "main.py: patch not applied" >&2; exit 70; }
echo "+ rsync $STAGE/ $HOST:~/$DIR/spike/"
if [ "$RUN" = 1 ]; then
  ssh "$HOST" "mkdir -p \"\$HOME/$DIR/work\" && chmod 777 \"\$HOME/$DIR/work\""
  rsync -a "$STAGE/" "$HOST:$DIR/spike/"
  ssh "$HOST" "chmod -R a+rX \"\$HOME/$DIR/spike\""
fi
rm -rf "$STAGE"

say "build the images"
wait_memory
remote "cd \$HOME/$DIR/spike && docker build -q -t d1spike-weasy -f Dockerfile.weasy . && docker build -q -t d1spike-chromium -f Dockerfile.chromium ."
remote "docker image inspect -f '{{.Size}}' jobapp-worker:latest d1spike-weasy python:3.12.15-slim-trixie d1spike-chromium | tr '\n' ' ' > \$HOME/$DIR/work/sizes.txt; cat \$HOME/$DIR/work/sizes.txt"

say "fixtures to HTML"
remote "docker run --rm --name d1spike-make --memory 1500m --cpus 2 --network none $MOUNTS -v \$HOME/$DIR/spike/fixtures:/fixtures:ro d1spike-weasy python /spike/make_html.py /fixtures /work"

say "WeasyPrint inside the parser sandbox, alone"
wait_memory
remote "docker run --rm --name d1spike-weasy-run $PARSER $MOUNTS d1spike-weasy python /spike/weasy_bench.py /work/html /work full alone"

say "Chromium: its own sandbox under the parser's settings, then relaxed, then without it"
wait_memory
remote "docker run --rm --name d1spike-chromium-a ${PARSER/10002:10002/10003:10003} --memory 1500m $MOUNTS d1spike-chromium python /spike/chromium_bench.py /work/html /work sandbox sandbox-parser || true"
remote "docker run --rm --name d1spike-chromium-b $RELAXED $MOUNTS d1spike-chromium python /spike/chromium_bench.py /work/html /work sandbox sandbox-relaxed || true"
remote "docker run --rm --name d1spike-chromium-c ${PARSER/10002:10002/10003:10003} --memory 1500m --tmpfs /tmp:size=64m $MOUNTS d1spike-chromium python /spike/chromium_bench.py /work/html /work sandbox nosandbox-pids32 --no-sandbox || true"
remote "docker run --rm --name d1spike-chromium-run $RELAXED $MOUNTS d1spike-chromium python /spike/chromium_bench.py /work/html /work full alone --no-sandbox"

say "both again while a stand in for an agent run is busy (two cores, 300 MB)"
wait_memory
remote "docker run -d --rm --name d1spike-load --memory 600m --cpus 2 --network none python:3.12.15-slim-trixie sh -c 'python -c \"x = bytearray(300 * 2**20); exec(\\\"while 1: pass\\\")\" & python -c \"while 1: pass\"'"
remote "docker run --rm --name d1spike-weasy-load $PARSER $MOUNTS d1spike-weasy python /spike/weasy_bench.py /work/html /work quick load"
remote "docker run --rm --name d1spike-chromium-load $RELAXED $MOUNTS d1spike-chromium python /spike/chromium_bench.py /work/html /work quick load --no-sandbox"
remote "docker rm -f d1spike-load"

say "side by side, probes, font check"
remote "docker run --rm --name d1spike-compare --memory 1500m --cpus 2 --network none $MOUNTS d1spike-weasy python /spike/compare.py /work /work/evidence"

say "bring the evidence back"
echo "+ rsync $HOST:~/$DIR/work/{evidence,*.json,sizes.txt} $EVIDENCE/"
if [ "$RUN" = 1 ]; then
  mkdir -p "$EVIDENCE"
  rsync -a "$HOST:$DIR/work/evidence/" "$EVIDENCE/"
  rsync -a "$HOST:$DIR/work/sizes.txt" "$HOST:$DIR/work/*.json" "$EVIDENCE/"
fi
