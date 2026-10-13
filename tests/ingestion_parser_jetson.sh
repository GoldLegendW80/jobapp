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
#   6. (G2b) the PDF reader: text PDFs of 1, 20 and 200 pages and a 20 MiB PDF, made in the
#      snippet itself (the image holds no test code), each read through pc.parse: wall time,
#      and the child's CPU, resident memory and address space, to judge `as` (256 MiB), the
#      CPU limit and the time budget of the kind against this machine.
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
import io
import json
import random
import statistics
import sys
import time
import zipfile

from app.ingestion import parser_client as pc

MIB = 1024 * 1024
# (label, bytes sent, rounds), each a ping. The PDF fixtures of G2b follow in PDFS.
JOBS = (("ping", 0, int(sys.argv[1])), ("echo of 1 MiB", MIB, 5), ("echo of 20 MiB", 20 * MIB, 3))


SENTENCE = "Robin Example led the migration of the billing service to Postgres in 2021."


def pdf(pages, rows=40, filler=0):
    """A PDF written by hand: `pages` pages of `rows` lines of made up text, and `filler` bytes
    of an image nothing shows (bytes that do not compress), for the 20 MiB case."""
    import random

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [%s] /Count %d >>"
        % (b" ".join(b"%d 0 R" % (5 + 2 * n) for n in range(pages)), pages),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    noise = random.Random(7).randbytes(filler)
    objects.append(
        b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray "
        b"/BitsPerComponent 8 /Length %d >>\nstream\n" % len(noise) + noise + b"\nendstream"
    )
    for n in range(pages):
        lines = " ".join(f"(Page {n + 1}, line {row + 1}. {SENTENCE}) Tj T*" for row in range(rows))
        stream = f"BT /F1 9 Tf 40 800 Td 11 TL {lines} ET".encode("latin-1")
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents %d 0 R "
            b"/Resources << /Font << /F1 3 0 R >> >> >>" % (6 + 2 * n)
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, start)
    return bytes(out)


# (label, the file, rounds): what a CV is, what the page cap allows, and the largest file.
PDFS = (
    ("pdf, 1 page", lambda: pdf(1), 5),
    ("pdf, 20 pages", lambda: pdf(20), 5),
    ("pdf, 200 pages", lambda: pdf(200, rows=25), 3),  # under the cap of 500,000 characters
    ("pdf, 20 MiB, 1 page", lambda: pdf(1, filler=20 * MIB - 8192), 3),
)


def archive(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
        for name, content in entries:
            out.writestr(name, content)
    return buffer.getvalue()


def words(count, seed):
    noise = random.Random(seed)
    letters = "abcdefghijklmnopqrstuvwxyz"
    return ["".join(noise.choices(letters, k=noise.randint(2, 9))) for _ in range(count)]


def docx(paragraphs, filler=0):
    """A Word document of `paragraphs` lines, and `filler` empty runs that are parsed for
    nothing: 850,000 of them are 19 MiB of XML in one part."""
    space = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    lines = "".join(f"<w:p><w:r><w:t>{SENTENCE} Line {n}.</w:t></w:r></w:p>" for n in range(paragraphs))
    runs = "".join(f'<w:r w:rsidR="{word}"/>' for word in words(filler, 5))
    body = f"<w:document {space}><w:body>{lines}<w:p>{runs}</w:p></w:body></w:document>"
    return archive([("[Content_Types].xml", "<Types/>"), ("word/document.xml", body)])


def export(count):
    """A LinkedIn like export: two known CSV files of `count` words each, one nobody reads."""
    return archive(
        [
            ("Profile.csv", "Headline\r\n" + " ".join(words(count, 8))),
            ("Positions.csv", "Title\r\n" + " ".join(words(count, 9))),
            ("Connections.csv", "First Name\r\nnever read\r\n"),
        ]
    )


# (label, kind of parser job, the file, rounds): the archives of G7.
ARCHIVES = (
    ("docx, a CV", "docx", lambda: docx(60), 5),
    ("docx, 19 MiB of XML in one part", "docx", lambda: docx(60, filler=850_000), 3),
    ("export, two small files", "linkedin_zip", lambda: export(300), 5),
    ("export, 20 MiB in two files", "linkedin_zip", lambda: export(1_600_000), 3),
)


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
    for label, make, rounds in PDFS:
        data = make()
        wall, cpu, rss, vm = [], [], [], []
        parsed = None
        for _ in range(rounds):
            started = time.monotonic()
            parsed = await pc.parse("pdf_text", data, timeout_s=90)
            wall.append((time.monotonic() - started) * 1000)
            used = parsed.usage or {}
            cpu.append(used.get("cpu_ms", 0))
            rss.append(used.get("max_rss_kb", 0) / 1024)
            vm.append(used.get("max_vm_kb", 0) / 1024)
        # Counts only: nothing of a text is printed, not even of a made up one.
        found["jobs"].append(
            {"job": label, "bytes": len(data), "rounds": rounds, "pages": parsed.pages,
             "pages_text": parsed.pages_text, "characters": len(parsed.text or ""),
             "wall_ms": spread(wall), "child_cpu_ms": spread(cpu),
             "child_max_rss_mib": spread(rss), "child_max_vm_mib": spread(vm)}
        )
    for label, kind, make, rounds in ARCHIVES:
        data = make()
        wall, cpu, rss, vm = [], [], [], []
        parsed = None
        for _ in range(rounds):
            started = time.monotonic()
            parsed = await pc.parse(kind, data, timeout_s=90)
            wall.append((time.monotonic() - started) * 1000)
            used = parsed.usage or {}
            cpu.append(used.get("cpu_ms", 0))
            rss.append(used.get("max_rss_kb", 0) / 1024)
            vm.append(used.get("max_vm_kb", 0) / 1024)
        characters = len(parsed.text or "") + sum(len(one.text) for one in parsed.documents)
        found["jobs"].append(
            {"job": label, "bytes": len(data), "rounds": rounds, "files": len(parsed.documents),
             "characters": characters, "truncated": parsed.truncated,
             "wall_ms": spread(wall), "child_cpu_ms": spread(cpu),
             "child_max_rss_mib": spread(rss), "child_max_vm_mib": spread(vm)}
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
