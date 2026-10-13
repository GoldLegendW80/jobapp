#!/usr/bin/env bash
# Measurements of the parser service on the Jetson (ingestion G2a), for the orchestrator to run
# once the service is deployed. Read only: it inspects containers, reads counters and sends
# pings through the worker. It changes nothing of the stack, needs no sudo, and prints numbers,
# names and codes, never a value of the environment. The one thing it writes is a temporary
# folder on the Jetson, removed at the end.
#
#   tests/ingestion_parser_jetson.sh                 on DEPLOY_HOST (default jetson), ~/jobapp
#   ROUNDS=50 tests/ingestion_parser_jetson.sh       pings without bytes (default 20)
#   DEPLOY_HOST=local tests/ingestion_parser_jetson.sh   on the Jetson itself, no ssh
#   PARSER_COMPOSE='docker compose -p x -f compose.yml' DEPLOY_HOST=local DEPLOY_DIR=$PWD \
#     tests/ingestion_parser_jetson.sh               against another stack (a scratch one;
#                                                    PARSER_INSPECT_OPTIONS=--code-mount for a
#                                                    dev stack)
#
# What it answers, each an assumption of the plan until it has run there:
#   1. the seccomp filter installs on the Jetson's kernel (Tegra 5.15) and denies every call of
#      the list: the ping child's own report, call by call, and the healthcheck's exit status;
#   2. what Docker itself adds there (its default seccomp profile, which the Mac's Docker
#      Desktop does not have), and whether a container on the internal network resolves
#      outside names;
#   3. the idle memory of the service, and the highest the container reached while it worked;
#   4. CPU and wall time of the trivial job: a ping, and an echo of 1 MiB and of 20 MiB;
#   5. the lowest available memory of the host during it.
# G2b adds its PDF fixtures to JOBS below, and reads `as`, `cpu` and the time budget from them.
#
# A problem shows as a line starting with PROBLEM and exit status 1. The numbers go under
# "Measurements" in the plan of the module.
set -euo pipefail

HOST="${DEPLOY_HOST:-jetson}"
RDIR="${DEPLOY_DIR:-jobapp}"
RDIR="${RDIR#\~/}"
ROUNDS="${ROUNDS:-20}"
COMPOSE="${PARSER_COMPOSE:-docker compose -f compose.yml --profile tunnel}"
# Options for tests/parser_check.py inspect: --code-mount for a dev stack, whose parser has the
# code mounted read only. The deployed stack has nothing mounted, and needs none.
INSPECT_OPTIONS="${PARSER_INSPECT_OPTIONS:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"

[[ "$ROUNDS" =~ ^[1-9][0-9]{0,3}$ ]] || {
  echo "ROUNDS must be a number between 1 and 9999" >&2
  exit 2
}

if [[ "$HOST" == "local" ]]; then
  r() { (cd "$HOME" && bash -c "$*"); }
else
  r() { ssh -o BatchMode=yes -o ConnectTimeout=5 "$HOST" "$@"; }
fi
r true || {
  echo "cannot reach $HOST" >&2
  exit 1
}

# The whole measurement is one script, sent to bash on the Jetson: no quoting through ssh, and
# one connection. Its last line is `result=<JSON>`, judged and laid out below. (A function, and
# not a here document inside the command substitution: bash 3.2, the one of macOS, cannot read
# an apostrophe there.)
remote_script() {
  cat <<'REMOTE'
set -euo pipefail
cd "$1"
rounds="$2"
read -r -a compose <<<"$3"
work="$(mktemp -d /tmp/jobapp-parser-measure.XXXXXX)"
sampler=""
cleanup() {
  [[ -z "$sampler" ]] || kill "$sampler" 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT

parser="$("${compose[@]}" ps -q parser)"
worker="$("${compose[@]}" ps -q worker)"
[[ -n "$parser" && -n "$worker" ]] || {
  echo "PROBLEM: the parser or the worker is not running"
  exit 1
}

echo "host: kernel $(uname -r), $(uname -m), $(nproc) cores"
echo "docker: $(docker info --format '{{.ServerVersion}}, cgroup v{{.CgroupVersion}}, security options {{.SecurityOptions}}')"
if [[ -r /proc/config.gz ]]; then
  echo "kernel config: $(zcat /proc/config.gz | grep -E '^CONFIG_SECCOMP(_FILTER)?=' | tr '\n' ' ')"
else
  echo "kernel config: /proc/config.gz is not readable here"
fi
echo "host memory: $(awk '/^(MemTotal|MemAvailable|SwapTotal|SwapFree):/ {printf "%s %d MiB  ", $1, $2 / 1024}' /proc/meminfo 2>/dev/null || echo "no /proc/meminfo here")"
echo "parser: $(docker inspect -f 'health {{.State.Health.Status}}, restarts {{.RestartCount}}, killed for memory {{.State.OOMKilled}}, started {{.State.StartedAt}}' "$parser")"
[[ "$(docker inspect -f '{{.Image}}' "$parser")" == "$(docker inspect -f '{{.Image}}' "$worker")" ]] ||
  echo "PROBLEM: the parser does not run the worker's image"
docker inspect "$parser" >"$work/inspect.json"

# Memory of the parser's own cgroup, read inside the container (cgroup v2, or v1).
cgroup() {
  docker exec "$parser" sh -c '
    if [ -r /sys/fs/cgroup/memory.current ]; then
      printf "current=%s " "$(cat /sys/fs/cgroup/memory.current)"
      if [ -r /sys/fs/cgroup/memory.peak ]; then printf "peak=%s " "$(cat /sys/fs/cgroup/memory.peak)"; fi
      printf "events=%s " "$(tr "\n" "," </sys/fs/cgroup/memory.events)"
      printf "pids=%s" "$(cat /sys/fs/cgroup/pids.current)"
    elif [ -r /sys/fs/cgroup/memory/memory.usage_in_bytes ]; then
      printf "current=%s peak=%s" "$(cat /sys/fs/cgroup/memory/memory.usage_in_bytes)" \
        "$(cat /sys/fs/cgroup/memory/memory.max_usage_in_bytes)"
    else
      printf "current=unreadable"
    fi'
}
current_of() { sed -n 's/.*current=\([0-9]*\).*/\1/p' <<<"$1"; }

idle=()
for _ in 1 2 3 4 5; do
  idle+=("$(current_of "$(cgroup)")")
  sleep 1
done
echo "idle: cgroup memory.current over 5 s, bytes: ${idle[*]}"
echo "idle: docker stats: $(docker stats --no-stream --format '{{.MemUsage}}, {{.PIDs}} pids' "$parser")"

# What the service says of itself: the healthcheck, then the network from inside.
if docker exec "$parser" python -I /app/app/ingestion/sandbox/server.py --check 2>"$work/check.err"; then
  echo "healthcheck: exit 0"
else
  echo "PROBLEM: the healthcheck fails: $(head -c 300 "$work/check.err")"
fi
echo "resolver inside the parser: $(docker exec "$parser" sh -c "grep -E '^(nameserver|# ExtServers)' /etc/resolv.conf | tr '\n' ' '")"

# The sampler: the parser's memory and the host's available memory, once a second, while the
# jobs run.
(
  while true; do
    printf '%s %s\n' "$(current_of "$(cgroup)")" "$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
    sleep 1
  done
) >"$work/samples" 2>/dev/null &
sampler=$!

# The jobs, from the worker's container through the application's own client.
docker exec -i "$worker" python - "$rounds" >"$work/jobs.json" 2>"$work/jobs.err" <<'PY' || echo "PROBLEM: the jobs failed: $(tail -n 3 "$work/jobs.err" | head -c 600)"
import asyncio
import json
import statistics
import sys
import time

from app.ingestion import parser_client as pc

MIB = 1024 * 1024
# (label, bytes sent, rounds). G2b adds its PDF fixtures here, through pc.parse.
JOBS = (("ping", 0, int(sys.argv[1])), ("echo of 1 MiB", MIB, 5), ("echo of 20 MiB", 20 * MIB, 3))


def spread(values):
    return [round(min(values), 1), round(statistics.median(values), 1), round(max(values), 1)]


async def main():
    found = {"jobs": []}
    report = None
    for label, size, rounds in JOBS:
        data = bytes(size)
        wall, cpu, rss = [], [], []
        for _ in range(rounds):
            started = time.monotonic()
            report = await pc.ping(data, timeout_s=90)
            wall.append((time.monotonic() - started) * 1000)
            cpu.append(report.cpu_ms)
            rss.append(report.max_rss_kb / 1024)
        found["jobs"].append(
            {"job": label, "rounds": rounds, "wall_ms": spread(wall), "child_cpu_ms": spread(cpu),
             "child_max_rss_mib": spread(rss)}
        )
    sandbox = report.sandbox
    found["sandbox"] = {
        "in_force": report.in_force,
        "problems": list(report.problems),
        "seccomp": sandbox["seccomp"],
        "filters": sandbox["filters"],
        "denied": sandbox["denied"],
        "limits": sandbox["limits"],
        "kernel": sandbox["kernel"],
        "machine": sandbox["machine"],
        "uid": sandbox["uid"],
    }
    print(json.dumps(found))


try:
    asyncio.run(main())
except pc.ParserError as error:
    print(json.dumps({"error": type(error).__name__, "code": error.code}))
PY
kill "$sampler" 2>/dev/null || true
wait "$sampler" 2>/dev/null || true
sampler=""

echo "after: $(cgroup)"
echo "after: docker stats: $(docker stats --no-stream --format '{{.MemUsage}}, {{.PIDs}} pids' "$parser")"
echo "after: $(docker inspect -f 'health {{.State.Health.Status}}, restarts {{.RestartCount}}, killed for memory {{.State.OOMKilled}}' "$parser")"
echo "samples: $(awk 'NF == 2 {n++; if ($1 > peak) peak = $1; if (low == "" || $2 < low) low = $2} END {printf "%d, parser cgroup highest %d bytes, host MemAvailable lowest %d MiB", n, peak, low / 1024}' "$work/samples")"
echo "inspect=$(tr -d '\n' <"$work/inspect.json")"
echo "result=$(cat "$work/jobs.json")"
REMOTE
}
out="$(remote_script | r "bash -s -- '$RDIR' '$ROUNDS' '$COMPOSE'")" || {
  printf '%s\n' "$out"
  echo "PROBLEM: the measurement did not run to its end on $HOST" >&2
  exit 1
}

# Everything but the two machine readable lines, as it came.
printf '%s\n' "$out" | grep -v -e '^inspect=' -e '^result='

# The container as it runs, judged like the smoke scripts judge it (names and numbers only).
# shellcheck disable=SC2086 # one option or nothing
if container="$(printf '%s\n' "$out" | sed -n 's/^inspect=//p' | python3 "$HERE/parser_check.py" inspect $INSPECT_OPTIONS 2>&1)"; then
  container="container: $container"
else
  container="PROBLEM: the container is not what compose.yml says: $container"
fi

report="$(
  printf '%s\n' "$out" | sed -n 's/^result=//p' | python3 -c '
import json, sys

raw = sys.stdin.read().strip()
try:
    found = json.loads(raw)
except ValueError:
    print("PROBLEM: the jobs gave no result")
    sys.exit(0)
if "error" in found:
    print("PROBLEM: the worker could not ping the parser: %s %s" % (found["error"], found["code"]))
    sys.exit(0)
sandbox = found["sandbox"]
refused = sum(1 for answer in sandbox["denied"].values() if answer == "EPERM")
state = "in force" if sandbox["in_force"] else "NOT in force"
print("sandbox on kernel %s (%s): %s" % (sandbox["kernel"], sandbox["machine"], state))
print("  seccomp mode %d, %d filters on the child (1: ours alone; 2: ours on top of the default profile of Docker)" % (sandbox["seccomp"], sandbox["filters"]))
print("  %d of %d denied calls answer EPERM" % (refused, len(sandbox["denied"])))
print("  limits %s" % json.dumps(sandbox["limits"], sort_keys=True))
for name, answer in sorted(sandbox["denied"].items()):
    if answer != "EPERM":
        print("PROBLEM: %s answers %s, not EPERM" % (name, answer))
for problem in sandbox["problems"]:
    print("PROBLEM: sandbox: %s" % problem)
print()
print("%-16s %6s  %-26s %-26s %s" % ("job", "rounds", "wall ms (min med max)", "child CPU ms", "child peak RSS MiB"))
for job in found["jobs"]:
    print("%-16s %6d  %-26s %-26s %s" % (job["job"], job["rounds"], job["wall_ms"], job["child_cpu_ms"], job["child_max_rss_mib"]))
'
)"

printf '\n%s\n\n%s\n' "$container" "$report"

if printf '%s\n%s\n%s\n' "$out" "$container" "$report" | grep -q '^PROBLEM'; then
  printf '\nPROBLEM lines above: the parser is not as the plan assumes on %s\n' "$HOST" >&2
  exit 1
fi
printf '\nNo problem found on %s.\n' "$HOST"
